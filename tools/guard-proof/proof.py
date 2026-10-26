# ruff: noqa: E501
"""Scripted runaway agent against the mock GLM, with and without the guard; then audit-chain verify and tamper.

    uv run python tools/guard-proof/proof.py --out results/guard-demo

The agent keeps running `make test` (which fails) no matter what the model or the bridge says. Without the guard it
runs to the client's turn cap. With the guard it is stopped by the loop detector (before the hard budget), or by the
hard budget alone when the loop detector is off. Also shows a destructive command removed from the response.
Everything lands in <out>: the audit log, the guard session envelopes (Guard tab) and the audit log copy that is
tampered with by one byte.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.guard_agent import rules, run_agent

from glmkit.bridge.core import get_upstream
from glmkit.bridge.proxy import ProxyConfig, create_app
from glmkit.bridge.server import BridgeServer
from glmkit.contracts.profile import get_profile
from glmkit.cost.prices import price_for
from glmkit.guard import GuardMiddleware, verify
from glmkit.guard.config import Budget, GuardConfig, LoopConfig
from glmkit.mock import MockConfig, MockServer

CAP = 40  # client turn cap ("runs to the cap")
N = 3  # loop threshold


def usd(r: dict) -> float:
    e = price_for("glm-5.3")
    assert e and e.input is not None and e.output is not None
    return r["prompt_tokens"] / 1e6 * e.input + r["completion_tokens"] / 1e6 * e.output


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("results/guard-demo"))
    out = ap.parse_args().out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    audit = out / "audit.jsonl"
    HARD, SOFT = 0.10, 0.04
    full = GuardMiddleware(
        GuardConfig(
            audit_path=str(audit),
            session_budget=Budget(soft_usd=SOFT, hard_usd=HARD),
            loop=LoopConfig(repeat_threshold=N, action="block"),
        ),
        out_dir=out,
    )
    gradual = GuardMiddleware(
        GuardConfig(
            audit_path=str(audit),
            session_budget=Budget(soft_usd=SOFT, hard_usd=HARD),
            loop=LoopConfig(repeat_threshold=N, action="inject_then_block", escalate_after=2),
        ),
        out_dir=out,
    )
    budget_only = GuardMiddleware(
        GuardConfig(
            audit_path=str(audit), session_budget=Budget(soft_usd=0.03, hard_usd=0.06), loop=LoopConfig(enabled=False)
        ),
        out_dir=out,
    )
    bad = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal bad
        print(("  ok   " if ok else "  FAIL ") + msg)
        bad += not ok

    with MockServer(MockConfig(rules=rules())) as mock, MockServer(MockConfig(rules=rules(obedient=True))) as mock_ok:

        def run(
            m: MockServer,
            mw: GuardMiddleware | None,
            session: str,
            agent: str,
            prompt: str = "RUNAWAY: run the build",
            **kw,
        ):
            profile = get_profile("mock").model_copy(update={"base_url": m.url + "/api/paas/v4", "spawn_mock": False})
            app = create_app(
                ProxyConfig(
                    profile=profile,
                    upstream=get_upstream("zai"),
                    out_dir=out,
                    middleware=[mw] if mw else [],
                    flush_interval_s=60,
                )
            )
            with BridgeServer(app) as b:
                return run_agent(
                    b.url + "/v1",
                    prompt,
                    max_turns=CAP,
                    headers={"x-glmkit-session": session, "x-glmkit-agent": agent},
                    **kw,
                )

        print(f"== runaway agent: `make test` fails every time; client cap {CAP} turns; hard budget ${HARD}")
        r0 = run(mock, None, "s-noguard", "runaway-agent")
        print(
            f"  WITHOUT guard : executed {len(r0['executed'])} times, stopped by {r0['stopped_by']}, spend ${usd(r0):.4f}"
        )
        check(len(r0["executed"]) == CAP and usd(r0) > HARD, "ran to the cap and past the hard budget")
        r1 = run(mock, full, "s-guard-block", "runaway-agent")
        s1 = next(s for s in full.snapshot()["sessions"] if s["session"] == "s-guard-block")
        print(
            f"  WITH guard (loop action=block, N={N}): executed {len(r1['executed'])} times, stopped by {r1['stopped_by']} ({r1['error']}), spend ${s1['usd']:.4f}"
        )
        check(
            len(r1["executed"]) <= N and s1["usd"] < HARD,
            f"stopped within N={N} repetitions and before the hard budget",
        )
        r2 = run(mock, gradual, "s-guard-inject", "stubborn-agent")
        s2 = next(s for s in gradual.snapshot()["sessions"] if s["session"] == "s-guard-inject")
        print(
            f"  WITH guard (inject then block, N={N}+2), agent ignores the message: executed {len(r2['executed'])}, stopped by {r2['stopped_by']}, spend ${s2['usd']:.4f}"
        )
        check(
            len(r2["executed"]) <= N + 2 and s2["usd"] < HARD,
            f"stopped within N+escalate={N + 2} and before the hard budget",
        )
        r3 = run(mock_ok, gradual, "s-guard-obey", "obedient-agent")
        print(
            f"  WITH guard (inject), agent heeds the message: executed {len(r3['executed'])}, stopped by {r3['stopped_by']}: {(r3['final'] or '')[:50]!r}"
        )
        check(
            len(r3["executed"]) == N and r3["stopped_by"] == "model",
            f"rethink message ended the loop after exactly N={N}",
        )
        r4 = run(mock, budget_only, "s-budget-only", "runaway-agent")
        s4 = next(s for s in budget_only.snapshot()["sessions"] if s["session"] == "s-budget-only")
        print(
            f"  WITH guard (loop detector OFF, hard budget $0.06): executed {len(r4['executed'])}, stopped by {r4['stopped_by']} ({r4['error']}), spend ${s4['usd']:.4f}"
        )
        check(r4["error"] == "guard_budget_exceeded" and len(r4["executed"]) < CAP, "hard budget alone stops it")

        print("== destructive command removed before the client sees it")
        for stream in (False, True):
            r = run(
                mock,
                full,
                f"s-danger-{'stream' if stream else 'plain'}",
                "ops-agent",
                "DANGER: clean up",
                stream=stream,
            )
            print(f"  stream={stream!s:5}: executed {r['executed']}, agent saw: {(r['final'] or '')[:96]!r}")
            check(not r["executed"] and "rm-root-or-home" in (r["final"] or ""), "rm -rf / never reached the client")
        r = run(mock, full, "s-careful", "ops-agent", "CAREFUL: push")
        check(bool(r["executed"]), "git push --force-with-lease is allowed (safe look-alike)")

    for mw in (full, gradual, budget_only):
        if mw.recorder:
            mw.recorder.flush()
    print("== audit chain")
    res = verify(audit)
    print(f"  verify (untouched): ok={res.ok} entries={res.entries} head={res.head[:16] if res.head else None}...")
    check(res.ok, "untouched log verifies")
    tampered = out / "audit.tampered.jsonl"
    data = bytearray(audit.read_bytes())
    i = data.index(b'"decision":"allow"') + len(b'"decision":"')
    data[i] = ord("A")  # allow -> Allow: one byte
    tampered.write_bytes(bytes(data))
    shutil.copy(str(audit) + ".head", str(tampered) + ".head")
    res2 = verify(tampered)
    print(f"  verify (one byte changed, allow -> Allow): ok={res2.ok}  {res2.error}")
    check(not res2.ok, "one-byte edit is detected")
    print(f"guard session envelopes under {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
