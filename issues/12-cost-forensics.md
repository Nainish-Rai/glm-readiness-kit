# 12 · Cost and cache forensics

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/12-cost-forensics

## What to build

`glmkit cost analyze traces.jsonl` explains what a workload costs on GLM and why. Users report cache-hit rates of
67% vs 95% across hosts, 43k output tokens per task at max effort, tokenizer inflation, and opaque Coding Plan
points. No tool explains those differences (`zai-research/sources/applied.md` §2.5; `research/03-eval.md` §1).

- A **versioned price table** (YAML) for Z.ai list prices, the Coding Plan points model (peak/off-peak), and
  third-party hosts. Each price carries a source URL and date; missing prices show as `not logged`.
- Per-trace and per-turn **cache forensics**:
  - cache-read share;
  - first turn where caching broke;
  - likely cause: system-prompt churn, tool-order change, reasoning not replayed verbatim, timestamp in the prefix.
  Causes are heuristic and labelled as such.
- **Verbosity forensics**: output and reasoning tokens per task, by effort level and model, and projected savings
  from a lower effort level.
- **Tokenizer inflation**: the ratio of GLM-counted to incumbent-counted input tokens by content type (JSON, code,
  CJK, prose), when both counts are available.
- **Cost projection** for the same workload on Z.ai pay-as-you-go, the Coding Plan tiers, and each priced host, with
  the assumptions spelled out.
- Dashboard: a **Cost** tab with a treemap of where tokens went, per-turn cache-hit bars with break markers, a
  projection table, and heuristic styling on estimates.

## Acceptance criteria

- [ ] Planted cache-break scenarios in synthetic traces (prefix churn, reordered tools, edited reasoning) are each
      attributed to the right cause (evidence included).
- [ ] Price table entries carry source URL + date; a test fails on an entry without them.
- [ ] Projection math has unit tests against hand-computed examples, including cached-input pricing.
- [ ] Cost tab renders; estimates use the heuristic style; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
