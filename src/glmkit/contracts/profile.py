"""Endpoint profile: one YAML file per target (a host + protocol + model + where its key lives).

Profiles never hold secrets. ``api_key_env`` names the environment variable that holds the key; the key
value is read at request time and is never written to results or logs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from glmkit.paths import profile_dirs

Protocol = Literal["openai_chat", "anthropic_messages"]
EndpointClass = Literal["api", "plan", "third_party", "self_hosted"]

PROTOCOL_PATHS: dict[str, str] = {"openai_chat": "/chat/completions", "anthropic_messages": "/v1/messages"}
MOCK_PLACEHOLDER = "{mock}"
_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class EndpointProfile(BaseModel):
    """A conformance / eval / bridge target."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Unique profile id, used as --target")
    title: str = ""
    provider: str = Field(description="zai | openrouter | vllm | sglang | generic | mock | ...")
    base_url: str = Field(
        description="Base URL; the protocol path is appended. '{mock}' is replaced by an in-process mock URL. "
        "${ENV:-default} references are expanded at load time (URLs only, never keys)."
    )
    protocol: Protocol
    endpoint_class: EndpointClass
    model: str
    model_family: str | None = Field(
        default=None, description="Canonical GLM model id for case applicability (default: derived from model)"
    )
    api_key_env: str | None = Field(default=None, description="Name of the env var holding the key")
    api_key_required: bool = True
    auth_header: Literal["bearer", "x-api-key"] | None = Field(
        default=None, description="Default: bearer for openai_chat, x-api-key for anthropic_messages"
    )
    headers: dict[str, str] = Field(default_factory=dict, description="Extra non-secret request headers")
    path: str | None = Field(default=None, description="Override the protocol path appended to base_url")
    spawn_mock: bool = Field(default=False, description="Start the mock GLM server in-process for this target")
    notes: str = ""
    sources: list[str] = Field(default_factory=list)

    @field_validator("headers")
    @classmethod
    def _no_secret_headers(cls, v: dict[str, str]) -> dict[str, str]:
        for k in v:
            if k.lower() in {"authorization", "x-api-key", "api-key"}:
                raise ValueError(f"header {k!r} carries a secret; use api_key_env instead")
        return v

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + (self.path or PROTOCOL_PATHS[self.protocol])

    @property
    def auth_style(self) -> str:
        return self.auth_header or ("bearer" if self.protocol == "openai_chat" else "x-api-key")

    @property
    def family(self) -> str:
        """'z-ai/glm-5.3' -> 'glm-5.3'; 'glm-5.3[1m]' -> 'glm-5.3'. Override with model_family."""
        if self.model_family:
            return self.model_family
        return re.sub(r"\[[^\]]*\]$", "", self.model.rsplit("/", 1)[-1]).lower()

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    def with_base_url(self, base_url: str) -> EndpointProfile:
        return self.model_copy(update={"base_url": base_url})


def _expand_env(value: str) -> str:
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)


def load_profile_file(path: Path) -> EndpointProfile:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data.get("base_url"), str):
        data["base_url"] = _expand_env(data["base_url"])
    return EndpointProfile.model_validate(data)


def load_profiles(extra_dirs: list[Path] | None = None) -> dict[str, EndpointProfile]:
    """All profiles by name. Later folders override earlier ones (see ``glmkit.paths.profile_dirs``)."""
    out: dict[str, EndpointProfile] = {}
    for d in profile_dirs() + list(extra_dirs or []):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            p = load_profile_file(f)
            out[p.name] = p
    return out


def get_profile(name_or_path: str) -> EndpointProfile:
    """Resolve ``--target``: a profile name or a path to a profile YAML file."""
    p = Path(name_or_path)
    if p.suffix in {".yaml", ".yml"} and p.is_file():
        return load_profile_file(p)
    profiles = load_profiles()
    if name_or_path not in profiles:
        raise KeyError(f"unknown target profile {name_or_path!r}; known: {', '.join(sorted(profiles))}")
    return profiles[name_or_path]
