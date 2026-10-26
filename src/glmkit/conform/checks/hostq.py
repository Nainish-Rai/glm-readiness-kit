"""Host-quality checks (latency LAT-*, fingerprint FP-*). All are aggregate: they read every attempt of an item,
run a statistical gate with an explicit threshold and fixed seed, and return the full statistic in ``observed``
so the dashboard and the host-quality verdict can show it. Gates that need a reference return ``skip`` (with the
host-side numbers still in ``observed``) when none is loaded.

These checks only run in the host-quality cases, which are ``opt_in``: they never reach the correctness verdict.
"""

from __future__ import annotations

from typing import Any

from glmkit.conform.checks import CheckContext, CheckOutcome, check, fail, ok
from glmkit.hostq import analysis, probes, stats
from glmkit.hostq.reference import DEFAULT_SEED, current_session

Obs = dict[str, Any]


def _attempts(ctx: CheckContext) -> list[list[Any]]:
    return [a.exchanges for a in ctx.attempts]


def _public(cond: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in cond.items()}


def _skip(message: str, observed: Obs | None = None) -> CheckOutcome:
    return CheckOutcome("skip", message, observed=observed)


def _fmt_s(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 1000:.0f} ms"


# ------------------------------------------------------------------ latency


@check("latency_tool_penalty", aggregate=True)
def latency_tool_penalty(ctx: CheckContext) -> CheckOutcome:
    """TTFT with tools minus TTFT without: flagged when the median penalty exceeds max_delta_s and is significant."""
    p = ctx.params
    max_delta, alpha, seed = (
        float(p.get("max_delta_s", 1.0)),
        float(p.get("alpha", 0.05)),
        int(p.get("seed", DEFAULT_SEED)),
    )
    cond = analysis.latency_by_label(_attempts(ctx), "lat.")
    base = cond.get("no_tools")
    obs: Obs = {"conditions": _public(cond), "max_delta_s": max_delta, "alpha": alpha, "seed": seed, "tests": {}}
    if not base or len(base["_ttft_values"]) < 2:
        return _skip("fewer than 2 usable no_tools samples (throttled or failed)", obs)
    flagged = []
    for name, row in cond.items():
        if name == "no_tools" or len(row["_ttft_values"]) < 2:
            continue
        t = stats.median_shift_test(base["_ttft_values"], row["_ttft_values"], seed=seed)
        obs["tests"][name] = t
        if t["delta"] is not None and t["delta"] > max_delta and t["p_value"] < alpha:
            flagged.append(f"{name} +{_fmt_s(t['delta'])} (p={t['p_value']:.3f})")
    if not obs["tests"]:
        return _skip("no tools condition had usable samples", obs)
    if flagged:
        return fail(
            f"TTFT penalty with tools above {max_delta:g} s: " + "; ".join(flagged),
            expected=f"median delta <= {max_delta:g} s",
            observed=obs,
        )
    worst = max((t["delta"] for t in obs["tests"].values() if t["delta"] is not None), default=None)
    return ok(f"largest median TTFT penalty with tools {_fmt_s(worst)} (limit {max_delta:g} s)", observed=obs)


@check("latency_recorded", aggregate=True)
def latency_recorded(ctx: CheckContext) -> CheckOutcome:
    """Record TTFT and tokens/s per effort level (not scored); fails only when no request produced a measurement."""
    cond = analysis.latency_by_label(_attempts(ctx), "eff.")
    obs: Obs = {"efforts": _public(cond)}
    usable = {k: v for k, v in cond.items() if v["ttft_s"]["n"]}
    if not usable:
        errors = sum(v["errors"] for v in cond.values())
        thr = sum(v["throttled"] for v in cond.values())
        if errors:
            return fail(f"no usable latency sample ({errors} failed, {thr} throttled)", observed=obs)
        return _skip(f"every request was throttled ({thr})", obs)
    bits = [f"{k}: ttft {_fmt_s(v['ttft_s']['p50'])}" for k, v in usable.items()]
    return ok("recorded " + ", ".join(bits), observed=obs)


# ------------------------------------------------------------------ fingerprint


@check("model_field_honest", aggregate=True)
def model_field_honest(ctx: CheckContext) -> CheckOutcome:
    """The response model equals the requested model, and an unknown model is refused rather than answered."""
    res = analysis.model_identity(_attempts(ctx))
    if not res["probes"]:
        return _skip("no identity probe produced a response", res)
    if res["problems"]:
        return fail(
            "; ".join(res["problems"]), expected="response model == request model; unknown model 4xx", observed=res
        )
    return ok("response model matches the request; unknown model refused", observed=res)


def _mmd_gate(
    ctx: CheckContext, key: str, samples: list[list[str]], ref_samples: list[list[str]] | None, kernel: Any, what: str
) -> CheckOutcome:
    p = ctx.params
    alpha, n_perm = float(p.get("alpha", 0.01)), int(p.get("permutations", 1999))
    seed, min_n = int(p.get("seed", DEFAULT_SEED)), int(p.get("min_samples", 30))
    session = current_session()
    session.collected[key] = {"samples": samples}
    obs: Obs = {
        "n": len(samples),
        "prompts": len({s[0] for s in samples}),
        "hist": analysis.histogram(samples) if key == "action" else None,
        "alpha": alpha,
        "permutations": n_perm,
        "seed": seed,
    }
    if session.reference is None or ref_samples is None:
        return _skip(f"no reference loaded: {len(samples)} {what} samples recorded, nothing to compare", obs)
    if len(samples) < min_n or len(ref_samples) < min_n:
        return _skip(f"too few samples (host {len(samples)}, reference {len(ref_samples)}, need {min_n})", obs)
    res = stats.mmd_permutation_test(
        [(a, b) for a, b in samples], [(a, b) for a, b in ref_samples], kernel, n_perm=n_perm, seed=seed
    )
    obs |= res.to_dict() | {
        "reference_id": session.reference.reference_id,
        "ref_n": len(ref_samples),
        "ref_hist": analysis.histogram(ref_samples) if key == "action" else None,
    }
    msg = (
        f"MMD^2={res.mmd2:.4f}, permutation p={res.p_value:.4f} ({n_perm} perms, seed {seed}, n={res.n_x} vs {res.n_y})"
    )
    if res.p_value < alpha:
        return fail(f"{what} distribution differs from the reference: {msg}", expected=f"p >= {alpha:g}", observed=obs)
    return ok(f"{what} distribution consistent with the reference: {msg}", observed=obs)


@check("action_distribution_vs_reference", aggregate=True)
def action_distribution_vs_reference(ctx: CheckContext) -> CheckOutcome:
    """Tool-choice histogram over the probe set vs the stored reference (MMD permutation test, AgentProv style)."""
    ref = current_session().reference
    return _mmd_gate(
        ctx,
        "action",
        analysis.action_samples(_attempts(ctx)),
        ref.action.get("samples") if ref else None,
        stats.categorical_kernel,
        "action",
    )


@check("text_distribution_vs_reference", aggregate=True)
def text_distribution_vs_reference(ctx: CheckContext) -> CheckOutcome:
    """Short random-string completions vs the stored reference (Hamming-kernel MMD permutation test, MET style)."""
    ref = current_session().reference
    return _mmd_gate(
        ctx,
        "text",
        analysis.text_samples(_attempts(ctx)),
        ref.text.get("samples") if ref else None,
        stats.hamming_kernel(int(ctx.params.get("length", 64))),
        "text",
    )


@check("greedy_self_agreement", aggregate=True)
def greedy_self_agreement(ctx: CheckContext) -> CheckOutcome:
    """Exact-match rate of identical temperature-0 requests; flagged when below the reference rate minus a margin."""
    margin = float(ctx.params.get("margin", 0.1))
    res = analysis.greedy_agreement(_attempts(ctx))
    session = current_session()
    session.collected["self_agreement"] = res
    res = {**res, "margin": margin}
    if not res["pairs"]:
        return _skip("no usable repeated greedy samples", res)
    ref = (session.reference.self_agreement if session.reference else None) or {}
    if ref.get("rate") is None:
        return _skip(f"no reference: noise floor {res['rate']:.3f} over {res['pairs']} pairs", res)
    res["ref_rate"] = ref["rate"]
    msg = f"self-agreement {res['rate']:.3f} vs reference {ref['rate']:.3f} (margin {margin:g})"
    if res["rate"] < ref["rate"] - margin:
        return fail(f"greedy self-agreement dropped: {msg}", expected=f">= {ref['rate'] - margin:.3f}", observed=res)
    return ok(msg, observed=res)


@check("capability_canaries", aggregate=True)
def capability_canaries(ctx: CheckContext) -> CheckOutcome:
    """Canary accuracy as percent-of-reference with a 95% CI; flagged when the CI lies entirely below 100%."""
    ps = probes.load_set("canaries")
    res = analysis.canary_results(_attempts(ctx), ps)
    session = current_session()
    session.collected["canary"] = res
    if not res["n"]:
        return _skip("no usable canary response", res)
    ref = session.reference.canary if session.reference else None
    if not ref or not ref.get("n"):
        return _skip(f"no reference: {res['correct']}/{res['n']} canaries correct (absolute only)", res)
    r = stats.ratio_ci(res["correct"], res["n"], ref["correct"], ref["n"])
    res = {**res, "ref": {"n": ref["n"], "correct": ref["correct"]}, "percent_of_reference": r}
    if r["ratio"] is None:
        return _skip("reference scored 0 canaries; ratio undefined", res)
    msg = f"{r['ratio']:.0%} of reference (95% CI {r['lo']:.0%} to {r['hi']:.0%}), {res['correct']}/{res['n']} correct"
    if r["hi"] < 1.0:
        return fail(f"canary score below reference: {msg}", expected="95% CI reaches 100%", observed=res)
    return ok(msg, observed=res)
