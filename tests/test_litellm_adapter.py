"""LiteLLM adapter logic and plugin packaging, without LiteLLM installed (the proxy tests are in
test_litellm_integration.py, marked ``litellm``)."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from glmkit.bridge.litellm_adapter import RETRY_FLAG, LiteLLMBridge

ONEOF = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "Look up",
        "parameters": {
            "oneOf": [
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            ]
        },
    },
}


def kw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "zai/glm-5.3",
        "messages": [{"role": "user", "content": "SECRET-PROMPT-TEXT"}],
        "litellm_call_id": "call-1234567890ab",
        "api_base": "http://upstream.invalid/v4",
        "api_key": "sk-secret-key-value",
    }
    return {**base, **over}


@pytest.fixture
def bridge(tmp_path: Path) -> LiteLLMBridge:
    return LiteLLMBridge(out_dir=tmp_path)


def events(b: LiteLLMBridge) -> list[str]:
    return [e.fix_id for e in b.emitter.events]


def test_forced_thinking_and_effort_move_to_extra_body(bridge: LiteLLMBridge) -> None:
    out = bridge.rewrite_kwargs(kw(thinking={"type": "disabled"}, reasoning_effort="medium"))
    assert out is not None
    assert "thinking" not in out and "reasoning_effort" not in out  # LiteLLM's zai provider mishandles both
    assert out["extra_body"] == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    assert events(bridge) == ["thinking-disabled-to-low", "effort-clamp"]


def test_thinking_arriving_via_extra_body_is_rewritten_too(bridge: LiteLLMBridge) -> None:
    out = bridge.rewrite_kwargs(kw(extra_body={"thinking": {"type": "disabled"}, "other": 1}))
    assert out is not None
    assert out["extra_body"]["thinking"] == {"type": "enabled"}
    assert out["extra_body"]["reasoning_effort"] == "low" and out["extra_body"]["other"] == 1


def test_schema_flatten_and_args_unflatten(bridge: LiteLLMBridge) -> None:
    out = bridge.rewrite_kwargs(kw(tools=[ONEOF]))
    assert out is not None
    params = out["tools"][0]["function"]["parameters"]
    assert "oneOf" not in params and set(params["properties"]) == {"query", "url"}
    assert "oneOf" in ONEOF["function"]["parameters"], "caller's tools are never mutated"
    resp = _response('{"query": "glm"}', name="lookup")
    bridge.fix_response(kw(), resp)
    assert "schema-flatten" in events(bridge)


def test_router_retry_keeps_original_schemas(bridge: LiteLLMBridge) -> None:
    first = bridge.rewrite_kwargs(kw(tools=[ONEOF]))
    assert first is not None
    bridge.rewrite_kwargs({**kw(), "tools": first["tools"]})  # a retry hands the hook the rewritten tools
    tracked = bridge.live.get("call-1234567890ab")
    assert tracked is not None and "oneOf" in tracked.ctx.tool_schemas["lookup"]


def test_non_glm_models_and_inner_retry_calls_are_left_alone(bridge: LiteLLMBridge) -> None:
    assert bridge.rewrite_kwargs(kw(model="openai/gpt-4o", thinking={"type": "disabled"})) is None
    assert bridge.rewrite_kwargs(kw(metadata={RETRY_FLAG: True}, thinking={"type": "disabled"})) is None
    assert events(bridge) == []


def test_flavor_follows_the_provider_prefix(bridge: LiteLLMBridge) -> None:
    assert bridge.upstream_for("zai/glm-5.3").name == "zai"  # type: ignore[union-attr]
    assert bridge.upstream_for("cerebras/zai-glm-4.7").name == "cerebras"  # type: ignore[union-attr]
    assert bridge.upstream_for("anthropic/claude-sonnet") is None


def _response(args: str, name: str = "get_weather") -> Any:
    fn = SimpleNamespace(name=name, arguments=args)
    tc = SimpleNamespace(id="call_1", type="function", function=fn)
    msg = SimpleNamespace(role="assistant", content=None, tool_calls=[tc])
    resp = SimpleNamespace(choices=[SimpleNamespace(index=0, message=msg, finish_reason="tool_calls")], usage=None)

    def dump() -> dict[str, Any]:
        return {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": c.type,
                                "function": {"name": c.function.name, "arguments": c.function.arguments},
                            }
                            for c in msg.tool_calls
                        ],
                    },
                }
            ]
        }

    resp.model_dump = dump  # type: ignore[attr-defined]
    return resp


def test_response_arguments_repaired_in_place(bridge: LiteLLMBridge) -> None:
    bridge.rewrite_kwargs(kw(tools=[{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]))
    resp = _response("{'city': 'Paris',}")
    assert bridge.fix_response(kw(), resp) is resp
    assert json.loads(resp.choices[0].message.tool_calls[0].function.arguments) == {"city": "Paris"}
    assert "tool-args-repair" in events(bridge)


def test_unrepairable_response_is_marked_and_retry_planned_once(bridge: LiteLLMBridge) -> None:
    bridge.rewrite_kwargs(kw())
    resp = _response("I will now call the weather tool.")
    plan = bridge.failures_in(resp, kw(), kw()["messages"])
    assert plan is not None
    last = plan.request["messages"][-1]
    assert last["role"] == "tool" and "not valid" in last["content"]
    assert bridge.failures_in(resp, kw(metadata={RETRY_FLAG: True}), kw()["messages"]) is None
    bridge.fix_response(kw(), resp)
    tracked = bridge.live.get("call-1234567890ab")
    assert tracked is not None and tracked.ctx.extras["unrepaired"]["error"]["code"] == "bridge_tool_arguments_invalid"
    assert "tool-args-unrepaired" in events(bridge)


def test_error_mapping_uses_the_upstream_body(bridge: LiteLLMBridge) -> None:
    bridge.rewrite_kwargs(kw())
    exc = SimpleNamespace(status_code=429, body={"code": "1113", "message": "Insufficient balance"})
    status, error, headers = bridge.map_failure(kw(), exc)  # type: ignore[misc,arg-type]
    assert status == 402 and error["code"] == "1113" and headers["x-should-retry"] == "false"


def test_session_envelope_has_source_and_no_content(bridge: LiteLLMBridge) -> None:
    bridge.rewrite_kwargs(kw(thinking={"type": "disabled"}))
    bridge.fix_response(kw(), _response('{"city": "Paris"}'))
    path = bridge.recorder.flush(final=True)
    assert path is not None
    text = path.read_text()
    env = json.loads(text)
    assert env["component"] == "bridge" and env["config"]["source"] == "litellm"
    assert all(i["data"]["source"] == "litellm" for i in env["items"])
    assert "SECRET-PROMPT-TEXT" not in text and "sk-secret-key-value" not in text and "upstream.invalid" not in text


def test_settings_from_env(tmp_path: Path) -> None:
    b = LiteLLMBridge.from_env({"GLMKIT_BRIDGE_FLAVOR": "zai-plan", "GLMKIT_BRIDGE_OUT": str(tmp_path)})
    assert b.flavor == "zai-plan" and b.out_dir == tmp_path and b.log_content is False
    assert LiteLLMBridge.from_env({"GLMKIT_BRIDGE_LOG_CONTENT": "1"}).log_content is True


@pytest.mark.skipif(importlib.util.find_spec("litellm") is not None, reason="litellm is installed here")
def test_plugin_import_without_litellm_explains_the_extra() -> None:
    with pytest.raises(ImportError, match=r"glmkit\[litellm\]"):
        importlib.import_module("glmkit.bridge.litellm_plugin")
