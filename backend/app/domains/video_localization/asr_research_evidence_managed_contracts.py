"""Managed input, manifest and final contracts for research evidence."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domains.video_localization.research_evidence import (
    AsrResearchCandidateAssessment,
    AsrResearchEvidenceItem,
    AsrResearchEvidenceQualitySummary,
    AsrResearchEvidenceStageTiming,
    AsrResearchQueryRun,
    AsrResearchRound,
    ResearchEvidenceInput,
    ResearchStopReason,
    ResearchTaskStatus,
)


ASR_RESEARCH_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-research-evidence-managed-input-v1"
)
ASR_RESEARCH_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-research-evidence-step-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrResearchEvidencePreparedInputV1(BaseModel):
    """Secret-free research request rebuilt from managed upstreams."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-research-evidence-managed-input-v1"
    ] = ASR_RESEARCH_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION
    document_artifact_fingerprint: str
    visual_artifact_fingerprint: str | None = None
    profile_configuration_fingerprint: str | None = None
    search_configuration_fingerprint: str | None = None
    behavior_fingerprint: str
    request: ResearchEvidenceInput

    @field_validator(
        "document_artifact_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_required_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "research prepared input")

    @field_validator(
        "visual_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "search_configuration_fingerprint",
    )
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "research prepared input")

    @model_validator(mode="after")
    def validate_locked_inputs(
        self,
    ) -> "AsrResearchEvidencePreparedInputV1":
        is_visual = (
            self.request.contract_version
            == "asr-research-evidence-input-v2"
        )
        if is_visual != bool(self.visual_artifact_fingerprint):
            raise ValueError(
                "research visual input fingerprint is incomplete"
            )
        has_candidates = bool(self.request.candidates)
        if has_candidates != bool(
            self.profile_configuration_fingerprint
        ) or has_candidates != bool(
            self.search_configuration_fingerprint
        ):
            raise ValueError(
                "research Provider identity must match candidate work"
            )
        return self


class AsrResearchSearchReferenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=180)
    round_index: int = Field(ge=1, le=3)
    candidate_id: str = Field(min_length=1, max_length=128)
    search_kind: Literal["primary", "general_fallback"]
    attempt: int = Field(ge=1, le=2)
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    artifact_fingerprint: str

    @field_validator("input_fingerprint", "artifact_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "research search reference")


class AsrResearchCallReferenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=180)
    purpose: Literal["query_rewrite", "evidence_assessment"]
    round_index: int = Field(ge=1, le=3)
    candidate_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=12,
    )
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    artifact_fingerprint: str

    @field_validator("input_fingerprint", "artifact_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "research call reference")


class AsrResearchEvidenceStepOutputV1(BaseModel):
    """Deterministic normalized result plus exact external-call manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-research-evidence-step-output-v1"
    ] = ASR_RESEARCH_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    status: ResearchTaskStatus
    stop_reason: ResearchStopReason
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    model_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    prompt_version: Literal[
        "asr-research-evidence-v1"
    ] = "asr-research-evidence-v1"
    query_runs: tuple[AsrResearchQueryRun, ...] = ()
    evidence: tuple[AsrResearchEvidenceItem, ...] = ()
    rounds: tuple[AsrResearchRound, ...] = ()
    supported_candidate_ids: tuple[str, ...] = ()
    unresolved_candidate_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    stage_timing: AsrResearchEvidenceStageTiming
    quality_summary: AsrResearchEvidenceQualitySummary
    searches: tuple[AsrResearchSearchReferenceV1, ...] = ()
    calls: tuple[AsrResearchCallReferenceV1, ...] = ()

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "research final output")

    @model_validator(mode="after")
    def validate_result_manifest(
        self,
    ) -> "AsrResearchEvidenceStepOutputV1":
        query_ids = [item.query_run_id for item in self.query_runs]
        evidence_ids = [item.evidence_id for item in self.evidence]
        search_ids = [item.request_id for item in self.searches]
        call_ids = [item.call_id for item in self.calls]
        if (
            len(set(query_ids)) != len(query_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
            or len(set(search_ids)) != len(search_ids)
            or len(set(call_ids)) != len(call_ids)
        ):
            raise ValueError(
                "research final manifest contains duplicate identities"
            )
        query_by_id = {
            item.query_run_id: item for item in self.query_runs
        }
        if any(
            item.query_run_id not in query_by_id
            or query_by_id[item.query_run_id].candidate_id
            != item.candidate_id
            for item in self.evidence
        ):
            raise ValueError(
                "research evidence references an invalid query run"
            )
        expected_rounds = tuple(range(1, len(self.rounds) + 1))
        if tuple(item.round_index for item in self.rounds) != expected_rounds:
            raise ValueError("research rounds must be continuous")
        for round_result in self.rounds:
            round_queries = {
                item.query_run_id
                for item in self.query_runs
                if item.round_index == round_result.round_index
            }
            if set(round_result.query_run_ids) != round_queries:
                raise ValueError(
                    "research round query manifest is incomplete"
                )
            if any(
                evidence_id not in set(evidence_ids)
                for evidence_id in round_result.evidence_ids
            ):
                raise ValueError(
                    "research round evidence manifest is invalid"
                )
        if (
            self.quality_summary.total_query_count
            != len(self.query_runs)
            or self.quality_summary.evidence_count
            != len(self.evidence)
            or self.quality_summary.round_count
            != len(self.rounds)
            or self.quality_summary.supported_candidate_count
            != len(self.supported_candidate_ids)
            or self.quality_summary.unresolved_candidate_count
            != len(self.unresolved_candidate_ids)
        ):
            raise ValueError(
                "research quality counts do not match final result"
            )
        if set(self.supported_candidate_ids) & set(
            self.unresolved_candidate_ids
        ):
            raise ValueError(
                "research candidate outcomes must be disjoint"
            )
        return self


def prepared_input_fingerprint(
    value: AsrResearchEvidencePreparedInputV1,
) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def prepared_input_bytes(
    value: AsrResearchEvidencePreparedInputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrResearchEvidencePreparedInputV1:
    return _parse_model(
        content,
        AsrResearchEvidencePreparedInputV1,
        label="research prepared input",
    )


def step_output_bytes(
    value: AsrResearchEvidenceStepOutputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_step_output(
    content: bytes,
) -> AsrResearchEvidenceStepOutputV1:
    return _parse_model(
        content,
        AsrResearchEvidenceStepOutputV1,
        label="research final output",
    )


def _fingerprint(value: str, label: str) -> str:
    normalized = str(value).strip()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(f"{label} fingerprint must be lowercase SHA-256")
    return normalized


def _parse_model(
    content: bytes,
    model_type: type[ModelT],
    *,
    label: str,
) -> ModelT:
    if not isinstance(content, bytes) or not content:
        raise ValueError(f"{label} must be non-empty bytes")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return model_type.model_validate(payload)


def _canonical_bytes(value: BaseModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "ASR_RESEARCH_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_RESEARCH_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrResearchCallReferenceV1",
    "AsrResearchEvidencePreparedInputV1",
    "AsrResearchEvidenceStepOutputV1",
    "AsrResearchSearchReferenceV1",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
]
