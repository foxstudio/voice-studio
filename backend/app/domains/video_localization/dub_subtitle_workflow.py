"""Execution facade for the six synthesized-dub subtitle atomic tasks."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from app.domains.video_localization import dub_subtitles
from app.domains.video_localization import subtitle_entry_timing
from app.domains.video_localization import subtitle_punctuation
from app.domains.video_localization import transcription
from app.domains.video_localization.development_checkpoints import (
    LocalizationDevelopmentMaterializationStore,
    result_fingerprint,
)
from app.domains.video_localization.workflow_contracts import (
    DUB_SUBTITLE_WORKFLOW_DEFINITION,
)
from app.domains.video_localization.workflow_behavior import (
    source_behavior_fingerprint,
)
from app.domains.video_localization.workflow_graph import (
    WorkflowExecutionPlan,
    WorkflowGraph,
)
from app.domains.video_localization.workflow_ledger import (
    LocalizationWorkflowLedger,
)
from app.errors import AppException
from app.schemas import video_localization_dub_subtitle_step as contracts


_STEP_BEHAVIOR_OBJECTS: dict[str, tuple[object, ...]] = {
    "prepare_track": (
        dub_subtitles.prepare_track,
        dub_subtitles._render_timeline_track,
        dub_subtitles._runtime_clips_from_source_clips,
    ),
    "transcribe_track": (
        dub_subtitles.transcribe_track,
    ),
    "proofread_text": (
        dub_subtitles.proofread_text,
        dub_subtitles._proofread_document_text,
        dub_subtitles._proofread_asr_cues,
        dub_subtitles._canonicalize_reference_display_forms,
    ),
    "align_words": (
        dub_subtitles.align_words,
        transcription.align_segments_strict,
        transcription._strict_aligned_words,
        transcription._reconcile_strict_alignment_tokens,
        subtitle_entry_timing.detect_subtitle_entries,
    ),
    "segment_subtitles": (
        dub_subtitles.segment_subtitles,
        dub_subtitles._cues_from_strict_alignment,
        dub_subtitles._merge_short_semantic_parts,
        dub_subtitles._split_semantic_parts_at_speaker_changes,
        dub_subtitles._speaker_for_aligned_word,
        dub_subtitles._display_text_from_aligned_words,
        dub_subtitles._candidate_subtitles,
        subtitle_punctuation.normalize_display_subtitle_numbers,
        subtitle_punctuation.normalize_display_subtitle_punctuation,
    ),
    "commit": (
        dub_subtitles.merge_generated_subtitles,
        dub_subtitles.ensure_prepare_source_unchanged,
    ),
}


@dataclass(frozen=True)
class DubSubtitleDevelopmentExecutionConfig:
    target_step_id: str
    development_session_id: str
    project_id: str
    snapshot_root: Path
    force_target: bool = True


@dataclass(frozen=True)
class DubSubtitleWorkflowResult:
    prepare_track: contracts.DubSubtitlePrepareTrackOutput | None
    transcribe_track: contracts.DubSubtitleTranscribeTrackOutput | None
    proofread_text: contracts.DubSubtitleProofreadTextOutput | None
    align_words: contracts.DubSubtitleAlignWordsOutput | None
    segment_subtitles: (
        contracts.DubSubtitleSegmentSubtitlesOutput | None
    )
    commit: contracts.DubSubtitleCommitOutput | None
    step_results: dict[str, dict]
    stage_timings: dict[str, dict[str, int | bool]]
    executed_step_ids: list[str]
    reused_step_ids: list[str]
    recomputed_reasons: dict[str, str]


class DubSubtitleWorkflowExecution:
    """Run formal and development targets through the same atomic actions."""

    def __init__(
        self,
        *,
        operation_id: str,
        workflow_input: contracts.DubSubtitleWorkflowInput,
        engine_id: str,
        audio_paths: Mapping[str, Path],
        commit_action: Callable[
            [contracts.DubSubtitleCommitInput],
            contracts.DubSubtitleCommitOutput,
        ],
        development: DubSubtitleDevelopmentExecutionConfig | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_report: Callable[[str, dict], None] | None = None,
        on_preview: Callable[[str, list[dict]], None] | None = None,
    ) -> None:
        self.operation_id = operation_id
        self.workflow_input = workflow_input
        self.engine_id = engine_id
        self.audio_paths = audio_paths
        self.commit_action = commit_action
        self.development = development
        self.is_cancelled = is_cancelled
        self.on_preview = on_preview
        self.graph = WorkflowGraph(DUB_SUBTITLE_WORKFLOW_DEFINITION)
        self.executed_step_ids: list[str] = []
        self.reused_step_ids: list[str] = []
        self.recomputed_reasons: dict[str, str] = {}
        self._lineage_fingerprints: dict[str, str] = {}
        self._result_models: dict[str, type[BaseModel]] = {
            "prepare_track": contracts.DubSubtitlePrepareTrackOutput,
            "transcribe_track": contracts.DubSubtitleTranscribeTrackOutput,
            "proofread_text": contracts.DubSubtitleProofreadTextOutput,
            "align_words": contracts.DubSubtitleAlignWordsOutput,
            "segment_subtitles": (
                contracts.DubSubtitleSegmentSubtitlesOutput
            ),
            "commit": contracts.DubSubtitleCommitOutput,
        }
        self._contract_versions = {
            task.id: str(task.output_contract_version or "")
            for stage in DUB_SUBTITLE_WORKFLOW_DEFINITION.stages
            for task in stage.atomic_tasks
        }
        self._workflow_input_fingerprint = result_fingerprint(
            workflow_input.model_dump(mode="json")
        )
        self.store = (
            LocalizationDevelopmentMaterializationStore(
                development.snapshot_root,
                project_id=development.project_id,
                development_session_id=(
                    development.development_session_id
                ),
            )
            if development is not None
            else None
        )
        self.ledger = LocalizationWorkflowLedger(
            operation_id,
            definition=DUB_SUBTITLE_WORKFLOW_DEFINITION,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            on_report=on_report,
        )

    def run(self) -> DubSubtitleWorkflowResult:
        if self.workflow_input.regeneration_scope is None:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_SCOPE_UNKNOWN",
                "旧开发快照没有保存字幕替换范围，不能安全继续；请重新创建当前步骤快照。",
            )
        target_step_id = (
            self.development.target_step_id
            if self.development is not None
            else "commit"
        )
        plan = self.graph.plan_for(target_step_id)
        if self.store is not None:
            artifact_directory = self.store.artifact_dir_for(
                "prepare_track"
            )
            artifact_directory.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_directory / "full-dub.wav"
            return self._run_plan(plan, artifact_path)
        with tempfile.TemporaryDirectory(
            prefix="voice-studio-dub-subtitle-workflow-"
        ) as temporary_directory:
            return self._run_plan(
                plan,
                Path(temporary_directory) / "full-dub.wav",
            )

    def _run_plan(
        self,
        plan: WorkflowExecutionPlan,
        artifact_path: Path,
    ) -> DubSubtitleWorkflowResult:
        prepared: contracts.DubSubtitlePrepareTrackOutput | None = None
        transcribed: contracts.DubSubtitleTranscribeTrackOutput | None = None
        proofread: contracts.DubSubtitleProofreadTextOutput | None = None
        aligned: contracts.DubSubtitleAlignWordsOutput | None = None
        segmented: contracts.DubSubtitleSegmentSubtitlesOutput | None = None
        committed: contracts.DubSubtitleCommitOutput | None = None
        if not self.workflow_input.prepare_track.clips:
            if self.development is not None:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DUB_SUBTITLE_EMPTY_INCREMENTAL_DEVELOPMENT",
                    "没有可听配音片段时，只能执行正式的字幕删除提交。",
                )
            committed = self._run_side_effect(
                "commit",
                "正在移除已失效的配音字幕",
                lambda: self.commit_action(
                    contracts.DubSubtitleCommitInput(
                        source_revision=self.workflow_input.source_revision,
                        subtitles=[],
                        regeneration_scope=(
                            self.workflow_input.regeneration_scope
                        ),
                    )
                ),
                _project_commit,
            )
            return DubSubtitleWorkflowResult(
                prepare_track=None,
                transcribe_track=None,
                proofread_text=None,
                align_words=None,
                segment_subtitles=None,
                commit=committed,
                step_results=dict(self.ledger.step_results),
                stage_timings=dict(self.ledger.stage_timings),
                executed_step_ids=list(self.executed_step_ids),
                reused_step_ids=list(self.reused_step_ids),
                recomputed_reasons=dict(self.recomputed_reasons),
            )
        if "prepare_track" in plan.required_step_ids:
            prepared = self._run_step(
                "prepare_track",
                "正在按视频时间线渲染完整配音音轨",
                lambda: dub_subtitles.prepare_track(
                    self.workflow_input.prepare_track,
                    audio_paths=self.audio_paths,
                    artifact_id=f"{self.operation_id}:full-dub",
                    artifact_path=artifact_path,
                ),
                _project_prepare_track,
            )
        assert prepared is not None

        if "transcribe_track" in plan.required_step_ids:
            transcribed = self._run_step(
                "transcribe_track",
                "正在听写完整配音音轨",
                lambda: dub_subtitles.transcribe_track(
                    contracts.DubSubtitleTranscribeTrackInput(
                        audio=prepared.audio,
                        engine_id=self.engine_id,
                        audible_clips=(
                            self.workflow_input.prepare_track.clips
                        ),
                    ),
                    audio_path=artifact_path,
                    is_cancelled=self.is_cancelled,
                ),
                _project_transcribe_track,
            )

        if "proofread_text" in plan.required_step_ids:
            assert transcribed is not None
            proofread = self._run_step(
                "proofread_text",
                "正在用本土化文字校正听写错字",
                lambda: dub_subtitles.proofread_text(
                    contracts.DubSubtitleProofreadTextInput(
                        chunks=transcribed.chunks,
                        references=self.workflow_input.references,
                    )
                ),
                _project_proofread_text,
            )

        if "align_words" in plan.required_step_ids:
            assert transcribed is not None
            assert proofread is not None
            aligned = self._run_step(
                "align_words",
                "正在取得配音字词的真实发音时间",
                lambda: dub_subtitles.align_words(
                    contracts.DubSubtitleAlignWordsInput(
                        audio=prepared.audio,
                        language=transcribed.language,
                        chunks=proofread.chunks,
                        video_frame_rate=(
                            self.workflow_input.video_frame_rate
                        ),
                    ),
                    audio_path=artifact_path,
                    is_cancelled=self.is_cancelled,
                ),
                _project_align_words,
            )

        if "segment_subtitles" in plan.required_step_ids:
            assert aligned is not None
            assert proofread is not None
            assert transcribed is not None
            segmented = self._run_step(
                "segment_subtitles",
                "正在按语义组成配音字幕",
                lambda: dub_subtitles.segment_subtitles(
                    contracts.DubSubtitleSegmentSubtitlesInput(
                        audio_sha256=aligned.audio_sha256,
                        asr_requires_review=(
                            transcribed.quality_status == "warning"
                        ),
                        video_duration_ms=(
                            self.workflow_input.prepare_track
                            .timeline_duration_ms
                        ),
                        video_frame_rate=(
                            self.workflow_input.video_frame_rate
                        ),
                        chunks=[
                            contracts.DubSubtitleSegmentTextChunk(
                                cue_id=item.cue_id,
                                text=item.text,
                            )
                            for item in proofread.chunks
                        ],
                        words=aligned.words,
                        subtitle_entry_by_word_id=(
                            aligned.subtitle_entry_by_word_id
                        ),
                        clips=[
                            contracts.DubSubtitleTimelineClip(
                                clip_id=item.clip_id,
                                dub_lane=item.dub_lane,
                                timeline_start_ms=item.timeline_start_ms,
                                timeline_end_ms=item.timeline_end_ms,
                                speaker_id=item.speaker_id,
                            )
                            for item in (
                                self.workflow_input.prepare_track.clips
                            )
                        ],
                    )
                ),
                _project_segment_subtitles,
            )
            self._preview(
                "timing_segmentation",
                [
                    {
                        "cue_id": item.subtitle_id,
                        "subtitle_id": item.subtitle_id,
                        "start_ms": item.start_ms,
                        "end_ms": item.end_ms,
                        "text": item.text,
                    }
                    for item in segmented.subtitles
                ],
            )

        if "commit" in plan.required_step_ids:
            assert segmented is not None
            committed = self._run_side_effect(
                "commit",
                "正在保存独立配音字幕轨",
                lambda: self.commit_action(
                    contracts.DubSubtitleCommitInput(
                        source_revision=self.workflow_input.source_revision,
                        subtitles=segmented.subtitles,
                        regeneration_scope=(
                            self.workflow_input.regeneration_scope
                        ),
                    )
                ),
                _project_commit,
            )

        return DubSubtitleWorkflowResult(
            prepare_track=prepared,
            transcribe_track=transcribed,
            proofread_text=proofread,
            align_words=aligned,
            segment_subtitles=segmented,
            commit=committed,
            step_results=dict(self.ledger.step_results),
            stage_timings=dict(self.ledger.stage_timings),
            executed_step_ids=list(self.executed_step_ids),
            reused_step_ids=list(self.reused_step_ids),
            recomputed_reasons=dict(self.recomputed_reasons),
        )

    def _run_step(
        self,
        step_id: str,
        summary: str,
        action: Callable[[], Any],
        projector: Callable[[Any], dict],
    ) -> Any:
        if self.development is None:
            return self.ledger.run(
                step_id,
                summary,
                action,
                projector,
            )
        assert self.store is not None
        dependency_fingerprints = self._dependency_fingerprints(step_id)
        behavior_fingerprint = self._behavior_fingerprint(step_id)
        lookup = self.store.load(
            step_id,
            result_model=self._result_models[step_id],
            expected_contract_version=self._contract_versions[step_id],
            expected_behavior_fingerprint=behavior_fingerprint,
            expected_dependency_fingerprints=dependency_fingerprints,
        )
        if step_id == "prepare_track" and lookup.status == "valid":
            try:
                dub_subtitles.ensure_prepared_audio_available(
                    lookup.result.audio,
                    audio_path=(
                        self.store.artifact_dir_for("prepare_track")
                        / "full-dub.wav"
                    ),
                )
            except AppException:
                lookup = lookup.model_copy(
                    update={
                        "status": "stale",
                        "reason": "完整配音音轨快照已丢失或变化。",
                        "result": None,
                    }
                )
        force = (
            step_id == self.development.target_step_id
            and self.development.force_target
        )
        if not force and lookup.status == "valid":
            assert lookup.result is not None
            assert lookup.envelope is not None
            self._lineage_fingerprints[step_id] = (
                lookup.envelope.materialization_fingerprint
            )
            self.reused_step_ids.append(step_id)
            return self.ledger.reuse(
                step_id,
                lookup.result,
                projector,
            )
        result = self.ledger.run(
            step_id,
            summary,
            action,
            projector,
        )
        envelope = self.store.save(
            step_id,
            result,
            produced_by_operation_id=self.operation_id,
            behavior_fingerprint=behavior_fingerprint,
            dependency_fingerprints=dependency_fingerprints,
        )
        self._lineage_fingerprints[step_id] = (
            envelope.materialization_fingerprint
        )
        self.executed_step_ids.append(step_id)
        self.recomputed_reasons[step_id] = (
            "当前目标节点按开发要求强制重跑。"
            if force
            else lookup.reason
        )
        return result

    def _run_side_effect(
        self,
        step_id: str,
        summary: str,
        action: Callable[[], Any],
        projector: Callable[[Any], dict],
    ) -> Any:
        if self.development is None:
            return self.ledger.run(
                step_id,
                summary,
                action,
                projector,
            )
        self._dependency_fingerprints(step_id)
        result = self.ledger.run(
            step_id,
            summary,
            action,
            projector,
        )
        self.executed_step_ids.append(step_id)
        self.recomputed_reasons[step_id] = (
            "保存节点有正式数据写入，开发模式也始终重新执行。"
        )
        return result

    def _dependency_fingerprints(self, step_id: str) -> dict[str, str]:
        dependencies = self.graph.dependencies(step_id)
        missing = [
            dependency
            for dependency in dependencies
            if dependency not in self._lineage_fingerprints
        ]
        if missing:
            raise ValueError(
                f"节点 {step_id} 缺少已验证的上游结果："
                + "、".join(missing)
            )
        return {
            dependency: self._lineage_fingerprints[dependency]
            for dependency in dependencies
        }

    def _behavior_fingerprint(self, step_id: str) -> str:
        return source_behavior_fingerprint(
            workflow_schema_version=(
                DUB_SUBTITLE_WORKFLOW_DEFINITION.schema_version
            ),
            workflow_id=DUB_SUBTITLE_WORKFLOW_DEFINITION.workflow_id,
            step_id=step_id,
            output_contract_version=self._contract_versions[step_id],
            implementation_objects=_STEP_BEHAVIOR_OBJECTS[step_id],
            behavior_context={
                "workflow_input_fingerprint": (
                    self._workflow_input_fingerprint
                ),
                "engine_id": self.engine_id,
            },
        )

    def _preview(self, phase: str, cues: list[dict]) -> None:
        if self.on_preview is not None:
            self.on_preview(phase, cues)


def _project_prepare_track(
    result: contracts.DubSubtitlePrepareTrackOutput,
) -> dict:
    return _step_result(
        "prepare_track",
        (
            f"已按原位置合成 {result.clip_count} 个片段，"
            f"完整音轨时长 {result.audio.duration_ms / 1000:.2f} 秒。"
        ),
        metrics=[
            {"label": "完整音轨时长", "value": f"{result.audio.duration_ms} ms"},
            {"label": "合成片段数", "value": str(result.clip_count)},
        ],
        sections=[],
        debug={
            "artifact_id": result.audio.artifact_id,
        },
    )


def _project_transcribe_track(
    result: contracts.DubSubtitleTranscribeTrackOutput,
) -> dict:
    return _step_result(
        "transcribe_track",
        (
            f"完整音轨调用 ASR 1 次，得到 {len(result.chunks)} "
            "个听写音频块；块范围仅用于后续声学对齐。"
        ),
        metrics=[
            {"label": "ASR 调用次数", "value": "1"},
            {"label": "听写音频块", "value": str(len(result.chunks))},
        ],
        sections=[
            {
                "title": "首次听写结果",
                "items": [
                    {
                        "title": item.segment_id,
                        "text": item.text,
                        "facts": [
                            {
                                "label": "音频计算范围",
                                "value": (
                                    f"{item.audio_window_start_ms}–"
                                    f"{item.audio_window_end_ms} ms"
                                ),
                            },
                            {
                                "label": "时间含义",
                                "value": "只用于裁切音频块，不是字幕时间",
                            },
                        ],
                    }
                    for item in result.chunks
                ],
            }
        ],
        debug={
            "engine_id": result.engine_id,
            "quality_status": result.quality_status,
            "quality_flags": result.quality_flags,
        },
    )


def _project_proofread_text(
    result: contracts.DubSubtitleProofreadTextOutput,
) -> dict:
    return _step_result(
        "proofread_text",
        (
            f"完成 {len(result.corrections)} 处文字校正；"
            f"{result.unmatched_reference_char_count} 个仅参考稿存在的"
            "文字没有写入字幕。"
        ),
        metrics=[
            {"label": "校对音频块", "value": str(len(result.chunks))},
            {"label": "文字修改", "value": str(len(result.corrections))},
        ],
        sections=[
            {
                "title": "校对后的听写",
                "items": [
                    {
                        "title": item.cue_id,
                        "before": item.raw_text,
                        "after": item.text,
                        "before_label": "ASR 听写",
                        "after_label": "校对文字",
                        "facts": [
                            {
                                "label": "音频计算范围",
                                "value": (
                                    f"{item.audio_window_start_ms}–"
                                    f"{item.audio_window_end_ms} ms"
                                ),
                            }
                        ],
                    }
                    for item in result.chunks
                ],
            }
        ],
        debug={
            "unmatched_asr_char_count": (
                result.unmatched_asr_char_count
            ),
            "unmatched_reference_char_count": (
                result.unmatched_reference_char_count
            ),
        },
    )


def _project_align_words(
    result: contracts.DubSubtitleAlignWordsOutput,
) -> dict:
    return _step_result(
        "align_words",
        f"取得 {len(result.words)} 个字词的真实发音时间。",
        metrics=[
            {"label": "已对齐字词", "value": str(len(result.words))},
        ],
        sections=[
            {
                "title": "真实字词时间",
                "items": [
                    {
                        "title": item.word_id,
                        "text": item.text,
                        "facts": [
                            {
                                "label": "完整音频时间",
                                "value": (
                                    f"{item.start_ms}–{item.end_ms} ms"
                                ),
                            },
                            {"label": "所属音频块", "value": item.cue_id},
                            {"label": "时间来源", "value": "Forced Aligner"},
                        ],
                    }
                    for item in result.words
                ],
            }
        ],
        debug={
            "alignment_call_count": result.alignment_call_count,
        },
    )


def _project_segment_subtitles(
    result: contracts.DubSubtitleSegmentSubtitlesOutput,
) -> dict:
    return _step_result(
        "segment_subtitles",
        f"按语义组成 {len(result.subtitles)} 条配音字幕。",
        metrics=[
            {"label": "字幕条数", "value": str(len(result.subtitles))},
            {
                "label": "建议复核",
                "value": str(
                    sum(item.needs_review for item in result.subtitles)
                ),
            },
        ],
        sections=[
            {
                "title": "最终字幕候选",
                "items": [
                    {
                        "title": item.subtitle_id,
                        "text": item.text,
                        "facts": [
                            {
                                "label": "字幕时间",
                                "value": (
                                    f"{item.start_ms}–{item.end_ms} ms"
                                ),
                            },
                            {
                                "label": "时间来源",
                                "value": "首尾字词真实发音时间",
                            },
                        ],
                    }
                    for item in result.subtitles
                ],
            }
        ],
        debug={},
    )


def _project_commit(
    result: contracts.DubSubtitleCommitOutput,
) -> dict:
    return _step_result(
        "commit",
        f"已保存 {result.saved_subtitle_count} 条配音字幕。",
        metrics=[
            {
                "label": "已保存字幕",
                "value": str(result.saved_subtitle_count),
            },
            {
                "label": "本次生成",
                "value": str(result.generated_subtitle_count),
            },
            {
                "label": "保留原字幕",
                "value": str(result.preserved_subtitle_count),
            },
        ],
        sections=[],
        debug={
            "write_scope": "dub_subtitles",
            "preserved_tracks": [
                "source_asr_subtitles",
                "localized_subtitles",
                "dub_audio_clips",
            ],
        },
    )


def _step_result(
    step_id: str,
    summary: str,
    *,
    metrics: list[dict],
    sections: list[dict],
    debug: dict,
) -> dict:
    task = next(
        task
        for stage in DUB_SUBTITLE_WORKFLOW_DEFINITION.stages
        for task in stage.atomic_tasks
        if task.id == step_id
    )
    return {
        "label": task.label,
        "order": task.order,
        "status": "success",
        "purpose": task.description,
        "summary": summary,
        "metrics": metrics,
        "sections": sections,
        "debug": {
            "description": "原子子流程输入、结果与诊断信息。",
            "metrics": [
                {
                    "label": key,
                    "value": (
                        value
                        if isinstance(value, str)
                        else str(value)
                    ),
                }
                for key, value in debug.items()
            ],
            "sections": [],
            "notes": [],
        },
    }


__all__ = [
    "DubSubtitleDevelopmentExecutionConfig",
    "DubSubtitleWorkflowExecution",
    "DubSubtitleWorkflowResult",
]
