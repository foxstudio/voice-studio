"""Versioned localization requirements resolved before model-driven work."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CATALOG_PATH = (
    Path(__file__).resolve().parent
    / "config"
    / "localization_requirements.json"
)


class LocalizationDeliverables(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_subtitles: bool
    spoken_script: bool
    rendered_audio: bool
    label: str = Field(min_length=1)


class LocalizationExpressionRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    preserve: list[str] = Field(min_length=1)
    adapt: list[str] = Field(min_length=1)
    avoid: list[str] = Field(min_length=1)


class LocalizationTimingRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    align_by: Literal["semantic_window"]
    source_cue_alignment_required: bool
    overlap_next_semantic_window_allowed: bool


class LocalizationSourceReferenceRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asr_document_brief_usage: Literal["reference_only"]
    full_source_reanalysis_required: bool
    description: str = Field(min_length=1)


class LocalizationSubtitleRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class LocalizationRequirementsProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    label: str = Field(min_length=1)
    target_language: str = Field(min_length=1)
    target_locale: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    deliverables: LocalizationDeliverables
    expression: LocalizationExpressionRequirements
    timing: LocalizationTimingRequirements
    source_reference: LocalizationSourceReferenceRequirements
    subtitle: LocalizationSubtitleRequirements

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LocalizationRequirementsCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["localization-requirements-catalog-v1"]
    default_profile_id: str = Field(min_length=1)
    profiles: list[LocalizationRequirementsProfile] = Field(min_length=1)


@lru_cache(maxsize=1)
def load_localization_requirements_catalog() -> (
    LocalizationRequirementsCatalog
):
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog = LocalizationRequirementsCatalog.model_validate(payload)
    profile_ids = [item.profile_id for item in catalog.profiles]
    if len(set(profile_ids)) != len(profile_ids):
        raise ValueError("本土化要求配置包含重复的 profile_id。")
    if catalog.default_profile_id not in profile_ids:
        raise ValueError("本土化要求配置的默认 profile_id 不存在。")
    return catalog


def resolve_localization_requirements(
    profile_id: str | None = None,
) -> LocalizationRequirementsProfile:
    catalog = load_localization_requirements_catalog()
    selected_id = str(profile_id or catalog.default_profile_id).strip()
    for profile in catalog.profiles:
        if profile.profile_id == selected_id:
            return profile.model_copy(deep=True)
    raise ValueError(f"没有找到本土化要求配置：{selected_id}")


__all__ = [
    "LocalizationDeliverables",
    "LocalizationExpressionRequirements",
    "LocalizationRequirementsCatalog",
    "LocalizationRequirementsProfile",
    "LocalizationSourceReferenceRequirements",
    "LocalizationSubtitleRequirements",
    "LocalizationTimingRequirements",
    "load_localization_requirements_catalog",
    "resolve_localization_requirements",
]
