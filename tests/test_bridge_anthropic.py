"""Anthropic Messages path of the bridge against the (defective) mock, driven by the Anthropic Python SDK."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest

from glmkit.bridge.anthropic import AnthropicOptions, FakeSearchBackend
from glmkit.bridge.core import BridgeMiddleware, Reject
from glmkit.mock import MockConfig, MockServer
from glmkit.results import load_envelopes
from tests.anthropic_corpora import CORPORA
from tests.anthropic_session import API_KEY, CONTEXT_TOLERANCE, MODEL, run_session, tool_loop
from tests.bridge_anthropic_fixture import ALL_DEFECTS, UPSTREAMS, build_bridge

KINDS = list(UPSTREAMS)
DIRECT_PATH = "/api/anthropic"  # the mock serves /v1/messages under any prefix
# defect -> the session step it breaks when the client talks to the mock directly
DEFECT_STEPS = {
    "bridge-anthropic-zero-start-usage": {"message-start-usage"},
    "bridge-anthropic-array-input-500": {"malformed-history"},
    "bridge-anthropic-missing-max-tokens-500": set(),  # a clean mock already answers 400; see the status test
    "bridge-anthropic-inflated-count": {"context-meter"},
}
# without any defect the clean mock still rejects these: 1210 on glm-5.3 thinking disabled, 400 without max_tokens
FORCED_THINKING_STEPS = {"helper-title", "helper-summary", "compact", "missing-max-tokens"}


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Any]:
    started: list[Any] = []

    def _make(kind: str, defects: list[str] | None = None, **kw: Any) -> Any:
        mock = MockServer(MockConfig(defects=defects or [], rules=kw.pop("rules", []))).start()
        bridge, app = build_bridge(mock, kind, tmp_path, **kw)
        started.append((mock, bridge))
        return mock, bridge, app

    yield _make
    for mock, bridge in started:
        bridge.stop()
        mock.stop()


def failed(res: Any) -> set[str]:
    return {s.name for s in res.steps if not s.ok}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_sdk_tool_loop_completes_through_the_bridge(make: Any, kind: str, stream: bool) -> None:
    _mock, bridge, _app = make(kind)
    out = tool_loop(bridge.url, stream=stream)
    assert out["ok"], out
    assert out["results"] == ["sunny in Paris"] and out["text"] and out["thinking"]
    if stream:
        assert out["events"][0] == "message_start" and out["events"][-1] == "message_stop"


@pytest.mark.parametrize("kind", KINDS)
def test_claude_code_session_fails_direct_and_passes_bridged(make: Any, kind: str) -> None:
    mock, bridge, app = make(kind, ALL_DEFECTS)
    direct = run_session(mock.url + DIRECT_PATH)
    bridged = run_session(bridge.url)
    assert (
        failed(direct)
        == {"message-start-usage", "malformed-history", "missing-max-tokens", "context-meter"} | FORCED_THINKING_STEPS
    )
    assert bridged.ok, bridged.show()
    fixes = {e.fix_id for e in app.state.emitter.events}
    assert {
        "anthropic-max-tokens-default",
        "anthropic-tool-input-repair",
        "anthropic-count-tokens",
        "thinking-disabled-to-low",
    } <= fixes
    assert "anthropic-usage-inject" in fixes


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("defect", sorted(DEFECT_STEPS))
def test_each_defect_alone_breaks_direct_and_is_fixed_bridged(make: Any, kind: str, defect: str) -> None:
    mock, bridge, _app = make(kind, [defect])
    direct = run_session(mock.url + DIRECT_PATH)
    bridged = run_session(bridge.url)
    assert failed(direct) == DEFECT_STEPS[defect] | FORCED_THINKING_STEPS
    assert bridged.ok, bridged.show()


@pytest.mark.parametrize("kind", KINDS)
def test_missing_max_tokens_is_400_clean_500_defective_200_bridged(make: Any, kind: str) -> None:
    body = {"model": MODEL, "messages": [{"role": "user", "content": "Say OK"}]}
    clean, bridge_clean, _ = make(kind)
    broken, bridge, app = make(kind, ["bridge-anthropic-missing-max-tokens-500"])
    assert httpx.post(clean.url + DIRECT_PATH + "/v1/messages", json=body).status_code == 400
    assert httpx.post(broken.url + DIRECT_PATH + "/v1/messages", json=body).status_code == 500
    for b in (bridge_clean, bridge):
        r = httpx.post(b.url + "/v1/messages", json=body)
        assert r.status_code == 200 and r.json()["content"]
    assert "anthropic-max-tokens-default" in {e.fix_id for e in app.state.emitter.events}


@pytest.mark.parametrize("kind", KINDS)
def test_count_tokens_within_five_percent_on_ten_corpora(make: Any, kind: str) -> None:
    mock, bridge, _app = make(kind, ["bridge-anthropic-inflated-count"])
    clean = MockServer(MockConfig()).start()  # the reference accounting: the same mock without the defect
    try:
        sdk = anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY, max_retries=0)
        direct = anthropic.Anthropic(base_url=mock.url + DIRECT_PATH, api_key=API_KEY, max_retries=0)
        ref = anthropic.Anthropic(base_url=clean.url + DIRECT_PATH, api_key=API_KEY, max_retries=0)
        assert len(CORPORA) == 10
        for name, text in CORPORA.items():
            msgs = [{"role": "user", "content": text}]
            want = ref.messages.count_tokens(model=MODEL, messages=msgs).input_tokens
            got = sdk.messages.count_tokens(model=MODEL, messages=msgs).input_tokens
            raw = direct.messages.count_tokens(model=MODEL, messages=msgs).input_tokens
            assert abs(got - want) / want <= CONTEXT_TOLERANCE, f"{name}: bridge {got} vs {want}"
            assert abs(raw - want) / want > CONTEXT_TOLERANCE, f"{name}: the defect should inflate {raw} vs {want}"
    finally:
        clean.stop()


def test_calibrator_learns_the_upstream_accounting() -> None:
    from glmkit.bridge.anthropic.tokens import TokenCalibrator, features

    body = {"model": MODEL, "messages": [{"role": "user", "content": "x" * 4000}]}
    cal = TokenCalibrator()
    assert cal.estimate(body) == 1004  # prior: 4 chars per token + 4 per message
    shapes = [(1000, 1), (2000, 3), (3000, 2), (4000, 5), (6000, 1), (800, 4), (1500, 2), (5000, 3)]
    for n, count in shapes:  # an upstream that bills 1.3x the content plus 20 per message
        b = {"model": MODEL, "messages": [{"role": "user", "content": "y" * n}] * count}
        f = features(b)
        cal.observe(MODEL, f, round(1.3 * f[0] + 20 * f[1]))
    want = 1.3 * 1000 + 20
    assert abs(cal.estimate(body) - want) / want < 0.05


def test_streaming_events_and_signature_for_openai_upstream(make: Any) -> None:
    _mock, bridge, _app = make("openai")
    client = anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY)
    with client.messages.stream(model=MODEL, max_tokens=256, messages=[{"role": "user", "content": "Say OK"}]) as s:
        events = list(s)
        msg = s.get_final_message()
    kinds = [e.type for e in events]
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop"
    assert msg.content[0].type == "thinking" and msg.content[0].signature.startswith("glmkit-v1.")
    # the signature round-trips: replay the assistant turn and the reasoning reaches the upstream again
    again = client.messages.create(
        model=MODEL,
        max_tokens=64,
        messages=[
            {"role": "user", "content": "Say OK"},
            {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]},
            {"role": "user", "content": "again"},
        ],
    )
    assert again.content


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_web_search_is_emulated_with_the_fake_backend(make: Any, kind: str, stream: bool) -> None:
    backend = FakeSearchBackend()
    _mock, bridge, app = make(kind, options=AnthropicOptions(search_backend=backend))
    client = anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY)
    kw: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 512,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
        "messages": [{"role": "user", "content": "Use web_search with query=glm"}],
    }
    if stream:
        with client.messages.stream(**kw) as s:
            msg = s.get_final_message()
    else:
        msg = client.messages.create(**kw)
    types = [b.type for b in msg.content]
    assert "server_tool_use" in types and "web_search_tool_result" in types and types[-1] == "text"
    result = next(b for b in msg.content if b.type == "web_search_tool_result")
    assert result.content[0].url.startswith("https://example.test/") and result.content[0].title
    assert backend.queries == ["glm"]
    assert msg.usage.server_tool_use.web_search_requests == 1
    assert {"anthropic-web-search-emulate", "anthropic-web-search-run"} <= {e.fix_id for e in app.state.emitter.events}


@pytest.mark.parametrize("kind", KINDS)
def test_web_search_is_stripped_cleanly_without_a_backend(make: Any, kind: str) -> None:
    _mock, bridge, app = make(kind)
    client = anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=128,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        tool_choice={"type": "tool", "name": "web_search"},
        messages=[
            {"role": "user", "content": "Say OK"},
            {
                "role": "assistant",
                "content": [
                    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "x"}},
                    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []},
                    {"type": "text", "text": "done"},
                ],
            },
            {"role": "user", "content": "thanks"},
        ],
    )
    assert msg.stop_reason == "end_turn"
    fixes = {e.fix_id for e in app.state.emitter.events}
    assert {"anthropic-web-search-strip", "anthropic-history-repair"} <= fixes


def test_upstream_errors_become_anthropic_errors(make: Any) -> None:
    rules = [
        {"name": "overload", "error": {"status": 429, "code": "1305", "message": "overloaded"}},
        {"name": "window", "error": {"status": 429, "code": "1308", "message": "limit, next_flush_time: 4102444800"}},
    ]
    for kind in KINDS:
        _mock, bridge, _app = make(kind, rules=rules)
        body = {"model": MODEL, "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}
        r = httpx.post(bridge.url + "/v1/messages", json=body, headers={"x-glmkit-scenario": "overload"})
        assert r.status_code == 503 and r.json()["type"] == "error"
        assert r.json()["error"]["type"] == "overloaded_error" and r.headers["x-should-retry"] == "true"
        assert r.headers["request-id"] == r.json()["request_id"]
        r = httpx.post(bridge.url + "/v1/messages", json=body, headers={"x-glmkit-scenario": "window"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 3600
        assert r.json()["error"]["type"] == "rate_limit_error"
        with pytest.raises(anthropic.RateLimitError):
            anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY, max_retries=0).messages.create(
                **body, extra_headers={"x-glmkit-scenario": "window"}
            )


def test_bad_bodies_are_anthropic_errors(make: Any) -> None:
    _mock, bridge, _app = make("openai")
    r = httpx.post(bridge.url + "/v1/messages", content=b"[1]")
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_request_error"
    r = httpx.post(bridge.url + "/v1/messages/count_tokens", json={"model": MODEL})
    assert r.status_code == 400 and r.json()["type"] == "error"


def test_native_profile_rejects_openai_route(make: Any) -> None:
    _mock, bridge, _app = make("anthropic")
    r = httpx.post(bridge.url + "/v1/chat/completions", json={"model": MODEL, "messages": []})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bridge_wrong_protocol"


def test_openai_route_still_works_next_to_the_anthropic_one(make: Any) -> None:
    from tests.bridge_loop import run_loop

    _mock, bridge, _app = make("openai", ["bridge-broken-json-args"])
    assert run_loop(bridge.url + "/v1", thinking_disabled=False, effort=None)["ok"]
    assert tool_loop(bridge.url, stream=True)["ok"]


class Spy(BridgeMiddleware):
    def __init__(self, reject_after: int | None = None) -> None:
        self.usages: list[Any] = []
        self.wires: list[str] = []
        self.reject_after = reject_after

    def before_request(self, ctx: Any, body: dict[str, Any]) -> Any:
        self.wires.append(ctx.extras["wire"])
        if self.reject_after is not None and len(self.wires) > self.reject_after:
            return Reject(status=429, message="budget exhausted", code="budget", retry_after_s=60, fix_id="spy-reject")
        return body

    def on_finish(self, ctx: Any, usage: Any) -> None:
        self.usages.append(usage)


@pytest.mark.parametrize("kind", KINDS)
def test_middleware_sees_the_call_and_openai_shaped_usage(make: Any, kind: str) -> None:
    spy = Spy(reject_after=2)
    _mock, bridge, app = make(kind, middleware=[spy])
    c = anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY, max_retries=0)
    for stream in (False, True):
        if stream:
            with c.messages.stream(model=MODEL, max_tokens=64, messages=[{"role": "user", "content": "Say OK"}]) as s:
                list(s)
        else:
            c.messages.create(model=MODEL, max_tokens=64, messages=[{"role": "user", "content": "Say OK"}])
    assert len(spy.usages) == 2 and all(u["prompt_tokens"] > 0 and u["completion_tokens"] > 0 for u in spy.usages)
    assert set(spy.wires) == {"anthropic_messages" if kind == "anthropic" else "openai_chat"}
    with pytest.raises(anthropic.RateLimitError) as e:
        c.messages.create(model=MODEL, max_tokens=64, messages=[{"role": "user", "content": "Say OK"}])
    assert e.value.response.headers["retry-after"] == "60"
    assert "spy-reject" in {ev.fix_id for ev in app.state.emitter.events}


@pytest.mark.parametrize("kind", KINDS)
def test_session_envelope_tags_protocol_and_leaks_nothing(make: Any, kind: str, tmp_path: Path, caplog: Any) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    _mock, bridge, _app = make(kind, ALL_DEFECTS)
    c = anthropic.Anthropic(
        base_url=bridge.url, api_key=API_KEY, default_headers={"x-glmkit-defects": ",".join(ALL_DEFECTS)}
    )
    c.messages.create(
        model=MODEL, max_tokens=32, thinking={"type": "disabled"}, messages=[{"role": "user", "content": "SECRETCITY"}]
    )
    bridge.stop()
    envs, warnings = load_envelopes(tmp_path)
    assert not warnings
    env = envs[0][1]
    assert env.items and all(i.data["details"]["protocol"] == "anthropic" for i in env.items)
    text = envs[0][0].read_text()
    assert "SECRETCITY" not in text and API_KEY not in text
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("glmkit"))  # not the test client's own
    assert ours and "SECRETCITY" not in ours and API_KEY not in ours and "x-api-key" not in ours.lower()
    assert env.data["requests"] and env.data["requests"][0]["path"].endswith("/v1/messages")


def test_log_content_adds_content_only_when_asked(make: Any, tmp_path: Path) -> None:
    _mock, bridge, _app = make("anthropic", ["bridge-anthropic-missing-max-tokens-500"], log_content=True)
    httpx.post(bridge.url + "/v1/messages", json={"model": MODEL, "messages": [{"role": "user", "content": "Say OK"}]})
    bridge.stop()
    env = load_envelopes(tmp_path)[0][0][1]
    assert any(i.data["fix_id"] == "anthropic-max-tokens-default" for i in env.items)


def test_unrepairable_arguments_are_retried_once_through_the_anthropic_path(make: Any) -> None:
    _mock, bridge, app = make("openai", ["bridge-unrepairable-args"])
    for stream in (False, True):
        out = tool_loop(bridge.url, stream=stream)
        assert out["ok"] and out["results"] == ["sunny in Paris"], out
    ids = [e.fix_id for e in app.state.emitter.events]
    assert ids.count("tool-args-retry") == 2 and "tool-args-unrepaired" not in ids


def test_arguments_that_stay_broken_are_an_error_not_an_empty_object(make: Any) -> None:
    rules = [{"name": "bad", "reply": {"tool_calls": [{"name": "get_weather", "arguments": "not json at all"}]}}]
    _mock, bridge, app = make("openai", rules=rules)
    body = {
        "model": MODEL,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "weather"}],
        "tools": [
            {
                "name": "get_weather",
                "input_schema": {"type": "object", "properties": {"city": {}}, "required": ["city"]},
            }
        ],
    }
    r = httpx.post(bridge.url + "/v1/messages", json=body)
    assert r.status_code == 502 and r.json()["type"] == "error" and "{}" not in r.text
    rs = httpx.post(bridge.url + "/v1/messages", json={**body, "stream": True})
    assert rs.status_code == 200 and "event: error" in rs.text and "message_stop" not in rs.text
    assert [e.fix_id for e in app.state.emitter.events].count("tool-args-unrepaired") == 2
