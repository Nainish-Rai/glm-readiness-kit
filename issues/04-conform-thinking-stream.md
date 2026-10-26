# 04 · Conformance: thinking, streaming, cache, language

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/04-conform-thinking-stream

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

- [ ] All listed cases exist with provenance; soak cases are excluded unless `--soak` is passed and stop at a
      token budget.
- [ ] Each deterministic case fails on its planted defect and passes without it (evidence included).
- [ ] Chunk-split fixtures prove the stream reassembler is order- and split-independent.
- [ ] CJK detector excludes text quoted from inputs (unit tests with mixed-language fixtures).
- [ ] Agent loop tab renders the mock run; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
