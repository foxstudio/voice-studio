"""Managed input, attempt manifest and final contracts for review decisions."""

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

from app.domains.video_localization.review_decisions import (
    AsrReviewDecisionsInput,
    AsrReviewDecisionsResult,
)
from app.schemas.video_localization_asr_review_decisions_step import (
    ReviewDecisionCallGroup,
)


ASR_REVIEW_DECISIONS_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-review-decisions-managed-input-v1"
)
ASR_REVIEW_DECISIONS_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-review-decisions-step-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrReviewDecisionsPreparedInputV1(BaseModel):
    """Exact section-review result and runtime identity for adjudication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-review-decisions-managed-input-v1"
    ] = ASR_REVIEW_DECISIONS_PREPARED_INPUT_SCHEMA_VERSION
    section_review_artifact_fingerprint: str
    profile_configuration_fingerprint: str | None = None
    behavior_fingerprint: str
    request: AsrReviewDecisionsInput

    @field_validator(
        "section_review_artifact_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_required_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "review-decisions input")

    @field_validator("profile_configuration_fingerprint")
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "review-decisions input")

    @model_validator(mode="after")
    def validate_provider_identity(
        self,
    ) -> "AsrReviewDecisionsPreparedInputV1":
        if bool(self.request.issues) != bool(
            self.profile_configuration_fingerprint
        ):
            raise ValueError(
                "review-decisions Provider identity is incomplete"
            )
        return self


class AsrReviewDecisionsAttemptReferenceV1(BaseModel):
    """One durable known outcome in a call group's attempt sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_group: ReviewDecisionCallGroup
    attempt: int = Field(ge=1, le=2)
    round_index: int = Field(ge=1, le=2)
    issue_ids: tuple[str, ...] = Field(min_length=1)
    primary_artifact_fingerprint: str | None = None
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
        return _fingerprint(value, "review-decisions attempt")

    @field_validator(
        "primary_artifact_fingerprint",
        "artifact_fingerprint",
    )
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "review-decisions attempt")

    @model_validator(mode="after")
    def validate_outcome(
        self,
    ) -> "AsrReviewDecisionsAttemptReferenceV1":
        if (
            self.call_group == "coverage"
        ) != bool(self.primary_artifact_fingerprint):
            raise ValueError(
                "coverage attempt lineage is incomplete"
            )
        if self.status == "success":
            if self.artifact_fingerprint is None or self.error_code:
                raise ValueError(
                    "successful attempt requires only an artifact"
                )
        elif self.artifact_fingerprint is not None or not self.error_code:
            raise ValueError(
                "failed attempt requires only an error code"
            )
        return self


class AsrReviewDecisionsStepOutputV1(BaseModel):
    """Deterministic result plus the full known-attempt manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-review-decisions-step-output-v1"
    ] = ASR_REVIEW_DECISIONS_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    result: AsrReviewDecisionsResult
    attempts: tuple[AsrReviewDecisionsAttemptReferenceV1, ...] = ()

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "review-decisions output")

    @model_validator(mode="after")
    def validate_attempt_identity(
        self,
    ) -> "AsrReviewDecisionsStepOutputV1":
        identities = [
            (item.call_group, item.attempt)
            for item in self.attempts
        ]
        if len(set(identities)) != len(identities):
            raise ValueError(
                "review-decisions attempts must be unique"
            )
        if len({item.step_id for item in self.attempts}) != len(
            self.attempts
        ):
            raise ValueError(
                "review-decisions step IDs must be unique"
            )
        return self


def validate_final_against_input(
    prepared: AsrReviewDecisionsPreparedInputV1,
    final: AsrReviewDecisionsStepOutputV1,
) -> None:
    """Recompute input, attempt, transcript and result invariants."""

    request = prepared.request
    result = final.result
    if result.input != request or result.profile_id != request.profile_id:
        raise ValueError("review-decisions final input changed")
    expected_issue_ids = tuple(item.issue_id for item in request.issues)
    if len(set(expected_issue_ids)) != len(expected_issue_ids):
        raise ValueError("review-decisions issue IDs are not unique")
    by_group: dict[
        ReviewDecisionCallGroup,
        list[AsrReviewDecisionsAttemptReferenceV1],
    ] = {"primary": [], "coverage": []}
    for item in final.attempts:
        if item.round_index != request.round_index:
            raise ValueError("review-decisions round changed")
        if item.call_group == "primary":
            if item.issue_ids != expected_issue_ids:
                raise ValueError(
                    "primary review-decisions issue coverage changed"
                )
        elif not set(item.issue_ids) <= set(expected_issue_ids):
            raise ValueError(
                "coverage review-decisions contains unknown issues"
            )
        by_group[item.call_group].append(item)
    for group, attempts in by_group.items():
        if not attempts:
            continue
        attempts.sort(key=lambda item: item.attempt)
        if [item.attempt for item in attempts] not in ([1], [1, 2]):
            raise ValueError(
                f"{group} review-decisions attempts are incomplete"
            )
        if attempts[-1].status != "success":
            raise ValueError(
                "final review-decisions result contains a failed call group"
            )
        if any(item.status == "success" for item in attempts[:-1]):
            raise ValueError(
                "review-decisions retried after a successful attempt"
            )
    if request.issues:
        if not by_group["primary"]:
            raise ValueError(
                "review-decisions primary call is missing"
            )
        success_attempts = [
            item
            for item in final.attempts
            if item.status == "success"
        ]
        expected_call_ids = {
            (
                f"review-decisions-r{item.round_index}-"
                f"{item.call_group}-a{item.attempt:02d}"
            )
            for item in success_attempts
        }
        if {item.call_id for item in result.llm_calls} != expected_call_ids:
            raise ValueError(
                "review-decisions call records do not match manifest"
            )
        if result.model_id is None:
            raise ValueError(
                "review-decisions model identity is missing"
            )
    elif final.attempts or result.llm_calls or result.model_id:
        raise ValueError(
            "zero-issue review-decisions cannot contain model output"
        )
    source = request.segments
    updated = result.updated_segments
    if len(source) != len(updated):
        raise ValueError("review-decisions segment coverage changed")
    for before, after in zip(source, updated, strict=True):
        if (
            before.segment_id != after.segment_id
            or before.start_ms != after.start_ms
            or before.end_ms != after.end_ms
            or before.raw_text != after.raw_text
            or before.speaker_cluster_id != after.speaker_cluster_id
        ):
            raise ValueError(
                "review-decisions changed source transcript identity"
            )
    issue_ids = set(expected_issue_ids)
    decision_issue_ids = [
        item.issue_id for item in result.decisions
    ]
    if (
        len(decision_issue_ids) != len(set(decision_issue_ids))
        or set(decision_issue_ids) != issue_ids
    ):
        raise ValueError(
            "review-decisions decision coverage changed"
        )
    segment_ids = {item.segment_id for item in source}
    if any(
        item.segment_id not in segment_ids
        or item.source_task_id
        != f"review_decisions_r{request.round_index}"
        or item.round_index != request.round_index
        for item in result.changes
    ):
        raise ValueError(
            "review-decisions change references an unknown segment"
        )
    if (
        len(result.cumulative_locked_changes)
        != len(request.locked_changes) + len(result.changes)
        or result.cumulative_locked_changes[
            : len(request.locked_changes)
        ]
        != request.locked_changes
    ):
        raise ValueError(
            "review-decisions locked-change lineage changed"
        )
    for change, locked in zip(
        result.changes,
        result.cumulative_locked_changes[
            len(request.locked_changes) :
        ],
        strict=True,
    ):
        if (
            locked.segment_id != change.segment_id
            or locked.before != change.before
            or locked.after != change.after
            or locked.reason != change.reason
            or locked.confidence != change.confidence
            or locked.evidence_source_ids
            != change.evidence_source_ids
            or locked.issue_id != change.issue_id
            or locked.source_task_id != change.source_task_id
            or locked.round_index != change.round_index
        ):
            raise ValueError(
                "review-decisions locked change does not match result"
            )
    quality = result.quality_summary
    unresolved = sum(
        item.outcome in {"needs_confirmation", "invalid"}
        for item in result.decisions
    )
    applied_issue_count = len(
        {
            item.issue_id
            for item in result.decisions
            if item.outcome == "applied"
        }
    )
    upstream_complete = request.upstream_status == "completed"
    expected_status = (
        "partial"
        if unresolved or not upstream_complete
        else "completed"
    )
    if (
        result.status != expected_status
        or quality.status
        != (
            "warning"
            if expected_status == "partial"
            else "passed"
        )
        or quality.issue_count != len(request.issues)
        or quality.decided_issue_count != len(result.decisions)
        or quality.applied_change_count != len(result.changes)
        or quality.applied_issue_count != applied_issue_count
        or quality.applied_patch_count != len(result.changes)
        or quality.unresolved_issue_count != unresolved
        or quality.upstream_review_complete != upstream_complete
        or not quality.segment_ids_unchanged
        or not quality.source_timing_unchanged
        or not quality.only_supplied_issues_considered
        or not quality.locked_changes_preserved
        or not quality.atomic_patch_sets_preserved
    ):
        raise ValueError(
            "review-decisions quality invariants are not satisfied"
        )


def prepared_input_fingerprint(
    value: AsrReviewDecisionsPreparedInputV1,
) -> str:
    return hashlib.sha256(prepared_input_bytes(value)).hexdigest()


def prepared_input_bytes(
    value: AsrReviewDecisionsPreparedInputV1,
) -> bytes:
    return _canonical_bytes(value)


def step_output_bytes(
    value: AsrReviewDecisionsStepOutputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrReviewDecisionsPreparedInputV1:
    return _parse_model(
        content,
        AsrReviewDecisionsPreparedInputV1,
        label="review-decisions prepared input",
    )


def parse_step_output(
    content: bytes,
) -> AsrReviewDecisionsStepOutputV1:
    return _parse_model(
        content,
        AsrReviewDecisionsStepOutputV1,
        label="review-decisions step output",
    )


def _fingerprint(value: str, label: str) -> str:
    normalized = str(value).strip()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(f"{label} fingerprint must be lowercase SHA-256")
    return normalized


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_bytes(value: BaseModel) -> bytes:
    return _canonical_json_bytes(value.model_dump(mode="json"))


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
    "ASR_REVIEW_DECISIONS_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_REVIEW_DECISIONS_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrReviewDecisionsAttemptReferenceV1",
    "AsrReviewDecisionsPreparedInputV1",
    "AsrReviewDecisionsStepOutputV1",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
    "validate_final_against_input",
]
