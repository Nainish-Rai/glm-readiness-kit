"""Result envelope: one JSON document per run, written by every component (conform, bridge, eval, ...).

Later slices may only add *optional* fields here; component-specific payloads go in ``data`` (envelope level)
and ``ItemResult.data`` (item level), which need no schema change.
"""

from __future__ import annotations

import platform
import secrets
import sys
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from glmkit import __version__
from glmkit.contracts.evidence import Exchange
from glmkit.contracts.profile import EndpointClass, EndpointProfile, Protocol

ENVELOPE_SCHEMA_VERSION = 1

ItemStatus = Literal["pass", "fail", "soft_fail", "skip", "error", "throttled"]
Verdict = Literal["pass", "warn", "fail", "error", "incomplete"]
Severity = Literal["critical", "major", "minor", "info"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetInfo(_Strict):
    """A target as recorded in results. Never holds a key: only the env var name and whether it was set."""

    profile: str
    title: str = ""
    provider: str
    base_url: str
    url: str
    protocol: Protocol
    endpoint_class: EndpointClass
    model: str
    api_key_env: str | None = None
    key_present: bool = False

    @classmethod
    def from_profile(cls, p: EndpointProfile) -> TargetInfo:
        return cls(
            profile=p.name,
            title=p.title,
            provider=p.provider,
            base_url=p.base_url,
            url=p.url,
            protocol=p.protocol,
            endpoint_class=p.endpoint_class,
            model=p.model,
            api_key_env=p.api_key_env,
            key_present=bool(p.api_key()),
        )


class CheckResult(_Strict):
    name: str
    status: Literal["pass", "fail", "soft_fail", "skip", "error"]
    message: str = ""
    expected: Any = None
    observed: Any = None
    soft: bool = False
    attempt: int | None = Field(default=None, description="Attempt index; None for aggregate checks")


class ItemResult(_Strict):
    id: str = Field(description="Unique within the envelope, e.g. 'WIRE-001[nonstream]'")
    group: str | None = Field(default=None, description="Grouping key, e.g. the case id 'WIRE-001'")
    title: str = ""
    kind: str = Field(default="item", description="conformance_case | fix_event | eval_item | ...")
    category: str | None = None
    severity: Severity | None = None
    target: str | None = Field(default=None, description="Target profile name this item ran against")
    labels: dict[str, str] = Field(default_factory=dict, description="mode, variant, protocol ...")
    status: ItemStatus
    reason: str | None = Field(default=None, description="Why skipped/errored, or a one-line failure summary")
    checks: list[CheckResult] = Field(default_factory=list)
    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    evidence: list[Exchange] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict, description="Provenance (issue URLs, doc URLs)")
    duration_ms: float | None = None
    data: dict[str, Any] = Field(default_factory=dict, description="Component-specific payload")


class Summary(_Strict):
    total: int = 0
    passed: int = 0
    failed: int = 0
    soft_failed: int = 0
    skipped: int = 0
    errored: int = 0
    throttled: int = 0
    hard_gate_failures: int = Field(default=0, description="Failed items with severity critical")
    pass_rate: float | None = Field(default=None, description="passed / (total - skipped - throttled)")

    @classmethod
    def from_items(cls, items: list[ItemResult]) -> Summary:
        c = {s: 0 for s in ("pass", "fail", "soft_fail", "skip", "error", "throttled")}
        for it in items:
            c[it.status] += 1
        scored = len(items) - c["skip"] - c["throttled"]
        return cls(
            total=len(items),
            passed=c["pass"],
            failed=c["fail"],
            soft_failed=c["soft_fail"],
            skipped=c["skip"],
            errored=c["error"],
            throttled=c["throttled"],
            hard_gate_failures=sum(1 for i in items if i.status == "fail" and i.severity == "critical"),
            pass_rate=(c["pass"] / scored) if scored else None,
        )


def compute_verdict(items: list[ItemResult]) -> Verdict:
    """fail: any failed item that is critical/major/unrated. warn: minor/info failures, soft failures,
    errors or throttling. pass: something ran and everything passed. incomplete: nothing ran."""
    ran = [i for i in items if i.status != "skip"]
    if not ran:
        return "incomplete"
    if any(i.status == "fail" and i.severity in (None, "critical", "major") for i in ran):
        return "fail"
    if any(i.status in ("fail", "soft_fail", "error", "throttled") for i in ran):
        return "warn"
    return "pass"


def new_run_id(component: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{component}-{secrets.token_hex(3)}"


def environment_info() -> dict[str, str]:
    return {"python": sys.version.split()[0], "platform": platform.platform(), "glmkit": __version__}


class Artifact(_Strict):
    kind: str = Field(description="traces | log | junit | ...")
    path: str = Field(description="Path relative to the run directory")


class ResultEnvelope(_Strict):
    schema_version: Literal[1] = ENVELOPE_SCHEMA_VERSION
    run_id: str
    kit_version: str = __version__
    component: str = Field(description="conform | bridge | eval | guard | cost | deploy | evidence | ...")
    started_at: datetime
    finished_at: datetime | None = None
    targets: list[TargetInfo] = Field(default_factory=list)
    items: list[ItemResult] = Field(default_factory=list)
    summary: Summary = Field(default_factory=Summary)
    verdict: Verdict = "incomplete"
    config: dict[str, Any] = Field(default_factory=dict, description="Run options (no secrets)")
    environment: dict[str, str] = Field(default_factory=environment_info)
    artifacts: list[Artifact] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict, description="Component-specific payload")

    def finalize(self) -> ResultEnvelope:
        self.summary = Summary.from_items(self.items)
        self.verdict = compute_verdict(self.items)
        self.finished_at = self.finished_at or datetime.now(UTC)
        return self
