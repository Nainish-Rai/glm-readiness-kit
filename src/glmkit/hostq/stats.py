"""Statistics for the host-quality gates, numpy only, all randomised procedures take an explicit seed.

- ``mmd_permutation_test``: two-sample test with a kernel on (group, value) items. Items only compare within
  the same group (a prompt), and labels are shuffled within groups, so prompt mix cannot create a difference.
  AgentProv uses a categorical kernel over tool choices, Model Equality Testing a Hamming kernel over strings.
- ``wilson`` / ``ratio_ci``: binomial proportion CI and percent-of-reference with a 95% CI (Katz log method).
- ``median_shift_test``: one-sided permutation test that ``b`` is slower than ``a`` (difference of medians).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

Item = tuple[str, Any]  # (group, value)
Kernel = Callable[[Any, Any], float]
Z95 = 1.959963984540054
EPS = 1e-12


@dataclass(frozen=True)
class MMDResult:
    mmd2: float
    p_value: float
    n_x: int
    n_y: int
    n_perm: int
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def categorical_kernel(a: Any, b: Any) -> float:
    return 1.0 if a == b else 0.0


def hamming_kernel(length: int = 64) -> Kernel:
    """Fraction of equal positions after padding/truncating both strings to ``length`` characters."""

    def k(a: Any, b: Any) -> float:
        sa, sb = str(a)[:length].ljust(length), str(b)[:length].ljust(length)
        return sum(1 for p, q in zip(sa, sb, strict=True) if p == q) / length

    return k


def mmd_permutation_test(
    x: Sequence[Item], y: Sequence[Item], kernel: Kernel, *, n_perm: int = 1999, seed: int = 0
) -> MMDResult:
    """Biased MMD^2 between samples ``x`` and ``y`` with a permutation p-value (labels shuffled within groups)."""
    n, m = len(x), len(y)
    if n == 0 or m == 0:
        raise ValueError("both samples need at least one item")
    items = [*x, *y]
    total = len(items)
    names = sorted({g for g, _ in items})
    gid = np.array([names.index(g) for g, _ in items])
    gram = np.zeros((total, total))
    for i in range(total):
        for j in range(i, total):
            if items[i][0] == items[j][0]:
                gram[i, j] = gram[j, i] = kernel(items[i][1], items[j][1])
    labels = np.concatenate([np.ones(n), np.zeros(m)])

    def weights(lab: np.ndarray) -> np.ndarray:
        return lab / n - (1.0 - lab) / m

    w0 = weights(labels)
    observed = float(w0 @ gram @ w0)
    rng = np.random.default_rng(seed)
    base = np.argsort(gid, kind="stable")
    perm_labels = np.empty((n_perm, total))
    for r in range(n_perm):
        order = np.lexsort((rng.random(total), gid))  # random order inside each group block
        row = np.empty(total)
        row[base] = labels[order]
        perm_labels[r] = row
    w = weights(perm_labels)
    stats = ((w @ gram) * w).sum(axis=1)
    p = (1 + int((stats >= observed - EPS).sum())) / (1 + n_perm)
    return MMDResult(mmd2=observed, p_value=p, n_x=n, n_y=m, n_perm=n_perm, seed=seed)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def ratio_ci(k1: int, n1: int, k2: int, n2: int, z: float = Z95) -> dict[str, float | None]:
    """Percent-of-reference: ``(k1/n1) / (k2/n2)`` with a 95% CI (Katz log method; the standard error uses the
    (k+0.5)/(n+1) adjusted proportions so a perfect score still has a finite interval)."""
    if n1 <= 0 or n2 <= 0 or k2 == 0:
        return {"ratio": None, "lo": None, "hi": None}
    ratio = (k1 / n1) / (k2 / n2)
    a1, a2 = (k1 + 0.5) / (n1 + 1), (k2 + 0.5) / (n2 + 1)
    se = math.sqrt((1 - a1) / (n1 * a1) + (1 - a2) / (n2 * a2))
    if k1 == 0:
        return {"ratio": ratio, "lo": 0.0, "hi": math.exp(math.log(a1 / a2) + z * se)}
    lg = math.log(ratio)
    return {"ratio": ratio, "lo": math.exp(lg - z * se), "hi": math.exp(lg + z * se)}


def quantile(values: Sequence[float], q: float) -> float | None:
    return float(np.percentile(np.asarray(values, dtype=float), q * 100)) if len(values) else None


def median(values: Sequence[float]) -> float | None:
    return quantile(values, 0.5)


def summarize(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "p50": median(values),
        "p90": quantile(values, 0.9),
        "p95": quantile(values, 0.95),
        "min": float(min(values)) if len(values) else None,
        "max": float(max(values)) if len(values) else None,
    }


def median_shift_test(
    a: Sequence[float], b: Sequence[float], *, n_perm: int = 1999, seed: int = 0
) -> dict[str, float | int | None]:
    """One-sided permutation test that ``b`` has a larger median than ``a``. Returns the median difference
    ``median(b) - median(a)`` and the p-value."""
    if len(a) < 2 or len(b) < 2:
        return {"delta": None, "p_value": None, "n_a": len(a), "n_b": len(b)}
    av, bv = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    delta = float(np.median(bv) - np.median(av))
    pooled = np.concatenate([av, bv])
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pooled)
        if float(np.median(pooled[len(av) :]) - np.median(pooled[: len(av)])) >= delta - EPS:
            hits += 1
    return {"delta": delta, "p_value": (1 + hits) / (1 + n_perm), "n_a": len(a), "n_b": len(b)}
