"""Dataset cards: source, pinned revision, checksums, license (with where and when it was verified), attribution.

A card without a license, a verification date, an attribution or a pinned sha256 per file is invalid;
``tests/test_workbench_sets.py`` fails on any shipped card that is, and ``glmkit demo cards`` exits 1.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CARDS_DIR = Path(__file__).resolve().parent / "data" / "cards"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    path: str
    sha256: str
    bytes: int = Field(gt=0)
    member: str | None = Field(default=None, description="Member to read inside a zip archive")

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not SHA256.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("source url must be https")
        return v


class Source(BaseModel):
    model_config = ConfigDict(extra="allow")
    dataset: str
    url: str
    revision: str = Field(min_length=7, description="Commit the files are pinned to")
    files: list[SourceFile] = Field(min_length=1)


class License(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spdx: str = Field(min_length=2)
    name: str = Field(min_length=2)
    url: str = Field(min_length=8)
    verified_at: str
    verified_via: list[str] = Field(min_length=1)
    notes: str = ""

    @field_validator("verified_at")
    @classmethod
    def _date(cls, v: str) -> str:
        if not DATE.match(v):
            raise ValueError("verified_at must be YYYY-MM-DD")
        return v


class DatasetCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    summary: str
    task_types: list[str] = Field(min_length=1)
    items: int = Field(ge=50, le=100, description="Items in the full set (50-100)")
    sampling: str
    graders: dict[str, dict[str, Any]]
    source: Source
    license: License
    attribution: str = Field(min_length=20)
    modifications: str
    standin: str


def parse_card(data: Any) -> DatasetCard:
    return DatasetCard.model_validate(data)


def problems(path: Path) -> list[str]:
    """Why this card file is not acceptable (empty list = fine). Missing license is reported by name."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [f"{path.name}: not valid YAML: {e}"]
    if not isinstance(data, dict):
        return [f"{path.name}: not a mapping"]
    out = []
    if not data.get("license"):
        out.append(f"{path.name}: no license stated")
    try:
        parse_card(data)
    except ValidationError as e:
        out += [f"{path.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
    return out


def card_files(directory: Path | None = None) -> list[Path]:
    return sorted((directory or CARDS_DIR).glob("*.yaml"))


def check_cards(directory: Path | None = None) -> list[str]:
    files = card_files(directory)
    if not files:
        return [f"no dataset cards in {directory or CARDS_DIR}"]
    return [p for f in files for p in problems(f)]


def load_card(set_id: str, directory: Path | None = None) -> DatasetCard:
    path = (directory or CARDS_DIR) / f"{set_id}.yaml"
    if not path.is_file():
        raise KeyError(f"unknown demo set {set_id!r}; known: {', '.join(set_ids())}")
    return parse_card(yaml.safe_load(path.read_text(encoding="utf-8")))


def set_ids() -> list[str]:
    return [p.stem for p in card_files()]
