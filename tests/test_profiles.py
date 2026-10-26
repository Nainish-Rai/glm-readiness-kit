from __future__ import annotations

import pytest
from pydantic import ValidationError

from glmkit.contracts.profile import EndpointProfile, get_profile, load_profiles

# base URLs and paths from research/01-conformance.md §2.2
EXPECTED = {
    "zai-api": ("https://api.z.ai/api/paas/v4/chat/completions", "openai_chat", "api", "ZAI_API_KEY"),
    "zai-plan": ("https://api.z.ai/api/coding/paas/v4/chat/completions", "openai_chat", "plan", "ZAI_CODING_PLAN_KEY"),
    "zai-api-anthropic": ("https://api.z.ai/api/anthropic/v1/messages", "anthropic_messages", "api", "ZAI_API_KEY"),
    "zai-plan-anthropic": (
        "https://api.z.ai/api/anthropic/v1/messages",
        "anthropic_messages",
        "plan",
        "ZAI_CODING_PLAN_KEY",
    ),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "openai_chat", "third_party", "OPENROUTER_API_KEY"),
}


def test_shipped_profiles() -> None:
    profiles = load_profiles()
    assert {
        "zai-api",
        "zai-plan",
        "zai-api-anthropic",
        "zai-plan-anthropic",
        "openrouter",
        "generic-openai",
        "vllm-local",
        "sglang-local",
        "mock",
        "mock-anthropic",
    } <= set(profiles)
    for name, (url, proto, cls, env) in EXPECTED.items():
        p = profiles[name]
        assert (p.url, p.protocol, p.endpoint_class, p.api_key_env) == (url, proto, cls, env)
    assert profiles["vllm-local"].endpoint_class == "self_hosted"
    assert profiles["vllm-local"].family == "glm-5.3"


def test_env_reference_in_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLMKIT_GENERIC_BASE_URL", "http://example.test/v1")
    assert get_profile("generic-openai").url == "http://example.test/v1/chat/completions"


def test_profiles_refuse_secret_headers() -> None:
    with pytest.raises(ValidationError, match="api_key_env"):
        EndpointProfile(
            name="x",
            provider="x",
            base_url="http://x",
            protocol="openai_chat",
            endpoint_class="api",
            model="glm-5.3",
            headers={"Authorization": "Bearer sk-nope"},
        )


def test_family_strips_provider_prefix_and_suffix() -> None:
    p = EndpointProfile(
        name="x",
        provider="x",
        base_url="http://x",
        protocol="openai_chat",
        endpoint_class="api",
        model="z-ai/GLM-5.3[1m]",
    )
    assert p.family == "glm-5.3"
