"""Versioned contracts for planning, generation and gas editing of dubbing."""

from __future__ import annotations

from app.schemas.video_localization_dubbing_preflight import DubbingGroupPreflightResult

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.tts_content import TtsContentEvidence


ReviewStatus = Literal[
    "passed",
    "warning",
    "failed",
    "needs_review",
    "not_reviewed",
]
DubbingProductionReviewMode = Literal["full", "risk_based", "supervised"]
FindingSeverity = Literal["info", "warning", "blocking"]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DubbingSemanticUnit(_StrictContract):
    """One meaning-bearing unit supplied by localization or editorial review."""

    unit_id: str = Field(min_length=1)
    subtitle_ids: list[str] = Field(min_length=1)
    source_cue_ids: list[str] = Field(default_factory=list)
    source_word_ids: list[str] = Field(default_factory=list)
    speaker_id: str = Field(min_length=1)
    scene_id: str | None = None
    scene_end_ms: int | None = Field(default=None, gt=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_anchor_start_ms: int = Field(ge=0)
    source_anchor_end_ms: int = Field(gt=0)
    display_text: str = Field(min_length=1)
    spoken_text: str = Field(min_length=1)
    speech_policy: Literal[
        "translate",
        "preserve_original",
        "omit_non_speech",
        "needs_review",
    ] = "translate"
    decision_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ranges(self) -> "DubbingSemanticUnit":
        if self.end_ms <= self.start_ms:
            raise ValueError("semantic unit end_ms must be greater than start_ms")
        if self.source_anchor_end_ms <= self.source_anchor_start_ms:
            raise ValueError(
                "semantic source anchor end must be greater than start"
            )
        if (
            self.scene_end_ms is not None
            and self.scene_end_ms < self.source_anchor_end_ms
        ):
            raise ValueError(
                "scene_end_ms cannot precede the source semantic anchor"
            )
        if len(set(self.subtitle_ids)) != len(self.subtitle_ids):
            raise ValueError("semantic unit subtitle_ids must be unique")
        return self


class DubbingBoundaryEvidence(_StrictContract):
    """Evidence at one ordered boundary between adjacent semantic units."""

    boundary_id: str = Field(min_length=1)
    left_unit_id: str = Field(min_length=1)
    right_unit_id: str = Field(min_length=1)
    gap_ms: int = Field(ge=0)
    same_speaker: bool
    same_scene: bool | None = None
    speech_between: bool | None = None
    pause_classification: Literal[
        "continuous",
        "natural_pause",
        "long_silence",
        "unknown",
    ] = "unknown"
    low_energy_confidence: Literal[
        "none",
        "low",
        "medium",
        "high",
    ] = "none"
    semantic_relation: Literal["continuous", "break", "unknown"] = (
        "unknown"
    )
    hard_boundary: bool = False
    no_break_with_next: bool = False
    evidence_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_no_break_claim(self) -> "DubbingBoundaryEvidence":
        if self.no_break_with_next and self.semantic_relation != "continuous":
            raise ValueError(
                "no_break_with_next requires a continuous semantic relation"
            )
        if self.no_break_with_next and (
            self.hard_boundary
            or not self.same_speaker
            or self.same_scene is False
            or self.speech_between is not False
            or self.pause_classification in {"long_silence", "unknown"}
        ):
            raise ValueError(
                "no_break_with_next requires verified continuous source evidence"
            )
        return self


class DubbingGenerationPolicy(_StrictContract):
    preferred_group_units: int = Field(default=2, ge=1, le=2)
    hard_max_group_units: int = Field(default=3, ge=2, le=3)
    allow_three_units_only_for_no_break_phrase: bool = True
    maximum_group_characters: int = Field(default=180, ge=40, le=500)
    maximum_group_subtitles: int = Field(default=3, ge=1, le=6)
    maximum_effective_speech_ms: int = Field(
        default=12_000,
        ge=2_000,
        le=30_000,
    )
    maximum_text_pressure: float = Field(default=1.3, ge=1.0, le=2.0)


class DubbingGenerationPlanInput(_StrictContract):
    schema_version: Literal["dubbing-generation-plan-input-v1"] = (
        "dubbing-generation-plan-input-v1"
    )
    source_revision: str = Field(min_length=1)
    semantic_units: list[DubbingSemanticUnit] = Field(min_length=1)
    boundaries: list[DubbingBoundaryEvidence] = Field(default_factory=list)
    policy: DubbingGenerationPolicy = Field(
        default_factory=DubbingGenerationPolicy
    )

    @model_validator(mode="after")
    def validate_ordered_boundaries(self) -> "DubbingGenerationPlanInput":
        unit_ids = [unit.unit_id for unit in self.semantic_units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("semantic unit IDs must be unique")
        expected = list(zip(unit_ids, unit_ids[1:]))
        actual = [
            (boundary.left_unit_id, boundary.right_unit_id)
            for boundary in self.boundaries
        ]
        if actual != expected:
            raise ValueError(
                "boundaries must cover every adjacent semantic unit in order"
            )
        return self


class DubbingSpeechIsland(_StrictContract):
    island_id: str = Field(min_length=1)
    unit_ids: list[str] = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    scene_id: str | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class DubbingGenerationGroup(_StrictContract):
    group_id: str = Field(min_length=1)
    island_id: str = Field(min_length=1)
    unit_ids: list[str] = Field(min_length=1)
    subtitle_ids: list[str] = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    scene_id: str | None = None
    spoken_text: str = Field(min_length=1)
    target_start_ms: int = Field(ge=0)
    target_end_ms: int = Field(gt=0)
    source_reference_start_ms: int = Field(ge=0)
    source_reference_end_ms: int = Field(gt=0)


class DubbingQualityFinding(_StrictContract):
    code: str = Field(min_length=1)
    severity: FindingSeverity
    message: str = Field(min_length=1)
    entity_ids: list[str] = Field(default_factory=list)
    recommended_action: str | None = None


class DubbingGenerationPlan(_StrictContract):
    schema_version: Literal["dubbing-generation-plan-v1"] = (
        "dubbing-generation-plan-v1"
    )
    source_revision: str = Field(min_length=1)
    plan_revision: int = Field(default=0, ge=0)
    status: ReviewStatus
    semantic_units: list[DubbingSemanticUnit]
    speech_islands: list[DubbingSpeechIsland]
    groups: list[DubbingGenerationGroup]
    findings: list[DubbingQualityFinding] = Field(default_factory=list)


class DubbingProductionSnapshot(_StrictContract):
    """Read-only deterministic projection used before Agent planning."""

    schema_version: Literal["dubbing-production-snapshot-v1"] = (
        "dubbing-production-snapshot-v1"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    semantic_units: list[DubbingSemanticUnit]
    boundaries: list[DubbingBoundaryEvidence]
    evidence_warnings: list[str] = Field(default_factory=list)


class DubbingProductionGroupProgress(_StrictContract):
    group_id: str = Field(min_length=1)
    resource_priority: Literal["foreground_resume", "normal", "background"] = "normal"
    group_index: int = Field(ge=0)
    target_subtitle_ids: list[str] = Field(min_length=1)
    stage: Literal[
        "ready_to_generate",
        "generating",
        "needs_gap_processing",
        "needs_regeneration",
        "needs_timeline_work",
        "needs_timeline_edit",
        "needs_semantic_review",
        "deferred_manual_timing",
        "accepted",
        "failed",
    ]
    recommended_action: Literal[
        "generate_candidate",
        "wait_for_generation",
        "process_gaps",
        "regenerate_candidate",
        "place_candidate",
        "edit_timeline",
        "review_semantic_boundaries",
        "complete",
    ]
    workflow_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    passed_candidate_id: str | None = None
    formal_clip_ids: list[str] = Field(default_factory=list)
    deferred_clip_ids: list[str] = Field(default_factory=list)
    existing_timeline_clip_ids: list[str] = Field(default_factory=list)
    uncovered_target_subtitle_ids: list[str] = Field(default_factory=list)
    timeline_requires_reconciliation: bool = False
    speaking_rate_ratio: float | None = Field(default=None, gt=0)
    content_speed_exception_applied: bool = False
    attempt_count: int = Field(default=0, ge=0)
    last_error: str | None = None


class DubbingProductionRunSnapshot(_StrictContract):
    """Recoverable server-owned projection of the per-group production run."""

    schema_version: Literal["dubbing-production-run-v1"] = (
        "dubbing-production-run-v1"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(default=0, ge=0)
    status: Literal[
        "needs_plan",
        "running",
        "needs_attention",
        "completed",
        "completed_with_deferred",
        "completed_with_failures",
    ]
    group_count: int = Field(default=0, ge=0)
    accepted_group_count: int = Field(default=0, ge=0)
    deferred_group_count: int = Field(default=0, ge=0)
    failed_group_count: int = Field(default=0, ge=0)
    active_group_count: int = Field(default=0, ge=0)
    attention_group_count: int = Field(default=0, ge=0)
    next_group_id: str | None = None
    next_action: Literal[
        "create_plan",
        "generate_candidate",
        "wait_for_generation",
        "process_gaps",
        "regenerate_candidate",
        "place_candidate",
        "edit_timeline",
        "review_semantic_boundaries",
        "complete",
    ]
    groups: list[DubbingProductionGroupProgress] = Field(default_factory=list)


class DubbingProductionManualReviewRequest(_StrictContract):
    """Explicitly keep one listenable candidate and continue with review state."""

    schema_version: Literal["dubbing-production-manual-review-v1"] = (
        "dubbing-production-manual-review-v1"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2_000)
    reason_code: str = Field(min_length=1, max_length=100)


class DubbingExistingFormalAcceptance(_StrictContract):
    """Current-plan claim for an unchanged, previously reviewed formal result."""

    schema_version: Literal["dubbing-existing-formal-acceptance-v1"] = (
        "dubbing-existing-formal-acceptance-v1"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    target_subtitle_ids: list[str] = Field(min_length=1)
    spoken_text_fingerprint: str = Field(min_length=64, max_length=64)
    inherited_gate_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_clip_projection_fingerprint: str = Field(
        min_length=64,
        max_length=64,
    )
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_target_subtitles(self) -> "DubbingExistingFormalAcceptance":
        if len(self.target_subtitle_ids) != len(set(self.target_subtitle_ids)):
            raise ValueError("existing formal target subtitle IDs must be unique")
        return self


class DubbingExistingFormalReconcileResponse(_StrictContract):
    """Summary of a non-destructive current-plan reconciliation command."""

    schema_version: Literal["dubbing-existing-formal-reconcile-v1"] = (
        "dubbing-existing-formal-reconcile-v1"
    )
    exact_existing_group_count: int = Field(ge=0)
    accepted_group_count: int = Field(ge=0)
    pending_review_group_count: int = Field(ge=0)
    unchanged_acceptance_count: int = Field(ge=0)
    accepted_group_ids: list[str] = Field(default_factory=list)
    pending_review_group_ids: list[str] = Field(default_factory=list)


class DubbingProductionGroupFailure(_StrictContract):
    """Terminal failure for one current-plan group after bounded recovery."""

    schema_version: Literal["dubbing-production-group-failure-v2"] = (
        "dubbing-production-group-failure-v2"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2_000)
    reason_code: str = Field(min_length=1, max_length=100)
    attempted_strategy_codes: list[str] = Field(default_factory=list)
    attempt_count: int = Field(default=0, ge=0)
    created_at: str = Field(min_length=1)


class DubbingProductionGroupReview(_StrictContract):
    """Durable review fact for one listenable current-plan group."""

    schema_version: Literal["dubbing-production-group-review-v1"] = (
        "dubbing-production-group-review-v1"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2_000)
    reason_code: str = Field(min_length=1, max_length=100)
    created_at: str = Field(min_length=1)


class DubbingProductionGroupFailureRequest(_StrictContract):
    """Record one exhausted group and continue unaffected current-plan groups."""

    schema_version: Literal["dubbing-production-group-failure-request-v1"] = (
        "dubbing-production-group-failure-request-v1"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2_000)
    reason_code: str = Field(min_length=1, max_length=100)
    attempted_strategy_codes: list[str] = Field(default_factory=list)
    attempt_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_unique_strategies(self) -> "DubbingProductionGroupFailureRequest":
        if len(self.attempted_strategy_codes) != len(
            set(self.attempted_strategy_codes)
        ):
            raise ValueError("attempted strategy codes must be unique")
        return self


CapacityRecoveryStrategy = Literal[
    "verify_window_and_group",
    "safe_gap_edit",
    "allowed_speed",
    "whole_regeneration",
    "semantic_split",
    "equivalent_text_compression",
]


class DubbingCapacityRecoveryEvidence(_StrictContract):
    """Auditable result of one bounded capacity-recovery decision."""

    strategy: CapacityRecoveryStrategy
    outcome: Literal[
        "applied",
        "not_applicable",
        "exhausted",
        "no_improvement",
    ]
    evidence_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1_000)
    attempt_count: int = Field(default=0, ge=0)
    before_duration_ms: int | None = Field(default=None, gt=0)
    after_duration_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_attempts(self) -> "DubbingCapacityRecoveryEvidence":
        if self.outcome == "not_applicable" and self.attempt_count:
            raise ValueError("not-applicable recovery evidence cannot record attempts")
        if self.strategy == "verify_window_and_group" and self.outcome != "applied":
            raise ValueError("window and group verification must be applied")
        if self.strategy == "equivalent_text_compression" and self.attempt_count > 2:
            raise ValueError("equivalent text compression is limited to two variants")
        return self


class DubbingManualTimingDeferralRequest(_StrictContract):
    """Park a complete take that cannot safely fit the current primary lane."""

    schema_version: Literal["dubbing-manual-timing-deferral-request-v1"] = (
        "dubbing-manual-timing-deferral-request-v1"
    )
    request_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    expected_repository_revision: int = Field(ge=0)
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    parked_clip_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    available_duration_ms: int = Field(gt=0)
    candidate_duration_ms: int = Field(gt=0)
    audio_sha256: str = Field(min_length=64, max_length=64)
    content_verification_status: Literal["verified_complete"]
    content_verification_evidence_ids: list[str] = Field(min_length=1)
    semantic_boundary_review: Literal["agent_asserted_complete"]
    semantic_boundary_evidence_ids: list[str] = Field(min_length=1)
    naturalness_review: Literal["agent_listened_acceptable", "not_claimed"]
    recovery_evidence: list[DubbingCapacityRecoveryEvidence] = Field(
        min_length=6,
        max_length=6,
    )

    @model_validator(mode="after")
    def validate_bounded_recovery(self) -> "DubbingManualTimingDeferralRequest":
        required = {
            "verify_window_and_group",
            "safe_gap_edit",
            "allowed_speed",
            "whole_regeneration",
            "semantic_split",
            "equivalent_text_compression",
        }
        observed = [item.strategy for item in self.recovery_evidence]
        if len(observed) != len(set(observed)) or set(observed) != required:
            raise ValueError(
                "manual timing deferral requires one evidence record per recovery strategy"
            )
        return self


class DubbingCandidateContentEvidenceRequest(_StrictContract):
    """Acquire an independent transcript for one exact current candidate."""

    schema_version: Literal["dubbing-candidate-content-evidence-request-v1"] = (
        "dubbing-candidate-content-evidence-request-v1"
    )
    expected_repository_revision: int = Field(ge=0)
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    use_current_timeline_projection: bool = False


class DubbingCurrentProjectionRequest(_StrictContract):
    """Refresh evidence for actual clips without editing or generating audio."""

    schema_version: Literal["dubbing-current-projection-v1"] = "dubbing-current-projection-v1"
    expected_repository_revision: int = Field(ge=0)
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    candidate_clip_projection_fingerprint: str = Field(min_length=64, max_length=64)


class DubbingManualTimingDeferral(_StrictContract):
    """Durable current-plan disposition; it is never a primary quality pass."""

    schema_version: Literal["dubbing-manual-timing-deferral-v1"] = (
        "dubbing-manual-timing-deferral-v1"
    )
    request_id: str = Field(min_length=1)
    request_fingerprint: str = Field(min_length=64, max_length=64)
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    evidence_origin_source_revision: str = Field(min_length=64, max_length=64)
    evidence_origin_plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    parked_clip_id: str = Field(min_length=1)
    dub_lane: Literal[1] = 1
    placement_failure_reason: Literal["duration_overflow", "protected_audio_conflict"] = "duration_overflow"
    conflicting_clip_ids: list[str] = Field(default_factory=list)
    target_subtitle_ids: list[str] = Field(min_length=1)
    source_cue_ids: list[str] = Field(min_length=1)
    available_duration_ms: int = Field(gt=0)
    candidate_duration_ms: int = Field(gt=0)
    audio_sha256: str = Field(min_length=64, max_length=64)
    source_context_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_spoken_text_fingerprint: str = Field(min_length=64, max_length=64)
    clip_projection_fingerprint: str = Field(min_length=64, max_length=64)
    content_verification_status: Literal["verified_complete"]
    content_verification_evidence_ids: list[str] = Field(min_length=1)
    semantic_boundary_review: Literal["agent_asserted_complete"]
    semantic_boundary_evidence_ids: list[str] = Field(min_length=1)
    naturalness_review: Literal["agent_listened_acceptable", "not_claimed"]
    recovery_evidence: list[DubbingCapacityRecoveryEvidence] = Field(min_length=6)
    created_at: str = Field(min_length=1)


class DubbingAudioGapEvidence(_StrictContract):
    """Reproducible evidence and disposition for one real audio gap."""

    gap_id: str = Field(min_length=1)
    kind: Literal["leading", "internal", "trailing"]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    evidence_sources: list[
        Literal["waveform", "energy", "vad", "word_alignment", "listening"]
    ] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    boundary_confidence: Literal["clear", "ambiguous"]
    edit_decision: Literal["remove", "shorten", "retain", "extend"]
    retained_duration_ms: int = Field(ge=0)
    decision_reason: str = Field(min_length=1)
    safe_edit_boundary: bool | None = None
    review_evidence_ids: list[str] = Field(default_factory=list)
    semantic_role: Literal[
        "semantic_boundary",
        "continuous_phrase",
        "uncertain",
    ] | None = None
    semantic_pause_scale: Literal["tight", "normal", "deliberate"] | None = None

    @model_validator(mode="after")
    def validate_gap(self) -> "DubbingAudioGapEvidence":
        if self.end_ms <= self.start_ms:
            raise ValueError("gap end_ms must be greater than start_ms")
        if self.duration_ms != self.end_ms - self.start_ms:
            raise ValueError("gap duration_ms must match its range")
        if (
            self.retained_duration_ms > self.duration_ms
            and self.edit_decision != "extend"
        ):
            raise ValueError("retained gap duration cannot exceed measured duration")
        if self.edit_decision == "remove" and self.retained_duration_ms != 0:
            raise ValueError("removed gap must retain zero milliseconds")
        if self.edit_decision == "retain" and self.retained_duration_ms != self.duration_ms:
            raise ValueError("retained gap must preserve its measured duration")
        if self.edit_decision == "shorten" and not (
            0 < self.retained_duration_ms < self.duration_ms
        ):
            raise ValueError("shortened gap must retain a strict subrange")
        if (
            self.edit_decision == "extend"
            and self.retained_duration_ms <= self.duration_ms
        ):
            raise ValueError("extended gap must exceed its measured duration")
        if len(set(self.evidence_sources)) != len(self.evidence_sources):
            raise ValueError("gap evidence sources must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("gap evidence IDs must be unique")
        if (
            self.semantic_pause_scale is not None
            and self.semantic_role != "semantic_boundary"
        ):
            raise ValueError(
                "semantic pause scale is valid only for a semantic boundary"
            )
        return self


class DubbingPauseEvidence(_StrictContract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    expected_semantic_boundary: bool
    safe_edit_boundary: bool | None = None

    @model_validator(mode="after")
    def validate_duration(self) -> "DubbingPauseEvidence":
        if self.end_ms <= self.start_ms:
            raise ValueError("pause end_ms must be greater than start_ms")
        if self.duration_ms != self.end_ms - self.start_ms:
            raise ValueError("pause duration_ms must match its range")
        return self


class DubbingVoicedSpan(_StrictContract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> "DubbingVoicedSpan":
        if self.end_ms <= self.start_ms:
            raise ValueError("voiced span end_ms must be greater than start_ms")
        return self


class DubbingCandidateAlignedWord(_StrictContract):
    """One forced-aligned token from the generated candidate audio."""

    word_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "DubbingCandidateAlignedWord":
        if self.end_ms < self.start_ms:
            raise ValueError("aligned word end_ms cannot precede start_ms")
        return self


class DubbingAutomaticAudioEvidence(_StrictContract):
    duration_ms: int = Field(gt=0)
    peak_dbfs: float
    clipping_ratio: float = Field(ge=0, le=1)
    leading_silence_ms: int = Field(ge=0)
    trailing_silence_ms: int = Field(ge=0)
    speech_start_ms: int | None = Field(default=None, ge=0)
    speech_end_ms: int | None = Field(default=None, gt=0)
    speech_span_ms: int | None = Field(default=None, gt=0)
    voiced_spans: list[DubbingVoicedSpan] = Field(default_factory=list)
    aligned_words: list[DubbingCandidateAlignedWord] = Field(
        default_factory=list
    )
    voiced_duration_ms: int | None = Field(default=None, gt=0)
    speaking_rate_ratio: float | None = Field(default=None, gt=0)
    project_speaking_rate_ratio_min: float | None = Field(default=None, gt=0)
    project_speaking_rate_ratio_max: float | None = Field(default=None, gt=0)
    content_speed_exception_reason: str | None = None
    content_speed_exception_evidence_ids: list[str] = Field(default_factory=list)
    gap_evidence: list[DubbingAudioGapEvidence] = Field(default_factory=list)
    internal_pauses: list[DubbingPauseEvidence] = Field(
        default_factory=list
    )
    expected_pause_baseline_ms: int | None = Field(default=None, ge=0)
    max_leading_silence_ms: int | None = Field(default=None, ge=0)
    max_trailing_silence_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_acoustic_evidence(self) -> "DubbingAutomaticAudioEvidence":
        speech_values = (
            self.speech_start_ms,
            self.speech_end_ms,
            self.speech_span_ms,
        )
        if any(value is not None for value in speech_values):
            if any(value is None for value in speech_values):
                raise ValueError("speech timing evidence must be complete")
            assert self.speech_start_ms is not None
            assert self.speech_end_ms is not None
            assert self.speech_span_ms is not None
            if not 0 <= self.speech_start_ms < self.speech_end_ms <= self.duration_ms:
                raise ValueError("speech timing must stay inside the candidate audio")
            if self.speech_span_ms != self.speech_end_ms - self.speech_start_ms:
                raise ValueError("speech span must match speech timing")
        previous_end_ms = -1
        for span in self.voiced_spans:
            if span.end_ms > self.duration_ms or span.start_ms < previous_end_ms:
                raise ValueError("voiced spans must be ordered, disjoint, and inside audio")
            previous_end_ms = span.end_ms
        previous_end_ms = -1
        aligned_word_ids: list[str] = []
        for word in self.aligned_words:
            if word.end_ms > self.duration_ms or word.start_ms < previous_end_ms:
                raise ValueError(
                    "aligned words must be ordered, disjoint, and inside audio"
                )
            previous_end_ms = word.end_ms
            aligned_word_ids.append(word.word_id)
        if len(aligned_word_ids) != len(set(aligned_word_ids)):
            raise ValueError("aligned word IDs must be unique")
        if self.voiced_duration_ms is not None:
            if not self.voiced_spans:
                raise ValueError("voiced duration requires voiced spans")
            if self.voiced_duration_ms != sum(
                span.end_ms - span.start_ms for span in self.voiced_spans
            ):
                raise ValueError("voiced duration must equal the sum of voiced spans")
        rate_range = (
            self.project_speaking_rate_ratio_min,
            self.project_speaking_rate_ratio_max,
        )
        if any(value is not None for value in rate_range):
            if any(value is None for value in rate_range):
                raise ValueError("project speaking-rate range must be complete")
            assert self.project_speaking_rate_ratio_min is not None
            assert self.project_speaking_rate_ratio_max is not None
            if self.project_speaking_rate_ratio_max < self.project_speaking_rate_ratio_min:
                raise ValueError("project speaking-rate maximum must not precede minimum")
        if bool(self.content_speed_exception_reason) != bool(
            self.content_speed_exception_evidence_ids
        ):
            raise ValueError(
                "content-speed exception requires both a reason and evidence IDs"
            )
        gap_ids = [gap.gap_id for gap in self.gap_evidence]
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("audio gap evidence IDs must be unique")
        if any(gap.end_ms > self.duration_ms for gap in self.gap_evidence):
            raise ValueError("audio gap evidence must stay inside candidate duration")
        return self


class DubbingSubjectiveReview(_StrictContract):
    dimension: Literal[
        "meaning",
        "pronunciation",
        "prosody_parse",
        "voice_match",
        "naturalness",
    ]
    status: Literal["passed", "failed", "not_reviewed"] = "not_reviewed"
    note: str = ""
    evidence_id: str | None = None


class DubbingCandidateReviewCommand(_StrictContract):
    """Candidate-bound decisions; retained plus submitted reviews must cover every boundary."""

    schema_version: Literal["dubbing-candidate-review-command-v2"] = (
        "dubbing-candidate-review-command-v2"
    )
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    candidate_id: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=64, max_length=64)
    candidate_evidence_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_clip_projection_fingerprint: str = Field(min_length=64, max_length=64)
    semantic_boundary_reviews: list["DubbingSemanticBoundaryReview"] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_dimensions(self) -> "DubbingCandidateReviewCommand":
        boundary_ids = [review.boundary_id for review in self.semantic_boundary_reviews]
        if len(boundary_ids) != len(set(boundary_ids)):
            raise ValueError("semantic boundary reviews must be unique by boundary_id")
        return self


class DubbingSemanticBoundaryEvidence(_StrictContract):
    """One adjacent rendered token edge, including touching and overlap."""

    boundary_id: str = Field(min_length=1)
    left_word_id: str = Field(min_length=1)
    right_word_id: str = Field(min_length=1)
    left_text: str = Field(min_length=1)
    right_text: str = Field(min_length=1)
    left_source_start_ms: int = Field(ge=0)
    left_source_end_ms: int = Field(ge=0)
    right_source_start_ms: int = Field(ge=0)
    right_source_end_ms: int = Field(ge=0)
    source_relation: Literal["separated", "touching", "overlapping"]
    source_gap_ms: int = Field(ge=0)
    source_overlap_ms: int = Field(ge=0)
    final_relation: Literal["separated", "touching", "overlapping", "cut"]
    final_gap_ms: int = Field(ge=0)
    final_overlap_ms: int = Field(ge=0)
    left_render_status: Literal["fully_retained", "partially_cut", "removed", "zero_width_anchor"]
    right_render_status: Literal["fully_retained", "partially_cut", "removed", "zero_width_anchor"]
    left_final_fragments: list[tuple[int, int]] = Field(default_factory=list)
    right_final_fragments: list[tuple[int, int]] = Field(default_factory=list)
    low_energy_evidence: list[DubbingAudioGapEvidence] = Field(default_factory=list)
    safe_edit_boundary: bool | None = None


class DubbingSemanticBoundaryAudit(_StrictContract):
    """Rebuildable final-projection facts awaiting the Agent's disposition."""

    schema_version: Literal["dubbing-semantic-boundary-audit-v1"] = "dubbing-semantic-boundary-audit-v1"
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    candidate_id: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=64, max_length=64)
    candidate_evidence_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_clip_projection_fingerprint: str = Field(min_length=64, max_length=64)
    expected_spoken_text: str = Field(min_length=1)
    aligned_words: list[DubbingCandidateAlignedWord] = Field(min_length=1)
    audio_gap_evidence: list[DubbingAudioGapEvidence] = Field(default_factory=list)
    status: Literal["pending_agent", "accepted", "recovery_required"] = "pending_agent"
    boundaries: list[DubbingSemanticBoundaryEvidence] = Field(default_factory=list)
    agent_reviews: list["DubbingSemanticBoundaryReview"] = Field(default_factory=list)


class DubbingSemanticBoundaryReview(_StrictContract):
    boundary_id: str = Field(min_length=1)
    semantic_role: Literal["semantic_boundary", "continuous_phrase"]
    disposition: Literal["acceptable", "recover", "uncertain"]
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_id: str | None = None


class DubbingStagedCandidateClip(_StrictContract):
    """Typed candidate projection held outside the live timeline pending review."""

    clip_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    track_id: Literal["dub"] = "dub"
    status: Literal["ready"] = "ready"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    target_subtitle_ids: list[str] = Field(min_length=1)
    subtitle_id: str | None = None
    dubbing_group_id: str = Field(min_length=1)
    result_id: str | None = None
    task_id: str | None = None
    generation_id: str | None = None
    cue_id: str | None = None
    target_start_ms: int | None = Field(default=None, ge=0)
    target_end_ms: int | None = Field(default=None, gt=0)
    tts_target_text: str | None = None
    source_cue_ids: list[str] = Field(default_factory=list)
    dub_lane: int = Field(default=0, ge=0)
    intentional_overlap: bool = False
    media_source_clip_id: str | None = None
    dubbing_slice_index: int | None = Field(default=None, ge=0)
    dubbing_slice_count: int | None = Field(default=None, ge=1)
    dubbing_alignment_word_ids: list[str] = Field(default_factory=list)
    dubbing_timeline_gap_before_ms: int | None = Field(default=None, ge=0)
    audio_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class DubbingStagedCandidateProjection(_StrictContract):
    schema_version: Literal["dubbing-staged-candidate-projection-v1"] = (
        "dubbing-staged-candidate-projection-v1"
    )
    candidate_id: str = Field(min_length=1)
    target_projection_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_clip_projection_fingerprint: str = Field(
        min_length=64, max_length=64
    )
    clips: list[DubbingStagedCandidateClip] = Field(min_length=1)


class DubbingCandidateCqcPolicy(_StrictContract):
    """Versioned generic thresholds, never values inferred from one video."""

    maximum_missing_token_ratio: float = Field(default=0.08, ge=0, le=1)
    maximum_extra_token_ratio: float = Field(default=0.08, ge=0, le=1)
    maximum_clipping_ratio: float = Field(default=0.002, ge=0, le=1)
    unexpected_pause_multiplier: float = Field(default=2.5, ge=1.0, le=10)


class DubbingRetainedContentEvidence(_StrictContract):
    audio_sha256: str = Field(min_length=64, max_length=64)
    candidate_clip_projection_fingerprint: str = Field(min_length=64, max_length=64)
    observation: TtsContentEvidence


class DubbingCandidateCqcInput(_StrictContract):
    schema_version: Literal["dubbing-candidate-cqc-input-v1"] = (
        "dubbing-candidate-cqc-input-v1"
    )
    source_revision: str = Field(min_length=1)
    plan_revision: int = Field(default=0, ge=0)
    plan_schema_version: Literal["dubbing-generation-plan-v1"] = (
        "dubbing-generation-plan-v1"
    )
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    task_status: Literal[
        "prepared",
        "queued",
        "running",
        "success",
        "failed",
        "cancelled",
    ]
    artifact_id: str | None = None
    audio_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    source_context_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    evidence_origin_source_revision: str | None = None
    evidence_origin_plan_revision: int | None = Field(default=None, ge=0)
    expected_spoken_text: str = Field(min_length=1)
    reference_transcript: str = ""
    candidate_transcript: str = ""
    content_evidence: TtsContentEvidence | None = None
    retained_content_evidence: DubbingRetainedContentEvidence | None = None
    target_start_ms: int = Field(ge=0)
    target_end_ms: int = Field(gt=0)
    planned_scene_end_ms: int | None = Field(default=None, gt=0)
    placement_start_ms: int | None = Field(default=None, ge=0)
    placement_end_ms: int | None = Field(default=None, gt=0)
    frame_tolerance_ms: int = Field(default=42, ge=0, le=250)
    audio: DubbingAutomaticAudioEvidence | None = None
    policy: DubbingCandidateCqcPolicy = Field(
        default_factory=DubbingCandidateCqcPolicy
    )
    subjective_reviews: list[DubbingSubjectiveReview] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_ranges(self) -> "DubbingCandidateCqcInput":
        if self.target_end_ms <= self.target_start_ms:
            raise ValueError("target end_ms must be greater than start_ms")
        if (
            self.placement_start_ms is None
        ) != (self.placement_end_ms is None):
            raise ValueError("placement range must be complete or absent")
        if (
            self.placement_start_ms is not None
            and self.placement_end_ms is not None
            and self.placement_end_ms <= self.placement_start_ms
        ):
            raise ValueError("placement end_ms must be greater than start_ms")
        dimensions = [review.dimension for review in self.subjective_reviews]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("subjective review dimensions must be unique")
        return self


class DubbingTranscriptComparison(_StrictContract):
    expected_tokens: int = Field(ge=0)
    matched_tokens: int = Field(ge=0)
    missing_tokens: list[str] = Field(default_factory=list)
    extra_tokens: list[str] = Field(default_factory=list)
    reference_only_extra_tokens: list[str] = Field(default_factory=list)
    coverage_ratio: float = Field(ge=0, le=1)
    extra_ratio: float = Field(ge=0)


class DubbingCandidateCqcReport(_StrictContract):
    schema_version: Literal["dubbing-candidate-cqc-v1"] = (
        "dubbing-candidate-cqc-v1"
    )
    source_revision: str = Field(min_length=1)
    plan_revision: int = Field(default=0, ge=0)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    evidence_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    automatic_status: ReviewStatus
    subjective_status: ReviewStatus
    overall_status: ReviewStatus
    transcript: DubbingTranscriptComparison
    audio_evidence: DubbingAutomaticAudioEvidence | None = None
    semantic_boundary_audit: DubbingSemanticBoundaryAudit | None = None
    staged_candidate_projection: DubbingStagedCandidateProjection | None = None
    findings: list[DubbingQualityFinding] = Field(default_factory=list)
    recommended_action: Literal[
        "accept",
        "listen_and_review",
        "regenerate",
        "repair_text_or_grouping",
        "edit_timeline",
    ]


class DubbingTimelineExpectedUnit(_StrictContract):
    unit_id: str = Field(min_length=1)
    subtitle_ids: list[str] = Field(default_factory=list)
    speaker_id: str = Field(min_length=1)
    scene_id: str | None = None
    speech_policy: Literal[
        "translate",
        "preserve_original",
        "omit_non_speech",
        "needs_review",
    ]
    source_anchor_start_ms: int = Field(ge=0)
    source_anchor_end_ms: int = Field(gt=0)
    scene_end_ms: int | None = Field(default=None, gt=0)
    source_continuous_with_next: bool = False


class DubbingTimelineClip(_StrictContract):
    clip_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    unit_ids: list[str] = Field(min_length=1)
    target_subtitle_ids: list[str] = Field(default_factory=list)
    speaker_id: str = Field(min_length=1)
    scene_id: str | None = None
    dub_lane: int = Field(ge=0)
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    source_start_ms: int | None = Field(default=None, ge=0)
    source_end_ms: int | None = Field(default=None, gt=0)
    # This is an observed timeline fact, not an editing request. Long source
    # pauses are valid and are evaluated by the timeline audit.
    timeline_gap_before_ms: int = Field(default=0, ge=0)
    source_revision: str = Field(min_length=1)
    plan_revision: int = Field(default=0, ge=0)
    cqc_status: ReviewStatus
    manual_review_reason_codes: list[str] = Field(default_factory=list)
    expected_audio_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    current_audio_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    intentional_overlap: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> "DubbingTimelineClip":
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("timeline clip end_ms must be greater than start_ms")
        if (self.source_start_ms is None) != (self.source_end_ms is None):
            raise ValueError("source crop range must be complete or absent")
        if (
            self.source_start_ms is not None
            and self.source_end_ms is not None
            and self.source_end_ms <= self.source_start_ms
        ):
            raise ValueError("source crop end_ms must be greater than start_ms")
        return self


class DubbingTimelineAlignedSlice(_StrictContract):
    target_subtitle_ids: list[str] = Field(min_length=1)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    speech_start_ms: int = Field(ge=0)
    speech_end_ms: int = Field(gt=0)
    alignment_word_ids: list[str] = Field(min_length=1)
    timeline_gap_before_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> "DubbingTimelineAlignedSlice":
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("slice source end_ms must be greater than start_ms")
        if self.speech_end_ms <= self.speech_start_ms:
            raise ValueError("slice speech end_ms must be greater than start_ms")
        if not (
            self.source_start_ms
            <= self.speech_start_ms
            < self.speech_end_ms
            <= self.source_end_ms
        ):
            raise ValueError("slice speech range must stay inside its source crop")
        if len(set(self.target_subtitle_ids)) != len(self.target_subtitle_ids):
            raise ValueError("slice target subtitle IDs must be unique")
        if len(set(self.alignment_word_ids)) != len(self.alignment_word_ids):
            raise ValueError("slice alignment word IDs must be unique")
        return self


class DubbingTimelineClipSplitCommand(_StrictContract):
    clip_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=64, max_length=64)
    slices: list[DubbingTimelineAlignedSlice] = Field(min_length=2)


class DubbingTimelineEditGate(_StrictContract):
    """Durable advisory evidence for the current candidate's timeline edit."""

    schema_version: Literal["dubbing-timeline-edit-gate-v1"] = (
        "dubbing-timeline-edit-gate-v1"
    )
    source_revision: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    candidate_id: str = Field(min_length=1)
    cqc_report_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_clip_projection_fingerprint: str = Field(
        min_length=64,
        max_length=64,
    )
    status: Literal["passed", "failed", "needs_review"]
    actual_speech_start_delta_ms: int
    actual_speech_end_delta_ms: int
    speaking_rate_ratio: float = Field(gt=0)
    content_speed_exception_applied: bool = False
    alignment_word_ids: list[str] = Field(min_length=1)
    gap_decisions: list[DubbingAudioGapEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_gap_ids(self) -> "DubbingTimelineEditGate":
        gap_ids = [gap.gap_id for gap in self.gap_decisions]
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("timeline edit gate gap evidence IDs must be unique")
        return self


class DubbingTimelineEditGateCommitRequest(_StrictContract):
    """Typed atomic write for one candidate's current timeline-edit evidence."""

    schema_version: Literal["dubbing-timeline-edit-gate-commit-v1"] = (
        "dubbing-timeline-edit-gate-commit-v1"
    )
    gate: DubbingTimelineEditGate


class DubbingTimelineSplitRequest(_StrictContract):
    schema_version: Literal["dubbing-timeline-split-request-v1"] = (
        "dubbing-timeline-split-request-v1"
    )
    source_revision: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    timeline_revision: str = Field(min_length=64, max_length=64)
    commands: list[DubbingTimelineClipSplitCommand] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_clips(self) -> "DubbingTimelineSplitRequest":
        clip_ids = [command.clip_id for command in self.commands]
        if len(clip_ids) != len(set(clip_ids)):
            raise ValueError("timeline split commands must be unique by clip_id")
        return self


class DubbingStagedCandidateSplitRequest(_StrictContract):
    """Safely partition one not-yet-adopted candidate projection."""

    schema_version: Literal["dubbing-staged-candidate-split-request-v1"] = (
        "dubbing-staged-candidate-split-request-v1"
    )
    expected_repository_revision: int = Field(ge=0)
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    candidate_id: str = Field(min_length=1)
    candidate_clip_projection_fingerprint: str = Field(
        min_length=64, max_length=64
    )
    commands: list[DubbingTimelineClipSplitCommand] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidate_commands(self) -> "DubbingStagedCandidateSplitRequest":
        if any(command.candidate_id != self.candidate_id for command in self.commands):
            raise ValueError("staged split commands must belong to the candidate")
        clip_ids = [command.clip_id for command in self.commands]
        if len(clip_ids) != len(set(clip_ids)):
            raise ValueError("staged split commands must be unique by clip_id")
        return self


class DubbingTimelineGroupResetRequest(_StrictContract):
    """Remove formal placements for exact current-plan groups before replay."""

    schema_version: Literal["dubbing-timeline-group-reset-v1"] = (
        "dubbing-timeline-group-reset-v1"
    )
    source_revision: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    group_ids: list[str] = Field(min_length=1)
    discard_current_candidates: bool = False

    @model_validator(mode="after")
    def validate_unique_groups(self) -> "DubbingTimelineGroupResetRequest":
        if len(self.group_ids) != len(set(self.group_ids)):
            raise ValueError("timeline reset group IDs must be unique")
        return self


class DubbingTimelineAuditInput(_StrictContract):
    schema_version: Literal["dubbing-timeline-audit-input-v1"] = (
        "dubbing-timeline-audit-input-v1"
    )
    current_source_revision: str = Field(min_length=1)
    current_timeline_revision: str = Field(min_length=1)
    phase: Literal["timeline", "delivery"] = "delivery"
    dub_subtitle_source_revision: str | None = None
    expected_units: list[DubbingTimelineExpectedUnit] = Field(min_length=1)
    clips: list[DubbingTimelineClip] = Field(default_factory=list)
    dub_subtitle_clip_ids: list[str] = Field(default_factory=list)
    frame_tolerance_ms: int = Field(default=42, ge=0, le=250)


class DubbingTimelineAuditReport(_StrictContract):
    schema_version: Literal["dubbing-timeline-audit-v1"] = (
        "dubbing-timeline-audit-v1"
    )
    current_source_revision: str = Field(min_length=1)
    current_timeline_revision: str = Field(min_length=1)
    phase: Literal["timeline", "delivery"] = "delivery"
    status: ReviewStatus
    covered_unit_ids: list[str]
    missing_unit_ids: list[str]
    duplicate_unit_ids: list[str]
    unexpected_unit_ids: list[str]
    findings: list[DubbingQualityFinding] = Field(default_factory=list)


class DubbingGroupSchedulingPolicy(_StrictContract):
    """Queue priority scoped to one immutable production-plan group."""

    source_revision: str
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    priority: Literal["foreground_resume", "normal", "background"] = "normal"


class DubbingProductionState(_StrictContract):
    """Durable project-owned CQC state; runtime placeholders never live here."""

    schema_version: Literal["dubbing-production-state-v2"] = (
        "dubbing-production-state-v2"
    )
    # ``legacy`` remains a read-only compatibility value for stored projects.
    # Every newly constructed or rewritten state enters the semantic plan.
    enforcement_mode: Literal["legacy", "planned"] = "planned"
    plan_revision_counter: int = Field(default=0, ge=0)
    active_plan: DubbingGenerationPlan | None = None
    scheduling_policies: list[DubbingGroupSchedulingPolicy] = Field(default_factory=list)
    candidate_reports: list[DubbingCandidateCqcReport] = Field(
        default_factory=list
    )
    candidate_inputs: list[DubbingCandidateCqcInput] = Field(
        default_factory=list
    )
    group_failures: list[DubbingProductionGroupFailure] = Field(
        default_factory=list
    )
    group_reviews: list[DubbingProductionGroupReview] = Field(
        default_factory=list
    )
    manual_timing_deferrals: list[DubbingManualTimingDeferral] = Field(
        default_factory=list
    )
    existing_formal_acceptances: list[DubbingExistingFormalAcceptance] = Field(
        default_factory=list
    )
    latest_timeline_audit: DubbingTimelineAuditReport | None = None

    @model_validator(mode="after")
    def validate_unique_candidate_reports(self) -> "DubbingProductionState":
        scheduling_keys = [
            (item.source_revision, item.plan_revision, item.group_id)
            for item in self.scheduling_policies
        ]
        if len(scheduling_keys) != len(set(scheduling_keys)):
            raise ValueError("scheduling policies must be unique per plan group")
        candidate_ids = [report.candidate_id for report in self.candidate_reports]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate CQC reports must be unique by candidate_id")
        input_ids = [item.candidate_id for item in self.candidate_inputs]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("candidate CQC inputs must be unique by candidate_id")
        failure_keys = [
            (item.source_revision, item.plan_revision, item.group_id)
            for item in self.group_failures
        ]
        if len(failure_keys) != len(set(failure_keys)):
            raise ValueError(
                "dubbing group failures must be unique by source/plan/group"
            )
        review_keys = [
            (item.source_revision, item.plan_revision, item.group_id, item.candidate_id)
            for item in self.group_reviews
        ]
        if len(review_keys) != len(set(review_keys)):
            raise ValueError(
                "dubbing group reviews must be unique by source/plan/group/candidate"
            )
        deferral_keys = [
            (item.source_revision, item.plan_revision, item.group_id)
            for item in self.manual_timing_deferrals
        ]
        if len(deferral_keys) != len(set(deferral_keys)):
            raise ValueError(
                "manual timing deferrals must be unique by source/plan/group"
            )
        request_ids = [item.request_id for item in self.manual_timing_deferrals]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("manual timing deferral request IDs must be unique")
        acceptance_keys = [
            (item.source_revision, item.plan_revision, item.group_id)
            for item in self.existing_formal_acceptances
        ]
        if len(acceptance_keys) != len(set(acceptance_keys)):
            raise ValueError(
                "existing formal acceptances must be unique by source/plan/group"
            )
        return self


class DubbingProductionExecuteRequest(_StrictContract):
    """Start the canonical semantic-group executor.

    ``all_remaining`` repeats the exact same single-group command after each
    durable task completes.  It is not a separate batch algorithm.
    """

    schema_version: Literal["dubbing-production-execute-v1"] = (
        "dubbing-production-execute-v1"
    )
    scope: Literal["single_group", "all_remaining"] = "single_group"
    group_id: str | None = Field(default=None, min_length=1)
    review_mode: DubbingProductionReviewMode = "full"
    resource_priority: Literal[
        "foreground_resume",
        "normal",
        "background",
    ] | None = Field(default=None, description="Omit to preserve this plan range's durable TTS queue priority; explicit values update only the selected groups.")
    start_group_id: str | None = Field(default=None, min_length=1)
    end_group_id: str | None = Field(default=None, min_length=1)
    max_in_flight_groups: int = Field(default=2, ge=1, le=3)
    ordinary_speed_baseline: float | None = Field(
        default=None,
        ge=0.5,
        le=2.0,
        description="连续执行期间冻结的常规语速基线；普通组显式沿用该值，容量恢复最多增加 0.05，不采用无冻结基线时的兼容速度例外。",
    )
    regenerate_existing: bool = False
    repair_timeline_capacity: bool = False

    @model_validator(mode="after")
    def validate_group_selection(self) -> "DubbingProductionExecuteRequest":
        if self.scope == "all_remaining" and self.group_id is not None:
            raise ValueError("all_remaining cannot target one explicit group")
        if self.scope == "single_group" and (
            self.start_group_id is not None or self.end_group_id is not None
        ):
            raise ValueError("single_group cannot define an execution range")
        if self.regenerate_existing and (
            self.scope != "single_group" or self.group_id is None
        ):
            raise ValueError(
                "regenerate_existing requires one explicit single_group"
            )
        if self.repair_timeline_capacity and not self.regenerate_existing:
            raise ValueError(
                "repair_timeline_capacity requires regenerate_existing"
            )
        return self


class DubbingCompletionSnapshot(_StrictContract):
    """Physical delivery facts; never a human or subjective quality approval."""

    schema_version: Literal["dubbing-completion-v1"] = "dubbing-completion-v1"
    source_revision: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    status: Literal["complete", "incomplete", "complete_with_warnings"]
    expected_target_subtitle_ids: list[str] = Field(default_factory=list)
    covered_target_subtitle_ids: list[str] = Field(default_factory=list)
    missing_target_subtitle_ids: list[str] = Field(default_factory=list)
    unplanned_target_subtitle_ids: list[str] = Field(default_factory=list)
    invalid_clip_ids: list[str] = Field(default_factory=list)
    overlapping_clip_pairs: list[tuple[str, str]] = Field(default_factory=list)
    repeated_clip_pairs: list[tuple[str, str]] = Field(default_factory=list)
    missing_prerequisites: list[str] = Field(default_factory=list)
    pending_workflow_ids: list[str] = Field(default_factory=list)
    unplaced_workflow_ids: list[str] = Field(default_factory=list)
    unresolved_group_ids: list[str] = Field(default_factory=list)
    unchecked_clip_ids: list[str] = Field(default_factory=list)
    deferred_manual_timing_group_ids: list[str] = Field(default_factory=list)
    deferred_manual_timing_clip_ids: list[str] = Field(default_factory=list)
    automated_production_status: Literal["resolved", "unresolved"] = "unresolved"


class DubbingManualTimingDeferralResponse(_StrictContract):
    schema_version: Literal["dubbing-manual-timing-deferral-response-v1"] = (
        "dubbing-manual-timing-deferral-response-v1"
    )
    repository_revision: int = Field(ge=0)
    disposition: DubbingManualTimingDeferral
    production_run: DubbingProductionRunSnapshot


class DubbingCandidateContentEvidenceResponse(_StrictContract):
    schema_version: Literal["dubbing-candidate-content-evidence-response-v1"] = (
        "dubbing-candidate-content-evidence-response-v1"
    )
    repository_revision: int = Field(ge=0)
    source_revision: str = Field(min_length=64, max_length=64)
    plan_revision: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    evidence: TtsContentEvidence


class DubbingProductionExecuteResponse(_StrictContract):
    schema_version: Literal["dubbing-production-execution-v1"] = (
        "dubbing-production-execution-v1"
    )
    status: Literal[
        "queued",
        "waiting",
        "complete",
        "needs_attention",
    ]
    scope: Literal["single_group", "all_remaining"]
    group_id: str | None = None
    task_id: str | None = None
    workflow_id: str | None = None
    queued_group_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    workflow_ids: list[str] = Field(default_factory=list)
    required_action: Literal["resolve_capacity"] | None = None
    preflight: DubbingGroupPreflightResult | None = None
    completion: DubbingCompletionSnapshot | None = None
    message: str


__all__ = [
    "DubbingCurrentProjectionRequest",
    "DubbingAudioGapEvidence",
    "DubbingAutomaticAudioEvidence",
    "DubbingBoundaryEvidence",
    "DubbingCandidateCqcInput",
    "DubbingCandidateContentEvidenceRequest",
    "DubbingCandidateContentEvidenceResponse",
    "DubbingCandidateReviewCommand",
    "DubbingSemanticBoundaryAudit",
    "DubbingSemanticBoundaryEvidence",
    "DubbingSemanticBoundaryReview",
    "DubbingStagedCandidateClip",
    "DubbingStagedCandidateProjection",
    "DubbingStagedCandidateSplitRequest",
    "DubbingCandidateCqcPolicy",
    "DubbingCandidateCqcReport",
    "DubbingGenerationGroup",
    "DubbingGenerationPlan",
    "DubbingGenerationPlanInput",
    "DubbingGenerationPolicy",
    "DubbingPauseEvidence",
    "DubbingProductionSnapshot",
    "DubbingProductionGroupProgress",
    "DubbingProductionGroupFailure",
    "DubbingProductionGroupFailureRequest",
    "DubbingCapacityRecoveryEvidence",
    "DubbingManualTimingDeferral",
    "DubbingManualTimingDeferralRequest",
    "DubbingManualTimingDeferralResponse",
    "DubbingProductionManualReviewRequest",
    "DubbingProductionRunSnapshot",
    "DubbingProductionState",
    "DubbingProductionExecuteRequest",
    "DubbingProductionExecuteResponse",
    "DubbingProductionReviewMode",
    "DubbingQualityFinding",
    "DubbingSemanticUnit",
    "DubbingSpeechIsland",
    "DubbingSubjectiveReview",
    "DubbingTimelineAuditInput",
    "DubbingTimelineAuditReport",
    "DubbingTimelineAlignedSlice",
    "DubbingTimelineClip",
    "DubbingTimelineClipSplitCommand",
    "DubbingTimelineEditGate",
    "DubbingTimelineEditGateCommitRequest",
    "DubbingTimelineExpectedUnit",
    "DubbingTimelineSplitRequest",
    "DubbingTranscriptComparison",
    "DubbingVoicedSpan",
    "FindingSeverity",
    "ReviewStatus",
]
