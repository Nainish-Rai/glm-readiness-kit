# 18 · Unify the price table

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/18-unify-prices

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

- [ ] `grep` shows one model-price data file in the package; a test fails if a second appears.
- [ ] Eval, cost and deploy tests pass unchanged in meaning (expected values updated only where a real price changed,
      each change listed in Evidence with its source).
- [ ] Every price entry has a source URL + retrieved date (existing enforcement still passes).
- [ ] `uv run pytest -q` and `uv run ruff check` green.

## Blocked by

- 10, 12, 14 (all merged)

## Evidence
