"""Run the same OpenAI-SDK tool-calling loop directly against a defective mock and through the bridge.

    uv run python tools/bridge-proof/proof.py --out results/bridge-demo

Each scenario plants a mock defect for the request via the X-GLMKit-Defects header (the bridge forwards it).
The bridge session (every fix event) is written to <out>/<run_id>/result.json and shows on the Bridge tab.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.bridge_loop import run_loop

from glmkit.bridge.core import get_upstream
from glmkit.bridge.proxy import ProxyConfig, create_app
from glmkit.bridge.server import BridgeServer
from glmkit.contracts.profile import get_profile
from glmkit.mock import MockConfig, MockServer

# (title, planted defect, upstream flavor, client sends thinking:disabled + effort:medium)
SCENARIOS = [
    ("forced thinking (thinking: disabled, effort medium)", "none", "zai", True),
    ("oneOf tool schema -> {} arguments", "bridge-oneof-empty-args", "zai", False),
    ("broken-JSON arguments", "bridge-broken-json-args", "zai", False),
    ("unrepairable arguments (retry with parse error)", "bridge-unrepairable-args", "zai", False),
    ("reasoning_content rejected (Cerebras flavor)", "bridge-reasoning-reject", "cerebras", False),
]


def show(r: dict) -> str:
    return "PASS " + ", ".join(r["tool_results"]) if r["ok"] else "FAIL " + r["error"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("results/bridge-demo"))
    out = ap.parse_args().out
    bad = 0
    with MockServer(MockConfig()) as mock:
        profile = get_profile("mock").model_copy(update={"base_url": mock.url + "/api/paas/v4", "spawn_mock": False})
        for title, defect, flavor, forced in SCENARIOS:
            app = create_app(
                ProxyConfig(profile=profile, upstream=get_upstream(flavor), out_dir=out, flush_interval_s=60)
            )
            with BridgeServer(app) as bridge:
                print(f"== {title}  [defect={defect}, flavor={flavor}]")
                for stream in (False, True):
                    h = {"x-glmkit-defects": defect}
                    kw = {"thinking_disabled": forced, "effort": "medium" if forced else None}
                    direct = run_loop(mock.url + "/api/paas/v4", stream=stream, headers=h, **kw)
                    via = run_loop(bridge.url + "/v1", stream=stream, headers=h, **kw)
                    mode = "stream   " if stream else "nonstream"
                    print(f"  {mode} direct : {show(direct)}\n  {mode} bridged: {show(via)}")
                    bad += (not via["ok"]) + direct["ok"]
                fixes = sorted({e.fix_id for e in app.state.emitter.events})
                print(f"  fix events: {len(app.state.emitter.events)} ({', '.join(fixes)})")
        print(f"session envelopes written under {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
