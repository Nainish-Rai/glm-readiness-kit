"""Host-quality run: execute the latency and fingerprint cases against a target and write an envelope of its own
(component ``hostq``), with a host-quality verdict that is separate from the correctness verdict.

The cases reuse the conformance runner's ``run_item`` (executors, checks, evidence). This module only adds what the
runner must not know: the reference (an ambient session), per-case repeat overrides, a mock server that answers
the canaries, and the verdict from explicit gates.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glmkit.client import GLMClient
from glmkit.conform.cases import load_cases
from glmkit.conform.runner import _NullLog, applicability, resolve_target, run_item
from glmkit.contracts.envelope import ItemResult, ResultEnvelope, TargetInfo, new_run_id
from glmkit.contracts.profile import MOCK_PLACEHOLDER, EndpointProfile
from glmkit.hostq import probes
from glmkit.hostq.reference import HostqSession, Reference, new_reference, use_session
from glmkit.redact import Redactor
from glmkit.results import add_artifact, run_dir, run_log, write_envelope

COMPONENT = "hostq"
HOST_PATTERNS = ["LAT-*", "FP-*"]
RECORD_PATTERNS = ["FP-002", "FP-003", "FP-004", "FP-005"]
LATENCY_CASES = ("LAT-001", "LAT-002")
SAMPLED_CASES = ("FP-002", "FP-003")  # repeats = samples per prompt
# which check of which case feeds which gate; severity of the case decides how hard a flag is
GATE_CHECKS = {
    "LAT-001": "latency_tool_penalty",
    "LAT-002": "latency_recorded",
    "FP-001": "model_field_honest",
    "FP-002": "action_distribution_vs_reference",
    "FP-003": "text_distribution_vs_reference",
    "FP-004": "greedy_self_agreement",
    "FP-005": "capability_canaries",
}
NEEDS_REFERENCE = {"FP-002", "FP-003", "FP-004", "FP-005"}


@dataclass
class HostOptions:
    patterns: list[str] = field(default_factory=lambda: list(HOST_PATTERNS))
    case_roots: list[Path] | None = None
    reference: Reference | None = None
    label: str = ""
    out_dir: Path = Path("results")
    latency_n: int | None = None  # valid samples per latency condition (overrides the case's repeats)
    samples: int | None = None  # samples per prompt for FP-002 / FP-003
    mock_defects: list[str] = field(default_factory=list)
    mock_chunk_delay_s: float = 0.0
    mock_first_token_delay_s: float = 0.0
    timeout_s: float = 60.0
    stream_timeout_s: float = 120.0
    write: bool = True


@dataclass
class HostOutcome:
    envelope: ResultEnvelope
    run_dir: Path | None
    session: HostqSession


@contextlib.contextmanager
def _target(profile: EndpointProfile, opts: HostOptions) -> Iterator[tuple[EndpointProfile, dict[str, str]]]:
    """Like the runner's ``resolve_target``, but the in-process mock also gets the canary oracle rules and the
    host-speed knobs (delays), which is what makes several differently-fast mock "hosts" possible."""
    if profile.spawn_mock or MOCK_PLACEHOLDER in profile.base_url:
        from glmkit.mock import MockConfig, MockServer

        cfg = MockConfig(
            defects=list(opts.mock_defects),
            rules=probes.mock_rules(probes.load_set("canaries")),
            chunk_delay_s=opts.mock_chunk_delay_s,
            first_token_delay_s=opts.mock_first_token_delay_s,
        )
        with MockServer(cfg) as srv:
            yield profile.with_base_url(profile.base_url.replace(MOCK_PLACEHOLDER, srv.url)), {}
    else:
        with resolve_target(profile, opts.mock_defects) as resolved:
            yield resolved


def _check_observed(item: ItemResult, check_name: str) -> tuple[str | None, Any]:
    c = next((c for c in item.checks if c.name == check_name), None)
    return (c.status, c.observed) if c else (None, None)


def build_host_quality(env: ResultEnvelope, ref: Reference | None, label: str) -> dict[str, Any]:
    """Gates, sections and the verdict from the items of a host run."""
    gates: list[dict[str, Any]] = []
    sections: dict[str, Any] = {}
    by_case = {i.group: i for i in env.items if i.status != "skip"}
    for case_id, check_name in GATE_CHECKS.items():
        item = by_case.get(case_id)
        if item is None:
            continue
        status, observed = _check_observed(item, check_name)
        check = next((c for c in item.checks if c.name == check_name), None)
        if status == "pass":
            g = "pass"
        elif status == "fail":
            g = "flag"
        else:
            g = "not_evaluated"
        hard = (item.severity or "") in ("critical", "major")
        gates.append(
            {
                "id": case_id,
                "title": item.title,
                "check": check_name,
                "status": g,
                "severity": "hard" if hard else "soft",
                "message": check.message if check else item.reason,
                "needs_reference": case_id in NEEDS_REFERENCE,
                "item": item.id,
            }
        )
        sections[case_id] = observed
    flagged_hard = [g for g in gates if g["status"] == "flag" and g["severity"] == "hard"]
    flagged_soft = [g for g in gates if g["status"] == "flag" and g["severity"] == "soft"]
    ref_gates = [g for g in gates if g["needs_reference"]]
    ref_evaluated = [g for g in ref_gates if g["status"] != "not_evaluated"]
    if flagged_hard:
        verdict, why = "fail", "fingerprint or identity gate flagged: " + ", ".join(g["id"] for g in flagged_hard)
    elif flagged_soft:
        verdict, why = "warn", "latency gate flagged: " + ", ".join(g["id"] for g in flagged_soft)
    elif not gates:
        verdict, why = "incomplete", "no host-quality case ran"
    elif ref_gates and not ref_evaluated:
        verdict, why = (
            "incomplete",
            "no reference loaded: fingerprint gates not evaluated (record one, then pass --reference)",
        )
    else:
        verdict, why = "pass", "every evaluated gate passed"
    return {
        "label": label,
        "verdict": verdict,
        "reason": why,
        "reference": ref.summary() if ref else None,
        "gates": gates,
        "sections": sections,
    }


def _prepare(lc: Any, opts: HostOptions) -> Any:
    """Apply per-run repeat overrides without touching the case files."""
    case = lc.case
    n = None
    if case.id in LATENCY_CASES and opts.latency_n:
        n = opts.latency_n
    elif case.id in SAMPLED_CASES and opts.samples:
        n = opts.samples
    if n and n != case.repeats:
        lc = type(lc)(case=case.model_copy(update={"repeats": n}), path=lc.path, root=lc.root, sha256=lc.sha256)
    return lc


async def run_host(profile: EndpointProfile, opts: HostOptions) -> HostOutcome:
    cases = load_cases(opts.case_roots, opts.patterns, include_opt_in=True)
    started = datetime.now(UTC)
    ref = opts.reference
    env = ResultEnvelope(
        run_id=new_run_id(COMPONENT, started),
        component=COMPONENT,
        started_at=started,
        config={
            "label": opts.label or profile.name,
            "patterns": opts.patterns,
            "mock_defects": opts.mock_defects,
            "reference_id": ref.reference_id if ref else None,
            "probe_sets": probes.probe_set_ids(),
            "latency_n": opts.latency_n,
            "samples": opts.samples,
            "mock": {"chunk_delay_s": opts.mock_chunk_delay_s, "first_token_delay_s": opts.mock_first_token_delay_s}
            if profile.spawn_mock or MOCK_PLACEHOLDER in profile.base_url
            else None,
            "cases": [{"id": lc.case.id, "file": lc.rel, "sha256": lc.sha256} for lc in cases],
        },
    )
    session = HostqSession(reference=ref)
    redactor = Redactor([profile.api_key()])
    out_dir = run_dir(opts.out_dir, env.run_id) if opts.write else None
    with contextlib.ExitStack() as stack:
        log = stack.enter_context(run_log(out_dir / "run.log", redactor)) if out_dir else _NullLog()
        target, extra_headers = stack.enter_context(_target(profile, opts))
        env.targets = [TargetInfo.from_profile(target)]
        stack.enter_context(use_session(session))
        client = GLMClient(
            target,
            timeout_s=opts.timeout_s,
            stream_timeout_s=opts.stream_timeout_s,
            extra_headers=extra_headers,
            redactor=redactor,
        )
        async with client:
            for lc in cases:
                lc = _prepare(lc, opts)
                case = lc.case
                why = applicability(case, target)
                for mode in case.modes:
                    if why:
                        env.items.append(
                            ItemResult(
                                id=f"{case.id}[{mode}]",
                                group=case.id,
                                title=case.title,
                                kind="conformance_case",
                                category=case.category,
                                severity=case.severity,
                                target=target.name,
                                status="skip",
                                reason=why,
                                labels={"mode": mode},
                            )
                        )
                        continue
                    env.items.append(await run_item(lc, target, client, mode, None, log))
    env.finalize()
    hq = build_host_quality(env, ref, opts.label or profile.name)
    env.verdict = hq["verdict"]
    env.data = {"host_quality": hq}
    if out_dir is not None:
        add_artifact(env, "log", out_dir / "run.log", out_dir)
        write_envelope(env, opts.out_dir, redactor)
    return HostOutcome(envelope=env, run_dir=out_dir, session=session)


def record_reference(outcome: HostOutcome, label: str, samples: int | None) -> Reference:
    """Build a reference from the raw samples the checks left in the session."""
    env, col = outcome.envelope, outcome.session.collected
    t = env.targets[0]
    ref = new_reference(
        label=label,
        target={
            "profile": t.profile,
            "title": t.title,
            "provider": t.provider,
            "model": t.model,
            "endpoint_class": t.endpoint_class,
            "protocol": t.protocol,
            "base_url": t.base_url,
        },
        config={
            "run_id": env.run_id,
            "samples": samples,
            "cases": {c["id"]: c["sha256"] for c in env.config.get("cases", [])},
            "mock_defects": env.config.get("mock_defects") or [],
        },
    )
    ref.action = col.get("action", {})
    ref.text = col.get("text", {})
    ref.self_agreement = col.get("self_agreement", {})
    ref.canary = col.get("canary", {})
    return ref.seal()
