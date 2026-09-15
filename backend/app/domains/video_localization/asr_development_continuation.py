"""Typed, development-only continuation between completed ASR atomic tasks.

This module never commits project data.  It derives one target input from the
immediately preceding successful development result plus immutable inputs from
the original workflow lineage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domains.video_localization import (
    asr_flow,
    asr_pipeline,
    asr_timing_contracts,
    development_checkpoints,
    document_understanding_contracts,
    review_decisions,
    source_pipeline,
    transcript_quality_gate,
    transcription,
    whole_recheck,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationTranscriptionState,
)
from app.errors import AppException


CONTINUATION_STEP_ORDER = (
    "review_decisions_r1",
    "whole_recheck_r1",
    "transcript_quality_gate",
    "alignment",
    "audio_boundaries",
    "boundary_review",
    "subtitle_track",
)


class AsrDevelopmentContinuationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-development-continuation-state-v1"] = (
        "asr-development-continuation-state-v1"
    )
    lineage_operation_id: str
    last_step_id: str
    transcription: VideoLocalizationTranscriptionState


_RESULT_MODELS = {
    "review_decisions_r1": review_decisions.AsrReviewDecisionsResult,
    "whole_recheck_r1": whole_recheck.AsrWholeRecheckResult,
    "transcript_quality_gate": transcript_quality_gate.AsrTranscriptQualityGateResult,
    "alignment": asr_timing_contracts.AsrAlignmentResult,
    "audio_boundaries": asr_timing_contracts.AsrAudioBoundariesResult,
    "boundary_review": asr_timing_contracts.AsrBoundaryReviewResult,
    "subtitle_track": asr_timing_contracts.AsrSubtitleTrackResult,
}


def _merged_quality_flags(
    state: VideoLocalizationTranscriptionState,
    metadata: dict,
) -> list[str]:
    return sorted(
        {
            *state.quality_flags,
            *(
                str(value)
                for value in (metadata.get("quality_flags") or [])
                if str(value).strip()
            ),
        }
    )


def _load(
    root: Path,
    *,
    project_id: str,
    operation_id: str,
    step_id: str,
    model: type[BaseModel],
) -> BaseModel:
    value = development_checkpoints.load_development_checkpoint(
        root,
        project_id=project_id,
        workflow_operation_id=operation_id,
        step_id=step_id,
        result_model=model,
    )
    if value is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_SNAPSHOT_INVALID",
            "ASR 开发续接缺少有效的类型化快照，未运行后续节点。",
            {"operation_id": operation_id, "step_id": step_id},
        )
    return value


def expected_predecessor(target_step_id: str) -> str:
    try:
        index = CONTINUATION_STEP_ORDER.index(target_step_id)
    except ValueError as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_TARGET_INVALID",
            "该 ASR 节点不支持从前驱结果续接。",
        ) from exc
    if index == 0:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_TARGET_INVALID",
            "复查决定节点没有可续接的前驱节点。",
        )
    return CONTINUATION_STEP_ORDER[index - 1]


def _base_state(
    root: Path,
    *,
    project_id: str,
    lineage_operation_id: str,
    segments,
    review_status: str,
    review_profile_id: str | None,
    review_model_id: str | None,
) -> VideoLocalizationTranscriptionState:
    workflow = _load(
        root,
        project_id=project_id,
        operation_id=lineage_operation_id,
        step_id="workflow_input",
        model=asr_pipeline.AsrPipelineInput,
    )
    raw = _load(
        root,
        project_id=project_id,
        operation_id=lineage_operation_id,
        step_id="asr_result",
        model=transcription.TranscribeRawOutput,
    )
    joined = _load(
        root,
        project_id=project_id,
        operation_id=lineage_operation_id,
        step_id="initial_analysis_join_result",
        model=asr_pipeline.AsrJoinedTranscript,
    )
    assert isinstance(workflow, asr_pipeline.AsrPipelineInput)
    assert isinstance(raw, transcription.TranscribeRawOutput)
    assert isinstance(joined, asr_pipeline.AsrJoinedTranscript)
    if (
        raw.input.audio_sha256 != workflow.source_audio_sha256
        or raw.input.source_track_id != workflow.source_track_id
        or joined.raw_asr.model_dump(mode="json")
        != raw.model_dump(mode="json")
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
            "ASR 开发续接的原始听写、汇合结果和工作流音轨不一致。",
        )
    diarization = joined.diarization
    initial_quality_flags = {
        *raw.quality_summary.warning_codes,
        "asr_flow_reviewed",
        f"asr_prompt:{asr_flow.PROMPT_VERSION}",
    }
    if diarization is not None:
        initial_quality_flags.update(diarization.quality_flags)
    return VideoLocalizationTranscriptionState(
        language=raw.language,
        source_track_id=workflow.source_track_id,
        source_audio_sha256=workflow.source_audio_sha256,
        alignment_source_track_id=workflow.alignment_source_track_id,
        alignment_audio_sha256=workflow.alignment_audio_sha256,
        engine_id=workflow.engine_id,
        raw_text=raw.raw_text,
        raw_asr_warning_codes=list(raw.quality_summary.warning_codes),
        raw_asr_incomplete_ranges=list(raw.incomplete_chunk_ranges),
        corrected_text=" ".join(
            (item.corrected_text or item.raw_text).strip()
            for item in segments
            if (item.corrected_text or item.raw_text).strip()
        ),
        segments=[item.model_copy(deep=True) for item in segments],
        diarization_status=(
            diarization.status if diarization is not None else "not_run"
        ),
        diarization_engine_id=(
            diarization.engine_id if diarization is not None else None
        ),
        diarization_model_id=(
            diarization.model_id if diarization is not None else None
        ),
        diarization_error=(
            diarization.error if diarization is not None else None
        ),
        speaker_clusters=(
            [item.model_copy(deep=True) for item in diarization.clusters]
            if diarization is not None
            else []
        ),
        review_status=review_status,
        review_profile_id=review_profile_id,
        review_model_id=review_model_id,
        review_prompt_version=asr_flow.PROMPT_VERSION,
        segmentation_profile_id=workflow.segmentation_profile_id,
        quality_flags=sorted(initial_quality_flags),
    )


def _previous_state(
    root: Path,
    *,
    project_id: str,
    predecessor_operation_id: str,
) -> AsrDevelopmentContinuationState:
    value = _load(
        root,
        project_id=project_id,
        operation_id=predecessor_operation_id,
        step_id="continuation_state",
        model=AsrDevelopmentContinuationState,
    )
    assert isinstance(value, AsrDevelopmentContinuationState)
    return value


def _state_before_direct_target(
    root: Path,
    *,
    project_id: str,
    lineage_operation_id: str,
    target_step_id: str,
) -> VideoLocalizationTranscriptionState:
    """Rebuild only the target's ancestors from the original typed results."""

    predecessor_step_id = expected_predecessor(target_step_id)
    predecessor = _load(
        root,
        project_id=project_id,
        operation_id=lineage_operation_id,
        step_id=f"{predecessor_step_id}_result",
        model=_RESULT_MODELS[predecessor_step_id],
    )
    state = build_state_after_result(
        root,
        project_id=project_id,
        lineage_operation_id=lineage_operation_id,
        predecessor_operation_id=None,
        target_step_id=predecessor_step_id,
        result=predecessor,
    )
    if state is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_SNAPSHOT_INVALID",
            "ASR 开发续接无法从目标节点的上游类型化结果重建状态。",
        )
    return state.transcription


def _validate_result_input_against_state(
    state: VideoLocalizationTranscriptionState,
    *,
    target_step_id: str,
    result: BaseModel,
) -> None:
    def segment_identity(item) -> tuple:
        return (
            item.segment_id,
            item.start_ms,
            item.end_ms,
            item.raw_text,
            item.corrected_text,
            item.speaker_cluster_id,
            item.speaker_confidence,
            item.has_speaker_overlap,
        )

    matches = True
    if isinstance(result, transcript_quality_gate.AsrTranscriptQualityGateResult):
        matches = [segment_identity(item) for item in state.segments] == [
            segment_identity(item) for item in result.input.segments
        ]
    elif isinstance(result, asr_timing_contracts.AsrAlignmentResult):
        matches = [segment_identity(item) for item in state.segments] == [
            segment_identity(item) for item in result.input.segments
        ]
    elif isinstance(result, asr_timing_contracts.AsrAudioBoundariesResult):
        matches = [item.model_dump(mode="json") for item in state.words] == [
            item.model_dump(mode="json") for item in result.input.words
        ]
    elif isinstance(result, asr_timing_contracts.AsrBoundaryReviewResult):
        matches = (
            [item.model_dump(mode="json") for item in state.words]
            == [item.model_dump(mode="json") for item in result.input.words]
            and [
                item.model_dump(mode="json")
                for item in state.audio_boundary_features
            ]
            == [
                item.model_dump(mode="json")
                for item in result.input.boundary_features
            ]
        )
    if not matches:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
            f"ASR 开发节点 {target_step_id} 的输入与重建上游状态不一致。",
        )


def _segment_state_update(segments) -> dict:
    copied = [item.model_copy(deep=True) for item in segments]
    return {
        "segments": copied,
        "corrected_text": " ".join(
            (item.corrected_text or item.raw_text).strip()
            for item in copied
            if (item.corrected_text or item.raw_text).strip()
        ),
    }


def build_target_input(
    root: Path,
    *,
    project_id: str,
    lineage_operation_id: str,
    predecessor_operation_id: str,
    predecessor_step_id: str,
    target_step_id: str,
) -> BaseModel:
    """Build exactly one target input and reject crossed/stale lineage."""

    if expected_predecessor(target_step_id) != predecessor_step_id:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
            "ASR 开发续接必须按节点顺序使用直接前驱结果。",
        )
    predecessor = _load(
        root,
        project_id=project_id,
        operation_id=predecessor_operation_id,
        step_id=f"{predecessor_step_id}_result",
        model=_RESULT_MODELS[predecessor_step_id],
    )
    workflow = _load(
        root,
        project_id=project_id,
        operation_id=lineage_operation_id,
        step_id="workflow_input",
        model=asr_pipeline.AsrPipelineInput,
    )
    assert isinstance(workflow, asr_pipeline.AsrPipelineInput)

    if target_step_id == "whole_recheck_r1":
        understanding = _load(
            root,
            project_id=project_id,
            operation_id=lineage_operation_id,
            step_id="understand_document_result",
            model=document_understanding_contracts.AsrDocumentUnderstandingResult,
        )
        assert isinstance(predecessor, review_decisions.AsrReviewDecisionsResult)
        assert isinstance(understanding, document_understanding_contracts.AsrDocumentUnderstandingResult)
        return whole_recheck.build_whole_recheck_input(
            predecessor,
            understanding,
            upstream_operation_id=predecessor_operation_id,
            understanding_operation_id=lineage_operation_id,
            profile_id=predecessor.profile_id or workflow.llm_profile_id,
        )

    state = _load_or_rebuild_state(
        root,
        project_id=project_id,
        lineage_operation_id=lineage_operation_id,
        predecessor_operation_id=predecessor_operation_id,
        predecessor_step_id=predecessor_step_id,
        predecessor_result=predecessor,
    )
    if state.lineage_operation_id != lineage_operation_id or state.last_step_id != predecessor_step_id:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
            "ASR 开发续接快照不属于指定原流程或直接前驱。",
        )
    if target_step_id == "transcript_quality_gate":
        assert isinstance(predecessor, whole_recheck.AsrWholeRecheckResult)
        return transcript_quality_gate.build_input(
            predecessor,
            upstream_operation_id=predecessor_operation_id,
            source_track_id=workflow.source_track_id,
            source_audio_sha256=workflow.source_audio_sha256,
            segments=state.transcription.segments,
        )
    if target_step_id == "alignment":
        assert isinstance(predecessor, transcript_quality_gate.AsrTranscriptQualityGateResult)
        if [item.model_dump(mode="json") for item in state.transcription.segments] != [
            item.model_dump(mode="json") for item in predecessor.input.segments
        ]:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
                "进入校时前检查结果和续接文字快照不一致。",
            )
        if transcript_quality_gate.blocks_alignment(predecessor):
            raise AppException(409, "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_FAILED", "进入校时前检查发现技术或结构问题，未执行逐词时间对齐。")
        joined = _load(
            root,
            project_id=project_id,
            operation_id=lineage_operation_id,
            step_id="initial_analysis_join_result",
            model=asr_pipeline.AsrJoinedTranscript,
        )
        assert isinstance(joined, asr_pipeline.AsrJoinedTranscript)
        return asr_timing_contracts.AsrAlignmentInput(
            alignment_audio_path=workflow.alignment_audio_path,
            alignment_audio_sha256=workflow.alignment_audio_sha256,
            alignment_source_track_id=workflow.alignment_source_track_id,
            segments=[
                item.model_copy(deep=True)
                for item in state.transcription.segments
            ],
            diarization_segments=(
                [
                    item.model_copy(deep=True)
                    for item in joined.diarization.segments
                ]
                if joined.diarization is not None
                else []
            ),
            language=state.transcription.language,
            duration_ms=workflow.duration_ms,
        )
    if target_step_id == "audio_boundaries":
        assert isinstance(predecessor, asr_timing_contracts.AsrAlignmentResult)
        if [item.model_dump(mode="json") for item in state.transcription.words] != [
            item.model_dump(mode="json") for item in predecessor.words
        ]:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
                "逐词校时结果和续接状态不一致。",
            )
        return asr_timing_contracts.AsrAudioBoundariesInput(
            audio_path=workflow.audio_path,
            audio_sha256=workflow.source_audio_sha256,
            source_track_id=workflow.source_track_id,
            words=[item.model_copy(deep=True) for item in predecessor.words],
            video_frame_rate=workflow.source_video_frame_rate,
        )
    if target_step_id == "boundary_review":
        assert isinstance(predecessor, asr_timing_contracts.AsrAudioBoundariesResult)
        if (
            [item.model_dump(mode="json") for item in state.transcription.words]
            != [item.model_dump(mode="json") for item in predecessor.input.words]
            or [
                item.model_dump(mode="json")
                for item in state.transcription.audio_boundary_features
            ]
            != [
                item.model_dump(mode="json")
                for item in predecessor.boundary_features
            ]
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
                "声音边界结果和续接状态不一致。",
            )
        return asr_timing_contracts.AsrBoundaryReviewInput(
            words=[
                item.model_copy(deep=True)
                for item in predecessor.input.words
            ],
            boundary_features=[
                item.model_copy(deep=True)
                for item in predecessor.boundary_features
            ],
            language=state.transcription.language,
            segmentation_profile_id=workflow.segmentation_profile_id,
            audio_analysis_available=(
                predecessor.metadata.get("status") == "completed"
            ),
            profile_id=workflow.llm_profile_id,
            existing_reviews=[
                item.model_copy(deep=True)
                for item in workflow.existing_boundary_reviews
            ],
        )
    assert target_step_id == "subtitle_track"
    assert isinstance(predecessor, asr_timing_contracts.AsrBoundaryReviewResult)
    if [
        item.model_dump(mode="json")
        for item in state.transcription.boundary_reviews
    ] != [item.model_dump(mode="json") for item in predecessor.reviews]:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
            "断句复核结果和续接状态不一致。",
        )
    return asr_timing_contracts.AsrSubtitleTrackInput(
        transcription=state.transcription.model_copy(deep=True),
        existing_speakers=[],
        source_duration_ms=(
            workflow.source_video_duration_ms or workflow.duration_ms
        ),
        video_frame_rate=workflow.source_video_frame_rate,
    )


def _load_or_rebuild_state(
    root: Path,
    *,
    project_id: str,
    lineage_operation_id: str,
    predecessor_operation_id: str,
    predecessor_step_id: str,
    predecessor_result: BaseModel | None,
) -> AsrDevelopmentContinuationState:
    try:
        return _previous_state(
            root,
            project_id=project_id,
            predecessor_operation_id=predecessor_operation_id,
        )
    except AppException as exc:
        if exc.code != "VIDEO_LOCALIZATION_ASR_CONTINUATION_SNAPSHOT_INVALID":
            raise
    if predecessor_result is None:
        predecessor_result = _load(
            root,
            project_id=project_id,
            operation_id=predecessor_operation_id,
            step_id=f"{predecessor_step_id}_result",
            model=_RESULT_MODELS[predecessor_step_id],
        )
    rebuilt = build_state_after_result(
        root,
        project_id=project_id,
        lineage_operation_id=lineage_operation_id,
        predecessor_operation_id=None,
        target_step_id=predecessor_step_id,
        result=predecessor_result,
    )
    if rebuilt is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_CONTINUATION_SNAPSHOT_INVALID",
            "ASR 开发续接缺少可重建的直接前驱状态。",
        )
    return rebuilt


def build_state_after_result(
    root: Path,
    *,
    project_id: str,
    lineage_operation_id: str,
    predecessor_operation_id: str | None,
    target_step_id: str,
    result: BaseModel,
) -> AsrDevelopmentContinuationState | None:
    """Carry only the rebuilt transcript tail to the next development node."""

    if target_step_id == "subtitle_track":
        return None
    if target_step_id == "review_decisions_r1":
        assert isinstance(result, review_decisions.AsrReviewDecisionsResult)
        transcription_state = _base_state(
            root,
            project_id=project_id,
            lineage_operation_id=lineage_operation_id,
            segments=result.updated_segments,
            review_status=result.status,
            review_profile_id=result.profile_id,
            review_model_id=None,
        )
    elif target_step_id == "whole_recheck_r1":
        assert isinstance(result, whole_recheck.AsrWholeRecheckResult)
        transcription_state = _base_state(
            root,
            project_id=project_id,
            lineage_operation_id=lineage_operation_id,
            segments=result.input.segments,
            review_status=result.input.upstream_status,
            review_profile_id=result.profile_id,
            review_model_id=result.model_id,
        )
    elif predecessor_operation_id:
        predecessor_step_id = expected_predecessor(target_step_id)
        transcription_state = _load_or_rebuild_state(
            root,
            project_id=project_id,
            lineage_operation_id=lineage_operation_id,
            predecessor_operation_id=predecessor_operation_id,
            predecessor_step_id=predecessor_step_id,
            predecessor_result=None,
        ).transcription.model_copy(deep=True)
    else:
        transcription_state = _state_before_direct_target(
            root,
            project_id=project_id,
            lineage_operation_id=lineage_operation_id,
            target_step_id=target_step_id,
        )
        _validate_result_input_against_state(
            transcription_state,
            target_step_id=target_step_id,
            result=result,
        )
    if isinstance(result, transcript_quality_gate.AsrTranscriptQualityGateResult):
        transcription_state = transcription_state.model_copy(
            update={
                **_segment_state_update(result.input.segments),
                "transcript_quality_cycle": result.model_dump(mode="json"),
            },
            deep=True,
        )
    elif isinstance(result, asr_timing_contracts.AsrAlignmentResult):
        transcription_state = transcription_state.model_copy(
            update={
                **_segment_state_update(result.input.segments),
                "words": [item.model_copy(deep=True) for item in result.words],
                "alignment_status": result.metadata.get("status", "completed"),
                "alignment_engine_id": result.metadata.get("engine_id"),
                "alignment_error": result.metadata.get("error"),
                "timing_confidence": result.metadata.get(
                    "timing_confidence", "low"
                ),
                "quality_flags": _merged_quality_flags(
                    transcription_state, result.metadata
                ),
            },
            deep=True,
        )
    elif isinstance(result, asr_timing_contracts.AsrAudioBoundariesResult):
        transcription_state = transcription_state.model_copy(update={"audio_boundary_status": result.metadata.get("status", "completed"), "audio_boundary_analysis_version": result.metadata.get("analysis_version"), "audio_boundary_error": result.metadata.get("error"), "audio_boundary_features": [item.model_copy(deep=True) for item in result.boundary_features], "subtitle_entry_by_word_id": dict(result.subtitle_entry_by_word_id), "quality_flags": _merged_quality_flags(transcription_state, result.metadata)}, deep=True)
    elif isinstance(result, asr_timing_contracts.AsrBoundaryReviewResult):
        transcription_state = transcription_state.model_copy(update={"boundary_review_status": result.metadata.get("status", "completed"), "boundary_review_profile_id": result.metadata.get("profile_id"), "boundary_review_model_id": result.metadata.get("model_id"), "boundary_review_prompt_version": result.metadata.get("prompt_version"), "boundary_review_error": result.metadata.get("error"), "boundary_reviews": [item.model_copy(deep=True) for item in result.reviews], "quality_flags": _merged_quality_flags(transcription_state, result.metadata)}, deep=True)
    return AsrDevelopmentContinuationState(lineage_operation_id=lineage_operation_id, last_step_id=target_step_id, transcription=transcription_state)


__all__ = [
    "AsrDevelopmentContinuationState",
    "CONTINUATION_STEP_ORDER",
    "build_state_after_result",
    "build_target_input",
    "expected_predecessor",
]
