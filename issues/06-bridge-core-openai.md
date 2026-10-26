# 06 · Bridge core + OpenAI-compatible proxy

Status: blocked · Type: AFK · Label: ready-for-agent · Branch: issue/06-bridge-core-openai

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

- [ ] An OpenAI Python SDK client pointed at the bridge completes a tool-calling loop against a defective mock
      (forced-thinking, oneOf-empty-args, broken-JSON args) with correct results (evidence: direct run fails,
      bridged run passes).
- [ ] Streaming through the bridge preserves event order; per-index argument buffers are correct under chunk splits
      (property-based test).
- [ ] Retry-with-parse-error happens at most once per call and is visible as a fix event.
- [ ] No request or response content is logged unless `--log-content` is set; auth headers are never logged.
- [ ] Bridge tab renders a mock session; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
