# Guard: spend and loop guard (issue 09)

`glmkit.guard` stops runaway agents (GLM-5 #172/#173: an 86.2M-token shell loop, 228M wasted tokens) and answers
"which agent acted, what was it allowed to do, can we prove it?". Four parts, one audit log.

| Part | What it does | Config (`guard.yaml`) |
|---|---|---|
| Budgets | Per-session and per-key token and USD limits. Soft = warning event + audit entry, hard = next request rejected (HTTP 402, code `guard_budget_exceeded`). Prices only from `glmkit.cost.prices`; an unpriced call leaves USD a lower bound ("not logged"), tokens still count | `session_budget`, `key_budget` (`soft_usd`, `hard_usd`, `soft_tokens`, `hard_tokens`), `price_host` |
| Loop detector | Reads the tool calls and results the client sends back. N identical-or-near-identical (similarity >= 0.9) calls that all failed, or N identical error outputs, in a row. Action `inject` (stop/rethink user message), `block` (HTTP 400 `guard_loop_blocked`) or `inject_then_block` (block after `escalate_after` more repeats) | `loop.repeat_threshold` (N, default 3), `loop.action`, `loop.escalate_after`, `loop.similarity` |
| Destructive-command gate | `default_policy.yaml`: allow / deny / ask per pattern on tool-call arguments, before the client sees the call. Shell is tokenised (quotes are data, `sudo`/`env`/`bash -c`/`$(...)` unwrapped). A blocked call is replaced by a short assistant message and the finish reason becomes `stop` | `policy` (path; `include_default: true` keeps the defaults), `ask_action: block|allow` |
| Audit log | Append-only JSONL, one hash chain shared by the bridge and the Claude Code hook | `audit_path` (or `$GLMKIT_GUARD_AUDIT`), `log_args` |

## Turn it on

```bash
uv run glmkit bridge serve --upstream mock --guard --guard-config guard.yaml --guard-audit audit.jsonl
```

`--guard` alone uses defaults (policy on, loop detector on, no budgets). Clients may send `x-glmkit-agent` and
`x-glmkit-session`; the session otherwise comes from `user` / `metadata` or a fingerprint of the conversation start.
The key budget uses a fingerprint of the credential (never the credential). The Guard tab reads the session
envelope (`component: guard`) the middleware writes next to the bridge session. In code:
`ProxyConfig(..., middleware=[GuardMiddleware(GuardConfig(...), out_dir=...)])`.

Hosts that speak the Anthropic protocol should set `ctx.extras["protocol"] = "anthropic"` (so no OpenAI-only
`stream_options` is added). Anthropic `tool_use` blocks are gated in responses and streams (unit-tested on
synthetic events; not yet exercised against issue 07's path).

## Audit log

Each line is the canonical JSON of an entry: `seq`, `ts`, `prev`, `event`, `source` (`bridge` / `claude-code-hook`),
`decision`, `reason`, `tool`, `agent`, `session`, `request_id`, `rule`, `args_sha256`, `hash`, where
`hash = sha256(prev + canonical(entry without hash))`. By default no arguments are stored (hash only); `--guard-log-args` /
`log_args: true` / `GLMKIT_GUARD_LOG_ARGS=1` stores them. Message content is never logged.

`glmkit guard verify [LOG]` exits 1 on any edit (even whitespace), deletion, insertion or reorder. A truncated
tail is caught by the `<log>.head` sidecar; that sidecar is not a trust anchor, so for real tamper evidence copy the
head hash that `verify` prints to somewhere else and pass it back with `--head`.

## Claude Code PreToolUse hook

`python -m glmkit.guard.hook` (also `glmkit guard hook`) applies the same policy to Claude Code tool calls and appends to
the same audit log. It prints a `permissionDecision` of `deny` or `ask` for matching calls and nothing otherwise, so
Claude Code's normal permission flow still decides everything else (the hook never auto-approves). Internal errors
fail safe to `ask`. Nothing installs it for you. To use it, add to `~/.claude/settings.json` (or the project's
`.claude/settings.json`), replacing the path with this checkout:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "GLMKIT_GUARD_AUDIT=$HOME/.glmkit/guard/audit.jsonl uv run --project /path/to/glm-readiness-kit python -m glmkit.guard.hook"
          }
        ]
      }
    ]
  }
}
```

Environment: `GLMKIT_GUARD_AUDIT` (log; share it with the bridge), `GLMKIT_GUARD_POLICY` (custom policy YAML),
`GLMKIT_GUARD_AGENT` (agent name, default `claude-code`), `GLMKIT_GUARD_LOG_ARGS=1`. Test without installing:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}' | GLMKIT_GUARD_AUDIT=/tmp/a.jsonl uv run python -m glmkit.guard.hook
glmkit guard check -c "rm -rf /"        # policy only, exit 0 allow / 3 ask / 2 deny
```

opencode: no equivalent hook point is shipped; run opencode through the bridge with `--guard`.

## Policy file

```yaml
include_default: true          # keep the shipped rules behind yours (yours are checked first)
rules:
  - {id: allow-build-clean, action: allow, regex: '^rm -rf \./build$'}
  - {id: no-prod-deploy, action: deny, kind: command, regex: '^deploy\b.*--prod', description: no prod deploys}
```

`kind: command` matches each normalised shell segment (anchor with `^`); `kind: text` matches the whole raw string
(SQL, pipelines). Optional `unless` regex and `tools` globs. Default rules: `glmkit guard policy`.
Known limits: a regex policy is a safety net, not a sandbox (obfuscated commands, scripts that delete things
internally, and file contents are not inspected); `kind: text` rules also fire on a quoted `DROP TABLE` inside `echo`.

## Result envelope (`component: guard`)

`data.sessions[]`: `session`, `agent`, `model`, `requests`, `tokens`, `usd` (lower bound), `unpriced_calls`, `budget`,
`burn[]` (`ts`, `request_id`, `tokens`, `usd`, `cum_tokens`, `cum_usd`). `data.audit`: the verify result. `data.counts`.
One `guard_event` item per intervention (`data` = kind loop | command | budget, action, session, agent, tool, rule, reason).

Proof: `uv run python tools/guard-proof/proof.py --out results/guard-demo`.
