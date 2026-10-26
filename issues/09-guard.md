# 09 · Spend and loop guard

Status: done · Type: AFK · Label: ready-for-agent · Branch: issue/09-guard

## What to build

A guard layer that runs as bridge middleware, and as a Claude Code / opencode hook where possible, to stop runaway
agents. This answers two things: the refund requests in GLM-5 #172/#173 (an 86.2M-token shell loop and 228M wasted
tokens), and the enterprise question "which agent acted, what was it authorized to do, and can we prove it?"

- Per-session and per-key **token and spend budgets** with soft (warn) and hard (block) limits. Cost comes from a
  versioned price table. Share it with slice 12 if merged; otherwise ship a minimal table and reconcile at merge.
- A **loop detector**: the same tool + near-identical arguments failing N times, or the same error output repeated.
  It intervenes by injecting a stop/rethink message or blocking, and the action is configurable.
- A **destructive-command gate**: a policy file of patterns such as `rm -rf`, `git push --force` and `DROP TABLE`,
  allow/deny/ask per pattern, evaluated on tool-call arguments before they're returned to the client.
- A **hash-chained audit log** (append-only JSONL, each entry hashing the previous) recording which agent/session,
  which tool, which decision and why. Ship a `glmkit guard verify` command that detects tampering.
- Dashboard: a **Guard** tab with budget burn-down per session, loop interventions, blocked commands, and audit-chain
  integrity status.

## Acceptance criteria

- [x] A scripted runaway agent against the mock (repeating a failing shell command) is stopped within the configured
      N repetitions and before the hard budget (evidence: without guard it runs to the cap; with guard it stops).
- [x] Destructive-command policy blocks or asks per pattern; unit tests cover each default pattern and false
      positives on safe look-alikes.
- [x] `glmkit guard verify` passes on an untouched log and fails after a one-byte edit.
- [x] Guard tab renders the mock session; token check passes; no console errors at 1440/390 px.

## Blocked by

- 06 Bridge core + OpenAI-compatible proxy

## Evidence

### Runaway agent, with and without the guard

`uv run python tools/guard-proof/proof.py --out results/guard-demo` (mock GLM, scripted agent that keeps running a failing `make test`; client cap 40 turns; hard budget $0.10 at glm-5.3 prices from `glmkit.cost.prices`; N = 3):

```
WITHOUT guard : executed 40 times, stopped by cap, spend $0.3725
WITH guard (loop action=block, N=3): executed 3 times, stopped by http-400 (guard_loop_blocked), spend $0.0202
WITH guard (inject then block, N=3+2), agent ignores the message: executed 5, stopped by http-400, spend $0.0340
WITH guard (inject), agent heeds the message: executed 3, stopped by model: 'Stopping: the build target is missing.'
WITH guard (loop detector OFF, hard budget $0.06): executed 9, stopped by http-402 (guard_budget_exceeded), spend $0.0614
destructive: stream=False/True: executed [], agent saw: '[glmkit-guard] The tool call `shell` was not run: it is not allowed by policy rule `rm-root-or-h...'
git push --force-with-lease is allowed (safe look-alike)
```

### Audit chain: verify passes, then fails after a one-byte edit

```
$ uv run glmkit guard verify results/guard-demo/audit.jsonl
OK  34 entries, head a41103654c4b811733b9da308163d68cc7f10a71e403f88534fea60b64560569  (results/guard-demo/audit.jsonl)   exit=0
$ (copy; change one byte: "deny" -> "Deny" on entry 28)
$ uv run glmkit guard verify /tmp/audit.copy.jsonl
FAIL  line 28: hash mismatch (entry edited)  (/tmp/audit.copy.jsonl; 28 entries verified before the break)   exit=1
```
`tests/test_guard_audit.py` also covers whitespace-only edits, deletion, reorder, inserted line, truncated tail, concurrent writers (two writers + threads), and arguments never stored unless asked.

### Policy tests (`tests/test_guard_policy.py`: 33 hit cases, 27 safe look-alikes, every default rule has a hit case)

```
$ glmkit guard check -c "rm -rf /"          -> deny  rule=rm-root-or-home
$ glmkit guard check -c 'echo "rm -rf /"'   -> allow
```
Look-alikes that pass: `git push --force-with-lease`, `git commit -m "git push --force origin main"`, `rm -f file`, `git clean -n -fd`, `DELETE FROM t WHERE ...`, `drop tables` in prose, `dd of=./disk.img`, `curl | jq`, `kubectl delete pod`.

### Dashboard

Token check, first on a planted raw colour (`.burn .bad { fill: #ff0000; }` appended to `guard.css`), then clean:
```
site/index.html:88 raw-color #ff0000
token check: 1 finding(s)
...
token check: 0 finding(s)
```
Headless Chrome (`--headless=new`) on the built site at `#guard`, 1440 and 390 px: screenshots viewed; no CONSOLE lines in Chrome's log; a probe at 390 px shows `scrollWidth == clientWidth` (tables scroll inside their cards). Tab shows burn-down with soft/hard lines, sessions, 6 loop interventions, 2 blocked commands, budget limits and audit chain "intact, 34 entries".

### Not verified

Anthropic `tool_use` gating is unit-tested on synthetic events only (issue 07 not merged); the Claude Code hook was run with piped JSON and a subprocess, not inside a live Claude Code session; opencode hook not shipped.
