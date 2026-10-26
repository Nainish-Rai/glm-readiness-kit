"""Bridge proxy against the defective mock, driven by the OpenAI Python SDK."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from glmkit.bridge.core import get_upstream
from glmkit.bridge.proxy import ProxyConfig, create_app
from glmkit.bridge.server import BridgeServer
from glmkit.contracts.profile import get_profile
from glmkit.mock import MockConfig, MockServer
from glmkit.results import load_envelopes
from tests.bridge_loop import run_loop

DEFECT_SETS = [
    pytest.param([], id="clean-mock-forced-thinking"),
    pytest.param(["bridge-oneof-empty-args"], id="oneof-empty-args"),
    pytest.param(["bridge-broken-json-args"], id="broken-json-args"),
    pytest.param(["bridge-unrepairable-args"], id="unrepairable-args"),
    pytest.param(["bridge-oneof-empty-args", "bridge-broken-json-args"], id="oneof+broken"),
]


@pytest.fixture
def make_bridge(tmp_path: Path) -> Iterator[Any]:
    started: list[Any] = []

    def make(defects: list[str], *, flavor: str = "zai", log_content: bool = False, endpoint_class: str = "api") -> Any:
        mock = MockServer(MockConfig(defects=defects, endpoint_class=endpoint_class)).start()
        profile = get_profile("mock").model_copy(
            update={"base_url": mock.url + "/api/paas/v4", "spawn_mock": False, "endpoint_class": endpoint_class}
        )
        app = create_app(
            ProxyConfig(
                profile=profile,
                upstream=get_upstream(flavor),
                out_dir=tmp_path,
                log_content=log_content,
                flush_interval_s=0.2,
            )
        )
        bridge = BridgeServer(app).start()
        started.append((mock, bridge))
        return mock, bridge, app

    yield make
    for mock, bridge in started:
        bridge.stop()
        mock.stop()


@pytest.mark.parametrize("defects", DEFECT_SETS)
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_loop_fails_direct_and_passes_bridged(make_bridge: Any, defects: list[str], stream: bool) -> None:
    mock, bridge, app = make_bridge(defects)
    direct = run_loop(mock.url + "/api/paas/v4", stream=stream)
    bridged = run_loop(bridge.url + "/v1", stream=stream)
    assert direct["ok"] is False, direct  # at least forced thinking (400/1210) breaks the direct run
    assert bridged["ok"] is True, bridged
    assert bridged["tool_results"] == ["sunny in Paris", "found glm"]
    fixes = {e.fix_id for e in app.state.emitter.events}
    assert "thinking-disabled-to-low" in fixes
    assert "effort-clamp" in fixes  # reasoning_effort="medium" -> high


def test_each_defect_alone_breaks_direct_run_without_forced_thinking(make_bridge: Any) -> None:
    for defect, fix in [
        ("bridge-oneof-empty-args", "schema-flatten"),
        ("bridge-broken-json-args", "tool-args-repair"),
        ("bridge-unrepairable-args", "tool-args-retry"),
    ]:
        mock, bridge, app = make_bridge([defect])
        direct = run_loop(mock.url + "/api/paas/v4", thinking_disabled=False, effort=None)
        bridged = run_loop(bridge.url + "/v1", thinking_disabled=False, effort=None)
        assert not direct["ok"], (defect, direct)
        assert bridged["ok"], (defect, bridged)
        assert fix in {e.fix_id for e in app.state.emitter.events}, defect


def test_retry_happens_at_most_once_and_is_a_fix_event(make_bridge: Any) -> None:
    _mock, bridge, app = make_bridge(["bridge-unrepairable-args"])
    r = run_loop(bridge.url + "/v1", thinking_disabled=False, effort=None)
    assert r["ok"]
    retries = [e for e in app.state.emitter.events if e.fix_id == "tool-args-retry"]
    # two tool calls in the loop, each retried exactly once, never more
    per_request: dict[str, int] = {}
    for e in retries:
        per_request[e.request_id] = per_request.get(e.request_id, 0) + 1
    assert retries and set(per_request.values()) == {1}


def test_unrepairable_after_retry_returns_error_not_empty_args(make_bridge: Any, tmp_path: Path) -> None:
    import httpx

    # a script rule that keeps returning prose even after the injected error
    rules = [{"name": "bad", "reply": {"tool_calls": [{"name": "get_weather", "arguments": "not json at all"}]}}]
    mock = MockServer(MockConfig(rules=rules)).start()
    try:
        profile = get_profile("mock").model_copy(update={"base_url": mock.url + "/api/paas/v4", "spawn_mock": False})
        app = create_app(ProxyConfig(profile=profile, upstream=get_upstream("zai"), out_dir=tmp_path))
        with BridgeServer(app) as bridge:
            body = {
                "model": "glm-5.3",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "parameters": {"type": "object", "properties": {"city": {}}, "required": ["city"]},
                        },
                    }
                ],
            }
            r = httpx.post(bridge.url + "/v1/chat/completions", json=body)
            assert r.status_code == 502
            assert r.json()["error"]["code"] == "bridge_tool_arguments_invalid"
            assert "{}" not in r.text
            body["stream"] = True
            rs = httpx.post(bridge.url + "/v1/chat/completions", json=body)
            assert (
                rs.status_code == 200
                and "bridge_tool_arguments_invalid" in rs.text
                and rs.text.rstrip().endswith("[DONE]")
            )
        ids = [e.fix_id for e in app.state.emitter.events]
        assert ids.count("tool-args-retry") == 2 and ids.count("tool-args-unrepaired") == 2
    finally:
        mock.stop()


def test_cerebras_flavor_strips_reasoning_replay(make_bridge: Any) -> None:
    mock, bridge, app = make_bridge(["bridge-reasoning-reject"], flavor="cerebras")
    direct = run_loop(mock.url + "/api/paas/v4", thinking_disabled=False, effort=None)
    bridged = run_loop(bridge.url + "/v1", thinking_disabled=False, effort=None)
    assert not direct["ok"] and "422" in direct["error"]
    assert bridged["ok"]
    assert "reasoning-replay" in {e.fix_id for e in app.state.emitter.events}


def test_session_envelope_written_and_has_no_content_by_default(make_bridge: Any, tmp_path: Path) -> None:
    _mock, bridge, _app = make_bridge(["bridge-broken-json-args"])
    run_loop(bridge.url + "/v1", prompts=("Use get_weather for city=SECRETCITY please",))
    bridge.stop()
    envs, warnings = load_envelopes(tmp_path)
    assert not warnings and len(envs) == 1
    env = envs[0][1]
    assert env.component == "bridge" and env.items and all(i.kind == "fix_event" for i in env.items)
    text = (envs[0][0]).read_text()
    assert "SECRETCITY" not in text and "sk-test-not-a-real-key" not in text
    assert "content" not in json.dumps([i.data for i in env.items])
    assert env.data["requests"]


def test_log_content_flag_adds_content_and_nothing_logs_auth(make_bridge: Any, tmp_path: Path, caplog: Any) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="glmkit.bridge")
    _mock, bridge, _app = make_bridge(["bridge-broken-json-args"], log_content=True)
    run_loop(bridge.url + "/v1", thinking_disabled=False, effort=None)
    bridge.stop()
    envs, _ = load_envelopes(tmp_path)
    assert any(i.data.get("content") for i in envs[0][1].items)
    assert "sk-test-not-a-real-key" not in caplog.text and "Bearer" not in caplog.text
    assert "Paris" in caplog.text  # content is logged only because log_content is on


def test_no_content_in_logs_by_default(make_bridge: Any, tmp_path: Path, caplog: Any) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    _mock, bridge, _app = make_bridge(["bridge-broken-json-args"])
    run_loop(bridge.url + "/v1", prompts=("Use get_weather for city=SECRETCITY",), thinking_disabled=False, effort=None)
    assert "SECRETCITY" not in caplog.text and "sk-test-not-a-real-key" not in caplog.text


def test_upstream_errors_are_classified(tmp_path: Path) -> None:
    import httpx

    rules = [
        {"name": "overload", "error": {"status": 429, "code": "1305", "message": "overloaded"}},
        {"name": "billing", "error": {"status": 429, "code": "1113", "message": "no balance"}},
        {"name": "window", "error": {"status": 429, "code": "1308", "message": "limit, next_flush_time: 4102444800"}},
    ]
    with MockServer(MockConfig(rules=rules)) as mock:
        profile = get_profile("mock").model_copy(update={"base_url": mock.url + "/api/paas/v4", "spawn_mock": False})
        app = create_app(ProxyConfig(profile=profile, upstream=get_upstream("zai"), out_dir=tmp_path))
        with BridgeServer(app) as bridge:
            body = {"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]}
            url = bridge.url + "/v1/chat/completions"
            r = httpx.post(url, json=body, headers={"x-glmkit-scenario": "overload"})
            assert r.status_code == 503 and r.headers["x-should-retry"] == "true" and int(r.headers["retry-after"]) > 0
            r = httpx.post(url, json=body, headers={"x-glmkit-scenario": "billing"})
            assert r.status_code == 402 and r.headers["x-should-retry"] == "false" and "retry-after" not in r.headers
            r = httpx.post(url, json=body, headers={"x-glmkit-scenario": "window"})
            assert r.status_code == 429 and int(r.headers["retry-after"]) > 3600


def test_traceback_500_is_sanitized(make_bridge: Any) -> None:
    import httpx

    # generic flavor leaves thinking alone, so the planted defect (500 + Python traceback) is reached
    mock, bridge, app = make_bridge(["forced-thinking-500"], flavor="generic")
    body = {"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}], "thinking": {"type": "disabled"}}
    direct = httpx.post(mock.url + "/api/paas/v4/chat/completions", json=body)
    assert direct.status_code == 500 and "Traceback" in direct.text
    r = httpx.post(bridge.url + "/v1/chat/completions", json=body)
    assert r.status_code == 502 and "Traceback" not in r.text and "/srv/gateway" not in r.text
    assert "error-sanitize" in {e.fix_id for e in app.state.emitter.events}
