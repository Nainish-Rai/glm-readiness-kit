# 13 · FDE workbench + demo golden sets

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/13-fde-workbench

## What to build

The day-1 experience for a forward-deployed engineer. `glmkit init <customer>` scaffolds an engagement workspace,
and four demo golden sets let anyone run a realistic migration evaluation in minutes.

- `glmkit init acme`. It creates an engagement folder containing:
  - an engagement brief template (use cases, success metrics, constraints, data residency needs);
  - target profiles to fill in;
  - a golden-set template with grader config;
  - a `Makefile`-style task list (`conform`, `eval`, `cost`, `evidence`, `report`);
  - a README that walks the 90-day path: week 1 conformance + evals, then pilot, then rollout.
- Demo golden sets, licenses as checked in `research/03-eval.md` §5:
  - **Banking77** intent classification (CC BY 4.0, exact grader);
  - a **CUAD** contract-clause slice (CC BY 4.0, judge + span grader);
  - **text-to-SQL** (gretelai, Apache-2.0, SQL result grader with a bundled SQLite DB);
  - **tau2-bench retail** subset (MIT, tool-use grader).
  Each has 50–100 items, a dataset card with source, license and attribution, and task-type labels. Datasets are
  downloaded by a fetch command with checksums, not committed.
- `glmkit demo run <set> --targets …` runs the full pipeline on the mock (or real targets) and builds the dashboard
  in one step.
- Dashboard: an **Engagement** tab showing the brief, the checklist of steps done/pending (from the workspace
  state), and links to each component's tab.

## Acceptance criteria

- [x] `glmkit init acme && cd acme && <one command>` produces a populated dashboard against the mock in under
      2 minutes on a laptop (evidence: timing output).
- [x] Each demo set's fetch verifies checksums and its dataset card states the license; a test fails if a card is
      missing a license.
- [x] The SQL grader runs against the bundled SQLite DB with a correct and an incorrect query (both outcomes tested).
- [x] Engagement tab renders; token check passes; no console errors at 1440/390 px.

## Blocked by

- 10 Migration eval engine

## Evidence

All commands run from `/Users/nainish/development/glm-kit-wt/13-fde-workbench` on 2026-10-26 (machine load average was
~500 from parallel agents, so timings are pessimistic).

### 1. `glmkit init acme && cd acme && glmkit demo run` (offline, mock): 40.5 s wall

```text
$ time (glmkit init acme && cd acme && glmkit demo run)
data:    banking77=stand-in (40 items), cuad=stand-in (44 items), tau2-retail=stand-in (30 items), text2sql=stand-in (44 items)
         stand-in = small synthetic items bundled with the kit; run `glmkit demo fetch all` for the licensed sets
targets: MOCK server with planted quality (baseline demo-incumbent; demo-glm-equal, demo-glm-worse, demo-glm@low/max); judge: mock
[conform] ok  7.6s   verdict: pass  (57 passed, 0 failed, 0 soft, 13 skipped, 0 errors)
[eval] ok  25.7s   set=demo-all (158 items)  ... ALL demo-glm-equal non_inferior | demo-glm-worse inferior | @low, @max non_inferior
[cost] ok  1.2s / 0.6s (synthetic traces, said so)   [evidence] ok  1.0s   [report] ok  1.3s  tabs=engagement,runs,conformance,errors,agent_loop,migration,cost,deploy,evidence
mode that ran: data stand-in; targets mock; 38.3s total
real 0m40.5s
```
A first run on this loaded machine, before tuning, took 77.7 s. With all four sets fetched (250 items, mock targets) the same command took 91.6 s (eval 84.9 s).

### 2. Fetch with checksums, real network (`glmkit demo fetch all`)

All four downloads verified sha256 against the pinned values and built 80 / 60 / 50 / 60 items (banking77 / cuad / tau2-retail /
text2sql). Example lines: `banking_data/test.csv: 239961 bytes, sha256 ok (d12d6e3bc4c3...)`,
`data.zip: 18309308 bytes, sha256 ok (f8161d18bea4...)`. A tampered download is rejected and removed
(`test_fetch_rejects_a_tampered_download_and_leaves_nothing`). Demo run on the fetched sets (mock targets): `data: banking77=fetched (80 items, rev 57ec275d8078), cuad=fetched (60 ...)`.

### 3. License-card test fails on a planted card without a license

```text
$ glmkit demo cards --dir planted/        # banking77.yaml with `license:` removed
FAIL banking77.yaml: no license stated
FAIL banking77.yaml: license: Field required        (exit 1)
$ glmkit demo cards                       # shipped cards
ok   banking77    CC-BY-4.0    verified 2026-10-26  rev 57ec275d80  1 file(s) pinned   ... (4 ok, exit 0)
$ pytest tests/test_workbench_sets.py::test_every_shipped_card_is_valid_and_states_a_license   # license block deleted from cuad.yaml
FAILED ... Left contains 2 more items, first extra item: 'cuad.yaml: no license stated'   (card restored afterwards)
```

### 4. SQL grader, correct and incorrect query (`sql_db`, bundled `shop.sqlite`, read-only)

`test_sql_db_grader_correct_query_passes` (`SELECT COUNT(id) ... WHERE country='DE'` vs reference: pass, score 1.0),
`test_sql_db_grader_incorrect_query_fails` (`SELECT COUNT(*) FROM customers`: fail, "1 rows vs 1 expected"-style detail, score 0.0),
`test_sql_db_grader_invalid_and_destructive_queries_are_wrong_not_crashes` (`DROP TABLE` fails, database unchanged), and the stock `sql` grader with per-item context for the gretel items.

### 5. Dashboard

Token check fails once on a planted raw colour, then passes:
```text
$ glmkit report check site-planted/      (".eg-brief h4 { color: #ff0000; }" injected)
site-planted/index.html:82 raw-color #ff0000
token check: 1 finding(s)
$ glmkit report check site/
token check: 0 finding(s)
```
Headless Chrome smoke (`tools/site-smoke/smoke.mjs`, `CHROME_PATH=/Applications/Google Chrome.app/...`, 1440 and 390 px, every tab, drawers):
`ok: true`, `errors: []` at both widths, `overflowX: 0` at 390. Screenshots `engagement-1440.png` and `engagement-390.png` were looked at: KPI strip, checklist with
done/pending pills and "open <tab>" buttons, dashed mock-target card, brief, component links; one KPI and the tab badge showed `NaN` for string values on the first build and were fixed (numeric values only).

### 6. Test and lint

`uv run pytest -q`: all passed (1 skipped, pre-existing). `uv run ruff check`: All checks passed. `ruff format --check` on the new files: clean.

### Dataset license verification (2026-10-26, sources in the cards)

| Set | License | Verified at |
|---|---|---|
| Banking77 | CC-BY-4.0 | HF API card @90d4e2ee and GitHub API/LICENSE of PolyAI-LDN/task-specific-datasets @57ec275d |
| CUAD | CC-BY-4.0 | HF API card @6d9a118f and `cuad-qa.py` text. NOT confirmed at the GitHub source: TheAtticusProject/cuad has no LICENSE file |
| text2sql (gretelai) | Apache-2.0 | HF API card and README front matter @740ab236 |
| tau2-bench retail | MIT | GitHub API and LICENSE @0c69df45 |

Notes: the tau2 set is a single-turn first-tool-call slice (50 of 114 retail tasks), not the multi-turn benchmark (no user simulator, no pass^k). Reading the gretel parquet needs the optional extra `glmkit[datasets]` (pyarrow). Card metadata is not legal review.
