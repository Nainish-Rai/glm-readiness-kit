"""One regression test per named check: each must be able to pass and to fail."""

from __future__ import annotations

import pytest

from glmkit.conform.checks import Attempt, CheckContext, discover, get_check
from glmkit.contracts.case import CheckSpec, ConformanceCase
from glmkit.contracts.evidence import (
    Exchange,
    HttpRequestRecord,
    HttpResponseRecord,
    NormalizedResponse,
    NormUsage,
    StreamMeta,
)
from glmkit.contracts.profile import get_profile

CASE = ConformanceCase(id="TEST-001", title="t", category="x", severity="info", expect={"default": []})
GOOD = NormalizedResponse(
    http_status=200,
    ok=True,
    content="OK",
    reasoning="r",
    model="glm-5.3",
    finish_reason="end_turn",
    finish_reason_norm="stop",
    usage=NormUsage(prompt_tokens=5, completion_tokens=2),
    stream=StreamMeta(event_count=5, done_seen=True, finish_events=1),
)
ERR = NormalizedResponse(http_status=400, error_code="1210", error_code_type="string", error_message="no")


def ex(
    n: NormalizedResponse, body_text: str = "", body_json=None, mode: str = "stream", model: str = "glm-5.3"
) -> Exchange:
    return Exchange(
        id="e",
        protocol="openai_chat",
        mode=mode,
        request=HttpRequestRecord(url="u", body={"model": model}),
        response=HttpResponseRecord(status=n.http_status, body_text=body_text, body_json=body_json),
        normalized=n,
    )


def run(name: str, exchange: Exchange | None, **params) -> str:
    ctx = CheckContext(
        case=CASE,
        target=get_profile("mock"),
        spec=CheckSpec(check=name, **params),
        mode="nonstream",
        exchanges=[exchange] if exchange else [],
    )
    return get_check(name).fn(ctx).status


ZAI_ERR = {"error": {"code": "1210", "message": "no"}}
CRASH = ex(
    NormalizedResponse(http_status=500),
    body_text='Traceback (most recent call last):\n  File "/a.py", line 3\nKeyError: x',
)
CASES = [
    ("http_status", ex(GOOD), {"equals": 200}, ex(ERR), {"equals": 200}),
    ("no_5xx", ex(ERR), {}, CRASH, {}),
    ("error_code", ex(ERR, body_json=ZAI_ERR), {"in": ["1210"]}, ex(ERR), {"in": ["1214"]}),
    ("error_shape", ex(ERR, body_json=ZAI_ERR), {}, CRASH, {}),
    ("no_internal_leak", ex(GOOD, body_text="{}"), {}, CRASH, {}),
    ("finish_reason", ex(GOOD), {"in": ["stop"]}, ex(GOOD), {"in": ["tool_calls"]}),
    ("reasoning_present", ex(GOOD), {}, ex(ERR), {}),
    ("reasoning_absent", ex(ERR), {}, ex(GOOD), {}),
    ("content_present", ex(GOOD), {"matches": "^OK$"}, ex(ERR), {}),
    ("usage_present", ex(GOOD), {}, ex(ERR), {}),
    ("model_field_equals_request", ex(GOOD), {}, ex(GOOD, model="glm-4.7"), {}),
    ("sse_terminates", ex(GOOD), {}, ex(GOOD.model_copy(update={"stream": StreamMeta(timed_out=True)})), {}),
]


@pytest.mark.parametrize(("name", "good", "gp", "bad", "bp"), CASES, ids=[c[0] for c in CASES])
def test_check_passes_and_fails(name, good, gp, bad, bp) -> None:
    assert run(name, good, **gp) == "pass"
    assert run(name, bad, **bp) == "fail"


def other_slices_tested() -> set[str]:
    """Slices keep their check rows in their own test module as ``CHECK_CASES`` (rows start with the check name)."""
    import importlib
    import pkgutil

    import tests

    names: set[str] = set()
    for m in pkgutil.iter_modules(tests.__path__):
        if m.name.startswith("test_") and m.name != "test_checks":
            names |= {c[0] for c in getattr(importlib.import_module(f"tests.{m.name}"), "CHECK_CASES", [])}
    return names


def test_every_registered_per_attempt_check_has_a_regression_test() -> None:
    from tests.test_agentloop import TESTED as AGENTLOOP_TESTED  # slice 04 tests its multi-exchange checks itself

    tested = {c[0] for c in CASES} | other_slices_tested() | AGENTLOOP_TESTED
    per_attempt = {n for n, d in discover().items() if not d.aggregate and d.module.startswith("glmkit.conform.checks")}
    assert per_attempt <= tested, f"untested checks: {sorted(per_attempt - tested)}"


def test_error_code_accepts_integer_codes() -> None:
    n = ERR.model_copy(update={"error_code_type": "integer"})
    assert run("error_code", ex(n), **{"in": [1210]}) == "pass"


def test_pass_rate_aggregate() -> None:
    attempts = [Attempt(i, [], [], s) for i, s in enumerate(["pass", "pass", "fail", "throttled"])]
    ctx = CheckContext(
        case=CASE,
        target=get_profile("mock"),
        spec=CheckSpec(check="pass_rate", gte=0.6),
        mode="nonstream",
        attempts=attempts,
    )
    assert get_check("pass_rate").fn(ctx).status == "pass"
    ctx.spec = CheckSpec(check="pass_rate", gte=0.9)
    assert get_check("pass_rate").fn(ctx).status == "fail"
