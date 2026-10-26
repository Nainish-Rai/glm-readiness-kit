# 06 · Bridge core + OpenAI-compatible proxy

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/06-bridge-core-openai

## What to build

`glm-bridge`: a framework-neutral core of request, response and stream rewriters, served first as a standalone
OpenAI-compatible proxy (`glmkit bridge serve --upstream <profile>`). Point any OpenAI-SDK app or harness at it and
the known GLM wire problems are fixed. The research found no existing gateway that does these fixes
(`research/02-bridge.md` §2, §5).

- Core, as pure functions over JSON bodies and parsed SSE events, with upstream behaviour described as data
  (profiles: zai, zai-plan, cerebras, generic):
  - `thinking: disabled` → enabled + effort `low` on models that force thinking;
  - clamp `reasoning_effort` to low/high/max;
  - flatten top-level `oneOf`/empty-`properties` tool schemas, and map arguments back;
  - repair malformed tool arguments (string vs object, broken JSON). If repair fails, **retry once with the parse
    error injected**; never silently substitute `{}`;
  - replay `reasoning_content` the way each upstream expects it (pass verbatim, rename, or strip);
  - error classifier that maps Z.ai business codes to HTTP status, retryability and `Retry-After`. Reuse 03's
    taxonomy if merged, otherwise implement the table from `research/02-bridge.md` and reconcile at merge.
- Every applied fix emits a structured **fix event** (fix id, request id, before/after summary, no content by
  default). Events are written as a result envelope so they show up on the dashboard.
- Mock-backed proof: run 02's and 03's relevant cases (or local equivalents if those slices aren't merged yet)
  directly against a defective mock and then through the bridge. Each must fail direct and pass via the bridge.
- Dashboard: a **Bridge** tab with fix events by type over time (stacked bars), top fixes (bar list), and an event
  table with drawer detail.

## Acceptance criteria

- [x] An OpenAI Python SDK client pointed at the bridge completes a tool-calling loop against a defective mock
      (forced-thinking, oneOf-empty-args, broken-JSON args) with correct results (evidence: direct run fails,
      bridged run passes).
- [x] Streaming through the bridge preserves event order; per-index argument buffers are correct under chunk splits
      (property-based test).
- [x] Retry-with-parse-error happens at most once per call and is visible as a fix event.
- [x] No request or response content is logged unless `--log-content` is set; auth headers are never logged.
- [x] Bridge tab renders a mock session; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

### Built

`src/glmkit/bridge/` (core, proxy, recorder, server, cli), mock defects `bridge-oneof-empty-args`,
`bridge-broken-json-args`, `bridge-unrepairable-args`, `bridge-reasoning-reject`, Bridge tab
(`report/pages/bridge.py|js`), contract docs in `docs/contracts.md` section 9. Error table is local
(`core/errors.py`) behind the `ErrorClassifier` protocol; reconcile with issue 03's taxonomy at merge.
Forced thinking and bad effort values need no defect: the mock's default api-class behaviour (400/1210, 400/1214)
is the failure.

### 1. OpenAI SDK tool loop, direct vs bridged

Command: `uv run python tools/bridge-proof/proof.py --out results/bridge-demo` (trimmed, exit 0; each defect is
planted per request via `X-GLMKit-Defects`, which the bridge forwards)

```text
== forced thinking (thinking: disabled, effort medium)  [defect=none, flavor=zai]
  nonstream direct : FAIL HTTP 400: Error code: 400 - {'error': {'code': '1210', 'message': '该模型始终思考，不支持关闭思考；请使用 low、high 或 max。'}}
  nonstream bridged: PASS sunny in Paris, found glm
  stream    direct : FAIL HTTP 400: Error code: 400 - {'error': {'code': '1210', 'message': '该模型始终思考，不支持关闭思考；请使用 low、high 或 max。'}}
  stream    bridged: PASS sunny in Paris, found glm
  fix events: 26 (effort-clamp, schema-args-unflatten, schema-flatten, thinking-disabled-to-low)
== oneOf tool schema -> {} arguments  [defect=bridge-oneof-empty-args, flavor=zai]
  nonstream direct : FAIL ValueError: lookup: needs query or url, got {}
  nonstream bridged: PASS sunny in Paris, found glm
  stream    direct : FAIL ValueError: lookup: needs query or url, got {}
  stream    bridged: PASS sunny in Paris, found glm
  fix events: 10 (schema-args-unflatten, schema-flatten)
== broken-JSON arguments  [defect=bridge-broken-json-args, flavor=zai]
  nonstream direct : FAIL JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
  nonstream bridged: PASS sunny in Paris, found glm
  stream    direct : FAIL JSONDecodeError: Expecting value: line 1 column 1 (char 0)
  stream    bridged: PASS sunny in Paris, found glm
  fix events: 14 (schema-args-unflatten, schema-flatten, tool-args-repair)
== unrepairable arguments (retry with parse error)  [defect=bridge-unrepairable-args, flavor=zai]
  nonstream direct : FAIL JSONDecodeError: Expecting value: line 1 column 1 (char 0)
  nonstream bridged: PASS sunny in Paris, found glm
  stream    direct : FAIL JSONDecodeError: Expecting value: line 1 column 1 (char 0)
  stream    bridged: PASS sunny in Paris, found glm
  fix events: 14 (schema-args-unflatten, schema-flatten, tool-args-retry)
== reasoning_content rejected (Cerebras flavor)  [defect=bridge-reasoning-reject, flavor=cerebras]
  nonstream direct : FAIL HTTP 422: Error code: 422 - {'error': {'message': 'Unrecognized request argument supplied: reasoning_content', 'type': 'inva
  nonstream bridged: PASS sunny in Paris, found glm
  stream    direct : FAIL HTTP 422: Error code: 422 - {'error': {'message': 'Unrecognized request argument supplied: reasoning_content', 'type': 'inva
  stream    bridged: PASS sunny in Paris, found glm
  fix events: 16 (reasoning-replay, schema-args-unflatten, schema-flatten)
session envelopes written under results/bridge-demo
```

### 2. Tests and lint

`uv run pytest -q` -> 163 passed. `uv run ruff check` -> All checks passed!
Relevant: `tests/test_bridge_core.py` (rewriters, repair, retry-once, errors, middleware, property-based stream
test: random argument fragmentation, interleaved indices, arbitrary byte re-chunking through `SSEParser`),
`tests/test_bridge_proxy.py` (OpenAI SDK loop per defect, stream and non-stream; retry at most once and visible as
`tool-args-retry`; unrepaired -> 502 / stream error event, never `{}`; no content or auth in logs/files by default;
`--log-content` adds content; error mapping and Retry-After; traceback 500 sanitized).

### 3. No content / no auth logged

`glmkit bridge serve --upstream mock --mock-defect bridge-broken-json-args --port 8791 --out /tmp/br`, then a curl
with `Authorization: Bearer sk-SECRETKEY123` and `city=SECRETCITY`: response 200 with
`x-glmkit-bridge-fixes: thinking-disabled-to-low,tool-args-repair`; console/log line is only
`req_ca5f175c5389 /v1/chat/completions -> 200 fixes=thinking-disabled-to-low,tool-args-repair`;
`grep -rl "SECRETKEY123\|SECRETCITY" /tmp/br` -> no match.

### 4. Dashboard

- Token check fails once on a planted raw colour: `.bridge-plant { color: #ff0000; }` in a temporary
  `bridge.css` -> `site/index.html:64 raw-color #ff0000`, `token check: 1 finding(s)`, exit 1. Removed, rebuilt:
  `glmkit report build results/bridge-demo -o site/` -> `token check: 0 finding(s)`, exit 0.
- `NODE_PATH=~/node_modules CHROME_PATH=".../Google Chrome" node tools/site-smoke/smoke.mjs site/index.html --out shots/`
  -> `"ok": true`, `errors: []` at 1440 and 390 px, no horizontal scroll, drawer opens/closes with focus return
  on the Runs and Bridge tabs. Screenshots viewed (`bridge-1440.png`, `bridge-390.png`): stacked bars, top-fixes
  list and event table render.
