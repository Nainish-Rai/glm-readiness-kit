# 10 · Migration eval engine (switch-eval)

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/10-eval-engine

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

- [x] Against mock targets with known injected quality, the engine recovers the planted differences: a "worse"
      target is flagged inferior and an equal one non-inferior, at the configured seed (evidence included).
- [x] Statistics module has unit tests against hand-computed examples (McNemar, bootstrap CI coverage on simulated
      data ≈ 95%).
- [x] Judge position-swap disagreement is reported, and items where swapped verdicts disagree are flagged.
- [x] Run interruption and resume produce the same result as an uninterrupted run.
- [x] Migration tab renders the mock run; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

Built and verified on branch `issue/10-eval-engine`. Contract and usage: `docs/contracts.md` section 9 "Eval".

**Note for the merge (issue 12).** Cost sits behind `glmkit.eval.pricing.PriceTable.cost_usd(model, provider, usage)`
reading `src/glmkit/eval/prices.yaml` (source URL + as_of per entry; GLM list prices from research/03-eval.md, units
not re-checked live). Reconcile with issue 12's table at merge by implementing the same two members (`version`,
`cost_usd`).

### 1. Planted quality is recovered (fixed seed 7)

Mock targets (`glmkit eval demo`): incumbent 3% planted error rate, `demo-glm-equal` 3%, `demo-glm-worse` 25%, 340 items,
5 task types, margin 5 points.

```text
$ glmkit eval demo --out $W/demo
$ glmkit eval run --set $W/demo/demo-set.yaml --baseline demo-incumbent --targets demo-glm-equal,demo-glm-worse \
    --judge demo-judge --prices $W/demo/prices.yaml --profiles-dir $W/demo/profiles --seed 7 --out $W/results
run 20261026T155730Z-eval-c99a23  set=mock-demo (340 items)  baseline=demo-incumbent  margin=0.05
task type      target                        n               delta [95% CI]  verdict        cost x  recommendation
arithmetic     demo-glm-equal               80 -0.038      [-0.087, +0.013]  inconclusive     0.04  inconclusive: need about 1423 more items
classify       demo-glm-equal              120 -0.008      [-0.050, +0.033]  inconclusive     0.04  inconclusive: need about 9 more items
extract        demo-glm-equal               60 +0.000      [-0.083, +0.083]  inconclusive     0.04  inconclusive: need about 94 more items
summarize      demo-glm-equal               40 +0.037      [+0.000, +0.075]  non_inferior     0.04  switch: quality within the margin at 0.04x the baseline cost
text_to_sql    demo-glm-equal               40 +0.100      [-0.025, +0.250]  non_inferior     0.04  switch: quality within the margin at 0.04x the baseline cost
ALL            demo-glm-equal              340 +0.004      [-0.025, +0.035]  non_inferior     0.04  switch: quality within the margin at 0.04x the baseline cost
arithmetic     demo-glm-worse               80 -0.113      [-0.188, -0.037]  inconclusive     0.09  inconclusive: need about 43 more items
classify       demo-glm-worse              120 -0.233      [-0.317, -0.150]  inferior         0.09  do not switch on this task type; consider routing it to the baseline
extract        demo-glm-worse               60 -0.200      [-0.317, -0.083]  inferior         0.09  do not switch on this task type; consider routing it to the baseline
summarize      demo-glm-worse               40 -0.075      [-0.163, +0.000]  inconclusive     0.09  inconclusive: need about 352 more items
text_to_sql    demo-glm-worse               40 +0.000      [-0.175, +0.175]  inconclusive     0.09  inconclusive: need about 421 more items
ALL            demo-glm-worse              340 -0.153      [-0.200, -0.106]  inferior         0.09  do not switch on this task type; consider routing it to the baseline
warning: task-type intervals are unadjusted; McNemar p-values carry Holm-adjusted p_holm across task types
spent $0.4410  (calls: 1020 new, 0 resumed)
verdict: fail
results: $W/results/20261026T155730Z-eval-c99a23/result.json
```

Overall: worse = inferior (CI [-0.200, -0.106] entirely below -0.05), equal = non-inferior (CI [-0.025, +0.035] above -0.05).
Task types with too few items (arithmetic, classify, extract for the equal target) are honestly `inconclusive`, with the
number of extra items needed. Regression test: `tests/test_eval_engine.py::test_planted_quality_is_recovered_at_fixed_seed`.

### 2. Statistics unit tests

`uv run pytest tests/test_eval_stats.py`: 11 passed. Hand-computed McNemar (b=5,c=1 -> 14/64; b=10,c=2 -> 158/4096;
b=c -> 1; 0/8 -> 2/256), Wilson 8/10 = [0.490, 0.943], Holm, clustered SE by hand (0.25 and sqrt(1.5)/4), pass@K / pass^K,
generic vs fast bootstrap agree, ratio/median bootstrap. CI coverage on simulated paired data (true delta 0.05, n=80,
correlated difficulty, clusters of 2, 300 simulations; the test asserts 0.91-0.99):

```text
coverage over 300 sims: bootstrap 0.933, clustered CLT 0.920
```

### 3. Judge position swap

Planted judge with a 15% first-slot bias (`demo-judge`). Both orders are asked; disagreeing orders score 0.5 and are
flagged `position_inconsistent` on the item (drawer + Items table flags). From the run above, `data.judge.per_target`:
`demo-glm-equal`: judged 40, position_inconsistent 4 (10.0%), position_bias_first 4, second 0, candidate_wins 3,
baseline_wins 0; `demo-glm-worse`: judged 40, position_inconsistent 4, candidate_wins 3, baseline_wins 9 (the worse
target loses to the incumbent, as planted). Tests: `test_eval_graders.py` (consistent win, always-A judge ->
inconsistent + bias flag, longer-wins flag, cache), `test_judge_audit_reports_swap_disagreement`,
`test_judge_family_overlap_warning` (judge from the candidates' family warns).

### 4. Interrupt and resume

`test_resume_equals_uninterrupted`: run stopped after 70 new calls (hook `interrupt_after`), re-run without `--resume`
refused ("already exists"), `--resume` continues, and quality CIs, McNemar, reliability, cost stats and every item score
equal an uninterrupted run (K=2). `test_resume_refuses_a_different_set` covers the mismatch guard. Other engine tests:
throttling retried and not a failure (`test_throttling_is_retried_and_never_a_failure`), exhausted retries are
`throttled` not failed, budget cap -> `incomplete`, unpriced model -> cost `not logged`, SwitchTrace JSONL input,
CLI, no key in any written file.

### 5. Dashboard

Design system read (`vendor/observatory/skill/references/style.md`, README). Token check seen to fail once on a planted
raw colour (`.rec { color: #ff0000; }` appended to `migration.css`), then clean after removal:

```text
$ glmkit report build $W/results -o $W/site-bad     # with the planted colour
$W/site-bad/index.html:109 raw-color #ff0000
token check: 1 finding(s)        (exit 1)
$ glmkit report build $W/results -o $W/site         # planted colour removed
token check: 0 finding(s)        (exit 0)
```

Headless Chrome, file://, 1440 and 390 px, every tab, drawer opened and closed:

```text
$ NODE_PATH=... CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tools/site-smoke/smoke.mjs $W/site/index.html --out $W/shots
width 1440: errors []    width 390: errors []    "ok": true     (exit 0)
```

Screenshots (`$W/shots/migration-1440.png`, `migration-390.png`, `migration-drawer-1440.png`) viewed: KPI strip, verdict table
with CI bars and margin line, cost-vs-quality scatter, targets table, items, judge audit, run details, and the drawer
with outputs of all targets side by side. Two defects found and fixed along the way: the item table rendered all rows
(now capped at 60 until "Show all"), and the segmented control overflowed 50 px at 390 px (now wraps).

### 6. Gates

`uv run pytest -q`: 134 passed. `uv run ruff check`: All checks passed.

### Not verified

Live GLM hosts (mock only; the judge-bias and verbosity numbers are planted, not measured). The effort sweep
(`@low/@max`) shows cost differences from mock reasoning length only. Judge calls are not metered against `--budget-usd`.
