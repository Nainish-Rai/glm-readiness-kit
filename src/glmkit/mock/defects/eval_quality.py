"""Mock behaviours for the migration eval (issue 10): targets with a controllable, *planted* answer quality.

These are not upstream bugs; they let the eval engine be proven against known ground truth. Switch them on per
target with profile headers (``X-GLMKit-Defects: eval-oracle,degrade-quality`` plus the knobs below), so one mock
server can play the incumbent, a worse candidate and an equal candidate at the same time.

* ``eval-oracle``      the reply is the text inside ``<mock-answer>...</mock-answer>`` of the last user message
                       (any text: a label, a number, JSON, SQL), instead of the default echo.
* ``degrade-quality``  with probability ``X-GLMKit-Degrade-Rate`` (default 0.5) the answer is corrupted
                       (reversed). The draw is a hash of (``X-GLMKit-Degrade-Salt``, ``X-GLMKit-Sample``, prompt), so
                       it is deterministic and independent per target and per replay.
* ``mock-judge``       answers a pairwise judge prompt (``### Answer A`` / ``### Answer B``) with a JSON verdict:
                       the answer closer to the reference wins. ``X-GLMKit-Judge-Position-Bias: p`` makes the
                       judge pick "A" with probability p regardless (the planted position bias).
* ``rate-limit-burst`` every ``X-GLMKit-Throttle-Every``-th request returns 429 / 1302 with ``Retry-After: 0``.

It also registers two model ids that only the mock knows, so a mock incumbent and a mock judge belong to other
model families than the GLM candidates: ``mock-incumbent-1`` and ``mock-judge-1``.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

from starlette.responses import JSONResponse, Response

from glmkit.mock import engine
from glmkit.mock.defects import Defect, register_defect
from glmkit.mock.types import MockContext, Reply

for _m in ("mock-incumbent-1", "mock-judge-1"):
    engine.MODELS.setdefault(_m, {"forced_thinking": False, "api_efforts": None})

ANSWER_TAG = re.compile(r"<mock-answer>(.*?)</mock-answer>", re.S)


def _last_user(ctx: MockContext) -> str:
    req = ctx.req
    return next((m.text for m in reversed(req.messages) if m.role == "user"), "") if req else ""


def _uniform(*parts: str) -> float:
    h = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _retoken(reply: Reply) -> None:
    """Recount completion tokens after the content changed (prompt/cache accounting must not run twice)."""
    reply.usage.completion_tokens = (
        engine.toks(reply.reasoning)
        + engine.toks(reply.content)
        + sum(engine.toks(t.arguments) for t in reply.tool_calls)
    )


def corrupt(text: str) -> str:
    wrong = text[::-1]
    return wrong if wrong != text else text + " (wrong)"


@register_defect
class EvalOracle(Defect):
    name = "eval-oracle"
    title = "eval mock: reply with the text in <mock-answer>...</mock-answer> of the last user message"

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        m = ANSWER_TAG.search(_last_user(ctx))
        if m and not reply.tool_calls:
            reply.content = m.group(1).strip()
            _retoken(reply)
        return reply


@register_defect
class DegradeQuality(Defect):
    name = "degrade-quality"
    title = "eval mock: corrupt the answer with probability X-GLMKit-Degrade-Rate (planted quality difference)"

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        rate = float(ctx.headers.get("x-glmkit-degrade-rate", "0.5"))
        u = _uniform(
            ctx.headers.get("x-glmkit-degrade-salt", ""), ctx.headers.get("x-glmkit-sample", "0"), _last_user(ctx)
        )
        if reply.content and u < rate:
            reply.content = corrupt(reply.content)
            _retoken(reply)
        return reply


def _section(prompt: str, title: str) -> str | None:
    m = re.search(rf"### {re.escape(title)}\n(.*?)(?=\n\n### |\n\nReply with only|\Z)", prompt, re.S)
    return m.group(1).strip() if m else None


def _closeness(a: str, ref: str) -> float:
    return difflib.SequenceMatcher(None, a.casefold().strip(), ref.casefold().strip()).ratio()


@register_defect
class MockJudge(Defect):
    name = "mock-judge"
    title = "eval mock: answer pairwise judge prompts; the answer closer to the reference wins (optional position bias)"

    def on_reply(self, ctx: MockContext, reply: Reply) -> Reply:
        prompt = _last_user(ctx)
        a, b = _section(prompt, "Answer A"), _section(prompt, "Answer B")
        if a is None or b is None:
            return reply
        ref = _section(prompt, "Reference answer")
        bias = float(ctx.headers.get("x-glmkit-judge-position-bias", "0"))
        if bias and _uniform("bias", prompt) < bias:
            winner, why = "A", "first answer is better"
        elif ref is None:
            winner, why = "TIE", "no reference to compare with"
        else:
            sa, sb = _closeness(a, ref), _closeness(b, ref)
            winner = "A" if sa - sb > 0.05 else "B" if sb - sa > 0.05 else "TIE"
            why = f"closeness to reference A={sa:.2f} B={sb:.2f}"
        reply.content = json.dumps({"winner": winner, "reason": why})
        _retoken(reply)
        return reply


@register_defect
class RateLimitBurst(Defect):
    name = "rate-limit-burst"
    title = "eval mock: every N-th request (X-GLMKit-Throttle-Every) returns 429 / 1302"

    def on_request(self, ctx: MockContext) -> Response | None:
        every = int(ctx.headers.get("x-glmkit-throttle-every", "0") or 0)
        if every <= 0:
            return None
        st: dict[str, Any] = self.state(ctx)
        st["n"] = st.get("n", 0) + 1
        if st["n"] % every == 0:
            return JSONResponse(
                {"error": {"code": "1302", "message": "Rate limit reached for requests"}},
                status_code=429,
                headers={"retry-after": "0"},
            )
        return None
