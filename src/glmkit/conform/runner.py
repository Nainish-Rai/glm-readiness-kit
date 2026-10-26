"""Conformance runner: cases x target -> result envelope.

For each case (and variant and mode) the runner runs ``repeats`` attempts through the case's executor,
evaluates the checks the case lists for the target's endpoint class, then the aggregate checks, and records
one ``ItemResult`` with evidence. The runner knows no case, check, executor or defect by name.
"""

from __future__ import annotations

import contextlib
import dataclasses
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glmkit.client import GLMClient, deep_merge
from glmkit.conform import checks as check_registry
from glmkit.conform import executors as exec_registry
from glmkit.conform.cases import LoadedCase, load_cases
from glmkit.conform.checks import Attempt, CheckContext
from glmkit.contracts.case import CheckSpec, ConformanceCase, Variant
from glmkit.contracts.envelope import CheckResult, ItemResult, ResultEnvelope, TargetInfo, new_run_id
from glmkit.contracts.evidence import Exchange
from glmkit.contracts.profile import MOCK_PLACEHOLDER, EndpointProfile
from glmkit.contracts.trace import exchange_to_trace
from glmkit.mock.defects import DEFECT_HEADER
from glmkit.redact import Redactor
from glmkit.results import add_artifact, run_dir, run_log, write_envelope, write_jsonl

COMPONENT = "conform"
THROTTLE_CODES = {"1302", "1305", "1308", "1309", "1310", "1311", "1313", *(str(c) for c in range(1314, 1322))}
MAX_EVIDENCE_ATTEMPTS = 5


@dataclass
class RunOptions:
    patterns: list[str] | None = None
    case_roots: list[Path] | None = None
    modes: list[str] | None = None
    mock_defects: list[str] = field(default_factory=list)
    out_dir: Path = Path("results")
    timeout_s: float = 60.0
    stream_timeout_s: float = 120.0
    include_opt_in: bool = False
    write: bool = True


@dataclass
class RunOutcome:
    envelope: ResultEnvelope
    run_dir: Path | None


@contextlib.contextmanager
def resolve_target(
    profile: EndpointProfile, mock_defects: list[str]
) -> Iterator[tuple[EndpointProfile, dict[str, str]]]:
    """Start the in-process mock for ``spawn_mock`` profiles (defects set server-wide). For any other
    target, requested mock defects are sent per request in the ``X-GLMKit-Defects`` header."""
    if profile.spawn_mock or MOCK_PLACEHOLDER in profile.base_url:
        from glmkit.mock import MockConfig, MockServer

        with MockServer(MockConfig(defects=list(mock_defects))) as srv:
            yield profile.with_base_url(profile.base_url.replace(MOCK_PLACEHOLDER, srv.url)), {}
    else:
        yield profile, ({DEFECT_HEADER: ",".join(mock_defects)} if mock_defects else {})


def applicability(case: ConformanceCase, target: EndpointProfile) -> str | None:
    """Reason the case does not apply to the target, or None."""
    a = case.applies_to
    family = (case.request.model or target.family).lower()
    if a.models is not None and family not in [m.lower() for m in a.models]:
        return f"model {family} not in applies_to.models"
    if a.protocols is not None and target.protocol not in a.protocols:
        return f"protocol {target.protocol} not in applies_to.protocols"
    if a.endpoint_classes is not None and target.endpoint_class not in a.endpoint_classes:
        return f"endpoint class {target.endpoint_class} not in applies_to.endpoint_classes"
    return None


def _status_of(checks: list[CheckResult]) -> str:
    st = {c.status for c in checks}
    for s in ("error", "fail", "soft_fail"):
        if s in st:
            return s
    return "pass"


def _throttled(exchanges: list[Exchange]) -> bool:
    return any(e.normalized.http_status == 429 and e.normalized.error_code in THROTTLE_CODES for e in exchanges)


def evaluate(spec: CheckSpec, ctx: CheckContext, attempt: int | None) -> CheckResult:
    try:
        cdef = check_registry.get_check(spec.check)
    except KeyError as e:
        return CheckResult(name=spec.check, status="error", message=str(e.args[0]), attempt=attempt)
    if spec.when is not None:
        pre = evaluate(spec.when, dataclasses.replace(ctx, spec=spec.when), attempt)
        if pre.status != "pass":
            return CheckResult(
                name=spec.check,
                status="skip",
                message=f"precondition {spec.when.check} not met",
                soft=spec.soft,
                attempt=attempt,
            )
    try:
        out = cdef.fn(ctx)
    except Exception as e:  # a buggy check is an error, never a crash
        return CheckResult(name=spec.check, status="error", message=f"{type(e).__name__}: {e}", attempt=attempt)
    status = out.status
    if status == "fail" and spec.soft:
        status = "soft_fail"
    return CheckResult(
        name=spec.check,
        status=status,
        message=out.message,
        expected=out.expected,
        observed=out.observed,
        soft=spec.soft,
        attempt=attempt,
    )


async def run_item(
    lc: LoadedCase, target: EndpointProfile, client: GLMClient, mode: str, variant: Variant | None, log: Any
) -> ItemResult:
    case = lc.case
    item_id = case.id + (f"/{variant.id}" if variant else "") + f"[{mode}]"
    base = ItemResult(
        id=item_id,
        group=case.id,
        title=case.title,
        kind="conformance_case",
        category=case.category,
        severity=case.severity,
        target=target.name,
        status="pass",
        labels={
            "mode": mode,
            "protocol": target.protocol,
            "endpoint_class": target.endpoint_class,
            "kind": case.kind,
            **({"variant": variant.id} if variant else {}),
        },
        source=case.source.model_dump(mode="json", exclude_defaults=True) | {"case_file": lc.rel},
    )
    specs = case.checks_for(target.endpoint_class, variant)
    aggregate = [s for s in specs if _is_aggregate(s)]
    per_attempt = [s for s in specs if not _is_aggregate(s)]
    request = case.request.model_dump(mode="json", exclude_none=True)
    if variant:
        request = deep_merge(request, variant.request)
    try:
        run = exec_registry.get_executor(case.executor)
    except KeyError as e:
        return base.model_copy(update={"status": "error", "reason": str(e.args[0])})
    t0 = time.perf_counter()
    attempts: list[Attempt] = []
    evidence: list[Exchange] = []
    for i in range(case.repeats):
        ectx = exec_registry.ExecContext(
            case=case,
            target=target,
            client=client,
            request=request,
            mode=mode,
            attempt=i,
            variant=variant,
            config=case.executor_config,
        )
        try:
            exchanges = await run(ectx)
        except Exception as e:
            log.exception("executor %s failed on %s", case.executor, item_id)
            exchanges = []
            results = [CheckResult(name="executor", status="error", message=f"{type(e).__name__}: {e}", attempt=i)]
        else:
            cctx = CheckContext(
                case=case, target=target, spec=CheckSpec(check="_"), mode=mode, variant=variant, exchanges=exchanges
            )
            results = [evaluate(s, dataclasses.replace(cctx, spec=s), i) for s in per_attempt]
        status = "throttled" if _throttled(exchanges) else _status_of(results)
        attempts.append(Attempt(index=i, exchanges=exchanges, checks=results, status=status))
        if i < MAX_EVIDENCE_ATTEMPTS or status in ("fail", "error"):
            evidence += exchanges
        for ex in exchanges:
            log.info(
                "%s attempt=%d %s HTTP %s code=%s %s",
                item_id,
                i,
                ex.label,
                ex.normalized.http_status,
                ex.normalized.error_code,
                ex.transport_error or ex.normalized.error_message or "",
            )
    agg_ctx = CheckContext(
        case=case,
        target=target,
        spec=CheckSpec(check="_"),
        mode=mode,
        variant=variant,
        exchanges=attempts[-1].exchanges if attempts else [],
        attempts=attempts,
    )
    agg_results = [evaluate(s, dataclasses.replace(agg_ctx, spec=s), None) for s in aggregate]

    statuses = [a.status for a in attempts]
    if aggregate:  # statistical: per-attempt failures are summarized by the aggregate checks
        status = _status_of(agg_results + [c for a in attempts for c in a.checks if c.status == "error"])
    elif statuses and all(s == "throttled" for s in statuses):
        status = "throttled"
    else:
        status = next((s for s in ("fail", "error", "soft_fail") if s in statuses), "pass")
    all_checks = [c for a in attempts for c in a.checks] + agg_results
    failing = next((c for c in all_checks if c.status in ("fail", "error", "soft_fail")), None)
    main = attempts[0].exchanges[-1] if attempts and attempts[0].exchanges else None
    metrics: dict[str, Any] = {"attempts": len(attempts)}
    if main is not None:
        metrics |= {
            "http_status": main.normalized.http_status,
            "error_code": main.normalized.error_code,
            "ttft_ms": main.timing.ttft_ms,
            "total_ms": main.timing.total_ms,
            "finish_reason": main.normalized.finish_reason_norm,
        }
    if len(attempts) > 1:
        scored = [s for s in statuses if s != "throttled"]
        metrics["pass_rate"] = (sum(1 for s in scored if s == "pass") / len(scored)) if scored else None
    item = base.model_copy(
        update={
            "status": status,
            "reason": f"{failing.name}: {failing.message}" if failing else None,
            "checks": all_checks,
            "metrics": metrics,
            "evidence": evidence,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
        }
    )
    log.info("%s -> %s %s", item_id, status, item.reason or "")
    return item


def _is_aggregate(spec: CheckSpec) -> bool:
    try:
        return check_registry.get_check(spec.check).aggregate
    except KeyError:
        return False


async def run_conformance(profile: EndpointProfile, opts: RunOptions) -> RunOutcome:
    cases = load_cases(opts.case_roots, opts.patterns, include_opt_in=opts.include_opt_in)
    started = datetime.now(UTC)
    env = ResultEnvelope(
        run_id=new_run_id(COMPONENT, started),
        component=COMPONENT,
        started_at=started,
        config={
            "patterns": opts.patterns,
            "modes": opts.modes,
            "mock_defects": opts.mock_defects,
            "cases": [{"id": lc.case.id, "file": lc.rel, "sha256": lc.sha256} for lc in cases],
        },
    )
    redactor = Redactor([profile.api_key()])
    out_dir = run_dir(opts.out_dir, env.run_id) if opts.write else None
    log_path = (out_dir / "run.log") if out_dir else None
    with contextlib.ExitStack() as stack:
        log = stack.enter_context(run_log(log_path, redactor)) if log_path else _NullLog()
        target, extra_headers = stack.enter_context(resolve_target(profile, opts.mock_defects))
        env.targets = [TargetInfo.from_profile(target)]
        log.info(
            "run %s target=%s url=%s cases=%d defects=%s",
            env.run_id,
            target.name,
            target.url,
            len(cases),
            opts.mock_defects,
        )
        client = GLMClient(
            target,
            timeout_s=opts.timeout_s,
            stream_timeout_s=opts.stream_timeout_s,
            extra_headers=extra_headers,
            redactor=redactor,
        )
        async with client:
            for lc in cases:
                case = lc.case
                why = applicability(case, target)
                modes = [m for m in case.modes if not opts.modes or m in opts.modes]
                for variant in [None, *case.variants]:
                    for mode in modes or case.modes[:1]:
                        if why or not modes:
                            env.items.append(
                                ItemResult(
                                    id=case.id + (f"/{variant.id}" if variant else "") + f"[{mode}]",
                                    group=case.id,
                                    title=case.title,
                                    kind="conformance_case",
                                    category=case.category,
                                    severity=case.severity,
                                    target=target.name,
                                    status="skip",
                                    reason=why or "mode not selected",
                                    labels={
                                        "mode": mode,
                                        "protocol": target.protocol,
                                        "endpoint_class": target.endpoint_class,
                                    },
                                )
                            )
                            continue
                        env.items.append(await run_item(lc, target, client, mode, variant, log))
    env.finalize()
    if out_dir is not None:
        traces = []
        seq = 0
        for item in env.items:
            for ex in item.evidence:
                traces.append(
                    exchange_to_trace(
                        ex,
                        trace_id=f"{env.run_id}/{item.id}",
                        seq=seq,
                        ts_start=env.started_at,
                        ref=f"{env.run_id}/{item.id}/{ex.id}",
                        provider=target.provider,
                        task_type=f"conformance/{item.category}",
                    ).to_jsonl()
                )
                seq += 1
        if traces:
            write_jsonl(traces, out_dir / "traces.jsonl", redactor)
            add_artifact(env, "traces", out_dir / "traces.jsonl", out_dir)
        add_artifact(env, "log", out_dir / "run.log", out_dir)
        write_envelope(env, opts.out_dir, redactor)
    return RunOutcome(envelope=env, run_dir=out_dir)


class _NullLog:
    def info(self, *a: Any, **k: Any) -> None:
        pass

    def exception(self, *a: Any, **k: Any) -> None:
        pass
