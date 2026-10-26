# 02 · Conformance: tool-call cases + provider scorecard

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/02-conform-tools

## What to build

The tool-calling part of the GLM vendor verifier, end to end. Tool-calling is the most frequently reported failure
area (see `research/01-conformance.md` §3, §5; `zai-research/sources/github.md`).

- Cases TOOL-001 … TOOL-015 from `research/01-conformance.md` §5:
  - basic call and irrelevance;
  - parallel calls;
  - typed args;
  - string fidelity: a 20 KB `write_file` content containing `</tool_call>`, quotes and backslashes;
  - schema shapes, with **TOOL-006** for the GLM-5#169 top-level `oneOf`/empty-`properties` → `{}` args bug;
  - 128/129 tools;
  - namespaced tool names (`mcp__server__tool`);
  - round trip with string `arguments` and `content: null` (GLM-5#158);
  - tool-call markers leaking into content (opencode#6708);
  - unique ids;
  - forced need at long context;
  - command-prefix emission fidelity (GLM-5#116);
  - trigger F1.
- Statistical cases run N repeats and report a pass rate with a Wilson 95% CI. **Critical cases are hard gates**:
  they cannot be averaged away. Validate JSON Schema against Draft 2020-12 and Draft 7 and report any disagreement.
  Classify errors with OpenRouter's taxonomy (InvalidJson / UnknownName / SchemaMismatch).
- An input adapter that replays K2-Vendor-Verifier / Prism-style JSONL corpora and computes trigger F1 and schema
  accuracy, so results stay comparable with Moonshot's verifier.
- Mock defects for each deterministic case:
  - oneOf → `{}`;
  - dict-only template rejecting string args;
  - `</tool_call>` truncation;
  - marker leak;
  - duplicate ids;
  - unknown tool name.
- Dashboard: a **Scorecard** tab with a provider × category heat strip, per-category pass rate with CI, hard-gate
  failures on top, and click-through to a case's request and response in the drawer.

## Acceptance criteria

- [x] All 15 cases exist as data files with provenance (source issue URL) and expected behaviour per endpoint class.
- [x] Every deterministic case fails against its planted mock defect and passes without it (evidence included).
- [x] Statistical cases report pass rate + Wilson CI; a critical failure makes the run verdict `fail` regardless of
      the other cases.
- [x] K2VV-format JSONL import works on a 20-line sample and reports F1 and schema accuracy.
- [x] Scorecard tab renders the mock run for at least 2 mock "providers" (clean vs defective), with no console
      errors at 1440/390 px; token check passes.

## Blocked by

- 01 Foundation

## Evidence

All commands run from the worktree root against the in-process mock (`--target mock`). Runs write to `results/`.

### What was built

- Cases `cases/tools/TOOL-001 ... TOOL-015` (+ fixtures in `cases/fixtures/tools/`, 20-line sample `cases/fixtures/corpora/k2vv-sample.jsonl`). Each carries source issue URLs and per-class `expect` (TOOL-007 differs per class). TOOL-014 is an opt-in soak.
- Checks `conform/checks/tools.py`: `tool_call_count`, `tool_calls_valid` (InvalidJson / UnknownName / SchemaMismatch, Draft 2020-12 and Draft 7, disagreement reported), `tool_arguments_nonempty_when_required`, `tool_args_echo_prompt` (strict types), `tool_arg_startswith`, `no_marker_leak`, `tool_ids_unique`, aggregate `pass_rate_wilson`, `k2vv_scores`.
- Executors `tool_shape` (128/129 tools, 20 KB string, padded 8k-130k history, 100 KB result) and `k2vv_replay`; K2VV adapter `conform/k2vv.py` and command `glmkit conform k2vv`.
- Mock: 16 defects in `mock/defects/tool_*.py`; `mock/toolsim.py` gives the mock parallel calls, "exactly these arguments:" fidelity and irrelevance answers.
- Scorecard tab: `report/pages/scorecard.{py,js,css}`.

### Proof of failure (clean passes, planted defect fails)

Full matrix is `tests/test_conform_tools.py::test_planted_defect_fails_its_case` (16 defect/case pairs, plus clean runs on mock, mock-anthropic and mock-plan). Samples:

```
$ glmkit conform run --target mock --cases TOOL-006
  PASS  TOOL-006[nonstream]  ... TOOL-006/refs[stream]   (10 items)
verdict: pass  (10 passed, 0 failed)
$ glmkit conform run --target mock --cases TOOL-006 -D oneof-empty-args
  FAIL  TOOL-006[nonstream]  critical  tool_calls_valid: SchemaMismatch on 'automation_update': 2020-12: <root>: {} is not valid under any of the given schemas
  FAIL  TOOL-006[stream]     critical  (same)
  PASS  TOOL-006/flat, /anyof, /allof, /refs (all modes)
verdict: fail  (8 passed, 2 failed)
```

Other defect runs (same pattern, all clean = pass):

```
TOOL-001 + flaky-tool-call            FAIL critical  (15/20 pass, Wilson CI 53-89%, below gte 0.95)
TOOL-002 + tools-always-called        FAIL  finish_reason: expected in ['stop'], got 'tool_calls'
TOOL-003/012 + duplicate-tool-ids     FAIL  tool_ids_unique: duplicate tool call ids [...]
TOOL-004 + typed-args-stringified     FAIL  SchemaMismatch ... active: 'True' is not of type 'boolean'
TOOL-005 + tool-call-end-truncation   FAIL critical  InvalidJson on 'write_file': Unterminated string
TOOL-007 + many-tools-broken          FAIL  names ['tool_000'] != ['tool_100']; n129: no_5xx HTTP 500
TOOL-008 + unknown-tool-name          FAIL  UnknownName on 'mcp.server.search_docs'
TOOL-009 + dict-only-chat-template    FAIL critical  http_status: expected equals 200, got 500
TOOL-009 + null-content-rejected      FAIL critical  http_status ... got 400 (content-null variant)
TOOL-010 + empty-tool-result-crash    FAIL  TOOL-010/empty: got 500
TOOL-011 + tool-marker-leak           FAIL critical  '<tool_call>' leaked into reasoning
TOOL-013 + long-context-tool-drop     FAIL  finish_reason: expected in ['tool_calls'], got 'stop'
TOOL-014 + command-prefix-dropped     FAIL  command does not start with 'cd /srv/app && '
TOOL-015 + tools-always-called        FAIL  F1 0.710 (P 0.550, R 1.000), below 0.73
```

### Statistical cases, hard gate, K2VV

```
$ glmkit conform k2vv corpora/k2vv-sample.jsonl --target mock
replayed 20 requests on mock
tool-call trigger F1 0.952  (precision 1.000, recall 0.909; TP 10 FP 0 FN 1 TN 9)
schema accuracy   1.000  (10 tool-call responses)
$ ... -D tools-always-called --json   ->  "f1": 0.7097, "schema_accuracy": 0.5
```

`test_critical_statistical_failure_cannot_be_averaged_away`: with `flaky-tool-call`, TOOL-001 (critical, 20 attempts) fails while 9 other items pass; verdict `fail`, hard_gate_failures >= 1. `test_statistical_case_reports_wilson_ci`: 20/20 gives CI 0.839-1.0.

### Dashboard

```
$ glmkit report build results/ -o site/      # 3 providers: mock, mock-anthropic, mock + 8 defects
built site/index.html  tabs=runs,conformance,scorecard  runs=4
token check: 0 finding(s)
# planted `.sc-bad { color: #ff0000; }` in scorecard.css:
site/index.html:86 raw-color #ff0000
token check: 1 finding(s)
$ CHROME_PATH=... node tools/site-smoke/smoke.mjs site/index.html --out shots
... "errors": [] at 1440 and at 390, "ok": true  (drawer opens/closes, focus returns)
```

Screenshots reviewed (`shots/scorecard-1440.png`, `-390.png`, `-drawer-1440.png`): hard-gate table, provider x category heat strips, per-category rate with CI, shape sensitivity; no horizontal page scroll at 390.

### Notes

- `uv run pytest -q`: all green; `uv run ruff check`: clean.
- Shared-file edits: `src/glmkit/mock/engine.py` (generate() delegates tool choice to `mock/toolsim.py`, plus an import); `tests/test_extension_points.py` (tab-list assertion made order-tolerant so new tabs do not break it). No contract changes, no schema export needed.
- Not verified against a live GLM endpoint (issue 16). Token counts for padded contexts use 4 chars/token.
