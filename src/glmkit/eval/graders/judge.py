"""LLM judge with the bias controls of research/03-eval.md §3.

* **Pairwise with position swap**: the candidate output and the baseline output are judged twice, once in each
  order. A win counts only if both orders agree; otherwise the item scores 0.5 and is flagged
  ``position_inconsistent``. The inconsistency rate is reported as a judge-quality metric.
* **Blind**: the judge sees "Answer A" and "Answer B", never a model name; only final text, no reasoning.
* **Different family**: the judge's model family is compared with every judged target's; a shared family is
  reported as a self-preference warning (``family_overlap``).
* **Length control**: character lengths of both answers are recorded, and a consistent win by an answer more than
  ``length_ratio_flag`` (default 1.3x) longer is flagged ``longer_won``; the report stratifies by length ratio.
* **Cache**: verdicts are cached by (judge model, rubric, content hash) so a re-run or resume is reproducible.

Score semantics: the candidate scores 1 (wins both orders), 0.5 (tie / inconsistent) or 0 (loses both orders)
against the baseline's answer to the same prompt. The baseline's own score on a judged item is 0.5 by construction,
so the paired quality delta on judge items is win-rate minus 0.5.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from glmkit.client import GLMClient
from glmkit.eval.calls import call_model
from glmkit.eval.goldenset import GoldenItem
from glmkit.eval.graders import Grade, GradeContext, grader

DEFAULT_RUBRIC = (
    "Prefer the answer that is correct, complete and follows the task's instructions. Ignore length and style."
)
FAMILIES = (
    ("glm", "zai-glm"),
    ("claude", "anthropic-claude"),
    ("fable", "anthropic-claude"),
    ("opus", "anthropic-claude"),
    ("sonnet", "anthropic-claude"),
    ("haiku", "anthropic-claude"),
    ("gpt", "openai-gpt"),
    ("o1", "openai-gpt"),
    ("o3", "openai-gpt"),
    ("gemini", "google-gemini"),
    ("gemma", "google-gemini"),
    ("llama", "meta-llama"),
    ("mistral", "mistral"),
    ("qwen", "alibaba-qwen"),
    ("kimi", "moonshot-kimi"),
    ("deepseek", "deepseek"),
)


def family_of(model: str) -> str:
    """Coarse model family from a model id ('z-ai/glm-5.3[1m]' -> 'zai-glm'); unknown ids are their own family."""
    name = re.sub(r"\[[^\]]*\]$", "", model.rsplit("/", 1)[-1]).lower()
    for prefix, fam in FAMILIES:
        if name.startswith(prefix):
            return fam
    return re.sub(r"[-_.]?v?\d[\w.]*$", "", name) or name  # unknown ids: the name without its version


@dataclass
class JudgeVerdict:
    outcome: str  # cand | base | tie
    score: float
    consistent: bool
    orders: list[str]  # winner per order as cand | base | tie (order 1: cand=A; order 2: base=A)
    reasons: list[str]
    len_cand: int
    len_base: int
    position_bias: str | None  # 'first' / 'second' when both orders picked the same slot
    longer_won: bool
    errors: list[str]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def render_prompt(task: str, reference: str | None, rubric: str, answer_a: str, answer_b: str) -> str:
    ref = f"### Reference answer\n{reference}\n\n" if reference else ""
    return (
        "You are an impartial judge comparing two answers to the same task. Decide which answer is better "
        "according to the rubric. Do not prefer an answer because of its position, length or style.\n\n"
        f"### Task\n{task}\n\n{ref}### Rubric\n{rubric}\n\n"
        f"### Answer A\n{answer_a}\n\n### Answer B\n{answer_b}\n\n"
        'Reply with only a JSON object: {"winner": "A" | "B" | "TIE", "reason": "<one sentence>"}'
    )


def parse_winner(text: str) -> tuple[str | None, str]:
    m = re.search(r"\{.*?\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            w = str(obj.get("winner", "")).strip().upper()
            if w in ("A", "B", "TIE"):
                return w, str(obj.get("reason", ""))[:300]
        except ValueError:
            pass
    m2 = re.search(r"\b(TIE|A|B)\b", text.upper())
    return (m2.group(1), text[:200]) if m2 else (None, text[:200])


class JudgeService:
    def __init__(
        self,
        client: GLMClient,
        *,
        headers: dict[str, str] | None = None,
        cache_path: Path | None = None,
        semaphore: asyncio.Semaphore | None = None,
        swap: bool = True,
        length_ratio_flag: float = 1.3,
        default_rubric: str = DEFAULT_RUBRIC,
    ) -> None:
        self.client = client
        self.model = client.profile.model
        self.family = family_of(self.model)
        self.headers = headers or {}
        self.sem = semaphore or asyncio.Semaphore(4)
        self.swap = swap
        self.length_ratio_flag = length_ratio_flag
        self.default_rubric = default_rubric
        self.cache_path = cache_path
        self.cache: dict[str, Any] = {}
        self.calls = 0
        self.cache_hits = 0
        if cache_path and cache_path.exists():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["key"]] = row["value"]

    def _remember(self, key: str, value: dict[str, Any]) -> None:
        self.cache[key] = value
        if self.cache_path:
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")

    async def _ask(self, prompt: str, rubric: str) -> dict[str, Any]:
        key = _hash(self.model, rubric, prompt)
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        async with self.sem:
            out = await call_model(
                self.client,
                {"messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 400},
                headers=self.headers,
            )
        self.calls += 1
        if out.status != "ok":
            return {"winner": None, "reason": f"judge call {out.status}: {out.error}", "error": True}
        winner, reason = parse_winner(out.content)
        value = {"winner": winner, "reason": reason, "error": winner is None}
        if winner is not None:
            self._remember(key, value)
        return value

    async def compare(self, item: GoldenItem, cand: str, base: str, config: dict[str, Any]) -> JudgeVerdict:
        task = "\n\n".join(str(m.get("content", "")) for m in item.messages if m.get("role") != "assistant")
        if item.system:
            task = item.system + "\n\n" + task
        ref = config.get("reference", item.expected if item.expected is not None else item.recorded)
        ref = None if ref is None else (ref if isinstance(ref, str) else json.dumps(ref, ensure_ascii=False))
        rubric = config.get("rubric", self.default_rubric)
        first = await self._ask(render_prompt(task, ref, rubric, cand, base), rubric)
        orders: list[str] = []
        reasons: list[str] = []
        errors: list[str] = []
        slots: list[str] = []

        def mapped(res: dict[str, Any], cand_is: str) -> str:
            w = res["winner"]
            if w is None:
                errors.append(res["reason"])
                return "error"
            slots.append(w)
            if w == "TIE":
                return "tie"
            return "cand" if w == cand_is else "base"

        orders.append(mapped(first, "A"))
        reasons.append(first["reason"])
        if self.swap:
            second = await self._ask(render_prompt(task, ref, rubric, base, cand), rubric)
            orders.append(mapped(second, "B"))
            reasons.append(second["reason"])
        consistent = "error" not in orders and len(set(orders)) == 1
        outcome = orders[0] if consistent else "tie"
        score = {"cand": 1.0, "base": 0.0, "tie": 0.5}[outcome]
        position_bias = None
        if self.swap and len(slots) == 2 and slots[0] == slots[1] and slots[0] in ("A", "B"):
            position_bias = "first" if slots[0] == "A" else "second"
        lc, lb = len(cand), len(base)
        longer = max(lc, lb) / max(1, min(lc, lb)) >= self.length_ratio_flag
        longer_won = consistent and longer and ((outcome == "cand" and lc > lb) or (outcome == "base" and lb > lc))
        return JudgeVerdict(outcome, score, consistent, orders, reasons, lc, lb, position_bias, longer_won, errors)


@grader("judge")
async def judge(ctx: GradeContext) -> Grade:
    """Pairwise LLM judge against the baseline's answer, position-swapped. Config: rubric, reference."""
    if ctx.is_baseline:
        return Grade(0.5, False, "baseline is the comparison point (0.5 by construction)", {"baseline": True})
    svc: JudgeService | None = ctx.judge
    if svc is None:
        raise ValueError("judge grader needs --judge <profile>")
    if ctx.baseline_output is None:
        return Grade(0.0, False, "no baseline output to compare with", {"error": True})
    v = await svc.compare(ctx.item, ctx.output, ctx.baseline_output, ctx.config)
    if v.errors:
        return Grade(
            0.5, False, f"judge error: {v.errors[0]}"[:200], {"judge": v.to_json(), "error": True, "judge_error": True}
        )
    flags = [f for f, on in (("position_inconsistent", not v.consistent), ("longer_won", v.longer_won)) if on]
    detail = f"judge {'/'.join(v.orders)} -> {v.outcome}" + (f" [{', '.join(flags)}]" if flags else "")
    return Grade(v.score, v.score > 0.5, detail, {"judge": v.to_json(), "flags": flags})
