# ruff: noqa: E501
"""CycloneDX AI-BOM (emit 1.6, validate against the vendored 1.6 and 1.7 schemas) and an optional SPDX 3.0 export.

Schemas are vendored under ``vendor/cyclonedx/`` (Apache-2.0, with the upstream LICENSE); nothing is downloaded
at runtime. The 1.7 schema does not make 1.6 documents invalid, which is what "validate against 1.7" checks:
the document is forward compatible when the kit bumps its emitted version.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from glmkit import __version__
from glmkit.evidence.descriptor import PATH_TITLES, Deployment
from glmkit.evidence.disclaimer import DISCLAIMER
from glmkit.evidence.index import IndexedRun

VENDOR = Path(__file__).resolve().parent / "vendor" / "cyclonedx"
SCHEMA_BASE = "http://cyclonedx.org/schema/"
EMIT_VERSION = "1.6"
VALIDATE_VERSIONS = ("1.6", "1.7")
LICENSE_URL = "https://huggingface.co/zai-org/GLM-5.3/raw/main/LICENSE"
LICENSE_NAME = "GLM-5.3 license (MIT-style custom license with a Model-as-a-Service security-review clause)"


class BomValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:5]) + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""))
        self.errors = errors


@lru_cache(maxsize=1)
def _registry() -> Registry:
    reg: Registry = Registry()
    for f in sorted(VENDOR.glob("*.schema.json")):
        reg = reg.with_resource(SCHEMA_BASE + f.name, Resource.from_contents(json.loads(f.read_text("utf-8")), DRAFT7))
    return reg


def validate_cyclonedx(doc: dict[str, Any], versions: tuple[str, ...] = VALIDATE_VERSIONS) -> list[str]:
    """Schema errors as 'schema: json.path: message' strings (empty = valid against every version asked)."""
    errors: list[str] = []
    for v in versions:
        schema = json.loads((VENDOR / f"bom-{v}.schema.json").read_text("utf-8"))
        validator = Draft7Validator(schema, registry=_registry(), format_checker=Draft7Validator.FORMAT_CHECKER)
        for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
            path = "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
            errors.append(f"cyclonedx-{v}: {path}: {e.message[:200]}")
    return errors


def assert_valid(doc: dict[str, Any]) -> None:
    errors = validate_cyclonedx(doc)
    if errors:
        raise BomValidationError(errors)


def _prop(name: str, value: Any) -> dict[str, str]:
    return {"name": f"glmkit:{name}", "value": str(value)}


def _sha(digest: str | None) -> str | None:
    m = re.fullmatch(r"(?:sha256:)?([0-9a-fA-F]{64})", digest or "")
    return m.group(1).lower() if m else None


def build_cyclonedx(
    dep: Deployment, runs: list[IndexedRun], *, residency: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    m, h, s = dep.model, dep.hosting, dep.serving
    app_ref, model_ref, host_ref = f"app:{dep.name}", f"model:{m.id}", "service:host"
    refs = [model_ref]

    model: dict[str, Any] = {
        "bom-ref": model_ref,
        "type": "machine-learning-model",
        "group": m.id.split("/")[0],
        "name": m.name,
        "version": m.revision or "unpinned",
        "description": "GLM-5.3: mixture-of-experts language model (GlmMoeDsaForCausalLM). Counts below are the same model counted three ways.",
        "licenses": [{"license": {"name": LICENSE_NAME, "url": LICENSE_URL}}],
        "externalReferences": [
            {"type": "distribution", "url": dep.weights.source_url or f"https://huggingface.co/{m.id}"},
            {"type": "license", "url": LICENSE_URL},
        ],
        "modelCard": {
            "modelParameters": {
                "task": "text-generation",
                "architectureFamily": "glm_moe_dsa",
                "modelArchitecture": "GlmMoeDsaForCausalLM",
            }
        },
        "properties": [
            _prop("total_params", m.total_params),
            _prop("params_excl_mtp", m.params_excl_mtp),
            _prop("active_params", m.active_params),
            _prop("quantization", m.quantization or "not logged"),
            _prop("license_note", "needs-counsel for AI Act Art. 53(2) open-source exemption"),
        ],
    }
    if dep.weights.sha256:
        model["hashes"] = [{"alg": "SHA-256", "content": dep.weights.sha256.lower()}]
    components: list[dict[str, Any]] = [model]

    if s.engine:
        eng: dict[str, Any] = {
            "bom-ref": "engine",
            "type": "application",
            "name": s.engine,
            "version": s.engine_version or "not logged",
        }
        digest = _sha(s.image_digest)
        if digest:
            eng["hashes"] = [{"alg": "SHA-256", "content": digest}]
        elif s.image_digest:
            eng["properties"] = [_prop("image_digest", s.image_digest)]
        components.append(eng)
        refs.append("engine")
    tmpl = _sha(s.tokenizer_template_sha256)
    if tmpl:
        components.append(
            {
                "bom-ref": "chat-template",
                "type": "data",
                "name": "tokenizer-chat-template",
                "description": "The chat template changes how tools are rendered.",
                "hashes": [{"alg": "SHA-256", "content": tmpl}],
            }
        )
        refs.append("chat-template")
    if s.hardware:
        components.append(
            {
                "bom-ref": "hardware",
                "type": "device",
                "name": s.hardware,
                "properties": [_prop("gpu_vendor", s.gpu_vendor or "not logged")],
            }
        )
        refs.append("hardware")

    host_data = [
        {
            "flow": "bi-directional",
            "classification": "personal-data" if dep.personal_data.categories else "unclassified",
            "description": ", ".join(dep.personal_data.categories) or "no personal data declared",
        }
    ]
    host: dict[str, Any] = {
        "bom-ref": host_ref,
        "name": h.provider,
        "provider": {"name": h.provider},
        "description": f"{PATH_TITLES[dep.path]} ({h.region or 'region not stated'})",
        "x-trust-boundary": True,
        "data": host_data,
        "properties": [
            _prop("jurisdiction", h.jurisdiction),
            _prop("path", dep.path),
            _prop("eu_data_residency_requirement", dep.requirements.eu_data_residency),
            _prop("eu_data_residency_status", residency or "not evaluated"),
            _prop("cross_border_flag", "true" if residency == "gap" else "see checklist"),
        ],
    }
    if h.endpoint:
        host["endpoints"] = [h.endpoint]
    services = [host]
    for i, sp in enumerate(dep.sub_processors):
        services.append(
            {
                "bom-ref": f"service:sub{i}",
                "name": sp.name,
                "provider": {"name": sp.name},
                "description": sp.role or "sub-processor",
                "properties": [
                    _prop("location", sp.location or "not logged"),
                    _prop("outside_eea", "unknown" if sp.outside_eea is None else str(sp.outside_eea).lower()),
                ],
            }
        )

    refs_ext = []
    for r in [x for x in runs if x.component in ("conform", "eval", "guard", "deploy")][:12]:
        refs_ext.append(
            {
                "type": "evidence",
                "url": f"results/{r.path}",
                "comment": f"{r.component} run {r.run_id}, verdict {r.verdict}",
            }
        )

    return {
        "bomFormat": "CycloneDX",
        "specVersion": EMIT_VERSION,
        "serialNumber": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"glmkit:{dep.name}:{m.id}:{m.revision}")),
        "version": 1,
        "metadata": {
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": {"components": [{"type": "application", "name": "glmkit", "version": __version__}]},
            "component": {
                "bom-ref": app_ref,
                "type": "application",
                "name": dep.name,
                "description": dep.description or PATH_TITLES[dep.path],
                **({"externalReferences": refs_ext} if refs_ext else {}),
            },
            "properties": [_prop("disclaimer", DISCLAIMER)],
        },
        "components": components,
        "services": services,
        "dependencies": [{"ref": app_ref, "dependsOn": [*refs, host_ref]}],
    }


def build_spdx3(dep: Deployment, *, now: datetime | None = None) -> dict[str, Any]:
    """Minimal SPDX 3.0.1 JSON-LD with an AI package. Optional export; not schema-validated by the kit."""
    now = now or datetime.now(UTC)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    ns = f"https://example.invalid/glmkit/{dep.name}"
    return {
        "@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
        "@graph": [
            {
                "type": "CreationInfo",
                "@id": "_:creationInfo",
                "specVersion": "3.0.1",
                "created": ts,
                "createdBy": [f"{ns}/tool/glmkit"],
            },
            {
                "type": "Tool",
                "spdxId": f"{ns}/tool/glmkit",
                "creationInfo": "_:creationInfo",
                "name": f"glmkit {__version__}",
            },
            {
                "type": "ai_AIPackage",
                "spdxId": f"{ns}/model",
                "creationInfo": "_:creationInfo",
                "name": dep.model.name,
                "software_packageVersion": dep.model.revision or "unpinned",
                "software_downloadLocation": dep.weights.source_url or f"https://huggingface.co/{dep.model.id}",
                "software_primaryPurpose": "model",
                "comment": LICENSE_NAME + ". " + DISCLAIMER,
                "ai_autonomyType": "noAssertion",
                "ai_safetyRiskAssessment": "unknown",
            },
        ],
    }
