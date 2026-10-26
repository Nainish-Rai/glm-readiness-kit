# 09 · Spend and loop guard

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/09-guard

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

- [ ] A scripted runaway agent against the mock (repeating a failing shell command) is stopped within the configured
      N repetitions and before the hard budget (evidence: without guard it runs to the cap; with guard it stops).
- [ ] Destructive-command policy blocks or asks per pattern; unit tests cover each default pattern and false
      positives on safe look-alikes.
- [ ] `glmkit guard verify` passes on an untouched log and fails after a one-byte edit.
- [ ] Guard tab renders the mock session; token check passes; no console errors at 1440/390 px.

## Blocked by

- 06 Bridge core + OpenAI-compatible proxy

## Evidence
