"""Guard as bridge middleware, against the mock and the scripted runaway agent."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from glmkit.bridge.core import get_upstream
from glmkit.bridge.proxy import ProxyConfig, create_app
from glmkit.bridge.server import BridgeServer
from glmkit.contracts.profile import get_profile
from glmkit.cost.prices import price_for
from glmkit.guard import GuardMiddleware, verify
from glmkit.guard import loop as loopmod
from glmkit.guard.audit import read_entries
from glmkit.guard.budget import cost_of, normalize_usage
from glmkit.guard.config import Budget, GuardConfig, LoopConfig
from glmkit.guard.hook import decide
from glmkit.mock import MockConfig, MockServer
from glmkit.results import load_envelopes
from tests.guard_agent import rules, run_agent

N = 3
CAP = 25


@pytest.fixture(scope="module")
def mock() -> Iterator[MockServer]:
    with MockServer(MockConfig(rules=rules())) as m:
        yield m


@pytest.fixture(scope="module")
def mock_obedient() -> Iterator[MockServer]:
    with MockServer(MockConfig(rules=rules(obedient=True))) as m:
        yield m


def run(
    m: MockServer, mw: GuardMiddleware | None, tmp: Path, session: str = "s1", prompt: str = "RUNAWAY go", **kw: Any
):
    profile = get_profile("mock").model_copy(update={"base_url": m.url + "/api/paas/v4", "spawn_mock": False})
    app = create_app(
        ProxyConfig(
            profile=profile,
            upstream=get_upstream("zai"),
            out_dir=tmp,
            middleware=[mw] if mw else [],
            flush_interval_s=60,
        )
    )
    with BridgeServer(app) as b:
        return run_agent(
            b.url + "/v1",
            prompt,
            max_turns=CAP,
            headers={"x-glmkit-session": session, "x-glmkit-agent": "agent-a"},
            **kw,
        )


def guard(tmp: Path, **kw: Any) -> GuardMiddleware:
    cfg = GuardConfig(audit_path=str(tmp / "audit.jsonl"), **kw)
    return GuardMiddleware(cfg, out_dir=tmp)


def test_runaway_without_guard_runs_to_cap(mock: MockServer, tmp_path: Path) -> None:
    r = run(mock, None, tmp_path)
    assert len(r["executed"]) == CAP and r["stopped_by"] == "cap"


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "stream"])
def test_loop_block_stops_within_n_and_before_hard_budget(mock: MockServer, tmp_path: Path, stream: bool) -> None:
    g = guard(tmp_path, session_budget=Budget(hard_usd=0.10), loop=LoopConfig(repeat_threshold=N, action="block"))
    r = run(mock, g, tmp_path, stream=stream)
    assert len(r["executed"]) <= N and r["stopped_by"] == "http-400" and r["error"] == "guard_loop_blocked"
    assert g.budget.sessions["s1"].usd < 0.10
    assert verify(tmp_path / "audit.jsonl").ok


def test_inject_then_block_and_obedient_agent(mock: MockServer, mock_obedient: MockServer, tmp_path: Path) -> None:
    g = guard(tmp_path, loop=LoopConfig(repeat_threshold=N, action="inject_then_block", escalate_after=2))
    r = run(mock, g, tmp_path)
    assert len(r["executed"]) == N + 2 and r["stopped_by"] == "http-400"
    r2 = run(mock_obedient, g, tmp_path, session="s2")
    assert len(r2["executed"]) == N and r2["stopped_by"] == "model" and "Stopping" in r2["final"]
    kinds = [(e["kind"], e["action"]) for e in g.recorder.events if e["kind"] == "loop"]
    assert ("loop", "inject") in kinds and ("loop", "block") in kinds


def test_hard_budget_alone_stops_and_soft_warns(mock: MockServer, tmp_path: Path) -> None:
    g = guard(tmp_path, session_budget=Budget(soft_usd=0.02, hard_usd=0.05), loop=LoopConfig(enabled=False))
    r = run(mock, g, tmp_path)
    assert r["error"] == "guard_budget_exceeded" and r["stopped_by"] == "http-402" and len(r["executed"]) < CAP
    actions = [e["action"] for e in g.recorder.events if e["kind"] == "budget"]
    assert actions[0] == "warn" and "block" in actions


def test_token_budget_and_key_budget(mock: MockServer, tmp_path: Path) -> None:
    g = guard(tmp_path, key_budget=Budget(hard_tokens=5000), loop=LoopConfig(enabled=False))
    r = run(mock, g, tmp_path)
    assert r["error"] == "guard_budget_exceeded" and len(r["executed"]) < CAP
    assert next(iter(g.budget.keys.values())).tokens >= 5000


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "stream"])
def test_destructive_call_removed_and_audited(mock: MockServer, tmp_path: Path, stream: bool) -> None:
    g = guard(tmp_path)
    r = run(mock, g, tmp_path, prompt="DANGER clean", stream=stream)
    assert r["executed"] == [] and "rm-root-or-home" in r["final"]
    entries = read_entries(tmp_path / "audit.jsonl")
    e = next(e for e in entries if e["decision"] == "deny")
    assert e["tool"] == "shell" and e["rule"] == "rm-root-or-home" and e["agent"] == "agent-a" and e["session"] == "s1"
    assert "rm -rf" not in (tmp_path / "audit.jsonl").read_text()  # arguments are hashed, never stored


def test_safe_lookalike_passes_through(mock: MockServer, tmp_path: Path) -> None:
    r = run(mock, guard(tmp_path, loop=LoopConfig(enabled=False)), tmp_path, prompt="CAREFUL push")
    assert r["executed"][0].startswith("git push --force-with-lease")


def test_ask_can_be_allowed_by_config(tmp_path: Path) -> None:
    g = guard(tmp_path, ask_action="allow")

    class Ctx:
        extras: ClassVar[dict[str, Any]] = {"guard": {"session": "s", "agent": "a", "key": None, "model": "glm-5.3"}}
        request_id = "r1"
        model = "glm-5.3"

        def emit(self, *a: Any, **k: Any) -> None: ...

    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [{"id": "1", "function": {"name": "bash", "arguments": '{"command":"rm -rf ./x"}'}}]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    out = g.after_response(Ctx(), body)
    assert out["choices"][0]["message"]["tool_calls"]  # ask + ask_action=allow: let through, but logged
    g2 = guard(tmp_path / "b", ask_action="block")
    out2 = g2.after_response(Ctx(), json.loads(json.dumps(body)))
    assert "tool_calls" not in out2["choices"][0]["message"] and out2["choices"][0]["finish_reason"] == "stop"


def test_anthropic_shapes(tmp_path: Path) -> None:
    g = guard(tmp_path)

    class Ctx:
        extras: ClassVar[dict[str, Any]] = {}
        request_id = "r1"
        model = "glm-5.3"

        def emit(self, *a: Any, **k: Any) -> None: ...

    ctx = Ctx()
    body = {
        "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "rm -rf /"}}],
        "stop_reason": "tool_use",
    }
    out = g.after_response(ctx, body)
    assert out["content"][0]["type"] == "text" and out["stop_reason"] == "end_turn"
    evs = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t", "name": "Bash", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"command": "rm -rf /"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    ]
    sent = [x for e in evs for x in g.on_stream_event(ctx, e)]
    assert [x["type"] for x in sent] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
    ]
    assert sent[1]["content_block"]["type"] == "text" and sent[-1]["delta"]["stop_reason"] == "end_turn"
    ok = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t", "name": "Bash", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"command": "ls"}'},
        },
        {"type": "content_block_stop", "index": 0},
    ]
    ctx2 = Ctx()
    ctx2.extras = {}
    assert len([x for e in ok for x in g.on_stream_event(ctx2, e)]) == 3 and sent[1]["content_block"]["type"] == "text"


def test_session_envelope_for_dashboard(mock: MockServer, tmp_path: Path) -> None:
    g = guard(
        tmp_path,
        session_budget=Budget(soft_usd=0.01, hard_usd=0.10),
        loop=LoopConfig(repeat_threshold=N, action="block"),
    )
    run(mock, g, tmp_path)
    found, _ = load_envelopes(tmp_path)
    env = next(e for _, e in found if e.component == "guard")
    s = env.data["sessions"][0]
    assert s["session"] == "s1" and s["agent"] == "agent-a" and s["usd"] > 0 and len(s["burn"]) == N
    assert env.data["audit"]["ok"] is True and env.data["counts"]["loop"] == 1
    assert any(i.kind == "guard_event" for i in env.items)

    from glmkit.report.pages.guard import GuardPage
    from glmkit.report.registry import RunRef

    data = GuardPage().collect([RunRef(env, "x/result.json")])
    assert data and data["audit"]["ok"] is True and data["audit"]["live"] is True
    (tmp_path / "audit.jsonl").write_bytes((tmp_path / "audit.jsonl").read_bytes().replace(b"shell", b"shelL", 1))
    assert GuardPage().collect([RunRef(env, "x/result.json")])["audit"]["ok"] is False  # tab shows a broken chain


def test_loop_detector_and_usage_math() -> None:
    def call(i: int, cmd: str, out: str) -> list[dict[str, Any]]:
        return [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": f"c{i}", "function": {"name": "shell", "arguments": json.dumps({"command": cmd})}}
                ],
            },
            {"role": "tool", "tool_call_id": f"c{i}", "content": out},
        ]

    msgs = [{"role": "user", "content": "x"}]
    for i in range(4):
        msgs += call(i, f"make test --seed {i}", f"error: build failed at 12:0{i}")
    v = loopmod.detect(msgs)
    assert v.streak == 4 and v.kind in ("same-call", "same-error")
    msgs += call(9, "ls", "a.txt")  # a success breaks the streak
    assert loopmod.detect(msgs).streak == 0
    different = [{"role": "user", "content": "x"}]
    for i, c in enumerate(["make", "pytest -x", "cargo build", "npm test"]):
        different += call(i, c, f"error: {['linker', 'syntax', 'timeout in network', 'missing module'][i]} broke")
    assert loopmod.detect(different).streak < 3
    u = normalize_usage(
        {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 400}}
    )
    e = price_for("glm-5.3")
    assert e and cost_of(u, e) == pytest.approx(600 / 1e6 * e.input + 400 / 1e6 * e.cached_input + 100 / 1e6 * e.output)
    assert cost_of(u, None) is None
    assert normalize_usage({"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 90}).input == 100


def test_hook_decisions_and_cli(tmp_path: Path) -> None:
    from glmkit.guard.audit import AuditLog

    audit = AuditLog(tmp_path / "h.jsonl")
    out = decide(
        {"tool_name": "Bash", "session_id": "S", "tool_input": {"command": "git push --force origin main"}}, audit=audit
    )
    assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny"
    ask = decide({"tool_name": "Bash", "tool_input": {"command": "rm -rf ./build"}}, audit=audit)
    assert ask and ask["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert decide({"tool_name": "Bash", "tool_input": {"command": "git push --force-with-lease"}}, audit=audit) is None
    assert decide({"tool_name": "Write", "tool_input": {"file_path": "a", "content": "rm -rf /"}}, audit=audit) is None
    assert [e["decision"] for e in read_entries(audit.path)] == ["deny", "ask", "allow", "allow"]
    assert verify(audit.path).ok
    env = {"GLMKIT_GUARD_AUDIT": str(tmp_path / "p.jsonl"), "PATH": "/usr/bin:/bin"}
    p = subprocess.run(
        [sys.executable, "-m", "glmkit.guard.hook"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}),
        capture_output=True,
        text=True,
        env={**env, "PYTHONPATH": ":".join(sys.path)},
        check=False,
    )
    assert p.returncode == 0 and json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert verify(tmp_path / "p.jsonl").ok
