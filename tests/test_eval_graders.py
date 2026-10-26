"""Eval graders: deterministic ones, SQL equality, and the judge's position-swap bookkeeping."""

from __future__ import annotations

import json

import pytest

from glmkit.eval import calls
from glmkit.eval.goldenset import GoldenItem
from glmkit.eval.graders import GradeContext, discover, grade
from glmkit.eval.graders import judge as judge_mod
from glmkit.eval.graders.judge import JudgeService, family_of


def ctx(output: str, config: dict, expected=None, **kw) -> GradeContext:
    item = GoldenItem(id="i", prompt="p", expected=expected)
    return GradeContext(item=item, config=config, output=output, target="t", **kw)


async def test_registered_graders() -> None:
    assert {"exact", "regex", "numeric", "json_schema", "sql", "judge"} <= set(discover())


@pytest.mark.parametrize(
    ("output", "config", "expected", "ok"),
    [
        ("  Card_Lost \n", {"type": "exact", "ignore_case": True}, "card_lost", True),
        ("card lost", {"type": "exact"}, "card_lost", False),
        ("b", {"type": "exact"}, ["a", "b"], True),
        ("order 4711 shipped", {"type": "regex", "pattern": r"\b4711\b"}, None, True),
        ("order 4712 shipped", {"type": "regex", "pattern": r"\b4711\b"}, None, False),
        ("The answer is 1,234.50", {"type": "numeric"}, 1234.5, True),
        ("about 99", {"type": "numeric", "abs_tol": 2}, 100, True),
        ("about 90", {"type": "numeric", "rel_tol": 0.05}, 100, False),
        ("no digits", {"type": "numeric"}, 3, False),
    ],
)
async def test_basic_graders(output, config, expected, ok) -> None:
    g = await grade(ctx(output, config, expected))
    assert g.passed is ok and g.score == (1.0 if ok else 0.0)


SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}


async def test_json_schema_grader() -> None:
    cfg = {"type": "json_schema", "schema": SCHEMA, "match_expected": True}
    assert (await grade(ctx('```json\n{"a": 1, "b": 2}\n```', cfg, {"a": 1}))).passed
    assert not (await grade(ctx('{"a": "x"}', cfg, {"a": 1}))).passed  # schema violation
    assert not (await grade(ctx("not json", cfg, {"a": 1}))).passed
    wrong = await grade(ctx('{"a": 2}', cfg, {"a": 1}))
    assert not wrong.passed and wrong.data["stage"] == "expected"


SETUP = "CREATE TABLE t (x INTEGER, y TEXT); INSERT INTO t VALUES (1,'a'),(2,'b'),(3,'a');"


async def test_sql_grader_result_equality_and_failures() -> None:
    cfg = {"type": "sql", "setup": [SETUP]}
    ref = "SELECT x FROM t WHERE y = 'a'"
    assert (await grade(ctx("```sql\nSELECT x FROM t WHERE y='a' ORDER BY x DESC;\n```", cfg, ref))).passed
    assert not (await grade(ctx("SELECT x FROM t", cfg, ref))).passed
    bad = await grade(ctx("SELEC nonsense", cfg, ref))
    assert not bad.passed and bad.data["stage"] == "execute"
    assert not (await grade(ctx("SELECT x FROM t WHERE y='a' ORDER BY x DESC", {**cfg, "ordered": True}, ref))).passed


async def test_unknown_grader_and_bad_config_do_not_crash() -> None:
    g = await grade(ctx("x", {"type": "regex"}))  # no pattern
    assert not g.passed and g.data["error"]


def test_family_of() -> None:
    assert family_of("z-ai/glm-5.3[1m]") == "zai-glm"
    assert family_of("claude-sonnet-4") == "anthropic-claude"
    assert family_of("gpt-5") == "openai-gpt"
    assert family_of("mock-judge-1") == "mock-judge" and family_of("mock-incumbent-1") == "mock-incumbent"


class FakeProfile:
    model = "other-judge-1"


class FakeClient:
    profile = FakeProfile()


def make_judge(monkeypatch, answer) -> JudgeService:
    """answer(prompt) -> winner letter; replaces the network call."""

    async def fake_call(client, canon, **kw):
        winner = answer(canon["messages"][0]["content"])
        return calls.CallOutcome(status="ok", content=json.dumps({"winner": winner, "reason": "r"}))

    monkeypatch.setattr(judge_mod, "call_model", fake_call)
    return JudgeService(FakeClient())


def slot_of(prompt: str, text: str) -> str:
    a = prompt.split("### Answer A\n")[1].split("\n\n### Answer B")[0]
    return "A" if a == text else "B"


async def test_judge_consistent_win_and_swap(monkeypatch) -> None:
    svc = make_judge(monkeypatch, lambda p: slot_of(p, "good answer"))
    c = ctx("good answer", {"type": "judge"}, "ref", baseline_output="bad", judge=svc)
    g = await grade(c)
    assert g.score == 1.0 and g.data["judge"]["consistent"] and svc.calls == 2


async def test_judge_position_bias_is_inconsistent_and_flagged(monkeypatch) -> None:
    svc = make_judge(monkeypatch, lambda p: "A")  # always the first slot
    g = await grade(ctx("good answer", {"type": "judge"}, "ref", baseline_output="bad", judge=svc))
    v = g.data["judge"]
    assert g.score == 0.5 and not v["consistent"] and v["position_bias"] == "first"
    assert "position_inconsistent" in g.data["flags"]


async def test_judge_flags_longer_winner_and_caches(monkeypatch) -> None:
    long_answer = "a much longer and wordier answer " * 4
    svc = make_judge(monkeypatch, lambda p: slot_of(p, long_answer))
    c = ctx(long_answer, {"type": "judge"}, "ref", baseline_output="short", judge=svc)
    g = await grade(c)
    assert g.data["judge"]["longer_won"] and "longer_won" in g.data["flags"]
    await grade(c)
    assert svc.calls == 2 and svc.cache_hits == 2  # second grading served from the cache


async def test_baseline_is_the_judge_reference_point() -> None:
    g = await grade(ctx("x", {"type": "judge"}, "ref", is_baseline=True))
    assert g.score == 0.5
