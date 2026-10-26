# 14 · Deploy: MoE sizing calculator + reference deployment

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/14-deploy

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

- [ ] Calculator reproduces 44.9 KB/token ± 1% and the research's 755.7 GB FP8 weights figure (unit tests).
- [ ] Infeasible configs (e.g. 1M context on H200 TP8) are reported infeasible with the reason.
- [ ] Every GPU price and calibration point carries a source URL + date; a test enforces it.
- [ ] Reference compose/Helm files lint (`docker compose config`, `helm lint` if available; otherwise schema check)
      and pin versions.
- [ ] Deploy tab calculator works offline from disk; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
