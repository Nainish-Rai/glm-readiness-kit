# 01 - glm-conformance research (competitors, GLM wire spec, failure catalog, fingerprinting)

Research date: 2026-10-26. Everything below was read from source, docs or issue threads this session unless marked UNVERIFIED. Stars and activity are from `gh repo view` on 2026-10-26.

---------------------------------------------------------------------------------------------

## 1. Competitors and prior art

### 1.1 Comparison table

| Project | Measures | Case format | Scoring | Runs as | Output | License | Stars / last push | Weaknesses we can beat |
|---|---|---|---|---|---|---|---|---|
| MoonshotAI/K2-Vendor-Verifier (K2VV) https://github.com/MoonshotAI/K2-Vendor-Verifier | (a) ToolCall-Trigger similarity: F1 of "finish_reason==tool_calls" vs the official API; (b) Schema accuracy: valid tool calls / tool_calls responses | JSONL, one raw chat-completions request body per line (messages, tools, temperature...). Special role `_input` is rewritten to `system`. ~4,000 requests; only 50% published (tarball statics.moonshot.cn/k2vv/tool-calls.tar.gz) | F1 vs official-vendor results (no live reference at score time); jsonschema `validate()` of parsed `function.arguments` against the matching `tools[].function.parameters` | Single script `tool_calls_eval.py samples.jsonl --model --base-url --api-key --concurrency --extra-body --incremental`; streaming supported; optional raw `/completions` mode with a tokenizer chat template | `results.jsonl` (per request) + `summary.json` (finish_stop/tool_calls/others counts, schema_validation_error_count, usage, timings). F1 is NOT computed by this script; it is computed from two results files by a separate step (the README table is hand-assembled) | No LICENSE file (GitHub reports none) | 597 stars; last push 2026-02-14 (superseded) | Kimi-only; single-turn replays only; no streaming-vs-nonstream parity, no history/thinking-replay checks, no error-contract checks; "pass" = agreement with one vendor's sampled behaviour, so 73% F1 threshold is an empirical band (official ran 75.8-76 avg), not a spec; only half the dataset is public; unlicensed; `finish_reason` only, ignores tool-name validity |
| MoonshotAI/Kimi-Vendor-Verifier (KVV, the successor) https://github.com/MoonshotAI/Kimi-Vendor-Verifier ; blog https://www.kimi.com/blog/kimi-vendor-verifier.html | Pre-flight API contract (pytest), tool-call JSON-schema validity on "walle" MFJS schemas (stream and non-stream), K3 feature contract (dynamic tools, response_format, tool_choice, reasoning_effort), `usage.prompt_tokens` vs expected constant, plus accuracy benchmarks via inspect-ai (OCRBench, MMMU-Pro, AIME25), BEAM 1M memory, DeepSWE agentic | pytest suites + JSONL cases (`testdata/prompt_token_cases/cases.jsonl`: `{id, description, request, expected_prompt_tokens}`; vision cases use `fixture://` URLs materialised at run time) | Hard pass/fail for contract tests; benchmark scores compared to the Moonshot row in a leaderboard (e.g. K3 table OCRBench 0.89 / MMMU 0.82 / BEAM / DeepSWE per provider) | `uv run pytest tests/<suite> --base-url --api-key --smoke-model --think-mode kimi\|opensource\|none`; ~15 h full run on 2x 8xH20 per blog | JUnit XML + JSON report (`tool-call-schema-report.json` with by_status/by_mode summary); markdown leaderboard | MIT | 185 stars; last push 2026-09-17 | Best design to copy: separate cheap contract tests from expensive benchmarks. Weaknesses: Kimi-specific parameters (immutable temperature/top_p, `chat_template_kwargs`), no cross-harness view (Claude Code vs Codex vs OpenCode request shapes), vendor-run so leaderboard is partial and unaudited, no multi-protocol (Anthropic path) tests, no behavioural fingerprinting |
| FLock-io/Vendor-Verifier https://github.com/FLock-io/Vendor-Verifier | Same two metrics as K2VV, generalised to any OpenAI-compatible model (adds `--model-type generic\|kimi` adapters) | Same JSONL (`tool-calls/samples_cleaned.jsonl`) | `vendor_verifier/compare.py` computes TP/FP/FN/TN, precision/recall/F1 and schema accuracy between a baseline results file and a vendor results file | `python -m vendor_verifier file.jsonl --model --base-url` | results.jsonl, summary.json, comparison JSON | No license | 0 stars; created 2026-02-12, last push 2026-02-13 (16 commits) | Thin fork of K2VV; abandoned; same single-turn limits |
| Prism-Shadow/agent-provider-verifier https://github.com/Prism-Shadow/agent-provider-verifier ; dataset https://huggingface.co/datasets/PrismShadow/AgentVendorVerifier | Six metrics: F1 vs a designated baseline vendor, request success rate, schema accuracy, avg tokens, avg TTFT, TPS | K2VV dataset with fixes (JSONL, `verifier-dataset.jsonl`); `config.yaml` lists vendors per model group with exactly one `is_baseline: true` (fields: name, model_id, api_key, url, validator, is_baseline, provider for openrouter) | "Inverse Rank Fusion": rank per metric, contribution 1/(rank+5), summed (comparable only within one model group). Baseline F1 fixed at 1.0. Has GLM-4.7 rows: bigmodel baseline vs z.ai via OpenRouter 0.8575 F1 and schema acc 0.83-0.84 | `pip install agent-vendor-verifier`; `agent-vendor-verifier --test-file-path --config-file-path --vendor-concurrency --request-concurrency`; also ships an agent "skill" dir | `data/` and `rank/` directories; README tables | Apache-2.0 | 4 stars; last push 2026-03-12 | Rank-fusion hides absolute quality (best of a bad set still scores high) and mixes cost/speed into a correctness ranking; baseline is trusted by fiat; still the single-turn K2VV corpus; no CI of the dataset against GLM wire quirks; stale (no GLM-5.x rows) |
| redd34/canitoolcall https://github.com/redd34/canitoolcall (found via ollama#18659) | Offline conformance of inference-engine tool-call and reasoning PARSERS: 469 fixtures replayed through vLLM, SGLang, llama.cpp, Ollama, transformers; 8 checks (expected match, graceful errors, stream = non-stream, split invariance, no marker leakage, valid JSON, schema, call order); also `canitoolcall probe` against a live server (single, parallel, no-call, nested, unicode, empty args, forced tool_choice, follow-up after tool result, reasoning then call) | Fixture = raw model text + token ids + tools + expected parse, with provenance (official chat-template render, engine test suites, bug reports) | pass / soft pass / fail per engine, triage.jsonl with repro commands. Explicitly "not a ranking" | CLI (`uvx canitoolcall probe --base-url --model`), pytest plugin, nightly matrix site | Matrix site + results JSON | Apache-2.0 | 0 stars; created 2026-09-26, pushed 2026-10-13 | Engine-parser only (offline, no hosted providers, no GLM wire flags like `thinking`/`tool_stream`/`clear_thinking`, no model-behaviour tests). It is the closest neighbour: we should interoperate (reuse its GLM fixtures) rather than duplicate the parser layer |
| OpenRouter ":exacto" + Auto Exacto https://openrouter.ai/blog/announcements/provider-variance-introducing-exacto/ , https://openrouter.ai/docs/guides/routing/auto-exacto , https://openrouter.ai/docs/features/exacto-variant | (1) Production tool-call validity telemetry per endpoint: `InvalidJson` (JSON.parse of arguments throws), `UnknownName` (function.name not in request tools), `SchemaMismatch` (JSON Schema Draft 7 via @cfworker/json-schema); (2) tool-call propensity (how often tools are requested when offered); (3) benchmark harness: GPQA Diamond (temp 0.5, 10 epochs) and Tau2-Bench Airline (temp 0, 1 epoch) on pinned endpoints | Live traffic (no public cases); benchmarks are third-party | Error rate computed at REQUEST level (any bad call flags the request, denominator = requests finishing with tool calls). Derank threshold = median of endpoint scores over first ~21 days minus 2 sigma; rolling 32-day window; min 50 GPQA questions / 45 Tau2 tasks | Hosted routing feature (not runnable by us) | Metrics on model-page Performance tab; benchmark card per enrolled model | Proprietary service | n/a | Closed, not reproducible, no per-failure repro, only Draft-7 validation (misses 2019-09/2020-12 keywords, JS regex semantics), requests with absent or uncompilable schemas count as valid (conservative), no streaming-parity or thinking-contract checks. 3-failure-mode taxonomy (InvalidJson/UnknownName/SchemaMismatch) is worth adopting verbatim; the 2-sigma-below-median-of-peers threshold is a good "no reference model" scoring rule |
| Artificial Analysis Endpoint Accuracy Index v1.0 https://artificialanalysis.ai/methodology/endpoint-accuracy-index | Endpoint accuracy as % of a self-hosted reference on a 3-eval suite: BFCL v4-500 (33%, 3 repeats), HLE-250 (33%, 10 repeats), AA-LCR-25 (33%, 10 repeats). GLM-5.2 and GLM-5.3 are covered | Third-party benchmarks | Per-eval mean accuracy / reference accuracy, weighted blend, 95% CI per endpoint (two-sided Student-t, combined in quadrature), flag "within range" vs "significantly below" | Closed, point-in-time snapshots | Dated charts with notes | Proprietary | n/a | Reference = self-hosted SGLang. For GLM-5.3: 8x B200, SGLang 0.5.18, FP8 E4M3, DeepGEMM, MTP EAGLE 2 draft tokens, `kv-cache-dtype fp8_e4m3`, `--reasoning-parser glm45`, `--tool-call-parser glm47`, context 262144 (note: below the 1M API context). Weaknesses: only 500 BFCL tasks, no agent-loop/multi-turn harness shapes, no wire-contract tests, closed, no CI, snapshots not monitoring. Useful as scoring template: percent-of-reference with CIs |
| Berkeley BFCL (gorilla) https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard | Function-calling accuracy: simple, parallel, multiple, parallel_multiple, irrelevance (should NOT call), live_* (user-contributed), multi_turn_{base,miss_func,miss_param,long_context}, memory_*, web_search_*, format_sensitivity | `BFCL_v4_*.json` JSONL: `{id, question:[[{role,content}]], function:[{name,description,parameters:{type:"dict",...}}]}`; answers in `data/possible_answer/*.json`: `{id, ground_truth:[{fn:{param:[allowed values...]}}]}` (list of acceptable values, `""` = optional) | AST matching of parsed calls against allowed-value lists; execution-based for multi-turn state | `bfcl generate` + `bfcl evaluate` CLI, `--test-category` | scores CSV/leaderboard | Apache-2.0 (LICENSE file read) | 13,060 stars; pushed 2026-10-17 | Measures model skill, not endpoint fidelity; no wire/streaming/thinking tests; schema type `dict` instead of JSON Schema `object`. Use as inspiration for the answer-list format and as a vendored subset (as AA does) |
| vLLM tool-parser tests https://github.com/vllm-project/vllm/tree/main/tests/tool_parsers (`common_tests.py`, `test_glm4_moe_tool_parser.py`, `test_glm47_moe_tool_parser.py`) and `tests/tool_use/test_tool_calls.py` | Parser unit behaviour: no tool calls, single call, parallel calls, various data types, empty args, surrounding text, escaped strings, malformed input, streaming reconstruction; GLM-4.7 parser incl. namespace tools | pytest with a `ToolParserTestConfig` dataclass of expected parses | assert-based | pytest | pytest | Apache-2.0 | 95,596 stars; active | Engine-internal, requires the vLLM tree; shows the right category list for our parser-level cases |
| SGLang function_call tests https://github.com/sgl-project/sglang/tree/main/test/registered/function_call (only kimik2/kimik3 detector tests present at that path; GLM detectors in `python/sglang/srt/function_call/glm4_moe_detector.py`, `glm47_moe_detector.py`) | Streaming detectors | pytest | assert | pytest | pytest | Apache-2.0 (UNVERIFIED, not opened) | 38,200 stars | GLM detectors lack matching tests in that dir; sglang#35564 shows `parse_streaming_increment` char-by-char differs from `detect_and_parse` for glm/glm45/glm47 (int -> str) |
| llama.cpp tests https://github.com/ggml-org/llama.cpp/blob/master/tools/server/tests/unit/test_tool_call.py ; `tests/test-chat*.cpp`; docs/function-calling.md | Real-model tool tests: `test_completion_with_required_tool_real_model`, `test_completion_without_tool_call_{fast,slow}`, `test_weather`, `test_calc_result` (tool result round trip), `test_thoughts` (reasoning_format), `test_hello_world`, each parametrised over stream/non-stream | pytest with HF repos and template overrides | assert | pytest | pytest | MIT (UNVERIFIED) | 132,419 stars | Local only, tiny models |
| groq/openbench, sierra-research/tau2-bench | Used by OpenRouter harness (GPQA, Tau2 Airline) | n/a | n/a | n/a | n/a | n/a | 829 / 2,325 stars | Not tool-contract tests |

### 1.2 What K2VV actually does (code read: `tool_calls_eval.py`, 1,017 lines)

- Input: JSONL of full request bodies. `prepare_request` copies the body, rewrites `_input` role to `system`, overrides `model`, optional `temperature`/`max_tokens`, forces `stream_options.include_usage` when streaming.
- Per request it records `finish_reason`, the response, usage, and, if `finish_reason == "tool_calls"`, runs `validate_tool_call`: find schema by `function.name` in the request's `tools`, `json.loads` the arguments if string, `jsonschema.validate`. Missing schema, bad JSON, or ValidationError all count as invalid.
- `compute_summary` outputs counts only (finish_stop, finish_tool_calls, finish_others_detail, successful_tool_call_count, schema_validation_error_count, usage). F1 is an offline comparison step between a vendor results file and the official results file (FLock's `compare.py` and Prism implement exactly this).
- Kimi-specific hacks to avoid: tool-call ID rewriting to `functions.name:idx`, raw `/completions` mode parsing `<|tool_calls_section_begin|>` markers.
- README guidance worth stealing: record engine versions (vLLM >= 0.11.1rc6, SGLang >= 0.5.5.post2 for K2-thinking), recommend guided decoding to enforce schemas, and state the official API's own run-to-run F1 spread (lowest 75.81%, avg 76%) to set the acceptance band at 73%.
- KVV adds the idea "API parameter constraints must be enforced" (send wrong `temperature`/`top_p`/`n`, expect 400) and "usage.prompt_tokens must equal a known constant" - both are cheap, deterministic contract tests we should have for GLM too.

### 1.3 Our differentiation (what none of the above do)

1. GLM wire spec as the oracle, not "another vendor's sampled behaviour". Most checks are deterministic contract assertions (error code 1210 on `thinking: disabled` for GLM-5.3, reasoning_content round trip, tool_stream chunk reassembly) so they need no reference deployment and have no sampling noise.
2. Provider x model x harness matrix. Competitors score provider x model. GLM failures are strongly harness-shaped (Claude Code sends `thinking.type: disabled`, Codex sends Responses-style MCP schemas with `oneOf`, OpenCode replays `reasoning_content`, Crush assembles stream deltas). Cases are tagged with the harness request shape they replay.
3. Both wire protocols (OpenAI chat and Anthropic Messages) plus Responses (`https://api.z.ai/api/v1` is documented), and endpoint class (standard API vs Coding Plan vs third-party host), because documented behaviour differs by class (see section 2.4).
4. Multi-turn, thinking-aware agent loops: preserved thinking, interleaved thinking, tool result round trip, long-context drift (reasoning collapse), not only single-turn trigger F1.
5. Stream parity as a first-class metric (stream vs non-stream agree on tool names, args, finish_reason, usage).
6. Failure taxonomy per case with machine-detectable oracle and a source issue, so each issue becomes a regression test and a "known upstream bug" label (as canitoolcall triage does).
7. Silent downgrade / quantization fingerprinting (section 4) using action-based probes rather than text, with explicit statistical gates and CIs (AA-style), and a "differential vs reference" mode for those who have a reference deployment.
8. Open licence, open full corpus (K2VV published half, unlicensed), reproducible JSON/JUnit/SARIF-ish output and a machine-readable provider card per run.
9. Scoring that avoids rank-fusion distortion: report per-category pass rates with Wilson CIs and a hard-gate verdict (contract failures cannot be averaged away).

Interop recommendations: import canitoolcall GLM fixtures as parser-level cases; accept K2VV/Prism JSONL as an input adapter ("replay corpus" mode, compute F1/schema accuracy for continuity); adopt OpenRouter's 3-way error taxonomy (InvalidJson / UnknownName / SchemaMismatch) and request-level aggregation; use Draft 2020-12 validation and ALSO Draft 7 (to match OpenRouter) and report disagreement.

---------------------------------------------------------------------------------------------

## 2. GLM wire spec (from https://docs.z.ai, read 2026-10-26)

Sources: llms.txt index; /guides/overview/migrate-to-glm-new.md; /guides/llm/glm-5.3.md; /guides/vlm/glm-5.3-flash.md; /guides/capabilities/{thinking-mode,thinking,stream-tool,function-calling,cache,streaming}.md; /api-reference/{api-code,llm/chat-completion}.md; /guides/overview/pricing.md; /devpack/{latest-model,notice/api-key-management,tool/claude}.md. NOT obtained: https://docs.z.ai/api-reference/rate-limit (empty from `.md`, JS shell from jina), and https://z.ai/blog/glm-5.3 (SPA, 655 bytes; the docs migration guide and cc-switch#6737, which quotes the blog, were used instead). The mention of a "GLM-5.3 migration guide" in the brief is satisfied by https://docs.z.ai/guides/overview/migrate-to-glm-new.md.

### 2.1 Models, context, pricing (USD per 1M tokens; pricing page)

| Model ID | Notes | Input | Cached input | Output |
|---|---|---|---|---|
| `glm-5.3` | text-only input; 1M context; 128K max output | 1.4 | 0.26 | 4.4 |
| `glm-5.3-flash` | native multimodal (image/video/file), 320B total/18B active, 1M context | 0.15 | 0.03 | 0.50 |
| `glm-5.3-flashx` | faster (200 tok/s); not on Coding Plan at time of writing | 0.37 | 0.075 | 1.25 |
| `glm-5.3-flashx-legal` | listed under text models | 0.37 | 0.075 | 1.25 |
| `glm-5.2`, `glm-5.1` | | 1.4 | 0.26 | 4.4 |
| `glm-5` | | 1.0 | 0.2 | 3.2 |
| `glm-4.7`, `glm-4.6` | | 0.6 | 0.11 | 2.2 |

Cached input storage: "Limited-time Free". GLM-5.3 is "the same base model as GLM-5.2, all improvements from post-training". Claude Code integration uses `glm-5.3[1m]` / `glm-5.3-flash[1m]` suffix client-side with `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`.

### 2.2 Endpoints and keys

| Protocol | Base URL | Notes |
|---|---|---|
| OpenAI chat, standard API | `https://api.z.ai/api/paas/v4` (path `/chat/completions`) | pay-as-you-go balance |
| OpenAI chat, Coding Plan | `https://api.z.ai/api/coding/paas/v4` | the GLM-5.3 model page lists THIS URL as "OpenAI Chat Completion Protocol" |
| OpenAI Responses | `https://api.z.ai/api/v1` | Codex |
| Anthropic Messages | `https://api.z.ai/api/anthropic` (`POST .../v1/messages`, headers `x-api-key` or `ANTHROPIC_AUTH_TOKEN`, `anthropic-version: 2023-06-01`) | same base URL for API and Coding Plan; billing is decided by the KEY type, not the URL |
| China mainland (seen in issues) | `https://open.bigmodel.cn/api/paas/v4`, `/api/coding/paas/v4`, `/api/anthropic` | separate platform |

From 2026-10-16 keys are classified Standard / Coding Plan (Personal or Team) / "Compatible" (legacy). Compatible keys on `paas/v4` bill balance; on `coding/paas/v4`, `/api/anthropic`, `/api/v1` bill plan quota if the account ever had a plan. Test harness must record key class because 1113/1308/1309/1310/1311/1313-1321 depend on it. Note from zai-coding-plugins#34: `zcode login` can mint a PAYG key returning 1113 against plan endpoints (test: plan-key smoke).

### 2.3 Request fields that matter (chat-completion OpenAPI)

- `model`, `messages` (roles system/user/assistant/tool; assistant carries `content`, `reasoning_content`, `tool_calls[{id,type:"function",function:{name,arguments(JSON string)}}]`; tool message needs `tool_call_id`, `content`).
- `thinking: {type: "enabled"|"disabled", clear_thinking: bool}`. Default enabled. GLM-5.3 / 5.3-Flash: only `enabled` (forced). GLM-5.2/5.1/5/4.6: model decides when enabled. GLM-4.7: forced thinking.
- `clear_thinking` default `true` (reasoning_content from prior turns dropped). `false` = Preserved Thinking: caller must send complete, unmodified, correctly ordered historical `reasoning_content`. Docs: preserved thinking is ON by default on the Coding Plan endpoint, OFF by default on the standard API endpoint.
- `reasoning_effort` (works only when thinking enabled; GLM-5.2+). OpenAPI enum: max, xhigh, high, medium, low, minimal, none. Default `max`.
  - Standard API, GLM-5.3 / 5.3-Flash: only `low`, `high`, `max`; "any other input will result in an error".
  - Standard API, GLM-5.2: `none`/`minimal` = skip thinking; `low`/`medium` -> `high`; `xhigh` -> `max`.
  - Coding Plan, GLM-5.3 / Flash: `none`,`minimal`,`low` -> `low`; `medium`,`high` -> `high`; `xhigh`,`max` -> `max`; and `thinking.type` in {false, disabled, none, off} -> `low` (request continues). Any unknown string -> `max`. Priority: explicit effort > thinking toggle > default max. Claude Code uses `thinking.type` + `output_config.effort`; Codex uses `reasoning.effort`.
- `temperature` [0,1] default 1.0; `top_p` [0.01,1] default 0.95 (do not tune both); `do_sample` (false ignores temperature/top_p); `max_tokens` up to 131072 (128K); `stream`; `tool_stream` (needs `stream=true`; supported by 5.3, 5.2, 5.1, 5, 4.7, 4.6 per OpenAPI; the stream-tool guide text still says "limited to glm-5" - doc inconsistency, test both); `tools` (max 128 functions; also `retrieval`, `web_search` types); `tool_choice` documented as `auto` only; `stop` (docs say only one stop word supported, schema maxItems 4); `response_format` {text|json_object}; `request_id`.
- Streaming: SSE, ends with `data: [DONE]`. Delta fields: `reasoning_content`, `content`, `tool_calls[{index,id,function{name,arguments}}]`. With `tool_stream=true`, argument fragments stream unbuffered and must be concatenated per `index` (docs sample code). Final usage in last chunk.
- Response: `choices[].finish_reason` in {`stop`, `tool_calls`, `length`, `sensitive`, `model_context_window_exceeded`, `network_error`}; `usage{prompt_tokens, completion_tokens, total_tokens, prompt_tokens_details{cached_tokens}}`. Context caching is implicit/automatic; the only visible field is `cached_tokens`. Note: SSE abnormal termination reports the reason in `finish_reason`, NOT an error code (api-code page).
- Interleaved thinking: always on; clients must return reasoning with tool results. Turn-level thinking: toggling per request (GLM-4.7+; moot for 5.3 since it cannot be disabled).

### 2.4 Behavioural contract differences by endpoint class (each is a test)

| Behaviour | Standard API | Coding Plan | Evidence |
|---|---|---|---|
| `thinking.type: "disabled"` on glm-5.3 / 5.3-flash | HTTP 400, `error.code` "1210" (zh message "该模型始终思考，不支持关闭思考；请使用 low、high 或 max。") | docs say converted to `low` and proceeds (devpack/latest-model) | cc-switch#6737 live A/B on `open.bigmodel.cn/api/anthropic` observed 400/1210 on the Anthropic path; docs.z.ai/guides/capabilities/thinking.md for the API rule; devpack page for the Plan rule. Treat as "doc says X; measure and record both", do not hard-fail on the Plan class until verified |
| `thinking: enabled` + `reasoning_effort: low` (no budget_tokens) | 200 | 200 | cc-switch#6737 |
| `reasoning_effort: "medium"` on glm-5.3 | error | maps to high | thinking.md |
| `clear_thinking` default | true (cleared) | preserved thinking effectively on | thinking-mode.md |
| Anthropic path | `message_start.usage` all zeros; real usage only in `message_delta` | same (observed with coding-plan key) | GLM-5#139 |

### 2.5 Error table (https://docs.z.ai/api-reference/api-code.md)

Shape: `{"error":{"code":"<string>","message":"..."}}` (code is a STRING in the docs example, integer in the OpenAPI Error schema: test for either, record which).

| code | HTTP | Meaning |
|---|---|---|
| (none) | 500 | Internal Error |
| 1000 / 1001 / 1003 / 1005 | 401 | auth failed / missing header / token expired / 2FA needed |
| 1113 | 429 | insufficient balance or no resource package |
| 1200 | 500 | API call error |
| 1210 | 400 | invalid API parameter (used for forced-thinking violation) |
| 1211 | 400 | unknown model |
| 1212 | 400 | model does not support `${method}` |
| 1213 | 400 | parameter `${field}` not received |
| 1214 | 400 | parameter `${field}` invalid |
| 1215 | 400 | `${field1}` and `${field2}` cannot be set simultaneously |
| 1220 | 403 | no permission for API |
| 1221 / 1222 | 400 | API offline / does not exist |
| 1230 | 500 | API call process error |
| 1234 | 500 | network error with error id |
| 1261 | 400 | prompt too long |
| 1301 | 400 | unsafe/sensitive content |
| 1302 | 429 | rate limit reached for requests |
| 1305 | 429 | service temporarily overloaded |
| 1308 | 429 | usage limit reached for N unit, resets at time |
| 1309 | 429 | Coding Plan expired |
| 1310 | 429 | weekly/monthly limit exhausted |
| 1311 | 429 | plan lacks access to model |
| 1313 | 429 | Fair Usage Policy limiting |
| 1314 / 1315 | 429 | enterprise package expired / key limited to enterprise coding scenario |
| 1316-1321 | 429 | 5h / 7d usage limits with extra-usage balance or monthly spend limit variants |

Rate-limit numbers: UNVERIFIED (page not retrievable). The harness must treat 1302/1305/1308/13xx as `throttled` not `failed`, back off, and report the throttle share per provider.

### 2.6 Native model output format (needed for parser-level cases)

GLM-4.7/5.x XML-ish tool call: `<tool_call>NAME<arg_key>K</arg_key><arg_value>V</arg_value>...</tool_call>`, string values written verbatim, non-strings via `tojson` (seen in vLLM `test_glm47_moe_tool_parser.py`, ollama#18659, GLM-5#158 chat_template line 163). Self-hosted: SGLang `--tool-call-parser glm47 --reasoning-parser glm45` (AA reference config); vLLM `--tool-call-parser glm47 --reasoning-parser glm45` (vllm#56868). Chat template renders every `reasoning_effort` except `low`/`high` as `max` (vllm#56868 comment by Lei-010, template dated Aug 26): so `medium` silently becomes `max` on self-hosted stacks.

---------------------------------------------------------------------------------------------

## 3. Known failure cases (issues read in full via `gh issue view --json body,comments`)

Local copies: /private/tmp/claude-501/-Users-nainish-development/80660d68-c334-46f4-899d-54a7cc71bc3e/scratchpad/iss/ (scratch only). Status as of 2026-10-26.

| Issue | Status | Trigger (exact request shape) | Expected vs actual | Automatic detection |
|---|---|---|---|---|
| zai-org/GLM-5#169 | open, "forwarded to MaaS team" 2026-10-16, "still processing" 2026-10-21 | glm-5.3 and glm-5.3-flash, `coding/paas/v4`. Tool with `parameters = {"type":"object","properties":{},"oneOf":[{type object, properties{mode:{const:"view"},id:{string}}, required[mode,id]}, {... mode:{const:"update"} ...}]}` plus a flat control tool; system "You are a helpful assistant. Use tools to complete the task."; user "Please set automation 23-00 mode to view. Call the tool."; `tool_choice:"auto"`, `max_tokens:300` | Expected arguments `{"mode":"view","id":"23-00"}`; actual HTTP 200, `finish_reason=tool_calls`, `arguments:"{}"` (stable). Flat schema returns correct args. Caused retry loop: 97 requests, ~16M input tokens | Validate arguments against schema: `required` violated. Run A/B: oneOf vs flattened; assert both pass; score "schema-shape sensitivity" |
| GLM-5#158 | open | Self-hosted / `apply_chat_template` with history whose assistant `tool_calls[].function.arguments` is a JSON STRING (OpenAI REST form); GLM-5.3-Flash `chat_template.jinja` L163 calls `.items()` | Expected render; actual `jinja2.UndefinedError: 'str object' has no attribute 'items'`; vLLM API server sometimes crashes (comment) | Black-box: send history with string arguments to the endpoint; expect 200 not 5xx. Also dict-vs-string argument parity test. Template-level check offline |
| GLM-5#168 (plus vllm#56868, same signature) | open | glm-5.3-flash, preserved thinking (every prior assistant turn's `reasoning_content` echoed), `reasoning_effort` max, temp 1.0, top_p 1.0/0.95, ~8 assistant turns whose reasoning is in drifted style (`=`,`the`, bold caps); 2k-token synthetic repro script in the issue (bash tool, fabricated outputs). Real session: 325 turns, 40k -> 450k tokens | Healthy reasoning has 5-12% of words in {`=`,`the`}; collapse = 70-99% until `max_tokens` (11/26 styled trials; prose control 0/20; GLM-5.3 non-Flash 0/20). Context length not the cause; removing all prior reasoning resets it | Metric: `reasoning_degeneracy = share of "="/"the" words` or low-entropy n-gram repetition (regex `(.{2,12})\1{7,}`), U+FFFD count; assert < 0.2 over N trials; `finish_reason=length` with no content. Run with and without replayed reasoning |
| vllm-project/vllm#56868 | open | GLM-5.3-Flash W4A16 (private quant), vLLM 0.28.1rc1, TP1, glm45/glm47 parsers, reasoning_effort max, clear_thinking true, long multi-agent session | Collapse (`= = =`, `.0.0.0`) in reasoning after ~76 sequential requests / ~200k accumulated DECODE tokens; cold replay of the failing request is healthy; prefill-only warm-up is healthy; MTP off/prefix cache off/FP8 vs bf16 do not remove it (uncalibrated FP8 KV made it worse and made greedy non-deterministic: 3 identical temp=0 seed=42 requests gave 8513/8296/8475 chars); effort `high` delays it; spec-decode off removed an empty-content stall (comment 2026-10-09); explicit `high` removed "reasoning runaway" 8/8 -> 0/60 | Soak test: N sequential agent turns on one session/engine; per-turn degeneracy metric; determinism test (3x identical temp=0 requests must match byte for byte, else flag); `reasoning_effort` sweep |
| GLM-5#142 (and #54, #163 referenced) | #142 open, #54 closed | glm-5.3-flash via Ollama Cloud/z.ai; all-English prompts/system, long agentic tool loop; `reasoning_effort` high/xhigh (hits only on `high`, none on low/max in one 8/15 session sample); after bursts of 3 quick successful tool calls | Expected English only; actual inline CJK (`日志`, `黔`), full Chinese sentences before tool calls, German word, and in reasoning channel more often than text (5 vs 3 parts); also mojibake U+FFFD collapse. #54: GLM-5.1/5.2: the model denies switching language | Language-consistency check: CJK codepoints in text and reasoning of English-only conversations, excluding quoted input; rate per 1000 turns with Wilson CI; must scan `reasoning_content` too |
| GLM-5#116 (closed) | closed | glm-5.2, Anthropic path, Claude Code, ~450 turns, three failed `pwd && ...` Bash calls then instruction to prefix `cd /workspaces/myrepo && ` | thinking contains the exact command with `cd`; emitted `tool_use.input.command` omits the prefix; 10 repeats. Controlled repro by a third party (10/10 correct) did NOT reproduce | Plan-vs-emission fidelity: instruct a literal prefix, assert emitted argument startswith it, after k anchoring turns. Low priority, flaky; keep as "soak/optional" |
| GLM-5#139 | open | `POST https://api.z.ai/api/anthropic/v1/messages` `{"model":"glm-5.3","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"Say OK"}]}` headers x-api-key, anthropic-version 2023-06-01 | Expected `message_start.message.usage.input_tokens` > 0 (and cache fields); actual zeros; real usage only in final `message_delta` (`input_tokens:14, output_tokens:32, cache_read_input_tokens:0, server_tool_use.web_search_requests, service_tier`) | Parse SSE: assert message_start input_tokens>0 |
| GLM-5#144 (closed) | closed | glm-5.3-flash, `paas/v4`, stream, 100% cache hit, add `tools` (1 or 60) or `tool_choice:"none"` | TTFT 0.76s without tools vs 3.7-4.2s with tools, independent of tool count; intermittent in follow-up (fast path 7/30) | Latency probe: TTFT with/without tools, report median/p95 delta, flag > 2s. NOTE: `tool_choice:"none"` was accepted though docs say only `auto` |
| zai-coding-plugins#12 | open | Anthropic path `/v1/messages`, model alias `opus`->glm-4.7, content `'{"key": "value", "nested": {"data": 123}} ' * 300` | usage.input_tokens 4506 vs `len//4` estimate 3150 = 1.43x (JSON), special chars 2.43-2.49x; Claude Code hits "context window limit" at 80% reported | Token-accounting calibration: compare reported `usage.prompt_tokens` for fixed corpora to an expected constant (KVV style) and to the same text on the reference tokenizer; record ratio per content type |
| zai-coding-plugins#14 | open | open.bigmodel.cn/api/anthropic, glm-5.1, history containing assistant `tool_use` with `input` as ARRAY (the model sometimes generates it) then `/compact` | Expected 4xx or graceful; actual HTTP 500 `['ClaudeContentBlockToolResult' object has no attribute 'id']`; session history unrecoverable | Send crafted history; assert 4xx with structured error, never 500 / Python exception text. Also detect model emitting non-object `input` |
| zai-coding-plugins#15 | open | (1) Anthropic request without `max_tokens`; (2) `tool_use.input: ["ls","-la"]`; (3) body > 1 MB; (4) `model:"claude-3-opus-20240229"` | (1) expected 400 invalid_request_error; actual 500 with leaked `'>' not supported between instances of 'int' and 'NoneType'`. (2) 500 as above. (3) HTTP 200 with `input_tokens:0` (silently dropped). (4) expected 404; actual answer from `glm-4.7` (silent model fallback) | Four negative-input tests; assert no stack text, no 5xx, no silent fallback (response `model` must equal requested or be a 4xx) |
| zai-coding-plugins#34 | open | `zcode login` + `zcode -p`, Team plan | 429 code 1113 because the minted key is PAYG | Key-class smoke: plan key against plan endpoint must not return 1113 (harness config check, not model) |
| charmbracelet/crush#3153 | closed (client bug) | GLM-5.2 on Fireworks `glm-5p2`, stream:true, `edit`/`write` tools with large string args, `tool_choice` required | Provider stream is valid: 23 `delta.tool_calls[].function.arguments` fragments concatenate to valid JSON (DeepSeek sends 1); client mis-assembled, replay gave 400 "must be a JSON object string" | Stream-reassembly test: many-fragment arguments with newlines/quotes/code; assert concatenation by `index` parses and equals non-stream result; also assert `index` present on every fragment |
| anomalyco/opencode#26762 | closed (provider-specific fix PR 26763) | cerebras `zai-glm-4.7`, follow-up turn replays assistant message with `reasoning_content` | `messages.2.assistant.reasoning_content: property ... is unsupported` (400) | Replay-compat test per provider: accepts/rejects `reasoning_content` (and alias `reasoning`) on assistant history; record which field name is accepted |
| anomalyco/opencode#6708 | closed | glm-4.7 on Z.ai Coding Plan, context >100-130k, interleaved thinking | Tool call text appears INSIDE the thinking block: e.g. `Thinking: bash<arg_key>command</arg_key><arg_value>...</arg_value></tool_call>`, tool never executes; session poisoned. Context length 128k workaround; possibly related to history pruning, `clear_thinking:false` | Marker-leak check: `<tool_call>`, `<arg_key>`, `<arg_value>` must not appear in `content` or `reasoning_content`; at 100k/130k/200k context |
| farion1231/cc-switch#6737 | open | Anthropic Messages to glm-5.3 / 5.3-flash with `"thinking":{"type":"disabled"}` (Claude Code auxiliary requests) | HTTP 400 code 1210 `该模型始终思考，不支持关闭思考；请使用 low、high 或 max。`; works with `thinking:{type:enabled}` + `reasoning_effort:"low"` (no budget_tokens). Flash same; `glm-5.3-flashx` NOT matched by their rule | Deterministic: expect 400/1210 on API class; record behaviour on third parties (they should either mimic 1210 or accept) |
| sgl-project/sglang#46037 | open (fix PR 46038) | `--tool-call-parser glm4_moe` or `glm47`; model text `<tool_call>api_fetch<arg_key>body</arg_key><arg_value>null</arg_value>...`; schema `params: anyOf[string,null]` | Expected JSON `null`; non-stream glm4 gives string `"None"`, glm47 `"null"` | Schema with `anyOf[string,null]`; force model output via prompt or run fixture; assert `arguments.body is None`. Nullable-arg case |
| sgl-project/sglang#35564 | open | SGLang parsers glm, glm45, glm47 etc., `parse_streaming_increment` fed one character at a time | `{"get_weather": 123}` streams as `"123"` (int -> str); final vs streamed differ; other parsers lose calls | Stream vs non-stream equality on typed args (int, bool, null, nested) |
| ollama/ollama#18659 | open | glm-4.7 parser; tool `write_file(path, content)`; model output has literal `</tool_call>` inside `<arg_value>`: `content` = "Wrap each call in <tool_call> and </tool_call>; keys go in <arg_key>." | Expected whole string in `content` arg; actual cut at first `</tool_call>`, rest leaks into assistant content. Sister bug ollama#18658 (newlines stripped from arg values) per canitoolcall | Ask the model to write a file whose content documents the tool-call format; assert exact roundtrip of string including marker text, newlines, leading/trailing whitespace |
| ollama/ollama#13820 | open | glm-4.7-flash with Claude Code, Ollama 0.14.3 | parse failure `XML syntax error on line 1: element <tool_call> closed by </arg_key>`; client loses context; maintainers: needs more context length, model template updated since | Malformed-XML tolerance: assert stable behaviour (no dropped session) on engines; low value for hosted |

### 3.1 Additional failure modes found (not in the requested list)

- vllm#56868 comment: self-hosted `chat_template` clamps `reasoning_effort` not in {low,high} to `max`.
- vllm#56868 comment (leongreiner): 63k-token Claude-Code-session replays gave 23-30% degenerate turns (short one-sentence reply, no tool call), 97k gave 7-20% plus corrupted tool-call args (`<arg_key>`/`<arg_value>` markup in a JSON key); unchanged across 6 targeted vLLM fixes. Same family as zai-coding-plugins; test "no tool call when one is required" at 60k and 100k.
- GLM-5#169 shows that 16M input tokens can burn on a retry loop; the harness must cap retries and report token cost per failure.

---------------------------------------------------------------------------------------------

## 4. Detecting quantization and silent model downgrade

Published methods read (abstracts via arXiv API, not full texts):

1. Model Equality Testing (Gao et al.), arXiv 2410.20247 https://arxiv.org/abs/2410.20247. Two-sample test between API samples and a reference distribution; MMD with a string (Hamming) kernel, median power 77.4% with ~10 samples per prompt across distortions; applied to 31 commercial Llama endpoints (Summer 2024), 11 served different distributions from Meta weights. Needs a reference you can sample (self-hosted weights).
2. Rank-Based Uniformity Test (RUT), arXiv 2506.06975 https://arxiv.org/abs/2506.06975 (ICLR 2026; code github.com/xyzhu123/RUT). Compares black-box model to a local authentic model using rank statistics; robust to providers that detect and reroute test queries; covers quantization, harmful fine-tuning, jailbreak prompts, full substitution.
3. Cai et al., "Are You Getting What You Pay For?", arXiv 2504.04715 https://arxiv.org/abs/2504.04715 (NeurIPS 2025; code github.com/sunblaze-ucb/llm-api-audit). Conclusion: text-output tests are query-hungry and miss subtle swaps; logprob methods are defeated by production nondeterminism; proposes TEEs. Search-result summary (not full text read): power weak on small models, and ~20% substitution indistinguishable from chance. Takeaway for us: do not promise single-request detection of quantization; give statistical confidence with budgets.
4. IRIS, arXiv 2607.20860 https://arxiv.org/abs/2607.20860. Text-only; asks endpoints to generate random numbers/strings, fingerprints backend; detects substitution and fractional "routing dilution", sizes its own query budget; on live cross-provider audit flagged 14 of 15 same-model provider pairs "by genuine quantization and kernel deviations"; 0.99 AUROC on a Qwen3 ladder.
5. AgentProv, arXiv 2609.00052 https://arxiv.org/abs/2609.00052. Fingerprint through the categorical TOOL-CALL distribution (which tool/action the model picks), MMD permutation test; 100% catch on 630 checkpoint pairs; false-positive rate 7% under provider-injected system prompts vs 67% for MET and 53% for RUT; notes token-count side channel reveals injected system prompts. This is the best fit for us (agentic, action-based, works when text is hidden).
6. Log Probability Tracking of LLM APIs (Chauvin et al., ICLR 2026, from search result only; UNVERIFIED details) - counterpoint that logprob tracking works. GLM endpoints: `logprobs` support is not documented in the pages read; assume unavailable.
7. Practitioner signals from issues: greedy non-determinism run to run (vllm#56868: identical temp=0 seed=42 requests differ => uncalibrated FP8 KV / state leak); `usage.prompt_tokens` constants (KVV) catch template/tokenizer differences; silent model fallback (zai-coding-plugins#15 bug 4: unknown model answered by glm-4.7); AA reference config shows what "full precision" means for GLM-5.3 (FP8 E4M3 weights, fp8 KV).

Proposed fingerprint stack for glm-conformance (cheapest first):

1. Identity and metadata: response `model` field equals requested; unknown-model request returns 4xx (1211); `usage.prompt_tokens` for a fixed corpus equals the golden constant from the official API (catches tokenizer/template swaps; KVV approach); `cached_tokens` behaviour on repeated prefix.
2. Determinism: 5x identical requests at `temperature=0` (or `do_sample:false`), measure exact-match rate and edit distance; compare to the official API's own self-agreement rate (reference noise floor). Quantization/KV-cache changes and speculative decoding often reduce self-agreement.
3. Action fingerprint (AgentProv style): 50-100 ambiguous agent prompts with 4-8 tools; record the categorical distribution of first tool choice and finish_reason; MMD permutation test vs reference (official API samples collected by us and stored as golden histograms). Gate with CI, not a point value.
4. Text fingerprint (MET/IRIS style): fixed prompt set (random-number/random-string prompts, short completions), Hamming-kernel MMD vs stored reference samples; budget 10 samples per prompt.
5. Capability canaries: AA-style percent-of-reference with CIs on a small slice of BFCL (multi-turn and parallel overweighted), a long-context recall item, and a hard-reasoning slice; ratio and 95% CI to reference.
6. Tripwires for known degradations: reasoning-degeneracy metric, CJK bleed rate, marker leak, schema-shape sensitivity (these are partly quantization/kernel symptoms).

Reference sources: official `api.z.ai` as "ground truth" (like K2VV), plus optional self-hosted reference using AA's published GLM-5.3 config (SGLang 0.5.18, `zai-org/GLM-5.3` FP8, glm45/glm47 parsers). Always publish noise floor: official-vs-official repeated runs (K2VV did this: 75.81% min).

Caveat on scope: Bedrock, Mistral, Fireworks, Together, Nebius, DeepInfra provider-specific quirks were NOT researched in this pass except Fireworks (crush#3153) and Cerebras (opencode#26762); K2VV data shows Together, DeepInfra, Nebius vary widely for Kimi, which is circumstantial evidence only for GLM.

---------------------------------------------------------------------------------------------

## 5. Proposed test-case catalog

Conventions: protocols O=OpenAI chat, A=Anthropic Messages, R=Responses; mode N=non-stream, S=stream; endpoint class API=standard, PLAN=Coding Plan, TP=third-party host, SH=self-hosted. Severity: C critical (hard gate), M major, m minor. "Det" = deterministic oracle; "Stat" = needs repeats and a threshold.

| ID | Category | Request shape | Pass criterion | Source |
|---|---|---|---|---|
| WIRE-001 | wire/errors | glm-5.3, `thinking:{type:"disabled"}`, "Reply OK", O and A, N | API class: HTTP 400 and `error.code` in {"1210",1210}; message non-empty. PLAN: 200 with low effort OR 400/1210 (record, soft) ; TP: 200 or 400, never 5xx | cc-switch#6737, thinking.md |
| WIRE-002 | wire | glm-5.3, `thinking:{type:"enabled"}`, `reasoning_effort:"low"` | 200; reasoning_content present or reasoning blocks present; finish stop | cc-switch#6737 |
| WIRE-003 | wire | glm-5.3, `reasoning_effort` in {low,high,max} | each 200; token usage monotone-ish: median completion tokens low <= high <= max over 5 runs (Stat, m) | glm-5.3.md |
| WIRE-004 | wire/errors | glm-5.3 API class, `reasoning_effort:"medium"`, `"xhigh"`, `"none"` | 400/1210 or 1214; PLAN: 200 (mapped). TP: record | thinking.md |
| WIRE-005 | wire | glm-5.2 `reasoning_effort:"none"` | 200 and no `reasoning_content` (thinking skipped) | thinking.md |
| WIRE-006 | wire/errors | model `glm-9.9` | 4xx (1211) and NOT a 200 answered by another model; response `model` field must never differ from request on 200 | api-code, zai-coding-plugins#15 b4 |
| WIRE-007 | wire/errors | missing `messages`; `temperature:2`; `top_p:0`; `max_tokens:200000`; `tools` with 129 functions | 400 with structured `error.code` in 121x; never 5xx, no Python exception text (`object has no attribute`, `NoneType`, `Traceback`) | api-code, plugins#15 |
| WIRE-008 | wire/errors | Anthropic body without `max_tokens` | 400 invalid_request_error (Anthropic shape) ; fail if 500 | plugins#15 b1 |
| WIRE-009 | wire/errors | Anthropic history with `tool_use.input` = array | 4xx structured; fail on 500 | plugins#14, #15 b2 |
| WIRE-010 | wire/errors | request body > 1 MB (padding) | 4xx (1261 or 413) or full processing with `usage.prompt_tokens` reflecting size; fail if 200 with `input_tokens:0` | plugins#15 b3 |
| WIRE-011 | wire | `tool_choice:"none"`, `"required"`, `{"type":"function",...}` with tools | Docs say only `auto`: record accept/reject; if accepted, behaviour must be honored ("none" -> no tool_calls; N=10) . Informational, m | function-calling.md, GLM-5#144 |
| WIRE-012 | wire | `stop:["END"]` and 2 stop words | honoured / 400 with 1214; record | chat-completion ref |
| WIRE-013 | wire | `do_sample:false` with temperature 1 | 5 runs identical (Stat) or at least higher self-agreement than default | chat-completion ref |
| WIRE-014 | wire | `response_format:{type:"json_object"}` + prompt requiring JSON | parses as JSON 20/20 | struct-output.md (only field list read) |
| WIRE-015 | wire/stream | stream:true plain | SSE ends with `[DONE]`; exactly one `finish_reason` terminal chunk; usage present in last chunk (`include_usage`); `delta.role` once | streaming.md, API ref |
| WIRE-016 | wire/stream | stream with thinking | `delta.reasoning_content` precedes `delta.content`; no content inside reasoning; both reassemble equal to non-stream length class | thinking-mode.md |
| WIRE-017 | wire/stream | stream abnormal: kill after N chunks (client side) / `max_tokens:1` | `finish_reason:"length"`; no hanging stream; ollama#18926 style hang detection: stream must terminate within timeout | api-code note, ollama listing |
| WIRE-018 | wire | context overflow (prompt > 1M or provider limit) | `finish_reason:"model_context_window_exceeded"` or 400/1261, never a hang or 5xx | chat-completion ref |
| ANTH-001 | anthropic | `POST /v1/messages` stream, "Say OK" | `message_start.usage.input_tokens` > 0 | GLM-5#139 |
| ANTH-002 | anthropic | `x-api-key` vs `Authorization: Bearer`; missing `anthropic-version` | 200 for supported auth; 401 code 1001/1000 otherwise | devpack/claude.md |
| ANTH-003 | anthropic | `thinking:{type:"enabled", budget_tokens:2048}` and `output_config.effort` | accepted (budget ignored OK); thinking blocks have `signature`? (record) ; tool round trip keeps thinking blocks | latest-model.md |
| ANTH-004 | anthropic | token accounting: `'{"key": "value", ...}' * 300`, 10 corpora (JSON, code, CJK, special chars, English) | record `usage.input_tokens` / reference ratio; flag > 1.3x vs official API same model (M) ; compare O vs A paths agree within 2% | plugins#12 |
| ANTH-005 | anthropic | model alias `opus`/`claude-sonnet-*`/`glm-5.3[1m]` | resolves to documented target; response `model` field reported; record | claude.md, latest-model.md |
| TOOL-001 | tools/basic | 1 flat tool, user requires it; N and S | `finish_reason=tool_calls`; name in tools; args valid vs schema (Draft 7 AND 2020-12); rates over 20 runs (Stat, C: >= 95% of official) | K2VV, OpenRouter |
| TOOL-002 | tools/no-call | tools offered, question needing none ("What is 2+2?") | `finish_reason=stop` , no tool_calls ≥ 90% (irrelevance) | BFCL irrelevance |
| TOOL-003 | tools/parallel | two independent calls required | 2 tool_calls with unique `id`s, both valid | BFCL parallel, canitoolcall |
| TOOL-004 | tools/types | args of int, float, bool, null (`anyOf[string,null]`), enum, array of objects, nested object, unicode, empty `{}` | typed round trip: JSON type equals schema type; null stays null (not "None"/"null"); int stays int | sglang#46037, #35564 |
| TOOL-005 | tools/string-fidelity | tool `write_file(path, content)`; content must include `</tool_call>`, `<arg_key>`, newlines, quotes, backslashes, leading/trailing spaces, 20 KB | exact string equality of `content` | ollama#18659 (+#18658), crush#3153 |
| TOOL-006 | tools/schema-shape | A/B same intent: flat props vs top-level `oneOf` with empty `properties` vs `anyOf` vs `$ref/$defs` vs `allOf` | each shape: arguments satisfy `required`; empty `{}` when required is non-empty = fail (C for the oneOf shape per #169). Report sensitivity = flat pass rate minus shape pass rate | GLM-5#169 |
| TOOL-007 | tools/many | 128 tools (max), 129 tools | 128: correct tool picked >= 90%; 129: 400 | chat-completion ref |
| TOOL-008 | tools/name | tool names with dots, dashes, `mcp__server__tool` (namespace), 64 chars | returned name exactly one of tools (no `UnknownName`) | OpenRouter taxonomy, vLLM glm47 test namespace case |
| TOOL-009 | tools/roundtrip | assistant tool_calls with string `arguments` + tool result + follow-up user | 200; answer uses result; same with `content:null` and `content:""` on the assistant turn | GLM-5#158, vllm#56868 (`content:null` normalisation) |
| TOOL-010 | tools/roundtrip | tool result as empty string / 100 KB / JSON / error | 200 and coherent continuation | function-calling.md |
| TOOL-011 | tools/markers | any tool turn, thinking on, context sizes 8k/64k/100k/130k/200k | no `<tool_call>`, `<arg_key>`, `<arg_value>`, `</tool_call>` in `content` or `reasoning_content`; tool executed (finish tool_calls) | opencode#6708 |
| TOOL-012 | tools/dup-ids | parallel calls, check unique `tool_calls[].id`, `type:"function"` present, `index` consecutive from 0 | pass | canitoolcall probe, crush#3153 |
| TOOL-013 | tools/forced-need | at 60k and 100k real Claude-Code-shape history, user asks for an action that requires a tool | tool call emitted >= 90% of official rate; no short "one-sentence stop"; no markup in arg keys | vllm#56868 (leongreiner) |
| TOOL-014 | tools/emission-fidelity | after 3 failed anchoring calls, instruct exact command prefix `cd /x && ` | emitted `command` startswith prefix >= 90% (optional soak) | GLM-5#116 |
| TOOL-015 | tools/trigger-F1 | K2VV-style replay corpus (adapter); F1 of tool_calls trigger vs official GLM API results | F1 >= (official self-F1 min - margin), schema accuracy >= official - 2 pts | K2VV |
| STRM-001 | stream-parity | all TOOL-001..005 in S and N | tool names equal; concatenated `arguments` JSON-equal to non-stream (temp 0 / do_sample false) or schema-valid in both; finish_reason equal | sglang#35564, canitoolcall |
| STRM-002 | stream/tool_stream | `stream:true`, `tool_stream:true`, large arguments (>= 4 KB code) | arguments arrive in >1 fragment each with `index`; concatenation by index valid JSON; `id`/`name` only on first fragment OK; total equals non-stream schema-valid | tool-stream docs, crush#3153 |
| STRM-003 | stream/tool_stream | `tool_stream:true` without `stream` ; `tool_stream` on glm-4.5 | 400 (1210/1215) or ignored; record | chat-completion ref, stream-tool.md ("limited to glm-5" inconsistency) |
| STRM-004 | stream | tool call with empty args `{}` and typed args, replayed with 1-char, 1-token chunk splits (offline parser fixtures) | stream result == non-stream result for all splits | sglang#35564 |
| STRM-005 | stream | `usage` in stream equals non-stream usage for same request (+/-1%) incl. `cached_tokens` | pass | KVV prompt_tokens, GLM-5#139 |
| THINK-001 | thinking/replay | 2-turn tool loop; resend assistant with `reasoning_content`, `thinking:{type:"enabled",clear_thinking:false}` | 200; `usage.prompt_tokens_details.cached_tokens` > 0 on turn 2 (cache hit) when reasoning replayed exactly (PLAN default / API with clear_thinking false) | thinking-mode.md |
| THINK-002 | thinking/replay | same history but reasoning truncated / reordered / edited | 200 (no crash); record cached_tokens drop and quality note | chat-completion ref |
| THINK-003 | thinking/replay | history with `reasoning_content` AND `reasoning` alias; provider rejecting field | provider accepts or documents rejection; the harness reports "replay-compat" (O: `reasoning_content`) | opencode#26762 |
| THINK-004 | thinking/clear | `clear_thinking:true` (default) with replayed reasoning | `prompt_tokens` for turn 2 smaller than with `clear_thinking:false` on same history (reasoning ignored) | chat-completion ref |
| THINK-005 | thinking/degeneracy | styled-reasoning replay (issue #168 script: 8 turns, 2k tokens, 62% `=`/`the`) vs prose control, 20 trials each, glm-5.3-flash and glm-5.3, `reasoning_effort` max | Degenerate share `reasoning_degeneracy>0.2` in prose control 0%; styled trials collapse rate recorded; fail (M) if prose control > 0% or glm-5.3 styled > 0% (GLM-5.3 non-flash was 0/20) | GLM-5#168 |
| THINK-006 | thinking/soak | 100+ sequential agent turns on one session (synthetic bash tool env), total decode > 200k tokens, effort max and high | degeneracy rate per 100 turns <= official; no `finish_reason=length` with empty content; U+FFFD count 0 | vllm#56868 |
| THINK-007 | thinking/determinism | 3 identical requests temp 0, top_p 1, seed 42 (self-hosted: also `do_sample:false`) | exact-match rate >= official self-agreement minus margin; identical byte count flagged | vllm#56868 FP8 KV finding |
| THINK-008 | thinking/effort-clamp | self-hosted: `reasoning_effort:"medium"` | document that template maps to `max`; mark SH-only informational | vllm#56868 comment |
| LANG-001 | language | 40 English-only agentic turns (system + user + tool results all English, includes 3-tool bursts), effort low/high/max | CJK codepoints in text or reasoning (excluding quotes from inputs) per 1000 turns <= threshold; report separately text vs reasoning; also non-English Latin words (German) optional | GLM-5#142, #54 |
| LANG-002 | language | same with Japanese-only context (#54 comment) | output language == context language | GLM-5#54 |
| LAT-001 | latency | stream, 20k-token cached prompt, with vs without `tools` (1 and 60), `tool_choice` none | TTFT delta with tools <= 1.0 s p50 (m, informational); report medians over 10 | GLM-5#144 |
| LAT-002 | latency | TTFT, TPS, completion tokens/s at low/high/max | recorded, not scored in correctness verdict | Prism metrics |
| CACHE-001 | caching | send same 2k-token prefix twice, 5 s apart | `usage.prompt_tokens_details.cached_tokens` > 0 on second; field present | cache.md |
| CACHE-002 | caching | 3-turn agent loop with growing history | cached_tokens grows monotonically; cached share of prompt >= 70% by turn 3 on Z.ai (record for others) | cache.md |
| USAGE-001 | usage | golden corpus (text, CJK, JSON, tools, image for flash) | `usage.prompt_tokens` equals golden constant from official API (+/- 0) for template-sensitive cases; ratio report otherwise | KVV prompt_tokens, plugins#12 |
| MM-001 | multimodal | glm-5.3-flash image_url (URL and base64), 1-10 images, text before and after | 200 with description; glm-5.3 (text-only) with image -> 400 (1210/1214) | glm-5.3-flash.md, cc-switch#6737 |
| AUTH-001 | auth/limits | invalid key; plan key on standard URL; standard key on plan URL | 401 1000/1001; 429 1113 etc. appropriate; classify | api-key-management.md, plugins#34 |
| LIM-001 | rate limit | burst of N concurrent | 429 with 1302/1305 and Retry hints; harness backs off; throttled calls excluded from quality denominators and reported | api-code |
| FP-001 | fingerprint | unknown/alias model name; response `model` | see WIRE-006 | plugins#15 b4 |
| FP-002 | fingerprint | action fingerprint set (AgentProv style), 100 prompts x 8 samples | MMD permutation p > 0.01 vs stored reference histogram, else flag "distribution differs" | arXiv 2609.00052 |
| FP-003 | fingerprint | MET text kernel set | same with Hamming-kernel MMD | arXiv 2410.20247 |
| FP-004 | fingerprint | greedy self-agreement noise floor, 5x | see THINK-007 | vllm#56868 |
| FP-005 | fingerprint | capability canaries (BFCL multi-turn slice, long-context needle at 128k/512k, math slice) | percent-of-reference with 95% CI; flag when CI entirely below 100% | AA methodology |

Known gaps (not yet sourced): Responses-API (`/api/v1`) cases; GLM-5#146 and #163 only seen as references; rate-limit numbers; Bedrock/Mistral/Together/Nebius/DeepInfra quirks.

---------------------------------------------------------------------------------------------

## 6. Recommended test-case file format

Principles: (a) cases are data, oracles are named checks implemented once in the runner (not code in the case file); (b) protocol-neutral canonical request with per-protocol overrides; (c) every case carries provenance and an expected-behaviour table per endpoint class; (d) deterministic checks and statistical checks are different types; (e) K2VV/Prism JSONL stays importable.

Directory: `cases/<category>/<id>.yaml`, plus `corpora/*.jsonl` for bulk replay corpora (K2VV-style one request per line) with a sidecar `corpora/*.meta.yaml`.

```yaml
# cases/tools/TOOL-006-schema-shape-oneof.yaml
schema_version: 1
id: TOOL-006
title: Top-level oneOf with empty properties must not yield {} arguments
category: tools/schema-shape
severity: critical            # critical | major | minor | info
kind: deterministic           # deterministic | statistical | soak | fingerprint
applies_to:
  models: [glm-5.3, glm-5.3-flash, glm-5.3-flashx, glm-5.2]
  protocols: [openai_chat, anthropic_messages, responses]
  endpoint_classes: [api, plan, third_party, self_hosted]
  harness_profiles: [codex_mcp, generic]   # request shape lineage
source:
  issues: ["zai-org/GLM-5#169"]
  urls: ["https://github.com/zai-org/GLM-5/issues/169"]
  spec: []                    # doc URLs used as oracle
  first_seen: 2026-10-15
modes: [nonstream, stream]
repeats: 20                   # statistical kinds only
request:                      # canonical, OpenAI-chat flavoured; adapters translate
  messages:
    - {role: system, content: "You are a helpful assistant. Use tools to complete the task."}
    - {role: user, content: "Set automation 23-00 to mode view. Call the tool."}
  tools:
    - $ref: fixtures/tools/automation_update_oneof.json
  tool_choice: auto
  max_tokens: 300
  params: {thinking: {type: enabled}, reasoning_effort: high}   # passed through verbatim
  overrides:                  # protocol/provider specific
    anthropic_messages: {max_tokens: 300}
variants:                     # same case, schema-shape A/B
  - id: flat
    tools: {$ref: fixtures/tools/automation_update_flat.json}
    expect_class: control
expect:                       # evaluated per endpoint class
  default:
    - check: http_status        ; equals: 200
    - check: finish_reason      ; in: [tool_calls]
    - check: tool_calls_valid   ; validators: [jsonschema_2020_12, jsonschema_draft7]
                                  # emits InvalidJson | UnknownName | SchemaMismatch
    - check: tool_arguments_nonempty_when_required
    - check: pass_rate          ; gte: 0.95
  api:
    - check: error_code         ; when: http_status!=200 ; in: ["1210","1214"]
tolerance:
  reference: official_api       # none | official_api | self_hosted
  margin: {metric: pass_rate, delta: -0.03}
notes: "Flat-schema control must pass; failure only for oneOf shape = schema-shape bug"
```

Checks catalog (runner-side, versioned): `http_status`, `error_code`, `error_shape`, `no_internal_leak` (regexes for Python exceptions), `finish_reason`, `tool_calls_valid` (3-way OpenRouter taxonomy + both JSON Schema drafts), `tool_arguments_nonempty_when_required`, `tool_arg_typed_equal`, `string_roundtrip_equal`, `no_marker_leak` (list of markers over content and reasoning), `stream_equals_nonstream`, `stream_fragments_min`, `sse_terminates`, `usage_present`, `usage_equals_golden`, `usage_ratio_within`, `cached_tokens_gt`, `model_field_equals_request`, `reasoning_present`/`absent`, `reasoning_degeneracy_lt`, `cjk_rate_lt`, `ttft_delta_lt`, `self_agreement_gte`, `mmd_pvalue_gte`, `pass_rate` (Wilson lower bound), `percent_of_reference_ci`.

Result record (JSONL, one per attempt; summaries derived):

```json
{"run_id":"2026-10-26T..","case_id":"TOOL-006","variant":"oneof","provider":"fireworks","endpoint_class":"third_party",
 "model_requested":"accounts/fireworks/models/glm-5p3","model_returned":"...","protocol":"openai_chat","harness_profile":"codex_mcp",
 "mode":"stream","attempt":3,"http_status":200,"error":null,"finish_reason":"tool_calls",
 "tool_calls":[{"name":"automation_update","arguments":"{}","valid":false,"error":"SchemaMismatch"}],
 "usage":{"prompt_tokens":812,"completion_tokens":57,"cached_tokens":0},"ttft_ms":3120,"checks":[{"name":"tool_calls_valid","pass":false}],
 "throttled":false,"raw_ref":"blob://sha256/..."}
```

Scoring recommendation: per provider x model x harness x endpoint-class report (1) hard-gate verdict (all critical deterministic checks), (2) per-category pass rate with Wilson 95% CI, (3) percent-of-reference with CI for statistical/fingerprint kinds, (4) K2VV-compatible F1 and schema accuracy for continuity, (5) cost of failures (tokens burned per retry loop), (6) throttled/unscored share. Do not use rank fusion.

Output artifacts: `results.jsonl`, `summary.json`, `junit.xml`, a provider card (markdown + JSON) with engine versions where self-hosted, key class, date, and the official-API noise floor used.

---------------------------------------------------------------------------------------------

## 7. Open items

- Fetch rate-limit page and z.ai/blog/glm-5.3 in a browser.
- Verify the Plan-class `thinking: disabled -> low` claim live (docs vs cc-switch observation on bigmodel.cn Anthropic path).
- Responses API (`/api/v1`) GLM wire behaviour untested.
- Read GLM-5#146, #163 (referenced in #142/#168), vllm#56583/#56605/#54359, canitoolcall GLM fixtures (`fixtures/glm/`).
- Provider-specific research for Bedrock, Mistral, Together, Nebius, DeepInfra, Fireworks beyond issues seen.
- Read full texts of the fingerprinting papers (only abstracts and one search summary used) and Chauvin et al. logprob tracking.
