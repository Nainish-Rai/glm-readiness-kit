# 07 · Bridge: Anthropic Messages path (Claude Code ready)

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/07-bridge-anthropic

## What to build

Make the bridge serve `/v1/messages` and `/v1/messages/count_tokens` so Claude Code and the Anthropic SDK work on
GLM through it. The upstream can be either Z.ai's Anthropic-compatible endpoint or any OpenAI-compatible GLM host,
in which case the bridge translates Anthropic Messages to and from OpenAI chat. See `research/02-bridge.md` §6 for
the shapes.

- Anthropic ↔ OpenAI translation:
  - thinking blocks;
  - signatures;
  - `tool_use`/`tool_result`;
  - streaming event sequence (`message_start`, `content_block_*`, `message_delta`, `message_stop`).
- Fixes on this path:
  - thinking rewrite;
  - inject real usage into `message_start` (GLM-5#139);
  - repair a `tool_use.input` sent as an array before it reaches upstream (zai-coding-plugins#14);
  - default `max_tokens` when missing (#15);
  - `count_tokens` corrected with a calibrated estimator so Claude Code's context meter matches upstream
    accounting (#12);
  - emulate the server-side `web_search` tool with a pluggable search backend, or strip it cleanly when none is
    configured.
- Smoke test: a scripted Claude-Code-shaped session against the mock, covering helper requests with disabled
  thinking, a tool loop, a malformed history entry and `/compact`. It must complete without errors via the bridge
  and fail direct.
- Fix events and dashboard reuse slice 06's Bridge tab: add the Anthropic-path fix types and a protocol filter.

## Acceptance criteria

- [x] The Anthropic Python SDK completes a streaming tool loop through the bridge against both mock upstream types.
- [x] Recorded Claude-Code-shaped session passes via bridge and fails direct (evidence included).
- [x] `count_tokens` estimate is within 5% of the mock's accounting on the 10-corpus set from slice 03 (or a local
      copy).
- [x] Translation is covered by golden request/response pairs, including streaming.
- [x] Bridge tab shows Anthropic-path fixes with a protocol filter; token check passes.

## Blocked by

- 06 Bridge core + OpenAI-compatible proxy

## Evidence

Commands: `uv run pytest -q` (all green), `uv run ruff check` (clean), `uv run python tools/bridge-proof/anthropic_proof.py --out /tmp/bd`
(exit 0). Per-defect direct-fails/bridged-passes is also a regression test for both upstream types
(`tests/test_bridge_anthropic.py::test_each_defect_alone_breaks_direct_and_is_fixed_bridged`).

```

######## upstream: anthropic (mock-anthropic)
== Claude-Code-shaped session, all four Anthropic defects planted
  direct (no bridge):
    FAIL helper-title: HTTP 400 该模型始终思考，不支持关闭思考；请使用 low、high 或 max。
    FAIL helper-summary: HTTP 400 该模型始终思考，不支持关闭思考；请使用 low、high 或 max。
    PASS tool-loop-stream: tools=['Bash'] final='Done. The tool returned: README.md\nsrc'
    FAIL message-start-usage: message_start input tokens per stream: [0, 0]
    FAIL malformed-history: HTTP 500 Internal server error
    FAIL missing-max-tokens: HTTP 500
    FAIL compact: HTTP 400 该模型始终思考，不支持关闭思考；请使用 low、high 或 max。
    FAIL context-meter: count_tokens=9462 billed=5566 off=70.0%
  via the bridge:
    PASS helper-title: stop=end_turn text='a'
    PASS helper-summary: stop=end_turn text='a'
    PASS tool-loop-stream: tools=['Bash'] final='Done. The tool returned: README.md\nsrc'
    PASS message-start-usage: message_start input tokens per stream: [108, 177]
    PASS malformed-history: stop=end_turn
    PASS missing-max-tokens: HTTP 200
    PASS compact: 57 messages, summary='Mock GLM reply: Your task is to create a'
    PASS context-meter: count_tokens=5416 billed=5566 off=2.7%
== Anthropic SDK tool loop (thinking: adaptive)
  nonstream via the bridge: PASS ['sunny in Paris']
  stream    via the bridge: PASS ['sunny in Paris']
== count_tokens on 10 corpora (reference = the same mock without the defect)
  corpus           reference   direct    off   bridge    off
  json-flat             3700     6290  70.0%     3797   2.6%
  json-nested           3445     5856  70.0%     3535   2.6%
  python-code           1803     3065  70.0%     1849   2.6%
  typescript-code       2033     3456  70.0%     2085   2.6%
  english-prose         1865     3170  70.0%     1913   2.6%
  cjk-chinese           1358     2309  70.0%     1393   2.6%
  cjk-japanese          1263     2147  70.0%     1295   2.5%
  special-chars         2115     3596  70.0%     2170   2.6%
  markdown-table        2398     4077  70.0%     2460   2.6%
  mixed-log             2140     3638  70.0%     2196   2.6%
== web_search emulation (offline fake backend)
  blocks: ['thinking', 'server_tool_use', 'web_search_tool_result', 'thinking', 'text'] searches: 1
== fix events: 23  (anthropic-count-tokens, anthropic-max-tokens-default, anthropic-tool-input-repair, anthropic-usage-inject, anthropic-web

######## upstream: openai (mock)
== Claude-Code-shaped session, all four Anthropic defects planted
  ... (the OpenAI-compatible upstream run repeats the same table: session, loop, count_tokens off 2.5-2.6%)
```

Notes on the proof: the three PASS/FAIL "direct" failures on helper-title, helper-summary and compact are the mock's
documented behaviour (1210 on `thinking: disabled` for glm-5.3 on the API class), fixed by `thinking-disabled-to-low`.
`missing-max-tokens` fails direct with 400 on a clean mock and 500 with the defect.

Dashboard: `glmkit report build /tmp/bd --out /tmp/site7` -> `token check: 0 finding(s)`. With `.x{color:#ff0000}` planted in
the built page: `/tmp/site7_planted/index.html:88 raw-color #ff0000`, `token check: 1 finding(s)`, exit 1. Browser smoke
(`tools/site-smoke/smoke.mjs`, Chrome headless, 1440 and 390 px, all 4 tabs, drawers): `"errors": []` for both widths,
`overflowX: 0`, `"ok": true`. Screenshots looked at: Bridge tab with protocol filter (All / OpenAI chat / Anthropic
Messages), Protocol column, top fixes listing the `anthropic-*` ids, KPI strip following the filter.

Unverified: behaviour against the real Z.ai Anthropic endpoint and real Claude Code (mock-only; assumed shapes of
Z.ai's 500s are the mock's, see issue 16); count_tokens accuracy against a real GLM tokenizer (the calibrator learns it
from usage, the 5% result is against the mock's accounting); the `clear_thinking:false` replay flag is untested upstream.

### Follow-up after merging main (context meter 5.4% under, and the per-defect tests)

Cause: issue 04's mock counts replayed `reasoning` toward prompt tokens only when the request carries
`thinking.clear_thinking: false` (THINK-004: the default clears earlier turns' reasoning). The estimator counted every
`thinking` block in the history, so on the Claude Code session (thinking blocks replayed, no clear_thinking) it was off,
and the refit constants could not follow because the difference depends on the request, not on a constant.

Fix: the estimator models it. `tokens.replay_preserved(body, wire, default)` decides whether the upstream will count
replayed reasoning: an explicit `thinking.clear_thinking` wins; on the translated OpenAI wire the bridge itself sets
`clear_thinking: false` when thinking is enabled/adaptive, so it counts; otherwise the endpoint default, which is the new
`UpstreamProfile.preserved_thinking` (True for `zai-plan`, False elsewhere, per the docs: preserved thinking on by default
on the Coding Plan, off on the standard API). `features(body, preserved)` leaves thinking text and thinking blocks out of
the count when they are not preserved. Tolerance stays 5%. New tests: `test_count_tokens_models_how_replayed_reasoning_is_counted`
(default / clear / preserved, both upstreams, against the mock's own count_tokens) and
`test_preserved_reasoning_changes_the_estimate_and_plan_defaults_to_preserved`.

The four failing `test_each_defect_alone...` tests (anthropic upstream) failed for the same reason, through the session's
context-meter step; they pass with the fix.

Results on the merged branch: `uv run ruff check` clean, full `uv run pytest -q` all passed (no F), proof
`anthropic_proof.py` exit 0: context-meter `count_tokens=5455 billed=5566 off=2.0%` (Anthropic upstream) and
`5430 vs 5587, 2.8%` (OpenAI upstream); direct run still 70.0% off.
