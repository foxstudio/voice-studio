"""Managed input, attempt manifest and final contracts for section review."""

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

from app.domains.video_localization.section_review import (
    AsrSectionReviewInput,
    AsrSectionReviewIssue,
    AsrSectionReviewQualitySummary,
    AsrSectionReviewSectionRun,
    AsrSectionReviewWarning,
)


ASR_SECTION_REVIEW_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-section-review-managed-input-v1"
)
ASR_SECTION_REVIEW_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-section-review-step-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrSectionReviewPreparedInputV1(BaseModel):
    """Exact upstream artifacts and runtime identity for review round one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-section-review-managed-input-v1"
    ] = ASR_SECTION_REVIEW_PREPARED_INPUT_SCHEMA_VERSION
    entity_artifact_fingerprint: str
    document_artifact_fingerprint: str
    profile_configuration_fingerprint: str
    behavior_fingerprint: str
    request: AsrSectionReviewInput

    @field_validator(
        "entity_artifact_fingerprint",
        "document_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "section-review prepared input")

    @model_validator(mode="after")
    def validate_round(self) -> "AsrSectionReviewPreparedInputV1":
        if self.request.round_index != 1:
            raise ValueError(
                "managed section review only supports round one"
            )
        return self


class AsrSectionReviewAttemptReferenceV1(BaseModel):
    """One durable known outcome in a section's attempt sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=256)
    section_id: str = Field(min_length=1, max_length=256)
    section_start_ordinal: int = Field(ge=1)
    core_segment_ids: tuple[str, ...] = Field(min_length=1)
    attempt: int = Field(ge=1, le=2)
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    status: Literal["success", "failed"]
    artifact_fingerprint: str | None = None
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @field_validator("input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "section-review attempt")

    @field_validator("artifact_fingerprint")
    @classmethod
    def validate_artifact_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "section-review attempt")

    @model_validator(mode="after")
    def validate_outcome(
        self,
    ) -> "AsrSectionReviewAttemptReferenceV1":
        if self.status == "success":
            if self.artifact_fingerprint is None or self.error_code:
                raise ValueError(
                    "successful section attempt requires only an artifact"
                )
        elif self.artifact_fingerprint is not None or not self.error_code:
            raise ValueError(
                "failed section attempt requires only an error code"
            )
        return self


class AsrSectionReviewStepOutputV1(BaseModel):
    """Deterministic section-review result plus full known-attempt manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-section-review-step-output-v1"
    ] = ASR_SECTION_REVIEW_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    status: Literal["completed", "partial", "failed"]
    profile_id: str = Field(min_length=1, max_length=128)
    model_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    prompt_version: Literal[
        "asr-section-review-v5"
    ] = "asr-section-review-v5"
    section_runs: tuple[AsrSectionReviewSectionRun, ...] = ()
    issues: tuple[AsrSectionReviewIssue, ...] = ()
    warnings: tuple[AsrSectionReviewWarning, ...] = ()
    attempts: tuple[AsrSectionReviewAttemptReferenceV1, ...] = ()
    duration_ms: int = Field(ge=0)
    quality_summary: AsrSectionReviewQualitySummary

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "section-review output")

    @model_validator(mode="after")
    def validate_attempt_identity(
        self,
    ) -> "AsrSectionReviewStepOutputV1":
        identities = [
            (item.section_id, item.attempt)
            for item in self.attempts
        ]
        if len(set(identities)) != len(identities):
            raise ValueError(
                "section-review attempts must be unique"
            )
        if len({item.step_id for item in self.attempts}) != len(
            self.attempts
        ):
            raise ValueError(
                "section-review step IDs must be unique"
            )
        if bool(self.model_id) != any(
            item.status == "success" for item in self.attempts
        ):
            raise ValueError(
                "section-review model identity does not match calls"
            )
        return self


def validate_final_against_input(
    prepared: AsrSectionReviewPreparedInputV1,
    final: AsrSectionReviewStepOutputV1,
) -> None:
    """Recompute section, issue, attempt and quality invariants."""

    request = prepared.request
    if final.profile_id != request.profile_id:
        raise ValueError("section-review final profile changed")
    section_ids = [item.section_id for item in request.sections]
    section_by_id = {
        item.section_id: item for item in request.sections
    }
    if len(set(section_ids)) != len(section_ids):
        raise ValueError("section-review section IDs are not unique")
    if [item.section_id for item in final.section_runs] != section_ids:
        raise ValueError(
            "section-review runs do not preserve section order"
        )
    attempts_by_section: dict[
        str,
        list[AsrSectionReviewAttemptReferenceV1],
    ] = {section_id: [] for section_id in section_ids}
    for attempt in final.attempts:
        section = section_by_id.get(attempt.section_id)
        if (
            section is None
            or attempt.section_start_ordinal
            != section.start_ordinal
            or attempt.call_id
            != f"section-review-r1-{section.section_id}"
        ):
            raise ValueError(
                "section-review attempt section identity changed"
            )
        expected_core_ids = tuple(
            item.segment_id
            for item in request.segments[
                section.start_ordinal - 1 : section.end_ordinal
            ]
        )
        if attempt.core_segment_ids != expected_core_ids:
            raise ValueError(
                "section-review attempt segment identity changed"
            )
        attempts_by_section[section.section_id].append(attempt)
    failed_sections: set[str] = set()
    for run in final.section_runs:
        attempts = sorted(
            attempts_by_section[run.section_id],
            key=lambda item: item.attempt,
        )
        if not attempts or [item.attempt for item in attempts] not in (
            [1],
            [1, 2],
        ):
            raise ValueError(
                "section-review attempt sequence is incomplete"
            )
        last = attempts[-1]
        expected_status = (
            "failed" if last.status == "failed" else "completed"
        )
        if run.status != expected_status:
            raise ValueError(
                "section-review run status does not match attempts"
            )
        expected_call_ids = [
            f"{item.call_id}-a{item.attempt:02d}"
            for item in attempts
            if item.status == "success"
        ]
        if run.llm_call_ids != expected_call_ids:
            raise ValueError(
                "section-review run call manifest changed"
            )
        if run.status == "failed":
            failed_sections.add(run.section_id)
            if (
                run.error_code != _runtime_error_code(
                    last.error_code
                )
                or not run.error_message
            ):
                raise ValueError(
                    "section-review failure detail is incomplete"
                )
        elif run.error_code or run.error_message:
            raise ValueError(
                "completed section cannot contain an error"
            )
    expected_status = (
        "failed"
        if len(failed_sections) == len(section_ids)
        else "partial"
        if failed_sections
        else "completed"
    )
    if final.status != expected_status:
        raise ValueError(
            "section-review final status cannot be recomputed"
        )
    failed_warning_ids = {
        item.section_id
        for item in final.warnings
        if item.code == "section_failed"
    }
    if failed_warning_ids != failed_sections:
        raise ValueError(
            "section-review failure warnings are incomplete"
        )
    segment_ids = [
        item.segment_id for item in request.segments
    ]
    if len(set(segment_ids)) != len(segment_ids):
        raise ValueError(
            "section-review source segment IDs are not unique"
        )
    segment_ordinals = {
        segment_id: index
        for index, segment_id in enumerate(segment_ids, start=1)
    }
    evidence_ids = {
        item.evidence_id for item in request.evidence
    }
    issue_ids: set[str] = set()
    for issue in final.issues:
        section = section_by_id.get(issue.section_id)
        if section is None or issue.issue_id in issue_ids:
            raise ValueError(
                "section-review issue identity is invalid"
            )
        issue_ids.add(issue.issue_id)
        target_ordinals = [
            segment_ordinals.get(segment_id)
            for segment_id in issue.target_segment_ids
        ]
        max_allowed = min(
            len(segment_ids),
            section.end_ordinal + 1,
        )
        if (
            any(value is None for value in target_ordinals)
            or target_ordinals[0] < section.start_ordinal
            or target_ordinals[-1] > max_allowed
            or not set(issue.evidence_source_ids) <= evidence_ids
        ):
            raise ValueError(
                "section-review issue references unlocked input"
            )
        if issue.patches and {
            item.segment_id for item in issue.patches
        } != set(issue.target_segment_ids):
            raise ValueError(
                "section-review patches do not match targets"
            )
    checked_count = len(section_ids) - len(failed_sections)
    adjacent_count = max(0, len(segment_ids) - 1)
    checked_adjacent = sum(
        max(
            0,
            min(section.end_ordinal, len(segment_ids) - 1)
            - section.start_ordinal
            + 1,
        )
        for section in request.sections
        if section.section_id not in failed_sections
    )
    quality = final.quality_summary
    sections_cover_all = (
        request.sections[0].start_ordinal == 1
        and all(
            right.start_ordinal == left.end_ordinal + 1
            for left, right in zip(
                request.sections,
                request.sections[1:],
            )
        )
        and request.sections[-1].end_ordinal == len(segment_ids)
    )
    expected_quality_status = (
        "failed"
        if final.status == "failed"
        else "warning"
        if final.status == "partial" or final.warnings
        else "passed"
    )
    if (
        quality.status != expected_quality_status
        or quality.section_count != len(section_ids)
        or quality.checked_section_count != checked_count
        or quality.issue_count != len(final.issues)
        or quality.sections_cover_all_segments != sections_cover_all
        or not quality.source_text_unchanged
        or quality.adjacent_boundary_count != adjacent_count
        or quality.checked_adjacent_boundary_count
        != checked_adjacent
        or quality.cross_segment_issue_count
        != sum(
            item.scope == "adjacent_segments"
            for item in final.issues
        )
        or quality.all_adjacent_boundaries_checked
        != (checked_adjacent == adjacent_count)
    ):
        raise ValueError(
            "section-review quality summary cannot be recomputed"
        )


def prepared_input_fingerprint(
    value: AsrSectionReviewPreparedInputV1,
) -> str:
    return hashlib.sha256(prepared_input_bytes(value)).hexdigest()


def prepared_input_bytes(
    value: AsrSectionReviewPreparedInputV1,
) -> bytes:
    return _canonical_bytes(value)


def step_output_bytes(
    value: AsrSectionReviewStepOutputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrSectionReviewPreparedInputV1:
    return _parse_model(
        content,
        AsrSectionReviewPreparedInputV1,
        label="section-review prepared input",
    )


def parse_step_output(
    content: bytes,
) -> AsrSectionReviewStepOutputV1:
    return _parse_model(
        content,
        AsrSectionReviewStepOutputV1,
        label="section-review step output",
    )


def _fingerprint(value: str, label: str) -> str:
    normalized = str(value).strip()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(
            f"{label} fingerprint must be lowercase SHA-256"
        )
    return normalized


def _runtime_error_code(value: str | None) -> str:
    normalized = str(value or "").strip()
    prefix = "VIDEO_LOCALIZATION_"
    if normalized.startswith(prefix):
        return normalized[len(prefix) :].casefold()
    return normalized.casefold() or "llm_request_failed"


def _canonical_bytes(value: BaseModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _parse_model(
    content: bytes,
    model: type[ModelT],
    *,
    label: str,
) -> ModelT:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    return model.model_validate(payload)


__all__ = [
    "ASR_SECTION_REVIEW_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_SECTION_REVIEW_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrSectionReviewAttemptReferenceV1",
    "AsrSectionReviewPreparedInputV1",
    "AsrSectionReviewStepOutputV1",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
    "validate_final_against_input",
]
