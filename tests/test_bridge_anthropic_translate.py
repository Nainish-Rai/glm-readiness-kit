"""Golden request/response pairs for the Anthropic <-> OpenAI translation, including streaming.

Regenerate after an intended change: ``GLMKIT_UPDATE_GOLDEN=1 uv run pytest tests/test_bridge_anthropic_translate.py``
and read the diff of ``tests/fixtures/bridge_anthropic/golden.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from glmkit.bridge.anthropic import translate as tr

GOLDEN = Path(__file__).parent / "fixtures" / "bridge_anthropic" / "golden.json"


@pytest.fixture(autouse=True)
def _fixed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tr, "_KEY", b"golden-test-key")
    monkeypatch.setattr(tr, "new_message_id", lambda: "msg_golden")


def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "model": "glm-5.3",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }


USAGE = {
    "prompt_tokens": 120,
    "completion_tokens": 30,
    "total_tokens": 150,
    "prompt_tokens_details": {"cached_tokens": 100},
}
WEATHER = {
    "name": "get_weather",
    "description": "Weather for a city",
    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}

REQUESTS: dict[str, dict[str, Any]] = {
    "basic": {
        "model": "glm-5.3",
        "max_tokens": 64,
        "system": [
            {"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "No emoji."},
        ],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Say OK"}]}],
        "thinking": {"type": "disabled"},
        "stop_sequences": ["END"],
        "temperature": 0.5,
        "stream": True,
    },
    "tool-loop": {
        "model": "glm-5.3",
        "max_tokens": 2048,
        "system": "You are a coding agent.",
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "tools": [WEATHER, {"type": "web_search_20250305", "name": "web_search"}],
        "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
        "messages": [
            {"role": "user", "content": "Weather in Paris?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "I should call the tool.", "signature": "sig-from-before"},
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "sunny"}]},
                    {"type": "text", "text": "and tomorrow?"},
                ],
            },
        ],
    },
    "image-and-error-result": {
        "model": "glm-5.3",
        "max_tokens": 100,
        "tool_choice": {"type": "tool", "name": "get_weather"},
        "tools": [WEATHER],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                    {"type": "text", "text": "what is this"},
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_9", "name": "get_weather", "input": {}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_9", "is_error": True, "content": "boom"}],
            },
        ],
    },
}

RESPONSES: dict[str, dict[str, Any]] = {
    "thinking-and-text": {
        "model": "glm-5.3",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "OK", "reasoning_content": "The user wants OK."},
            }
        ],
        "usage": USAGE,
    },
    "tool-calls": {
        "model": "glm-5.3",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city": "Rome"}'},
                        },
                    ],
                },
            }
        ],
        "usage": USAGE,
    },
    "length": {
        "model": "glm-5.3",
        "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "truncated"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 64},
    },
}

STREAMS: dict[str, list[Any]] = {
    "text-with-reasoning": [
        chunk({"role": "assistant"}),
        chunk({"reasoning_content": "Think "}),
        chunk({"reasoning_content": "hard."}),
        chunk({"content": "Hel"}),
        chunk({"content": "lo"}),
        chunk({}, "stop", usage=USAGE),
        "[DONE]",
    ],
    "tool-call-whole": [
        chunk({"role": "assistant"}),
        chunk({"content": "Checking."}),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ]
            }
        ),
        chunk({}, "tool_calls", usage=USAGE),
        "[DONE]",
    ],
    "tool-call-fragments-usage-last": [
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": ""},
                    }
                ]
            }
        ),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"city"'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": ': "Paris"}'}}]}),
        chunk({}, "tool_calls"),
        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": "glm-5.3", "choices": [], "usage": USAGE},
        "[DONE]",
    ],
}


def run_stream(chunks: list[Any]) -> list[dict[str, Any]]:
    t = tr.OpenAIStreamTranslator(model="glm-5.3", start_usage={"input_tokens": 77, "cache_read_input_tokens": 0})
    out: list[dict[str, Any]] = []
    for c in chunks:
        out += t.feed(c)
    out += t.finish()
    return out


def compute() -> dict[str, Any]:
    out: dict[str, Any] = {"requests": {}, "responses": {}, "streams": {}, "replay": None}
    for name, req in REQUESTS.items():
        out["requests"][name] = {"anthropic": req, "openai": tr.to_openai_request(req)}
    for name, resp in RESPONSES.items():
        out["responses"][name] = {"openai": resp, "anthropic": tr.from_openai_response(resp)}
    for name, chunks in STREAMS.items():
        out["streams"][name] = {"openai": chunks, "anthropic": run_stream(chunks)}
    msg = tr.from_openai_response(RESPONSES["thinking-and-text"])
    msg["content"].append({"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}})
    out["replay"] = {"message": msg, "events": tr.events_from_message(msg)}
    return out


def test_golden_pairs() -> None:
    got = json.loads(json.dumps(compute(), ensure_ascii=False))
    if os.environ.get("GLMKIT_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert got == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_input_is_never_mutated() -> None:
    before = json.dumps(REQUESTS, sort_keys=True)
    for r in REQUESTS.values():
        tr.to_openai_request(r)
    assert json.dumps(REQUESTS, sort_keys=True) == before


def test_stream_event_sequence_is_anthropic_shaped() -> None:
    for name, chunks in STREAMS.items():
        events = run_stream(chunks)
        types = [e["type"] for e in events]
        assert types[0] == "message_start" and types[-1] == "message_stop", name
        assert types[-2] == "message_delta", name
        opened: set[int] = set()
        for e in events:
            if e["type"] == "content_block_start":
                assert e["index"] not in opened
                opened.add(e["index"])
            if e["type"] == "content_block_stop":
                opened.remove(e["index"])
        assert not opened, f"{name}: every block is closed"
        assert events[0]["message"]["usage"]["input_tokens"] == 77  # the estimate; real usage comes last


def test_final_message_delta_carries_the_real_usage() -> None:
    delta = next(e for e in run_stream(STREAMS["tool-call-fragments-usage-last"]) if e["type"] == "message_delta")
    assert delta["usage"] == {
        "input_tokens": 20,
        "output_tokens": 30,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 100,
    }
    assert delta["delta"]["stop_reason"] == "tool_use"


def test_thinking_signature_is_stable_and_verifiable() -> None:
    sig = tr.sign("reasoning")
    assert sig == tr.sign("reasoning") and sig != tr.sign("other")
    assert tr.is_ours(sig, "reasoning") and not tr.is_ours(sig, "other")
