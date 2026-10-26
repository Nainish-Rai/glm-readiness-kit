# ruff: noqa: E501
"""``glmkit demo run``: the whole first-weeks pipeline in one command, on the mock or on real targets.

Steps (each a real glmkit command run as a subprocess in the workspace): conformance, migration eval on the chosen
demo set(s), cost forensics, evidence pack, dashboard. Which data ran (stand-in or fetched) and which targets ran
(mock with planted quality, or real profiles) is printed up front and again at the end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from glmkit.workbench.builders import merge_sets, with_mock_answers
from glmkit.workbench.cards import set_ids
from glmkit.workbench.fetch import fetched
from glmkit.workbench.standins import STANDINS

MOCK_BASELINE = "demo-incumbent"
MOCK_TARGETS = ["demo-glm-equal", "demo-glm-worse", "demo-glm@low", "demo-glm@max"]
MOCK_JUDGE = "demo-judge"
STEPS = ("conform", "eval", "cost", "evidence", "report")


class DemoError(RuntimeError):
    pass


@dataclass
class DemoOptions:
    root: Path
    sets: list[str]  # set ids, resolved
    data_dir: Path
    standin: bool = False
    targets: list[str] = field(default_factory=list)  # empty = mock demo targets
    baseline: str | None = None
    judge: str | None = None
    skip: set[str] = field(default_factory=set)
    max_items: int | None = None
    echo: Callable[[str], None] = print

    @property
    def mock(self) -> bool:
        return not self.targets


def resolve_sets(name: str) -> list[str]:
    ids = set_ids()
    if name == "all":
        return ids
    if name not in ids:
        raise DemoError(f"unknown demo set {name!r}; choose one of: all, {', '.join(ids)}")
    return [name]


def load_set(set_id: str, data_dir: Path, standin: bool) -> tuple[dict, dict]:
    """(golden set dict, mode info). Fetched data wins unless ``standin`` is forced."""
    rec = None if standin else fetched(set_id, data_dir)
    if rec:
        gs = yaml.safe_load((data_dir / rec["golden_set"]).read_text(encoding="utf-8"))
        return gs, {"set": set_id, "mode": "fetched", "items": len(gs["items"]), "revision": rec["revision"][:12]}
    gs = STANDINS[set_id]()
    return gs, {"set": set_id, "mode": "stand-in", "items": len(gs["items"]), "revision": None}


def _run(opts: DemoOptions, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "glmkit", *args], cwd=opts.root, env=env, capture_output=True, text=True, check=False
    )


def _tail(proc: subprocess.CompletedProcess[str], keys: tuple[str, ...]) -> list[str]:
    lines = (proc.stdout + proc.stderr).splitlines()
    hit = [ln for ln in lines if ln.startswith(keys)]
    return hit or lines[-2:]


def run_demo(o: DemoOptions) -> int:
    """Run the pipeline; returns 0 when every step ran (a failing verdict is data, not an error), else 1."""
    t0 = time.monotonic()
    root = o.root
    demo_dir = root / "demo"
    demo_dir.mkdir(exist_ok=True)
    loaded = [load_set(s, o.data_dir, o.standin) for s in o.sets]
    sets = [g for g, _ in loaded]
    infos = [i for _, i in loaded]
    if not o.mock and (not o.baseline):
        raise DemoError("--baseline is required with --targets (the incumbent profile to compare against)")
    judged = any(
        (g["graders"].get(it["task_type"]) or it.get("grader") or {}).get("type") == "judge"
        for g in sets
        for it in g["items"]
    )
    judge = MOCK_JUDGE if o.mock else o.judge
    if judged and not judge:
        raise DemoError("these sets contain judge-graded items; pass --judge PROFILE")
    merged = merge_sets("demo-" + ("all" if len(o.sets) > 1 else o.sets[0]), sets)
    if o.max_items:
        merged["items"] = merged["items"][: o.max_items]
    if o.mock:
        merged = with_mock_answers(merged)

    modes = ", ".join(
        f"{i['set']}={i['mode']} ({i['items']} items" + (f", rev {i['revision']}" if i["revision"] else "") + ")"
        for i in infos
    )
    o.echo(f"workspace: {root}")
    o.echo(f"data:    {modes}")
    if any(i["mode"] == "stand-in" for i in infos):
        o.echo(
            "         stand-in = small synthetic items bundled with the kit; run `glmkit demo fetch all` for the licensed sets"
        )
    if o.mock:
        o.echo(
            "targets: MOCK server with planted quality (baseline demo-incumbent; demo-glm-equal, demo-glm-worse, demo-glm@low/max); judge: mock"
        )
        o.echo("         results show how the kit reports, not how GLM performs")
    else:
        o.echo(
            f"targets: REAL profiles: baseline {o.baseline}, candidates {', '.join(o.targets)}"
            + (f", judge {judge}" if judge else "")
        )

    set_path = demo_dir / "sets" / f"{merged['name']}.yaml"
    set_path.parent.mkdir(exist_ok=True)
    set_path.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (demo_dir / "last-run.json").write_text(
        json.dumps({"sets": infos, "targets": "mock" if o.mock else o.targets}, indent=1), encoding="utf-8"
    )

    env = dict(os.environ)
    env["GLMKIT_WORKSPACE"] = str(root)
    profile_dirs = [str(root / "profiles")]
    prices: list[str] = []
    if o.mock:
        from glmkit.eval.demo import write_demo

        paths = write_demo(demo_dir / "_eval-demo", 1)
        profiles_out = demo_dir / "profiles"
        profiles_out.mkdir(exist_ok=True)
        for f in paths["profiles"].glob("*.yaml"):
            (profiles_out / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        (demo_dir / "prices.yaml").write_text(paths["prices"].read_text(encoding="utf-8"), encoding="utf-8")
        profile_dirs.append(str(profiles_out))
        prices = ["--prices", str(demo_dir / "prices.yaml")]
    env["GLMKIT_PROFILES_DIRS"] = os.pathsep.join(profile_dirs)

    failed = 0

    def step(
        name: str, args: list[str], keys: tuple[str, ...], full: bool = False, ok_codes: tuple[int, ...] = (0,)
    ) -> None:
        nonlocal failed
        if name in o.skip:
            o.echo(f"[{name}] skipped")
            return
        t = time.monotonic()
        proc = _run(o, args, env)
        took = time.monotonic() - t
        good = proc.returncode in ok_codes
        o.echo(
            f"[{name}] {'ok' if good else 'FAILED (exit ' + str(proc.returncode) + ')'}  {took:.1f}s   glmkit {' '.join(args[:3])} ..."
        )
        shown = (
            (proc.stdout.splitlines() if full else _tail(proc, keys))
            if good
            else (proc.stdout + proc.stderr).splitlines()[-12:]
        )
        for ln in shown:
            o.echo("    " + ln)
        failed += 0 if good else 1

    conform_targets = ["mock"] if o.mock else sorted({t.split("@")[0] for t in [o.baseline or "", *o.targets] if t})
    for tgt in conform_targets:
        step("conform", ["conform", "run", "--target", tgt, "--out", "results"], ("verdict:",), ok_codes=(0, 1))

    cand = MOCK_TARGETS if o.mock else o.targets
    args = [
        "eval", "run", "--set", str(set_path.relative_to(root)), "--baseline", MOCK_BASELINE if o.mock else str(o.baseline),
        "--targets", ",".join(cand), "--out", "results", "--profiles-dir", "demo/profiles" if o.mock else "profiles",
        "--seed", "7", *prices,
    ]  # fmt: skip
    if judge:
        args += ["--judge", judge]
    step("eval", args, (), full=True)

    traces = root / "traces" / "traces.jsonl"
    cost_args = ["cost", "analyze", str(traces.relative_to(root)), "--out", "results"]
    if not traces.is_file():
        step("cost", ["cost", "demo", "--out", "demo/traces"], ("wrote",))
        cost_args = [
            "cost",
            "analyze",
            "demo/traces/traces.jsonl",
            "--counts",
            "demo/traces/counts.jsonl",
            "--out",
            "results",
        ]
        o.echo(
            "         (cost forensics runs on SYNTHETIC traces; import yours with `glmkit traces import` into traces/traces.jsonl)"
        )
    step("cost", cost_args, ("verdict:", "results:"))

    dep = root / "deployments" / "zai-api.yaml"
    if not dep.is_file() and "evidence" not in o.skip:
        _run(o, ["evidence", "examples", "--out", "deployments"], env)
    step(
        "evidence",
        ["evidence", "build", "--deployment", "deployments/zai-api.yaml", "--results", "results", "--out", "evidence"],
        ("verdict", "pack", "wrote"),
    )
    step("report", ["report", "build", "results", "--out", "site"], ("built", "wrote", "site", "tabs"), ok_codes=(0,))

    o.echo(f"dashboard: {root / 'site' / 'index.html'}  (open it; the Engagement tab shows the checklist)")
    o.echo(
        f"mode that ran: data {'/'.join(sorted({i['mode'] for i in infos}))}; targets {'mock' if o.mock else 'real'}; {time.monotonic() - t0:.1f}s total"
    )
    return 1 if failed else 0
