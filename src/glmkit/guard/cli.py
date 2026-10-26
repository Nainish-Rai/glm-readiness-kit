"""``glmkit guard``: verify the audit chain, check a command against the policy, list rules, run the hook."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Guard: budgets, loop detector, destructive-command policy, audit log.", no_args_is_help=True)

LogArg = Annotated[
    Path | None, typer.Argument(help="Audit log (default: $GLMKIT_GUARD_AUDIT or ~/.glmkit/guard/audit.jsonl)")
]


@app.command()
def verify(
    log: LogArg = None,
    head: Annotated[
        str | None, typer.Option(help="Anchored head hash to compare with (detects tail truncation)")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON")] = False,
) -> None:
    """Re-derive the hash chain. Exit 0 when intact, 1 when any entry was edited, deleted, inserted or reordered."""
    from glmkit.guard.audit import default_audit_path
    from glmkit.guard.audit import verify as run_verify

    res = run_verify(log or default_audit_path(), expect_head=head)
    if as_json:
        typer.echo(json.dumps(res.to_dict(), indent=2))
    elif res.ok:
        typer.echo(f"OK  {res.entries} entries, head {res.head}  ({res.path})")
    else:
        typer.echo(f"FAIL  {res.error}  ({res.path}; {res.entries} entries verified before the break)", err=True)
    raise typer.Exit(0 if res.ok else 1)


@app.command()
def check(
    command: Annotated[str | None, typer.Option("--command", "-c", help="A shell command to check")] = None,
    tool: Annotated[str, typer.Option(help="Tool name")] = "bash",
    args: Annotated[str | None, typer.Option(help="Tool arguments as JSON (instead of --command)")] = None,
    policy: Annotated[Path | None, typer.Option(help="Policy YAML (default: shipped policy)")] = None,
) -> None:
    """Evaluate the policy on one tool call. Exit 0 allow, 3 ask, 2 deny. Nothing is written to the audit log."""
    from glmkit.guard.policy import load_policy

    if (command is None) == (args is None):
        raise typer.BadParameter("give exactly one of --command / --args")
    payload = {"command": command} if command is not None else json.loads(args or "{}")
    d = load_policy(policy).evaluate(tool, payload)
    typer.echo(f"{d.action}" + (f"  rule={d.rule}  {d.reason}" if d.rule else ""))
    raise typer.Exit({"allow": 0, "ask": 3, "deny": 2}[d.action])


@app.command()
def policy(file: Annotated[Path | None, typer.Option(help="Policy YAML (default: shipped policy)")] = None) -> None:
    """List the rules in force."""
    from glmkit.guard.policy import load_policy

    p = load_policy(file)
    typer.echo(f"policy: {p.source}  ({len(p.rules)} rules)")
    for r in p.rules:
        typer.echo(f"  {r.action:5} {r.kind:8} {r.id:30} {r.description}")


@app.command()
def log(
    path: LogArg = None,
    tail: Annotated[int, typer.Option(help="Show the last N entries")] = 20,
) -> None:
    """Print recent audit entries (tool, decision, rule, reason; never arguments)."""
    from glmkit.guard.audit import default_audit_path, read_entries

    p = path or default_audit_path()
    if not p.exists():
        raise typer.BadParameter(f"{p} does not exist")
    for e in read_entries(p)[-tail:]:
        typer.echo(
            f"{e.get('seq'):>5} {e.get('ts', '')[11:23]} {e.get('source', ''):16} {e.get('decision', ''):12} "
            f"{e.get('tool') or '-':16} {e.get('rule') or '-':26} {e.get('session') or '-'}"
        )


@app.command()
def hook() -> None:
    """Run the Claude Code PreToolUse hook (reads the event JSON on stdin). See docs/guard.md."""
    from glmkit.guard.hook import main

    raise typer.Exit(main())
