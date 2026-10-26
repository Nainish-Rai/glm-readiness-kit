"""JSON Schema export for the shared contracts. ``glmkit schema export`` writes these to ``schemas/``;
a test fails when the committed copies drift from the models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from glmkit.contracts.case import ConformanceCase
from glmkit.contracts.envelope import ResultEnvelope
from glmkit.contracts.profile import EndpointProfile
from glmkit.contracts.trace import SwitchTrace

SCHEMAS: dict[str, type[BaseModel]] = {
    "result-envelope": ResultEnvelope,
    "conformance-case": ConformanceCase,
    "switch-trace-v0": SwitchTrace,
    "endpoint-profile": EndpointProfile,
}


def schema_for(name: str) -> dict[str, Any]:
    model = SCHEMAS[name]
    schema = model.model_json_schema(mode="serialization", by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://github.com/glm-readiness-kit/glmkit/schemas/{name}.schema.json"
    return schema


def render(name: str) -> str:
    return json.dumps(schema_for(name), indent=2, sort_keys=True) + "\n"


def export_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in SCHEMAS:
        p = out_dir / f"{name}.schema.json"
        p.write_text(render(name), encoding="utf-8")
        paths.append(p)
    return paths


def validate(name: str, instance: Any) -> None:
    """Raise jsonschema.ValidationError if ``instance`` does not match the exported schema."""
    import jsonschema

    jsonschema.validate(instance, schema_for(name), cls=jsonschema.Draft202012Validator)
