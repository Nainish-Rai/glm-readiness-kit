"""Statistics for paired migration evals (research/03-eval.md §3). Pure Python, seeded, no numpy.

* paired difference per item (mean over K replays first), cluster-robust SE (Miller, arXiv 2411.00640, Eq. 8
  without small-sample correction) and a clustered percentile bootstrap CI;
* McNemar exact test on discordant pairs for pass/fail;
* paired clustered bootstrap for ratios and medians (cost, latency);
* non-inferiority verdict against a margin, with the extra items needed when inconclusive;
* Wilson interval, pass@k / pass^k, Holm adjustment.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Sequence
from typing import Any

Z95 = 1.959963984540054
Pair = tuple[str, float, float]  # (cluster id, baseline value, candidate value)


def sub_seed(seed: int, *parts: object) -> int:
    """Stable per-metric seed so results do not depend on the order metrics are computed in."""
    h = hashlib.sha256(("|".join(str(p) for p in (seed, *parts))).encode()).digest()
    return int.from_bytes(h[:8], "big")


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def quantile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy default)."""
    s = sorted(xs)
    if not s:
        raise ValueError("quantile of empty data")
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def median(xs: Sequence[float]) -> float:
    return quantile(xs, 0.5)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value: b = baseline-only passes, c = candidate-only passes.
    Under H0 the discordant pairs split Binomial(b + c, 1/2); p = min(1, 2 * P(X <= min(b, c)))."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values, in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adj[i] = running
    return adj


def pass_at_k(correct: Sequence[int], k: int | None = None) -> float:
    """Share of items solved at least once in their K replays (pass@K)."""
    return mean([1.0 if c > 0 else 0.0 for c in correct]) if correct else float("nan")


def pass_hat_k(correct: Sequence[int], ks: Sequence[int]) -> float:
    """tau-bench style pass^K: share of items solved in every one of their K replays."""
    return mean([1.0 if c == k else 0.0 for c, k in zip(correct, ks, strict=True)]) if correct else float("nan")


# ------------------------------------------------------------------ clustering


def group_clusters(pairs: Sequence[Pair]) -> list[list[tuple[float, float]]]:
    by: dict[str, list[tuple[float, float]]] = {}
    for cid, a, b in pairs:
        by.setdefault(cid, []).append((a, b))
    return [by[k] for k in sorted(by)]


def clustered_se(pairs: Sequence[Pair]) -> float:
    """Cluster-robust SE of the mean paired difference: sqrt(sum_c (sum_{i in c}(d_i - dbar))^2) / n."""
    n = len(pairs)
    if n < 2:
        return float("nan")
    d = [b - a for _, a, b in pairs]
    dbar = mean(d)
    sums: dict[str, float] = {}
    for (cid, _, _), di in zip(pairs, d, strict=True):
        sums[cid] = sums.get(cid, 0.0) + (di - dbar)
    return math.sqrt(sum(s * s for s in sums.values())) / n


def correlation(pairs: Sequence[Pair]) -> float | None:
    a = [p[1] for p in pairs]
    b = [p[2] for p in pairs]
    if len(a) < 2:
        return None
    ma, mb = mean(a), mean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (sa * sb)


# ------------------------------------------------------------------ bootstrap


def cluster_bootstrap(
    pairs: Sequence[Pair],
    stat: Callable[[list[tuple[float, float]]], float],
    *,
    n_boot: int = 4000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile CI of ``stat(list of (baseline, candidate))`` resampling whole clusters with replacement."""
    clusters = group_clusters(pairs)
    g = len(clusters)
    if g == 0:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    reps: list[float] = []
    for _ in range(n_boot):
        sample: list[tuple[float, float]] = []
        for _ in range(g):
            sample.extend(clusters[rng.randrange(g)])
        try:
            reps.append(stat(sample))
        except (ZeroDivisionError, ValueError):
            continue
    if not reps:
        return (float("nan"), float("nan"))
    return (quantile(reps, alpha / 2), quantile(reps, 1 - alpha / 2))


def bootstrap_mean_diff(
    pairs: Sequence[Pair], *, n_boot: int = 4000, seed: int = 0, alpha: float = 0.05
) -> tuple[float, float]:
    """Fast path of ``cluster_bootstrap`` for the mean paired difference (candidate - baseline)."""
    clusters = group_clusters(pairs)
    g = len(clusters)
    if g == 0:
        return (float("nan"), float("nan"))
    sums = [sum(b - a for a, b in c) for c in clusters]
    sizes = [len(c) for c in clusters]
    rng = random.Random(seed)
    reps = []
    for _ in range(n_boot):
        idx = [rng.randrange(g) for _ in range(g)]
        reps.append(sum(sums[i] for i in idx) / sum(sizes[i] for i in idx))
    return (quantile(reps, alpha / 2), quantile(reps, 1 - alpha / 2))


# ------------------------------------------------------------------ paired summary and verdict


def non_inferiority(
    delta: float, ci: tuple[float, float], se: float, n: int, margin: float, min_items: int
) -> dict[str, Any]:
    """Verdict against ``-margin`` (quality: higher is better).

    non_inferior: CI lower bound > -margin.  inferior: CI upper bound < -margin.  Otherwise inconclusive, with the
    number of additional items the CLT half-width needs to separate delta from the margin (if it can)."""
    lo, hi = ci
    out: dict[str, Any] = {"margin": margin, "better": False, "extra_items_needed": None}
    if n < min_items or math.isnan(lo):
        out["verdict"] = "inconclusive"
        out["extra_items_needed"] = max(0, min_items - n)
        out["reason"] = f"n={n} below the minimum of {min_items} items"
        return out
    out["better"] = lo > 0
    if lo > -margin:
        out["verdict"] = "non_inferior"
    elif hi < -margin:
        out["verdict"] = "inferior"
    else:
        out["verdict"] = "inconclusive"
        gap = abs(delta + margin)
        if gap > 1e-12 and not math.isnan(se):
            need = math.ceil((Z95 * se * math.sqrt(n) / gap) ** 2)
            out["extra_items_needed"] = max(0, need - n)
    return out


def paired_summary(
    pairs: Sequence[Pair], *, margin: float, seed: int, n_boot: int = 4000, min_items: int = 20
) -> dict[str, Any]:
    """Headline paired comparison for one (task type, candidate) of per-item scores in [0, 1]."""
    n = len(pairs)
    if n == 0:
        return {"n": 0, "verdict": "inconclusive", "reason": "no paired items"}
    base = [p[1] for p in pairs]
    cand = [p[2] for p in pairs]
    delta = mean(cand) - mean(base)
    se = clustered_se(pairs)
    ci = bootstrap_mean_diff(pairs, n_boot=n_boot, seed=seed)
    ci_clt = (delta - Z95 * se, delta + Z95 * se) if not math.isnan(se) else (float("nan"), float("nan"))
    ni = non_inferiority(delta, ci, se, n, margin, min_items)
    return {
        "n": n,
        "n_clusters": len({p[0] for p in pairs}),
        "mean_baseline": mean(base),
        "mean_candidate": mean(cand),
        "delta": delta,
        "se_clustered": None if math.isnan(se) else se,
        "ci": list(ci),
        "ci_clt": list(ci_clt),
        "correlation": correlation(pairs),
        **ni,
    }


def mcnemar_summary(pairs: Sequence[Pair], threshold: float = 0.5) -> dict[str, Any]:
    """McNemar on one binary per item: pass = mean score over replays > threshold (majority for binary scores)."""
    b = c = both = neither = 0
    for _, x, y in pairs:
        px, py = x > threshold, y > threshold
        if px and py:
            both += 1
        elif px:
            b += 1
        elif py:
            c += 1
        else:
            neither += 1
    return {"b_baseline_only": b, "c_candidate_only": c, "both": both, "neither": neither, "p": mcnemar_exact(b, c)}


def ratio_of_means(sample: list[tuple[float, float]]) -> float:
    return sum(y for _, y in sample) / sum(x for x, _ in sample)


def mean_diff(sample: list[tuple[float, float]]) -> float:
    return mean([y - x for x, y in sample])


def median_diff(sample: list[tuple[float, float]]) -> float:
    return median([y for _, y in sample]) - median([x for x, _ in sample])


def paired_metric_summary(
    pairs: Sequence[Pair], *, seed: int, n_boot: int = 2000, ratio: bool = True
) -> dict[str, Any]:
    """Cost / latency: mean and median per side, paired bootstrap CI of the mean difference, of the median
    difference and (for positive baselines) of the ratio of means."""
    if not pairs:
        return {"n": 0}
    base = [p[1] for p in pairs]
    cand = [p[2] for p in pairs]
    out: dict[str, Any] = {
        "n": len(pairs),
        "mean_baseline": mean(base),
        "mean_candidate": mean(cand),
        "median_baseline": median(base),
        "median_candidate": median(cand),
        "p95_baseline": quantile(base, 0.95),
        "p95_candidate": quantile(cand, 0.95),
        "mean_diff": mean(cand) - mean(base),
        "mean_diff_ci": list(cluster_bootstrap(pairs, mean_diff, n_boot=n_boot, seed=sub_seed(seed, "md"))),
        "median_diff": median(cand) - median(base),
        "median_diff_ci": list(cluster_bootstrap(pairs, median_diff, n_boot=n_boot, seed=sub_seed(seed, "med"))),
    }
    if ratio and sum(base) > 0:
        out["ratio"] = sum(cand) / sum(base)
        out["ratio_ci"] = list(cluster_bootstrap(pairs, ratio_of_means, n_boot=n_boot, seed=sub_seed(seed, "ratio")))
    return out
