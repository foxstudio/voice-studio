"""Typed, evidence-bound repair contract for one bad raw-ASR segment."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ASR_SOURCE_REPAIR_CONTRACT_VERSION = "asr-source-repair-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AsrSourceRepairEvidence(_StrictModel):
    source_audio_start_ms: int = Field(ge=0)
    source_audio_end_ms: int = Field(gt=0)
    # An independent check may observe only silence; that is still evidence and
    # must not force a fabricated placeholder into the persisted receipt.
    observed_text: str = Field(max_length=4_000)
    engine_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_range(self) -> "AsrSourceRepairEvidence":
        if self.source_audio_end_ms <= self.source_audio_start_ms:
            raise ValueError("repair evidence end must be after its start")
        return self


class AsrSourceRepairRequest(_StrictModel):
    """A deletion-only correction, never a free-form transcript rewrite."""

    contract_version: Literal["asr-source-repair-v1"] = ASR_SOURCE_REPAIR_CONTRACT_VERSION
    expected_project_revision: str = Field(min_length=1, max_length=128)
    transcription_revision_id: str = Field(min_length=1, max_length=128)
    audio_sha256: str = Field(min_length=64, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    excluded_segment_ids: list[str] = Field(min_length=1, max_length=64)
    evidence: AsrSourceRepairEvidence

    @field_validator("audio_sha256")
    @classmethod
    def validate_audio_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("audio_sha256 must be a lowercase SHA-256")
        return value

    @field_validator("excluded_segment_ids")
    @classmethod
    def validate_segment_ids(cls, values: list[str]) -> list[str]:
        if any(not value for value in values):
            raise ValueError("excluded segment IDs must not be empty")
        if len(values) != len(set(values)):
            raise ValueError("excluded segment IDs must be unique")
        return values

    def fingerprint(self) -> str:
        """Stable idempotency identity; persistence owns request-ID policy."""

        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class AsrSourceRepairDeletedSegment(_StrictModel):
    segment_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    raw_text: str
    corrected_text: str | None = None


class AsrSourceRepairDeletedWord(_StrictModel):
    word_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class AsrSourceRepairDeletedCue(_StrictModel):
    cue_id: str = Field(min_length=1)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    en_subtitle_text: str | None = None
    source_word_ids: list[str] = Field(default_factory=list)


class AsrSourceRepairReceipt(_StrictModel):
    """Minimal immutable before-image for one deletion-only repair."""

    contract_version: Literal["asr-source-repair-v1"] = ASR_SOURCE_REPAIR_CONTRACT_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    request_fingerprint: str = ""
    before_revision_id: str = Field(min_length=1)
    after_revision_id: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=64, max_length=64)
    evidence: AsrSourceRepairEvidence
    deleted_segments: list[AsrSourceRepairDeletedSegment] = Field(min_length=1)
    deleted_words: list[AsrSourceRepairDeletedWord] = Field(default_factory=list)
    deleted_cues: list[AsrSourceRepairDeletedCue] = Field(default_factory=list)
    updated_cue_ids: list[str] = Field(default_factory=list)


__all__ = [
    "ASR_SOURCE_REPAIR_CONTRACT_VERSION",
    "AsrSourceRepairDeletedCue",
    "AsrSourceRepairDeletedSegment",
    "AsrSourceRepairDeletedWord",
    "AsrSourceRepairEvidence",
    "AsrSourceRepairReceipt",
    "AsrSourceRepairRequest",
]
