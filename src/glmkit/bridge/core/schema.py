"""Tool-schema helpers: flatten top-level oneOf/anyOf (GLM-5#169 returns ``{}`` arguments for them), validate
arguments, and map arguments of a flattened tool back to one branch of the original schema."""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

COMBINATORS = ("oneOf", "anyOf")


def needs_flatten(params: Any) -> str | None:
    """The combinator keyword when ``params`` has a top-level oneOf/anyOf with inline object branches."""
    if not isinstance(params, dict):
        return None
    for key in COMBINATORS:
        branches = params.get(key)
        if isinstance(branches, list) and branches and all(isinstance(b, dict) and "$ref" not in b for b in branches):
            return key
    return None


def flatten_schema(params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Merge the branches' properties into one object schema. A property is required only when every branch
    requires it (or the top level does). Returns ``(flat_schema, hint)``; ``hint`` describes the variants."""
    key = needs_flatten(params)
    assert key
    branches: list[dict[str, Any]] = params[key]
    props: dict[str, Any] = copy.deepcopy(params.get("properties") or {})
    req_sets = []
    for b in branches:
        for name, sub in (b.get("properties") or {}).items():
            props.setdefault(name, copy.deepcopy(sub))
        req_sets.append(set(b.get("required") or []))
    common = set.intersection(*req_sets) if req_sets else set()
    required = list(dict.fromkeys([*(params.get("required") or []), *sorted(common)]))
    flat = {k: copy.deepcopy(v) for k, v in params.items() if k not in COMBINATORS}
    flat["type"] = "object"
    flat["properties"] = props
    if required:
        flat["required"] = required
    else:
        flat.pop("required", None)
    variants = [
        " + ".join(sorted(b.get("required") or list(b.get("properties") or {})) or ["(none)"]) for b in branches
    ]
    hint = "Provide exactly one of these argument sets: " + " | ".join(f"({v})" for v in variants) + "."
    return flat, hint


def validation_issues(schema: Any, args: Any, limit: int = 3) -> list[tuple[str, str]]:
    """``(validator keyword, message)`` per violation of ``schema`` (empty when valid or the schema is unusable)."""
    if not isinstance(schema, dict) or not schema:
        return []
    try:
        validator = Draft202012Validator(schema)
        errs = sorted(validator.iter_errors(args), key=lambda e: list(map(str, e.path)))
    except SchemaError:
        return []
    except Exception:  # an exotic schema must never break a request
        return []
    out = []
    for e in errs[:limit]:
        where = "/".join(str(p) for p in e.path)
        out.append((str(e.validator), f"{where + ': ' if where else ''}{e.message[:160]}"))
    return out


def validation_errors(schema: Any, args: Any, limit: int = 3) -> list[str]:
    return [m for _, m in validation_issues(schema, args, limit)]


def unflatten_args(original: dict[str, Any], args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Map arguments produced for a flattened schema back to the original. Returns ``(args, changed)``.

    If the arguments already satisfy the original schema they are untouched. Otherwise the branch whose
    requirements are all met and that shares the most keys wins, and keys outside that branch are dropped.
    """
    if not validation_errors(original, args):
        return args, False
    key = needs_flatten(original)
    if not key:
        return args, False
    top_props = set(original.get("properties") or {})
    top_req = set(original.get("required") or [])
    best: tuple[int, dict[str, Any]] | None = None
    for b in original[key]:
        props = set(b.get("properties") or {}) | top_props
        req = set(b.get("required") or []) | top_req
        if not req <= set(args):
            continue
        cand = {k: v for k, v in args.items() if k in props} if props else dict(args)
        if validation_errors({**{k: v for k, v in original.items() if k not in COMBINATORS}, **b}, cand):
            continue
        score = len(set(cand))
        if best is None or score > best[0]:
            best = (score, cand)
    if best is None or best[1] == args:
        return args, False
    return best[1], True
