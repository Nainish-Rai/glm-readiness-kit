"""Real LiteLLM proxy (pinned in uv.lock, run in its own uv env) + the glmkit plugin, in front of mock GLM servers.

Marked ``litellm`` and excluded from the default run (it needs the pinned LiteLLM wheel set, ~15 s per proxy start):

    uv run pytest -m litellm

LiteLLM pins ``openai<3`` while the dev group needs ``openai>=3``, so the proxy runs via
``uv run --isolated --no-dev --extra litellm --frozen litellm ...`` and the tests talk to it over HTTP.
Each scenario has its own mock server with one planted defect; "direct" goes to that mock, "via" goes through
LiteLLM (model alias -> the same mock). Everything runs twice: default routing and ``LITELLM_RUST=1``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from glmkit.mock import MockConfig, MockServer
from tests.bridge_loop import run_loop

pytestmark = pytest.mark.litellm

REPO = Path(__file__).resolve().parent.parent
KEY = "sk-glmkit-litellm-test-master-key"
# alias -> (planted defect, run_loop client options)
SCENARIOS: dict[str, tuple[str, dict[str, Any]]] = {
    "forced-thinking": ("none", {"thinking_disabled": True, "effort": None}),  # thinking: disabled on glm-5.3
    "effort-clamp": ("none", {"thinking_disabled": False, "effort": "medium"}),  # medium is not low|high|max
    "oneof-schema": ("bridge-oneof-empty-args", {"thinking_disabled": False, "effort": None}),
    "broken-json": ("bridge-broken-json-args", {"thinking_disabled": False, "effort": None}),
    "unrepairable": ("bridge-unrepairable-args", {"thinking_disabled": False, "effort": None}),
}
HOPELESS = "hopeless"  # the model never produces usable arguments, even after the one retry
NO_BALANCE = "no-balance"  # Z.ai answers HTTP 429 + business code 1113; the plugin maps it to 402
PROMPT_MARKER = "city=Paris"  # appears in the user prompts of bridge_loop: must never reach the session file


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Gateway:
    def __init__(self, rust: bool, tmp: Path) -> None:
        self.rust = rust
        self.out = tmp / "results"
        self.mocks = {
            name: MockServer(MockConfig(defects=[] if defect == "none" else [defect]))
            for name, (defect, _) in SCENARIOS.items()
        }
        self.mocks[HOPELESS] = MockServer(
            MockConfig(
                rules=[
                    {
                        "name": "h",
                        "reply": {"tool_calls": [{"name": "get_weather", "arguments": "no json here, sorry"}]},
                    }
                ]
            )
        )
        self.mocks[NO_BALANCE] = MockServer(
            MockConfig(
                rules=[{"name": "nb", "error": {"status": 429, "code": "1113", "message": "Insufficient balance"}}]
            )
        )
        self.port = _free_port()
        self.cfg = tmp / "config.yaml"
        self.log = tmp / "litellm.log"
        self.proc: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        for m in self.mocks.values():
            m.start()
        self.cfg.write_text(
            yaml.safe_dump(
                {
                    "model_list": [
                        {
                            "model_name": name,
                            "litellm_params": {
                                "model": "zai/glm-5.3",
                                "api_base": m.url + "/api/paas/v4",
                                "api_key": "sk-mock-not-a-real-key",
                            },
                        }
                        for name, m in self.mocks.items()
                    ],
                    "litellm_settings": {"callbacks": "glmkit.bridge.litellm_plugin.plugin"},
                    "router_settings": {"num_retries": 0},  # otherwise LiteLLM retries the 429 for ~1 minute first
                }
            )
        )
        env = {
            **os.environ,
            "LITELLM_MASTER_KEY": KEY,
            "GLMKIT_BRIDGE_OUT": str(self.out),
            "LITELLM_RUST": "1" if self.rust else "0",
            "LITELLM_LOG": "ERROR",
        }
        with self.log.open("wb") as log:
            self.proc = subprocess.Popen(
                ["uv", "run", "--isolated", "--no-dev", "--extra", "litellm", "--frozen", "litellm",
                 "--config", str(self.cfg), "--host", "127.0.0.1", "--port", str(self.port)],
                cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
            )  # fmt: skip
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("litellm exited:\n" + self.log.read_text()[-3000:])
            try:
                if httpx.get(self.url + "/health/liveliness", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("litellm did not start:\n" + self.log.read_text()[-3000:])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)  # atexit in the plugin writes the final session file
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for m in self.mocks.values():
            m.stop()

    def session(self) -> dict[str, Any]:
        files = sorted(self.out.glob("*/result.json"), key=lambda f: f.stat().st_mtime)
        assert files, f"no session file under {self.out}"
        return json.loads(files[-1].read_text())


@pytest.fixture(scope="module", params=[False, True], ids=["python", "LITELLM_RUST=1"])
def gateway(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Gateway]:
    gw = Gateway(request.param, tmp_path_factory.mktemp("litellm"))
    try:
        gw.start()
        yield gw
    finally:
        gw.stop()


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_direct_fails_via_litellm_passes(gateway: Gateway, scenario: str, stream: bool) -> None:
    _, kw = SCENARIOS[scenario]
    direct = run_loop(gateway.mocks[scenario].url + "/api/paas/v4", stream=stream, **kw)
    via = run_loop(gateway.url + "/v1", stream=stream, api_key=KEY, model=scenario, **kw)
    assert not direct["ok"], "the planted defect must break the direct call"
    assert via["ok"], via
    assert via["tool_results"] == ["sunny in Paris", "found glm"]


def test_business_error_code_mapped_to_402(gateway: Gateway) -> None:
    body = {"model": NO_BALANCE, "messages": [{"role": "user", "content": "hi"}]}
    direct = httpx.post(
        gateway.mocks[NO_BALANCE].url + "/api/paas/v4/chat/completions", json={**body, "model": "glm-5.3"}
    )
    via = httpx.post(
        gateway.url + "/v1/chat/completions", json=body, headers={"authorization": f"Bearer {KEY}"}, timeout=90
    )
    assert direct.status_code == 429
    assert via.status_code == 402, via.text


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_arguments_that_stay_broken_become_an_error_not_garbage(gateway: Gateway, stream: bool) -> None:
    body = {
        "model": HOPELESS,
        "stream": stream,
        "messages": [{"role": "user", "content": "weather in Paris"}],
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
    }
    via = httpx.post(gateway.url + "/v1/chat/completions", json=body, headers={"authorization": f"Bearer {KEY}"})
    assert "no json here" not in via.text, "the broken arguments must not reach the client"
    if not stream:
        assert via.status_code == 502, via.text
    assert "bridge_tool_arguments_invalid" in via.text


def test_effort_clamp_and_session_envelope(gateway: Gateway) -> None:
    """Runs after the scenarios (file order): the plugin's session file shows every fix with source litellm."""
    # explicit effort values: medium is clamped to high, an allowed value is untouched
    r = run_loop(gateway.url + "/v1", api_key=KEY, model="forced-thinking", thinking_disabled=False, effort="xhigh")
    assert r["ok"], r
    time.sleep(2.5)  # the plugin rewrites the session file about once a second
    env = gateway.session()
    assert env["component"] == "bridge"
    assert env["config"]["source"] == "litellm"
    items = env["items"]
    fixes = {i["group"] for i in items}
    assert {"thinking-disabled-to-low", "effort-clamp", "schema-flatten", "schema-args-unflatten"} <= fixes
    assert {"tool-args-repair", "tool-args-retry", "tool-args-unrepaired", "error-classify"} <= fixes
    assert all(i["kind"] == "fix_event" and i["data"]["source"] == "litellm" for i in items)
    assert env["data"]["requests"], "per-request records are written too"
    assert PROMPT_MARKER not in json.dumps(env), "message content must not be recorded by default"


def test_example_configs_load_in_the_pinned_litellm() -> None:
    """The shipped examples parse, the plugin object resolves, and web-search interception is enabled for zai."""
    code = (
        "import sys, yaml\n"
        "from litellm.integrations.websearch_interception.handler import WebSearchInterceptionLogger\n"
        "from glmkit.bridge.litellm_plugin import plugin\n"
        "cfg = yaml.safe_load(open(sys.argv[1]))['litellm_settings']\n"
        "ws = WebSearchInterceptionLogger.initialize_from_proxy_config(\n"
        "    litellm_settings=cfg, callback_specific_params={})\n"
        "print('providers', ws.enabled_providers, 'plugin', type(plugin).__name__)\n"
    )
    run = subprocess.run(
        ["uv", "run", "--isolated", "--no-dev", "--extra", "litellm", "--frozen", "python", "-c", code,
         str(REPO / "examples" / "litellm" / "config.websearch.yaml")],
        cwd=REPO, capture_output=True, text=True, timeout=240,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr[-2000:]
    assert "providers ['zai'] plugin GLMBridgePlugin" in run.stdout
    assert "glmkit.bridge.litellm_plugin.plugin" in (REPO / "examples/litellm/config.yaml").read_text()
