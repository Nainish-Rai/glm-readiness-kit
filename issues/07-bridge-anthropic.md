# 07 · Bridge: Anthropic Messages path (Claude Code ready)

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/07-bridge-anthropic

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

- [ ] The Anthropic Python SDK completes a streaming tool loop through the bridge against both mock upstream types.
- [ ] Recorded Claude-Code-shaped session passes via bridge and fails direct (evidence included).
- [ ] `count_tokens` estimate is within 5% of the mock's accounting on the 10-corpus set from slice 03 (or a local
      copy).
- [ ] Translation is covered by golden request/response pairs, including streaming.
- [ ] Bridge tab shows Anthropic-path fixes with a protocol filter; token check passes.

## Blocked by

- 06 Bridge core + OpenAI-compatible proxy

## Evidence
