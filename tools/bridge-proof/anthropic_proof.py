"""Anthropic Messages path: the same Claude-Code-shaped session direct and through the bridge, against both
upstream types (Z.ai's Anthropic-compatible endpoint and an OpenAI-compatible host), with the mock's Anthropic
defects planted.

    uv run python tools/bridge-proof/anthropic_proof.py --out results/bridge-demo

Each bridge session (every fix event, tagged protocol=anthropic) is written to <out>/<run_id>/result.json and shows
on the Bridge tab: `uv run glmkit report build results/bridge-demo --out site/`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import anthropic
from tests.anthropic_corpora import CORPORA
from tests.anthropic_session import API_KEY, MODEL, run_session, tool_loop
from tests.bridge_anthropic_fixture import ALL_DEFECTS, UPSTREAMS, build_bridge

from glmkit.bridge.anthropic import AnthropicOptions, FakeSearchBackend
from glmkit.mock import MockConfig, MockServer

DIRECT = "/api/anthropic"


def pct(got: int, want: int) -> str:
    return f"{abs(got - want) / want:.1%}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("results/bridge-demo"))
    out = ap.parse_args().out
    bad = 0
    for kind in UPSTREAMS:
        print(f"\n######## upstream: {kind} ({UPSTREAMS[kind][0]})")
        with MockServer(MockConfig(defects=ALL_DEFECTS)) as mock, MockServer(MockConfig()) as clean:
            bridge, app = build_bridge(mock, kind, out, options=AnthropicOptions(search_backend=FakeSearchBackend()))
            try:
                print("== Claude-Code-shaped session, all four Anthropic defects planted")
                direct = run_session(mock.url + DIRECT)
                bridged = run_session(bridge.url)
                print("  direct (no bridge):\n" + direct.show())
                print("  via the bridge:\n" + bridged.show())
                bad += (not bridged.ok) + direct.ok

                print("== Anthropic SDK tool loop (thinking: adaptive)")
                for stream in (False, True):
                    r = tool_loop(bridge.url, stream=stream)
                    mode, verdict = ("stream   " if stream else "nonstream"), ("PASS" if r["ok"] else "FAIL")
                    print(f"  {mode} via the bridge: {verdict} {r.get('results')}")
                    bad += not r["ok"]

                print("== count_tokens on 10 corpora (reference = the same mock without the defect)")
                sdk = anthropic.Anthropic(base_url=bridge.url, api_key=API_KEY)
                raw = anthropic.Anthropic(base_url=mock.url + DIRECT, api_key=API_KEY)
                ref = anthropic.Anthropic(base_url=clean.url + DIRECT, api_key=API_KEY)
                print(f"  {'corpus':16} {'reference':>9} {'direct':>8} {'off':>6} {'bridge':>8} {'off':>6}")
                for name, text in CORPORA.items():
                    msgs = [{"role": "user", "content": text}]
                    want = ref.messages.count_tokens(model=MODEL, messages=msgs).input_tokens
                    d = raw.messages.count_tokens(model=MODEL, messages=msgs).input_tokens
                    b = sdk.messages.count_tokens(model=MODEL, messages=msgs).input_tokens
                    bad += abs(b - want) / want > 0.05
                    print(f"  {name:16} {want:9d} {d:8d} {pct(d, want):>6} {b:8d} {pct(b, want):>6}")

                print("== web_search emulation (offline fake backend)")
                msg = sdk.messages.create(
                    model=MODEL,
                    max_tokens=512,
                    tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
                    messages=[{"role": "user", "content": "Use web_search with query=glm"}],
                )
                print(
                    "  blocks:",
                    [b.type for b in msg.content],
                    "searches:",
                    msg.usage.server_tool_use.web_search_requests,
                )
                fixes = sorted({e.fix_id for e in app.state.emitter.events})
                print(f"== fix events: {len(app.state.emitter.events)}  ({', '.join(fixes)})")
            finally:
                bridge.stop()
    print(f"\nsession envelopes written under {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
