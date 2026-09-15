"""Data-only contracts for unresolved ASR text, not proposed edits."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AsrReviewDecisionWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["missing_decision", "invalid_decision", "needs_confirmation", "unsafe_change", "protected_change"]
    issue_id: str = ""
    segment_id: str = ""
    excerpt: str = ""
    message: str = Field(min_length=1)


class AsrUnconfirmedTextSpan(BaseModel):
    """An uncertainty attached to one frozen segment's text, not an edit."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-unconfirmed-text-span-v1"] = "asr-unconfirmed-text-span-v1"
    issue_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    segment_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_task_id: str = Field(min_length=1)
    match_policy: Literal["unique", "all_occurrences"] = "unique"
