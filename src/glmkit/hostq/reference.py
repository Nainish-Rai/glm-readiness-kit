"""Stored reference fingerprint, and the ambient session a host-quality run uses to find it.

A reference is recorded from a trusted target (the official API, or a self-hosted FP8 deployment) and holds
the action and text samples, the greedy self-agreement and the canary scores. ``reference_id`` hashes the content
and the probe-set ids, so changing a probe set (or the data) changes the id; comparing against a reference
recorded with different probe sets is refused.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from glmkit import __version__
from glmkit.hostq.probes import digest, probe_set_ids

REFERENCE_SCHEMA = "glmkit.hostq.reference/1"
DEFAULT_SEED = 20261026


class ReferenceFileError(ValueError):
    """A reference file is unreadable or was recorded with different probe sets."""


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: str = Field(default=REFERENCE_SCHEMA, alias="schema")
    reference_id: str = ""
    kit_version: str = __version__
    recorded_at: datetime | None = None
    label: str = ""
    target: dict[str, Any] = Field(default_factory=dict)
    seed: int = DEFAULT_SEED
    probe_sets: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)  # {"samples": [[prompt_id, value], ...]}
    text: dict[str, Any] = Field(default_factory=dict)
    self_agreement: dict[str, Any] = Field(default_factory=dict)
    canary: dict[str, Any] = Field(default_factory=dict)

    def content_id(self) -> str:
        body = {
            "probe_sets": self.probe_sets,
            "target": {k: self.target.get(k) for k in ("profile", "model", "endpoint_class")},
            "action": self.action,
            "text": self.text,
            "self_agreement": self.self_agreement,
            "canary": {k: v for k, v in self.canary.items() if k != "unscored"},
        }
        return digest(body, 16)

    def seal(self) -> Reference:
        self.reference_id = self.content_id()
        return self

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(mode="json", by_alias=True), indent=1, ensure_ascii=False) + "\n")
        return path

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.reference_id,
            "label": self.label,
            "profile": self.target.get("profile"),
            "model": self.target.get("model"),
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "probe_sets": self.probe_sets,
            "n_action": len(self.action.get("samples", [])),
            "n_text": len(self.text.get("samples", [])),
            "canary": {"n": self.canary.get("n"), "correct": self.canary.get("correct")},
        }


def load_reference(path: Path | str, *, check_probes: bool = True) -> Reference:
    p = Path(path)
    try:
        ref = Reference.model_validate(json.loads(p.read_text()))
    except (OSError, ValueError) as e:
        raise ReferenceFileError(f"cannot read reference {p}: {e}") from e
    if ref.reference_id != ref.content_id():
        raise ReferenceFileError(f"reference {p} was edited: id {ref.reference_id} != content {ref.content_id()}")
    if check_probes:
        now = probe_set_ids()
        stale = {k: (v, now.get(k)) for k, v in ref.probe_sets.items() if now.get(k) != v}
        if stale:
            detail = ", ".join(f"{k}: recorded {a}, current {b}" for k, (a, b) in stale.items())
            raise ReferenceFileError(
                f"reference {ref.reference_id} was recorded with other probe sets ({detail}); re-record"
            )
    return ref


def new_reference(label: str, target: dict[str, Any], config: dict[str, Any], seed: int = DEFAULT_SEED) -> Reference:
    return Reference(
        label=label,
        target=target,
        seed=seed,
        recorded_at=datetime.now(UTC),
        probe_sets=probe_set_ids(),
        config=config,
    )


# ------------------------------------------------------------------ ambient session


@dataclass
class HostqSession:
    """What checks may use besides the exchanges: the reference to compare against, and a bag in which
    they leave their raw samples (``glmkit conform reference record`` builds a reference from it)."""

    reference: Reference | None = None
    collected: dict[str, Any] = field(default_factory=dict)


_SESSION: ContextVar[HostqSession | None] = ContextVar("glmkit_hostq_session", default=None)


def current_session() -> HostqSession:
    return _SESSION.get() or HostqSession()


@contextlib.contextmanager
def use_session(session: HostqSession) -> Iterator[HostqSession]:
    token = _SESSION.set(session)
    try:
        yield session
    finally:
        _SESSION.reset(token)
