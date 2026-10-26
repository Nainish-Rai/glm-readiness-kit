# 14 · Deploy: MoE sizing calculator + reference deployment

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/14-deploy

## What to build

Answer "what does it take to run GLM ourselves, and is it cheaper than the API?" with a calculator built for MoE
models, plus a reference deployment an FDE can hand to a sovereign partner. Sources and formulas are in
`research/04-deploy-compliance.md` §1–§2 and §7.

- `glmkit deploy size --model glm-5.3 --precision fp8 --gpu h200 --context 128k --concurrency 64`. It computes, for
  a given hardware choice:
  - weights memory;
  - MLA KV cache per token, accounting for tensor-parallel replication;
  - max tokens in KV;
  - the MoE bandwidth term (experts touched per batch);
  - feasibility.
  It estimates throughput, using the published llm-d/SGLang measurements as calibration points labelled as such,
  and computes cost per million tokens against a GPU price table with sources and dates. It compares against API
  prices and outputs a break-even utilisation.
- Validate the KV formula against llm-d's published 44.9 KB/token figure, which the research reproduced.
- **Reference deployment** files:
  - single-node vLLM (pinned version, FP8, MTP, tool/reasoning parsers **pinned per engine**: vLLM `glm47`,
    SGLang/llm-d `glm45`), as docker compose and a Helm values file;
  - an llm-d cluster profile;
  - a health/readiness check that runs slice 01's WIRE cases against the deployment.
  These are config and docs, not live-applied.
- Dashboard: a **Deploy** tab with an interactive calculator (inputs → memory composition bar, feasibility pills,
  cost vs API comparison, break-even point). Estimates use the heuristic style.

## Acceptance criteria

- [x] Calculator reproduces 44.9 KB/token ± 1% and the research's 755.7 GB FP8 weights figure (unit tests).
- [x] Infeasible configs (e.g. 1M context on H200 TP8) are reported infeasible with the reason.
- [x] Every GPU price and calibration point carries a source URL + date; a test enforces it.
- [x] Reference compose/Helm files lint (`docker compose config`, `helm lint` if available; otherwise schema check)
      and pin versions.
- [x] Deploy tab calculator works offline from disk; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

All commands run in the worktree with `uv run`. `docker compose config` ran (docker 29.2.1); **`helm` is not installed**,
so the Helm values are checked against `deploy/schemas/vllm-stack-values.schema.json` (jsonschema) and were NOT linted
against the real chart. Reference files were never applied anywhere.

### 44.9 KB/token and 755.7 GB: pass, then fail when a config value is perturbed
Both figures are recomputed from a snapshot of the model's `config.json` and the HF repo file listing
(`src/glmkit/deploy/data/`), fetched 2026-10-26.
```
$ glmkit deploy validate
PASS  kv_bytes_per_token_fp8: computed 44,928.0 vs reference 44,900.0 (error 0.062%, tolerance 1.0%)  [78 layers x (512 + 64) x 1 B]
PASS  fp8_checkpoint_gb: computed 755.7 vs reference 755.7 (error 0.005%, tolerance 0.1%)  [sum of 156 file sizes in the repo listing]
exit=0
$ glmkit deploy validate --set kv_lora_rank=1024   (perturb KV)
FAIL  kv_bytes_per_token_fp8: computed 84,864.0 vs reference 44,900.0 (error 89.007%, tolerance 1.0%)  [78 layers x (1024 + 64) x 1 B]
PASS  fp8_checkpoint_gb: computed 755.7 vs reference 755.7 (error 0.005%, tolerance 0.1%)  [sum of 156 file sizes in the repo listing]
exit=1
dropped ['model-00070-of-00141.safetensors', 5366353992]
$ glmkit deploy validate --manifest manifest-missing-shard.json   (perturb weights)
PASS  kv_bytes_per_token_fp8: computed 44,928.0 vs reference 44,900.0 (error 0.062%, tolerance 1.0%)  [78 layers x (512 + 64) x 1 B]
FAIL  fp8_checkpoint_gb: computed 750.3 vs reference 755.7 (error 0.715%, tolerance 0.1%)  [sum of 155 file sizes in the repo listing]
exit=1
```
Also reproduced in tests: llm-d 598K KV tokens at TP8 on H200 (within 1%), the research cost examples ($4.93, $2.47,
$7.67 per M), experts-touched table (8/31/102/163/222/252), and the fitted eta 0.123 / routing skew 0.48 re-derived from
the two SGLang points.

### Infeasible config reported with reason
```
$ glmkit deploy size --context 1m --no-write
  cost           $3.56/M self-hosted at 50% utilisation vs $1.73/M API (zai)
  break-even     never at this config (needs 103% utilisation)
  feasibility    INFEASIBLE
    - KV cache holds 597,446 tokens but context 1,048,576 needs one full sequence; the MLA cache is replicated on every tensor-parallel rank, so adding GPUs adds no KV capacity (use more HBM per GPU, FP8 KV, dcp, or a shorter context)
exit=0
$ glmkit deploy size --gpu b200 --context 1m --concurrency 8 --no-write
  cost           $7.81/M self-hosted at 50% utilisation vs $1.73/M API (zai)
  break-even     never at this config (needs 225% utilisation)
  feasibility    FEASIBLE
```
(exit code 1 for the infeasible config; 8 x B200 is feasible at 1M, matching the vLLM recipe.)

### Health check (WIRE cases) pass / planted defect fail
```
$ glmkit deploy health --target mock
  PASS      WIRE-001[nonstream]
  PASS      WIRE-002[nonstream]
  PASS      WIRE-002[stream]
verdict: pass
results: /private/tmp/claude-501/-Users-nainish-development/80660d68-c334-46f4-899d-54a7cc71bc3e/scratchpad/dp/h/20261026T154754Z-conform-a8f787/result.json
$ ... --mock-defect forced-thinking-500
  FAIL      WIRE-001[nonstream]  no_5xx: HTTP 500
  PASS      WIRE-002[nonstream]
  PASS      WIRE-002[stream]
verdict: fail
results: /private/tmp/claude-501/-Users-nainish-development/80660d68-c334-46f4-899d-54a7cc71bc3e/scratchpad/dp/h/20261026T154755Z-conform-6e4c6a/result.json
```
Unreachable endpoint: `glmkit deploy health --target vllm-local` prints `endpoint: NOT READY` and exits 1 (test).

### Python / JS parity
`tests/test_deploy_sizing.py::test_js_port_matches_python_on_the_same_inputs` runs `node` (v24) on 10 input sets
(infeasible, BF16, NVFP4, DCP, MTP, prefix reuse, every API) and asserts every numeric/boolean output and the reason
strings match (rel 1e-9).

### Dashboard
- `glmkit report build <results> -o <site>`: `token check: 0 finding(s)`.
- Token check fails once on a planted raw colour: `tests/test_deploy_page.py::test_token_check_fails_on_a_planted_raw_colour_in_the_deploy_css`
  (injects `.dp-note { color: #ff0000; }` into the built site; asserts a `raw-color` finding).
- `NODE_PATH=~/node_modules CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tools/site-smoke/smoke.mjs site/index.html --out shots/`
  -> `"ok": true`, `errors: []` at 1440 and 390 px, every tab, drawers, no horizontal scroll. First run caught
  `keyOf is not a function` (my `rowKey` was a string); fixed and re-run. Screenshots viewed: calculator, memory bar,
  feasibility pills, cost-vs-API bars with break-even line, sources table all render; estimate cards use the dashed
  `heuristic` style.

### Gates
`uv run pytest -q` all green (1 skip: helm not installed); `uv run ruff check` clean.

### Notes
- Throughput/cost are heuristic: a roofline fitted on two published SGLang GLM-5 points (not 5.3, synthetic load).
  GPU HBM bandwidth / FP8 FLOPs come from NVIDIA specs from memory (research section 8 item 9), flagged `from memory`
  in the tab. Z.ai API price is a secondary aggregator.
- Edited shared file `tests/test_extension_points.py` (one assertion: tab list now tolerant of extra tabs).
