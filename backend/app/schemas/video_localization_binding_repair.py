"""Public request contract for a bounded localized-track binding repair.

This module deliberately does not import application models: it is used at the
transport boundary and must remain free of domain-model import cycles.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LocalizedSubtitleBindingRepair(_StrictContract):
    subtitle_id: str = Field(min_length=1)
    source_word_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_word_ids(self):
        if len(self.source_word_ids) != len(set(self.source_word_ids)):
            raise ValueError("source_word_ids must be unique per subtitle")
        return self


class BindingRepairRequest(_StrictContract):
    schema_version: Literal["localized-track-binding-repair-v1"] = (
        "localized-track-binding-repair-v1"
    )
    expected_project_revision: str = Field(min_length=1)
    transcription_revision_id: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=64, max_length=64)
    bindings: list[LocalizedSubtitleBindingRepair] = Field(min_length=2)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique_subtitle_ids(self):
        ids = [item.subtitle_id for item in self.bindings]
        if len(ids) != len(set(ids)):
            raise ValueError("bindings must contain unique subtitle IDs")
        return self


class BindingRepairReceipt(_StrictContract):
    """Persisted evidence for one non-idempotent source-binding correction."""

    request: BindingRepairRequest
    changed_subtitle_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_changed_ids(self):
        if len(self.changed_subtitle_ids) != len(set(self.changed_subtitle_ids)):
            raise ValueError("changed_subtitle_ids must be unique")
        if any(item not in {binding.subtitle_id for binding in self.request.bindings} for item in self.changed_subtitle_ids):
            raise ValueError("changed_subtitle_ids must belong to request bindings")
        return self
