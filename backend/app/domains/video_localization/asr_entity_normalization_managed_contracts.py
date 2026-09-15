"""Managed input, manifest and final contracts for entity normalization."""

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

from app.domains.video_localization import entity_variant_safety
from app.domains.video_localization.entity_normalization import (
    AsrEntityNormalizationChange,
    AsrEntityNormalizationInput,
    AsrEntityNormalizationWarning,
    AsrEntityResolution,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationGlossaryEntry,
    VideoLocalizationTranscriptSegment,
)
from app.schemas.video_localization_asr_entity_normalization_step import (
    EntityCallPurpose,
)


ASR_ENTITY_NORMALIZATION_PREPARED_INPUT_SCHEMA_VERSION = (
    "asr-entity-normalization-managed-input-v1"
)
ASR_ENTITY_NORMALIZATION_STEP_OUTPUT_SCHEMA_VERSION = (
    "asr-entity-normalization-step-output-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ModelT = TypeVar("ModelT", bound=BaseModel)


class AsrEntityNormalizationPreparedInputV1(BaseModel):
    """Exact research, glossary and runtime identity for one task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-entity-normalization-managed-input-v1"
    ] = ASR_ENTITY_NORMALIZATION_PREPARED_INPUT_SCHEMA_VERSION
    research_artifact_fingerprint: str
    glossary_fingerprint: str
    profile_configuration_fingerprint: str | None = None
    behavior_fingerprint: str
    request: AsrEntityNormalizationInput

    @field_validator(
        "research_artifact_fingerprint",
        "glossary_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_required_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "entity-normalization input")

    @field_validator("profile_configuration_fingerprint")
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _fingerprint(value, "entity-normalization input")

    @model_validator(mode="after")
    def validate_locked_identity(
        self,
    ) -> "AsrEntityNormalizationPreparedInputV1":
        if self.glossary_fingerprint != glossary_fingerprint(
            self.request.glossary
        ):
            raise ValueError(
                "entity-normalization glossary fingerprint mismatch"
            )
        if bool(self.request.profile_id) != bool(
            self.profile_configuration_fingerprint
        ):
            raise ValueError(
                "entity-normalization Provider identity is incomplete"
            )
        return self


class AsrEntityNormalizationCallReferenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: Literal[
        "entity_resolution",
        "entity_variant_mapping",
    ]
    purpose: EntityCallPurpose
    attempt: int = Field(ge=1, le=2)
    candidate_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=24,
    )
    step_id: str = Field(min_length=1, max_length=256)
    input_fingerprint: str
    artifact_fingerprint: str

    @field_validator("input_fingerprint", "artifact_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "entity-normalization call reference")


class AsrEntityNormalizationStepOutputV1(BaseModel):
    """Deterministic result plus exact committed model-call manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "asr-entity-normalization-step-output-v1"
    ] = ASR_ENTITY_NORMALIZATION_STEP_OUTPUT_SCHEMA_VERSION
    prepared_input_fingerprint: str
    status: Literal["not_needed", "completed", "partial", "failed"]
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
        "asr-entity-normalization-v1"
    ] = "asr-entity-normalization-v1"
    resolutions: tuple[AsrEntityResolution, ...] = ()
    updated_segments: tuple[
        VideoLocalizationTranscriptSegment, ...
    ] = ()
    changes: tuple[AsrEntityNormalizationChange, ...] = ()
    warnings: tuple[AsrEntityNormalizationWarning, ...] = ()
    calls: tuple[AsrEntityNormalizationCallReferenceV1, ...] = ()
    duration_ms: int = Field(ge=0)

    @field_validator("prepared_input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return _fingerprint(value, "entity-normalization output")

    @model_validator(mode="after")
    def validate_call_manifest(
        self,
    ) -> "AsrEntityNormalizationStepOutputV1":
        identities = [
            (item.call_id, item.attempt) for item in self.calls
        ]
        if len(set(identities)) != len(identities):
            raise ValueError(
                "entity-normalization calls must be unique"
            )
        if self.model_id and not self.profile_id:
            raise ValueError(
                "entity-normalization model requires a profile"
            )
        if self.calls and not (self.profile_id and self.model_id):
            raise ValueError(
                "entity-normalization call identity is incomplete"
            )
        return self


def validate_final_against_input(
    prepared: AsrEntityNormalizationPreparedInputV1,
    final: AsrEntityNormalizationStepOutputV1,
) -> None:
    """Enforce transcript, evidence and replacement invariants."""

    request = prepared.request
    if final.profile_id != request.profile_id:
        raise ValueError(
            "entity-normalization final profile changed"
        )
    candidate_ids = tuple(
        item.candidate_id for item in request.candidates
    )
    if any(
        item.candidate_ids != candidate_ids
        for item in final.calls
    ):
        raise ValueError(
            "entity-normalization call candidates changed"
        )
    resolution_calls = [
        item
        for item in final.calls
        if item.purpose == "entity_resolution"
    ]
    variant_calls = [
        item
        for item in final.calls
        if item.purpose == "entity_variant_mapping"
    ]
    if request.profile_id:
        if len(resolution_calls) != 1 or len(variant_calls) != bool(
            final.resolutions
        ):
            raise ValueError(
                "entity-normalization call manifest is incomplete"
            )
    elif (
        final.calls
        or final.model_id
        or final.resolutions
    ):
        raise ValueError(
            "local entity-normalization cannot contain model output"
        )
    source = request.segments
    updated = final.updated_segments
    if len(source) != len(updated):
        raise ValueError(
            "entity-normalization segment coverage changed"
        )
    source_ids = [item.segment_id for item in source]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError(
            "entity-normalization source segment IDs are not unique"
        )
    for before, after in zip(source, updated, strict=True):
        if (
            before.segment_id != after.segment_id
            or before.start_ms != after.start_ms
            or before.end_ms != after.end_ms
            or before.text != after.raw_text
            or before.speaker_cluster_id
            != after.speaker_cluster_id
        ):
            raise ValueError(
                "entity-normalization changed source transcript identity"
            )
    evidence_ids = {
        item.evidence_id for item in request.evidence
    }
    if (
        request.contract_version
        == "asr-entity-normalization-input-v2"
        and request.visual_evidence_operation_id
    ):
        evidence_ids.update(
            item.evidence_id
            for item in request.visual_name_evidence
            if item.frame_ids
        )
    segment_ids = set(source_ids)
    for resolution in final.resolutions:
        if not set(resolution.evidence_source_ids) <= evidence_ids:
            raise ValueError(
                "entity resolution cites unknown evidence"
            )
        for variant in resolution.variants:
            if (
                variant.casefold()
                != resolution.canonical_name.casefold()
                and not entity_variant_safety
                .is_safe_proper_name_replacement(
                    resolution.canonical_name,
                    variant,
                )
            ):
                raise ValueError(
                    "entity resolution contains an unsafe variant"
                )
    for change in final.changes:
        if (
            change.segment_id not in segment_ids
            or not set(change.evidence_source_ids)
            <= evidence_ids
        ):
            raise ValueError(
                "entity change references unknown input"
            )
    for warning in final.warnings:
        if warning.segment_id and warning.segment_id not in segment_ids:
            raise ValueError(
                "entity warning references an unknown segment"
            )


def glossary_fingerprint(
    glossary: list[VideoLocalizationGlossaryEntry]
    | tuple[VideoLocalizationGlossaryEntry, ...],
) -> str:
    payload = [
        item.model_dump(mode="json")
        for item in glossary
    ]
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def prepared_input_fingerprint(
    value: AsrEntityNormalizationPreparedInputV1,
) -> str:
    return hashlib.sha256(prepared_input_bytes(value)).hexdigest()


def prepared_input_bytes(
    value: AsrEntityNormalizationPreparedInputV1,
) -> bytes:
    return _canonical_bytes(value)


def step_output_bytes(
    value: AsrEntityNormalizationStepOutputV1,
) -> bytes:
    return _canonical_bytes(value)


def parse_prepared_input(
    content: bytes,
) -> AsrEntityNormalizationPreparedInputV1:
    return _parse_model(
        content,
        AsrEntityNormalizationPreparedInputV1,
        label="entity-normalization prepared input",
    )


def parse_step_output(
    content: bytes,
) -> AsrEntityNormalizationStepOutputV1:
    return _parse_model(
        content,
        AsrEntityNormalizationStepOutputV1,
        label="entity-normalization step output",
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
    "ASR_ENTITY_NORMALIZATION_PREPARED_INPUT_SCHEMA_VERSION",
    "ASR_ENTITY_NORMALIZATION_STEP_OUTPUT_SCHEMA_VERSION",
    "AsrEntityNormalizationCallReferenceV1",
    "AsrEntityNormalizationPreparedInputV1",
    "AsrEntityNormalizationStepOutputV1",
    "glossary_fingerprint",
    "parse_prepared_input",
    "parse_step_output",
    "prepared_input_bytes",
    "prepared_input_fingerprint",
    "step_output_bytes",
    "validate_final_against_input",
]
