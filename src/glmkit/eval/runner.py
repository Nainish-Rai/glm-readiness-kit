"""Replay a golden set on several targets, grade it, and write a result envelope (component ``eval``).

Properties (docs/contracts.md, "Eval"):

* **Concurrent and rate-limit aware**: ``concurrency`` calls in flight; a throttled response pauses every call to
  that target until the retry time, then the call is retried. Throttled calls are counted apart and never failures.
* **Resumable**: every finished call is appended to ``calls.jsonl`` (and every judge verdict to ``judge.jsonl``)
  in the run directory. ``--resume RUN_ID`` replays only what is missing; grading and statistics are recomputed
  from the stored calls, so an interrupted + resumed run equals an uninterrupted one.
* **Budget-capped**: spend is recomputed from the price table; once ``budget_usd`` is reached no new call starts and
  the rest is reported as skipped (verdict ``incomplete``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glmkit.client import GLMClient, MissingKeyError
from glmkit.contracts.envelope import ResultEnvelope, TargetInfo, new_run_id
from glmkit.contracts.profile import MOCK_PLACEHOLDER, EndpointProfile, get_profile
from glmkit.eval.analysis import Rec, analyse
from glmkit.eval.calls import CallOutcome, call_model
from glmkit.eval.goldenset import GoldenItem, GoldenSet, load_golden_set
from glmkit.eval.graders import Grade, GradeContext, grade
from glmkit.eval.graders.judge import JudgeService
from glmkit.eval.pricing import PriceTable
from glmkit.redact import Redactor
from glmkit.results import add_artifact, run_dir, run_log, write_envelope

CALLS_FILE = "calls.jsonl"
JUDGE_FILE = "judge.jsonl"
MANIFEST_FILE = "manifest.json"


class EvalError(RuntimeError):
    """Bad input (unknown target, resume mismatch ...): the CLI exits 2."""


class EvalInterrupted(RuntimeError):
    """Raised by the ``interrupt_after`` test hook after N new calls; the cache holds what finished."""


@dataclass
class TargetSpec:
    label: str
    profile: EndpointProfile
    effort: str | None = None


@dataclass
class EvalOptions:
    set_path: Path
    targets: list[str]
    baseline: str
    judge: str | None = None
    out_dir: Path = Path("results")
    run_id: str | None = None
    resume: bool = False
    concurrency: int = 8
    k: int | None = None  # override every item's replays
    margin: float = 0.05  # non-inferiority margin in score points (0.05 = 5 points of pass rate)
    min_items: int = 20  # fewer paired items than this is always inconclusive
    seed: int = 0
    n_boot: int = 4000
    budget_usd: float | None = None
    prices: list[Path] = field(default_factory=list)
    timeout_s: float = 60.0
    max_retries: int = 6
    max_items: int | None = None
    task_types: list[str] | None = None
    interrupt_after: int | None = None  # test hook


@dataclass
class EvalOutcome:
    envelope: ResultEnvelope
    run_dir: Path


def parse_target(spec: str) -> tuple[str, str | None]:
    """'zai-api@low' -> ('zai-api', 'low'); a path or a plain profile name has no effort."""
    if "@" in spec:
        name, effort = spec.rsplit("@", 1)
        if effort in ("low", "high", "max", "medium", "minimal", "xhigh", "none"):
            return name, effort
    return spec, None


def resolve_targets(options: list[str], baseline: str) -> list[TargetSpec]:
    specs: list[TargetSpec] = []
    seen: set[str] = set()
    for raw in [baseline, *options]:
        if raw in seen:
            continue
        seen.add(raw)
        name, effort = parse_target(raw)
        try:
            profile = get_profile(name)
        except KeyError as e:
            raise EvalError(str(e.args[0])) from e
        label = profile.name + (f"@{effort}" if effort else "")
        if any(s.label == label for s in specs):
            raise EvalError(f"duplicate target {label!r}")
        specs.append(TargetSpec(label, profile, effort))
    return specs


class _Gate:
    """Pauses every call to one target while it is throttled."""

    def __init__(self) -> None:
        self.until = 0.0

    async def wait(self) -> None:
        d = self.until - time.monotonic()
        if d > 0:
            await asyncio.sleep(d)

    async def sleep(self, seconds: float) -> None:
        self.until = max(self.until, time.monotonic() + seconds)
        await asyncio.sleep(seconds)


def _call_key(item_id: str, label: str, k: int) -> str:
    return f"{item_id}|{label}|{k}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:  # a line cut off by a kill: that call is simply redone
                continue
    return rows


@contextlib.contextmanager
def _mock_if_needed(profiles: list[EndpointProfile]) -> Iterator[dict[str, EndpointProfile]]:
    """Start ONE in-process mock for every spawn_mock / '{mock}' profile and point them at it."""
    needs = [p for p in profiles if p.spawn_mock or MOCK_PLACEHOLDER in p.base_url]
    if not needs:
        yield {p.name: p for p in profiles}
        return
    from glmkit.mock import MockConfig, MockServer

    with MockServer(MockConfig()) as srv:
        yield {
            p.name: (
                p.model_copy(update={"base_url": p.base_url.replace(MOCK_PLACEHOLDER, srv.url), "spawn_mock": False})
                if p in needs
                else p
            )
            for p in profiles
        }


def config_fingerprint(gs: GoldenSet, specs: list[TargetSpec], opts: EvalOptions) -> str:
    blob = json.dumps(
        {
            "set": gs.fingerprint(),
            "targets": [(s.label, s.profile.model, s.profile.provider, s.profile.protocol) for s in specs],
            "k": opts.k,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _filter_set(gs: GoldenSet, opts: EvalOptions) -> GoldenSet:
    items = gs.items
    if opts.task_types:
        items = [i for i in items if i.task_type in opts.task_types]
    if opts.max_items:
        items = items[: opts.max_items]
    if not items:
        raise EvalError("no items to run after filtering")
    return gs.model_copy(update={"items": items})


async def run_eval(opts: EvalOptions) -> EvalOutcome:
    started = datetime.now(UTC)
    gs = _filter_set(load_golden_set(opts.set_path), opts)
    specs = resolve_targets(opts.targets, opts.baseline)
    baseline = specs[0].label
    for it in gs.items:
        gs.grader_for(it)  # fail fast on an item without grader
    prices = PriceTable.load(*opts.prices)
    redactor = Redactor()

    rid = opts.run_id or new_run_id("eval", started)
    rdir = opts.out_dir / rid
    fp = config_fingerprint(gs, specs, opts)
    manifest_path = rdir / MANIFEST_FILE
    if rdir.exists() and manifest_path.exists():
        if not opts.resume:
            raise EvalError(f"run {rid} already exists; pass --resume to continue it")
        if json.loads(manifest_path.read_text())["fingerprint"] != fp:
            raise EvalError(f"run {rid} was started with a different golden set or targets; refusing to mix")
    elif opts.resume:
        raise EvalError(f"nothing to resume: {rdir} has no {MANIFEST_FILE}")
    rdir = run_dir(opts.out_dir, rid)
    manifest_path.write_text(
        json.dumps({"fingerprint": fp, "started_at": started.isoformat(), "set": gs.name}), encoding="utf-8"
    )

    cache: dict[str, CallOutcome] = {}
    for row in _read_jsonl(rdir / CALLS_FILE):
        cache[row["key"]] = CallOutcome.from_json(row["outcome"])
    resumed_calls = len(cache)

    all_profiles = [s.profile for s in specs]
    judge_profile = None
    if opts.judge:
        try:
            judge_profile = get_profile(opts.judge)
        except KeyError as e:
            raise EvalError(str(e.args[0])) from e
        all_profiles.append(judge_profile)

    with _mock_if_needed(all_profiles) as live, run_log(rdir / "run.log", redactor) as log:
        resolved = {s.label: TargetSpec(s.label, live[s.profile.name], s.effort) for s in specs}
        try:
            async with contextlib.AsyncExitStack() as stack:
                clients = {
                    label: await stack.enter_async_context(
                        GLMClient(s.profile, timeout_s=opts.timeout_s, redactor=redactor)
                    )
                    for label, s in resolved.items()
                }
                judge_svc = None
                if judge_profile is not None:
                    jc = await stack.enter_async_context(
                        GLMClient(live[judge_profile.name], timeout_s=opts.timeout_s, redactor=redactor)
                    )
                    judge_svc = JudgeService(
                        jc,
                        headers=_mock_headers(live[judge_profile.name], 0),
                        cache_path=rdir / JUDGE_FILE,
                        semaphore=asyncio.Semaphore(max(1, opts.concurrency // 2)),
                    )
                new_calls, skipped = await _replay(gs, resolved, clients, cache, rdir, opts, prices, redactor, log)
                recs = await _grade_all(gs, resolved, baseline, cache, judge_svc, opts, prices)
        except MissingKeyError as e:
            raise EvalError(str(e)) from e

    targets = [TargetInfo.from_profile(s.profile) for s in resolved.values()]
    env = analyse(
        recs=recs,
        gs=gs,
        specs=list(resolved.values()),
        baseline=baseline,
        opts=opts,
        prices=prices,
        judge=judge_svc,
        judge_profile=live[judge_profile.name] if judge_profile else None,
        run_id=rid,
        started=started,
        targets=targets,
        budget_skipped=skipped,
        resumed_calls=resumed_calls,
        new_calls=new_calls,
        cache=cache,
    )
    add_artifact(env, "calls", rdir / CALLS_FILE, rdir)
    if (rdir / JUDGE_FILE).exists():
        add_artifact(env, "judge", rdir / JUDGE_FILE, rdir)
    write_envelope(env, opts.out_dir, redactor)
    scrub_file(rdir / CALLS_FILE, redactor)
    return EvalOutcome(env, rdir)


def scrub_file(path: Path, redactor: Redactor) -> None:
    if redactor.active and path.exists():
        path.write_text(redactor.text(path.read_text(encoding="utf-8")), encoding="utf-8")


def _mock_headers(profile: EndpointProfile, replay: int) -> dict[str, str]:
    """Per-call header that makes mock targets draw independently per replay (real hosts get nothing extra)."""
    return {"X-GLMKit-Sample": str(replay)} if profile.provider == "mock" else {}


def _canon(item: GoldenItem, gs: GoldenSet, spec: TargetSpec) -> dict[str, Any]:
    canon = item.canonical_request(gs.params)
    if spec.effort:
        canon.setdefault("params", {})["reasoning_effort"] = spec.effort
    return canon


async def _replay(
    gs: GoldenSet,
    specs: dict[str, TargetSpec],
    clients: dict[str, GLMClient],
    cache: dict[str, CallOutcome],
    rdir: Path,
    opts: EvalOptions,
    prices: PriceTable,
    redactor: Redactor,
    log: Any,
) -> tuple[int, int]:
    sem = asyncio.Semaphore(max(1, opts.concurrency))
    gates = {label: _Gate() for label in specs}
    spent = 0.0
    for key, out in cache.items():
        label = key.split("|")[1]
        c = (
            prices.cost_usd(specs[label].profile.model, specs[label].profile.provider, out.usage)
            if out.status == "ok"
            else None
        )
        spent += c or 0.0
    state = {"new": 0, "skipped": 0, "spent": spent, "stop": False}
    calls_path = rdir / CALLS_FILE
    fh = calls_path.open("a", encoding="utf-8")

    async def one(item: GoldenItem, label: str, k: int) -> None:
        key = _call_key(item.id, label, k)
        if key in cache:
            return
        spec = specs[label]
        async with sem:
            if state["stop"]:
                return
            if opts.budget_usd is not None and state["spent"] >= opts.budget_usd:
                state["skipped"] += 1
                return
            gate = gates[label]
            await gate.wait()
            out = await call_model(
                clients[label],
                _canon(item, gs, spec),
                headers=_mock_headers(spec.profile, k),
                max_retries=opts.max_retries,
                sleep=gate.sleep,
            )
            if out.throttled_retries:
                log.info("throttled %s x%d (%s)", label, out.throttled_retries, item.id)
            cache[key] = out
            state["new"] += 1
            if out.status == "ok":
                state["spent"] += prices.cost_usd(spec.profile.model, spec.profile.provider, out.usage) or 0.0
            fh.write(json.dumps({"key": key, "outcome": out.to_json()}, ensure_ascii=False) + "\n")
            fh.flush()
            if opts.interrupt_after is not None and state["new"] >= opts.interrupt_after:
                state["stop"] = True

    jobs = [one(item, label, k) for item in gs.items for k in range(opts.k or gs.k_for(item)) for label in specs]
    try:
        await asyncio.gather(*jobs)
    finally:
        fh.close()
        scrub_file(calls_path, redactor)
    if state["stop"]:
        raise EvalInterrupted(f"stopped after {state['new']} new calls")
    return state["new"], state["skipped"]


async def _grade_all(
    gs: GoldenSet,
    specs: dict[str, TargetSpec],
    baseline: str,
    cache: dict[str, CallOutcome],
    judge: JudgeService | None,
    opts: EvalOptions,
    prices: PriceTable,
) -> list[Rec]:
    async def one(item: GoldenItem, label: str, k: int) -> Rec | None:
        out = cache.get(_call_key(item.id, label, k))
        if out is None:
            return None
        spec = specs[label]
        cost = prices.cost_usd(spec.profile.model, spec.profile.provider, out.usage) if out.status == "ok" else None
        if out.status == "throttled":
            return Rec(item, label, k, out, None, cost)
        if out.status == "error":
            return Rec(
                item, label, k, out, Grade(0.0, False, f"call failed: {out.error}"[:200], {"call_error": True}), cost
            )
        base_out = cache.get(_call_key(item.id, baseline, k))
        ctx = GradeContext(
            item=item,
            config=gs.grader_for(item),
            output=out.output_text(),
            target=label,
            baseline_output=base_out.output_text() if base_out and base_out.status == "ok" else None,
            is_baseline=label == baseline,
            replay=k,
            judge=judge,
        )
        return Rec(item, label, k, out, await grade(ctx), cost)

    jobs = [one(item, label, k) for item in gs.items for k in range(opts.k or gs.k_for(item)) for label in specs]
    return [r for r in await asyncio.gather(*jobs) if r is not None]
