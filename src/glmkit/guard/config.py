"""Guard configuration: budgets, loop detector, policy, audit log. Plain YAML, every field optional."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Budget(BaseModel):
    """Soft = warn (fix event + audit entry), hard = block further requests. ``None`` = no limit."""

    model_config = ConfigDict(extra="forbid")
    soft_usd: float | None = None
    hard_usd: float | None = None
    soft_tokens: int | None = None
    hard_tokens: int | None = None

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.soft_usd, self.hard_usd, self.soft_tokens, self.hard_tokens))


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    repeat_threshold: int = Field(3, ge=2, description="N identical failing calls in a row trigger the action")
    action: Literal["inject", "block", "inject_then_block"] = "inject_then_block"
    escalate_after: int = Field(2, ge=1, description="inject_then_block: extra repeats tolerated after the message")
    similarity: float = Field(0.9, ge=0.5, le=1.0, description="argument similarity that counts as 'near-identical'")


class GuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_budget: Budget = Field(default_factory=Budget)
    key_budget: Budget = Field(default_factory=Budget)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    policy: str | None = Field(None, description="Policy YAML path; default: the shipped policy")
    policy_enabled: bool = True
    ask_action: Literal["block", "allow"] = Field(
        "block", description="The bridge cannot prompt a human: block (fail closed) or allow-and-log 'ask' rules"
    )
    audit_path: str | None = Field(
        None, description="Audit log (JSONL); default $GLMKIT_GUARD_AUDIT or ~/.glmkit/guard"
    )
    log_args: bool = Field(False, description="Store full tool arguments in the audit log (default: sha256 only)")
    price_host: str = "zai-api"
    agent: str | None = Field(None, description="Agent name recorded when the client does not send x-glmkit-agent")

    @classmethod
    def load(cls, path: Path | str | None) -> GuardConfig:
        if not path:
            return cls()
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)
