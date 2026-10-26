"""A planted fake key must never reach results, traces, logs or console output, even when the host echoes it."""

from __future__ import annotations

from typer.testing import CliRunner

from glmkit.cli import build_app

FAKE_KEY = "sk-glmkit-PLANTED-0123456789abcdef"


def test_planted_key_never_written(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GLMKIT_MOCK_API_KEY", FAKE_KEY)
    for target in ("mock", "mock-anthropic"):
        res = CliRunner().invoke(
            build_app(), ["conform", "run", "-t", target, "-c", "WIRE-00*", "-D", "auth-echo", "-o", str(tmp_path)]
        )
        assert res.exit_code == 0, res.output
        assert FAKE_KEY not in res.output
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert {p.name for p in files} >= {"result.json", "traces.jsonl", "run.log"}
    leaked = [str(p) for p in files if FAKE_KEY in p.read_text(encoding="utf-8")]
    assert leaked == []
    # the echo really happened upstream and was scrubbed, not just never sent
    assert any("[REDACTED]" in p.read_text() and "debug" in p.read_text() for p in files if p.name == "result.json")
    # and the key was sent: the mock saw it (echoed) and the evidence recorded the header as redacted
    assert all(
        '"authorization": "[REDACTED]"' in p.read_text() or '"x-api-key": "[REDACTED]"' in p.read_text()
        for p in files
        if p.name == "result.json"
    )
