# 03 · Conformance: wire contract, errors, Anthropic endpoint

Status: blocked · Type: AFK · Label: ready-for-agent · Branch: issue/03-conform-wire-anthropic

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

- [ ] All listed cases exist with provenance and per-class expectations.
- [ ] Each deterministic case fails on its planted defect and passes without it (evidence included).
- [ ] The error taxonomy covers every code in the research table and has unit tests; codes not in the table are
      reported as `unknown`, never dropped.
- [ ] A hanging stream is detected within a configurable timeout, never hangs the runner.
- [ ] Errors tab renders the mock run; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
