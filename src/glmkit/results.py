"""Writing and reading result envelopes. Every component uses these so the report builder finds its runs.

Layout: ``<results>/<run_id>/result.json`` (the envelope) plus optional artifacts next to it
(``traces.jsonl``, ``run.log`` ...), listed in ``envelope.artifacts``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from glmkit.contracts.envelope import Artifact, ResultEnvelope
from glmkit.redact import RedactingFilter, Redactor

RESULT_FILE = "result.json"
log = logging.getLogger("glmkit")


def run_dir(out_dir: Path, run_id: str) -> Path:
    d = out_dir / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_envelope(env: ResultEnvelope, out_dir: Path, redactor: Redactor | None = None) -> Path:
    """Scrub every string with ``redactor`` and write ``<out_dir>/<run_id>/result.json``."""
    data: Any = env.model_dump(mode="json")
    if redactor is not None:
        data = redactor.scrub(data)
    path = run_dir(out_dir, env.run_id) / RESULT_FILE
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_jsonl(lines: list[str], path: Path, redactor: Redactor | None = None) -> None:
    text = "".join(line + "\n" for line in lines)
    path.write_text(redactor.text(text) if redactor else text, encoding="utf-8")


def add_artifact(env: ResultEnvelope, kind: str, path: Path, base: Path) -> None:
    env.artifacts.append(Artifact(kind=kind, path=path.relative_to(base).as_posix()))


def load_envelopes(results: Path) -> tuple[list[tuple[Path, ResultEnvelope]], list[str]]:
    """Every valid envelope under ``results`` (recursive ``*.json``), newest first, plus warnings for files
    that look like envelopes but fail validation."""
    found: list[tuple[Path, ResultEnvelope]] = []
    warnings: list[str] = []
    files = [results] if results.is_file() else sorted(results.rglob("*.json"))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "run_id" not in data or "schema_version" not in data:
            continue
        try:
            found.append((f, ResultEnvelope.model_validate(data)))
        except ValidationError as e:
            warnings.append(f"{f}: not a valid result envelope ({e.error_count()} errors)")
    found.sort(key=lambda pe: pe[1].started_at, reverse=True)
    return found, warnings


@contextmanager
def run_log(path: Path, redactor: Redactor) -> Iterator[logging.Logger]:
    """Attach a redacting file handler for the duration of a run (``<run_dir>/run.log``)."""
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter(redactor))
    prev = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield log
    finally:
        log.removeHandler(handler)
        log.setLevel(prev)
        handler.close()
