"""Development-only replay of one canonical ASR atomic task.

Formal execution never imports historical task outputs from this module. A
developer explicitly selects a source workflow operation and one target step;
the target then receives the exact typed input captured at its original entry
boundary and runs the same public domain implementation used by the full flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from app.domains.video_localization import (
    asr_pipeline,
    asr_timing_contracts,
    development_checkpoints,
    document_understanding_contracts,
    entity_normalization,
    research_evidence,
    review_decisions,
    section_review,
    source_pipeline,
    speaker_diarization,
    transcript_quality_gate,
    transcription,
    visual_evidence,
    whole_recheck,
    workflow_contracts,
)
from app.errors import AppException


ASR_DEVELOPMENT_TARGET_STEP_IDS = tuple(task.id for task in workflow_contracts.asr_development_replay_tasks())


@dataclass(frozen=True)
class AsrDevelopmentReplayExecution:
    target_step_id: str
    source_snapshot_step_id: str
    result: BaseModel
    skipped_paid_preparation: bool = False


def _load_required(
    root: Path,
    *,
    project_id: str,
    source_operation_id: str,
    step_id: str,
    result_model: type[BaseModel],
) -> BaseModel:
    result = development_checkpoints.load_development_checkpoint(
        root,
        project_id=project_id,
        workflow_operation_id=source_operation_id,
        step_id=step_id,
        result_model=result_model,
    )
    if result is None:
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_ASR_DEVELOPMENT_SNAPSHOT_NOT_FOUND",
            (
                f"没有找到 {step_id} 的有效开发快照。"
                "如果该节点在原流程中尚未开始、被跳过，或输入契约已经变化，"
                "需要从受影响的更早节点重新生成快照。"
            ),
            {
                "source_operation_id": source_operation_id,
                "snapshot_step_id": step_id,
            },
        )
    return result


def _load_research_input(
    root: Path,
    *,
    project_id: str,
    source_operation_id: str,
) -> research_evidence.ResearchEvidenceInput:
    for model in (
        research_evidence.AsrResearchEvidenceInputV2,
        research_evidence.AsrResearchEvidenceInput,
    ):
        result = development_checkpoints.load_development_checkpoint(
            root,
            project_id=project_id,
            workflow_operation_id=source_operation_id,
            step_id="research_input",
            result_model=model,
        )
        if result is not None:
            return result
    raise AppException(
        404,
        "VIDEO_LOCALIZATION_ASR_DEVELOPMENT_SNAPSHOT_NOT_FOUND",
        "没有找到 research_input 的有效开发快照。",
        {
            "source_operation_id": source_operation_id,
            "snapshot_step_id": "research_input",
        },
    )


def _workflow_runtime_input(
    root: Path,
    *,
    project_id: str,
    source_operation_id: str,
) -> asr_pipeline.AsrPipelineInput:
    return _load_required(
        root,
        project_id=project_id,
        source_operation_id=source_operation_id,
        step_id="workflow_input",
        result_model=asr_pipeline.AsrPipelineInput,
    )


def _run_review_decisions(
    root: Path,
    *,
    project_id: str,
    source_operation_id: str,
    target_step_id: str,
    is_cancelled: Callable[[], bool] | None,
) -> AsrDevelopmentReplayExecution:
    prepared_step_id = f"{target_step_id}_prepared"
    prepared = development_checkpoints.load_development_checkpoint(
        root,
        project_id=project_id,
        workflow_operation_id=source_operation_id,
        step_id=prepared_step_id,
        result_model=review_decisions.AsrReviewDecisionsPreparedSnapshot,
    )
    if prepared is not None:
        return AsrDevelopmentReplayExecution(
            target_step_id=target_step_id,
            source_snapshot_step_id=prepared_step_id,
            result=(
                review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run_prepared(
                    prepared,
                    is_cancelled=is_cancelled,
                )
            ),
            skipped_paid_preparation=True,
        )
    request = _load_required(
        root,
        project_id=project_id,
        source_operation_id=source_operation_id,
        step_id=f"{target_step_id}_input",
        result_model=review_decisions.AsrReviewDecisionsInput,
    )
    return AsrDevelopmentReplayExecution(
        target_step_id=target_step_id,
        source_snapshot_step_id=f"{target_step_id}_input",
        result=asr_pipeline.DEFAULT_ASR_PIPELINE.run_review_decisions(
            request,
            context=asr_pipeline.AsrRunContext(
                is_cancelled=is_cancelled,
            ),
        ),
    )


def replay_asr_target(
    *,
    root: Path,
    project_id: str,
    source_operation_id: str,
    replay_operation_id: str,
    target_step_id: str,
    is_cancelled: Callable[[], bool] | None = None,
) -> AsrDevelopmentReplayExecution:
    """Replay exactly one ASR task from its historical typed input."""

    if target_step_id not in ASR_DEVELOPMENT_TARGET_STEP_IDS:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_DEVELOPMENT_TARGET_INVALID",
            "ASR 开发目标节点不存在。",
            {"available_target_step_ids": list(ASR_DEVELOPMENT_TARGET_STEP_IDS)},
        )
    if target_step_id.startswith("review_decisions_r"):
        return _run_review_decisions(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            target_step_id=target_step_id,
            is_cancelled=is_cancelled,
        )

    input_step_id = f"{target_step_id}_input"
    pipeline = asr_pipeline.DEFAULT_ASR_PIPELINE
    context = asr_pipeline.AsrRunContext(
        is_cancelled=is_cancelled,
    )
    if target_step_id == "asr":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=transcription.TranscribeRawInput,
        )
        result = pipeline.run_raw_asr(request, context=context)
    elif target_step_id == "diarization":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=speaker_diarization.DiarizeSpeakersInput,
        )
        result = pipeline.run_speaker_diarization(
            request,
            context=context,
        )
    elif target_step_id == "initial_analysis_join":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=asr_pipeline.AsrInitialAnalysisResult,
        )
        result = pipeline.join_initial_analysis(
            request,
            context=context,
        )
    elif target_step_id == "understand_document":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=(document_understanding_contracts.AsrDocumentUnderstandingInput),
        )
        result = pipeline.run_document_understanding(
            request,
            context=context,
        )
    elif target_step_id == "visual_evidence":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=visual_evidence.AsrVisualEvidenceInput,
        )
        runtime = _workflow_runtime_input(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
        )
        if not runtime.source_video_path or runtime.source_video_sha256 != request.video_sha256:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ASR_DEVELOPMENT_SOURCE_CHANGED",
                "画面取证快照对应的源视频已经无法确认，不能误用其他视频重跑。",
            )
        result = pipeline.run_visual_evidence(
            request,
            source_video_path=runtime.source_video_path,
            frame_dir=(Path(root) / replay_operation_id / "visual-evidence-frames"),
            context=context,
        )
    elif target_step_id == "research":
        request = _load_research_input(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
        )
        result = pipeline.run_research_evidence(
            request,
            context=context,
            cache_dir=(Path(root) / replay_operation_id / "research-cache"),
        )
    elif target_step_id == "normalize_entities":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=entity_normalization.AsrEntityNormalizationInput,
        )
        result = pipeline.run_entity_normalization(
            request,
            context=context,
        )
    elif target_step_id.startswith("section_review_r"):
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=section_review.AsrSectionReviewInput,
        )
        result = pipeline.run_section_review(
            request,
            context=context,
        )
    elif target_step_id.startswith("whole_recheck_r"):
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=whole_recheck.AsrWholeRecheckInput,
        )
        result = pipeline.run_whole_recheck(
            request,
            context=context,
        )
    elif target_step_id == "transcript_quality_gate":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=(transcript_quality_gate.AsrTranscriptQualityGateInput),
        )
        result = pipeline.run_transcript_quality_gate(
            request,
            context=context,
        )
    elif target_step_id == "alignment":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=asr_timing_contracts.AsrAlignmentInput,
        )
        result = transcription.run_alignment_step(request)
    elif target_step_id == "audio_boundaries":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=asr_timing_contracts.AsrAudioBoundariesInput,
        )
        result = transcription.run_audio_boundaries_step(request)
    elif target_step_id == "boundary_review":
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=asr_timing_contracts.AsrBoundaryReviewInput,
        )
        result = transcription.run_boundary_review_step(
            request,
            is_cancelled=is_cancelled,
        )
    else:
        request = _load_required(
            root,
            project_id=project_id,
            source_operation_id=source_operation_id,
            step_id=input_step_id,
            result_model=asr_timing_contracts.AsrSubtitleTrackInput,
        )
        result = source_pipeline.run_subtitle_track_step(request)

    return AsrDevelopmentReplayExecution(
        target_step_id=target_step_id,
        source_snapshot_step_id=input_step_id,
        result=result,
    )


def expected_output_contract(target_step_id: str) -> str:
    for task in workflow_contracts.asr_development_replay_tasks():
        if task.id == target_step_id:
            return task.output_contract_version or ""
    return ""


def development_task_definition(
    target_step_id: str,
) -> workflow_contracts.WorkflowAtomicTaskDefinition:
    for task in workflow_contracts.asr_development_replay_tasks():
        if task.id == target_step_id:
            return task
    raise KeyError(target_step_id)


def output_contract_matches(
    target_step_id: str,
    actual_contract_version: str,
) -> bool:
    """Accept declared compatibility variants without weakening other nodes."""

    if target_step_id == "asr":
        return actual_contract_version in {
            expected_output_contract(target_step_id),
            "asr-raw-v2",
        }
    if target_step_id == "research":
        return actual_contract_version in {
            "asr-research-evidence-v1",
            "asr-research-evidence-v2",
        }
    return actual_contract_version == expected_output_contract(target_step_id)


__all__ = [
    "ASR_DEVELOPMENT_TARGET_STEP_IDS",
    "AsrDevelopmentReplayExecution",
    "expected_output_contract",
    "output_contract_matches",
    "replay_asr_target",
]
