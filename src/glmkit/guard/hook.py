"""Claude Code PreToolUse hook: the destructive-command policy plus the shared audit log.

Reads the hook JSON from stdin and prints a ``permissionDecision`` for ``deny`` / ``ask`` rules. Allowed calls
print nothing, so Claude Code's own permission flow is untouched (the hook never auto-approves). Every call is
appended to the audit log (``$GLMKIT_GUARD_AUDIT``, same file as the bridge), as tool name, decision, rule and an
argument hash. Full arguments only with ``GLMKIT_GUARD_LOG_ARGS=1``. A policy file can be set with
``GLMKIT_GUARD_POLICY``. Internal errors fail safe to ``ask``.

    python -m glmkit.guard.hook < event.json        (see docs/guard.md for settings.json)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from glmkit.guard.audit import AuditLog
from glmkit.guard.policy import Decision, load_policy

SOURCE = "claude-code-hook"


def decide(
    payload: dict[str, Any], *, audit: AuditLog | None = None, policy_path: str | None = None
) -> dict[str, Any] | None:
    """The hook output dict for ``payload``, or None to defer to Claude Code's normal permission flow."""
    tool = str(payload.get("tool_name") or "")
    args = payload.get("tool_input") or {}
    try:
        d = load_policy(policy_path).evaluate(tool, args)
    except Exception as e:  # fail safe: ask, never silently allow when the policy cannot run
        d = Decision("ask", "guard-error", f"guard could not evaluate this call ({type(e).__name__})")
    if audit is not None:
        try:
            audit.append(
                event="tool_call",
                source=SOURCE,
                decision=d.action,
                reason=d.reason if d.action != "allow" else "no rule matched",
                tool=tool,
                agent=os.environ.get("GLMKIT_GUARD_AGENT") or "claude-code",
                session=payload.get("session_id"),
                rule=d.rule,
                args=args,
            )
        except Exception as e:
            print(f"glmkit-guard: audit log not written ({e})", file=sys.stderr)
    if d.action == "allow":
        return None
    reason = f"glmkit guard rule `{d.rule}`: {d.reason}"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": d.action,
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        print("glmkit-guard: stdin is not JSON", file=sys.stderr)
        return 1
    audit = AuditLog(store_args=os.environ.get("GLMKIT_GUARD_LOG_ARGS") == "1")
    out = decide(payload, audit=audit, policy_path=os.environ.get("GLMKIT_GUARD_POLICY"))
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
