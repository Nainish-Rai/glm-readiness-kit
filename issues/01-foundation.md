# 01 · Foundation tracer bullet

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/01-foundation

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

- [x] `uv sync && uv run glmkit --help` lists `mock`, `conform` and `report`.
- [x] Mock without defect: `glmkit conform run --target mock --cases WIRE-00*` passes both cases. With the
      WIRE-001 defect (returns 500 with a Python traceback) it fails WIRE-001, and the evidence shows the body.
- [x] Result envelope validates against its exported JSON Schema; the target's key never appears in results or logs
      (test with a planted fake key).
- [x] `glmkit report build` produces `site/index.html` that opens from disk with no console errors at 1440 and
      390 px, shows the Runs and Conformance tabs, and passes the Observatory token check.
- [x] Adding a tab, a CLI sub-app, a defect or a case folder needs no edit to builder, runner or server core. This
      is proven by a test that registers a dummy of each.
- [x] `docs/contracts.md` documents the case format, result envelope, trace record and extension points for later
      slices.
- [x] `uv run pytest` and `uv run ruff check` are green.

## Blocked by

None. Can start immediately.

## Evidence

All commands run on 2026-10-26 from the repo root on macOS, Python 3.12 via uv. Outputs are trimmed.

**1. CLI lists the sub-apps**

```
$ uv sync && uv run glmkit --help
│ conform   Conformance: run named cases against a target profile.
│ mock      Mock GLM server (OpenAI + Anthropic paths, named defects).
│ profiles  Endpoint profiles (targets). ...
│ report    Static dashboard from result envelopes (opens from disk).
│ schema    JSON Schemas of the shared contracts (envelope, case, trace, profile).
```

**2. Mock without and with the WIRE-001 defect** (quote the glob in zsh)

```
$ uv run glmkit conform run --target mock --cases 'WIRE-00*'
run 20261026T153330Z-conform-373662  target=mock (openai_chat, api, glm-5.3)
  PASS  WIRE-001[nonstream]                critical
  PASS  WIRE-002[nonstream]                critical
  PASS  WIRE-002[stream]                   critical
verdict: pass  (3 passed, 0 failed, 0 soft, 0 skipped, 0 errors)            # exit 0

$ uv run glmkit conform run --target mock --cases 'WIRE-00*' --mock-defect forced-thinking-500
run 20261026T153331Z-conform-7d286d  target=mock (openai_chat, api, glm-5.3)  defects=forced-thinking-500
  FAIL  WIRE-001[nonstream]                critical  no_5xx: HTTP 500
  PASS  WIRE-002[nonstream]                critical
  PASS  WIRE-002[stream]                   critical
verdict: fail  (2 passed, 1 failed, 0 soft, 0 skipped, 0 errors)            # exit 1
```

Failing checks on WIRE-001: `no_5xx`, `no_internal_leak` ("Traceback (most recent call last)"), `http_status`
(expected 400, got 500), `error_code` (got None), `error_shape`. The item's exchange in `result.json` and in the
dashboard drawer carries the text/plain response body, a Python traceback ending `KeyError: 'disabled'`.

The same failure reproduces against a standalone mock:
`glmkit mock serve --port 8765 --defect forced-thinking-500` plus
`glmkit conform run --target mock-server --cases WIRE-001` gives FAIL with HTTP 500 and exit 1
(`/_mock/info` reports `defects_active: ['forced-thinking-500']`). On `mock-anthropic`, the clean run passes
and `-D forced-thinking-500` fails WIRE-001. On `mock-plan`, the clean run passes, and the 1210 error-code check is
skipped by its `when` precondition.

**3. Schema validation and planted key**

- Both envelopes above validate against `schemas/result-envelope.schema.json`. Each run's 3 `traces.jsonl` lines
  validate against `schemas/switch-trace-v0.schema.json`. `tests/test_contracts.py` also checks for schema drift.
- Planted key: `GLMKIT_MOCK_API_KEY=sk-glmkit-PLANTED-0123456789abcdef` with the `auth-echo` defect, which echoes
  the auth header back into the response, on `mock` and on `mock-anthropic`.
  - `grep -r PLANTED` over result.json, run.log, traces.jsonl and captured stdout: 0 matches.
  - The redacted forms are present: `"authorization": "[REDACTED]"`, `"x-api-key": "[REDACTED]"` and the echoed
    body `debug: auth=Bearer [REDACTED]`.
  - The same proof is automated in `tests/test_secrets.py`.

**4. Dashboard**

- Token check fails on a planted raw colour: adding `.planted { color: #ff0000; }` to `kit.css` made
  `glmkit report build results/ -o site/` report `site/index.html:62 raw-color #ff0000`, 1 finding, exit 1.
  After reverting: 0 findings, exit 0. `glmkit report check site/` also gives 0 findings.
- Browser check from disk (`file://`), with system Chrome through playwright-core:
  `node tools/site-smoke/smoke.mjs site/index.html`
  - At 1440 and 390 px: Runs and Conformance tabs visible; `scrollWidth - clientWidth = 0` (no horizontal
    overflow); the drawer opens, closes and returns focus.
  - Console errors `[]`; `ok: true`.
  - Screenshots were inspected by eye.

**5. Extension points without core edits**

`tests/test_extension_points.py` loads a dummy plugin module through `GLMKIT_PLUGINS`. The plugin registers a
CLI sub-app, a check, an executor, a mock defect and a report page, and the test adds a case folder through
`GLMKIT_CASES_DIRS`. Each one is then exercised end to end: CLI help, a conform run, the mock response and the
built site. The test edits no file in builder, runner or server.

**6. Docs**

`docs/contracts.md` covers the endpoint profile, case format, result envelope and verdict rules, evidence,
SwitchTrace v0 and the extension-point table, with worked examples.

**7. Gates**

```
$ uv run pytest                       -> 88 passed in 11.07s
$ uv run ruff check                   -> All checks passed!
$ uv run ruff format --check src tests -> 63 files already formatted
```

**Not verified**

- `.github/workflows/ci.yml` has never run, because nothing was pushed.
- No live endpoint was called; that is issue 16.
- These GLM behaviours are ASSUMED from docs and marked so in the mock and profiles:
  - the Anthropic-path error body shape;
  - code 1214 for a bad effort value;
  - code 1213 for a missing `max_tokens`;
  - the OpenRouter model slug.
- Fonts load from Google Fonts, so the dashboard falls back to system fonts offline. The offline fallback was not
  checked by eye.
