"""Eval statistics against hand-computed examples, plus simulated CI coverage."""

from __future__ import annotations

import math
import random

import pytest

from glmkit.eval import stats


def test_mcnemar_exact_hand_computed() -> None:
    # b=5, c=1: n=6, P(X<=1)=(1+6)/64, two-sided = 14/64
    assert stats.mcnemar_exact(5, 1) == pytest.approx(14 / 64)
    # b=10, c=2: n=12, P(X<=2)=(1+12+66)/4096, two-sided = 158/4096
    assert stats.mcnemar_exact(10, 2) == pytest.approx(158 / 4096)
    assert stats.mcnemar_exact(4, 4) == 1.0
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(0, 8) == pytest.approx(2 / 256)


def test_wilson_known_interval() -> None:
    lo, hi = stats.wilson(8, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.490, 0.943)
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_holm() -> None:
    assert stats.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_quantile_and_median() -> None:
    assert stats.quantile([1, 2, 3, 4], 0.5) == 2.5
    assert stats.quantile([1, 2, 3, 4], 0.95) == pytest.approx(3.85)
    assert stats.median([5]) == 5


def test_clustered_se_hand_computed() -> None:
    # d = [1, 0, 0, 1], mean .5. Unclustered: sqrt(sum (d-mean)^2)/n = sqrt(4 * .25)/4 = .25
    pairs = [("a", 0, 1), ("b", 0, 0), ("c", 0, 0), ("d", 0, 1)]
    assert stats.clustered_se(pairs) == pytest.approx(0.25)
    # items 1 and 4 share a cluster: cluster deviation sums are 1.0, -0.5, -0.5 -> sqrt(1.5)/4
    pairs = [("a", 0, 1), ("b", 0, 0), ("c", 0, 0), ("a", 0, 1)]
    assert stats.clustered_se(pairs) == pytest.approx(math.sqrt(1.5) / 4)


def test_bootstrap_is_seeded_and_degenerate_cases() -> None:
    pairs = [(str(i), 1.0, 1.0) for i in range(30)]
    assert stats.bootstrap_mean_diff(pairs, n_boot=200, seed=1) == (0.0, 0.0)
    rng = random.Random(3)
    pairs = [(str(i), float(rng.random() < 0.6), float(rng.random() < 0.7)) for i in range(80)]
    a = stats.bootstrap_mean_diff(pairs, n_boot=500, seed=5)
    assert a == stats.bootstrap_mean_diff(pairs, n_boot=500, seed=5)
    assert a != stats.bootstrap_mean_diff(pairs, n_boot=500, seed=6)
    delta = sum(p[2] - p[1] for p in pairs) / len(pairs)
    assert a[0] < delta < a[1]


def test_generic_bootstrap_matches_fast_path_for_mean() -> None:
    rng = random.Random(1)
    pairs = [(str(i // 2), rng.random(), rng.random()) for i in range(60)]
    fast = stats.bootstrap_mean_diff(pairs, n_boot=400, seed=2)
    slow = stats.cluster_bootstrap(pairs, stats.mean_diff, n_boot=400, seed=2)
    assert fast == pytest.approx(slow)


def test_ratio_and_median_bootstrap() -> None:
    pairs = [(str(i), 2.0, 1.0) for i in range(40)]
    s = stats.paired_metric_summary(pairs, seed=1, n_boot=200)
    assert s["ratio"] == 0.5 and s["ratio_ci"] == [0.5, 0.5]
    assert s["median_diff"] == -1.0 and s["median_diff_ci"] == [-1.0, -1.0]


def test_non_inferiority_verdicts() -> None:
    ni = stats.non_inferiority
    assert ni(0.0, (-0.03, 0.03), 0.015, 100, 0.05, 20)["verdict"] == "non_inferior"
    assert ni(-0.15, (-0.2, -0.1), 0.025, 100, 0.05, 20)["verdict"] == "inferior"
    r = ni(-0.04, (-0.09, 0.01), 0.025, 100, 0.05, 20)
    assert r["verdict"] == "inconclusive" and r["extra_items_needed"] > 0
    assert ni(0.0, (0.0, 0.0), 0.0, 5, 0.05, 20)["verdict"] == "inconclusive"
    assert ni(0.1, (0.05, 0.15), 0.025, 100, 0.05, 20)["better"] is True


def test_pass_at_k_and_pass_hat_k() -> None:
    correct, ks = [3, 0, 1, 2], [3, 3, 3, 3]
    assert stats.pass_at_k(correct) == 0.75
    assert stats.pass_hat_k(correct, ks) == 0.25


def test_bootstrap_ci_coverage_is_about_95_percent() -> None:
    """Paired binary scores with a known true difference; the clustered bootstrap CI should cover it ~95%."""
    true_delta = 0.05  # candidate 0.75 vs baseline 0.70 (independent draws given the per-item difficulty)
    rng = random.Random(2024)
    covered = covered_clt = 0
    sims, n = 300, 80
    for s in range(sims):
        pairs = []
        for i in range(n):
            hard = rng.random()  # per-item difficulty shared by both models: induces correlation
            pb = 0.4 + 0.6 * hard * 0.9 if hard > 0 else 0.4
            pairs.append((f"c{i // 2}", float(rng.random() < pb), float(rng.random() < min(1.0, pb + true_delta))))
        lo, hi = stats.bootstrap_mean_diff(pairs, n_boot=400, seed=s)
        # true delta of this generator: E[min(1, pb + .05) - pb]; pb <= 0.94 so it is exactly .05
        covered += lo <= true_delta <= hi
        se = stats.clustered_se(pairs)
        d = sum(p[2] - p[1] for p in pairs) / n
        covered_clt += d - stats.Z95 * se <= true_delta <= d + stats.Z95 * se
    assert 0.91 <= covered / sims <= 0.99, covered / sims
    assert 0.91 <= covered_clt / sims <= 0.99, covered_clt / sims
