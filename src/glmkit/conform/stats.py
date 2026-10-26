"""Small statistics helpers for statistical conformance cases."""

from __future__ import annotations

import math

Z95 = 1.959963984540054  # two-sided 95% normal quantile


def wilson_interval(successes: int, trials: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. ``(0.0, 1.0)`` when there are no trials."""
    if trials <= 0:
        return 0.0, 1.0
    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    centre = (p + z2 / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def f1_scores(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """(precision, recall, F1); 0.0 where undefined."""
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return precision, recall, f1
