# 16 · Live verification run

Status: blocked · Type: HITL · Label: needs-human · Branch: issue/16-live-verification

## What to build

Run the conformance suite against real endpoints and turn the claims the research could not verify into recorded
facts.

**Needs the owner:**
- API keys in the environment for Z.ai API, Z.ai Coding Plan, OpenRouter, and optionally Bedrock or Mistral;
- a spend cap;
- approval of which hosts to probe.

- Probe the unverified items:
  - 1210 on `thinking: disabled`;
  - the effort enum;
  - zero `message_start` usage;
  - tokenizer ratios;
  - oneOf → `{}`;
  - Cerebras rejecting `reasoning_content`;
  - whether GLM-5.3 reports reasoning tokens separately;
  - TTFT penalty with tools.
- Record sanitised request/response fixtures from the real API (keys and content redacted). Use them to tighten the
  mock so its default behaviour matches reality, and file any mismatch as a mock bug.
- Record a reference fingerprint from the official API (slice 05).
- Run the scorecard across the agreed hosts and build the dashboard.

## Acceptance criteria

- [ ] Each previously unverified claim is marked confirmed / refuted with the run id that shows it.
- [ ] Mock defaults updated where reality differed; the full test suite still passes.
- [ ] Total spend reported and within the cap.
- [ ] No key or customer content in committed fixtures (secret scan clean).

## Blocked by

- 02, 03, 04, 05

## Evidence
