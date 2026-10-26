# 03 · Conformance: wire contract, errors, Anthropic endpoint

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/03-conform-wire-anthropic

## What to build

The protocol-contract part of the verifier: does the endpoint behave as GLM's documented wire spec says, on both
the OpenAI-compatible and Anthropic-compatible paths, without 500s that leak Python errors?

- Cases WIRE-003 … WIRE-018, ANTH-001 … ANTH-005, AUTH-001 and LIM-001 from `research/01-conformance.md` §5:
  - effort enum low/high/max and the rejection of other values;
  - unknown model must never be silently answered by another model;
  - bad-input validation (never 5xx, no `Traceback`/`NoneType` in bodies);
  - Anthropic body without `max_tokens`;
  - `tool_use.input` sent as an array (zai-coding-plugins#14/#15);
  - >1 MB bodies;
  - `tool_choice` variants;
  - stop words;
  - JSON mode;
  - SSE termination and finish semantics;
  - context overflow;
  - `message_start.usage.input_tokens` > 0 (GLM-5#139);
  - auth header variants;
  - token-accounting ratio across 10 corpora vs reference (zai-coding-plugins#12);
  - model aliases;
  - auth/plan-key errors;
  - burst rate limiting with Retry hints.
- Behaviour depends on endpoint class. The case's expectation table decides pass/soft/fail per class (API, Plan,
  third-party, self-hosted), as the research documents differences between them.
- A Z.ai **error taxonomy** module mapping business codes (1000–1321 table in `research/01-conformance.md` §2.5)
  to category, retryability and suggested HTTP status. Slice 06 will reuse it.
- Mock defects:
  - 500-with-traceback on missing `max_tokens` and on an array `tool_use.input`;
  - zero `message_start` usage;
  - silent model fallback;
  - inflated token counts;
  - hanging stream.
- Dashboard: an **Errors** tab that groups failures by taxonomy category (error-group accordion), plus a
  token-accounting ratio chart per corpus.

## Acceptance criteria

- [x] All listed cases exist with provenance and per-class expectations.
- [x] Each deterministic case fails on its planted defect and passes without it (evidence included).
- [x] The error taxonomy covers every code in the research table and has unit tests; codes not in the table are
      reported as `unknown`, never dropped.
- [x] A hanging stream is detected within a configurable timeout, never hangs the runner.
- [x] Errors tab renders the mock run; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

Test and lint: `uv run pytest -q` all green (includes `tests/test_wire_cases.py`: clean pass on three profiles, one
fail-on-defect row per defect, hang timing, Errors tab data) and `uv run ruff check` clean.

What was built: 23 cases (WIRE-003..018 with variants, ANTH-001..005, AUTH-001, LIM-001) under `cases/{wire,anthropic,auth,limits}`;
`glmkit.zai_errors` (taxonomy, documented in docs/contracts.md section 9); 22 mock defects (`mock/defects/wire_*.py`);
`conform/checks/wire.py` (25 `wire_*` checks); `conform/executors/wire.py`; the Errors tab (`report/pages/errors.{py,js,css}`).

Not verified / known limits: nothing ran against live Z.ai (issue 16); ASSUMED codes are marked in the case notes
(1214 for a bad effort, Anthropic error shape). ANTH-004's OpenAI-vs-Anthropic path comparison (2 percent) is not
implemented; the reference is count_tokens or `$GLMKIT_TOKEN_REFERENCE`. AUTH-001 cross-class key probes only run when
`GLMKIT_ZAI_PLAN_KEY` / `GLMKIT_ZAI_API_KEY` are set. WIRE-011 repeats 3 (research asks 10); WIRE-016 compares length class only.

### Dashboard

- Token check: planted `.ratio-h { color: #ff0000; }` in `errors.css`, then `uv run glmkit report build results/ -o /tmp/claude-501/site-bad`
  printed `.../index.html:69 raw-color #ff0000` and `token check: 1 finding(s)`; removed, rebuilt: `token check: 0 finding(s)`.
- `NODE_PATH=/Users/nainish/node_modules CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tools/site-smoke/smoke.mjs site/index.html --out shots/`
  -> `"ok": true`, `errors: []` for every tab at 1440 and 390 px (no console errors, no horizontal scroll). Screenshots
  `shots/errors-1440.png` and `shots/errors-390.png` were viewed: failure accordion by category, token ratio bars per corpus
  (clean 1.00x, inflated 1.40x / 2.80x flagged "over limit"), taxonomy table.

### Proof: each case passes on the clean mock and fails on its planted defect

Clean runs, all new cases, three profiles:

```text
$ glmkit conform run --target mock --cases 'WIRE-00[3-9]' --cases 'WIRE-01*' --cases 'ANTH-*' --cases 'AUTH-*' --cases 'LIM-*' --include-opt-in  (exit 0)
verdict: pass  (25 passed, 0 failed, 0 soft, 12 skipped, 0 errors)
$ glmkit conform run --target mock-anthropic --cases 'WIRE-00[3-9]' --cases 'WIRE-01*' --cases 'ANTH-*' --cases 'AUTH-*' --cases 'LIM-*' --include-opt-in  (exit 0)
verdict: pass  (35 passed, 0 failed, 0 soft, 2 skipped, 0 errors)
$ glmkit conform run --target mock-plan --cases 'WIRE-00[3-9]' --cases 'WIRE-01*' --cases 'ANTH-*' --cases 'AUTH-*' --cases 'LIM-*' --include-opt-in  (exit 0)
verdict: pass  (25 passed, 0 failed, 0 soft, 12 skipped, 0 errors)
```

#### `anthropic-missing-max-tokens-500` -> WIRE-008

```text
$ glmkit conform run --target mock-anthropic -c WIRE-008 --stream-timeout 3  (exit 0)
  PASS  WIRE-008[nonstream]                critical
  PASS  WIRE-008[stream]                   critical
verdict: pass  (2 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c WIRE-008 --stream-timeout 3  -D anthropic-missing-max-tokens-500  (exit 1)
  FAIL  WIRE-008[nonstream]                critical  no_5xx: HTTP 500
  FAIL  WIRE-008[stream]                   critical  no_5xx: HTTP 500
verdict: fail  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)
```

#### `tool-use-input-array-500` -> WIRE-009

```text
$ glmkit conform run --target mock-anthropic -c WIRE-009 --stream-timeout 3  (exit 0)
  PASS  WIRE-009[nonstream]                critical
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c WIRE-009 --stream-timeout 3  -D tool-use-input-array-500  (exit 1)
  FAIL  WIRE-009[nonstream]                critical  no_5xx: HTTP 500
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `validation-500` -> WIRE-007

```text
$ glmkit conform run --target mock -c WIRE-007 --stream-timeout 3  (exit 0)
  PASS  WIRE-007[nonstream]                critical
  PASS  WIRE-007/temperature-2[nonstream]  critical
  PASS  WIRE-007/top-p-0[nonstream]        critical
  PASS  WIRE-007/max-tokens-200000[nonstream] critical
  PASS  WIRE-007/tools-129[nonstream]      critical
verdict: pass  (5 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-007 --stream-timeout 3  -D validation-500  (exit 1)
  FAIL  WIRE-007[nonstream]                critical  no_5xx: HTTP 500
  FAIL  WIRE-007/temperature-2[nonstream]  critical  no_5xx: HTTP 500
  FAIL  WIRE-007/top-p-0[nonstream]        critical  no_5xx: HTTP 500
  FAIL  WIRE-007/max-tokens-200000[nonstream] critical  no_5xx: HTTP 500
  FAIL  WIRE-007/tools-129[nonstream]      critical  no_5xx: HTTP 500
verdict: fail  (0 passed, 5 failed, 0 soft, 0 skipped, 0 errors)
```

#### `context-overflow-500` -> WIRE-018

```text
$ glmkit conform run --target mock -c WIRE-018 --stream-timeout 3  (exit 0)
  PASS  WIRE-018[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-018 --stream-timeout 3  -D context-overflow-500  (exit 1)
  FAIL  WIRE-018[nonstream]                major     no_5xx: HTTP 500
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `auth-missing-500` -> AUTH-001

```text
$ glmkit conform run --target mock-anthropic -c AUTH-001 --stream-timeout 3  (exit 0)
  PASS  AUTH-001[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c AUTH-001 --stream-timeout 3  -D auth-missing-500  (exit 1)
  FAIL  AUTH-001[nonstream]                major     wire_no_5xx_any: 1 of 2 exchanges failed hard: [('auth:missing', 500)]
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `silent-model-fallback` -> WIRE-006

```text
$ glmkit conform run --target mock -c WIRE-006 --stream-timeout 3  (exit 0)
  PASS  WIRE-006[nonstream]                critical
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-006 --stream-timeout 3  -D silent-model-fallback  (exit 1)
  FAIL  WIRE-006[nonstream]                critical  wire_unknown_model_rejected: unknown model 'glm-9.9' was answered 200 (response model 'glm-5.3')
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `alias-echo-model` -> ANTH-005

```text
$ glmkit conform run --target mock-anthropic -c ANTH-005 --stream-timeout 3  (exit 0)
  PASS  ANTH-005[nonstream]                minor   
  PASS  ANTH-005/claude-sonnet[nonstream]  minor   
  PASS  ANTH-005/one-million[nonstream]    minor   
verdict: pass  (3 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c ANTH-005 --stream-timeout 3  -D alias-echo-model  (exit 0)
  FAIL  ANTH-005[nonstream]                minor     wire_alias_model_reported: alias 'opus' answered 200 but the reply model is 'opus', not a GLM model
  FAIL  ANTH-005/claude-sonnet[nonstream]  minor     wire_alias_model_reported: alias 'claude-sonnet-4-5' answered 200 but the reply model is 'claude-sonnet-4-5', not a GLM model
  PASS  ANTH-005/one-million[nonstream]    minor   
verdict: warn  (1 passed, 2 failed, 0 soft, 0 skipped, 0 errors)
```

#### `zero-message-start-usage` -> ANTH-001

```text
$ glmkit conform run --target mock-anthropic -c ANTH-001 --stream-timeout 3  (exit 0)
  PASS  ANTH-001[stream]                   major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c ANTH-001 --stream-timeout 3  -D zero-message-start-usage  (exit 1)
  FAIL  ANTH-001[stream]                   major     wire_message_start_input_tokens: message_start.usage input tokens are 0
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `inflated-token-counts` -> ANTH-004

```text
$ glmkit conform run --target mock-anthropic -c ANTH-004 --stream-timeout 3  (exit 0)
  PASS  ANTH-004[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c ANTH-004 --stream-timeout 3  -D inflated-token-counts  (exit 1)
  FAIL  ANTH-004[nonstream]                major     wire_token_ratio: over 1.3x the reference: json 1.40x, english 1.40x, python 1.40x, cjk 2.80x, special 1.40x, markdown 1.40x, logs 1.40x, ba
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `large-body-zero-usage` -> WIRE-010

```text
$ glmkit conform run --target mock -c WIRE-010 --stream-timeout 3  (exit 0)
  PASS  WIRE-010[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-010 --stream-timeout 3  -D large-body-zero-usage  (exit 1)
  FAIL  WIRE-010[nonstream]                major     wire_large_body_handled: 200 but usage.prompt_tokens=0 for a 1100126-byte body (expected >= 137516)
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `stream-malformed` -> WIRE-015

```text
$ glmkit conform run --target mock -c WIRE-015 --stream-timeout 3  (exit 0)
  PASS  WIRE-015[stream]                   critical
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-015 --stream-timeout 3  -D stream-malformed  (exit 1)
  FAIL  WIRE-015[stream]                   critical  sse_terminates: 2 finish events
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `reasoning-after-content` -> WIRE-016

```text
$ glmkit conform run --target mock-anthropic -c WIRE-016 --stream-timeout 3  (exit 0)
  PASS  WIRE-016[stream]                   major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c WIRE-016 --stream-timeout 3  -D reasoning-after-content  (exit 1)
  FAIL  WIRE-016[stream]                   major     wire_reasoning_before_content: 7 reasoning delta(s) after the first answer text
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `effort-ignored` -> WIRE-003

```text
$ glmkit conform run --target mock -c WIRE-003 --stream-timeout 3  (exit 0)
  PASS  WIRE-003[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-003 --stream-timeout 3  -D effort-ignored  (exit 1)
  FAIL  WIRE-003[nonstream]                major     wire_effort_monotone: effort has no effect on thinking length (low:38 <= high:38 <= max:38)
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `invalid-effort-accepted` -> WIRE-004

```text
$ glmkit conform run --target mock -c WIRE-004 --stream-timeout 3  (exit 0)
  PASS  WIRE-004[nonstream]                major   
  PASS  WIRE-004/xhigh[nonstream]          major   
  PASS  WIRE-004/none[nonstream]           major   
verdict: pass  (3 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-004 --stream-timeout 3  -D invalid-effort-accepted  (exit 1)
  FAIL  WIRE-004[nonstream]                major     http_status: expected equals 400, got 200
  FAIL  WIRE-004/xhigh[nonstream]          major     http_status: expected equals 400, got 200
  FAIL  WIRE-004/none[nonstream]           major     http_status: expected equals 400, got 200
verdict: fail  (0 passed, 3 failed, 0 soft, 0 skipped, 0 errors)
```

#### `effort-none-still-thinks` -> WIRE-005

```text
$ glmkit conform run --target mock -c WIRE-005 --stream-timeout 3  (exit 0)
  PASS  WIRE-005[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-005 --stream-timeout 3  -D effort-none-still-thinks  (exit 1)
  FAIL  WIRE-005[nonstream]                major     reasoning_absent: unexpected reasoning (51 chars)
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `tool-choice-ignored` -> WIRE-011

```text
$ glmkit conform run --target mock -c WIRE-011 --stream-timeout 3  (exit 0)
  PASS  WIRE-011[nonstream]                info    
  PASS  WIRE-011/required[nonstream]       info    
  PASS  WIRE-011/named[nonstream]          info    
verdict: pass  (3 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-011 --stream-timeout 3  -D tool-choice-ignored  (exit 0)
  FAIL  WIRE-011[nonstream]                info      wire_tool_choice_honored: tool_choice none but the model called ['get_weather']
  FAIL  WIRE-011/required[nonstream]       info      wire_tool_choice_honored: tool_choice required but no tool was called
  FAIL  WIRE-011/named[nonstream]          info      wire_tool_choice_honored: tool_choice named 'get_time' but calls were ['get_weather']
verdict: warn  (0 passed, 3 failed, 0 soft, 0 skipped, 0 errors)
```

#### `stop-words-ignored` -> WIRE-012

```text
$ glmkit conform run --target mock -c WIRE-012 --stream-timeout 3  (exit 0)
  PASS  WIRE-012[nonstream]                minor   
  PASS  WIRE-012/two-words[nonstream]      minor   
verdict: pass  (2 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-012 --stream-timeout 3  -D stop-words-ignored  (exit 0)
  FAIL  WIRE-012[nonstream]                minor     wire_stop_honoured: stop word(s) ['END'] still in the answer
  FAIL  WIRE-012/two-words[nonstream]      minor     wire_stop_honoured: stop word(s) ['END'] still in the answer
verdict: warn  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)
```

#### `json-mode-fenced` -> WIRE-014

```text
$ glmkit conform run --target mock -c WIRE-014 --stream-timeout 3  (exit 0)
  PASS  WIRE-014[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-014 --stream-timeout 3  -D json-mode-fenced  (exit 1)
  FAIL  WIRE-014[nonstream]                major     wire_json_parseable: content is not JSON: Expecting value: line 1 column 1 (char 0)
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `do-sample-ignored` -> WIRE-013

```text
$ glmkit conform run --target mock -c WIRE-013 --stream-timeout 3  (exit 0)
  PASS  WIRE-013[nonstream]                minor   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c WIRE-013 --stream-timeout 3  -D do-sample-ignored  (exit 0)
  FAIL  WIRE-013[nonstream]                minor     wire_outputs_identical: 5 different answers in 5 runs
verdict: warn  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `anthropic-bearer-rejected` -> ANTH-002

```text
$ glmkit conform run --target mock-anthropic -c ANTH-002 --stream-timeout 3  (exit 0)
  PASS  ANTH-002[nonstream]                major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c ANTH-002 --stream-timeout 3  -D anthropic-bearer-rejected  (exit 1)
  FAIL  ANTH-002[nonstream]                major     http_status: expected equals 200, got 401
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `thinking-block-rejected` -> ANTH-003

```text
$ glmkit conform run --target mock-anthropic -c ANTH-003 --stream-timeout 3  (exit 0)
  PASS  ANTH-003[nonstream]                major   
  PASS  ANTH-003/effort[nonstream]         major   
  PASS  ANTH-003/roundtrip[nonstream]      major   
verdict: pass  (3 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock-anthropic -c ANTH-003 --stream-timeout 3  -D thinking-block-rejected  (exit 1)
  PASS  ANTH-003[nonstream]                major   
  PASS  ANTH-003/effort[nonstream]         major   
  FAIL  ANTH-003/roundtrip[nonstream]      major     wire_tool_roundtrip: turn2 failed: HTTP 400 code=1210 Invalid API parameter: thinking block in history
verdict: fail  (2 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `rate-limit-bare` -> LIM-001

```text
$ glmkit conform run --target mock -c LIM-001 --stream-timeout 3  (exit 0)
  PASS  LIM-001[nonstream]                 major   
verdict: pass  (1 passed, 0 failed, 0 soft, 0 skipped, 0 errors)

$ glmkit conform run --target mock -c LIM-001 --stream-timeout 3  -D rate-limit-bare  (exit 1)
  FAIL  LIM-001[nonstream]                 major     wire_rate_limit_wellformed: burst:4: code None is not a documented throttle code; burst:5: code None is not a documented throttle code; burs
verdict: fail  (0 passed, 1 failed, 0 soft, 0 skipped, 0 errors)
```

#### `rate-limit-burst` (well-formed 429s: throttled, not failed)

```text
$ glmkit conform run --target mock -c LIM-001 --stream-timeout 3  -D rate-limit-burst  (exit 0)
  THRT  LIM-001[nonstream]                 major   
verdict: warn  (0 passed, 0 failed, 0 soft, 0 skipped, 0 errors)
```

#### `hanging-stream` -> WIRE-015, WIRE-017 (detected at the 3 s stream timeout; wall time shown)

```text
$ glmkit conform run --target mock -c WIRE-01[57] --stream-timeout 3  -D hanging-stream  (exit 1)
  FAIL  WIRE-015[stream]                   critical  sse_terminates: stream did not finish before the timeout
  FAIL  WIRE-017[stream]                   critical  sse_terminates: stream did not finish before the timeout
verdict: fail  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)
wall: 6.7 s
$ glmkit conform run --target mock-anthropic -c WIRE-01[57] --stream-timeout 3  -D hanging-stream  (exit 1)
  FAIL  WIRE-015[stream]                   critical  sse_terminates: stream did not finish before the timeout
  FAIL  WIRE-017[stream]                   critical  sse_terminates: stream did not finish before the timeout
verdict: fail  (0 passed, 2 failed, 0 soft, 0 skipped, 0 errors)
wall: 6.6 s
```

