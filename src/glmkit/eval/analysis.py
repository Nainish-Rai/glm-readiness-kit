"""Scored calls -> paired statistics per task type and candidate -> result envelope (component ``eval``).

Unit of analysis: the item. Its score on a target is the mean over the K replays that were scored; the cluster
(session, ticket, customer) is resampled as a whole. Throttled calls that never succeeded are not scored, and an
item enters a comparison only when both the baseline and the candidate have at least one scored replay.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from glmkit.contracts.envelope import CheckResult, ItemResult, ResultEnvelope, TargetInfo
from glmkit.eval import stats
from glmkit.eval.calls import CallOutcome
from glmkit.eval.goldenset import GoldenItem, GoldenSet
from glmkit.eval.graders import Grade
from glmkit.eval.graders.judge import JudgeService, family_of
from glmkit.eval.pricing import PriceTable

if TYPE_CHECKING:
    from glmkit.eval.runner import EvalOptions, TargetSpec

ALL = "ALL"
TOP_REGRESSIONS = 10


@dataclass
class Rec:
    item: GoldenItem
    target: str
    replay: int
    outcome: CallOutcome
    grade: Grade | None  # None: throttled for good, not scored
    cost: float | None

    @property
    def judged(self) -> bool:
        return "judge" in (self.grade.data if self.grade else {}) or bool(
            self.grade and self.grade.data.get("baseline")
        )


@dataclass
class Cell:
    """All replays of one item on one target."""

    item: GoldenItem
    target: str
    recs: list[Rec]

    @property
    def scored(self) -> list[Rec]:
        return [r for r in self.recs if r.grade is not None and not r.grade.data.get("judge_error")]

    @property
    def score(self) -> float | None:
        s = self.scored
        return stats.mean([r.grade.score for r in s]) if s else None  # type: ignore[union-attr]

    @property
    def judged(self) -> bool:
        return any(r.judged for r in self.recs)

    @property
    def cost(self) -> float | None:
        ok = [r for r in self.recs if r.outcome.status == "ok"]
        if not ok or any(r.cost is None for r in ok):
            return None
        return stats.mean([r.cost for r in ok])  # type: ignore[misc]

    def mean_of(self, fn: Any) -> float | None:
        vals = [fn(r) for r in self.recs if r.outcome.status == "ok"]
        vals = [v for v in vals if v is not None]
        return stats.mean(vals) if vals else None


def _latency(r: Rec) -> float | None:
    return r.outcome.latency_ms


def _cell_latency(c: Cell) -> float | None:
    return c.mean_of(_latency)


def _out_tokens(r: Rec) -> float | None:
    return r.outcome.usage.get("output_tokens_total")


def _reasoning_tokens(r: Rec) -> float | None:
    return r.outcome.usage.get("reasoning_tokens")


def recommendation(quality: dict[str, Any], cost: dict[str, Any] | None, scope: str = "this task type") -> str:
    v = quality.get("verdict")
    ratio = (cost or {}).get("ratio")
    if v == "non_inferior":
        if ratio is not None and ratio < 1:
            return f"switch: quality within the margin at {ratio:.2f}x the baseline cost"
        if ratio is not None:
            return f"quality is within the margin but cost is {ratio:.2f}x the baseline: no saving"
        return "switch on quality; cost not logged for this target (no price entry)"
    if v == "inferior":
        return f"do not switch on {scope}; consider routing it to the baseline"
    extra = quality.get("extra_items_needed")
    return f"inconclusive: need about {extra} more items" if extra else "inconclusive"


def _pairs(cells_b: dict[str, Cell], cells_c: dict[str, Cell], value: Any, items: list[str]) -> list[stats.Pair]:
    out: list[stats.Pair] = []
    for iid in items:
        b, c = cells_b.get(iid), cells_c.get(iid)
        if b is None or c is None:
            continue
        vb, vc = value(b), value(c)
        if vb is None or vc is None:
            continue
        out.append((b.item.cluster_id, float(vb), float(vc)))
    return out


def _target_summary(cells: list[Cell]) -> dict[str, Any]:
    scored = [c for c in cells if c.score is not None]
    binary = [c for c in scored if not c.judged]
    ok_recs = [r for c in cells for r in c.recs if r.outcome.status == "ok"]
    lat = [r.outcome.latency_ms for r in ok_recs if r.outcome.latency_ms is not None]
    outs = [v for r in ok_recs if (v := _out_tokens(r)) is not None]
    reas = [v for r in ok_recs if (v := _reasoning_tokens(r)) is not None]
    costs = [c.cost for c in cells if c.cost is not None]
    all_priced = bool(cells) and len(costs) == len([c for c in cells if any(r.outcome.status == "ok" for r in c.recs)])
    wins = sum(1 for c in binary if c.score is not None and c.score > 0.5)
    lo, hi = stats.wilson(wins, len(binary)) if binary else (None, None)
    cost_1k = stats.mean(costs) * 1000 if costs and all_priced else None
    successes = sum(c.score for c in binary if c.score is not None)
    total_cost = sum(costs) if costs and all_priced else None
    cps = (total_cost / successes) if total_cost is not None and successes > 0 else None
    return {
        "n_items": len(scored),
        "n_binary_items": len(binary),
        "score_mean": stats.mean([c.score for c in scored]) if scored else None,  # type: ignore[misc]
        "pass_rate": wins / len(binary) if binary else None,
        "pass_rate_wilson": [lo, hi] if binary else None,
        "cost_per_1000_usd": cost_1k,
        "cost_per_success_usd": cps,
        "solves_per_100_usd": (100 / cps) if cps else None,
        "latency_p50_ms": stats.median(lat) if lat else None,
        "latency_p95_ms": stats.quantile(lat, 0.95) if lat else None,
        "output_tokens_median": stats.median(outs) if outs else None,
        "output_tokens_p95": stats.quantile(outs, 0.95) if outs else None,
        "reasoning_tokens_median": stats.median(reas) if reas else None,
        "truncated_share": (sum(1 for r in ok_recs if r.outcome.finish_reason == "length") / len(ok_recs))
        if ok_recs
        else None,
        "call_errors": sum(1 for c in cells for r in c.recs if r.outcome.status == "error"),
        "throttled_retries": sum(r.outcome.throttled_retries for c in cells for r in c.recs),
        "throttled_final": sum(1 for c in cells for r in c.recs if r.outcome.status == "throttled"),
    }


def _reliability(cells_b: dict[str, Cell], cells_c: dict[str, Cell], items: list[str]) -> dict[str, Any] | None:
    multi = [i for i in items if len(cells_c[i].scored) > 1 and not cells_c[i].judged]
    if not multi:
        return None

    def side(cells: dict[str, Cell]) -> dict[str, float]:
        correct = [sum(1 for r in cells[i].scored if r.grade.score > 0.5) for i in multi]  # type: ignore[union-attr]
        ks = [len(cells[i].scored) for i in multi]
        return {"pass_at_k": stats.pass_at_k(correct), "pass_hat_k": stats.pass_hat_k(correct, ks)}

    return {"items": len(multi), "baseline": side(cells_b), "candidate": side(cells_c)}


def _judge_audit(
    cells: dict[str, dict[str, Cell]],
    baseline: str,
    cand_labels: list[str],
    svc: JudgeService | None,
    specs: list[TargetSpec],
) -> dict[str, Any] | None:
    if svc is None:
        return None
    per: dict[str, Any] = {}
    for label in cand_labels:
        verdicts = [
            (r.grade.data["judge"], r.grade.score)  # type: ignore[union-attr]
            for c in cells[label].values()
            for r in c.recs
            if r.grade and "judge" in r.grade.data
        ]
        if not verdicts:
            continue
        n = len(verdicts)
        incons = [v for v, _ in verdicts if not v["consistent"]]
        strata: dict[str, list[float]] = {"candidate shorter": [], "similar length": [], "candidate longer": []}
        for v, score in verdicts:
            ratio = (v["len_cand"] or 1) / max(1, v["len_base"])
            key = "candidate shorter" if ratio < 1 / 1.3 else "candidate longer" if ratio > 1.3 else "similar length"
            strata[key].append(score)
        per[label] = {
            "judged": n,
            "position_inconsistent": len(incons),
            "position_inconsistency_rate": len(incons) / n,
            "position_bias_first": sum(1 for v, _ in verdicts if v["position_bias"] == "first"),
            "position_bias_second": sum(1 for v, _ in verdicts if v["position_bias"] == "second"),
            "longer_won": sum(1 for v, _ in verdicts if v["longer_won"]),
            "candidate_wins": sum(1 for v, _ in verdicts if v["outcome"] == "cand"),
            "baseline_wins": sum(1 for v, _ in verdicts if v["outcome"] == "base"),
            "ties_or_inconsistent": sum(1 for v, _ in verdicts if v["outcome"] == "tie"),
            "win_score_by_length": {
                k: {"n": len(v), "mean_score": stats.mean(v) if v else None} for k, v in strata.items()
            },
        }
    judge_family = svc.family
    overlap = sorted({s.label for s in specs if family_of(s.profile.model) == judge_family})
    return {
        "judge_model": svc.model,
        "judge_family": judge_family,
        "swap": svc.swap,
        "family_overlap": overlap,
        "warnings": [f"judge family {judge_family!r} is also the family of {', '.join(overlap)}: self-preference risk"]
        if overlap
        else [],
        "calls": svc.calls,
        "cache_hits": svc.cache_hits,
        "per_target": per,
        "note": "Judge scores are win scores against the baseline's answer (0.5 = tie or inconsistent); "
        "a win counts only when both position orders agree. Calibrate against human labels before relying on it.",
    }


def analyse(
    *,
    recs: list[Rec],
    gs: GoldenSet,
    specs: list[TargetSpec],
    baseline: str,
    opts: EvalOptions,
    prices: PriceTable,
    judge: JudgeService | None,
    judge_profile: Any,
    run_id: str,
    started: datetime,
    targets: list[TargetInfo],
    budget_skipped: int,
    resumed_calls: int,
    new_calls: int,
    cache: dict[str, CallOutcome],
) -> ResultEnvelope:
    labels = [s.label for s in specs]
    cand_labels = [lb for lb in labels if lb != baseline]
    by: dict[str, dict[str, Cell]] = {lb: {} for lb in labels}
    for r in recs:
        cell = by[r.target].setdefault(r.item.id, Cell(r.item, r.target, []))
        cell.recs.append(r)
    for cs in by.values():
        for c in cs.values():
            c.recs.sort(key=lambda r: r.replay)
    items = {it.id: it for it in gs.items}
    task_types = sorted({it.task_type for it in gs.items})

    target_summary = {lb: _target_summary(list(by[lb].values())) for lb in labels}

    comparisons: list[dict[str, Any]] = []
    regressions: dict[str, Any] = {}
    for cand in cand_labels:
        both = [
            iid
            for iid in items
            if iid in by[baseline]
            and iid in by[cand]
            and by[baseline][iid].score is not None
            and by[cand][iid].score is not None
        ]
        rows: list[dict[str, Any]] = []
        for tt in [*task_types, ALL]:
            ids = [i for i in both if tt == ALL or items[i].task_type == tt]
            seed = stats.sub_seed(opts.seed, tt, cand)
            qp = _pairs(by[baseline], by[cand], lambda c: c.score, ids)
            quality = stats.paired_summary(
                qp, margin=opts.margin, seed=seed, n_boot=opts.n_boot, min_items=opts.min_items
            )
            binary_ids = [i for i in ids if not by[cand][i].judged]
            mc = stats.mcnemar_summary(_pairs(by[baseline], by[cand], lambda c: c.score, binary_ids))
            cost_pairs = _pairs(by[baseline], by[cand], lambda c: c.cost, ids)
            cost = (
                stats.paired_metric_summary(cost_pairs, seed=seed + 1, n_boot=min(opts.n_boot, 2000))
                if cost_pairs
                else None
            )
            lat_pairs = _pairs(by[baseline], by[cand], _cell_latency, ids)
            latency = (
                stats.paired_metric_summary(lat_pairs, seed=seed + 2, n_boot=min(opts.n_boot, 2000))
                if lat_pairs
                else None
            )
            tok_pairs = _pairs(by[baseline], by[cand], lambda c: c.mean_of(_out_tokens), ids)
            tokens = (
                stats.paired_metric_summary(tok_pairs, seed=seed + 3, n_boot=min(opts.n_boot, 2000))
                if tok_pairs
                else None
            )
            rows.append(
                {
                    "task_type": tt,
                    "target": cand,
                    "n": len(ids),
                    "quality": quality,
                    "mcnemar": mc if binary_ids else None,
                    "cost": cost,
                    "latency": latency,
                    "output_tokens": tokens,
                    "reliability": _reliability(by[baseline], by[cand], ids),
                    "recommendation": recommendation(quality, cost, "the whole workload" if tt == ALL else "this task type"),
                    "adjusted": False,
                }
            )
        per_type = [r for r in rows if r["task_type"] != ALL and r["mcnemar"]]
        for r, p in zip(per_type, stats.holm([r["mcnemar"]["p"] for r in per_type]), strict=True):
            r["mcnemar"]["p_holm"] = p
        comparisons.extend(rows)

        deltas = sorted(
            (by[cand][i].score - by[baseline][i].score, i)  # type: ignore[operator]
            for i in both
            if by[cand][i].score < by[baseline][i].score  # type: ignore[operator]
        )
        regressions[cand] = {
            "worst": [_regression(by, baseline, cand, i, d) for d, i in deltas[:TOP_REGRESSIONS]],
            "critical": [_regression(by, baseline, cand, i, d) for d, i in deltas if items[i].critical],
        }

    cost_quality = [
        {
            "target": lb,
            "profile": s.profile.name,
            "model": s.profile.model,
            "effort": s.effort,
            "quality": target_summary[lb]["score_mean"],
            "pass_rate": target_summary[lb]["pass_rate"],
            "cost_per_1000_usd": target_summary[lb]["cost_per_1000_usd"],
            "baseline": lb == baseline,
        }
        for lb, s in zip(labels, specs, strict=True)
    ]

    item_results = _item_results(by, labels, baseline, gs)
    spent = sum(r.cost or 0.0 for r in recs if r.outcome.status == "ok")
    unpriced = sorted({s.profile.model for s in specs if prices.lookup(s.profile.model, s.profile.provider) is None})
    warnings = []
    if unpriced:
        warnings.append(f"no price entry for {', '.join(unpriced)}: cost shows as not logged (add --prices)")
    if len(task_types) > 1:
        warnings.append(
            "task-type intervals are unadjusted; McNemar p-values carry Holm-adjusted p_holm across task types"
        )
    thr_final = sum(t["throttled_final"] for t in target_summary.values())
    if thr_final:
        warnings.append(f"{thr_final} calls stayed throttled after retries and were left out of scoring (not failures)")
    if budget_skipped:
        warnings.append(
            f"budget cap reached: {budget_skipped} calls were not made; comparisons use complete pairs only"
        )

    env = ResultEnvelope(
        run_id=run_id,
        component="eval",
        started_at=started,
        targets=targets,
        items=item_results,
        config={
            "set": str(opts.set_path),
            "baseline": baseline,
            "targets": labels,
            "judge": judge_profile.name if judge_profile else None,
            "margin": opts.margin,
            "min_items": opts.min_items,
            "seed": opts.seed,
            "n_boot": opts.n_boot,
            "k": opts.k,
            "concurrency": opts.concurrency,
            "budget_usd": opts.budget_usd,
            "max_retries": opts.max_retries,
        },
        data={
            "set": {
                "name": gs.name,
                "fingerprint": gs.fingerprint(),
                "items": len(gs.items),
                "task_types": {tt: sum(1 for i in gs.items if i.task_type == tt) for tt in task_types},
            },
            "baseline": baseline,
            "margin": opts.margin,
            "targets": [
                {
                    "label": s.label,
                    "profile": s.profile.name,
                    "model": s.profile.model,
                    "provider": s.profile.provider,
                    "effort": s.effort,
                }
                for s in specs
            ],
            "target_summary": target_summary,
            "comparisons": comparisons,
            "regressions": regressions,
            "cost_quality": cost_quality,
            "judge": _judge_audit(by, baseline, cand_labels, judge, specs),
            "budget": {
                "cap_usd": opts.budget_usd,
                "spent_usd": spent,
                "exhausted": bool(budget_skipped),
                "calls_skipped": budget_skipped,
            },
            "run": {"resumed_calls": resumed_calls, "new_calls": new_calls, "calls_total": len(cache)},
            "price_table": prices.describe(),
            "warnings": warnings,
            "method": {
                "unit": "item; score = mean over K replays; cluster = item.cluster or item id",
                "ci": "clustered percentile bootstrap (ci) and clustered CLT (ci_clt) of the paired difference",
                "verdict": "non_inferior if CI lower bound > -margin; inferior if upper bound < -margin; else inconclusive",  # noqa: E501
                "reference": "research/03-eval.md section 3",
            },
        },
    ).finalize()
    env.verdict = _verdict(comparisons, budget_skipped, bool(cand_labels))
    return env


def _regression(by: dict[str, dict[str, Cell]], baseline: str, cand: str, iid: str, delta: float) -> dict[str, Any]:
    c = by[cand][iid]
    return {
        "item": iid,
        "task_type": c.item.task_type,
        "critical": c.item.critical,
        "delta": delta,
        "baseline_score": by[baseline][iid].score,
        "candidate_score": c.score,
        "reason": next((r.grade.detail for r in c.scored if r.grade and not r.grade.passed), ""),  # type: ignore[union-attr]
    }


def _verdict(comparisons: list[dict[str, Any]], budget_skipped: int, has_candidates: bool) -> str:
    overall = [c for c in comparisons if c["task_type"] == ALL]
    if not has_candidates or not overall or budget_skipped:
        return "incomplete"
    if any(c["quality"].get("verdict") == "inferior" for c in overall):
        return "fail"
    if any(c["quality"].get("verdict") != "non_inferior" for c in overall):
        return "warn"
    return "pass"


def _item_results(by: dict[str, dict[str, Cell]], labels: list[str], baseline: str, gs: GoldenSet) -> list[ItemResult]:
    out: list[ItemResult] = []
    for item in gs.items:
        base_cell = by[baseline].get(item.id)
        for lb in labels:
            cell = by[lb].get(item.id)
            if cell is None:
                continue
            judged = cell.judged
            score = cell.score
            if not cell.scored and all(r.outcome.status == "throttled" for r in cell.recs):
                status = "throttled"
            elif judged and lb == baseline:
                status = "skip"
            elif score is None:
                status = "error"
            else:
                status = "pass" if score > 0.5 else "fail"
            replays = [
                {
                    "k": r.replay,
                    "status": r.outcome.status,
                    "output": r.outcome.output_text()[:4000],
                    "score": r.grade.score if r.grade else None,
                    "passed": r.grade.passed if r.grade else None,
                    "detail": r.grade.detail if r.grade else r.outcome.error,
                    "grade": {
                        k: v
                        for k, v in (r.grade.data if r.grade else {}).items()
                        if k in ("judge", "flags", "stage", "value")
                    },
                    "cost_usd": r.cost,
                    "latency_ms": r.outcome.latency_ms,
                    "finish_reason": r.outcome.finish_reason,
                    "usage": r.outcome.usage,
                    "throttled_retries": r.outcome.throttled_retries,
                }
                for r in cell.recs
            ]
            delta = (
                score - base_cell.score
                if lb != baseline and base_cell is not None and score is not None and base_cell.score is not None
                else None
            )
            lat = [r.outcome.latency_ms for r in cell.recs if r.outcome.latency_ms is not None]
            out.append(
                ItemResult(
                    id=f"{item.id}@{lb}",
                    group=item.id,
                    title=item.task_type,
                    kind="eval_item",
                    category=item.task_type,
                    severity="info",
                    target=lb,
                    labels={
                        "task_type": item.task_type,
                        "grader": str(gs.grader_for(item).get("type", "exact")),
                        "role": "baseline" if lb == baseline else "candidate",
                    },
                    status=status,
                    reason=cell.recs[0].grade.detail if cell.recs and cell.recs[0].grade else None,
                    checks=[
                        CheckResult(
                            name=f"grade:{gs.grader_for(item).get('type', 'exact')}",
                            status="pass" if (r.grade and r.grade.passed) else "fail",
                            message=(r.grade.detail if r.grade else "throttled, not scored"),
                            attempt=r.replay,
                        )
                        for r in cell.recs
                    ],
                    metrics={
                        "score": score,
                        "delta_vs_baseline": delta,
                        "replays": len(cell.recs),
                        "cost_usd": cell.cost,
                        "total_ms": stats.mean(lat) if lat else None,
                        "output_tokens": cell.mean_of(_out_tokens),
                    },
                    data={
                        "task_type": item.task_type,
                        "cluster": item.cluster_id,
                        "critical": item.critical,
                        "judged": judged,
                        "prompt": _prompt_text(item)[:2000],
                        "expected": item.expected,
                        "replays": replays,
                    },
                )
            )
    return out


def _prompt_text(item: GoldenItem) -> str:
    return "\n\n".join(f"[{m.get('role')}] {m.get('content')}" for m in item.messages if m.get("content") is not None)


def items_by_target(env: ResultEnvelope) -> dict[str, dict[str, ItemResult]]:
    """Index an eval envelope's items as {group: {target: ItemResult}} (used by the dashboard page)."""
    out: dict[str, dict[str, ItemResult]] = defaultdict(dict)
    for it in env.items:
        out[it.group or it.id][it.target or ""] = it
    return out
