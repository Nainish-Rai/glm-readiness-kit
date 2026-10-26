# GLM bridge as a LiteLLM plugin

The same core rewriters as `glmkit bridge serve`, packaged as a LiteLLM `CustomLogger` so a LiteLLM proxy with
`zai/glm-5.3` gets the fixes without running a second proxy. Pinned and tested against **LiteLLM 1.105.5**
(`pyproject.toml` extra `litellm`, locked in `uv.lock`).

```sh
pip install 'glmkit[litellm]'            # LiteLLM is an optional extra, never a core dependency
export ZAI_API_KEY=...                   # read by LiteLLM, never by glmkit; nothing is committed
export GLMKIT_BRIDGE_OUT=./results       # where the Bridge tab's session file goes
litellm --config examples/litellm/config.yaml
glmkit report build results/ -o site/    # fix events appear on the Bridge tab with source "litellm"
```

`examples/litellm/config.yaml` is the whole integration: one line, `callbacks: glmkit.bridge.litellm_plugin.plugin`.
Without LiteLLM installed, importing the plugin fails with a message naming the extra.

| Setting (environment) | Effect |
|---|---|
| `GLMKIT_BRIDGE_OUT` | results folder (default `./results`); the session is rewritten about once a second and at exit |
| `GLMKIT_BRIDGE_FLAVOR` | force `zai`, `zai-plan`, `cerebras` or `generic` (default: from the provider prefix of the model) |
| `GLMKIT_BRIDGE_MODELS` | extra regex of model names to treat as GLM (default `glm-*`); other models are never touched |
| `GLMKIT_BRIDGE_LOG_CONTENT` | `1` to also record message content in the session file (default off) |

**Privacy.** No message content, upstream URL or key is written to the session file or logged unless
`GLMKIT_BRIDGE_LOG_CONTENT=1`. Tests assert it.

## Which fixes run where

"Tested" means a test in `tests/test_litellm_integration.py` (real LiteLLM proxy, mock GLM, direct fails and via
LiteLLM passes), run with default routing and `LITELLM_RUST=1`. "Unit" means `tests/test_litellm_adapter.py` only.

| Fix | Standalone proxy (`glmkit bridge serve`) | LiteLLM plugin | Plugin hook | Plugin status |
|---|---|---|---|---|
| `thinking-disabled-to-low` | yes | yes, stream and non-stream | `async_pre_call_deployment_hook` | tested |
| `effort-clamp` | yes | yes, stream and non-stream | same | tested |
| `schema-flatten`, `schema-args-unflatten` | yes | yes, stream and non-stream | same + response hooks | tested |
| `tool-args-repair` | yes | non-stream: yes; stream: yes, **LiteLLM proxy only** | `async_post_call_success_deployment_hook`; `async_post_call_streaming_iterator_hook` | tested |
| `tool-args-retry` (re-ask once with the parse error) | yes | non-stream: yes; stream: yes, proxy only | `async_should_run_chat_completion_agentic_loop` / `async_run_chat_completion_agentic_loop`; stream: re-ask inside the iterator hook | tested (no `CustomLLM` wrapper was needed) |
| `tool-args-unrepaired` (error, never `{}`) | HTTP 502 | non-stream: HTTP 502; stream: error frame then `[DONE]` | `async_post_call_success_hook` (proxy); iterator hook | tested |
| `tool-args-schema-mismatch` | yes | yes | response hooks | unit |
| `reasoning-replay` (strip for Cerebras, rename) | yes | yes (request side) | `async_pre_call_deployment_hook` | unit |
| `reasoning-normalize` | yes | not needed: LiteLLM already maps the reasoning field | n/a | n/a |
| `error-classify` (Z.ai business code to 402/429/503 + `Retry-After`) | yes, exact status and headers | status mapped on the way out of the LiteLLM proxy; **LiteLLM's own Router has already decided retry/fallback by then** (it retries 429 whatever the Z.ai code) | `async_post_call_failure_hook` | tested for 1113 to 402 |
| `error-sanitize` | yes | no (LiteLLM wraps upstream errors itself) | n/a | n/a |
| web search | no | LiteLLM's own `websearch_interception` for `zai`, enabled by config, nothing reimplemented | config only (`examples/litellm/config.websearch.yaml`) | config loads (tested); live search not verified |
| `/v1/messages` (Anthropic clients) | issue 07 | the inner chat call is rewritten, so the request-side fixes apply to `zai/` models reached through `/v1/messages`; the outer `anthropic_messages` call is skipped on purpose | `call_type` guard | request side observed in a probe, not asserted in a test |
| spend and loop guard | issue 09 | not in the plugin | n/a | n/a |

Costs and limits to know:

- Streamed tool calls are buffered per index and released once, repaired, before the finish chunk (the same trade-off
  as the standalone proxy). Text and reasoning still stream live.
- Streaming repair and the stream retry need the LiteLLM **proxy** (`async_post_call_streaming_iterator_hook` is
  proxy only); a bare `litellm.acompletion(stream=True)` call from Python gets the request-side fixes only.
- The retry is one extra non-stream call to the same deployment, issued by the plugin with the deployment's
  `api_base` / `api_key` held in memory only. LiteLLM's usage accounting sees it as a second call.
- LiteLLM's `zai` provider cannot pass `thinking` or `reasoning_effort` as top-level parameters (the OpenAI SDK
  call raises an unexpected-keyword error, and `reasoning_effort` raises `UnsupportedParamsError`). The plugin sends
  both through `extra_body`, so with the plugin an OpenAI-SDK client can send them unchanged.
- Hook signatures change between LiteLLM releases; this is why the version is pinned and the integration tests run in
  a separate CI job (`uv run pytest -m litellm`).

## Does LiteLLM route `zai/` through its Rust core?

No, not in 1.105.5. `litellm/rust_bridge/catalog.py` lists the routes: chat completions are `PYTHON_ONLY` for every
provider, `/v1/messages` is Rust opt-in (`LITELLM_RUST=1`) only for provider `anthropic`, everything else
(responses, embeddings, token counting) is `PYTHON_ONLY`. A `zai/glm-5.3` call therefore always takes the Python
path, with or without `LITELLM_RUST=1`, and every hook fires. The integration tests run both ways and pass
identically. One difference was observed on the `anthropic/glm-5.3` + Z.ai Anthropic endpoint combination (issue 07's
path): with `LITELLM_RUST=1` the Rust path skips `async_pre_request_hook`; `async_pre_call_deployment_hook` and
`async_post_call_success_deployment_hook` still fire. Re-check this list when upgrading LiteLLM: the catalog is the
rollout policy and it is moving.

## Running the tests

```sh
uv run pytest                      # default: unit tests only, offline, fast
uv run pytest -m litellm           # real LiteLLM proxy (pinned), a few minutes, needs uv + network for the first wheel set
uv run python tools/litellm-proof/proof.py --out results/litellm-demo   # direct vs via LiteLLM, writes the Bridge session
```

LiteLLM pins `openai<3` while the dev group needs `openai>=3`, so they cannot share an environment (`[tool.uv]
conflicts` in `pyproject.toml`). The tests start the proxy with `uv run --isolated --no-dev --extra litellm --frozen
litellm ...` and talk to it over HTTP.
