"""Session recorder: turns fix events and request records into a result envelope (``component: bridge``).

One ``ItemResult`` (kind ``fix_event``) per fix event; per-request records live in ``envelope.data.requests``.
The envelope is rewritten on a timer and at shutdown, so the dashboard can show a live session. No message
content is recorded unless the emitter was created with ``log_content=True``.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glmkit.bridge.core.events import FixEvent
from glmkit.contracts.envelope import ItemResult, ResultEnvelope, TargetInfo, new_run_id
from glmkit.contracts.profile import EndpointProfile
from glmkit.redact import Redactor
from glmkit.results import write_envelope

MAX_REQUESTS = 5000


@dataclass
class RequestRecord:
    request_id: str
    ts: str
    path: str
    stream: bool
    model: str | None
    status: int | None = None
    upstream_status: int | None = None
    duration_ms: float | None = None
    retried: bool = False
    fixes: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None


class SessionRecorder:
    def __init__(
        self,
        profile: EndpointProfile,
        out_dir: Path,
        *,
        flavor: str,
        config: dict[str, Any] | None = None,
        redactor: Redactor | None = None,
        source: str | None = None,
    ) -> None:
        self.out_dir = out_dir
        self.redactor = redactor
        self.source = source  # which adapter produced the session: None/"proxy" (standalone), "litellm", ...
        self.env = ResultEnvelope(
            run_id=new_run_id("bridge"),
            component="bridge",
            started_at=datetime.now(UTC),
            targets=[TargetInfo.from_profile(profile)],
            config={"flavor": flavor, **({"source": source} if source else {}), **(config or {})},
        )
        self.requests: list[RequestRecord] = []
        self._lock = threading.Lock()
        self._dirty = True
        self._n = 0

    # sink for FixEmitter
    def on_event(self, ev: FixEvent) -> None:
        with self._lock:
            self._n += 1
            self.env.items.append(
                ItemResult(
                    id=f"{ev.request_id}/{ev.fix_id}#{self._n}",
                    group=ev.fix_id,
                    title=ev.title,
                    kind="fix_event",
                    category=ev.fix_id,
                    target=self.env.targets[0].profile,
                    labels={
                        "fix_id": ev.fix_id,
                        "request_id": ev.request_id,
                        "upstream": ev.upstream or "",
                        **({"source": self.source} if self.source else {}),
                    },
                    status="pass" if ev.ok else "fail",
                    reason=f"{ev.before} -> {ev.after}",
                    data={**ev.to_dict(), **({"source": self.source} if self.source else {})},
                )
            )
            self._dirty = True

    def add_request(self, rec: RequestRecord) -> None:
        with self._lock:
            self.requests.append(rec)
            del self.requests[:-MAX_REQUESTS]
            self._dirty = True

    def flush(self, *, final: bool = False) -> Path | None:
        with self._lock:
            if not self._dirty and not final:
                return None
            self._dirty = False
            self.env.data = {
                "requests": [asdict(r) for r in self.requests],
                "fix_event_count": len(self.env.items),
            }
            snapshot = self.env.model_copy(deep=True)
        snapshot.finalize()
        if not final:
            snapshot.finished_at = None
        return write_envelope(snapshot, self.out_dir, self.redactor)
