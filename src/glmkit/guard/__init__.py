"""Spend and loop guard (issue 09): budgets, loop detector, destructive-command gate and a hash-chained audit log.

The guard is a ``BridgeMiddleware`` (``GuardMiddleware``) and also ships a Claude Code PreToolUse hook
(``python -m glmkit.guard.hook``). See ``docs/guard.md``. Names are imported lazily so the hook starts fast.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "AuditLog": "glmkit.guard.audit",
    "VerifyResult": "glmkit.guard.audit",
    "verify": "glmkit.guard.audit",
    "Budget": "glmkit.guard.config",
    "GuardConfig": "glmkit.guard.config",
    "LoopConfig": "glmkit.guard.config",
    "GuardMiddleware": "glmkit.guard.middleware",
    "Decision": "glmkit.guard.policy",
    "Policy": "glmkit.guard.policy",
    "load_policy": "glmkit.guard.policy",
}
__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return getattr(importlib.import_module(_EXPORTS[name]), name)
    raise AttributeError(name)
