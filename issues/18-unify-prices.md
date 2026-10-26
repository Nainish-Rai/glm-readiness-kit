# 18 · Unify the price table

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/18-unify-prices

## What to build

Three slices merged with their own prices: cost forensics (12) owns the canonical versioned table
(`glmkit.cost.prices`), while the eval engine (10) ships its own `prices.yaml` read through
`PriceTable.cost_usd(model, provider, usage)` + `version`, and the deploy calculator (14) carries its own API
price (from an aggregator) for break-even maths. Make 12's table the only source of model/API prices.

- Extend the canonical price API so it serves the eval engine's needs (`cost_usd` for a usage record, `version`),
  including the mock-only models the eval tests rely on (marked mock, never shown as real prices).
- Point the eval engine and the deploy calculator's API-price comparison at it; delete their private API price
  data. GPU prices and throughput calibration in deploy stay where they are (they are not model API prices).
- Where the three tables disagreed on a real price, keep the entry with the better primary source (URL + date) and
  note the discrepancy in the issue's Evidence.
- The Migration, Cost and Deploy tabs keep rendering with identical numbers for unchanged prices.

## Acceptance criteria

- [x] `grep` shows one model-price data file in the package; a test fails if a second appears.
- [x] Eval, cost and deploy tests pass unchanged in meaning (expected values updated only where a real price changed,
      each change listed in Evidence with its source).
- [x] Every price entry has a source URL + retrieved date (existing enforcement still passes).
- [x] `uv run pytest -q` and `uv run ruff check` green.

## Blocked by

- 10, 12, 14 (all merged)

## Evidence

Built: the one table is `src/glmkit/cost/prices.yaml`. `eval/prices.yaml` deleted; `eval/pricing.py` is a thin view over
`glmkit.cost.prices` (`cost_usd`, `version`, `describe`, `--prices` overlay in the canonical entry format);
`deploy/data/catalog.json` keeps GPU prices + calibration and only an `api_hosts` name -> table-host map, `load_catalog()`
builds `apis` from the table. Additive API: `PriceEntry.title/secondary/mock`, `PriceTable.cost_usd`,
`lookup_for_provider`, `with_overlay`; `models()` now excludes mock entries. Mock incumbent = `host: mock, mock: true`
entry (flagged MOCK in the Migration tab and `glmkit eval prices`). `glmkit eval demo` no longer writes `prices.yaml`.
Guard: `tests/test_cost.py::test_exactly_one_model_price_file_in_the_package` (+ deploy-from-table test).
docs/contracts.md sections 10 and 13 (9.5 "Prices") updated in place.

### 1. One price file, guard fails on a planted second file

```text
$ grep -rl cached_input src --include='*.yaml' --include='*.json'
src/glmkit/cost/prices.yaml
$ printf 'prices:\n  - {model: glm-5.3, input: 1.4, cached_input: 0.26, output: 4.4}\n' > src/glmkit/eval/prices.yaml
$ uv run pytest -q tests/test_cost.py -k one_model_price
>       assert _price_files() == ["cost/prices.yaml"]
E       AssertionError: assert ['cost/prices.../prices.yaml'] == ['cost/prices.yaml']
$ rm src/glmkit/eval/prices.yaml && uv run pytest -q tests/test_cost.py -k one_model_price
.                                                                        [100%]
```

### 2. Numbers unchanged (same seed, before = pre-change commit, after = this branch)

- `glmkit eval run` demo (seed 7): result `data` identical apart from `price_table` rows (now only the entries the run
  used, plus a `mock` flag): all verdicts, costs, `spent $0.4410` the same.
- `glmkit cost analyze` on the same traces: all existing rows identical; the PAYG projection gains rows for the hosts
  moved into the table (mistral, mistral-eu, bedrock-global, crusoe, scaleway; scaleway cached price not logged).
- `glmkit deploy size` default: `$3.56/M self-hosted ... vs $1.73/M API (zai)`, 86% break-even vs bedrock-global,
  both before and after.
- Migration, Cost and Deploy tabs rebuilt from these runs (`glmkit report build`, token check 0 findings) and rendered in
  headless Chrome at 1440 px: all three render, no console errors (screenshots in the scratch folder i18/after/).

### 3. Discrepancies between the three tables and their resolution

| Price | Old | New | Source kept |
|---|---|---|---|
| deploy `zai` glm-5.3 cached input | not logged (null), source an aggregator (anotherwrapper.com, secondary) | 0.26 | https://docs.z.ai/guides/overview/pricing.md (primary, 2026-10-26) |
| deploy `zai` source URL | anotherwrapper.com | docs.z.ai | same as above |
| eval glm-5.3 / flash / flashx source URL | .../pricing (no `.md`) | .../pricing.md | same page, same 2026-10-26 |

No other real price changed: glm-5.3 1.40/0.26/4.40, flash 0.15/0.03/0.50, flashx 0.37/0.075/1.25 agreed in eval and
cost; Mistral 1.40/0.14/4.40 agreed in eval and deploy (one entry now). Deploy-only hosts (mistral-eu, bedrock-global,
crusoe, scaleway) moved unchanged with their sources and `secondary` flags. The cached-input change does not move the
default Deploy numbers (prefix reuse 0) but now applies when prefix reuse > 0.
Bedrock stays not logged in the plain `bedrock` host; `bedrock-global` keeps the secondary calculator price.

### 4. Suite

`uv run ruff check` -> All checks passed; `uv run pytest -q` -> all pass (1 pre-existing skip).
