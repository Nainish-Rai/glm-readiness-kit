"""Same OpenAI-SDK tool-calling loop, direct against a defective mock and through a real LiteLLM proxy running the
glmkit plugin. Needs `uv` (the proxy runs in its own env from the `litellm` extra, pinned in uv.lock).

    uv run python tools/litellm-proof/proof.py --out results/litellm-demo

The plugin's session (every fix event, source `litellm`) lands in <out>/<run_id>/result.json for the Bridge tab.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.bridge_loop import run_loop
from tests.test_litellm_integration import KEY, SCENARIOS, Gateway


def show(r: dict) -> str:
    return "PASS " + ", ".join(r["tool_results"]) if r["ok"] else "FAIL " + r["error"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("results/litellm-demo"))
    ap.add_argument("--rust", action="store_true", help="start LiteLLM with LITELLM_RUST=1")
    args = ap.parse_args()
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        gw = Gateway(args.rust, Path(tmp))
        gw.out = args.out.resolve()
        try:
            gw.start()
            print(f"LiteLLM proxy up on {gw.url} (LITELLM_RUST={'1' if args.rust else '0'}), plugin: glmkit")
            for name, (defect, kw) in SCENARIOS.items():
                print(f"== {name}  [defect={defect}, client={kw}]")
                for stream in (False, True):
                    direct = run_loop(gw.mocks[name].url + "/api/paas/v4", stream=stream, **kw)
                    via = run_loop(gw.url + "/v1", stream=stream, api_key=KEY, model=name, **kw)
                    mode = "stream   " if stream else "nonstream"
                    print(f"  {mode} direct : {show(direct)}\n  {mode} litellm: {show(via)}")
                    bad += (not via["ok"]) + direct["ok"]
            time.sleep(2.5)
        finally:
            gw.stop()
        env = gw.session()
        fixes = sorted({i["group"] for i in env["items"]})
        print(f"fix events: {len(env['items'])} ({', '.join(fixes)}); source={env['config']['source']}")
        print(f"session envelope written under {gw.out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
