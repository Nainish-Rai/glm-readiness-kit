# 04 · Conformance: thinking, streaming, cache, language

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/04-conform-thinking-stream

## What to build

The multi-turn agent-loop part of the verifier, which no competing verifier covers. It checks:
- reasoning replay;
- reasoning collapse;
- stream/non-stream parity;
- caching;
- whether the model switches to Chinese mid-conversation.

- Cases THINK-001 … THINK-008, STRM-001 … STRM-005, CACHE-001/002, LANG-001/002, USAGE-001 and MM-001 from
  `research/01-conformance.md` §5:
  - `reasoning_content` replay with `clear_thinking` true/false and its effect on cached tokens;
  - truncated or edited reasoning;
  - the `reasoning` alias and providers that reject the field (opencode#26762);
  - degeneracy: the GLM-5#168 styled-reasoning script vs a prose control, and a soak run for vllm#56868-style
    collapse;
  - determinism;
  - effort-clamp on self-hosted templates;
  - stream parity across tool cases, including offline 1-char/1-token chunk-split fixtures (sglang#35564);
  - `tool_stream` fragment reassembly;
  - usage parity;
  - cache-hit growth over an agent loop;
  - CJK leakage in English-only sessions (GLM-5#142, #54);
  - exact prompt-token constants;
  - image input on Flash and rejection on text-only 5.3.
- Soak cases are opt-in (`--soak`) and budget-capped. They are reported separately from the correctness verdict.
- Degeneracy metrics: repeated-token share, U+FFFD count, empty content with `finish_reason=length`.
- Mock defects:
  - reasoning-field rejection;
  - collapse after N turns;
  - stream/non-stream argument mismatch;
  - CJK injection;
  - no cache hits;
  - tool calls emitted inside reasoning.
- Dashboard: a **Agent loop** tab with per-turn cached-token growth (stacked bars), a degeneracy heat strip by turn,
  and a stream-parity table.

## Acceptance criteria

- [x] All listed cases exist with provenance; soak cases are excluded unless `--soak` is passed and stop at a
      token budget.
- [x] Each deterministic case fails on its planted defect and passes without it (evidence included).
- [x] Chunk-split fixtures prove the stream reassembler is order- and split-independent.
- [x] CJK detector excludes text quoted from inputs (unit tests with mixed-language fixtures).
- [x] Agent loop tab renders the mock run; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

### Built (extension points used)

- Cases: `cases/{thinking,stream,cache,language,usage,multimodal}/` (THINK-001..008, STRM-001..005 plus STRM-004B
  detector self-test, CACHE-001/002, LANG-001/002, USAGE-001, MM-001), fixtures in `cases/fixtures/agentloop/`.
- Executors: `conform/executors/agent_loop.py` (agent_loop, soak_loop, replay_compare, stream_pair, stream_fixture,
  repeat_prefix, repeat_identical, param_sweep, styled_replay). Checks: `conform/checks/agent_loop.py`.
- Mock defects (12): reasoning-field-rejected, reasoning-collapse, stream-args-mismatch, cjk-injection, no-cache-hits,
  tool-call-in-reasoning (the six listed) plus stream-usage-drift, usage-prompt-drift, nondeterministic-greedy,
  text-only-accepts-image, tool-stream-without-stream-500.
- Dashboard: `report/pages/agent_loop.{py,js,css}`. Soak: `glmkit conform soak` (`conform/commands/soak.py`).
- Helpers: `src/glmkit/agentloop/` (metrics, synth, streamfix).

### Deviations and notes

- Soak switch is `glmkit conform soak --budget N` (own run, own verdict, severity info) rather than `conform run --soak`,
  because `conform/cli.py` is a core file. `conform run --include-opt-in` also runs it. Budget also via
  `GLMKIT_SOAK_BUDGET_TOKENS`, turn cap via `GLMKIT_SOAK_MAX_TURNS`.
- Shared-file edits: `mock/engine.py` (text-only models reject image blocks with 400/1214; `clear_thinking` now decides
  whether replayed reasoning counts toward prompt tokens, default true = ignored); `tests/test_checks.py` (coverage test also
  accepts the checks tested in `tests/test_agentloop.py`); `tests/test_extension_points.py` (tab list assertion no longer
  hard-codes the exact tabs). No contract changes, no schema changes.
- Golden prompt-token constants exist only for the mock. api.z.ai constants are not guessed; the check skips there until
  issue 16 records them. THINK-008 is self-hosted only (skipped on the mock; its check is unit-tested).
  STRM-003 and THINK-002/-004/-008 are record-style cases: STRM-003 has a planted defect, the others are covered by unit tests.
- LANG detector treats a CJK run as quoted when it is a substring of any system/user/tool input.

### Evidence

Clean run, then each planted defect against its cases (trimmed):

```
$ uv run glmkit conform run --target mock --cases "THINK-*,STRM-*,CACHE-*,LANG-*,USAGE-*,MM-*"
run 20261026T155905Z-conform-c3332a  target=mock (openai_chat, api, glm-5.3)
  SKIP  THINK-008[nonstream]               info      endpoint class api not in applies_to.endpoint_classes
verdict: pass  (30 passed, 0 failed, 0 soft, 1 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "THINK-001,THINK-003" --mock-defect reasoning-field-rejected
  FAIL  THINK-001[nonstream]               major     all_turns_ok: turn1: HTTP 400 wrong_api_format messages.2.assistant.reasoning_content: property 'reasoning_content' i
  FAIL  THINK-003[nonstream]               major     http_status: expected equals 200, got 400
verdict: fail  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "THINK-005" --mock-defect reasoning-collapse
  SOFT  THINK-005[nonstream]               major     reasoning_degeneracy: final reasoning degenerate: score 1.0 (= / the 1.0, loops 1.0, char repeat True, U+FFFD 0)
  FAIL  THINK-005/prose-control[nonstream] major     reasoning_degeneracy: final reasoning degenerate: score 1.0 (= / the 1.0, loops 1.0, char repeat True, U+FFFD 0)
verdict: fail  (0 passed, 1 failed, 1 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "STRM-001,STRM-002" --mock-defect stream-args-mismatch
  FAIL  STRM-001[nonstream]                major     stream_nonstream_parity: stream differs from non-stream: get_weather arguments {"city": "example", "days": 1, "metric
  FAIL  STRM-002[nonstream]                major     tool_stream_fragments: tool call(s) {0: 1} streamed in fewer than 2 fragments
verdict: fail  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "LANG-001,LANG-002" --mock-defect cjk-injection
  FAIL  LANG-001[nonstream]                major     no_cjk_leak: 35 of 40 turns leak CJK (18 in text, 35 in reasoning); 875 per 1000 turns, 95% CI 739-945
  FAIL  LANG-001/effort-low[nonstream]     major     no_cjk_leak: 35 of 40 turns leak CJK (18 in text, 35 in reasoning); 875 per 1000 turns, 95% CI 739-945
  FAIL  LANG-001/effort-high[nonstream]    major     no_cjk_leak: 35 of 40 turns leak CJK (18 in text, 35 in reasoning); 875 per 1000 turns, 95% CI 739-945
  FAIL  LANG-001/effort-max[nonstream]     major     no_cjk_leak: 35 of 40 turns leak CJK (18 in text, 35 in reasoning); 875 per 1000 turns, 95% CI 739-945
  FAIL  LANG-002[nonstream]                major     output_language_matches: 5 of 5 answers are not in the context language (ja expected, zh in turn1)
verdict: fail  (0 passed, 5 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "CACHE-001,CACHE-002" --mock-defect no-cache-hits
  FAIL  CACHE-001[nonstream]               major     cached_tokens_positive: no cache hit (cached_tokens=0)
  FAIL  CACHE-002[nonstream]               major     cached_tokens_grow: no cache hits in 3 turns: [0, 0, 0]
verdict: fail  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "THINK-001" --mock-defect tool-call-in-reasoning
  FAIL  THINK-001[nonstream]               major     agent_turn_has_tool_call: no tool call in the turn
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "STRM-005" --mock-defect stream-usage-drift
  FAIL  STRM-005[nonstream]                major     usage_parity: usage differs between modes: prompt_tokens 39 vs 49
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "USAGE-001" --mock-defect usage-prompt-drift
  FAIL  USAGE-001[nonstream]               minor     prompt_tokens_golden: expected equals 33, got 48
  FAIL  USAGE-001/cjk[nonstream]           minor     prompt_tokens_golden: expected equals 33, got 48
  FAIL  USAGE-001/json[nonstream]          minor     prompt_tokens_golden: expected equals 237, got 339
  FAIL  USAGE-001/tools[nonstream]         minor     prompt_tokens_golden: expected equals 114, got 164
  FAIL  USAGE-001/image-flash[nonstream]   minor     prompt_tokens_golden: expected equals 35, got 51
verdict: warn  (0 passed, 5 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "THINK-007" --mock-defect nondeterministic-greedy
  FAIL  THINK-007[nonstream]               minor     identical_outputs: exact-match rate 33% over 3 runs (output sizes [224, 224, 224])
verdict: warn  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "MM-001" --mock-defect text-only-accepts-image
  FAIL  MM-001/text-only-rejects[nonstream] major     http_status: expected equals 400, got 200
verdict: fail  (3 passed, 1 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "STRM-003" --mock-defect tool-stream-without-stream-500
  FAIL  STRM-003[nonstream]                info      no_5xx: HTTP 500
  FAIL  STRM-003/glm-4.5[nonstream]        info      no_5xx: HTTP 500
verdict: warn  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform run --target mock --cases "STRM-004*"
  PASS  STRM-004[nonstream]                major   
  PASS  STRM-004B[nonstream]               minor   
verdict: pass  (2 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ uv run glmkit conform soak --target mock --budget 300
soak run 20261026T155917Z-conform-636302  target=mock  budget=300 decoded tokens
  PASS  THINK-006[nonstream]               6 turns  
  PASS  THINK-006/effort-high[nonstream]   8 turns  
verdict: pass  (soak is reported apart from the correctness verdict)

$ uv run glmkit conform soak --target mock --mock-defect reasoning-collapse
soak run 20261026T155917Z-conform-4ed5c9  target=mock  budget=200000 decoded tokens
  FAIL  THINK-006[nonstream]               100 turns  soak_degeneracy_rate: 96 of 100 turns degenerate (96.0 per 100); first turn4
  FAIL  THINK-006/effort-high[nonstream]   100 turns  soak_degeneracy_rate: 96 of 100 turns degenerate (96.0 per 100); first turn4
verdict: warn  (soak is reported apart from the correctness verdict)
```

Token check on a planted raw colour (`.al-note { color: #ff0000; }` appended to agent_loop.css), then restored:

```
$ uv run glmkit report build results/ -o /tmp/zz_bad
built /tmp/zz_bad/index.html  tabs=runs,conformance,agent_loop  runs=3
/tmp/zz_bad/index.html:79 raw-color #ff0000
token check: 1 finding(s)
exit=1
$ uv run glmkit report build results/ -o site/
built site/index.html  tabs=runs,conformance,agent_loop  runs=3
token check: 0 finding(s)
```

Browser smoke at 1440 and 390 px (no console errors, no page errors, no horizontal scroll, drawers open/close):

```
$ NODE_PATH=/Users/nainish/node_modules CHROME_PATH=".../Google Chrome" node tools/site-smoke/smoke.mjs site/index.html --out shots/
"errors": [] (1440)   "errors": [] (390)   "ok": true
```

Screenshots viewed: `shots/agent_loop-1440.png`, `shots/agent_loop-390.png` (not committed). Tests: `uv run pytest -q` and
`uv run ruff check` green.
