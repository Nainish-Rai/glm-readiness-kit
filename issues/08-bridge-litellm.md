# 08 · Bridge: LiteLLM plugin adapter

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/08-bridge-litellm

## What to build

Distribute the bridge core to LiteLLM users (the dominant gateway, ~62k stars) as a plugin. It reuses the same
core rewriters, so a LiteLLM proxy with `zai/glm-5.3` gets the fixes without running our proxy. See
`research/02-bridge.md` §3 for the hook inventory and which fixes fit which hook.

- A `CustomLogger`-based plugin wiring the core into the pre-call, deployment, streaming-iterator, success and
  failure hooks. Add a `CustomLLM` wrapper only for the retry-with-parse-error path, which hooks can't do reliably.
- Enable LiteLLM's existing web-search interception for the `zai` provider through config rather than
  reimplementing it.
- Document which fixes are available in the plugin vs only in the standalone proxy, as a table in the docs.
- Integration tests run a real LiteLLM proxy (pinned version) against the mock. They cover both the Python path and
  the Rust-core path if LiteLLM routes `zai/` through it; the research flags this as unverified.
- Fix events go into the same result envelope, so the Bridge tab shows them with source `litellm`.

## Acceptance criteria

- [x] `litellm --config` example with the plugin fixes forced-thinking, effort-clamp and schema-flatten defects
      on the mock (direct fails, via LiteLLM+plugin passes; evidence included).
- [x] The test suite pins a LiteLLM version, and a CI job runs the integration tests.
- [x] Docs table of fix availability per adapter is accurate to the tests.
- [x] No content logged by default.

## Blocked by

- 06 Bridge core + OpenAI-compatible proxy

## Evidence

Built: `src/glmkit/bridge/litellm_adapter.py` (logic, no LiteLLM import), `src/glmkit/bridge/litellm_plugin.py`
(`CustomLogger`, optional extra `glmkit[litellm]`, pinned `litellm[proxy]==1.105.5`), `examples/litellm/config*.yaml`,
`docs/bridge-litellm.md` (fix-availability table, Rust-core finding), `tools/litellm-proof/proof.py`, tests, CI job
`litellm`. Recorder/Bridge tab gained an optional `source` (events show `litellm`).

**Default suite and lint** (LiteLLM is not installed in the dev env, tests marked `litellm` are deselected):

```
$ uv run pytest
276 passed, 1 skipped (helm), 29 deselected in 34.66s
$ uv run ruff check && uv run ruff format --check src tests
All checks passed!   124 files already formatted
```

**Integration suite** (real LiteLLM 1.105.5 proxy in its own `uv run --isolated --no-dev --extra litellm --frozen`
env, plugin on, mock GLM servers; whole module runs twice: default routing and `LITELLM_RUST=1`):

```
$ uv run pytest -m litellm
29 passed, 277 deselected in 140.07s
```

**Proof, direct vs via LiteLLM + plugin** (`uv run python tools/litellm-proof/proof.py --out results/litellm-demo`, trimmed):

```
== forced-thinking  [defect=none, client={'thinking_disabled': True, 'effort': None}]
  nonstream direct : FAIL HTTP 400: Error code: 400 - {'error': {'code': '1210', 'message': '该模型始终思考，不支持关闭思考...
  nonstream litellm: PASS sunny in Paris, found glm
  stream    direct : FAIL HTTP 400: ... 1210 ...
  stream    litellm: PASS sunny in Paris, found glm
== effort-clamp  [defect=none, client={'thinking_disabled': False, 'effort': 'medium'}]
  nonstream direct : FAIL HTTP 400: Error code: 400 - {'error': {'code': '1214', 'message': 'The parameter reasoning_effort is invalid; use low, high or max
  nonstream litellm: PASS sunny in Paris, found glm
  stream    litellm: PASS sunny in Paris, found glm
== oneof-schema  [defect=bridge-oneof-empty-args, ...]
  nonstream direct : FAIL ValueError: lookup: needs query or url, got {}
  nonstream litellm: PASS sunny in Paris, found glm        (stream: same)
== broken-json  [defect=bridge-broken-json-args, ...]
  nonstream direct : FAIL JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
  nonstream litellm: PASS sunny in Paris, found glm        (stream: same)
== unrepairable  [defect=bridge-unrepairable-args, ...]
  nonstream direct : FAIL JSONDecodeError: Expecting value: line 1 column 1 (char 0)
  nonstream litellm: PASS sunny in Paris, found glm        (stream: same, retry with the parse error)
fix events: 74 (effort-clamp, schema-args-unflatten, schema-flatten, thinking-disabled-to-low, tool-args-repair, tool-args-retry); source=litellm
```

Also tested through the live proxy: Z.ai `429 / 1113` becomes HTTP 402; arguments that stay broken after the retry
become HTTP 502 (stream: an error frame then `[DONE]`) and the broken text never reaches the client; the session file
contains no prompt text, key or upstream URL; `config.websearch.yaml` loads with `enabled_providers == ['zai']`.

**Bridge tab**: `uv run glmkit report build results/litellm-demo -o site/`, then headless Chrome
(`--headless=new`, 1440 and 390) and `tools/site-smoke/smoke.mjs`: `"ok": true`, `"errors": []` on every tab at both
widths, no horizontal scroll at 390. The tab shows 74 events, a `Source` column with `litellm` pills, and the drawer
shows `source litellm`. No page CSS was added (so no token-check plant); `report build` prints `token check: 0 finding(s)`.

**Rust core**: LiteLLM 1.105.5 does not route `zai/` through it. `litellm/rust_bridge/catalog.py`: chat completions
`PYTHON_ONLY` for all providers; `/v1/messages` Rust opt-in only for provider `anthropic`. Hooks fire identically with
`LITELLM_RUST=1` (integration suite passes both ways). Probe: with `LITELLM_RUST=1` and `anthropic/glm-5.3` on
`/v1/messages`, `async_pre_request_hook` does not fire (the deployment hooks do).

**LiteLLM findings that shaped the plugin**: the `zai` provider fails when `thinking` is passed top-level (OpenAI
SDK `unexpected keyword argument 'thinking'`) and rejects `reasoning_effort` (`UnsupportedParamsError`); the plugin
moves both into `extra_body`. `/v1/messages` on `zai/` runs an outer `anthropic_messages` call and an inner
`acompletion`; the plugin only rewrites the inner one. LiteLLM Router retries a 429 before the failure hook, so a
mapped 402 arrives after those retries (the test config sets `num_retries: 0`).

**Not verified**: live Z.ai (mock only); a real web-search backend; Anthropic `/v1/messages` fixes through the plugin
(only that the inner call is rewritten, observed in a probe); stream repair outside the LiteLLM proxy (not supported,
documented); other LiteLLM versions.
