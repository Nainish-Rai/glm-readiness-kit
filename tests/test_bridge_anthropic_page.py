"""Bridge tab with both protocols: the payload carries a view per protocol and the token check passes."""

from __future__ import annotations

import json
from pathlib import Path

import anthropic
import httpx

from glmkit.mock import MockConfig, MockServer
from glmkit.report.builder import build_site
from tests.anthropic_session import API_KEY, MODEL
from tests.bridge_anthropic_fixture import ALL_DEFECTS, build_bridge
from tests.bridge_loop import run_loop


def test_bridge_tab_has_protocol_views(tmp_path: Path) -> None:
    res = tmp_path / "results"
    with MockServer(MockConfig(defects=[*ALL_DEFECTS, "bridge-broken-json-args"])) as mock:
        bridge, _app = build_bridge(mock, "openai", res)
        try:
            run_loop(bridge.url + "/v1", thinking_disabled=False, effort=None)
            c = anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY)
            c.messages.create(
                model=MODEL,
                max_tokens=32,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": "Say OK"}],
            )
            httpx.post(
                bridge.url + "/v1/messages", json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
            )
            c.messages.count_tokens(model=MODEL, messages=[{"role": "user", "content": "Say OK"}])
        finally:
            bridge.stop()
    site = tmp_path / "site"
    built = build_site(res, site)
    assert "bridge" in built.pages and built.findings == []
    data = json.loads((site / "data" / "bridge.json").read_text())
    assert [p["id"] for p in data["protocols"]] == ["all", "openai", "anthropic"]
    fixes = {p: {t["fix_id"] for t in v["top"]} for p, v in data["views"].items()}
    assert {"anthropic-max-tokens-default", "anthropic-count-tokens"} <= fixes["anthropic"]
    assert "tool-args-repair" in fixes["openai"] and "anthropic-count-tokens" not in fixes["openai"]
    assert fixes["all"] == fixes["openai"] | fixes["anthropic"]
    assert {e["protocol"] for e in data["events"]} == {"openai", "anthropic"}
    assert data["views"]["anthropic"]["kpis"][1]["value"] == 3  # two /v1/messages calls and a count_tokens
