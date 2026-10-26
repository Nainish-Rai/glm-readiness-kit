"""Audit log: append-only hash chain; verify detects any edit, deletion, insertion, reorder or truncation."""

from __future__ import annotations

import threading
from pathlib import Path

from typer.testing import CliRunner

from glmkit.cli import build_app
from glmkit.guard.audit import AuditLog, read_entries, verify


def make(tmp_path: Path, n: int = 6, **kw) -> tuple[AuditLog, Path]:
    log = AuditLog(tmp_path / "a.jsonl", **kw)
    for i in range(n):
        log.append(
            event="tool_call", source="t", decision="allow", tool=f"t{i}", session="s", args={"command": f"ls {i}"}
        )
    return log, log.path


def test_untouched_log_verifies(tmp_path: Path) -> None:
    _, p = make(tmp_path)
    r = verify(p)
    assert r.ok and r.entries == 6 and r.head


def test_one_byte_edit_fails(tmp_path: Path) -> None:
    _, p = make(tmp_path)
    data = bytearray(p.read_bytes())
    data[data.index(b'"allow"') + 1] ^= 0x20  # a -> A
    p.write_bytes(bytes(data))
    r = verify(p)
    assert not r.ok and "edited" in (r.error or "")


def test_whitespace_only_edit_fails(tmp_path: Path) -> None:
    _, p = make(tmp_path)
    p.write_bytes(p.read_bytes().replace(b'"seq":2,', b'"seq":2, ', 1))
    assert not verify(p).ok


def test_deleted_reordered_inserted_truncated(tmp_path: Path) -> None:
    _, p = make(tmp_path)
    lines = p.read_bytes().split(b"\n")[:-1]
    p.write_bytes(b"\n".join(lines[:2] + lines[3:]) + b"\n")
    assert not verify(p).ok  # deleted
    p.write_bytes(b"\n".join([lines[1], lines[0], *lines[2:]]) + b"\n")
    assert not verify(p).ok  # reordered
    p.write_bytes(b"\n".join([*lines[:3], lines[1], *lines[3:]]) + b"\n")
    assert not verify(p).ok  # duplicated line inserted
    p.write_bytes(b"\n".join(lines[:4]) + b"\n")
    r = verify(p)
    assert not r.ok and "truncated" in (r.error or "")  # tail cut: caught by the head sidecar
    p.write_bytes(b"\n".join(lines) + b"\n")
    assert verify(p).ok  # restoring the original bytes passes again


def test_arguments_stored_only_with_flag(tmp_path: Path) -> None:
    _, p = make(tmp_path, 1)
    e = read_entries(p)[0]
    assert "args" not in e and len(e["args_sha256"]) == 64 and "ls 0" not in p.read_text()
    _, p2 = make(tmp_path / "x", 1, store_args=True)
    assert read_entries(p2)[0]["args"] == {"command": "ls 0"}


def test_concurrent_appends_keep_chain(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "c.jsonl")
    other = AuditLog(tmp_path / "c.jsonl")  # a second writer, as the hook is a second process

    def work(w: AuditLog) -> None:
        for i in range(20):
            w.append(event="e", source="t", decision="allow", tool=str(i))

    ts = [threading.Thread(target=work, args=(w,)) for w in (log, other, log)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    r = verify(log.path)
    assert r.ok and r.entries == 60


def test_cli_verify_exit_codes(tmp_path: Path) -> None:
    _, p = make(tmp_path)
    runner = CliRunner()
    app = build_app()
    assert runner.invoke(app, ["guard", "verify", str(p)]).exit_code == 0
    p.write_bytes(p.read_bytes().replace(b"t3", b"t4", 1))
    res = runner.invoke(app, ["guard", "verify", str(p)])
    assert res.exit_code == 1 and "FAIL" in res.output
