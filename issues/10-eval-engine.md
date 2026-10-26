# 10 · Migration eval engine (switch-eval)

Status: blocked · Type: AFK · Label: ready-for-agent · Branch: issue/10-eval-engine

## What to build

The core FDE tool. Given a golden set or replayable traces (SwitchTrace v0), replay each item on several targets
(the incumbent model, GLM-5.3 at low/high/max, Flash, FlashX, different hosts), grade them, and report per task type
whether GLM is non-inferior, at what cost and latency, with paired statistics. The research found no tool that does
paired, per-task-type migration comparison with CIs across variants and hosts (`research/03-eval.md` §1, §3).

- `glmkit eval run --set <file> --targets <profiles…> --baseline <profile>`. Runs are concurrent, rate-limit aware,
  resumable and budget-capped. Throttled calls are retried and reported separately, never counted as failures.
- **Graders**, configured per item or task type:
  - exact / regex / numeric tolerance;
  - JSON Schema;
  - SQL result equality (pluggable executor);
  - LLM judge with position swap, a judge from a different model family than the candidates, length control,
    and agreement reporting.
- **Statistics** per task type and overall:
  - paired difference with clustered bootstrap CI;
  - McNemar for pass/fail;
  - paired bootstrap for cost and latency;
  - a **non-inferiority verdict** against a configurable margin;
  - K-resampling for stochastic items.
  Follow `research/03-eval.md` §3.
- Cost is computed from a versioned price table (input, cached-input, output; per target), never trusted from the
  source alone.
- Dashboard: a **Migration** tab with a KPI strip (quality delta, cost delta, p50 latency delta, verdict), a
  per-task-type table with CI bars and verdict pills, a cost-vs-quality view per target and effort level, and
  item-level drill-down with side-by-side outputs in the drawer.

## Acceptance criteria

- [ ] Against mock targets with known injected quality, the engine recovers the planted differences: a "worse"
      target is flagged inferior and an equal one non-inferior, at the configured seed (evidence included).
- [ ] Statistics module has unit tests against hand-computed examples (McNemar, bootstrap CI coverage on simulated
      data ≈ 95%).
- [ ] Judge position-swap disagreement is reported, and items where swapped verdicts disagree are flagged.
- [ ] Run interruption and resume produce the same result as an uninterrupted run.
- [ ] Migration tab renders the mock run; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
