"""Input-token estimate for ``/v1/messages/count_tokens`` and for the ``message_start`` usage.

Why an estimator: Z.ai's count_tokens has been reported to disagree with the usage it bills (zai-coding-plugins#12),
which makes Claude Code's context meter and auto-compact trigger wrong. The bridge answers count_tokens itself.

``features`` reduces a request to five numbers: content tokens (characters of everything the model reads -- system,
tool schemas, message text, thinking, tool_use input, tool_result content -- at 4 characters per token), the
message, structured-block and tool-definition counts, and a constant per request. ``TokenCalibrator`` fits, per
model, ``tokens = sum(coef_i * feature_i)`` by ridge-regularised least squares, weighted so relative error counts
equally for small and large requests, over the last observations of what the upstream really reported for
requests that went through the bridge (``usage.input_tokens`` plus cache reads). Before enough observations it
uses the prior (1 per content token, 4 per message, 3 per structured block, 8 per tool definition).
"""

from __future__ import annotations

import json
import math
import threading
from collections import deque
from typing import Any

from glmkit.bridge.anthropic.translate import block_text

CHARS_PER_TOKEN = 4.0
MESSAGE_OVERHEAD = 4
IMAGE_TOKENS = 1000
# prior tokens per: content token, message, structured block (tool_use, tool_result, thinking), tool definition, request
PRIOR = (1.0, float(MESSAGE_OVERHEAD), 3.0, 8.0, 0.0)
# size of the pseudo-observation that holds each coefficient near its prior
PRIOR_SCALE = (1000.0, 10.0, 10.0, 10.0, 10.0)
PRIOR_WEIGHT = (1.0, 1.0, 1.0, 1.0, 1.0)
WINDOW = 64
MIN_FIT = 3  # observations before the fit replaces the prior
COEF_RANGE = ((0.25, 4.0), (0.0, 200.0), (0.0, 200.0), (0.0, 400.0), (-50.0, 500.0))


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _clen(text: Any) -> int:
    """Length of a string as it travels in the JSON request (quotes, backslashes and newlines are escaped)."""
    return len(json.dumps(text, ensure_ascii=False)) - 2 if isinstance(text, str) else 0


def _block_chars(b: Any) -> tuple[int, int]:
    """(characters, extra tokens) of one content block."""
    if isinstance(b, str):
        return _clen(b), 0
    if not isinstance(b, dict):
        return 0, 0
    t = b.get("type")
    if t == "text":
        return _clen(b.get("text", "")), 0
    if t == "thinking":
        return _clen(b.get("thinking", "")), 0
    if t in ("tool_use", "server_tool_use"):
        return len(b.get("name", "")) + len(_dump(b.get("input") or {})), 0
    if t == "tool_result":
        c = b.get("content")
        if not isinstance(c, list):
            return _clen(block_text(c)), 0
        parts = [_block_chars(x) for x in c]
        return sum(p[0] for p in parts), sum(p[1] for p in parts)
    if t == "image":
        return 0, IMAGE_TOKENS
    return 0, 0


Features = tuple[float, int, int, int, int]  # content tokens, messages, structured blocks, tool definitions, 1


def features(body: dict[str, Any]) -> Features:
    """What a request costs, as (content tokens, message count, structured block count, tool count)."""
    chars = _clen(block_text(body.get("system")))
    extra = 0
    tools = [t for t in body.get("tools") or [] if isinstance(t, dict)]
    for tool in tools:
        chars += (
            len(tool.get("name", ""))
            + _clen(tool.get("description") or "")
            + len(_dump(tool.get("input_schema") or {}))
        )
    msgs = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    blocks = 0
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str):
            chars += _clen(c)
            continue
        for b in c or []:
            n, e = _block_chars(b)
            chars += n
            extra += e
            blocks += isinstance(b, dict) and b.get("type") in (
                "tool_use",
                "tool_result",
                "thinking",
                "server_tool_use",
            )
    return chars / CHARS_PER_TOKEN + extra, len(msgs), blocks, len(tools), 1


def estimate_raw(body: dict[str, Any]) -> int:
    """Uncalibrated estimate (the prior)."""
    return math.ceil(sum(p * x for p, x in zip(PRIOR, features(body), strict=True)))


def _solve(a: list[list[float]], y: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when singular."""
    n = len(y)
    m = [[*row, y[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-9:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


class TokenCalibrator:
    def __init__(self) -> None:
        self._obs: dict[str, deque[tuple[Features, int]]] = {}
        self.observations = 0
        self._lock = threading.Lock()

    @staticmethod
    def _key(model: str | None) -> str:
        return (model or "").split("[", 1)[0].rsplit("/", 1)[-1].lower()

    def coefficients(self, model: str | None) -> tuple[float, ...]:
        with self._lock:
            obs = list(self._obs.get(self._key(model), ()))
        if len(obs) < MIN_FIT:
            return PRIOR
        k = len(PRIOR)
        # weight by 1/y^2: relative error counts equally for small and large requests
        ref = sum(y for _, y in obs) / len(obs)
        rows: list[tuple[list[float], float, float]] = [
            ([float(v) for v in f], float(y), (ref / y) ** 2) for f, y in obs
        ]
        for j in range(k):  # one pseudo-observation per coefficient pulls it toward the prior
            x = [0.0] * k
            x[j] = PRIOR_SCALE[j]
            rows.append((x, PRIOR[j] * PRIOR_SCALE[j], PRIOR_WEIGHT[j]))
        ata = [[sum(w * r[0][i] * r[0][j] for r in rows for w in (r[2],)) for j in range(k)] for i in range(k)]
        aty = [sum(r[2] * r[0][i] * r[1] for r in rows) for i in range(k)]
        sol = _solve(ata, aty)
        if sol is None:
            return PRIOR
        return tuple(min(max(v, lo), hi) for v, (lo, hi) in zip(sol, COEF_RANGE, strict=True))

    def factor(self, model: str | None) -> float:
        """The content-token multiplier (1.0 before calibration)."""
        return self.coefficients(model)[0]

    def observe(self, model: str | None, feats: Features, actual: int) -> None:
        if actual <= 0:
            return
        with self._lock:
            self._obs.setdefault(self._key(model), deque(maxlen=WINDOW)).append((feats, actual))
            self.observations += 1

    def estimate_features(self, model: str | None, feats: Features) -> int:
        coef = self.coefficients(model)
        return max(1, round(sum(c * x for c, x in zip(coef, feats, strict=True))))

    def estimate(self, body: dict[str, Any]) -> int:
        return self.estimate_features(body.get("model"), features(body))
