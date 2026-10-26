# GLM Production Readiness Kit: issue board

Local tracker. One file per issue; the **Status** line in each file and the table below are the source of truth.
Statuses: `ready` → `in-progress` → `review` → `done` (or `blocked`). Labels: `ready-for-agent` (AFK) / `needs-human` (HITL).

| # | Issue | Type | Blocked by | Status |
|---|---|---|---|---|
| 01 | [Foundation tracer bullet](01-foundation.md) | AFK | none | done |
| 02 | [Conformance: tool-call cases + scorecard](02-conform-tools.md) | AFK | 01 | done |
| 03 | [Conformance: wire, errors, Anthropic endpoint](03-conform-wire-anthropic.md) | AFK | 01 | done |
| 04 | [Conformance: thinking, streaming, cache, language](04-conform-thinking-stream.md) | AFK | 01 | done |
| 05 | [Conformance: latency + downgrade fingerprinting](05-conform-latency-fingerprint.md) | AFK | 01 | done |
| 06 | [Bridge core + OpenAI-compatible proxy](06-bridge-core-openai.md) | AFK | 01 | done |
| 07 | [Bridge: Anthropic Messages path](07-bridge-anthropic.md) | AFK | 06 | done |
| 08 | [Bridge: LiteLLM plugin adapter](08-bridge-litellm.md) | AFK | 06 | done |
| 09 | [Spend and loop guard](09-guard.md) | AFK | 06 | done |
| 10 | [Migration eval engine](10-eval-engine.md) | AFK | 01 | done |
| 11 | [Trace importers](11-trace-importers.md) | AFK | 01 | done |
| 12 | [Cost and cache forensics](12-cost-forensics.md) | AFK | 01 | done |
| 13 | [FDE workbench + demo golden sets](13-fde-workbench.md) | AFK | 10 | done |
| 14 | [Deploy: sizing calculator + reference deployment](14-deploy.md) | AFK | 01 | done |
| 15 | [Evidence pack generator](15-evidence-pack.md) | AFK | 01 | done |
| 16 | [Live verification run](16-live-verification.md) | HITL | 02, 03, 04, 05 | blocked |
| 17 | [Public readiness report + launch](17-public-report.md) | HITL | all | blocked |
| 18 | [Unify the price table](18-unify-prices.md) | AFK | 10, 12, 14 | done |
| 19 | [Unify the Z.ai error taxonomy in the bridge](19-unify-errors.md) | AFK | 03, 06, 07, 08, 09 | done |

## Conventions every issue follows

**Product.** An open-source kit a forward-deployed engineer runs in the first weeks of a GLM enterprise engagement:
conformance (does GLM work on this host and harness), bridge (make it work), eval (should this workload move, at
which effort level, on which host, at what cost), guard, deploy and evidence. Background: `../research/*.md` and
`/Users/nainish/development/zai-research/REPORT.md`.

**Stack.** Python 3.12+, `uv`, one package `glmkit` under `src/`, `typer` CLI, `httpx`, `pydantic` v2, `pytest`,
`ruff`. MIT license. No secrets in code, fixtures or logs; API keys only from environment variables.

**Every slice is vertical.** It ships the CLI command, the engine, JSON results in the shared result envelope, a
dashboard tab, and tests. A slice is done when you can run its command against the mock server and see its tab
render the result.

**Ownership, to keep parallel work conflict-free.** Each slice adds files in its own area and touches shared files
only by appending a registration line:
- its own subpackage (`glmkit/<area>/`), its own CLI sub-app (registered in the CLI registry), its own conformance
  cases folder and check module, its own mock-server defect module, its own dashboard tab (page module + data
  file discovered by the report builder);
- shared contracts (case schema, result envelope, trace schema, endpoint profiles) are owned by 01. Later slices
  may only add optional fields; any breaking change is raised on the issue as a blocker instead.

**Mock-first verification.** The mock GLM server (from 01) reproduces documented GLM behaviour and can switch on
each known upstream bug as a named defect. Every check must be shown to fail against its planted defect and pass
without it; put both runs (command + output) in the issue's Evidence section. That proof is evidence, not an extra
test: keep one regression test per behaviour.

**Dashboard.** Built with Observatory UI (`.observatory-ref/`, from github.com/Nainish-Rai/observatory-ui; 01
vendors the design-system files). Static site, opens from disk (`components.iife.js`). Missing data reads
`not logged`, estimates use the dashed `heuristic` style. Run the design system's token check on every page you
add, after seeing it fail once on a planted raw colour.

**Git.** Each issue is built on its own branch `issue/NN-slug` in its own worktree; commits end with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Nothing is pushed to a remote without the owner's approval.

**Done.** Acceptance criteria checked, `uv run pytest` and `uv run ruff check` green, the Evidence section filled
with the exact commands run and their output, Status set to `review`.
