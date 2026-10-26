# glmkit contracts and extension points

This page is the build reference for every slice after 01. It covers the shared data contracts (owned by issue
01), the runtime pieces you reuse (client, mock, runner, report builder), and each extension point, with a
worked example for each.

**Rule for shared contracts.** Later slices may only add *optional* fields. A breaking change goes on issue 01
as a blocker. Put component-specific data in the `data` dicts (`ResultEnvelope.data`, `ItemResult.data`), or
in `executor_config` for cases; none of these need a schema change. After you change a model, run
`uv run glmkit schema export -o schemas` and commit the result. A test fails when `schemas/*.json` drifts from
the models.

**Rule for extension points.** Every registry is filled by discovery: either a module in a known folder, or a
plugin module. A slice adds files and never edits a core file. No core file has a list of names to append to,
so slices built in parallel do not conflict in git.

| You want to add | Put a file at | Registered by |
|---|---|---|
| CLI sub-app `glmkit <area>` | `src/glmkit/<area>/cli.py` with `app: typer.Typer` | folder discovery (`glmkit/cli.py`) |
| Top-level command `glmkit <cmd>` | `register_cli(root)` in any `glmkit/<area>/cli.py` | same |
| Sub-command `glmkit conform <cmd>` | `src/glmkit/conform/commands/<name>.py` with `register(app)` | `glmkit/conform/cli.py` |
| Conformance cases | `cases/<category>/<ID>-slug.yaml` (+ `cases/fixtures/...`) | case-root scan |
| Check (named oracle) | `src/glmkit/conform/checks/<area>.py`, `@check("name")` | package import |
| Case executor | `src/glmkit/conform/executors/<name>.py`, `@executor("name")` | package import |
| Mock defect | `src/glmkit/mock/defects/<name>.py`, `@register_defect` | package import |
| Dashboard tab | `src/glmkit/report/pages/<id>.py` + `<id>.js` (+ optional `.css`), `@register_page` | package import |
| Endpoint profile | `src/glmkit/profiles/<name>.yaml` (or `./profiles/`, `$GLMKIT_PROFILES_DIRS`) | folder scan |
| Anything, out of tree | a module listed in `GLMKIT_PLUGINS=mod1,mod2` or the `glmkit.plugins` entry point | `glmkit.plugins.load_plugins()` |

`tests/test_extension_points.py` proves the table end to end. A dummy plugin outside the package registers a
CLI sub-app, a check, an executor, a defect and a tab, and a dummy case folder is added. Everything shows up in
the CLI, the runner, the mock and the dashboard with no core edit.

---------------------------------------------------------------------------------------------------------------

## 1. Endpoint profile (`glmkit.contracts.profile.EndpointProfile`)

There is one YAML file per target. Schema: `schemas/endpoint-profile.schema.json`. List them with
`glmkit profiles list` and inspect one with `glmkit profiles show <name>`.

```yaml
name: zai-api                    # --target value
title: Z.ai API (pay-as-you-go), OpenAI-compatible
provider: zai                    # zai | openrouter | vllm | sglang | generic | mock | ...
base_url: https://api.z.ai/api/paas/v4    # ${ENV:-default} expanded at load; '{mock}' = in-process mock
protocol: openai_chat            # openai_chat | anthropic_messages
endpoint_class: api              # api | plan | third_party | self_hosted
model: glm-5.3
model_family: null               # canonical GLM id for case applicability (derived: 'z-ai/GLM-5.3[1m]' -> 'glm-5.3')
api_key_env: ZAI_API_KEY         # NAME of the env var; the key itself is never in a file
api_key_required: true
auth_header: null                # default: bearer (openai_chat), x-api-key (anthropic_messages)
headers: {}                      # extra non-secret headers; Authorization/x-api-key are rejected
path: null                       # override /chat/completions or /v1/messages
spawn_mock: false                # true: the runner starts the mock in-process and replaces '{mock}'
sources: ["research/01-conformance.md §2.2"]
```

The request URL is `base_url + path`. The path is `/chat/completions` for `openai_chat` and `/v1/messages`
for `anthropic_messages`.

Shipped profiles: `zai-api`, `zai-api-anthropic`, `zai-plan`, `zai-plan-anthropic`, `openrouter`,
`generic-openai`, `vllm-local`, `sglang-local`, `mock`, `mock-anthropic`, `mock-plan` (all three are
in-process), and `mock-server` (an external `glmkit mock serve`, `$GLMKIT_MOCK_URL`).

## 2. Conformance case (`glmkit.contracts.case.ConformanceCase`)

Cases are data. They live in `cases/<category>/<ID>-slug.yaml`, and the file name must start with the id.
Schema: `schemas/conformance-case.schema.json`. The format follows research/01-conformance.md §6.

```yaml
schema_version: 1
id: WIRE-001                       # ^[A-Z]+-[0-9]{3}...
title: "thinking: disabled ... returns 400/1210 on the API class, never 5xx"
category: wire/errors
severity: critical                 # critical | major | minor | info  (critical failure = hard gate)
kind: deterministic                # deterministic | statistical | soak | fingerprint
executor: single                   # registered executor (section 7.3)
executor_config: {}                # free-form, read by the executor
applies_to:                        # omitted list = any
  models: [glm-5.3, glm-5.3-flash] # matched against the target's model family
  protocols: [openai_chat, anthropic_messages]
  endpoint_classes: [api, plan, third_party, self_hosted]
source:                            # provenance: required in practice (a test checks issues or urls)
  issues: ["farion1231/cc-switch#6737"]
  urls: ["https://github.com/farion1231/cc-switch/issues/6737"]
  spec: ["https://docs.z.ai/guides/capabilities/thinking.md"]
  first_seen: 2026-10-01
modes: [nonstream]                 # nonstream | stream; one item per mode
repeats: 1                         # attempts per item (statistical kinds)
opt_in: false                      # true: runs only with --include-opt-in or when named exactly
request:                           # canonical, OpenAI-chat flavoured; the client translates per protocol
  model: null                      # default: the target's model
  messages: [{role: user, content: "Reply OK"}]
  tools: null                      # OpenAI {type: function, function: {...}} or shorthand {name, description, parameters}
  tool_choice: null                # auto | none | required | {type: function, function: {name}}
  max_tokens: 32
  temperature: null
  top_p: null
  stop: null
  params: {thinking: {type: disabled}}       # deep-merged verbatim into the protocol body
  overrides: {anthropic_messages: {}}        # per-protocol deep-merge applied last; a null value deletes a key
  omit: []                         # top-level body keys to delete after translation (e.g. [max_tokens])
variants:                          # optional A/B shapes of the same case; one item per variant x mode
  - id: flat
    request: {tools: [{$ref: fixtures/tools/flat.json}]}   # deep-merged into request
    expect: null                   # replaces the case's expect for this variant
    expect_class: control
expect:                            # check lists by endpoint class; 'default' always applies
  default:
    - {check: no_5xx}
    - {check: no_internal_leak}
  api:
    - {check: http_status, equals: 400}
    - {check: error_code, in: ["1210"]}
  plan:
    - {check: http_status, in: [200, 400], soft: true}                          # soft: failure = soft_fail
    - {check: error_code, in: ["1210"], soft: true, when: {check: http_status, equals: 400}}  # precondition
tolerance: {}                      # free-form (reference/margins), for statistical slices
tags: []                           # also matched by --cases globs
notes: ""
```

- `{$ref: fixtures/x.json}` (JSON or YAML) anywhere in a case is replaced by that file, resolved against the
  case root. It cannot leave the root. Files under `<root>/fixtures/` and files whose name starts with `_` are
  never loaded as cases.
- Case roots are searched in this order: `$GLMKIT_CASES_DIRS` (os.pathsep-separated), the repo's `cases/`,
  and the wheel-bundled copy. `--cases-dir` replaces them all.
- `--cases` globs (repeatable or comma-separated) match the id, category, relative path, file stem and tags.
  Quote them in zsh (`--cases 'WIRE-00*'`): unquoted, zsh fails with "no matches found".
- Checks for an item are `expect.default + expect[<target endpoint class>]`.
- YAML dates (`first_seen: 2026-10-01`) may be left unquoted.

## 3. Evidence (`glmkit.contracts.evidence.Exchange`)

Every model call becomes one `Exchange`, attached to the item that made it:

```text
Exchange { id, label ("main" | executor label), protocol, mode,
  request:  { method, url, headers (Authorization / x-api-key / cookies -> "[REDACTED]"), body, body_bytes },
  response: { status, headers, content_type, body_text (<= 64 KiB, truncated flag), body_json,
              events: [{event, data (parsed JSON or raw string; "[DONE]" stays a string), t_ms}] (<= 2000) } | null,
  normalized: NormalizedResponse,
  timing: { ttfb_ms, ttft_ms (first event carrying reasoning/text/tool call; stream only), total_ms },
  transport_error: null | "timeout" | "stream_timeout" | "ConnectError: ..." }

NormalizedResponse { http_status, ok, error_code (string; "1210"), error_code_type ("string"|"integer"),
  error_type, error_message, model, content, reasoning,
  tool_calls: [{index, id, name, arguments (raw string), arguments_parsed, parse_error}],
  finish_reason (raw), finish_reason_norm (stop|tool_calls|length|raw),
  usage: { prompt_tokens (INCLUDES cache), completion_tokens, total_tokens, cached_tokens, reasoning_tokens,
           source_convention ("openai"|"anthropic"), raw },
  stream: { event_count, done_seen, finish_events, role_events, first_usage (Anthropic message_start usage),
            timed_out, parse_errors } | null }
```

Anthropic `stop_reason` maps to the OpenAI vocabulary: end_turn/stop_sequence → stop, tool_use → tool_calls,
max_tokens → length. Anthropic usage is converted to the total-including-cache convention:
`prompt = input + cache_read + cache_creation`. Error codes are read from `error.code` in both the Z.ai
shape and the Anthropic shape, and also from top-level `{code, msg}`.

## 4. Result envelope (`glmkit.contracts.envelope.ResultEnvelope`)

There is one JSON per run, at `<results>/<run_id>/result.json`. Every component writes one. Schema:
`schemas/result-envelope.schema.json`.

```text
ResultEnvelope {
  schema_version: 1, run_id: "20261026T151701Z-conform-8a06cc" (new_run_id(component)), kit_version,
  component: "conform" | "bridge" | "eval" | ...,
  started_at, finished_at,
  targets: [TargetInfo { profile, title, provider, base_url, url, protocol, endpoint_class, model,
                         api_key_env (NAME only), key_present (bool) }],
  items: [ItemResult],
  summary: { total, passed, failed, soft_failed, skipped, errored, throttled, hard_gate_failures, pass_rate },
  verdict: "pass" | "warn" | "fail" | "error" | "incomplete",
  config: {...run options, no secrets},  environment: {python, platform, glmkit},
  artifacts: [{kind: "traces"|"log"|..., path (relative to the run dir)}],
  data: {...component payload} }

ItemResult {
  id (unique in the envelope, e.g. "WIRE-001[nonstream]", "TOOL-006/flat[stream]"), group ("WIRE-001"),
  title, kind ("conformance_case" | "fix_event" | "eval_item" | ...), category, severity, target (profile),
  labels {mode, protocol, endpoint_class, kind, variant?},
  status: "pass" | "fail" | "soft_fail" | "skip" | "error" | "throttled",
  reason (one-line summary: first failing check, or the skip reason),
  checks: [CheckResult {name, status, message, expected, observed, soft, attempt}],
  metrics {attempts, http_status, error_code, ttft_ms, total_ms, finish_reason, pass_rate?},
  evidence: [Exchange], source {issues, urls, spec, case_file}, duration_ms, data {} }
```

**Verdict rules** (`compute_verdict`):
- `fail`: any item failed with severity critical, major or none.
- `warn`: no such failure, but a minor or info item failed, or there is a `soft_fail`, `error` or
  `throttled` item.
- `pass`: at least one item ran and every item passed.
- `incomplete`: nothing ran (everything was skipped).

The `glmkit conform run` exit code is 1 on `fail`/`error`, 2 on usage errors (unknown target, bad case file,
missing key), else 0.

**Item status** (conformance runner):
- Each attempt's status is the worst of its checks: error > fail > soft_fail > pass. An attempt is
  `throttled` when it got a 429 with codes 1302/1305/1308–1321.
- Deterministic items take the worst attempt.
- If the case lists an aggregate check (such as `pass_rate`), the item status comes from the aggregate checks
  (plus any errors). Per-attempt failures are then expected and summarised by those checks.
- Evidence keeps the first 5 attempts plus every failing attempt.

**Writing an envelope from your component.** Use `ResultEnvelope(...)`, append `ItemResult`s, call
`env.finalize()` (it fills summary, verdict and finished_at), then
`glmkit.results.write_envelope(env, out_dir, redactor)`. Use `results.run_dir`, `write_jsonl`,
`add_artifact` and `run_log` for artifacts and the redacting log.

## 5. Trace record: SwitchTrace v0 (`glmkit.contracts.trace.SwitchTrace`)

There is one JSONL line per model call. Schema: `schemas/switch-trace-v0.schema.json`, matching
research/03-eval.md §2 (`"schema": "switch-eval.call/0"`, call_id, trace_id, parent_call_id, seq, ts_start,
task_type, source{kind, ref, adapter_version}, request{provider, model, system[parts], messages[{role,
parts[]}], tools[], params{temperature, top_p, max_tokens, reasoning_effort, response_format, stream}},
response{messages, finish_reason, error}, usage{input_tokens_total (includes cache), cache_read_tokens,
cache_write_tokens, output_tokens_total, reasoning_tokens, source_convention}, latency{total_ms, ttft_ms},
cost_usd_reported, labels{...}, privacy{content_present, redacted}).

Part types are `text | tool_call | tool_result | reasoning`. `source.kind` adds `glmkit` for calls the kit
made itself. The conformance runner writes `<run_dir>/traces.jsonl`, using
`exchange_to_trace(exchange, trace_id=, seq=, ts_start=, ref=, provider=, task_type=)`. Importers (slice 11)
construct `SwitchTrace` directly and serialise with `.to_jsonl()` (which uses the `schema` alias).

## 6. Reusable runtime

### 6.1 Client (`glmkit.client`)

```python
from glmkit.client import GLMClient
async with GLMClient(profile, timeout_s=60, stream_timeout_s=120, extra_headers={}, redactor=None) as c:
    ex = await c.send_canonical(canonical_request_dict, stream=True, label="turn1")  # -> Exchange
    ex = await c.send(raw_protocol_body, stream=False)                                # already-translated body
```

- `GLMClient(...)` raises `MissingKeyError` when the profile needs a key and the env var is unset.
- Transport failures never raise; they are recorded in `transport_error`.
- `stream_timeout_s` is a deadline on the whole stream, so a hanging stream ends as `stream_timeout`.
- Translation helpers: `glmkit.client.adapters` (`to_openai_body`, `to_anthropic_body`, `normalize_*`,
  `StreamAccumulator`, `deep_merge`).
- SSE: `glmkit.client.sse.SSEParser` gives the same result whatever the chunk splits.

### 6.2 Mock GLM server (`glmkit.mock`)

`glmkit mock serve [--port 8765] [--defect NAME ...] [--endpoint-class api] [--script rules.yaml]
[--chunk-chars 12] [--chunk-delay 0] [--require-key-env VAR]`. `glmkit mock defects` lists the defects.
From Python (any test, any slice):

```python
from glmkit.mock import MockConfig, MockServer
with MockServer(MockConfig(defects=["forced-thinking-500"], rules=[...])) as srv:
    profile = get_profile("mock").with_base_url(srv.url + "/api/paas/v4")
```

- **Routes.** `POST …/chat/completions` (OpenAI), `POST …/v1/messages` (Anthropic),
  `POST …/v1/messages/count_tokens`, `GET /_mock/info`, `GET /health`. Any prefix works. A path containing
  `/coding/` is served as the `plan` class; other paths use `--endpoint-class` (default `api`).
- **Default behaviour is the documented, correct GLM behaviour** (`mock/engine.py`):
  - forced thinking on glm-5.3/5.3-flash*/4.7: `thinking.type` disabled/false/none/off returns 400 code
    "1210" with the zh message on api; on plan it becomes effort `low`;
  - effort: glm-5.3 on api accepts only low/high/max (otherwise 400 "1214", ASSUMED code); plan maps
    none/minimal/low→low, medium/high→high, xhigh/max→max (unknown→max); glm-5.2 maps none/minimal→no
    thinking;
  - errors: unknown model 400 "1211"; missing model or messages 400 "1213"; Anthropic without `max_tokens`
    400 (ASSUMED code 1213); temperature outside [0,1], top_p outside [0.01,1], max_tokens > 131072 or
    > 128 tools: 400 "1214";
  - reasoning before content; tool calls whose arguments satisfy the schema (a top-level oneOf takes its
    first branch);
  - `tool_stream: true` streams argument fragments per `index`; otherwise one delta per call;
  - OpenAI usage comes in the final chunk, before `[DONE]`; Anthropic `message_start` carries real
    `input_tokens`;
  - implicit prefix cache: earlier messages and the system/tools head count as `cached_tokens`; the newest
    message never does;
  - `max_tokens` truncation gives finish `length`;
  - IDs and `created` are deterministic.
- **Error bodies.** OpenAI path: `{"error":{"code":"1210","message":...}}`. Anthropic path:
  `{"type":"error","error":{"type":"invalid_request_error","code":"1210","message":...}}` (shape ASSUMED,
  to be verified in issue 16).
- **Headers.**
  - `X-GLMKit-Defects: a,b` adds defects to one request; `none` turns off the server-wide ones. An unknown
    name returns 400 with `x-glmkit-mock-error: 1`.
  - `X-GLMKit-Scenario: <rule name>` forces a scripted rule.
- **Scripted rules** (`MockConfig.rules`, `--script file.yaml` with `rules: [...]`):
  `{name, match: {model, protocol, endpoint_class, has_tools, last_user_regex, header: {k: v}},
  reply: {content, reasoning, tool_calls: [{name, arguments}], finish_reason, model, usage: {...}}
  | error: {status, code, message, anthropic_type}, delay_s, chunk_delay_s}`. The first match wins.

### 6.3 Conformance runner (`glmkit.conform.runner.run_conformance(profile, RunOptions)`)

`RunOptions(patterns, case_roots, modes, mock_defects, out_dir, timeout_s, stream_timeout_s, include_opt_in,
write)` returns a `RunOutcome(envelope, run_dir)`.

- `spawn_mock` profiles (or a `{mock}` base URL) start the mock in-process, and `mock_defects` are applied
  server-wide.
- Any other target gets the `X-GLMKit-Defects` header, which only has an effect when the target is a mock.
- Cases run sequentially, so the mock's cache state stays deterministic.
- The runner writes `result.json`, `traces.jsonl` and `run.log` (redacted) to `<out>/<run_id>/`.

## 7. Extension points, with worked examples

### 7.1 CLI sub-app

```python
# src/glmkit/bridge/cli.py         -> `glmkit bridge ...` (no other file changes)
import typer
app = typer.Typer(help="glm-bridge proxy", no_args_is_help=True)
# COMMAND = "bridge"               # optional: mount under a different name than the package

@app.command()
def serve(upstream: str = typer.Option(..., "--upstream")) -> None:
    ...

def register_cli(root: typer.Typer) -> None:   # optional: top-level commands, e.g. `glmkit init`
    @root.command("init")
    def init(customer: str) -> None: ...
```

`glmkit conform <cmd>`: add `src/glmkit/conform/commands/reference.py` with `def register(app): ...`.
For example, slice 05 adds a `reference` Typer via `app.add_typer(ref_app, name="reference")`.

### 7.2 Check (named oracle)

```python
# src/glmkit/conform/checks/tools.py
from glmkit.conform.checks import CheckContext, CheckOutcome, check, compare, fail, ok, skip

@check("tool_arguments_nonempty_when_required")
def tool_arguments_nonempty_when_required(ctx: CheckContext) -> CheckOutcome:
    """First line = description shown by `glmkit conform checks`."""
    calls = ctx.response.tool_calls          # ctx.exchange / ctx.exchanges / ctx.params / ctx.case / ctx.target
    if not calls:
        return skip("no tool calls")
    bad = [c.name for c in calls if c.arguments_parsed == {}]
    return fail(f"empty args for {bad}", observed=bad) if bad else ok()

@check("pass_rate_wilson", aggregate=True)   # runs once per item; sees ctx.attempts[i].status / .checks
def pass_rate_wilson(ctx): ...
```

- Case usage: `- {check: tool_arguments_nonempty_when_required}`. Params come from `ctx.params`, and
  `compare(observed, ctx.params)` implements equals/not_equals/in/not_in/lt/lte/gt/gte/matches.
- `params.exchange: <label>` selects an exchange by label.
- A check that raises becomes status `error`; it never crashes the run.
- Add one pass and one fail case to `tests/test_checks.py`. A test fails for any registered per-attempt check
  in `glmkit.conform.checks` that has no regression row.

### 7.3 Case executor (multi-turn loops, probes, soak)

```python
# src/glmkit/conform/executors/agent_loop.py
from glmkit.conform.executors import ExecContext, executor

@executor("agent_loop")
async def agent_loop(ctx: ExecContext):
    turns = ctx.config.get("turns", 2)      # from the case's executor_config
    exchanges, req = [], dict(ctx.request)
    for i in range(turns):
        ex = await ctx.client.send_canonical(req, stream=ctx.stream, label=f"turn{i}")
        exchanges.append(ex)
        req = {**req, "messages": [*req["messages"], {"role": "assistant", "content": ex.normalized.content,
               "reasoning_content": ex.normalized.reasoning}, {"role": "user", "content": "continue"}]}
    return exchanges                          # every exchange becomes evidence + a trace line
```

The case selects it with `executor: agent_loop` and passes knobs in `executor_config: {turns: 3}`. Checks see
all exchanges (`ctx.exchanges`) and the last one by default (`ctx.exchange`).

### 7.4 Case folder

Add `cases/tools/TOOL-006-schema-shape-oneof.yaml` (and shared JSON under `cases/fixtures/tools/`). Nothing
else is needed: `glmkit conform list` shows it, and `glmkit conform run --cases 'TOOL-*'` runs it. Out of
tree, point `GLMKIT_CASES_DIRS` at another root.

### 7.5 Mock defect

```python
# src/glmkit/mock/defects/oneof_empty_args.py
from glmkit.mock.defects import Defect, register_defect

@register_defect
class OneOfEmptyArgs(Defect):
    name = "oneof-empty-args"
    title = "top-level oneOf with empty properties yields {} arguments"
    sources = ("https://github.com/zai-org/GLM-5/issues/169",)
    cases = ("TOOL-006",)

    def on_reply(self, ctx, reply):          # ctx.req (parsed request), ctx.protocol, ctx.endpoint_class, ctx.headers
        tools = {t.name: t for t in ctx.req.tools}
        for tc in reply.tool_calls:
            p = tools[tc.name].parameters
            if p.get("oneOf") and not p.get("properties"):
                tc.arguments = "{}"
        return reply
```

Hooks run in this order, and each defaults to a no-op:
1. `on_request(ctx) -> Response|None`: short-circuits the whole request.
2. `on_error(ctx, ErrorReply) -> ErrorReply|Response`: see `forced_thinking_500.py`.
3. `on_reply(ctx, Reply) -> Reply`: changes the structured reply before encoding.
4. `on_body(ctx, dict) -> dict`: changes the non-stream JSON.
5. `on_events(ctx, [MockEvent]) -> [MockEvent]`: changes the streamed events. Set `MockEvent.delay_s` for a
   hanging stream, and drop the `message_stop`/`[DONE]` event for an unterminated one.

`self.state(ctx)` is a per-server dict, for example a turn counter for "collapse after N turns".

**Proof convention.** Each check must fail against its planted defect and pass without it. Put both commands
and their outputs in the issue's Evidence section:
`glmkit conform run --target mock --cases 'TOOL-006'` and the same with `--mock-defect oneof-empty-args`.

### 7.6 Dashboard tab

```python
# src/glmkit/report/pages/scorecard.py
from glmkit.report.registry import Page, RunRef, register_page

@register_page
class ScorecardPage(Page):
    id = "scorecard"; title = "Scorecard"; order = 30      # tab order (runs=10, conformance=20)
    script = "scorecard.js"                                 # next to this module
    css = None                                              # optional "scorecard.css" (token-checked, inlined)

    def collect(self, runs: list[RunRef]) -> dict | None:  # runs: newest first; r.envelope, r.path
        rows = [...]
        return {"count": len(rows), "rows": rows,           # "count" -> tab badge; None -> hide the tab
                "kpis": [{"label": "Hard gates", "value": 2, "sub": "critical cases failed", "tone": "err"},
                         {"label": "Pass rate", "value": 0.93, "format": "pct"}]}   # format: n|k|pct|ms|s
```

```js
// src/glmkit/report/pages/scorecard.js   (inlined into index.html; no imports; tokens only, no raw colours)
GLMKit.registerPage("scorecard", {
  render(panel, data, kit) {                 // called once, when the tab is first shown
    const { O, el } = kit;                   // O = window.Observatory (components.iife.js)
    const table = el("table");
    panel.replaceChildren(el("div", { class: "card" },
      el("div", { class: "card-h" }, el("h2", {}, "Scorecard"), el("span", { class: "hint" }, "how to read it")),
      el("div", { class: "tbl-wrap" }, table)));
    O.sortableTable(table, [{ key: "id", label: "Case" }], data.rows, {
      onRowClick: (row, tr) => kit.openItem(row.item, row.run, tr) });   // shared evidence drawer
  },
  onShow(data, kit) {},                      // optional: every time the tab becomes active
});
```

The `kit` (`window.GLMKit`) API:
- `openDrawer({title, lead, tag, strip, stats, main: Node[], detail: Node[], opener, onClose})`;
- `openItem(itemResult, {run_id, started_at, defects}, opener)`: the standard request / response /
  normalized / checks / source drawer;
- `exchangeView(ex, i)`, `checksView(checks)`, `sourceView(source)`, `statusPill(status)`,
  `nl("not logged")`, `fmtMs`, `fmtTime`, `pretty`, `pre`, `tone(status)`, `label(status)`, `rank(status)`,
  `setKpis(tiles)`.

Observatory component signatures are in `src/glmkit/report/vendor/observatory/README.md`. The style and
component rules are in `vendor/observatory/skill/references/{style,components}.md`. Follow them:
- missing values read `not logged`;
- estimates use the dashed `heuristic` card or pill;
- colours are token names (`"--cat-1"`, `"--ok"`) only.

**Checks for every tab:**
1. `uv run glmkit report build results/ -o site/` runs the Observatory token check and exits 1 on any finding.
   `glmkit report check site/` re-runs it. First see it fail once on a planted raw colour, for example a
   `color: #ff0000` rule in your page CSS. The builder generates the token contract comment, and an undefined
   `var(--x)` is a build error.
2. `node tools/site-smoke/smoke.mjs site/index.html --out shots/` opens the page from file:// in Chrome at 1440
   and 390 px. It visits every tab, opens and closes a drawer, and fails on console errors, page errors, failed
   requests or horizontal page scroll at 390 px. Install with `npm install --prefix tools/site-smoke`, or set
   `NODE_PATH` to an existing `playwright-core`. Set `CHROME_PATH` if Chrome is not where Playwright looks.

Site layout:
- `site/index.html`: the shell from `report/shell/index.html.tmpl`, with kit CSS and all page scripts inlined;
- `site/assets/`: the vendored tokens.css, components.css, components.iife.js and LICENSE;
- `site/data/<id>.js|.json`: `window.GLMKIT_DATA[id]`; `data/__site.js` holds the tab list and metadata.

## 8. Secrets

- Keys come only from the env var a profile names.
- Evidence records `Authorization`, `x-api-key` and cookies as `[REDACTED]`.
- The run's `Redactor` also scrubs the literal key value from every string in result.json, traces.jsonl and
  run.log (through `RedactingFilter`). This covers hosts that echo the key back (mock defect `auth-echo`).
- `tests/test_secrets.py` plants a fake key and asserts it appears in no written file and no console output.
- If your component writes files, pass them through `Redactor.scrub` / `write_envelope(..., redactor)`.

---------------------------------------------------------------------------------------------------------------

## 9. Trace importers (`glmkit.traces`, issue 11)

`glmkit traces import --from <kind> <path> -o traces.jsonl [--redact] [--redact-pattern REGEX]
[--task-map map.yaml] [--results results/] [--name LABEL]` converts a production export into SwitchTrace v0 lines.
`glmkit traces sources` lists the kinds and `glmkit traces stats traces.jsonl... [--json]` prints counts by source and
task type, replayable vs baseline-only, and token totals. The import also writes a `component: "traces"` result
envelope (data: `set`, `source`, `output`, `stats`, `skipped`, `redactions`) plus a copy of the JSONL as a `traces`
artifact. That envelope feeds the **Traces** tab.

| Kind | Input | Notes |
|---|---|---|
| `otel` | JSONL of spans, or OTLP/JSON `resourceSpans` | semconv-genai commit `20259355b963...`; content is opt-in, spans without it are baseline-only |
| `langfuse` | observations as JSONL, JSON or CSV | `GENERATION` rows only. Column names are assumed (not verified against a real export) |
| `openai` | `{request, response}` JSONL, or bare chat completions | `prompt_tokens` already includes cache |
| `anthropic` | `{request, response}` JSONL, or bare Messages responses | `input_tokens` excludes cache and is converted; repeated response ids are dropped |
| `litellm` | spend-log rows as JSONL, JSON or CSV | `spend` becomes `cost_usd_reported`; rows with `{}` messages are baseline-only |
| `claude_code` | session JSONL (a file, or a folder of them) | lines merged by `message.id`; `isSidechain` gets `labels.tags: ["subagent"]`; unknown line types are skipped and counted |

**Replayable** means `privacy.content_present` and at least one request message. Everything else is
**baseline-only**: it can contribute cost, latency and verbosity but cannot be replayed.

**Task type** comes from, in order: a metadata key (`task_type` or `task`), the mapping file, a model regex in the
mapping file, the first source tag, then `unlabelled`. Mapping file (YAML or JSON):
`{metadata_keys: [...], tags: {tag: type}, models: {regex: type}, use_first_tag: true, default: unlabelled}`.

**Redaction** (`--redact`) replaces emails, phone numbers and API-key-like strings (`sk-...`, `sk-ant-...`, `ghp_...`,
AWS/Google/Slack keys, JWTs, `Bearer ...`, `api_key=...`) with `[REDACTED:<kind>]`, plus any `--redact-pattern`. It
covers every string in the trace except `call_id`, `trace_id`, `parent_call_id`, `ts_start` and `source`, and sets
`privacy.redacted`. It is pattern based: review a sample before sharing a set.

**Contract addition.** `labels.tags: list[str]` (optional, default `[]`) on SwitchTrace v0; schema re-exported.

**Adding a source = one file.** Create `src/glmkit/traces/adapters/<kind>.py` (or a `GLMKIT_PLUGINS` module):

```python
from glmkit.traces.registry import Adapter, ImportContext, read_records, register_adapter

@register_adapter
class MyAdapter(Adapter):
    kind = "mysource"                 # must be a SwitchTrace source.kind literal; adding one is an optional-field change
    version = "0.1"; title = "My source"; formats = "jsonl"

    def parse(self, ctx: ImportContext):          # yield SwitchTrace; never raise on a bad row
        for row_no, row in read_records(ctx):     # JSONL / JSON / CSV; bad lines are counted, not fatal
            if "id" not in row:
                ctx.skip("row without id"); continue
            yield SwitchTrace(..., task_type=ctx.task_type(tags=[...], meta={...}, model=...),
                              source=TraceSource(kind="mysource", ref=ctx.ref(row_no), adapter_version=self.version))
```

`glmkit.traces.common` has the usage converters (`openai_usage`, `anthropic_usage`), message and tool converters, and
`parse_ts`. Add a fixture under `tests/fixtures/traces/`, a golden file (`GLMKIT_UPDATE_GOLDEN=1 uv run pytest
tests/test_traces.py`) and a row in `CASES` in `tests/test_traces.py`; a test fails for any adapter without one.

## 10. Price table (`glmkit.cost.prices`, owned by issue 12)

One versioned YAML, `src/glmkit/cost/prices.yaml` (override: `$GLMKIT_PRICES` or `load_price_table(path)`). Issues 09
(guard) and 10 (eval) read prices only through this API and never keep their own.

```python
from glmkit.cost.prices import load_price_table, price_for
e = price_for("glm-5.3")                    # PriceEntry | None; host defaults to "zai-api"
e.input, e.cached_input, e.output           # USD per 1M tokens; None = "not logged" (never guess a price)
e.source, e.retrieved                       # URL + date, required on every entry
table = load_price_table()
table.hosts("glm-5.3")                      # every host listing the model: zai-api, openrouter-cheapest, bedrock ...
table.lookup("glm-5.3-flash", "zai-api")
table.coding_plan.tiers                     # Lite/Pro/Max: usd_per_month, credits_5h, credits_weekly
table.coding_plan.off_peak_multiplier.value # 0.5 (published); reported_peak_multiplier is heuristic=True
table.effort_factor("max", "high")          # EffortFactor(factor=0.5, heuristic=True) | None
```

- Loading fails (pydantic `ValidationError`) for any entry without a `source` URL or a `retrieved` date;
  `tests/test_cost.py` pins this.
- Coding Plan: tier prices, 5-hour and weekly credit caps, the peak window and the 50% off-peak rate are
  published. Credits charged per token are not, so credits-needed figures need a user-supplied, heuristic
  `--credits-per-usd`. The 3x peak burn is an unofficial user report and is flagged `heuristic`.
- Cost math: `glmkit.cost.projection.cost_usd(workload, entry)` = `(input - cache_read) x input + cache_read x
  cached_input + output x output` (input includes cache, output includes reasoning). Missing price gives `None`.
- Cost result envelope: `component: "cost"`, `data` = analysis (totals, causes, verbosity, tokenizer, projection),
  one `cost_trace` item per trace with per-turn forensics in `item.data.turns`. The Cost tab reads the newest one.
---

## 11. Bridge core (`glmkit.bridge.core`, issue 06)

A framework-neutral core: pure functions over JSON bodies and parsed SSE events, no web framework and no HTTP
client. Hosts drive it: the OpenAI proxy (`glmkit.bridge.proxy`, `glmkit bridge serve`), the Anthropic path
(issue 07), the LiteLLM plugin (issue 08). Policies (issue 09 guard) plug in through middleware.

```python
from glmkit.bridge.core import BridgeCore, FixEmitter, get_upstream
core = BridgeCore(get_upstream("zai"), emitter=FixEmitter(log_content=False, sinks=[recorder.on_event]))
ctx   = core.new_context(body)                      # per client call: request_id, tool schemas, retry count
sent  = core.rewrite_request(ctx, body)             # RequestRewriter chain (copy; input is never mutated)
data, failures = core.rewrite_response(ctx, resp)   # ResponseRewriter: repair tool args, normalise reasoning field
rw = core.new_stream(ctx)                           # StreamRewriter: rw.feed(chunk_dict | "[DONE]") -> [events]
cls, client_body = core.classify_error(ctx, status, body_text, headers)   # ErrorClassifier
plan = core.plan_retry(ctx, sent, assistant_msg, failures)                 # RetryPolicy: at most one per call
```

| Piece | Where | Notes |
|---|---|---|
| `UpstreamProfile` (data) | `core/upstream.py`, `UPSTREAMS` | `zai`, `zai-plan`, `cerebras`, `generic`. `upstream_for_endpoint(provider, class)` picks one from an endpoint profile; `--flavor` overrides. Rewriters read fields, never provider names |
| Request rewriters | `core/pipeline.py`, `DEFAULT_REQUEST_REWRITERS` | `(ctx, body) -> body`: `rewrite_thinking` (disabled -> enabled + effort low on forced-thinking models), `rewrite_effort` (clamp to low/high/max), `rewrite_tool_schemas` (flatten top-level oneOf/anyOf), `rewrite_reasoning_replay` (verbatim / rename / strip). Add a fix = add a function to the tuple passed as `request_rewriters=` |
| Response side | `core/toolcalls.py::fix_call`, `core/jsonrepair.py`, `core/schema.py` | Repairs single quotes, trailing commas, fences, double-encoding, truncation; maps flattened-schema args back to one branch; validates against the original schema. Never substitutes `{}` (only when the arguments are empty and the schema accepts `{}`) |
| `StreamRewriter` | `core/stream.py` | Text/reasoning pass through live and in order. Tool-call deltas are buffered per `index` and emitted once, repaired, before the finish chunk. `rw.needs_retry` / `rw.failures` / `rw.assistant_message`, then `rw.resolve(calls)` or `rw.fail()` |
| `ErrorClassifier` | `core/errors.py` | Protocol `classify(status, body, headers) -> ClassifiedError`. `TableErrorClassifier` holds the research/02-bridge.md table (1302, 1305, 1308/1316-1318 with `next_flush_time`, 1310, 1313, 1113/1309/1311/1314/1315, 1210, 1261). To be reconciled with issue 03's taxonomy: implement the protocol on top of it and pass `classifier=` |
| `RetryPolicy` | `core/retry.py` | Protocol `plan_after_tool_failure(request, assistant_message, failures, attempts_so_far) -> RetryPlan | None`. `ParseErrorRetry` (max 1) appends the failed turn plus one `tool` message per failed call with the parse error |
| Fix events | `core/events.py`, `FIX_TITLES` | `ctx.emit(fix_id, before, after, details=, content=)`. `before`/`after` are short summaries; raw values go to `content` and are dropped unless `FixEmitter(log_content=True)` |
| Middleware hook | `core/middleware.py` | Subclass `BridgeMiddleware`; methods may be async: `before_request(ctx, body)` (runs after the rewriters; return a body or `Reject(status, message, code, retry_after_s, fix_id)`), `after_response`, `on_stream_event(ctx, event) -> [events] | Reject`, `on_error`, `on_finish(ctx, usage)` for spend accounting. Pass `middleware=[...]` to `BridgeCore` / `ProxyConfig`. Per-call state: `ctx.extras` |

**Fix ids**: `thinking-disabled-to-low`, `effort-clamp`, `schema-flatten`, `schema-args-unflatten`,
`tool-args-repair`, `tool-args-retry`, `tool-args-unrepaired` (failed), `tool-args-schema-mismatch`,
`reasoning-replay`, `reasoning-normalize`, `error-classify`, `error-sanitize`.

**Proxy** (`glmkit bridge serve --upstream mock --mock-defect bridge-broken-json-args [--flavor ...] [--log-content]`):
`POST .../chat/completions`, `GET /healthz`, `GET /_bridge/info`; `create_app(ProxyConfig, extra_routes=[...])` lets
issue 07 add `/v1/messages`. Errors carry the mapped status, `Retry-After` and `x-should-retry` (the OpenAI SDK
honours both). Responses carry `x-glmkit-bridge-request-id` and `x-glmkit-bridge-fixes`. The upstream key comes from
the profile's env var, otherwise the caller's credentials are forwarded; auth headers are never logged or recorded.
No message content is logged or written unless `--log-content`.

**Session result**: `<out>/<run_id>/result.json`, `component: "bridge"`, one `ItemResult` (`kind: "fix_event"`,
`group` = fix id, `data` = the event) per fix, `data.requests` = per-request records. Rewritten about once a second
and at shutdown. The **Bridge** dashboard tab (`report/pages/bridge.py|js`) reads it.

**Mock defects** (prefixed `bridge-` to avoid registry collisions): `bridge-oneof-empty-args`,
`bridge-broken-json-args`, `bridge-unrepairable-args`, `bridge-reasoning-reject`. Forced thinking and effort
errors need no defect: the mock's default behaviour on the `api` class already rejects them.

**Proof**: `uv run python tools/bridge-proof/proof.py --out results/bridge-demo` (OpenAI SDK loop, direct vs bridged).
## 12. Error taxonomy (`glmkit.zai_errors`, slice 03)

One small module maps a Z.ai business code (table in research/01-conformance.md §2.5) to what a caller needs to
decide. Slice 06 (bridge) reuses it; the Errors dashboard tab and the `wire_*` checks use it too.

```python
from glmkit.zai_errors import classify, retry_after_hint, table_rows, TABLE, CATEGORIES

info = classify("1302", http_status=429, message="Retry after 2 seconds", headers={"retry-after": "2"})
info.code            # "1302" (integer codes are normalised to strings; None when the body has no code)
info.known           # True when the code is in the documented table
info.category        # auth | billing | permission | usage_limit | rate_limit | overloaded | invalid_request |
                     # not_found | context_length | content_safety | server | unknown
info.retryable       # same request may succeed later without a change
info.retry_strategy  # "backoff" | "wait_for_reset" | "none"
info.upstream_http   # status Z.ai documents (1113 arrives as 429)
info.suggested_http  # status a bridge should emit so clients behave (1113 -> 402, 1211 -> 404, 1305 -> 503)
info.retry_after_s   # seconds from a Retry-After header or the message ("retry after 30 seconds",
                     # "resets at 2026-10-26 13:00:00"), else None
```

- Every code in the research table is in `TABLE` (1000-1005, 1113, 1200, 1210-1215, 1220-1222, 1230, 1234, 1261,
  1301, 1302, 1305, 1308-1321). `classify(None, http_status=500)` is a documented server error.
- A code outside the table is never dropped: `known=False`, `category="unknown"`, the code is kept, and
  retryability follows the HTTP status (429 and 5xx retry with backoff, anything else does not).
- `info.to_dict()` and `table_rows()` give JSON-ready dicts. `is_throttle(code)` is true for the rate-limit,
  overloaded and usage-window categories.
- Conformance only reads codes through `NormalizedResponse.error_code`; `wire_error_code_known` fails on an
  undocumented code and the Errors tab groups failures by `category` (plus two non-taxonomy buckets: `transport`
  for no HTTP answer and `contract` for a bad answer without an error code).

### Slice 03 additions to the shared runtime (all optional or additive)

- **Mock behaviour** (`mock/engine.py`): a prompt over 1M estimated tokens is `400/1261`; `response_format:
  {type: json_object}` makes the reply a JSON object; the request header `X-GLMKit-Require-Auth: 1` makes the mock
  enforce credentials for that request (none: 401/1001; the value `glmkit-invalid-key`: 401/1000), which is what
  the `auth_probe` executor uses. Real hosts ignore that header.
- **Executors** (`conform/executors/wire.py`): `wire_single` (padding, case-level stream timeout), `effort_sweep`,
  `paired_stream`, `auth_probe`, `corpora`, `burst`, `thinking_roundtrip`. `ProbeClient` there is a `GLMClient` with
  per-request credentials and header removal.
- **Checks** (`conform/checks/wire.py`): all named `wire_*`. A check that reads a specific exchange of a
  multi-request executor takes `exchange: <label>` (labels: `auth:<probe>`, `cross:<probe>`, `effort:<lvl>#<n>`,
  `corpus:<name>` / `ref:<name>`, `burst:<i>`, `turn1` / `turn2`, `nonstream` / `stream`).
- **Token reference** for ANTH-004: the host's `count_tokens` answer, or a JSON file `{"corpus": tokens}` named by
  `$GLMKIT_TOKEN_REFERENCE` (measured on the official API for the same model).
- **Cases carry `$ref` only inside lists or free-form dicts** (`executor_config`): the case JSON schema does not
  allow `{$ref}` in place of `tools`, so shared tool lists are written inline.
- **Tests**: a slice may keep its check regression rows in its own module as `CHECK_CASES`; the meta test in
  `tests/test_checks.py` collects them.
