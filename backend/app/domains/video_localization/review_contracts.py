"""Shared typed contracts for transcript review stages.

This module contains data only. Atomic review modules may depend on it without
importing one another or the legacy workflow orchestrator.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AsrTranscriptEditPatch(BaseModel):
    """One grounded text edit inside a transcript issue.

    A single review issue may own several patches when one grammatical repair
    crosses an ASR segment boundary. The decision stage accepts or rejects the
    complete patch set atomically.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    current_excerpt: str = Field(min_length=1)
    proposed_replacement: str = ""


class AsrLockedTranscriptChange(BaseModel):
    """One accepted transcript change protected from later reversal."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    before: str = ""
    after: str = Field(min_length=1)
    reason: str = ""
    confidence: float = Field(default=1, ge=0, le=1)
    evidence_source_ids: list[str] = Field(default_factory=list)
    issue_id: str | None = None
    source_task_id: str | None = None
    round_index: int | None = Field(default=None, ge=1, le=2)
