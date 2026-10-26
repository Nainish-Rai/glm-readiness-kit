"""Shared builders: a defective mock and the Anthropic-path bridge in front of it, both on real sockets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from glmkit.bridge.anthropic import AnthropicOptions, create_anthropic_app
from glmkit.bridge.core import get_upstream
from glmkit.bridge.proxy import ProxyConfig
from glmkit.bridge.server import BridgeServer
from glmkit.contracts.profile import get_profile
from glmkit.mock import MockServer

ALL_DEFECTS = [
    "bridge-anthropic-zero-start-usage",
    "bridge-anthropic-array-input-500",
    "bridge-anthropic-missing-max-tokens-500",
    "bridge-anthropic-inflated-count",
]
UPSTREAMS = {
    "anthropic": ("mock-anthropic", "/api/anthropic"),  # Z.ai's Anthropic-compatible endpoint
    "openai": ("mock", "/api/paas/v4"),  # any OpenAI-compatible GLM host
}


def build_bridge(
    mock: MockServer,
    kind: str,
    out: Path,
    *,
    flavor: str = "zai",
    options: AnthropicOptions | None = None,
    log_content: bool = False,
    middleware: list[Any] | None = None,
) -> tuple[BridgeServer, Any]:
    profile_name, path = UPSTREAMS[kind]
    profile = get_profile(profile_name).model_copy(update={"base_url": mock.url + path, "spawn_mock": False})
    app = create_anthropic_app(
        ProxyConfig(
            profile=profile,
            upstream=get_upstream(flavor),
            out_dir=out,
            flush_interval_s=0.2,
            log_content=log_content,
            middleware=middleware or [],
        ),
        options=options,
    )
    return BridgeServer(app).start(), app
