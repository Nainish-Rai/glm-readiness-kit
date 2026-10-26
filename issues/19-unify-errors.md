# 19 · Unify the Z.ai error taxonomy in the bridge

Status: blocked · Type: AFK · Label: ready-for-agent · Branch: issue/19-unify-errors

## What to build

The bridge core (06) shipped its own error-code table behind an `ErrorClassifier` protocol; conformance slice 03
shipped the canonical taxonomy (`glmkit.zai_errors`: `classify()`, `retry_after_hint()`). Make the bridge's
classifier use the canonical taxonomy so a code means the same thing in conformance results, bridge responses,
the LiteLLM plugin and the guard.

- Bridge classifier delegates to `glmkit.zai_errors`; delete the duplicate table. Keep `next_flush_time` →
  `Retry-After` behaviour if the canonical module lacks it, by adding it there.
- Where the two tables disagreed on a code's status/retryability, pick the one backed by the docs table in
  `research/01-conformance.md` §2.5 and record the decision in Evidence.

## Acceptance criteria

- [ ] One code table in the package; a test fails if a second mapping of Z.ai business codes appears.
- [ ] Bridge proof (`tools/bridge-proof/proof.py`) and all bridge, LiteLLM and guard tests still pass.
- [ ] `uv run pytest -q` and `uv run ruff check` green.

## Blocked by

- 03, 06, 07, 08, 09

## Evidence
