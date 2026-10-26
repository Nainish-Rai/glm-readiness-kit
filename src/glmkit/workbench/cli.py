# ruff: noqa: E501
"""``glmkit init``, ``glmkit status`` and ``glmkit demo ...`` (issue 13)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="Demo golden sets (licensed, fetched with checksums) and the one-command pipeline.", no_args_is_help=True
)
COMMAND = "demo"


def _workspace(path: Path | None) -> Path | None:
    from glmkit.workbench.workspace import find_workspace

    return find_workspace(path or Path.cwd())


def register_cli(root: typer.Typer) -> None:
    @root.command("init")
    def init(
        customer: Annotated[str, typer.Argument(help="Customer name; becomes the folder name")],
        parent: Annotated[Path, typer.Option("--dir", help="Where to create the folder")] = Path("."),
        force: Annotated[bool, typer.Option(help="Add missing files to an existing folder")] = False,
    ) -> None:
        """Scaffold an engagement workspace: brief, profiles, golden-set template, task list, 90-day README."""
        from glmkit.workbench.workspace import WorkspaceError, init_workspace

        try:
            ws = init_workspace(customer, parent, force=force)
        except WorkspaceError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(2) from e
        typer.echo(f"created {ws}")
        for p in sorted(ws.rglob("*")):
            if p.is_file():
                typer.echo(f"  {p.relative_to(ws)}")
        typer.echo(
            f"next: cd {ws} && glmkit demo run     (conformance, eval, cost, evidence and the dashboard, offline)"
        )

    @root.command("status")
    def status(
        workspace: Annotated[
            Path | None, typer.Option("--workspace", "-w", help="Workspace folder (default: nearest parent of cwd)")
        ] = None,
        as_json: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Which engagement steps are done, derived from the files and results in the workspace."""
        from glmkit.results import load_envelopes
        from glmkit.workbench.workspace import derive_state

        ws = _workspace(workspace)
        if ws is None:
            typer.echo(
                "error: not inside an engagement workspace (no engagement.yaml); run `glmkit init <customer>`", err=True
            )
            raise typer.Exit(2)
        envs = [e for _, e in load_envelopes(ws / "results")[0]] if (ws / "results").is_dir() else []
        st = derive_state(ws, envs)
        if as_json:
            typer.echo(
                json.dumps({"customer": st.customer, "steps": [s.__dict__ for s in st.steps]}, indent=1, default=str)
            )
            return
        typer.echo(f"{st.customer}: {st.done}/{len(st.steps)} steps done  ({ws})")
        for s in st.steps:
            typer.echo(f"  [{'x' if s.done else ' '}] {s.title:30} {s.week:10} {s.evidence if s.done else s.command}")


@app.command("list")
def list_sets(
    data_dir: Annotated[
        Path | None, typer.Option("--data-dir", help="Where fetched sets live (default: golden/)")
    ] = None,
) -> None:
    """The demo sets: task types, items, license, and whether the licensed data is fetched here."""
    from glmkit.workbench.cards import load_card, set_ids
    from glmkit.workbench.fetch import fetched

    d = data_dir or ((_workspace(None) or Path.cwd()) / "golden")
    typer.echo(f"{'set':12} {'items':>5}  {'license':12} {'task types':28} data here")
    for sid in set_ids():
        c = load_card(sid)
        rec = fetched(sid, d)
        typer.echo(
            f"{sid:12} {c.items:>5}  {c.license.spdx:12} {','.join(c.task_types):28} "
            + (f"fetched (rev {rec['revision'][:10]})" if rec else "not fetched (stand-in items ship with the kit)")
        )


@app.command("cards")
def cards(
    directory: Annotated[
        Path | None, typer.Option("--dir", help="Check the cards in this folder instead of the shipped ones")
    ] = None,
) -> None:
    """Validate dataset cards (license, verification date, attribution, pinned sha256). Exit 1 on any problem."""
    from glmkit.workbench.cards import card_files, check_cards, load_card

    bad = check_cards(directory)
    for b in bad:
        typer.echo(f"FAIL {b}")
    if bad:
        raise typer.Exit(1)
    for f in card_files(directory):
        c = load_card(f.stem, directory)
        typer.echo(
            f"ok   {c.id:12} {c.license.spdx:12} verified {c.license.verified_at}  rev {c.source.revision[:10]}  {len(c.source.files)} file(s) pinned"
        )


@app.command("fetch")
def fetch(
    which: Annotated[str, typer.Argument(help="Set id or 'all'")] = "all",
    dest: Annotated[Path | None, typer.Option("--dest", help="Download and output folder (default: golden/)")] = None,
) -> None:
    """Download the pinned files, verify sha256, build the golden set. Nothing is committed to the repo."""
    from glmkit.workbench.fetch import FetchError, fetch_set
    from glmkit.workbench.pipeline import DemoError, resolve_sets

    try:
        ids = resolve_sets(which)
    except DemoError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    d = dest or ((_workspace(None) or Path.cwd()) / "golden")
    code = 0
    for sid in ids:
        typer.echo(f"{sid}:")
        try:
            out = fetch_set(sid, d, log=typer.echo)
            typer.echo(f"  built {out} (see the dataset card: glmkit demo cards)")
        except (FetchError, RuntimeError) as e:
            typer.echo(f"  FAILED: {e}", err=True)
            code = 1
    raise typer.Exit(code)


@app.command("run")
def run(
    which: Annotated[str, typer.Argument(help="Set id or 'all'")] = "all",
    targets: Annotated[
        list[str] | None,
        typer.Option(
            "--targets", "-t", help="Real candidate profiles ('name' or 'name@effort'). Omit for the mock demo"
        ),
    ] = None,
    baseline: Annotated[str | None, typer.Option("--baseline", "-b", help="Incumbent profile (real targets)")] = None,
    judge: Annotated[
        str | None, typer.Option("--judge", help="Judge profile (real targets, CUAD explain items)")
    ] = None,
    standin: Annotated[
        bool, typer.Option("--standin", help="Use the bundled stand-in items even if fetched data exists")
    ] = False,
    data_dir: Annotated[
        Path | None, typer.Option("--data-dir", help="Where fetched sets live (default: golden/)")
    ] = None,
    skip: Annotated[
        list[str] | None, typer.Option("--skip", help="Skip a step: conform, eval, cost, evidence, report")
    ] = None,
    max_items: Annotated[int | None, typer.Option(help="Only the first N items of the merged set")] = None,
) -> None:
    """Conformance, eval, cost, evidence and the dashboard in one step. Offline on the mock when no --targets."""
    from glmkit.workbench.pipeline import STEPS, DemoError, DemoOptions, resolve_sets, run_demo
    from glmkit.workbench.workspace import find_workspace

    cwd = Path.cwd()
    root = find_workspace(cwd)
    if root is None:
        root = cwd
        typer.echo(
            f"note: no engagement.yaml found; using {cwd} as the workspace (glmkit init <customer> makes a proper one)"
        )
    flat = [s.strip() for raw in (targets or []) for s in raw.split(",") if s.strip()]
    skips = {s.strip() for raw in (skip or []) for s in raw.split(",")}
    if skips - set(STEPS):
        typer.echo(f"error: unknown step(s) {sorted(skips - set(STEPS))}; steps: {', '.join(STEPS)}", err=True)
        raise typer.Exit(2)
    try:
        opts = DemoOptions(
            root=root, sets=resolve_sets(which), data_dir=data_dir or root / "golden", standin=standin, targets=flat,
            baseline=baseline, judge=judge, skip=skips, max_items=max_items, echo=typer.echo,
        )  # fmt: skip
        code = run_demo(opts)
    except DemoError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    raise typer.Exit(code)
