# 01 · Foundation tracer bullet

Status: ready · Type: AFK · Label: ready-for-agent · Branch: issue/01-foundation

## What to build

The thinnest complete path through every layer, which all other slices plug into. After this slice, a user
can start the mock GLM server, run one conformance case against it with and without its planted defect, and open a
static dashboard that shows the run.

1. **Project skeleton.** `uv` project, package `glmkit`, `typer` CLI (`glmkit --help`) with a sub-app registry that
   later slices add to with one line each. Also ruff config, pytest, MIT license, a short README, and CI
   (GitHub Actions workflow file only; nothing pushed).
2. **Endpoint profiles.** YAML profiles describing a target: base URL, protocol (`openai_chat`,
   `anthropic_messages`), endpoint class (`api`, `plan`, `third_party`, `self_hosted`), model id, and the env var
   holding the key. Ship profiles for Z.ai API, Z.ai Coding Plan (OpenAI and Anthropic paths), OpenRouter, a
   generic OpenAI-compatible host, local vLLM/SGLang, and the mock. The base URLs and paths come from
   `research/01-conformance.md` §2.2.
3. **Shared contracts** (pydantic models + JSON Schema export + docs page):
   - *conformance case* file format: as in `research/01-conformance.md` §6, YAML under
     `cases/<category>/<ID>-slug.yaml`. Cases are data; named oracles live in check modules.
   - *result envelope*: one JSON per run with run id, kit version, timestamp, target profile (key redacted),
     component, per-item results, summary and verdict. Every component writes this.
   - *trace record*: "SwitchTrace v0" from `research/03-eval.md` §2, one JSONL line per model call.
4. **Unified client.** An async `httpx` client for both protocols, stream and non-stream. It parses SSE and records
   latency (TTFT, total) and raw bodies for evidence, redacting auth headers.
5. **Mock GLM server** (`glmkit mock serve`). An ASGI app speaking Z.ai's OpenAI-compatible
   `/chat/completions` and Anthropic-compatible `/v1/messages`, stream and non-stream, with thinking
   (`reasoning_content`), tool calls, usage and Z.ai error bodies (`{"error":{"code":"1210","message":...}}`).
   Behaviour is deterministic and scriptable. Known upstream bugs are **named defects** that can be switched on per
   server or per request header. Defect modules are discovered from a folder so later slices add their own.
   Ship the defect for this slice's case.
6. **Conformance runner** (`glmkit conform run --target <profile> [--cases <glob>]`). It loads cases, runs them
   against a target, evaluates named checks, and writes the result envelope. Implement **WIRE-001** (GLM-5.3
   `thinking: disabled` must return 400 with code 1210 on the API class, and never 5xx) and **WIRE-002**,
   end to end.
7. **Report builder** (`glmkit report build results/ -o site/`). It vendors Observatory UI's design-system files,
   builds a static site from the layout shell (header, tabs, KPI strip) and writes data files. Tabs are
   discovered from page modules, so later slices add a tab without editing the builder. Ship the "Runs" overview
   tab (every run, its component, target and verdict) and a generic "Conformance" table (case × target, pass/fail,
   click for evidence in the drawer).

## Acceptance criteria

- [ ] `uv sync && uv run glmkit --help` lists `mock`, `conform` and `report`.
- [ ] Mock without defect: `glmkit conform run --target mock --cases WIRE-00*` passes both cases. With the
      WIRE-001 defect (returns 500 with a Python traceback) it fails WIRE-001, and the evidence shows the body.
- [ ] Result envelope validates against its exported JSON Schema; the target's key never appears in results or logs
      (test with a planted fake key).
- [ ] `glmkit report build` produces `site/index.html` that opens from disk with no console errors at 1440 and
      390 px, shows the Runs and Conformance tabs, and passes the Observatory token check.
- [ ] Adding a tab, a CLI sub-app, a defect or a case folder needs no edit to builder, runner or server core. This
      is proven by a test that registers a dummy of each.
- [ ] `docs/contracts.md` documents the case format, result envelope, trace record and extension points for later
      slices.
- [ ] `uv run pytest` and `uv run ruff check` are green.

## Blocked by

None. Can start immediately.

## Evidence

(commands run and output, filled in by the implementer)
