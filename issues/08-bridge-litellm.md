# 08 · Bridge: LiteLLM plugin adapter

Status: blocked · Type: AFK · Label: ready-for-agent · Branch: issue/08-bridge-litellm

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

- [ ] `litellm --config` example with the plugin fixes forced-thinking, effort-clamp and schema-flatten defects
      on the mock (direct fails, via LiteLLM+plugin passes; evidence included).
- [ ] The test suite pins a LiteLLM version, and a CI job runs the integration tests.
- [ ] Docs table of fix availability per adapter is accurate to the tests.
- [ ] No content logged by default.

## Blocked by

- 06 Bridge core + OpenAI-compatible proxy

## Evidence
