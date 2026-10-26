"""Hash-chained, append-only audit log (JSONL).

Every line is the canonical JSON (sorted keys, compact, ASCII) of one entry::

    {"seq": 7, "ts": "...", "prev": "<hash of entry 6>", ..., "hash": "<sha256(prev + canonical(entry without hash))>"}

``prev`` of entry 0 is 64 zeros. ``verify`` re-derives everything from the bytes on disk, and also requires each
line to be *byte-identical* to its canonical form, so even whitespace-only edits are caught. It detects an edited
entry (hash), a deleted or reordered entry (prev / seq), an inserted line (prev / canonical form) and, through the
``<log>.head`` sidecar, a truncated tail. A sidecar is not a trust anchor: an attacker who rewrites both files
consistently is only stopped by anchoring the head hash elsewhere (``glmkit guard verify --head``).

The log stores tool names, decisions, reasons and argument *hashes*. Full arguments are stored only when the
writer was created with ``store_args=True`` (``--guard-log-args``).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # POSIX advisory lock so the bridge and the Claude Code hook can append to one log
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

GENESIS = "0" * 64
ENV_AUDIT = "GLMKIT_GUARD_AUDIT"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def args_hash(args: Any) -> str:
    return sha256_hex(canonical(args))


def entry_hash(prev: str, body: dict[str, Any]) -> str:
    """``body`` is the entry without its ``hash`` field."""
    return sha256_hex(prev + canonical(body))


def default_audit_path() -> Path:
    env = os.environ.get(ENV_AUDIT)
    return Path(env) if env else Path.home() / ".glmkit" / "guard" / "audit.jsonl"


@dataclass
class VerifyResult:
    ok: bool
    entries: int
    head: str | None = None
    error: str | None = None
    error_seq: int | None = None
    path: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "entries": self.entries,
            "head": self.head,
            "error": self.error,
            "error_seq": self.error_seq,
            "path": self.path,
            "checked_at": self.checked_at,
        }


def verify(path: Path, *, expect_head: str | None = None) -> VerifyResult:
    path = Path(path)
    if not path.exists():
        return VerifyResult(False, 0, error="log file not found", path=str(path))
    prev, n = GENESIS, 0
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        return VerifyResult(False, 0, error="last line is not newline-terminated", error_seq=0, path=str(path))
    lines = raw.split(b"\n")[:-1] if raw else []

    def bad(msg: str, seq: int) -> VerifyResult:
        return VerifyResult(False, n, head=prev if n else None, error=msg, error_seq=seq, path=str(path))

    for i, line in enumerate(lines):
        try:
            text = line.decode("utf-8")
            entry = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            return bad(f"line {i}: not valid JSON (entry edited)", i)
        if not isinstance(entry, dict) or "hash" not in entry:
            return bad(f"line {i}: not an audit entry", i)
        if text != canonical(entry):
            return bad(f"line {i}: bytes differ from the canonical form (entry edited)", i)
        if entry.get("seq") != i:
            return bad(f"line {i}: seq is {entry.get('seq')!r} (entry deleted, inserted or reordered)", i)
        if entry.get("prev") != prev:
            return bad(f"line {i}: prev hash does not match the previous entry (entry deleted or reordered)", i)
        body = {k: v for k, v in entry.items() if k != "hash"}
        if entry_hash(prev, body) != entry["hash"]:
            return bad(f"line {i}: hash mismatch (entry edited)", i)
        prev, n = entry["hash"], n + 1
    head = prev if n else None
    sidecar = Path(str(path) + ".head")
    if sidecar.exists():
        try:
            h = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return VerifyResult(False, n, head, "head sidecar unreadable", None, str(path))
        if h.get("seq") != n or (n and h.get("hash") != head):
            return VerifyResult(
                False,
                n,
                head,
                f"head sidecar says {h.get('seq')} entries, log has {n} (tail truncated or log replaced)",
                None,
                str(path),
            )
    if expect_head and expect_head != head:
        return VerifyResult(False, n, head, "head hash differs from the anchored value", None, str(path))
    return VerifyResult(True, n, head, path=str(path))


def read_entries(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


class AuditLog:
    """Append-only writer. Safe across threads and (POSIX) processes sharing the same file."""

    def __init__(self, path: Path | str | None = None, *, store_args: bool = False) -> None:
        self.path = Path(path) if path else default_audit_path()
        self.store_args = store_args
        self._lock = threading.Lock()

    @contextmanager
    def _locked(self):  # type: ignore[no-untyped-def]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.path, "ab+") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield f
            finally:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_UN)

    @staticmethod
    def _last(f: Any) -> tuple[int, str]:
        """(next seq, hash of the last entry) read from the end of the file."""
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return 0, GENESIS
        block = min(size, 65536)
        while True:
            f.seek(size - block)
            tail = f.read(block)
            parts = tail.rstrip(b"\n").split(b"\n")
            if len(parts) > 1 or block == size:
                break
            block = min(size, block * 4)
        try:
            last = json.loads(parts[-1])
            return int(last["seq"]) + 1, str(last["hash"])
        except (ValueError, KeyError, TypeError):
            return -1, GENESIS  # corrupt tail: refuse to extend silently

    def append(
        self,
        *,
        event: str,
        source: str,
        decision: str,
        reason: str = "",
        tool: str | None = None,
        agent: str | None = None,
        session: str | None = None,
        request_id: str | None = None,
        rule: str | None = None,
        args: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._locked() as f:
            seq, prev = self._last(f)
            if seq < 0:
                raise RuntimeError(f"{self.path}: tail of the audit log is corrupt; run `glmkit guard verify`")
            body: dict[str, Any] = {
                "seq": seq,
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "prev": prev,
                "event": event,
                "source": source,
                "decision": decision,
                "reason": reason,
                "tool": tool,
                "agent": agent,
                "session": session,
                "request_id": request_id,
                "rule": rule,
            }
            if args is not None:
                body["args_sha256"] = args_hash(args)
                if self.store_args:
                    body["args"] = args
            if extra:
                body["extra"] = extra
            entry = {**body, "hash": entry_hash(prev, body)}
            f.seek(0, os.SEEK_END)
            f.write((canonical(entry) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
            self._write_head(seq + 1, entry["hash"])
        return entry

    def _write_head(self, n: int, h: str) -> None:
        sidecar = Path(str(self.path) + ".head")
        tmp = sidecar.with_name(sidecar.name + ".tmp")
        tmp.write_text(json.dumps({"seq": n, "hash": h}), encoding="utf-8")
        os.replace(tmp, sidecar)

    def verify(self) -> VerifyResult:
        return verify(self.path)
