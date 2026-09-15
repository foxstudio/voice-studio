"""Versioned, explicit semantic decisions for the shared TTS recovery chain."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DubbingRecoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["dubbing-recovery-decision-v1"] = "dubbing-recovery-decision-v1"
    recovery_id: str = Field(pattern=r"^[0-9a-f]{12}$")
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    stage: Literal["semantic_phrases", "nearby_reference"]
    phrases: list[str] = Field(min_length=1, max_length=12)
    reference_cue_ids: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_decision(self):
        if any(not text.strip() for text in self.phrases):
            raise ValueError("recovery phrases must not be empty")
        if self.stage == "semantic_phrases" and (len(self.phrases) < 2 or self.reference_cue_ids):
            raise ValueError("semantic recovery needs at least two phrases and preserves reference")
        if self.stage == "nearby_reference" and not self.reference_cue_ids:
            raise ValueError("nearby reference recovery requires explicit source cues")
        if len(set(self.reference_cue_ids)) != len(self.reference_cue_ids):
            raise ValueError("reference cues must be unique")
        return self
