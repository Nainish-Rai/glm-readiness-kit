"""Named oracles ("checks") for conformance cases.

A check is a function registered with ``@check("name")`` in any module of this package (or a plugin module).
It receives a ``CheckContext`` and returns a ``CheckOutcome``. Cases reference checks by name and pass
parameters; checks never know which case called them.

Per-attempt checks see the exchanges of one attempt. Aggregate checks (``@check(name, aggregate=True)``) run
once per item after all attempts and see every attempt's per-check results (``ctx.attempts``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from glmkit.contracts.case import CheckSpec, ConformanceCase, Variant
from glmkit.contracts.envelope import CheckResult
from glmkit.contracts.evidence import Exchange, NormalizedResponse
from glmkit.contracts.profile import EndpointProfile
from glmkit.plugins import import_submodules, load_plugins


@dataclass
class CheckOutcome:
    status: str  # pass | fail | skip | error
    message: str = ""
    expected: Any = None
    observed: Any = None


def ok(message: str = "", *, expected: Any = None, observed: Any = None) -> CheckOutcome:
    return CheckOutcome("pass", message, expected, observed)


def fail(message: str, *, expected: Any = None, observed: Any = None) -> CheckOutcome:
    return CheckOutcome("fail", message, expected, observed)


def skip(message: str) -> CheckOutcome:
    return CheckOutcome("skip", message)


@dataclass
class Attempt:
    index: int
    exchanges: list[Exchange]
    checks: list[CheckResult]
    status: str


@dataclass
class CheckContext:
    case: ConformanceCase
    target: EndpointProfile
    spec: CheckSpec
    mode: str
    variant: Variant | None = None
    exchanges: list[Exchange] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)  # aggregate checks only

    @property
    def params(self) -> dict[str, Any]:
        return self.spec.params

    @property
    def exchange(self) -> Exchange | None:
        """The exchange named by ``params.exchange`` (label), else the last one."""
        label = self.params.get("exchange")
        if label:
            return next((e for e in self.exchanges if e.label == label), None)
        return self.exchanges[-1] if self.exchanges else None

    @property
    def response(self) -> NormalizedResponse:
        ex = self.exchange
        return ex.normalized if ex else NormalizedResponse()


CheckFn = Callable[[CheckContext], CheckOutcome]


@dataclass
class CheckDef:
    name: str
    fn: CheckFn
    aggregate: bool
    description: str
    module: str


_REGISTRY: dict[str, CheckDef] = {}
_discovered = False


def check(name: str, *, aggregate: bool = False, description: str = "") -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        if name in _REGISTRY and _REGISTRY[name].fn is not fn:
            raise ValueError(f"check {name!r} registered twice ({_REGISTRY[name].module}, {fn.__module__})")
        doc = description or (fn.__doc__ or "").strip().split("\n")[0]
        _REGISTRY[name] = CheckDef(name, fn, aggregate, doc, fn.__module__)
        return fn

    return deco


def discover() -> dict[str, CheckDef]:
    global _discovered
    if not _discovered:
        import_submodules(__name__)
        _discovered = True
    load_plugins()
    return dict(sorted(_REGISTRY.items()))


def get_check(name: str) -> CheckDef:
    reg = discover()
    if name not in reg:
        raise KeyError(f"unknown check {name!r}; known: {', '.join(reg)}")
    return reg[name]


# ------------------------------------------------------------------ shared comparison helper


def _holds(op: str, obs: Any, w: Any) -> bool:
    if op == "equals":
        return obs == w
    if op == "not_equals":
        return obs != w
    if op == "in":
        return obs in w
    if op == "not_in":
        return obs not in w
    if op == "matches":
        return obs is not None and re.search(str(w), str(obs)) is not None
    if obs is None:
        return False
    return {"lt": obs < w, "lte": obs <= w, "gt": obs > w, "gte": obs >= w}[op]


COMPARATORS = ("equals", "not_equals", "in", "not_in", "lt", "lte", "gt", "gte", "matches")


def compare(observed: Any, params: dict[str, Any], *, normalize: Callable[[Any], Any] = lambda v: v) -> CheckOutcome:
    """Compare ``observed`` with every comparator present in ``params`` (all must hold)."""
    present = {k: params[k] for k in COMPARATORS if k in params}
    if not present:
        return CheckOutcome("error", f"no comparator given; use one of {COMPARATORS}")
    obs = normalize(observed)
    for op, want in present.items():
        w = [normalize(x) for x in want] if op in ("in", "not_in") else normalize(want)
        if not _holds(op, obs, w):
            return fail(f"expected {op} {want!r}, got {observed!r}", expected={op: want}, observed=observed)
    return ok(f"{observed!r}", expected=present, observed=observed)
