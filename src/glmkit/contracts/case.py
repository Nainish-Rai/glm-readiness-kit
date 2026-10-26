"""Conformance case file format (``cases/<category>/<ID>-slug.yaml``), from research/01-conformance.md §6.

Cases are data. Oracles are named checks implemented once in check modules (``glmkit.conform.checks``);
a case only names them and passes parameters. How a case is executed (one request, a multi-turn loop, a
latency probe ...) is chosen by ``executor`` (default ``single``), so new case shapes need a new executor
module, not a runner change.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from glmkit.contracts.envelope import Severity

CASE_SCHEMA_VERSION = 1
EXPECT_KEYS = ("default", "api", "plan", "third_party", "self_hosted")
Mode = Literal["nonstream", "stream"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckSpec(BaseModel):
    """``{check: <name>, <params...>, soft?: bool, when?: <CheckSpec>}``. Params are check-specific."""

    model_config = ConfigDict(extra="allow")

    check: str
    soft: bool = Field(default=False, description="A failure is recorded as soft_fail and does not fail the run")
    when: CheckSpec | None = Field(default=None, description="Precondition; the check is skipped unless it passes")

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class AppliesTo(_Strict):
    models: list[str] | None = Field(default=None, description="None = any model")
    protocols: list[Literal["openai_chat", "anthropic_messages", "responses"]] | None = None
    endpoint_classes: list[Literal["api", "plan", "third_party", "self_hosted"]] | None = None
    harness_profiles: list[str] | None = None


class Source(_Strict):
    issues: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    spec: list[str] = Field(default_factory=list, description="Doc URLs used as the oracle")
    first_seen: date | None = None


class CaseRequest(_Strict):
    """Canonical request, OpenAI-chat flavoured; the client adapters translate it per protocol."""

    model: str | None = Field(default=None, description="Default: the target profile's model")
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] | None = Field(
        default=None,
        description="OpenAI {type: function, function: {...}} or shorthand {name, description, parameters}",
    )
    tool_choice: Any = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | str | None = None
    params: dict[str, Any] = Field(default_factory=dict, description="Merged verbatim into the body (thinking ...)")
    overrides: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Per-protocol deep-merge applied last: {anthropic_messages: {...}}"
    )
    omit: list[str] = Field(default_factory=list, description="Top-level body keys to delete after translation")


class Variant(_Strict):
    id: str
    request: dict[str, Any] = Field(default_factory=dict, description="Deep-merged into the case request")
    expect: dict[str, list[CheckSpec]] | None = Field(default=None, description="Replaces the case expect")
    expect_class: str | None = Field(default=None, description="Free label, e.g. 'control'")


class ConformanceCase(_Strict):
    schema_version: Literal[1] = CASE_SCHEMA_VERSION
    id: str = Field(pattern=r"^[A-Z]+-[0-9]{3}[A-Za-z0-9-]*$")
    title: str
    category: str
    severity: Severity
    kind: Literal["deterministic", "statistical", "soak", "fingerprint"] = "deterministic"
    executor: str = Field(default="single", description="Registered executor name")
    executor_config: dict[str, Any] = Field(default_factory=dict, description="Free-form executor parameters")
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    source: Source = Field(default_factory=Source)
    modes: list[Mode] = Field(default_factory=lambda: ["nonstream"])
    repeats: int = Field(default=1, ge=1)
    request: CaseRequest = Field(default_factory=CaseRequest)
    variants: list[Variant] = Field(default_factory=list)
    expect: dict[str, list[CheckSpec]] = Field(description="Check lists by endpoint class; 'default' always applies")
    tolerance: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    opt_in: bool = Field(default=False, description="Only runs when selected explicitly (soak, costly cases)")
    notes: str = ""

    @field_validator("expect")
    @classmethod
    def _expect_keys(cls, v: dict[str, list[CheckSpec]]) -> dict[str, list[CheckSpec]]:
        bad = set(v) - set(EXPECT_KEYS)
        if bad:
            raise ValueError(f"unknown expect keys {sorted(bad)}; allowed: {EXPECT_KEYS}")
        return v

    def checks_for(self, endpoint_class: str, variant: Variant | None = None) -> list[CheckSpec]:
        """``default`` checks plus the endpoint class's checks (a variant's expect replaces the case's)."""
        expect = variant.expect if variant and variant.expect is not None else self.expect
        return list(expect.get("default", [])) + list(expect.get(endpoint_class, []))
