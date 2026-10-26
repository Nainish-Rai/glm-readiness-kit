"""Deployment descriptor (YAML): what the customer deploys, where data flows, who else touches it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ChecklistStatus = Literal["met", "gap", "n/a", "needs-counsel"]
DeployPath = Literal["self-hosted", "mistral-regional", "bedrock", "zai-api"]
Jurisdiction = Literal["EU", "SG", "US", "CN", "other", "unknown"]

PATH_TITLES: dict[str, str] = {
    "self-hosted": "Self-hosted open weights",
    "mistral-regional": "Mistral regional endpoint",
    "bedrock": "Amazon Bedrock (cross-Region inference)",
    "zai-api": "Z.ai first-party API",
}


class DescriptorError(ValueError):
    pass


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelInfo(_M):
    id: str = "zai-org/GLM-5.3"
    name: str = "GLM-5.3"
    revision: str | None = Field(default=None, description="Pinned repo revision SHA, if known")
    quantization: str | None = "FP8 (e4m3, block-128, native checkpoint)"
    # Research 04 section 0: three counts of the same model, kept as separate fields.
    total_params: int = 753_329_940_480
    params_excl_mtp: int = 744_000_000_000
    active_params: int = 40_000_000_000


class Hosting(_M):
    provider: str
    region: str | None = None
    jurisdiction: Jurisdiction = "unknown"
    endpoint: str | None = None
    bedrock_profile: Literal["us", "global"] | None = None
    mistral_endpoint: Literal["eu", "us", "global"] | None = None
    glm_on_endpoint_verified: bool = Field(
        default=False, description="models.list on the regional endpoint listed GLM-5.3 (Mistral path)"
    )


class Serving(_M):
    engine: str | None = None
    engine_version: str | None = None
    image_digest: str | None = None
    gpu_vendor: str | None = None
    hardware: str | None = None
    tokenizer_template_sha256: str | None = None


class Weights(_M):
    source_url: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    outbound_telemetry: bool | None = None


class DataFlow(_M):
    clients: list[str] = Field(default_factory=lambda: ["Customer applications"])
    telemetry_to_zai: bool | None = Field(default=None, description="Does any data, telemetry or fee flow to Z.ai?")
    notes: str | None = None


class PersonalData(_M):
    categories: list[str] = Field(default_factory=list)
    special_category: bool = False
    data_subjects: list[str] = Field(default_factory=list)


class Retention(_M):
    prompts_days: int | None = None
    logs_days: int | None = None
    provider_retention: str | None = None


class SubProcessor(_M):
    name: str
    role: str = ""
    location: str = ""
    outside_eea: bool | None = None


class FineTuning(_M):
    performed: bool = False
    tokens: int | None = Field(default=None, ge=0)
    gpu_hours: float | None = Field(default=None, ge=0)
    dataset_provenance: str | None = None


class Requirements(_M):
    eu_data_residency: bool = True
    sg_processing: bool = False


class Operator(_M):
    maas_over_10b_revenue: bool | None = Field(
        default=None, description="Licensee (with affiliates) runs a MaaS business over US$10B revenue / 12 months"
    )


class Attestation(_M):
    """A human statement for an item the kit cannot measure. ``met`` needs an ``evidence_ref``; on an item that
    depends on legal interpretation it also needs ``counsel_reviewed``, or it is recorded as needs-counsel."""

    status: ChecklistStatus
    evidence_ref: str | None = None
    note: str | None = None
    counsel_reviewed: bool = False


class Deployment(_M):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str = ""
    path: DeployPath
    jurisdictions: list[Literal["EU", "SG", "US"]] = Field(default_factory=lambda: ["EU", "SG", "US"])
    model: ModelInfo = Field(default_factory=ModelInfo)
    hosting: Hosting
    serving: Serving = Field(default_factory=Serving)
    weights: Weights = Field(default_factory=Weights)
    data_flow: DataFlow = Field(default_factory=DataFlow)
    personal_data: PersonalData = Field(default_factory=PersonalData)
    retention: Retention = Field(default_factory=Retention)
    sub_processors: list[SubProcessor] = Field(default_factory=list)
    fine_tuning: FineTuning = Field(default_factory=FineTuning)
    requirements: Requirements = Field(default_factory=Requirements)
    operator: Operator = Field(default_factory=Operator)
    attestations: dict[str, Attestation] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _path_fields(self) -> Deployment:
        h = self.hosting
        if self.path == "bedrock" and h.bedrock_profile is None:
            raise ValueError(
                "path bedrock needs hosting.bedrock_profile (us | global): Bedrock GLM 5.3 is cross-Region only"
            )
        if self.path == "mistral-regional" and h.mistral_endpoint is None:
            raise ValueError("path mistral-regional needs hosting.mistral_endpoint (eu | us | global)")
        if self.path == "self-hosted" and (h.region is None or h.jurisdiction == "unknown"):
            raise ValueError("path self-hosted needs hosting.region and hosting.jurisdiction")
        return self


def load_deployment(path: Path) -> Deployment:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise DescriptorError(f"{path}: {e}") from e
    if not isinstance(raw, dict):
        raise DescriptorError(f"{path}: expected a mapping at the top level")
    try:
        return Deployment.model_validate(raw)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise DescriptorError(f"{path}: {msgs}") from e
