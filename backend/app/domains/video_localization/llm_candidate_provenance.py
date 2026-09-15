"""Data-only provenance for verified recovered candidates with missing telemetry."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RecoveredLlmCandidateProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1, max_length=200)
    attempt: int = Field(ge=0)
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_evidence_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    telemetry: Literal["unavailable"] = "unavailable"


class DevelopmentCandidateIdentityBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1, max_length=200)
    attempt: int = Field(ge=0)
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class DevelopmentCandidateImport(BaseModel):
    """Explicit complete evidence plus an independently preserved identity."""

    model_config = ConfigDict(extra="forbid")

    completeness: Literal["complete"]
    batch_id: str = Field(min_length=1, max_length=200)
    attempt: int = Field(ge=0)
    candidate: dict[str, Any]
    baseline: DevelopmentCandidateIdentityBaseline
    source_evidence_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class DevelopmentCandidateReceipt(BaseModel):
    """Current development-session receipt, never an old call checkpoint."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["development-candidate-receipt-v1"] = "development-candidate-receipt-v1"
    target_project_id: str = Field(min_length=1)
    target_session_id: str = Field(min_length=1)
    evidence: DevelopmentCandidateImport
    provenance: RecoveredLlmCandidateProvenance
