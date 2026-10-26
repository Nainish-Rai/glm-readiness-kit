"""``glmkit conform k2vv``: replay a K2VV / Prism JSONL corpus against a target and report trigger F1 and schema
accuracy (comparable with Moonshot's verifier). See ``glmkit.conform.k2vv`` for the corpus format."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer


def register(app: typer.Typer) -> None:
    @app.command("k2vv")
    def k2vv_command(
        corpus: Annotated[Path, typer.Argument(help="K2VV / Prism JSONL: one chat-completions request per line")],
        target: Annotated[str, typer.Option("--target", "-t", help="Profile name or path to a profile YAML")],
        reference: Annotated[
            Path | None,
            typer.Option("--reference", help="K2VV results.jsonl of the official API (finish_reason per line)"),
        ] = None,
        limit: Annotated[int | None, typer.Option(help="Replay only the first N lines")] = None,
        stream: Annotated[bool, typer.Option(help="Replay with stream=true")] = False,
        max_tokens: Annotated[int, typer.Option(help="max_tokens when a line sets none")] = 512,
        mock_defect: Annotated[
            list[str] | None, typer.Option("--mock-defect", "-D", help="Plant a mock defect")
        ] = None,
        out: Annotated[Path, typer.Option("--out", "-o", help="Results folder")] = Path("results"),
        timeout: Annotated[float, typer.Option(help="Per-request timeout (s)")] = 60.0,
        json_out: Annotated[bool, typer.Option("--json", help="Print the scores as JSON")] = False,
    ) -> None:
        """Replay CORPUS against TARGET; print tool-call trigger F1 and schema accuracy, write a result envelope."""
        from glmkit.client import GLMClient, MissingKeyError
        from glmkit.conform import k2vv
        from glmkit.conform.runner import COMPONENT, resolve_target
        from glmkit.contracts.envelope import CheckResult, ItemResult, ResultEnvelope, TargetInfo, new_run_id
        from glmkit.contracts.profile import get_profile
        from glmkit.redact import Redactor
        from glmkit.results import write_envelope

        try:
            profile = get_profile(target)
            samples = k2vv.load_corpus(
                k2vv.resolve_corpus(corpus),
                limit=limit,
                reference=k2vv.resolve_corpus(reference) if reference else None,
            )
        except (KeyError, k2vv.CorpusError, OSError) as e:
            typer.echo(f"error: {e.args[0] if e.args else e}", err=True)
            raise typer.Exit(2) from e
        if not any(s.reference for s in samples):
            typer.echo("error: no reference finish reasons (add a `reference` per line or pass --reference)", err=True)
            raise typer.Exit(2)

        async def go() -> ResultEnvelope:
            started = datetime.now(UTC)
            env = ResultEnvelope(
                run_id=new_run_id(COMPONENT, started),
                component=COMPONENT,
                started_at=started,
                config={"corpus": str(corpus), "limit": limit, "stream": stream, "mock_defects": mock_defect or []},
            )
            redactor = Redactor([profile.api_key()])
            with resolve_target(profile, list(mock_defect or [])) as (tgt, headers):
                env.targets = [TargetInfo.from_profile(tgt)]
                t0 = time.perf_counter()
                async with GLMClient(tgt, timeout_s=timeout, extra_headers=headers, redactor=redactor) as client:
                    exchanges = await k2vv.replay(client, samples, stream=stream, max_tokens=max_tokens)
            scores = k2vv.score(exchanges) or {}
            ok = bool(scores) and scores["request_errors"] == 0
            env.items.append(
                ItemResult(
                    id="K2VV-REPLAY",
                    group="K2VV-REPLAY",
                    title=f"K2VV replay of {Path(corpus).name}: trigger F1 and schema accuracy",
                    kind="conformance_case",
                    category="tools/trigger-f1",
                    severity="info",
                    target=tgt.name,
                    status="pass" if ok else "error",
                    reason=None if ok else f"{scores.get('request_errors', '?')} requests failed",
                    labels={
                        "mode": "stream" if stream else "nonstream",
                        "protocol": tgt.protocol,
                        "endpoint_class": tgt.endpoint_class,
                        "kind": "statistical",
                    },
                    checks=[
                        CheckResult(
                            name="k2vv_scores",
                            status="pass" if ok else "error",
                            message=f"F1 {scores.get('f1')}, schema accuracy {scores.get('schema_accuracy')}",
                            observed=scores,
                        )
                    ],
                    metrics={"attempts": len(samples), "pass_rate": scores.get("f1")},
                    evidence=exchanges[:5] + [e for e in exchanges[5:] if not e.normalized.ok],
                    duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                    data=scores,
                )
            )
            env.data = {"k2vv": scores}
            env.finalize()
            write_envelope(env, out, redactor)
            return env

        try:
            env = asyncio.run(go())
        except MissingKeyError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(2) from e
        scores = env.data["k2vv"]
        if json_out:
            typer.echo(json.dumps(scores, indent=2))
        else:
            typer.echo(f"replayed {scores.get('n', 0)} requests on {env.targets[0].profile}")
            p, r = scores["precision"], scores["recall"]
            cm = f"TP {scores['tp']} FP {scores['fp']} FN {scores['fn']} TN {scores['tn']}"
            typer.echo(f"tool-call trigger F1 {scores['f1']:.3f}  (precision {p:.3f}, recall {r:.3f}; {cm})")
            acc, n_calls = scores["schema_accuracy"], scores["tool_call_responses"]
            typer.echo(f"schema accuracy   {acc:.3f}  ({n_calls} tool-call responses)")
            typer.echo(f"result: {out / env.run_id / 'result.json'}")
