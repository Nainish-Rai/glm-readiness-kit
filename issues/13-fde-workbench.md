# 13 · FDE workbench + demo golden sets

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/13-fde-workbench

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

- [ ] `glmkit init acme && cd acme && <one command>` produces a populated dashboard against the mock in under
      2 minutes on a laptop (evidence: timing output).
- [ ] Each demo set's fetch verifies checksums and its dataset card states the license; a test fails if a card is
      missing a license.
- [ ] The SQL grader runs against the bundled SQLite DB with a correct and an incorrect query (both outcomes tested).
- [ ] Engagement tab renders; token check passes; no console errors at 1440/390 px.

## Blocked by

- 10 Migration eval engine

## Evidence
