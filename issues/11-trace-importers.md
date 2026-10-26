# 11 · Trace importers

Status: blocked · Type: AFK · Label: ready-for-agent · Branch: issue/11-trace-importers

## What to build

`glmkit traces import --from <kind> <path> -o traces.jsonl` converts a customer's existing production data into
SwitchTrace v0, so it can be replayed by the eval engine and analysed by cost forensics. Field-level details and
known traps are in `research/03-eval.md` §2.

- Adapters:
  - **OTel GenAI** spans (pin the semantic-conventions commit; content is opt-in, so spans without content become
    baseline-only rows);
  - **Langfuse** export (JSONL/CSV);
  - **OpenAI** chat completion logs;
  - **Anthropic** Messages logs (convert `input_tokens`, which *excludes* cache, to the total-including-cache
    convention);
  - **LiteLLM** spend logs (cost truth; often no prompts);
  - **Claude Code** session JSONL (one API response spans several lines: dedupe by `message.id`, mark
    `isSidechain` as subagent traffic, fail soft on unknown line types).
- Optional redaction pass (`--redact`): emails, phone numbers, API-key-like strings, and custom regexes.
- Task-type assignment: from source tags/metadata, a mapping file, or `unlabelled`.
- `glmkit traces stats` prints counts by source, task type, replayable vs baseline-only, and token totals.
- Dashboard: an **Traces** tab showing imported sets, replayable share (composition bar), task-type mix (bar list)
  and token totals by source.

## Acceptance criteria

- [ ] Each adapter has fixture files (synthetic, no real customer data) and golden output tests.
- [ ] Anthropic and Claude Code usage normalisation is proven by a test where the naive sum over-counts and the
      adapter does not.
- [ ] Redaction removes planted secrets and PII in a fixture; a test fails if any survive.
- [ ] Output validates against the SwitchTrace v0 schema from slice 01.
- [ ] Traces tab renders; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
