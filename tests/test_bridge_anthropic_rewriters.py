"""Unit tests for the Anthropic-level request rewriters (no network)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from glmkit.bridge.anthropic import FakeSearchBackend
from glmkit.bridge.anthropic import rewriters as rw
from glmkit.bridge.core import BridgeCore, FixEmitter, get_upstream


def run(body: dict[str, Any], chain: Any = rw.COMMON, flavor: str = "zai", **extras: Any) -> tuple[dict[str, Any], Any]:
    core = BridgeCore(get_upstream(flavor), emitter=FixEmitter())
    ctx = core.new_context(body)
    ctx.extras.update(extras)
    out = copy.deepcopy(body)
    for fn in chain:
        out = fn(ctx, out)
    return out, ctx


def tool_use(inp: Any, name: str = "Read") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": name, "input": inp}],
    }


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ([{"file_path": "/a"}], {"file_path": "/a"}),
        ([{"a": 1}, {"b": 2}], {"a": 1, "b": 2}),
        ([["file_path", "/a"]], {"file_path": "/a"}),
        (["x", "y"], {"_input": ["x", "y"]}),
        ([], {"_input": []}),
    ],
)
def test_array_input_repair(raw: list[Any], want: dict[str, Any]) -> None:
    out, ctx = run({"model": "glm-5.3", "max_tokens": 5, "messages": [tool_use(raw)]})
    assert out["messages"][0]["content"][0]["input"] == want
    assert [e.fix_id for e in ctx.emitter.events] == ["anthropic-tool-input-repair"]


def test_array_input_wrapped_under_the_schemas_single_array_property() -> None:
    tools = [{"name": "Multi", "input_schema": {"type": "object", "properties": {"paths": {"type": "array"}}}}]
    out, _ = run({"model": "m", "max_tokens": 5, "tools": tools, "messages": [tool_use(["a", "b"], "Multi")]})
    assert out["messages"][0]["content"][0]["input"] == {"paths": ["a", "b"]}


def test_string_and_null_inputs_and_clean_inputs_untouched() -> None:
    body = {"model": "m", "max_tokens": 5, "messages": [tool_use("{'a': 1,}"), tool_use(None), tool_use({"ok": 1})]}
    out, ctx = run(body)
    assert [m["content"][0]["input"] for m in out["messages"]] == [{"a": 1}, {}, {"ok": 1}]
    assert len(ctx.emitter.events) == 1


def test_input_body_is_not_mutated_by_the_handler_contract() -> None:
    body = {"model": "m", "max_tokens": 5, "messages": [tool_use([{"x": 1}])]}
    snapshot = copy.deepcopy(body)
    run(body)
    assert body == snapshot  # run() copies; rewriters work on the copy the host hands them


def test_max_tokens_default_only_when_missing() -> None:
    out, ctx = run({"model": "m", "messages": []}, default_max_tokens=1234)
    assert out["max_tokens"] == 1234 and ctx.emitter.events[0].fix_id == "anthropic-max-tokens-default"
    out, ctx = run({"model": "m", "max_tokens": 7, "messages": []})
    assert out["max_tokens"] == 7 and not ctx.emitter.events


def test_web_search_strip_and_emulate_tool_lists() -> None:
    ws = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    bash = {"name": "Bash", "input_schema": {"type": "object"}}
    out, ctx = run(
        {
            "model": "m",
            "max_tokens": 5,
            "tools": [ws, bash],
            "tool_choice": {"type": "tool", "name": "web_search"},
            "messages": [],
        }
    )
    assert out["tools"] == [bash] and "tool_choice" not in out
    assert ctx.emitter.events[0].fix_id == "anthropic-web-search-strip"
    out, _ = run({"model": "m", "max_tokens": 5, "tools": [ws], "messages": []})
    assert "tools" not in out and "tool_choice" not in out
    out, ctx = run(
        {"model": "m", "max_tokens": 5, "tools": [ws, bash], "messages": []}, search_backend=FakeSearchBackend()
    )
    assert [t["name"] for t in out["tools"]] == ["web_search", "Bash"] and "type" not in out["tools"][0]
    assert ctx.extras["web_search"] == {"max_uses": 3}


def test_history_server_blocks_are_split_when_emulating_and_dropped_otherwise() -> None:
    asst = {
        "role": "assistant",
        "content": [
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "q"}},
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [{"type": "web_search_result", "url": "u", "title": "t"}],
            },
            {"type": "text", "text": "answer"},
        ],
    }
    body = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "q"}, asst]}
    out, _ = run(body, search_backend=FakeSearchBackend(), web_search={"max_uses": 1})
    roles = [m["role"] for m in out["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert out["messages"][1]["content"][0]["type"] == "tool_use"
    assert (
        out["messages"][2]["content"][0]["type"] == "tool_result" and "t" in out["messages"][2]["content"][0]["content"]
    )
    out, _ = run(body)
    assert out["messages"][1]["content"] == [{"type": "text", "text": "answer"}]


def test_orphan_tool_result_becomes_text() -> None:
    body = {
        "model": "m",
        "max_tokens": 5,
        "messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "gone", "content": "x"}]}],
    }
    out, ctx = run(body)
    assert out["messages"][0]["content"][0]["type"] == "text" and "gone" in out["messages"][0]["content"][0]["text"]
    assert ctx.emitter.events[0].fix_id == "anthropic-history-repair"


def test_native_thinking_and_effort() -> None:
    body = {
        "model": "glm-5.3",
        "max_tokens": 5,
        "messages": [],
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "medium"},
    }
    out, ctx = run(body, rw.NATIVE)
    assert out["thinking"] == {"type": "enabled"} and out["output_config"]["effort"] == "high"
    assert [e.fix_id for e in ctx.emitter.events] == ["thinking-disabled-to-low", "effort-clamp"]
    out, ctx = run(body, rw.NATIVE, flavor="zai-plan")  # the plan host maps these itself
    assert out["thinking"] == {"type": "disabled"} and not ctx.emitter.events
