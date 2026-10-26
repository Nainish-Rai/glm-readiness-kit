# 11 · Trace importers

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/11-trace-importers

## What to build

`glmkit traces import --from <kind> <path> -o traces.jsonl` converts a customer's existing production data into
SwitchTrace v0, so it can be replayed by the eval engine and analysed by cost forensics. Field-level details and
known traps are in `research/03-eval.md` §2.

- Adapters:
  - **OTel GenAI** spans (pin the semantic-conventions commit; content is opt-in, so spans without content become
    baseline-only rows);
  - **Langfuse** export (JSONL/CSV);
  - **OpenAI** chat completion logs;
  - **Anthropic** Messages logs (convert `input_tokens`, which *excludes* cache, to the total-including-cache
    convention);
  - **LiteLLM** spend logs (cost truth; often no prompts);
  - **Claude Code** session JSONL (one API response spans several lines: dedupe by `message.id`, mark
    `isSidechain` as subagent traffic, fail soft on unknown line types).
- Optional redaction pass (`--redact`): emails, phone numbers, API-key-like strings, and custom regexes.
- Task-type assignment: from source tags/metadata, a mapping file, or `unlabelled`.
- `glmkit traces stats` prints counts by source, task type, replayable vs baseline-only, and token totals.
- Dashboard: an **Traces** tab showing imported sets, replayable share (composition bar), task-type mix (bar list)
  and token totals by source.

## Acceptance criteria

- [x] Each adapter has fixture files (synthetic, no real customer data) and golden output tests.
- [x] Anthropic and Claude Code usage normalisation is proven by a test where the naive sum over-counts and the
      adapter does not.
- [x] Redaction removes planted secrets and PII in a fixture; a test fails if any survive.
- [x] Output validates against the SwitchTrace v0 schema from slice 01.
- [x] Traces tab renders; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

All commands run in the worktree; `$S` is a scratch folder. Fixtures are synthetic (`tests/fixtures/traces/`); the
Claude Code fixture mimics the structure of a real session file (line types, `message.id`, `apiBlockIndex`,
`usage` shape) with invented content.

**Built.** `src/glmkit/traces/` (registry, 6 adapters, redaction, stats, CLI `glmkit traces import|stats|sources`),
Traces tab (`report/pages/traces.{py,js}`), `tests/test_traces.py` (golden + schema + proofs), docs section 9 in
`docs/contracts.md`. Contract addition: optional `labels.tags` (schemas re-exported). OTel semconv pinned to
open-telemetry/semantic-conventions-genai `20259355b9631e3f032dcc099d1b5a2666818c42` (fetched with `gh api`).

**Tests and lint.**
```
$ uv run pytest -q          -> all passed (whole suite incl. 30+ new trace tests)
$ uv run ruff check         -> All checks passed!
```

**Usage normalisation: naive sum over-counts, adapter does not** (`test_anthropic_usage_naive_sum_overcounts_adapter_does_not`,
`test_claude_code_usage_naive_per_line_sum_overcounts_adapter_does_not`):
```
anthropic:   naive per-row total 2280 | naive input_tokens only 80 | adapter 1750
claude_code: naive per-line output 292, input-total 10150 | adapter output 132, input-total 5130
```
Anthropic: the fixture repeats response `msg_01B` (log duplicate) so a per-row sum over-counts (2280 vs 1750); using
`input_tokens` as if it were the total under-counts (80). Claude Code: `msg_cc_1` spans 3 lines with the same usage, so the
per-line sum is about 2x the truth.

**Redaction fails when disabled.** Planted: 3 emails, 2 phone numbers, an `sk-ant-` key, a `ghp_` key, a bearer
token, `api_key=...`, and a custom `ACME-CUST-\d+` id. With `TraceRedactor.trace` temporarily made a no-op:
```
FAILED tests/test_traces.py::test_redaction_removes_every_planted_secret_and_pii
E       AssertionError: assert ['ops.lead@ex...AKE0001', ...] == []
E         Left contains 10 more items, first extra item: 'ops.lead@example.com'
```
Restored: `uv run pytest tests/test_traces.py -k redaction` -> 3 passed. (A companion test asserts the fixture really
contains all 10 planted strings when redaction is off.)

**Stats** over six imports (`glmkit traces stats`):
```
traces: 15  replayable: 12  baseline-only: 3  redacted: 15
by source:    claude_code=3, openai=3, otel=3, anthropic=2, langfuse=2, litellm=2
  anthropic 1750/1000/700/37/0 ... TOTAL 9520 6348 1350 619 40   (input_total/cache_read/cache_write/output/reasoning)
```

**Dashboard.** Token check failed once on a planted `.tbl-wrap { color: #ff0000; }` in a temporary `traces.css`:
```
.../site11x/index.html:64 raw-color #ff0000
token check: 1 finding(s)        (glmkit report build exit code: 1)
```
Removed the CSS, then `glmkit report build $S/r11 -o $S/site11` -> `token check: 0 finding(s)`, `glmkit report check` -> 0.
Smoke (headless Chrome via Playwright, 1440 and 390 px, every tab, drawer open/close):
`NODE_PATH=/Users/nainish/node_modules CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tools/site-smoke/smoke.mjs $S/site11/index.html --out $S/shots11 --tab traces` -> `"errors": []` at both
viewports, `overflowX: 0`, `"ok": true`. Screenshots viewed: composition bar 80/20 replayable, task-type bar list,
token bar list, sets table (scrolls inside its wrapper at 390 px).

**Unverified.** Langfuse export column names (assumed, documented in the adapter); Claude Code format is
undocumented (fail-soft, tested against synthetic lines only); real OTel exporters may name the content attributes
slightly differently from the pinned doc.
