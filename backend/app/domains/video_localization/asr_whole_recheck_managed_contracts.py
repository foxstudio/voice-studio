"""Managed input, attempt manifest and final contracts for whole recheck."""

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

from app.domains.video_localization.whole_recheck import (
    AsrWholeRecheckInput,
    AsrWholeRecheckResult,
)


ASR_WHOLE_RECHECK_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-whole-recheck-managed-input-v1"
)
ASR_WHOLE_RECHECK_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-whole-recheck-step-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrWholeRecheckPreparedInputV1(BaseModel):
    """Exact upstream results and runtime identity for one recheck."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-whole-recheck-managed-input-v1"
    ] = ASR_WHOLE_RECHECK_PREPARED_INPUT_SCHEMA_VERSION
    review_decisions_artifact_fingerprint: str
    document_understanding_artifact_fingerprint: str
    profile_configuration_fingerprint: str
    behavior_fingerprint: str
    request: AsrWholeRecheckInput

    @field_validator(
        "review_decisions_artifact_fingerprint",
        "document_understanding_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "whole-recheck input")


class AsrWholeRecheckAttemptReferenceV1(BaseModel):
    """One durable known outcome in the attempt sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int = Field(ge=1, le=2)
    round_index: int = Field(ge=1, le=2)
    decision_ids: tuple[str, ...] = ()
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    status: Literal["success", "failed"]
    artifact_fingerprint: str | None = None
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @field_validator(
        "input_fingerprint",
        "artifact_fingerprint",
    )
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "whole-recheck attempt")

    @model_validator(mode="after")
    def validate_outcome(
        self,
    ) -> "AsrWholeRecheckAttemptReferenceV1":
        if self.status == "success":
            if self.artifact_fingerprint is None or self.error_code:
                raise ValueError(
                    "successful attempt requires only an artifact"
                )
        elif self.artifact_fingerprint is not None or not self.error_code:
            raise ValueError("failed attempt requires only an error code")
        return self


class AsrWholeRecheckStepOutputV1(BaseModel):
    """Deterministic result plus the complete known-attempt manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-whole-recheck-step-output-v1"
    ] = ASR_WHOLE_RECHECK_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    result: AsrWholeRecheckResult
    attempts: tuple[AsrWholeRecheckAttemptReferenceV1, ...]

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "whole-recheck output")

    @model_validator(mode="after")
    def validate_attempt_identity(
        self,
    ) -> "AsrWholeRecheckStepOutputV1":
        attempts = [item.attempt for item in self.attempts]
        if not attempts:
            if self.result.llm_calls or self.result.model_id is not None:
                raise ValueError(
                    "local whole-recheck cannot contain provider calls"
                )
            return self
        if attempts not in ([1], [1, 2]):
            raise ValueError("whole-recheck attempts are incomplete")
        if len({item.step_id for item in self.attempts}) != len(
            self.attempts
        ):
            raise ValueError("whole-recheck step IDs must be unique")
        if self.attempts[-1].status != "success":
            raise ValueError(
                "final whole-recheck requires a successful attempt"
            )
        if any(
            item.status == "success" for item in self.attempts[:-1]
        ):
            raise ValueError(
                "whole-recheck retried after a successful attempt"
            )
        return self


def validate_final_against_input(
    prepared: AsrWholeRecheckPreparedInputV1,
    final: AsrWholeRecheckStepOutputV1,
) -> None:
    """Recompute attempt, input and read-only result invariants."""

    request = prepared.request
    result = final.result
    if result.input != request or result.profile_id != request.profile_id:
        raise ValueError("whole-recheck final input changed")
    expected_decision_ids = tuple(
        item.issue_id for item in request.decisions
    )
    for item in final.attempts:
        if (
            item.round_index != request.round_index
            or item.decision_ids != expected_decision_ids
        ):
            raise ValueError("whole-recheck attempt identity changed")
    successful = [
        item for item in final.attempts if item.status == "success"
    ]
    expected_call_ids = [
        (
            f"whole-recheck-r{item.round_index}-"
            f"a{item.attempt:02d}"
        )
        for item in successful
    ]
    if [item.call_id for item in result.llm_calls] != expected_call_ids:
        raise ValueError("whole-recheck call manifest changed")
    if any(
        item.purpose != "whole_recheck"
        or item.round_index != request.round_index
        or tuple(item.candidate_ids) != expected_decision_ids
        for item in result.llm_calls
    ):
        raise ValueError("whole-recheck call observability changed")
    if result.model_id != (
        result.llm_calls[0].model_id if result.llm_calls else None
    ):
        raise ValueError("whole-recheck model identity changed")
    if result.duration_ms < sum(
        item.duration_ms for item in result.llm_calls
    ):
        raise ValueError("whole-recheck duration is inconsistent")
    input_segments = request.segments
    if (
        result.quality_summary.segment_count != len(input_segments)
        or not result.quality_summary.source_text_unchanged
        or not result.quality_summary.segment_ids_unchanged
        or not result.quality_summary.source_timing_unchanged
    ):
        raise ValueError("whole-recheck read-only quality changed")
    segment_ids = [item.segment_id for item in input_segments]
    if len(set(segment_ids)) != len(segment_ids):
        raise ValueError("whole-recheck segment identity changed")
    known_segment_ids = set(segment_ids)
    if not all(
        (
            item.segment_id is None
            or item.segment_id in known_segment_ids
        )
        and all(
            segment_id in known_segment_ids
            for segment_id in item.target_segment_ids
        )
        for item in result.unresolved_items
    ):
        raise ValueError("whole-recheck unresolved references changed")
    sections_cover = _sections_cover_all(
        result.next_sections,
        segment_ids,
    )
    if (
        result.quality_summary.next_sections_cover_all_segments
        != sections_cover
        or result.quality_summary
        .unresolved_items_reference_known_segments
        is not True
    ):
        raise ValueError("whole-recheck derived quality changed")
    if result.passed:
        if (
            result.status != "completed"
            or result.next_action != "finish"
            or result.next_sections
            or result.quality_summary.status != "passed"
        ):
            raise ValueError("whole-recheck finish state changed")
    else:
        expected_action = (
            "review_next_round"
            if result.next_sections
            else "manual_review"
        )
        if (
            result.status != "partial"
            or result.next_action != expected_action
            or result.quality_summary.status != "warning"
        ):
            raise ValueError("whole-recheck continuation state changed")
    if request.upstream_status != "completed" and result.passed:
        raise ValueError("partial upstream cannot pass whole recheck")
    if request.round_index >= 2 and result.next_sections:
        raise ValueError("targeted closure cannot schedule another review")


def prepared_input_bytes(
    value: AsrWholeRecheckPreparedInputV1,
) -> bytes:
    return _canonical_bytes(value)


def prepared_input_fingerprint(
    value: AsrWholeRecheckPreparedInputV1,
) -> str:
    return hashlib.sha256(prepared_input_bytes(value)).hexdigest()


def step_output_bytes(
    value: AsrWholeRecheckStepOutputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrWholeRecheckPreparedInputV1:
    return _parse_model(
        content,
        AsrWholeRecheckPreparedInputV1,
        label="whole-recheck prepared input",
    )


def parse_step_output(
    content: bytes,
) -> AsrWholeRecheckStepOutputV1:
    return _parse_model(
        content,
        AsrWholeRecheckStepOutputV1,
        label="whole-recheck step output",
    )


def _sections_cover_all(
    sections,
    segment_ids: list[str],
) -> bool:
    if not sections:
        return True
    expected_start = 1
    for item in sections:
        if (
            item.start_ordinal != expected_start
            or item.end_ordinal < item.start_ordinal
            or item.end_ordinal > len(segment_ids)
            or item.start_segment_id
            != segment_ids[item.start_ordinal - 1]
            or item.end_segment_id
            != segment_ids[item.end_ordinal - 1]
        ):
            return False
        expected_start = item.end_ordinal + 1
    return expected_start == len(segment_ids) + 1


def _fingerprint(value: str, label: str) -> str:
    normalized = str(value).strip()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError(f"{label} fingerprint must be lowercase SHA-256")
    return normalized


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
    "ASR_WHOLE_RECHECK_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_WHOLE_RECHECK_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrWholeRecheckAttemptReferenceV1",
    "AsrWholeRecheckPreparedInputV1",
    "AsrWholeRecheckStepOutputV1",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
    "validate_final_against_input",
]
