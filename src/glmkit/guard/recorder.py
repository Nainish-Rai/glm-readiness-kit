"""Guard session recorder: writes ``<out>/<run_id>/result.json`` (``component: guard``) for the Guard tab.

One ``ItemResult`` (kind ``guard_event``) per intervention (loop, blocked/ask command, budget limit). Per-session
budget burn-down, counters and the audit-chain status live in ``envelope.data``. No message content is recorded:
tool names, rule ids, counts and argument hashes only.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glmkit.contracts.envelope import ItemResult, ResultEnvelope, new_run_id
from glmkit.guard.audit import verify
from glmkit.results import write_envelope

KINDS = ("loop", "command", "budget")


class GuardRecorder:
    def __init__(
        self, out_dir: Path, audit_path: Path, config: dict[str, Any], snapshot: Callable[[], dict[str, Any]]
    ) -> None:
        self.out_dir, self.audit_path, self.snapshot = Path(out_dir), Path(audit_path), snapshot
        self.env = ResultEnvelope(
            run_id=new_run_id("guard"), component="guard", started_at=datetime.now(UTC), config=config
        )
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._audit_cache: tuple[float, dict[str, Any]] | None = None

    def event(self, kind: str, action: str, **fields: Any) -> None:
        """kind: loop | command | budget; action: inject | block | deny | ask | warn | allow-logged ..."""
        ev = {"ts": datetime.now(UTC).isoformat(), "kind": kind, "action": action, **fields}
        with self._lock:
            self.events.append(ev)
            n = len(self.events)
            soft = action in ("inject", "ask", "warn")
            self.env.items.append(
                ItemResult(
                    id=f"{kind}#{n}",
                    group=kind,
                    title=f"{kind}: {action}" + (f" {fields['tool']}" if fields.get("tool") else ""),
                    kind="guard_event",
                    category=kind,
                    severity="info",
                    status="soft_fail" if soft else "fail",
                    reason=fields.get("reason"),
                    labels={k: str(fields[k]) for k in ("session", "agent", "rule") if fields.get(k)},
                    data=ev,
                )
            )

    def audit_status(self, max_age_s: float = 1.0) -> dict[str, Any]:
        now = time.monotonic()
        if self._audit_cache and now - self._audit_cache[0] < max_age_s:
            return self._audit_cache[1]
        res = verify(self.audit_path).to_dict() if self.audit_path.exists() else None
        out = res or {"ok": None, "entries": 0, "error": "no audit entries yet", "path": str(self.audit_path)}
        self._audit_cache = (now, out)
        return out

    def flush(self) -> Path:
        with self._lock:
            snap = self.snapshot()
            self.env.data = {
                **snap,
                "audit": self.audit_status(0),
                "counts": {k: sum(1 for e in self.events if e["kind"] == k) for k in KINDS},
            }
            copy = self.env.model_copy(deep=True)
        return write_envelope(copy.finalize(), self.out_dir)
