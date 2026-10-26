# 02 - glm-bridge: competitor research and architecture recommendation

Date: 2026-10-26. Method: shallow clones of each repo read locally (file/line refs below are against the default-branch HEAD I cloned today), `gh repo view` for stars/license/last push, vendor docs fetched via r.jina.ai / raw `.md`. Claims not backed by something I read are marked UNVERIFIED.

Caveat on the brief itself: the Z.ai docs I read (https://docs.z.ai/guides/capabilities/thinking-mode) say GLM-5.3 and GLM-5.3-FLASH "use forced thinking and cannot be disabled", which is consistent with the `thinking: disabled` fix, but they do not mention code 1210 for that case, nor reasoning-effort values, nor the 1.4x / 2.4x tokenizer ratios, nor the Cerebras reasoning_content rejection. I could not confirm those from docs; they stay as "given by the brief" and need probes in Component 1 (the test harness).

---

## 1. Competitor table

| Project | Lang / arch | Deploy | Ext. mechanism | License | Stars | Last commit | GLM relevance |
|---|---|---|---|---|---|---|---|
| BerriAI/litellm | Python SDK + FastAPI proxy; "Rust core with Python SDK" (repo description); `litellm/rust_bridge` picks Rust or Python per call and falls back to Python (`rust_bridge/AGENTS.md`) | pip, Docker, Helm | `CustomLogger` callbacks (pre/post/stream/failure hooks), `CustomLLM` custom providers via `custom_provider_map`, guardrail hooks, WebSearchInterception logger | MIT, except `enterprise/` dir (`LICENSE` lines 1-5) | 62,066 | 2026-10-24 | `zai` provider exists, thin (see sec. 2) |
| Portkey-AI/gateway | TypeScript (Hono), Node/Workers | npm, Docker, Cloudflare | `plugins/` guardrails with `beforeRequestHook`/`afterRequestHook`, can return `transformedData` (`src/middlewares/hooks/index.ts` ~298-353) | MIT | 13,274 | 2026-05-25 | `zhipu` provider = open.bigmodel.cn, default model `glm-3-turbo`, only max_tokens/temperature/top_p/stream mapped (`src/providers/zhipu/chatComplete.ts` 1-60). `z-ai` provider = api.z.ai base URL + chat endpoint only (`src/providers/z-ai/api.ts`, 18 lines, no chatComplete.ts) |
| Kong/kong (AI Gateway) | Lua (OpenResty) | binary, Docker, K8s | Lua/Go/Python/JS plugins; AI plugins `ai-proxy`, `ai-request-transformer`, `ai-response-transformer`, `ai-prompt-*` (listed via GitHub contents API) | Apache-2.0 | 44,302 | 2026-10-19 | `kong/llm/drivers` = anthropic, azure, bedrock, cohere, gemini, huggingface, llama2, mistral, openai (no GLM driver). OpenAI driver honours `model.options.upstream_url` (openai.lua 134, 201-212), so GLM can be reached as "openai-compatible", with zero GLM fixes |
| envoyproxy/ai-gateway (README now says "Agent Router") | Go; Envoy ext_proc + translators | K8s / Helm, Envoy Gateway | `internal/translator` schema translators (anthropic<->openai, bedrock, gcp...), `bodymutator`, `headermutator`, `extensionserver` | Apache-2.0 | 2,292 | 2026-10-26 | No zhipu/zai/glm code (grep over repo returned only go.sum + test cassettes). Has Anthropic<->OpenAI translators (`anthropic_openai.go`) that are the closest "reference implementation" in Go |
| Helicone/ai-gateway | Rust | binary, Docker | config-driven router; provider endpoints dir lists anthropic, bedrock, google, ollama, openai | GPL-3.0 | 640 | 2025-11-21 (stale) | No GLM provider seen in `endpoints/`. GPL-3.0 makes it unusable as a dependency for a permissive kit. (The Helicone/helicone observability repo, Apache-2.0, 6,238 stars, is not a translation gateway; I did not read it.) |
| musistudio/claude-code-router (CCR) | TypeScript monorepo; HEAD (2026-10-10) is a rewrite: `packages/core` depends on `@the-next-ai/ai-gateway` (npm; repo the-next-ai/ai-gateway, MIT, 10 stars) and registers request/response/stream hooks by key (`gateway/core-runtime/router-plugin-contract.ts`) | npm global, Docker, Electron desktop | v1 "transformers" now live in the separate `musistudio/llms` repo (`src/types/transformer.ts`: `transformRequestIn/Out`, `transformResponseIn/Out`, `auth`, `endPoint`). Transformers chained per provider/model in `config.json` `transformer.use` | MIT (CCR). `musistudio/llms`: no license file (GitHub `licenseInfo: null`) | 37,860 (CCR); 308 (llms) | 2026-10-10 (CCR); 2026-01-07 (llms) | No GLM-specific code in HEAD (grep over CCR: only docs/models.json hits for `glm-5.1` via aihubmix). Generic fixes: see sec. 2 |
| dejay2/glmproxy | Node.js, Express-style server, vanilla-JS dashboard, 10.7k LOC | npm CLI (`ccglm`) | MCP registry (dashboard-managed) | MIT | 19 | 2025-12-22 | Targets GLM-4.7. Ignores client `thinking` ("Client sent thinking parameter (ignored)", `src/server.js` 503-506), injects a "ultrathink ... `<reasoning_content>`" prompt instead (`src/reasoning/injector.js`); intercepts Claude Code WebSearch/WebFetch via Z.ai MCP |
| farion1231/cc-switch | Rust (Tauri) desktop app with embedded proxy `src-tauri/src/proxy/` | desktop installer | provider presets, "rectifiers" (internal modules) | MIT | 148,902 | 2026-10-26 | Generic Anthropic-oriented rectifiers: `thinking_rectifier.rs` (strip signatures + retry on signature errors), `thinking_budget_rectifier.rs` (clamp budget_tokens/max_tokens), `error_mapper.rs`, `reasoning_bridge.rs`, `failover_switch.rs`, `circuit_breaker.rs`. GLM mentions are only model-mapper presets and a "GLM native Responses gateway does not know hosted web_search" strip (`forwarder.rs` ~7295). A useful pattern source, not a GLM fix list |
| godkey1314/kdx-anthropic-bridge | Go, ~10 files, transparent proxy | Docker | none (hard-coded) | MIT | 3 | 2026-07-13 | Fronts iFlytek's Anthropic endpoint that relays GLM. Rewrites `thinking.type=adaptive` -> `enabled` (`internal/proxy/rewriter.go` 54-65). Intercepts the model's plain `web_search` tool_use in the SSE stream and rewrites it into `server_tool_use` + `web_search_tool_result` blocks at the same index, searching via Google (`internal/proxy/stream_filter.go` 1-30, 165-232). Retries 502/503/429 up to 10x (README) |
| akirose/glm-proxy | Python FastAPI, single `server.py` (690 lines) | Docker | none | MIT | 10 | 2025-11-01 | Parses GLM-4.x XML `<tool_call>` text into OpenAI `tool_calls` (`server.py` 164-278, 481-491, 547-559). Obsolete for GLM-5.x if those emit native tool_calls (UNVERIFIED) |
| dabstractor/ccr-glm-config | JSON + 2 CCR plugin JS files | copy into CCR | CCR transformers `z-ai-websearch.js` (Z.ai MCP search via a local dummy server), `z-ai-vision.js`; uses stock `reasoning`, `enhancetool` | none declared | 68 | 2025-12-23 | Config recipe for CCR v1, GLM-4.6 era |
| jedarden/zai-proxy | Go reverse proxy + dashboard | Docker | none | MIT | 9 | 2026-10-24 | Observability only: EWMA ceiling estimator on upstream 429s, Prometheus, tiktoken fallback counts (README). No protocol repair |
| allrouter369-droid/glm-failover | Python `proxy.py` | script | config.yaml | MIT | 0 | 2026-06-30 | "429 auto failover" only (repo description) |
| eequaled/GLMproxy | Node, many lib modules (governor, queue, vault, identity) | npm | none | MIT | 39 | 2026-10-17 | Proxy for the AutoClaw/Zhipu consumer app path, has a `BAN-RISK-AND-LIMITS.md`; out of scope (non-API access) |
| Codex-oriented proxies: JichinX/codex-glm-proxy (21), KevinSHH/codex-glm-proxy (2), lucianolixin/codex-glm-proxy (2), erubescent/codex-zai-proxy (2) | Python/Node | script | none | mostly MIT | tiny | 2026 | OpenAI Responses -> GLM chat. Different client protocol; not read in depth |
| Xiang-CH/zhipu-ai-provider (Vercel AI SDK community provider) | TypeScript | npm `zhipu-ai-provider` | AI SDK provider interface | Apache-2.0 | 20 | 2026-07-27 | Listed at https://ai-sdk.dev/providers/community-providers/zhipu; docs show only `glm-4-plus` example, default base URL open.bigmodel.cn. I did not read its source, so I cannot say whether it handles reasoning_content or thinking. No official Vercel Z.ai provider found |

Other GitHub searches ("glm proxy", "zai proxy", "glm claude code", "glm anthropic", "ccr glm", "glm-5") surfaced only the repos above plus many 0-3 star setup scripts. No project found implementing business-code mapping, tokenizer correction, schema flattening or the Cerebras/Z.ai reasoning replay split.

---

## 2. Who already does each fix (matrix)

Legend: YES = code read, partial/generic = adjacent behaviour that is not GLM-aware, NO = looked and not found, ? = not checked.

| Fix | LiteLLM | Portkey | Kong | Envoy AIGW | CCR (llms v1 transformers) | cc-switch | kdx bridge | glmproxy | Evidence / notes |
|---|---|---|---|---|---|---|---|---|---|
| 1. `thinking: disabled` -> enabled + low effort for GLM-5.3 | NO | NO | NO | NO | Opposite: `reasoning` transformer sets `thinking.type="disabled"` when its `enable` option is false (`llms/src/transformer/reasoning.transformer.ts` 15-27) | NO | Related only: adaptive -> enabled (`rewriter.go` 54-65) | NO (ignores client thinking) | LiteLLM `ZAIChatConfig.get_supported_openai_params` (zai/chat/transformation.py 43-63) lists `thinking` only if `supports_reasoning`, and passes it through unchanged |
| 2. Clamp reasoning effort to low/high/max | NO: `reasoning_effort` is not in the zai supported-params list (same lines 44-53, 57-61); cerebras adds it (cerebras/chat.py 76-78) | NO | NO | NO | partial: `getThinkLevel(budget_tokens)` buckets budgets to effort (anthropic.transformer.ts 191-195) | partial: per-model effort level tables for Zen/Codex (`providers/codex.rs` ~607) | NO | NO | OpenRouter documents the unified vocabulary (none/minimal/low/medium/high/xhigh/max) and `supported_efforts` per model: https://openrouter.ai/docs/use-cases/reasoning-tokens . That is the model to copy for the clamp table |
| 3. Repair malformed tool-call args (string vs object, array input) + retry with parse error | NO (grep for repair finds only request-body surrogate repair in `proxy/common_utils/http_parsing_utils.py`) | NO | NO | NO | YES, repair but no retry: `enhancetool` + `utils/toolArgumentsParser.ts` (JSON -> JSON5 -> `jsonrepair` -> `"{}"`), streaming-aware | NO | NO | NO | CCR's fallback to `"{}"` silently drops the call's data; no one feeds the parse error back to the model. Gap = our differentiator |
| 4. `reasoning_content` replay per upstream | partial: maps inbound `reasoning` -> `reasoning_content` on deltas (`llms/openai/chat/gpt_transformation.py` 917-933). No Z.ai `clear_thinking` support, no Cerebras-specific strip found | NO | NO | NO | partial: cerebras transformer deletes `reasoning` (`cerebras.transformer.ts` 19-23); anthropic transformer maps thinking+signature to a `thinking` field (anthropic.transformer.ts 163-169) | partial: `reasoning_bridge.rs` (multi-form reasoning extraction, handlers.rs 2865-3097) | NO | NO (prompt hack) | Z.ai requirement (docs): return complete unmodified `reasoning_content`; set `clear_thinking:false` for preserved thinking on the standard endpoint (default on Coding Plan endpoint); consecutive blocks must match original order. https://docs.z.ai/guides/capabilities/thinking-mode |
| 5. Inject usage into Anthropic `message_start` | NO evidence (LiteLLM has `STREAM_COST_INJECTION` feature flag in messages route, `messages/dispatch.py`, unrelated) | n/a | n/a | partial: `translator/anthropic_usage.go` exists (not read) | ? | ? | NO | NO | Anthropic's own streams carry `usage.input_tokens` in `message_start` (docs example, streaming.md line 536) |
| 6. Tokenizer estimate correction | NO (count_tokens handler for Anthropic only: `llms/anthropic/count_tokens/`) | NO | NO | partial: `counttokens_*.go` translators | NO | NO | NO | NO | jedarden/zai-proxy uses tiktoken fallback (README). Z.ai exposes a Tokenizer endpoint (nav link https://docs.z.ai/api-reference/tools/tokenizer) usable for calibration |
| 7. Flatten `oneOf` / empty-properties tool schemas | NO for GLM. Precedent: cc-switch has `gemini_schema.rs`, `transform_codex_chat_moonshot_schema.rs`; Envoy has `jsonschema_helper.go` (not read in detail) | NO | NO | partial | NO | partial (other vendors) | NO | NO | Pattern exists for Gemini/Moonshot, none for GLM |
| 8. Emulate Claude Code server-side `web_search` | YES but Bedrock-only by default: `WebSearchInterceptionLogger` (`integrations/websearch_interception/handler.py` 249-284, default `enabled_providers=[bedrock]`), converts `web_search_20250305` to `litellm_web_search`, runs an agentic loop with Perplexity/Tavily etc. (ARCHITECTURE.md) | NO | NO | NO | partial: plugin `z-ai-websearch.js` (ccr-glm-config) | NO | YES (stream rewrite to `server_tool_use` + `web_search_tool_result`, Google backend) | YES (Z.ai MCP search, intercepts WebSearch/WebFetch) | LiteLLM can be pointed at zai by config (`enabled_providers: ["zai"]`, handler.py 540-567 shows config parsing); not tested by me |
| 9. Map Z.ai business codes -> HTTP semantics + Retry-After | NO (grep for the codes found nothing relevant) | NO | NO | NO | NO | partial: generic `error_mapper.rs`, `circuit_breaker.rs` | partial: retries 502/503/429 on fixed interval | NO | Z.ai docs table (https://docs.z.ai/api-reference/api-code): 1113, 1302, 1305, 1308, 1309, 1310, 1311, 1313-1318 are ALL HTTP 429; 1210 = 400; 1261 = 400 prompt too long; 1234 = 500. So "correct HTTP semantics" must be derived from the code, not the status: see sec. 5 |
| 10. Scrub `cache_control` etc. | YES (keeps cache_control for zai: zai/chat/transformation.py 30-41) | ? | ? | ? | ? | ? | ? | ? | The one GLM-specific behaviour LiteLLM's zai config has |

Bottom line: across everything read, no project implements fixes 1, 2, 5, 6, 7, 9 for GLM; fix 3 exists only as lossy repair (CCR); fix 4 only in generic or cross-vendor form; fix 8 exists in four independent forms (LiteLLM, kdx, glmproxy, ccr plugin).

---

## 3. LiteLLM deep dive (the likely host)

### 3.1 What its zai provider is
`litellm/llms/zai/chat/transformation.py` (63 lines): `ZAIChatConfig(OpenAIGPTConfig)`, base URL `https://api.z.ai/api/paas/v4` (line 8), overrides `remove_cache_control_flag_from_messages_and_tools` (30-41) and `get_supported_openai_params` (43-63; max_tokens, stream, stream_options, temperature, top_p, stop, tools, tool_choice, plus `thinking` if `supports_reasoning`). It is registered in `litellm/utils.py:9140` and `get_llm_provider_logic.py:790`. Model catalog (`model_prices_and_context_window_backup.json`) already lists `zai/glm-5.3`, `zai/glm-5.3-flash`, 5.2, 5.1, 5, 5-code, 4.7... with `supports_reasoning: true`. Note: no `glm-5.3-flashx` entry found in the grep, and no `parallel_tool_calls`, `response_format` or `reasoning_effort` in supported params. All upstream quirks fall through to the generic OpenAI path.

### 3.2 How `/v1/messages` (Anthropic) reaches Z.ai
`litellm/messages/dispatch.py` classifies each Messages call as `Target.MESSAGES` (native Anthropic passthrough), `Target.RESPONSES` or `Target.CHAT_COMPLETIONS` (`_target`, ~lines 88-99). `_native_messages_config` (`llms/anthropic/pass_through/messages/handler.py` 205-225) only returns a native config for providers that implement `get_provider_anthropic_messages_config`, or deployments flagged as Anthropic passthrough. Two ways to run GLM through it:
- `zai/glm-5.3`: Anthropic request is translated to chat-completions (paas/v4) and back. The thinking/signature translation is LiteLLM's generic adapter; for GLM this is where reasoning_content <-> thinking blocks, tool_use <-> tool_calls conversion happens. I did not trace the adapter end to end.
- `anthropic/glm-5.3` with `api_base=https://api.z.ai/api/anthropic` (the endpoint Z.ai documents for Claude Code, https://docs.z.ai/devpack/tool/claude): near-pure passthrough, so Z.ai's own Anthropic-compat quirks (usage 0 in message_start, etc., per the brief) arrive untouched.

Risk: LiteLLM now routes some calls to a Rust core; `rust_bridge/AGENTS.md` says "a route's sync and async dispatches read different detector maps ... Messages runs pre-request hooks, web search interception and interceptors only on async calls". Hooks therefore work on the async path (proxy uses it) but a plugin author must test sync vs async and with the Rust path enabled, because routing "falls back to Python" only when a detector says a Python-only feature is in play (`dispatch.py` `_ASYNC_FEATURES` includes `PRE_REQUEST_HOOK`).

### 3.3 Hook inventory (all in `litellm/integrations/custom_logger.py`)
| Hook (line) | Fires | Can mutate | Use for |
|---|---|---|---|
| `async_pre_call_hook` (417) | proxy only, before routing, receives `data`, `call_type` incl. `"anthropic_messages"` | request dict (return dict) or reject | fixes 1, 2, 7 at the edge, before deployment pick; but does not know which upstream (Z.ai vs Cerebras) was chosen |
| `async_pre_call_deployment_hook` (280) | after deployment selected, before send | kwargs | per-upstream fixes: 1, 2, 4 (strip/keep `reasoning_content`, add `clear_thinking`), 7 |
| `async_pre_request_hook` (165) | in the Messages passthrough, before request | kwargs (tools, stream, ...) | fix 8 tool conversion; fix 1/2 on `/v1/messages` |
| `async_post_call_success_deployment_hook` (297) | after upstream response, per deployment attempt | response object | fixes 3 (non-stream), 5, 6 |
| `async_post_call_streaming_deployment_hook` (355) | per stream chunk | chunk | fixes 3 and 5 for streams (stateful per request needed) |
| `async_post_call_streaming_iterator_hook` (526) | wraps whole stream, proxy | async generator | best place for stateful SSE rewriting (message_start usage injection, tool-arg buffering) |
| `async_post_call_failure_hook` (474) | proxy, after failure | return/raise `HTTPException` | fix 9: turn Z.ai 429-with-business-code into 402/503/429 + `Retry-After` header |
| `async_post_call_failure_deployment_hook` (307) | per failed attempt | none (observe only; exception is a snapshot) | metrics / circuit breaker |
| `async_post_call_response_headers_hook` (446) | proxy | headers | Retry-After on successes/errors |
| `async_should_run_chat_completion_agentic_loop` / `async_run_chat_completion_agentic_loop` / `async_build_chat_completion_agentic_loop_plan` (796-843) | after response, can re-issue calls | response | the only hook family designed for "inspect response, then call the model again": fix 3's retry-with-parse-error and fix 8's search loop |
| `CustomLLM` provider (`llms/custom_llm.py` 42+; registered via `litellm.custom_provider_map`, `__init__.py:1568`) | replaces the whole provider call | everything | full control: wrap `zai/` calls, own retries, own streaming |

### 3.4 How each fix would be built as a LiteLLM plugin
1. thinking disabled -> enabled+low: `async_pre_call_deployment_hook` (and `async_pre_request_hook` for `/v1/messages`) when model matches `glm-5.3*`. Cheap, stateless.
2. effort clamp: same hooks; table-driven per model (needs the probe data from Component 1). Also needs `reasoning_effort` added to supported params, because `ZAIChatConfig` drops it (transformation.py 44-53). Plugin must either subclass/patch `ZAIChatConfig` or pass via `extra_body`.
3. tool-arg repair + retry: non-stream fixable in `async_post_call_success_deployment_hook`; the retry-with-error needs the agentic-loop hook family (response is inspected, then the model is re-called with an error tool_result) or a `CustomLLM` wrapper. Streams: tool-arg deltas must be buffered per index in the streaming-iterator hook, which breaks streaming latency for tool calls (same trade-off CCR's `enhancetool` makes).
4. reasoning replay: message-level rewrite in `async_pre_call_deployment_hook` keyed by the resolved upstream (needs `litellm_params.api_base` / `custom_llm_provider`), plus `clear_thinking:false` injection.
5. message_start usage: `async_post_call_streaming_iterator_hook` on the Anthropic-format stream; requires a token estimate before the upstream has reported usage, i.e. fix 6.
6. tokenizer: LiteLLM has no token-counter extension point per provider that I found for zai; implement as a helper in the plugin used by 5 and by a `/v1/messages/count_tokens` override.
7. schema flatten: pre-call hooks on `tools`.
8. web_search: already supported by `WebSearchInterceptionLogger`; plugin config `enabled_providers: ["zai"]` plus a search backend. Only gap: verify it with GLM's `tool_use` shape.
9. errors: `async_post_call_failure_hook` returning `HTTPException`; but LiteLLM's exception mapping decides retry/fallback before this, so for correct router retry behaviour you also want the code mapped inside the provider error path (a `ZAIChatConfig.get_error_class` override), otherwise Router treats all as generic 429. (Not verified by reading exception mapping in full.)

Limits of the plugin route: (a) hook signatures have changed repeatedly (new deployment hooks, rust bridge) so we would pin and test against LiteLLM versions; (b) a hook cannot easily change the HTTP status returned for streaming errors already started; (c) coverage ends at users who run LiteLLM. Docs for operators: https://docs.litellm.ai/docs/proxy/call_hooks and https://docs.litellm.ai/docs/providers/custom_llm_server (not re-fetched; the source above is authoritative).

---

## 4. Other approaches worth stealing

- OpenRouter (https://openrouter.ai/docs/use-cases/reasoning-tokens): one unified `reasoning` object (`effort` none..max, `max_tokens`, `enabled`, `exclude`); response has `reasoning` string plus `reasoning_details[]` (types in a typed array; streaming under `choices[].delta.reasoning_details`); replay by passing `message.reasoning` or `message.reasoning_details` back, `reasoning_content` accepted as alias; per-model `supported_efforts`, `default_effort`, `mandatory` (do not send `none` when the model rejects it - exactly the GLM-5.3 case) from `GET /api/v1/models`. Messages-API mapping table: `thinking.enabled+budget_tokens` -> `reasoning.max_tokens`; `adaptive` -> `enabled:true`; `disabled` -> `enabled:false`; `output_config.effort` wins over adaptive. Error docs (https://openrouter.ai/docs/api-reference/errors): response status equals `error.code`; typed error vocabulary; `Retry-After` on 429/503. I could not fetch an OpenRouter tool-calling page (404 at the URL I tried).
- Vercel AI SDK: no first-party Z.ai provider found; only the community `zhipu-ai-provider` (20 stars). GLM through AI SDK realistically uses `@ai-sdk/openai-compatible`, which means a bridge URL swap is the integration point.
- cc-switch "rectifier" pattern: detect a specific upstream error string, mutate the request once, retry once (thinking_rectifier.rs, thinking_budget_rectifier.rs). Directly reusable for fix 3 and for unknown 1210 variants.
- kdx bridge pattern: buffer one SSE content block, rewrite, flush with preserved `index` (stream_filter.go). Reusable for web_search emulation and tool-arg repair.
- CCR llms: transformer interface with `transformRequestIn/Out`, `transformResponseOut` chained per provider/model in config. Good config ergonomics; weak typing (JS `any`), and v1 is stale (last commit 2026-01-07, no license) while CCR HEAD moved to a closed-ish `@the-next-ai/ai-gateway` core.

---

## 5. Recommendation: (c) core library + adapters

Choose (c): a framework-neutral Python core, with two thin adapters (LiteLLM plugin and standalone ASGI proxy).

Evidence:
1. LiteLLM is the dominant host (62k stars, pushed 2026-10-24) and already has the extension surfaces we need (sec. 3.3), a WebSearch interception feature we can reuse (fix 8) and a model catalog with GLM-5.3. So a plugin gets distribution for free. But it covers only fixes that fit hooks; fix 3 (retry-with-parse-error), the stateful SSE fixes (3, 5) and error-status control (9) are awkward or version-fragile as hooks.
2. LiteLLM is not the only stack: the brief's targets include Claude Code, OpenAI SDK and Anthropic SDK "on any host". Competitors outside LiteLLM have no GLM fixes at all (Portkey: bigmodel base URL + 4 params; Kong: no driver; Envoy: no GLM; Helicone: GPL and stale). A standalone proxy is what the Claude Code crowd actually runs today (CCR 37.9k stars, cc-switch 148.9k stars, kdx, glmproxy), and those need a drop-in URL.
3. The fixes are pure functions over JSON bodies and SSE event streams (request rewriters, response/stream rewriters, an error classifier, a tokenizer estimator). They can live in one core with no web-framework dependency, tested once against recorded Z.ai/Cerebras fixtures from Component 1.
4. (a) alone fails fix 9 and some of fix 3 reliably; (b) alone ignores LiteLLM users and duplicates routing/auth/budget that they already have. Writing only (b) also forgoes the lowest-cost distribution channel.

Suggested shape:
- `glm_bridge.core`: `RequestRewriter` chain (thinking, effort clamp, schema flatten, reasoning replay by upstream profile), `ResponseRewriter` chain, `StreamRewriter` (async generator over parsed SSE events, per-index buffers), `ErrorClassifier` (code -> status, retryable, `retry_after`), `TokenEstimator`, `UpstreamProfile` (zai, zai-coding-plan, cerebras, bigmodel...) as data, plus a `RetryPolicy` interface that lets the host re-issue a call with an injected parse error.
- `glm_bridge.asgi`: Starlette/FastAPI app exposing `/v1/chat/completions`, `/v1/messages`, `/v1/messages/count_tokens`, `/healthz`; httpx upstream; uses the core directly; this is where the full retry loop and status/Retry-After control lives.
- `glm_bridge.litellm`: `CustomLogger` subclass wiring the same core into `async_pre_call_deployment_hook`, `async_pre_request_hook`, `async_post_call_success_deployment_hook`, `async_post_call_streaming_iterator_hook`, `async_post_call_failure_hook`, and an optional `CustomLLM` provider for the retry-requiring path; config sets `WebSearchInterceptionLogger(enabled_providers=["zai"])`.
- Optionally a Go/TS rewrite later only if latency demands (kdx and cc-switch show Go/Rust proxies are viable); start with Python since LiteLLM hosts it.

Open verification items (do before building): confirm 1210-on-disabled, effort enum, reasoning_content rejection by Cerebras, usage=0 in `message_start`, tokenizer ratios, and `{}` args by direct probes; confirm LiteLLM hooks fire on the Rust-core path for `/v1/messages` with `zai/`.

### Error-code proposal (derived from Z.ai doc table; semantics are my inference)
| Z.ai code (docs: HTTP 429 unless noted) | Meaning in docs | Proposed client status | Retry-After |
|---|---|---|---|
| 1302 | rate limit reached | 429 | short (e.g. 1-5 s with jitter) |
| 1305 | overloaded, try later | 503 (Anthropic: 529 `overloaded_error`) | 5-30 s |
| 1308, 1316-1318 | usage window limit; message carries `next_flush_time` | 429 | seconds until `next_flush_time` (parse from message) |
| 1310 | weekly/monthly exhausted | 429, non-retryable by SDK | until `next_flush_time` |
| 1313 | fair-usage throttling | 429/403, non-retryable | none |
| 1113 (also 1309, 1311, 1314, 1315) | no balance / package expired / plan lacks model | 402 (or 403) | none |
| 1210 (400), 1261 (400 prompt too long) | invalid param / too long | 400 (`invalid_request_error`), 1261 maybe 413 | none |
Anthropic SDK retry rules for reference: 429, 5xx, 529 retried with backoff honouring `retry-after` (https://docs.claude.com/en/api/errors); OpenRouter 402 is not auto-retried by SDKs (https://openrouter.ai/docs/api-reference/errors).

---

## 6. API shapes the bridge must translate

Sources: Anthropic docs fetched as markdown (https://platform.claude.com/docs/en/build-with-claude/streaming.md, .../thinking.md, .../agents-and-tools/tool-use/handle-tool-calls.md, .../implement-tool-use.md, .../web-search-tool.md; also https://docs.claude.com/en/api/messages, /en/api/errors). OpenAI shapes from the OpenAPI spec https://github.com/openai/openai-openapi (`manual_spec/openapi.yaml`; note it is a stale snapshot: `ReasoningEffort` enum there is only low/medium/high at line 32736, and it has NO `reasoning_content` field anywhere, so `reasoning_content` is a vendor extension that Z.ai/DeepSeek-style APIs add). Z.ai: https://docs.z.ai/guides/capabilities/thinking-mode, /stream-tool, /api-reference/api-code, /devpack/tool/claude.

### 6.1 Anthropic Messages (client side of Claude Code / Anthropic SDK)
Request: `model`, `max_tokens`, `messages[]` (`role` user|assistant, `content` string or blocks), `system`, `tools[]` (each `name`, `description`, `input_schema`; server tools have `type` like `web_search_20250305` plus `name`, `max_uses`), `tool_choice`, `stream`, `thinking` (`{type:"enabled", budget_tokens}` | `{type:"adaptive"}` | `{type:"disabled"}`; optional `display: "omitted"|"updates"`), `output_config.effort`.
Constraint worth encoding: `budget_tokens` must be < `max_tokens` except in interleaved mode (extended-thinking doc). Claude Code sends `adaptive` (kdx README).
Content blocks:
- `thinking`: `{type, thinking, signature}`; `redacted_thinking`: `{type, data}`. Doc rule: in the latest assistant message consecutive thinking blocks must be passed back unmodified (thinking.md ~952); signature is encrypted and verified by Anthropic (thinking.md 1156). A GLM upstream cannot validate Anthropic signatures, so the bridge must decide: store reasoning in `thinking` with a bridge-generated signature (opaque, e.g. HMAC or a session cache key) so Claude Code round-trips it, and map back to `reasoning_content` verbatim on replay.
- `tool_use`: `{type, id, name, input}` where `input` is always an OBJECT (streaming.md line 53). Array `input` is therefore invalid and must be repaired.
- `tool_result` (in a `user` message): `{type:"tool_result", tool_use_id, content, is_error?}`; must immediately follow the tool_use turn and come FIRST in the user content array with text after (handle-tool-calls.md 54-66).
- Server tools: `server_tool_use` `{id, name:"web_search", input:{query}}` then `web_search_tool_result` with `content[]` of `web_search_result` items (`url`, `title`, `encrypted_content`, `page_age`), or an error object, e.g. `max_uses_exceeded` (web-search-tool.md ~443-518). The emulation must produce these same two blocks.
Response (non-stream): `{id, type:"message", role:"assistant", content[], model, stop_reason (end_turn|max_tokens|stop_sequence|tool_use|pause_turn|refusal...), stop_sequence, usage:{input_tokens, output_tokens, cache_*}}`.
Streaming events (streaming.md 22-70, 535-560, 1170-1199): `message_start` (message with empty `content` and `usage.input_tokens`, `output_tokens:1`) -> per block `content_block_start{index, content_block}` -> `content_block_delta{index, delta}` with delta types `text_delta`, `input_json_delta{partial_json}`, `thinking_delta{thinking}`, `signature_delta{signature}` (emitted just before `content_block_stop`) -> `content_block_stop{index}` -> `message_delta{delta:{stop_reason,...}, usage}` -> `message_stop`; `ping` anywhere; `error` event e.g. `overloaded_error` (HTTP 529 non-stream); unknown events must be tolerated. With `display:"omitted"` the thinking block still emits an empty `thinking_delta` and one `signature_delta`.
Errors: `{type:"error", error:{type, message}, request_id}`; types incl. `rate_limit_error` (429), `api_error` (500), `overloaded_error` (529); `request-id` response header (errors.md 26-67).
Token counting: `POST /v1/messages/count_tokens` takes the same body, returns `{input_tokens}` (token-counting doc).

### 6.2 OpenAI Chat Completions (what Z.ai paas/v4 and Cerebras speak)
Request: `model`, `messages[]` roles developer|system|user|assistant|tool, assistant `tool_calls[]` `{id, type:"function", function:{name, arguments:<JSON STRING>}}`, tool message `{role:"tool", tool_call_id, content}` (spec `ChatCompletionRequestToolMessage` line 18184), `tools[]` `{type:"function", function:{name, description, parameters}}`, `tool_choice`, `stream`, `stream_options.include_usage` (spec line 18394: extra chunk before `[DONE]` carrying `usage`), `reasoning_effort` (spec enum low|medium|high only). Z.ai additions: `thinking:{type:"enabled"|"disabled", clear_thinking:bool}`, `tool_stream:true` (stream tool-call args; docs stream-tool), assistant `reasoning_content` string on messages for replay.
Response: `choices[].message{content, tool_calls, reasoning_content (vendor)}`, `finish_reason` (stop|length|tool_calls|content_filter), `usage{prompt_tokens, completion_tokens, total_tokens, ...details}`.
Streaming: `data: {choices:[{index, delta:{role?, content?, reasoning_content? (vendor), tool_calls:[{index, id?, type?, function:{name?, arguments:<fragment string>}}]}, finish_reason}]}` ... `data: [DONE]` (spec `ChatCompletionStreamResponseDelta` line 18416, `ChatCompletionMessageToolCallChunk` 17781; OpenAI itself warns `arguments` may be invalid JSON, line 17803-17807). Fragment accumulation is by `index`; Z.ai docs show concatenating `function.arguments` per index (stream-tool doc lines ~205-214).

### 6.3 Translation table (bridge responsibilities)
| Anthropic | OpenAI/Z.ai | Notes |
|---|---|---|
| `system` (string or blocks) | `system` message | cache_control kept for Z.ai (LiteLLM does the same) |
| `thinking.type` enabled/adaptive/disabled, `output_config.effort` | `thinking.type`, `reasoning_effort` (clamped) | disabled on GLM-5.3 -> enabled + low (brief); OpenRouter table is the precedent |
| assistant `thinking` block(s) | assistant `reasoning_content` (concatenate in original order; omit entirely for Cerebras) | `clear_thinking:false` when preserved thinking wanted |
| `tool_use.input` object | `tool_calls[].function.arguments` JSON string | reverse on responses; repair string/array/invalid |
| `tool_result` blocks (first in user message) | one `role:"tool"` message per result, in order | `is_error` has no OpenAI field: prefix content |
| `content_block_start/delta/stop` with `index` | `delta` fragments by `tool_calls[].index` | text and thinking blocks must be closed before the next block opens |
| `message_start.usage` | `stream_options.include_usage` final chunk | usage arrives late in OpenAI streams, so message_start needs an estimate (fix 5/6) |
| `stop_reason` end_turn/tool_use/max_tokens | `finish_reason` stop/tool_calls/length | |
| `error` event / 529 | HTTP status + Z.ai `error.code` | via ErrorClassifier |
| server tool `web_search` | model emits plain `tool_use` for a bridge-defined tool; bridge executes search and emits `server_tool_use` + `web_search_tool_result` | kdx and LiteLLM both do this |
