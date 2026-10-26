# 02 · Conformance: tool-call cases + provider scorecard

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/02-conform-tools

## What to build

The tool-calling part of the GLM vendor verifier, end to end. Tool-calling is the most frequently reported failure
area (see `research/01-conformance.md` §3, §5; `zai-research/sources/github.md`).

- Cases TOOL-001 … TOOL-015 from `research/01-conformance.md` §5:
  - basic call and irrelevance;
  - parallel calls;
  - typed args;
  - string fidelity: a 20 KB `write_file` content containing `</tool_call>`, quotes and backslashes;
  - schema shapes, with **TOOL-006** for the GLM-5#169 top-level `oneOf`/empty-`properties` → `{}` args bug;
  - 128/129 tools;
  - namespaced tool names (`mcp__server__tool`);
  - round trip with string `arguments` and `content: null` (GLM-5#158);
  - tool-call markers leaking into content (opencode#6708);
  - unique ids;
  - forced need at long context;
  - command-prefix emission fidelity (GLM-5#116);
  - trigger F1.
- Statistical cases run N repeats and report a pass rate with a Wilson 95% CI. **Critical cases are hard gates**:
  they cannot be averaged away. Validate JSON Schema against Draft 2020-12 and Draft 7 and report any disagreement.
  Classify errors with OpenRouter's taxonomy (InvalidJson / UnknownName / SchemaMismatch).
- An input adapter that replays K2-Vendor-Verifier / Prism-style JSONL corpora and computes trigger F1 and schema
  accuracy, so results stay comparable with Moonshot's verifier.
- Mock defects for each deterministic case:
  - oneOf → `{}`;
  - dict-only template rejecting string args;
  - `</tool_call>` truncation;
  - marker leak;
  - duplicate ids;
  - unknown tool name.
- Dashboard: a **Scorecard** tab with a provider × category heat strip, per-category pass rate with CI, hard-gate
  failures on top, and click-through to a case's request and response in the drawer.

## Acceptance criteria

- [ ] All 15 cases exist as data files with provenance (source issue URL) and expected behaviour per endpoint class.
- [ ] Every deterministic case fails against its planted mock defect and passes without it (evidence included).
- [ ] Statistical cases report pass rate + Wilson CI; a critical failure makes the run verdict `fail` regardless of
      the other cases.
- [ ] K2VV-format JSONL import works on a 20-line sample and reports F1 and schema accuracy.
- [ ] Scorecard tab renders the mock run for at least 2 mock "providers" (clean vs defective), with no console
      errors at 1440/390 px; token check passes.

## Blocked by

- 01 Foundation

## Evidence
