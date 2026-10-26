"""Upstream behaviour as data. A rewriter never branches on a provider name; it reads an ``UpstreamProfile``.

Not to be confused with ``glmkit.contracts.profile.EndpointProfile`` (where to connect). ``upstream_for_endpoint``
picks the behaviour profile for an endpoint profile; ``--flavor`` overrides it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ReplayMode = Literal["verbatim", "rename", "strip"]
ThinkingPolicy = Literal["to_low", "passthrough"]
EffortPolicy = Literal["clamp", "passthrough"]

# Client-side spellings of "give me no thinking" (Z.ai accepts type in {disabled, false, none, off} on plan).
DISABLED_VALUES = frozenset({"disabled", "false", "none", "off"})
# Names under which reasoning text travels in assistant messages and responses.
REASONING_KEYS = ("reasoning_content", "reasoning")


@dataclass(frozen=True)
class UpstreamProfile:
    name: str
    title: str = ""
    # thinking: models matching any regex force thinking; "to_low" rewrites a disable request
    forced_thinking_models: tuple[str, ...] = ()
    disabled_thinking: ThinkingPolicy = "passthrough"
    # reasoning_effort: models matching a key in ``effort_allowed`` accept only those values
    effort_policy: EffortPolicy = "passthrough"
    effort_models: tuple[str, ...] = ()
    effort_allowed: tuple[str, ...] = ()
    effort_map: dict[str, str] = field(default_factory=dict)  # out-of-range value -> allowed value
    effort_default: str = "max"  # unknown strings
    # reasoning replay: how assistant-message reasoning must be sent upstream
    reasoning_replay: ReplayMode = "verbatim"
    reasoning_field: str = "reasoning_content"  # target name for "verbatim" / "rename"
    # replayed reasoning counts as prompt tokens by default (Coding Plan: preserved thinking on; standard API: cleared)
    preserved_thinking: bool = False
    # schemas
    flatten_oneof_tools: bool = True
    notes: str = ""

    def matches(self, patterns: tuple[str, ...], model: str | None) -> bool:
        base = re.sub(r"\[[^\]]*\]$", "", (model or "").rsplit("/", 1)[-1]).lower()
        return any(re.fullmatch(p, base) for p in patterns)

    def forces_thinking(self, model: str | None) -> bool:
        return self.disabled_thinking == "to_low" and self.matches(self.forced_thinking_models, model)

    def clamps_effort(self, model: str | None) -> bool:
        return self.effort_policy == "clamp" and self.matches(self.effort_models, model)


_EFFORT_MAP = {"none": "low", "minimal": "low", "medium": "high", "xhigh": "max"}
_FORCED = (r"glm-5\.3.*", r"glm-4\.7.*")

UPSTREAMS: dict[str, UpstreamProfile] = {
    p.name: p
    for p in (
        UpstreamProfile(
            name="zai",
            title="Z.ai API (pay-as-you-go): forced thinking, effort enum low|high|max",
            forced_thinking_models=_FORCED,
            disabled_thinking="to_low",
            effort_policy="clamp",
            effort_models=(r"glm-5\.3.*",),
            effort_allowed=("low", "high", "max"),
            effort_map=_EFFORT_MAP,
            notes="research/01-conformance.md 2.3-2.5; 1210 on disabled thinking, 1214 on other effort values",
        ),
        UpstreamProfile(
            name="zai-plan",
            title="Z.ai Coding Plan: the host maps thinking/effort itself, so the bridge leaves them alone",
            forced_thinking_models=_FORCED,
            disabled_thinking="passthrough",
            effort_policy="passthrough",
            preserved_thinking=True,
            notes="devpack docs: thinking.type false|disabled|none|off -> low; effort mapped server-side",
        ),
        UpstreamProfile(
            name="cerebras",
            title="Cerebras: rejects reasoning_content in request messages",
            reasoning_replay="strip",
            notes="research/02-bridge.md 6.3: omit reasoning_content entirely for Cerebras",
        ),
        UpstreamProfile(
            name="generic",
            title="Generic OpenAI-compatible host: reasoning_content passed verbatim, nothing else assumed",
            flatten_oneof_tools=False,
        ),
    )
}


def get_upstream(name: str) -> UpstreamProfile:
    if name not in UPSTREAMS:
        raise KeyError(f"unknown upstream flavor {name!r}; known: {', '.join(UPSTREAMS)}")
    return UPSTREAMS[name]


def upstream_for_endpoint(provider: str, endpoint_class: str, base_url: str = "") -> UpstreamProfile:
    """Default behaviour profile for an endpoint profile (``--flavor`` overrides)."""
    if provider == "cerebras" or "cerebras" in base_url:
        return UPSTREAMS["cerebras"]
    if provider in ("zai", "mock"):
        return UPSTREAMS["zai-plan" if endpoint_class == "plan" else "zai"]
    return UPSTREAMS["generic"]
