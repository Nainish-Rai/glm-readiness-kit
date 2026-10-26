# GLM Production Readiness Kit (`glmkit`)

An open-source kit a forward-deployed engineer runs in the first weeks of a GLM enterprise engagement:
conformance (does GLM work on this host and harness), bridge (make it work), eval (should this workload move),
guard, deploy and evidence. Everything runs against a deterministic mock GLM server first, so every check is
proven to fail on its planted upstream bug before it touches a real endpoint.

## Quickstart

```sh
uv sync
uv run glmkit --help

# conformance against the in-process mock (no key, no network)
uv run glmkit conform run --target mock --cases 'WIRE-00*'

# the same with a planted upstream bug: WIRE-001 fails and the evidence shows the 500 + traceback
uv run glmkit conform run --target mock --cases 'WIRE-00*' --mock-defect forced-thinking-500

# static dashboard (opens from disk)
uv run glmkit report build results/ -o site/
open site/index.html
```

Real targets: `uv run glmkit profiles list` shows the shipped endpoint profiles (Z.ai API and Coding Plan on
both protocols, OpenRouter, a generic OpenAI-compatible host, local vLLM/SGLang, the mock). Keys are read only
from the environment variable each profile names (for example `ZAI_API_KEY`) and are redacted from every
result, trace and log.

The mock can also run standalone: `uv run glmkit mock serve --defect forced-thinking-500`
(`uv run glmkit mock defects` lists the defects).

## Layout

| Path | What |
|---|---|
| `src/glmkit/contracts/` | Shared contracts: endpoint profile, conformance case, result envelope, evidence, SwitchTrace v0 |
| `src/glmkit/client/` | Async client for OpenAI-chat and Anthropic-Messages, stream and non-stream, with TTFT and evidence |
| `src/glmkit/mock/` | Mock GLM server; `mock/defects/` holds one module per planted upstream bug |
| `src/glmkit/conform/` | Conformance runner; `conform/checks/` holds the named oracles |
| `src/glmkit/report/` | Static dashboard builder; `report/pages/` holds one module per tab |
| `cases/` | Conformance cases as YAML data (`cases/<category>/<ID>-slug.yaml`) |
| `schemas/` | Exported JSON Schemas of the contracts (`glmkit schema export`) |
| `docs/contracts.md` | Contracts and every extension point, with worked examples |
| `tools/site-smoke/` | Headless Chrome check of a built dashboard (console errors, tabs, drawer, 1440/390 px) |

Development: `uv run pytest` and `uv run ruff check`. Dashboard smoke:
`npm install --prefix tools/site-smoke && node tools/site-smoke/smoke.mjs site/index.html`.

## License

MIT (see `LICENSE`). The dashboard vendors the Observatory UI design system
(https://github.com/Nainish-Rai/observatory-ui, MIT); its license is kept in
`src/glmkit/report/vendor/observatory/LICENSE`.
