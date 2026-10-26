# 12 · Cost and cache forensics

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/12-cost-forensics

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

- [x] Planted cache-break scenarios in synthetic traces (prefix churn, reordered tools, edited reasoning) are each
      attributed to the right cause (evidence included).
- [x] Price table entries carry source URL + date; a test fails on an entry without them.
- [x] Projection math has unit tests against hand-computed examples, including cached-input pricing.
- [x] Cost tab renders; estimates use the heuristic style; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

Built: `src/glmkit/cost/` (`prices.yaml` + `prices.py` price table API, `forensics.py`, `projection.py`, `analyze.py`,
`synth.py` planted-scenario generator, `cli.py`), Cost tab `src/glmkit/report/pages/cost.{py,js,css}`,
`tests/test_cost.py` (28 tests), docs/contracts.md section 9 "Price table". Commands: `glmkit cost analyze|demo|prices`.

**1. Planted cache breaks attributed (synthetic traces, cache usage from a prefix-cache simulator independent of the detectors)**

```
$ uv run glmkit cost demo -o demo && uv run glmkit cost analyze demo/traces.jsonl --counts demo/counts.jsonl -o res
14 traces, 98 calls · cache share 68.0% · 13 break(s) in 8 trace(s) · lost 67,529 cached tokens (~$0.0770 ...)
  synthetic-timestamp-in-prefix    first break at call 1: timestamp_in_prefix
  synthetic-system-prompt-churn    first break at call 3: system_prompt_churn
  synthetic-tool-reorder           first break at call 3: tool_order_change
  synthetic-reasoning-edited       first break at call 3: reasoning_not_replayed
  synthetic-reasoning-dropped      first break at call 3: reasoning_not_replayed
  synthetic-history-compaction     first break at call 3: history_rewritten
  synthetic-idle-gap               first break at call 3: idle_gap
  synthetic-host-eviction          first break at call 3: unattributed   (nothing in the prompt changed)
```
Healthy trace: 0 breaks. Causes are labelled heuristic everywhere (CLI, result, tab).

**2. Attribution test fails with its detector disabled** (`GLMKIT_COST_DISABLE` or `--disable`)
```
$ uv run pytest tests/test_cost.py -k "planted_break and tool-reorder"            -> 1 passed
$ GLMKIT_COST_DISABLE=tool_order uv run pytest tests/test_cost.py -k "planted_break and tool-reorder"
E   AssertionError: assert 'unattributed' == 'tool_order_change'     -> 1 failed
$ for d in timestamp_in_prefix system_churn reasoning_replay history_rewritten idle_gap tool_order:
    GLMKIT_COST_DISABLE=$d uv run pytest tests/test_cost.py -k planted_break
timestamp_in_prefix: 1 failed   system_churn: 1 failed   reasoning_replay: 2 failed
history_rewritten: 1 failed     idle_gap: 1 failed       tool_order: 1 failed
$ uv run glmkit cost analyze demo/traces.jsonl --disable tool_order
  synthetic-tool-reorder           first break at call 3: unattributed
```

**3. Price table / projection tests**: `test_price_entry_without_source_or_date_fails`,
`test_price_entry_with_non_url_source_fails` (entries need source URL + date); projection tests against hand-computed
numbers incl. cached input (1M in / 600k cached / 100k out at 1.4/0.26/4.4 = $1.156; 2.54 and 1.97 cache-fixed case;
Coding Plan break-even 18/1.27, off-peak 0.5x, heuristic 3x). Bedrock and OpenRouter cached prices read `not logged`.

**4. Token check, seen failing then passing**
```
$ echo ".x { color: #ff0000; }" >> src/glmkit/report/pages/cost.css; uv run glmkit report build res -o site2
.../site2/index.html:98 raw-color #ff0000
token check: 1 finding(s)        (exit 1)
(removed) $ uv run glmkit report build res -o site  ->  tabs=runs,conformance,cost  token check: 0 finding(s)
```

**5. Browser** (`NODE_PATH=~/node_modules CHROME_PATH=".../Google Chrome" node tools/site-smoke/smoke.mjs site/index.html --out shots`):
`"ok": true`, `errors: []`, `overflowX: 0` at 1440 and 390 px, all tabs incl. Cost, drawer open/close. Screenshots
(`cost-1440.png`, `cost-390.png`) viewed: treemap, composition (dashed heuristic card), per-turn bars with red break
markers, causes, verbosity, tokenizer, projection with `not logged` and heuristic pills, Coding Plan, assumptions.

**6.** `uv run pytest -q`: all green (full suite); `uv run ruff check`: All checks passed.

Limits: tokenizer-inflation needs a separate `--counts` file (SwitchTrace v0 has no incumbent-count field; no contract
change made); Coding Plan credits per token are unpublished, so tasks-per-window needs `--credits-per-usd` (heuristic).
No live GLM data used; demo numbers are synthetic and labelled so in the tab.

