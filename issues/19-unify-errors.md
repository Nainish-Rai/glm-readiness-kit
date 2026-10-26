# 19 · Unify the Z.ai error taxonomy in the bridge

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/19-unify-errors

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

- [x] One code table in the package; a test fails if a second mapping of Z.ai business codes appears.
- [x] Bridge proof (`tools/bridge-proof/proof.py`) and all bridge, LiteLLM and guard tests still pass.
- [x] `uv run pytest -q` and `uv run ruff check` green.

## Blocked by

- 03, 06, 07, 08, 09

## Evidence

### What changed

- `src/glmkit/bridge/core/errors.py`: `CODE_TABLE` deleted. `TableErrorClassifier` calls `glmkit.zai_errors.classify()` and
  maps the result to `ClassifiedError` (`status` = `suggested_http`, `kind` = canonical category, `error.type` from a
  category map, which is not a code table). `ErrorClassifier` protocol, `ClassifiedError` and the non-table fallbacks
  (no JSON body -> sanitized 502, unknown code -> upstream status) are unchanged. `seconds_until_flush` stays as a thin wrapper.
- `src/glmkit/zai_errors.py`: gained `next_flush_time` parsing (`flush_seconds`, also used by `retry_after_hint`) and
  `DEFAULT_RETRY_AFTER_S` (rate_limit 2, overloaded 10, usage_limit 300, the bridge's old defaults, now per category).
  Order of Retry-After sources is now header, then message hint (incl. `next_flush_time`), then default (was flush, header, default).
- `tests/test_one_error_table.py` (new guard), `tests/test_bridge_core.py`, `tests/test_zai_errors.py`, `docs/contracts.md`.
- The guard (`flows`, litellm adapter, Anthropic handler, proxy) held no code table of its own; they read `ClassifiedError`.

### Disagreements resolved (old bridge table -> canonical, decided by research/01-conformance.md §2.5)

| Code | Old bridge | New (canonical) | Deciding doc row |
|---|---|---|---|
| 1310 | 429, quota, retryable False, no Retry-After | 429, usage_limit, retryable True (wait_for_reset), Retry-After flush time or 300 | "1310 / 429 / weekly/monthly limit exhausted": same 429 family as 1308 (a window that resets); the docs give no non-retryable reading |
| 1313 | 429, rate_limit, retryable False | 429, usage_limit, retryable True (wait_for_reset) | "1313 / 429 / Fair Usage Policy limiting": throttling, not a hard stop |
| 1311 | 402, billing, billing_error | 403, permission, permission_error | "1311 / 429 / plan lacks access to model": an access problem, not a payment problem |
| 1315 | 402, billing, billing_error | 403, permission, permission_error | "1315 / 429 / key limited to enterprise coding scenario": access, not payment |
| 1319-1321 | not in bridge table (upstream status passed through) | 429, usage_limit, retryable | "1316-1321 / 429" row |
| 1200, 1230, 1234, 1212, 1215, 1220-1222, 1301, 1003, 1005 | not in bridge table (upstream status passed through) | per canonical (1234 -> 502, 1221/1222 -> 404, 1220 -> 403); retryable only for server codes | rows in §2.5 |
| 1305, 1308, 1316-1318, 1302, 1113, 1309, 1314, 1210, 1211, 1213, 1214, 1261, 1000, 1001 | | unchanged status and retryability | agree |

Side effect: `kind` now uses canonical category names (`usage_limit` for old `quota`, `context_length` for `too_long`,
`not_found` for the 1211 case). Nothing in src or tests read the old strings other than the log line in pipeline.py.

## Evidence

Guard test planted-failure proof (`tests/test_one_error_table.py::test_one_code_table_in_the_package`):

```
$ echo 'T = {"1302": 429, "1305": 503, "1308": 429}' > src/glmkit/bridge/core/_planted.py
$ uv run pytest -o addopts="" -q tests/test_one_error_table.py::test_one_code_table_in_the_package
E   AssertionError: assert ['bridge/core...siness codes'] == []
E     Left contains one more item: 'bridge/core/_planted.py:1 dict keyed by Z.ai business codes'
1 failed
$ rm src/glmkit/bridge/core/_planted.py && uv run pytest -o addopts="" -q tests/test_one_error_table.py
2 passed          (the second test plants and removes the file itself, so the detector stays tested)
```

Proofs (exit 0 each): `uv run python tools/bridge-proof/proof.py` (10 direct FAIL, all bridged PASS, 0 bridged FAIL),
`tools/bridge-proof/anthropic_proof.py` (23 fix events, web-search emulation ok), `tools/guard-proof/proof.py`
(all `ok`, audit chain verify ok, one-byte edit detected).

Suites:
- `uv run pytest -o addopts="" -m "not litellm" -q`: 741 passed, 1 skipped, 29 deselected in 431s.
- `uv run pytest -m litellm -q`: 29 passed, 0 failed (the repo's `-q` addopts hides the summary line; 29 dots).
- `uv run ruff check .`: All checks passed. `uv run ruff format --check`: clean.
