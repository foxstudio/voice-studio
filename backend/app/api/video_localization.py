from __future__ import annotations

from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision
from app.schemas.video_localization_dubbing_preflight import DubbingGroupPreflightResult
from app.schemas.video_localization_dubbing_production import DubbingCompletionSnapshot
from app.schemas.video_localization_asr_repair import AsrSourceRepairRequest
from app.schemas.video_localization_binding_repair import BindingRepairRequest

import asyncio
import json
from typing import Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    File,
    Query,
    Response,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.video_localization_responses import (
    audio_file_response,
    download_file_response,
    json_attachment,
    media_file_response,
    require_resource,
    srt_attachment,
)
from app.api.video_localization_public_contracts import (
    PublicAsrInitialAnalysisSnapshot,
    PublicDiarizeSpeakersOutput,
    PublicProject as Project,
    PublicTranscribeRawOutput,
    PublicVideoLocalizationDraft as VideoLocalizationDraftResponse,
    PublicVideoLocalizationEditableDraft as VideoLocalizationEditableDraftResponse,
    PublicVideoLocalizationOperation as VideoLocalizationOperation,
    PublicVideoLocalizationOperationFeedV2 as VideoLocalizationOperationFeedV2,
    PublicVideoLocalizationTimelineMutation as VideoLocalizationTimelineMutationResponse,
    PublicVideoLocalizationTimelineProjection as VideoLocalizationTimelineProjectionResponse,
    PublicVideoLocalizationWorkspace as VideoLocalizationWorkspaceResponse,
    PublicVideoLocalizationWorkspaceDetail as VideoLocalizationWorkspaceDetailResponse,
    public_video_localization_export,
    public_timeline_edit_receipt,
)
from app.domains.video_localization import (
    asr_pipeline,
    document_understanding_contracts,
    entity_normalization,
    localization_requirements,
    media_assets,
    preview_cache_contracts,
    preview_media_contracts,
    research_evidence,
    review_decisions,
    section_review,
    speaker_diarization,
    state_ownership,
    transcript_quality_gate,
    transcription,
    tts_history,
    visual_evidence,
    whole_recheck,
    workflow_contracts,
    project_snapshot_projection,
)
from app.domains.video_localization.export_contracts import (
    VideoLocalizationExportDestinationRequest,
    VideoLocalizationExportDestinationResponse,
    VideoLocalizationMediaExportCommandRequest,
    VideoLocalizationMediaExportFilenamePreviewResponse,
    VideoLocalizationMediaExportRequest,
)
from app.domains.video_localization.export_filenames import (
    ExportFilenameSpec,
)
from app.domains.video_localization import service as video_localization_service
from app.domains.video_localization.tts_parameter_pack import TtsParameterPack
from app.domains.video_localization.tts_selection import TtsSelectionRequest
from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationAsrVadTimingCorrectionRequest,
    VideoLocalizationCue,
    VideoLocalizationCueTimingConfirmationRequest,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationOperationRequest,
    VideoLocalizationSpeakerCreate,
    VideoLocalizationSpeakerUpdate,
    VideoLocalizationSpokenSegmentUpdate,
    VideoLocalizationSubtitleCue,
    VideoLocalizationSubtitleCueUpdate,
    VideoLocalizationSubtitleImportRequest,
)
from app.schemas.voice_studio import (
    GenerateRequest,
    ProjectSummary,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskFeed,
)
from app.schemas.video_localization_dubbing_production import (
    DubbingCandidateCqcReport,
    DubbingCandidateContentEvidenceRequest,
    DubbingCurrentProjectionRequest,
    DubbingCandidateContentEvidenceResponse,
    DubbingCandidateReviewCommand,
    DubbingStagedCandidateSplitRequest,
    DubbingSemanticBoundaryAudit,
    DubbingGenerationPlan,
    DubbingGenerationPlanInput,
    DubbingManualTimingDeferralRequest,
    DubbingManualTimingDeferralResponse,
    DubbingProductionSnapshot,
    DubbingProductionExecuteRequest,
    DubbingProductionExecuteResponse,
    DubbingProductionRunSnapshot,
    DubbingProductionGroupFailureRequest,
    DubbingTimelineGroupResetRequest,
)
from app.schemas.video_localization_timeline_edit import (
    VideoLocalizationTimelineEditPatchRequest,
    VideoLocalizationTimelineEditPatchResponse,
)
from app.schemas.video_localization_mutation import (
    VideoLocalizationMutationAckResponse,
    VideoLocalizationUiStatePatchResponse,
)
from app.schemas.waveform import WaveformPeaksResponse
from app.services import waveform_cache
from app.services import project_store
from app.services import video_localization_operations
from app.services import video_localization_export_destinations
from app.services import video_localization_dubbing_executor
from app.services.video_localization_exports import (
    video_localization_exports,
)
from app.domains.video_localization.dubbing_production_service import (
    dubbing_production,
)

router = APIRouter()


class VideoLocalizationWorkspaceRevisionResponse(BaseModel):
    revision: str


def _project_revision(project_id: str) -> str:
    revision = project_store.get_project_repository_revision(project_id)
    return str(revision or "")


def _project_exists(project_id: str) -> bool:
    """Check project existence without deserializing its localization draft."""

    return project_store.get_project_repository_revision(project_id) is not None


def _timeline_mutation_response(
    project_id: str,
    updated: VideoLocalizationDraft,
    *,
    affected_clip_ids: set[str],
    affected_cue_ids: set[str] | None = None,
    affected_subtitle_ids: set[str] | None = None,
) -> VideoLocalizationTimelineMutationResponse:
    if updated._repository_revision is None:
        raise RuntimeError("Timeline mutation response requires a committed snapshot revision")
    clips = [
        dict(clip)
        for clip in updated.timeline_clips
        if str(clip.get("clip_id") or "") in affected_clip_ids
    ]
    subtitle_ids = set(affected_subtitle_ids or set()) | {
        str(value)
        for clip in clips
        for value in [
            clip.get("subtitle_id"),
            *(clip.get("target_subtitle_ids") or []),
        ]
        if value
    }
    cue_ids = set(affected_cue_ids or set()) | {
        str(value)
        for clip in clips
        for value in [clip.get("cue_id"), *(clip.get("source_cue_ids") or [])]
        if value
    }
    lane_states = updated.ui_state.get("dub_lane_states", {})
    discarded_ids = updated.ui_state.get("discarded_tts_task_ids", [])
    return VideoLocalizationTimelineMutationResponse(
        updated_at=updated.updated_at,
        revision=str(updated._repository_revision),
        affected_clip_ids=sorted(affected_clip_ids),
        timeline_clips=clips,
        cues=[cue for cue in updated.cues if cue.cue_id in cue_ids],
        localized_subtitles=[
            subtitle
            for subtitle in updated.localized_subtitles
            if subtitle.subtitle_id in subtitle_ids
        ],
        dub_lane_states=(
            {str(key): dict(value) for key, value in lane_states.items() if isinstance(value, dict)}
            if isinstance(lane_states, dict)
            else {}
        ),
        discarded_tts_task_ids=(
            [str(value) for value in discarded_ids if value]
            if isinstance(discarded_ids, list)
            else []
        ),
    )


class TtsHistoryPlacementRequest(BaseModel):
    request_id: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        description="Stable ID for one user placement action. Reuse on retry; use a new ID for another intentional placement.",
    )


class TtsHistoryTimelineApplyRequest(TtsHistoryPlacementRequest):
    segment_id: str
    clip_id: str | None = None
    new_clip_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    start_ms: int | None = Field(default=None, ge=0)
    dub_lane: int | None = Field(default=None, ge=0)
    force_new: bool = False

    @model_validator(mode="after")
    def validate_clip_identity(self):
        if bool(self.clip_id) == bool(self.new_clip_id):
            raise ValueError("exactly one of clip_id or new_clip_id is required")
        if self.force_new and not self.new_clip_id:
            raise ValueError("force_new requires new_clip_id")
        return self


class TtsLocalPhraseRepairCommitRequest(BaseModel):
    schema_version: Literal["video-localization-local-phrase-repair-v1"] = (
        "video-localization-local-phrase-repair-v1"
    )
    replace_clip_ids: list[str] = Field(min_length=1)
    target_start_ms: int = Field(ge=0)
    target_end_ms: int = Field(gt=0)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    target_text: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.target_end_ms <= self.target_start_ms:
            raise ValueError("target_end_ms must be greater than target_start_ms")
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("source_end_ms must be greater than source_start_ms")
        return self


class TtsHandoffPrepareRequest(BaseModel):
    submission_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{12}$")
    history_result_id: str | None = None
    parameters: dict[str, Any] | None = None
    timeline_clip_id: str | None = None
    target_subtitle_ids: list[str] = Field(min_length=1)
    source_cue_ids: list[str] = Field(default_factory=list)
    voice_library_voice_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "可选的本地音色库 voice_id。仅替换本次 TTS 的音色参考；"
            "目标字幕、来源映射、时间窗、引擎和 Provider 仍由项目 handoff 锁定。"
        ),
    )


class TtsUnusedCleanupRequest(BaseModel):
    segment_id: str | None = None


class TtsUnusedCleanupResponse(BaseModel):
    removed_records: int
    removed_files: int
    kept_used_records: int


class VideoLocalizationDubSubtitleReviewCue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subtitle_id: str = Field(min_length=1)
    source_subtitle_ids: list[str] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "VideoLocalizationDubSubtitleReviewCue":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms 必须大于 start_ms")
        return self


class VideoLocalizationDubSubtitleReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_revision: str = Field(min_length=64, max_length=64)
    cues: list[VideoLocalizationDubSubtitleReviewCue] = Field(min_length=1)


class TtsHistoryDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope: Literal["result_ids", "segment", "project"]
    result_ids: list[str] = Field(default_factory=list, max_length=5_000)
    segment_id: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "TtsHistoryDeleteRequest":
        if self.scope == "result_ids":
            if not any(value.strip() for value in self.result_ids):
                raise ValueError("result_ids 范围必须提供至少一个结果 ID")
            if self.segment_id is not None:
                raise ValueError("result_ids 范围不能同时指定 segment_id")
            return self
        if self.result_ids:
            raise ValueError(f"{self.scope} 范围不能指定 result_ids")
        if self.scope == "segment" and not (self.segment_id or "").strip():
            raise ValueError("segment 范围必须指定 segment_id")
        if self.scope == "project" and self.segment_id is not None:
            raise ValueError("project 范围不能指定 segment_id")
        return self


class TtsHistoryDeleteResponse(BaseModel):
    removed_records: int
    cleanup_failures: int


class SourceCueMergeRequest(BaseModel):
    cue_ids: list[str] = Field(min_length=2)
    survivor_cue_id: str | None = None


AsrDevelopmentTargetStepId = Literal[
    "asr",
    "diarization",
    "initial_analysis_join",
    "understand_document",
    "visual_evidence",
    "research",
    "normalize_entities",
    "section_review_r1",
    "review_decisions_r1",
    "whole_recheck_r1",
    "transcript_quality_gate",
    "alignment",
    "audio_boundaries",
    "boundary_review",
    "subtitle_track",
]


class VideoLocalizationAsrOperationRequest(BaseModel):
    """Typed public request for the complete ASR workflow or a development breakpoint."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: Literal["full", "development_target"] = Field(
        default="full",
        description=(
            "正式执行使用 full；development_target 从一次流程保存的"
            "类型化输入只重跑一个原子任务。"
        ),
    )
    development_source_operation_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "仅 development_target 使用：保存目标节点输入快照的原 ASR 流程任务 ID；允许原任务成功或中途失败。"
        ),
    )
    development_predecessor_operation_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "仅 development_target 续接使用：直接前驱开发任务 ID。"
            "后台从其成功的类型化结果构建当前节点输入；不读取原流程的旧下游输入。"
        ),
    )
    development_target_step_id: AsrDevelopmentTargetStepId | None = Field(
        default=None,
        description=("仅 development_target 使用：只执行这个原子任务，不向后继续，也不写入正式项目字幕。"),
    )
    max_research_rounds: int = Field(
        default=3,
        ge=1,
        le=3,
        description="资料查询最多进行几轮；每轮后由语言模型判断是否继续。",
    )
    max_research_queries: int = Field(
        default=9,
        ge=1,
        le=12,
        description="资料查询整个任务最多执行多少个查询。",
    )
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选语言模型配置；不填则使用默认配置。",
    )
    engine_id: str = Field(default="auto", min_length=1, description="听写引擎；auto 由后台选择可用引擎。")
    diarization_engine_id: str | None = Field(
        default="auto",
        min_length=1,
        description=(
            "默认并行分析声纹；只有检测到多个稳定声音角色时才给字幕添加匿名标签。"
            "传 null 可显式跳过该分析，且说话人模型失败不会阻断 ASR。"
        ),
    )
    source_track_id: Literal["auto", "original", "vocals"] = Field(
        default="auto",
        description=(
            "源语言字幕听写使用哪条音轨；auto 固定使用干净人声轨，"
            "缺失时要求先完成人声分离，不会自动回退原音轨。"
            "只有用户明确选择 original 时才使用原音轨。"
            "合成配音字幕使用独立工作流。"
        ),
    )
    source_language: str = Field(
        default="auto",
        min_length=1,
        description="源语言；auto 自动识别。",
    )
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE,
        description="可选的最少说话人数提示，只用于结果复核，不强制合并声纹。",
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE,
        description="可选的最多说话人数提示，只用于结果复核，不强制合并声纹。",
    )
    segmentation_profile_id: str = Field(
        default="generic_zh",
        min_length=1,
        description="正式流程使用的字幕切分配置。",
    )

    @model_validator(mode="after")
    def validate_execution_boundary(self):
        if self.execution_mode == "development_target":
            if self.development_source_operation_id is None or self.development_target_step_id is None:
                raise ValueError("development_target 必须指定原流程任务和目标节点")
        elif (
            self.development_source_operation_id is not None
            or self.development_predecessor_operation_id is not None
            or self.development_target_step_id is not None
        ):
            raise ValueError("只有 development_target 可以指定原流程任务和目标节点")
        if self.min_speakers is not None and self.max_speakers is not None and self.min_speakers > self.max_speakers:
            raise ValueError("min_speakers 不能大于 max_speakers")
        return self


class VideoLocalizationLocalizationOperationRequest(BaseModel):
    """Request for formal or explicitly enabled development execution."""

    model_config = ConfigDict(extra="forbid")

    source_language: Literal["auto", "en", "zh"] = Field(
        default="auto",
        description="原始讲话语言；auto 使用项目已检测的语言。",
    )
    target_language: Literal["zh-Hans"] = Field(
        default="zh-Hans",
        description="当前本土化目标语言为中国大陆简体中文。",
    )
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选语言模型配置；省略时使用设置页的本土化 AI 策略。",
    )
    localization_requirements_id: str | None = Field(
        default=None,
        min_length=1,
        description=("可选本土化要求配置；省略时使用项目默认配置。未来设置页只需提交这个 ID，不需要复制整份规则。"),
    )
    execution_mode: Literal["full", "development_target"] = Field(
        default="full",
        description=(
            "full 从头运行正式流程；development_target 只用于开发环境，复用有效上游物化结果并在目标节点完成后停止。"
        ),
    )
    development_target_step_id: str | None = Field(
        default=None,
        min_length=1,
        description="开发增量模式本次要执行并停下的原子任务 ID。",
    )
    development_session_id: str | None = Field(
        default=None,
        min_length=1,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="跨多次开发任务复用节点结果的稳定会话 ID。",
    )
    force_development_target: bool = Field(
        default=True,
        description="是否强制重跑目标节点；有效上游节点仍会复用。",
    )
    recover_verified_candidates: bool = Field(
        default=False,
        description="仅开发模式：复用当前项目和会话显式导入且输入指纹一致的完整候选；不恢复未知调用记录。",
    )
    retry_unknown_batch_step_id: str | None = Field(
        default=None, min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_.-]+$",
        description="仅开发模式：用户明确接受可能重复消耗额度后，授权指定执行失败或超时且未保存候选的批次按同一输入新调用一次。保留旧失败，不授权其他批次或后续失败自动重试。",
    )

    @model_validator(mode="after")
    def validate_development_execution(
        self,
    ) -> "VideoLocalizationLocalizationOperationRequest":
        if self.execution_mode == "full":
            if self.recover_verified_candidates:
                raise ValueError("正式全流程不能导入恢复候选。")
            if self.retry_unknown_batch_step_id is not None:
                raise ValueError("正式全流程不能授权未知结果批次重试。")
            if self.development_target_step_id is not None or self.development_session_id is not None:
                raise ValueError("正式全流程不能指定开发目标节点或开发会话。")
            return self
        if self.development_target_step_id is None:
            raise ValueError("开发增量模式必须指定目标节点。")
        if self.development_session_id is None:
            raise ValueError("开发增量模式必须指定稳定的开发会话 ID。")
        return self


class VideoLocalizationDubSubtitleOperationRequest(BaseModel):
    """Typed request for the only synthesized-dub subtitle workflow."""

    model_config = ConfigDict(extra="forbid")

    engine_id: str = Field(
        default="qwen3-asr-mlx",
        min_length=1,
        description="用于听写完整合成配音音频的 ASR 引擎。",
    )
    regeneration_mode: Literal["auto", "full"] = Field(
        default="auto",
        description=(
            "auto 只识别本地音频编辑后的受影响范围；full 明确要求重建全部配音字幕。"
        ),
    )
    execution_mode: Literal["full", "development_target"] = Field(
        default="full",
        description=("full 从头运行正式流程；development_target 只在开发环境执行必要依赖到目标并停止。"),
    )
    development_target_step_id: (
        Literal[
            "prepare_track",
            "transcribe_track",
            "proofread_text",
            "align_words",
            "segment_subtitles",
            "commit",
        ]
        | None
    ) = Field(
        default=None,
        description="开发增量模式本次要执行并停下的原子任务 ID。",
    )
    development_session_id: str | None = Field(
        default=None,
        min_length=1,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="开发目标模式使用的稳定会话 ID。",
    )

    @model_validator(mode="after")
    def validate_development_execution(
        self,
    ) -> "VideoLocalizationDubSubtitleOperationRequest":
        if self.execution_mode == "full":
            if self.development_target_step_id is not None or self.development_session_id is not None:
                raise ValueError("正式全流程不能指定开发目标节点或开发会话。")
            return self
        if self.development_target_step_id is None:
            raise ValueError("开发增量模式必须指定目标节点。")
        if self.development_session_id is None:
            raise ValueError("开发增量模式必须指定稳定的开发会话 ID。")
        return self


class SourceCueSplitRequest(BaseModel):
    replacements: list[VideoLocalizationCue] = Field(min_length=2)


class LocalizedSubtitleSplitRequest(BaseModel):
    children: list[VideoLocalizationSubtitleCue] = Field(min_length=2)
    source_word_ids_by_subtitle_id: dict[str, list[str]] | None = None


class PreviewCacheBuildRequest(BaseModel):
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    full: bool = False


@router.post("/video-localization/sync-projects", response_model=list[Project])
async def sync_video_localization_projects():
    return await asyncio.to_thread(video_localization_service.sync_local_projects)


@router.post(
    "/video-localization/sync-project-summaries",
    response_model=list[ProjectSummary],
    description=(
        "扫描可发现的视频本土化项目包并修复必要索引，再返回历史项目菜单需要的"
        "轻量摘要。已索引但目录暂不可用的项目会保留并标记为 missing；"
        "只有显式删除命令会移除项目。"
    ),
)
async def sync_video_localization_project_summaries():
    return await asyncio.to_thread(video_localization_service.sync_local_project_summaries)


@router.get("/{project_id}/video-localization", response_model=VideoLocalizationDraftResponse)
async def get_video_localization(project_id: str):
    draft = await asyncio.to_thread(video_localization_service.get_video_localization, project_id)
    return require_resource(draft)


@router.post(
    "/{project_id}/video-localization/repair-storage",
    response_model=VideoLocalizationDraftResponse,
    summary="修复视频本土化项目存储",
    description=(
        "显式迁移旧项目目录、重写受管理路径，并在数据库草稿缺失或损坏时从项目快照恢复。普通 GET 不会执行这些写操作。"
    ),
)
async def repair_video_localization_storage(project_id: str):
    repaired = await asyncio.to_thread(
        video_localization_service.repair_video_localization_storage,
        project_id,
    )
    return require_resource(repaired)


@router.get(
    "/{project_id}/video-localization/workspace",
    response_model=VideoLocalizationWorkspaceResponse,
    description=(
        "一次返回带仓库版本的草稿与同一时刻计算的媒体健康状态。revision 与草稿"
        "在同一个数据库快照中读取，客户端不得用更旧的时间线投影覆盖它。"
        "媒体健康仅是只读投影，"
        "不会写回草稿，也不会暴露本地文件路径；公开草稿中的 source/stem locator "
        "固定为空；可用且未被用户禁用或重新编排的系统媒体会确定性投影为"
        "只含 media_source_clip_id 的原音/人声/背景片段，但不会写回 Draft。"
        "任务历史由 operation summaries/detail "
        "和 TTS tasks 专用读取接口提供，因此本响应中的 operations、tts_tasks 固定为空。"
    ),
)
async def get_video_localization_workspace(project_id: str):
    workspace = await asyncio.to_thread(
        video_localization_service.get_video_localization_workspace,
        project_id,
    )
    return require_resource(workspace)


@router.get(
    "/{project_id}/video-localization/workspace-details/{section}",
    response_model=VideoLocalizationWorkspaceDetailResponse,
    description=(
        "按需读取一个服务端详情区。主工作区不携带 ASR 逐词证据、参考音频候选、"
        "生成候选或完整配音计划；只有对应检查器或操作需要时才读取。"
    ),
)
async def get_video_localization_workspace_detail(
    project_id: str,
    section: Literal[
        "transcription",
        "reference_clips",
        "generated_candidates",
        "dubbing_production",
    ],
):
    detail = await asyncio.to_thread(
        video_localization_service.get_video_localization_workspace_detail,
        project_id,
        section,
    )
    return require_resource(detail)


@router.put(
    "/{project_id}/video-localization/workspace",
    response_model=VideoLocalizationEditableDraftResponse,
    description=(
        "保存工作区拥有的可编辑字段。主工作区省略的服务端详情区始终从当前项目保留，"
        "不会因为浏览器未加载而被保存为空。字幕、朗读稿及其配音计划由各自专用"
        "命令维护，工作区快照不会覆盖这些较新的结果。"
    ),
)
async def put_video_localization_workspace(
    project_id: str,
    draft: VideoLocalizationDraft,
):
    updated = await asyncio.to_thread(
        video_localization_service.replace_video_localization_workspace_from_client,
        project_id,
        draft,
    )
    return require_resource(updated)


@router.get(
    "/{project_id}/video-localization/workspace-revision",
    response_model=VideoLocalizationWorkspaceRevisionResponse,
    description=(
        "只返回项目内容版本。页面可先检查这个小响应，版本变化时再读取完整工作区。"
    ),
)
async def get_video_localization_workspace_revision(project_id: str):
    revision = await asyncio.to_thread(
        project_store.get_project_repository_revision,
        project_id,
    )
    if revision is None:
        raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
    return VideoLocalizationWorkspaceRevisionResponse(revision=revision)


@router.get(
    "/{project_id}/video-localization/timeline-projection",
    response_model=VideoLocalizationTimelineProjectionResponse,
    description=(
        "只返回当前时间线片段和对应项目版本。后台配音完成、排队状态变化或另一页面写入片段时，"
        "工作台用这个轻量接口更新轨道，不重新读取 ASR、参考音频和历史任务。"
    ),
)
async def get_video_localization_timeline_projection(project_id: str):
    projection = await asyncio.to_thread(
        video_localization_service.get_video_localization_timeline_projection,
        project_id,
    )
    return require_resource(projection)


@router.post("/{project_id}/video-localization/auto-name", response_model=Project)
async def auto_name_video_localization_project(project_id: str):
    project = await asyncio.to_thread(video_localization_service.auto_name_video_localization_project, project_id)
    return require_resource(project, code="PROJECT_NOT_FOUND", message="Project not found")


@router.put("/{project_id}/video-localization", response_model=VideoLocalizationEditableDraftResponse)
async def put_video_localization(project_id: str, draft: VideoLocalizationDraft):
    updated = await asyncio.to_thread(
        video_localization_service.replace_video_localization_from_client, project_id, draft
    )
    return require_resource(updated)


@router.patch(
    "/{project_id}/video-localization/ui-state",
    response_model=VideoLocalizationUiStatePatchResponse,
)
async def patch_video_localization_ui_state(project_id: str, patch: dict = Body(default_factory=dict)):
    public_patch = await asyncio.to_thread(state_ownership.client_ui_state_patch, patch)
    updated = await asyncio.to_thread(video_localization_service.update_video_localization_ui_state, project_id, patch)
    updated = require_resource(updated)
    if updated._repository_revision is None:
        raise RuntimeError(
            "UI state response requires a committed snapshot revision"
        )
    return VideoLocalizationUiStatePatchResponse(
        updated_at=updated.updated_at,
        revision=str(updated._repository_revision),
        ui_state_patch={
            str(key): updated.ui_state.get(str(key))
            for key in public_patch
            if str(key) in updated.ui_state
        },
    )


@router.patch(
    "/{project_id}/video-localization/timeline-edit",
    response_model=VideoLocalizationTimelineEditPatchResponse,
    summary="Patch Video Localization Timeline Edit",
)
async def patch_video_localization_timeline_edit(
    project_id: str,
    patch: VideoLocalizationTimelineEditPatchRequest,
):
    updated = await asyncio.to_thread(
        video_localization_service.update_video_localization_timeline_edit,
        project_id,
        patch,
    )
    updated = require_resource(updated)
    if updated._repository_revision is None:
        raise RuntimeError(
            "Timeline edit response requires a committed snapshot revision"
        )
    return await asyncio.to_thread(
        public_timeline_edit_receipt,
        updated,
        patch,
        str(updated._repository_revision),
    )


@router.delete("/{project_id}/video-localization", response_model=VideoLocalizationEditableDraftResponse)
def reset_video_localization(project_id: str):
    updated = video_localization_service.reset_video_localization(project_id)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/open-directory")
def open_video_localization_project_directory(project_id: str):
    opened = video_localization_service.open_project_directory(project_id)
    return require_resource(opened, code="VIDEO_LOCALIZATION_PROJECT_NOT_FOUND", message="Project not found")


@router.post(
    "/{project_id}/video-localization/source-media",
    response_model=VideoLocalizationMutationAckResponse,
)
async def import_video_localization_source_media(
    project_id: str,
    file: UploadFile = File(...),
):
    updated = await video_localization_service.import_source_media(project_id, file)
    updated = require_resource(updated)
    return VideoLocalizationMutationAckResponse(
        updated_at=updated.updated_at,
        revision=await asyncio.to_thread(_project_revision, project_id),
    )


@router.get("/{project_id}/video-localization/source-media/video")
async def get_video_localization_source_video(project_id: str):
    video_path = await asyncio.to_thread(video_localization_service.source_video_file, project_id)
    return audio_file_response(
        video_path, code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND", message="Source video file not found"
    )


@router.get("/{project_id}/video-localization/source-media/preview-video")
async def get_video_localization_source_preview_video(
    project_id: str,
    variant: Literal["auto", "source", "preview"] = Query(default="auto"),
):
    video_path = await asyncio.to_thread(
        video_localization_service.source_preview_video_file,
        project_id,
        variant=variant,
    )
    return media_file_response(
        video_path, code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND", message="Source video file not found"
    )


@router.post(
    "/{project_id}/video-localization/source-media/preview-video",
    response_model=preview_media_contracts.VideoPlaybackProxyStatus,
)
async def prepare_video_localization_source_preview_video(
    project_id: str,
    request: preview_media_contracts.VideoPlaybackProxyRequest | None = None,
):
    status = await asyncio.to_thread(
        video_localization_service.request_source_playback_proxy,
        project_id,
        **(request or preview_media_contracts.VideoPlaybackProxyRequest()).model_dump(),
    )
    return require_resource(
        status,
        code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
        message="Source video file not found",
    )


@router.get("/{project_id}/video-localization/source-media/preview-video/segments/{segment_index}")
async def get_video_localization_source_preview_video_segment(
    project_id: str,
    segment_index: int,
    revision: str = Query(min_length=1),
):
    segment_path = await asyncio.to_thread(
        video_localization_service.source_playback_proxy_segment_file,
        project_id,
        segment_index,
        revision,
    )
    return media_file_response(
        segment_path,
        code="VIDEO_LOCALIZATION_PLAYBACK_SEGMENT_NOT_READY",
        message="Playback segment is not ready",
        cache_control="public, max-age=31536000, immutable",
    )


@router.get(
    "/{project_id}/video-localization/source-media/preview-cache",
    response_model=preview_cache_contracts.PreviewCacheStatus,
)
def get_video_localization_preview_cache(project_id: str):
    return require_resource(
        video_localization_service.source_preview_cache_status(project_id),
        code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
        message="Source video file not found",
    )


@router.post(
    "/{project_id}/video-localization/source-media/preview-cache",
    response_model=preview_cache_contracts.PreviewCacheStatus,
)
def build_video_localization_preview_cache(project_id: str, request: PreviewCacheBuildRequest):
    return require_resource(
        video_localization_service.request_source_preview_cache(
            project_id,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            full=request.full,
        ),
        code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
        message="Source video file not found",
    )


@router.post(
    "/{project_id}/video-localization/source-media/preview-cache/refresh",
    response_model=preview_cache_contracts.PreviewCacheStatus,
)
def refresh_video_localization_preview_cache(project_id: str):
    return require_resource(
        video_localization_service.refresh_source_preview_cache(project_id),
        code="VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
        message="Source video file not found",
    )


@router.get("/{project_id}/video-localization/source-media/preview-cache/sprite/{chunk_index}")
def get_video_localization_preview_cache_sprite(project_id: str, chunk_index: int):
    sprite_path = video_localization_service.source_preview_cached_sprite(project_id, chunk_index)
    response = media_file_response(
        sprite_path,
        code="VIDEO_LOCALIZATION_PREVIEW_SPRITE_NOT_READY",
        message="Preview sprite is not cached yet",
    )
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@router.get("/{project_id}/video-localization/source-media/audio")
async def get_video_localization_source_audio(
    project_id: str,
    variant: Literal["auto", "source", "preview"] = Query(default="auto"),
):
    audio_path = await asyncio.to_thread(
        video_localization_service.source_preview_audio_file,
        project_id,
        variant=variant,
    )
    return audio_file_response(
        audio_path, code="VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND", message="Source audio file not found"
    )


@router.post("/{project_id}/video-localization/source-media/audio-preview")
async def prepare_video_localization_source_preview_audio(project_id: str):
    previous_path = await asyncio.to_thread(
        video_localization_service.source_preview_audio_file,
        project_id,
    )
    audio_path = await asyncio.to_thread(video_localization_service.ensure_source_preview_audio, project_id)
    source_path = await asyncio.to_thread(video_localization_service.source_audio_file, project_id)
    return require_resource(
        {
            "status": "ready",
            "profile": "audio-aac-128k-v1",
            "changed": previous_path != audio_path,
            "variant": "preview" if audio_path != source_path else "source",
        },
        code="VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
        message="Source audio file not found",
    )


@router.get("/{project_id}/video-localization/operations", response_model=list[VideoLocalizationOperation])
def list_video_localization_operations(project_id: str):
    operations = video_localization_operations.list_operations(project_id)
    return require_resource(operations)


@router.get(
    "/{project_id}/video-localization/workflows/asr",
    response_model=workflow_contracts.WorkflowDefinition,
    summary="查看听写工作流结构",
    description="返回父级阶段、原子子任务、执行关系和输出契约，供 WebUI、自动化工具和其他 Agent 共用。",
)
def get_video_localization_asr_workflow(project_id: str):
    return require_resource(
        workflow_contracts.ASR_WORKFLOW_DEFINITION
        if _project_exists(project_id)
        else None
    )


@router.get(
    "/{project_id}/video-localization/workflows/localization",
    response_model=workflow_contracts.WorkflowDefinition,
    summary="查看本土化工作流结构",
    description="返回当前本土化工作流的阶段、原子子任务、执行关系和输出契约。",
)
def get_video_localization_localization_workflow(project_id: str):
    return require_resource(
        workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION
        if _project_exists(project_id)
        else None
    )


@router.get(
    "/{project_id}/video-localization/workflows/dub-subtitles",
    response_model=workflow_contracts.WorkflowDefinition,
    summary="查看合成配音字幕工作流结构",
    description=("返回当前唯一合成配音字幕工作流的六个原子子任务、依赖关系和版本化输出契约。"),
)
def get_video_localization_dub_subtitle_workflow(project_id: str):
    return require_resource(
        workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION
        if _project_exists(project_id)
        else None
    )


@router.get(
    "/{project_id}/video-localization/localization-requirements",
    response_model=(localization_requirements.LocalizationRequirementsCatalog),
    summary="查看可选的本土化要求",
    description="返回项目默认配置和可选的本土化要求配置。",
)
def get_video_localization_requirements(project_id: str):
    return require_resource(
        localization_requirements.load_localization_requirements_catalog()
        if _project_exists(project_id)
        else None
    )


@router.get(
    "/{project_id}/video-localization/operations/feed-v2",
    response_model=VideoLocalizationOperationFeedV2,
    summary="Read Bounded Video Localization Operation Feed",
    description=(
        "返回全部活动任务和一页终态历史。历史使用 opaque keyset cursor；"
        "游标对应的终态历史发生变化时返回明确冲突，客户端应重新读取第一页。"
        "无 cursor 的轮询可携带 after_revision，版本未变时不重复返回任务。"
    ),
)
def read_video_localization_operation_feed_v2(
    project_id: str,
    after_revision: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(
        default=None,
        min_length=1,
        max_length=1024,
    ),
    history_limit: int = Query(default=50, ge=1, le=100),
):
    feed = require_resource(
        video_localization_operations.read_operation_feed_v2(
            project_id,
            after_revision=after_revision,
            cursor=cursor,
            history_limit=history_limit,
        )
    )
    public_feed = VideoLocalizationOperationFeedV2.model_validate(
        feed,
        from_attributes=True,
    )
    return Response(
        content=public_feed.model_dump_json(),
        media_type="application/json",
    )


@router.post("/{project_id}/video-localization/operations", response_model=VideoLocalizationOperation)
def submit_video_localization_operation(project_id: str, request: VideoLocalizationOperationRequest):
    operation = video_localization_operations.submit_operation(
        project_id,
        request.kind,
        request.parameters,
    )
    return require_resource(operation)


@router.post(
    "/{project_id}/video-localization/operations/english-asr",
    response_model=VideoLocalizationOperation,
    summary="启动听写工作流",
    description=(
        "使用与 WebUI 相同的 ASR 领域能力。正式模式会继续完成全文校对、"
        "时间对齐和字幕轨；开发模式可只运行原始听写、初始语音分析，"
        "也可从指定上游结果单独执行全文理解或资料查询。"
    ),
)
def submit_video_localization_asr_operation(
    project_id: str,
    request: VideoLocalizationAsrOperationRequest,
):
    parameters = request.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=(request.execution_mode == "development_target"),
    )
    operation = video_localization_operations.submit_operation(
        project_id,
        "english_asr",
        parameters,
    )
    return require_resource(operation)


@router.post(
    "/{project_id}/video-localization/operations/localization",
    response_model=VideoLocalizationOperation,
    summary="启动本土化工作流",
    description=(
        "使用与 WebUI 相同的文档优先本土化能力：先理解全文和人物，"
        "识别需要保留的非语言表演，再按稳定连续范围生成全文本土化初稿，并独立"
        "复核原意与中文自然度，再用本地跨语言"
        "语义模型映射英文逐词时间，最后生成中文台词轨和上屏字幕轨。"
        "内容质检多次尝试后仍有问题时会记录到配音分组并继续；"
        "数据缺失、来源重复或非法时间等完整性错误仍会阻断受影响分支。"
        "开发增量模式会在产品数据目录之外保存并复用节点结果，哪里变化"
        "就从哪里向后失效；正式完整任务始终从头计算，不读取开发快照。"
    ),
)
def submit_video_localization_localization_operation(
    project_id: str,
    request: VideoLocalizationLocalizationOperationRequest,
):
    operation = video_localization_operations.submit_operation(
        project_id,
        "localization_draft",
        request.model_dump(mode="json", exclude_none=True),
    )
    return require_resource(operation)


@router.post(
    "/{project_id}/video-localization/operations/dub-subtitles",
    response_model=VideoLocalizationOperation,
    summary="启动合成配音字幕工作流",
    description=(
        "使用与 WebUI 相同的完整配音字幕能力。正式模式从当前可听配音"
        "生成完整音频并依次完成听写、文字校对、时间确认和保存；"
        "开发增量模式执行必要依赖到指定原子任务并停止。"
    ),
)
def submit_video_localization_dub_subtitle_operation(
    project_id: str,
    request: VideoLocalizationDubSubtitleOperationRequest,
):
    operation = video_localization_operations.submit_operation(
        project_id,
        "dub_subtitle_generation",
        request.model_dump(mode="json", exclude_none=True),
    )
    return require_resource(operation)


@router.post(
    "/{project_id}/video-localization/dub-subtitles/review",
    response_model=VideoLocalizationEditableDraftResponse,
    summary="提交合成配音字幕复审结果",
    description=(
        "在当前生成字幕版本上提交完整复审结果。允许调整文字和时间，"
        "也允许把连续、同说话人的字幕合并；服务端保留配音来源证据，"
        "并拒绝过期、缺失、乱序或同轨重叠的结果。"
    ),
)
def review_video_localization_dub_subtitles(
    project_id: str,
    request: VideoLocalizationDubSubtitleReviewRequest,
):
    updated = video_localization_service.review_dub_subtitles(
        project_id,
        source_revision=request.source_revision,
        cues=[item.model_dump(mode="json") for item in request.cues],
    )
    return require_resource(updated)


@router.get("/{project_id}/video-localization/operations/{operation_id}", response_model=VideoLocalizationOperation)
def get_video_localization_operation(project_id: str, operation_id: str):
    operation = video_localization_operations.get_operation_detail(
        project_id,
        operation_id,
    )
    return require_resource(operation, code="VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", message="Operation not found")


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-asr-result",
    response_model=PublicTranscribeRawOutput,
)
async def get_video_localization_development_asr_result(project_id: str, operation_id: str):
    result = await asyncio.to_thread(
        video_localization_operations.get_development_asr_result,
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_ASR_RESULT_NOT_FOUND",
        message="Development ASR result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-initial-analysis-result",
    response_model=PublicAsrInitialAnalysisSnapshot,
)
async def get_video_localization_development_initial_analysis_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        video_localization_operations.get_development_initial_analysis_result,
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_INITIAL_ANALYSIS_RESULT_NOT_FOUND",
        message="Development ASR initial analysis result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-document-understanding-result",
    response_model=document_understanding_contracts.AsrDocumentUnderstandingResult,
    summary="查看全文理解开发结果",
    description=("返回独立的全文理解与复查规划结果。该步骤不会联网查询、不会核实名称，也不会修改或写入字幕。"),
)
async def get_video_localization_development_document_understanding_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        (video_localization_operations.get_development_document_understanding_result),
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code=("VIDEO_LOCALIZATION_DEVELOPMENT_DOCUMENT_UNDERSTANDING_NOT_FOUND"),
        message="Development document understanding result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-visual-evidence-result",
    response_model=visual_evidence.AsrVisualEvidenceResult,
    summary="查看画面取证开发结果",
    description=(
        "返回定点截图、画面可见文字、搜索线索和限制。该步骤不会根据长相识别人，不会决定规范名称，也不会修改听写或字幕。"
    ),
)
async def get_video_localization_development_visual_evidence_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        video_localization_operations.get_development_visual_evidence_result,
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_VISUAL_EVIDENCE_NOT_FOUND",
        message="Development visual evidence result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-visual-evidence-frames/{frame_id}",
    summary="查看画面取证截图",
    description="只返回当前项目、当前任务清单中登记且指纹一致的截图。",
)
async def get_video_localization_development_visual_evidence_frame(
    project_id: str,
    operation_id: str,
    frame_id: str,
    preview: bool = Query(
        default=False,
        description="设为 true 时返回列表用的小尺寸预览；点击查看仍使用原图。",
    ),
):
    frame_path = await asyncio.to_thread(
        video_localization_operations.get_development_visual_evidence_frame,
        project_id,
        operation_id,
        frame_id,
    )
    if preview and frame_path is not None:
        frame_path = await asyncio.to_thread(
            media_assets.visual_evidence_thumbnail,
            frame_path,
        )
    return media_file_response(
        frame_path,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_VISUAL_FRAME_NOT_FOUND",
        message="Development visual evidence frame not found",
        cache_control="private, max-age=31536000, immutable",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/visual-evidence-frames/{frame_id}",
    summary="查看正式任务的画面取证截图",
    description=("只返回当前项目、当前正式 ASR 或本土化任务隔离目录中登记且文件指纹一致的截图。"),
)
async def get_video_localization_visual_evidence_frame(
    project_id: str,
    operation_id: str,
    frame_id: str,
    preview: bool = Query(
        default=False,
        description="设为 true 时返回列表用的小尺寸预览；点击查看仍使用原图。",
    ),
):
    frame_path = await asyncio.to_thread(
        video_localization_operations.get_formal_visual_evidence_frame,
        project_id,
        operation_id,
        frame_id,
    )
    if preview and frame_path is not None:
        frame_path = await asyncio.to_thread(
            media_assets.visual_evidence_thumbnail,
            frame_path,
        )
    return media_file_response(
        frame_path,
        code="VIDEO_LOCALIZATION_VISUAL_FRAME_NOT_FOUND",
        message="Visual evidence frame not found",
        cache_control="private, max-age=31536000, immutable",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-research-evidence-result",
    response_model=research_evidence.AsrResearchEvidenceResult,
    summary="查看资料查询开发结果",
    description=(
        "返回独立的资料查询结果，包括每轮查询、来源、模型是否建议继续、"
        "停止原因和未解决项。该步骤不会决定规范名称，也不会修改字幕。"
    ),
)
async def get_video_localization_development_research_evidence_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        (video_localization_operations.get_development_research_evidence_result),
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_RESEARCH_EVIDENCE_NOT_FOUND",
        message="Development research evidence result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-entity-normalization-result",
    response_model=entity_normalization.AsrEntityNormalizationResult,
    summary="查看名称与术语统一开发结果",
    description=("返回证据支持的规范名称、全文文字修改和建议复核项。该开发结果不会写入正式项目字幕。"),
)
async def get_video_localization_development_entity_normalization_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        (video_localization_operations.get_development_entity_normalization_result),
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_ENTITY_NORMALIZATION_NOT_FOUND",
        message="Development entity normalization result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-section-review-result",
    response_model=section_review.AsrSectionReviewResult,
    summary="查看第 1 轮分段复查开发结果",
    description=("返回逐段发现的可能听写问题、失败区块和模型用量。该步骤只列疑点，不采纳修改，也不改变当前字幕。"),
)
async def get_video_localization_development_section_review_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        video_localization_operations.get_development_section_review_result,
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_SECTION_REVIEW_NOT_FOUND",
        message="Development section review result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-review-decisions-result",
    response_model=review_decisions.AsrReviewDecisionsResult,
    summary="查看第 1 轮修改汇总开发结果",
    description=(
        "返回每条疑点的采纳、拒绝或待确认结论，以及应用后的完整字幕快照。"
        "只允许处理上一步明确提出的问题，并保持片段 ID 和时间码不变。"
    ),
)
async def get_video_localization_development_review_decisions_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        (video_localization_operations.get_development_review_decisions_result),
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_REVIEW_DECISIONS_NOT_FOUND",
        message="Development review decisions result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-whole-recheck-result",
    response_model=whole_recheck.AsrWholeRecheckResult,
    summary="查看第 1 轮全文复核开发结果",
    description=(
        "返回修改后的整份转写是否已完成本地收尾，以及建议复听的问题。"
        "该步骤只读，不修改字幕文字或时间码，也不会启动下一轮模型复查。"
    ),
)
async def get_video_localization_development_whole_recheck_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        video_localization_operations.get_development_whole_recheck_result,
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_WHOLE_RECHECK_NOT_FOUND",
        message="Development whole recheck result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-transcript-quality-gate-result",
    response_model=transcript_quality_gate.AsrTranscriptQualityGateResult,
    summary="查看进入校时前检查开发结果",
    description=(
        "返回文字是否已经稳定到可以开始逐词校时、具体阻断原因和"
        "建议复听片段。文字疑点不阻断正式流程；只有技术或结构错误"
        "才会阻断。该步骤只做本地规则判断，不调用语言模型，"
        "也不修改字幕文字、片段 ID 或时间码。"
    ),
)
async def get_video_localization_development_transcript_quality_gate_result(
    project_id: str,
    operation_id: str,
):
    result = await asyncio.to_thread(
        (video_localization_operations.get_development_transcript_quality_gate_result),
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_TRANSCRIPT_QUALITY_GATE_NOT_FOUND",
        message="Development transcript quality gate result not found",
    )


@router.get(
    "/{project_id}/video-localization/operations/{operation_id}/development-diarization-result",
    response_model=PublicDiarizeSpeakersOutput,
)
async def get_video_localization_development_diarization_result(project_id: str, operation_id: str):
    result = await asyncio.to_thread(
        video_localization_operations.get_development_diarization_result,
        project_id,
        operation_id,
    )
    return require_resource(
        result,
        code="VIDEO_LOCALIZATION_DEVELOPMENT_DIARIZATION_RESULT_NOT_FOUND",
        message="Development speaker diarization result not found",
    )


@router.post(
    "/{project_id}/video-localization/operations/{operation_id}/cancel", response_model=VideoLocalizationOperation
)
def cancel_video_localization_operation(project_id: str, operation_id: str):
    operation = video_localization_operations.cancel_operation(
        project_id,
        operation_id,
    )
    return require_resource(operation)


@router.post(
    "/{project_id}/video-localization/operations/{operation_id}/retry", response_model=VideoLocalizationOperation
)
def retry_video_localization_operation(project_id: str, operation_id: str):
    operation = video_localization_operations.retry_operation(
        project_id,
        operation_id,
    )
    return require_resource(operation)


@router.get("/{project_id}/video-localization/stems/{kind}/audio")
async def get_video_localization_stem_audio(
    project_id: str,
    kind: str,
    variant: Literal["auto", "source", "preview"] = Query(default="auto"),
):
    audio_path = await asyncio.to_thread(
        video_localization_service.stem_preview_audio_file,
        project_id,
        kind,
        variant=variant,
    )
    return audio_file_response(
        audio_path, code="VIDEO_LOCALIZATION_STEM_AUDIO_NOT_FOUND", message="Stem audio file not found"
    )


@router.post("/{project_id}/video-localization/stems/{kind}/audio-preview")
async def prepare_video_localization_stem_preview_audio(project_id: str, kind: str):
    previous_path = await asyncio.to_thread(
        video_localization_service.stem_preview_audio_file,
        project_id,
        kind,
    )
    audio_path = await asyncio.to_thread(video_localization_service.ensure_stem_preview_audio, project_id, kind)
    source_path = await asyncio.to_thread(video_localization_service.stem_audio_file, project_id, kind)
    return require_resource(
        {
            "status": "ready",
            "profile": "audio-aac-128k-v1",
            "changed": previous_path != audio_path,
            "variant": "preview" if audio_path != source_path else "source",
        },
        code="VIDEO_LOCALIZATION_STEM_AUDIO_NOT_FOUND",
        message="Stem audio file not found",
    )


@router.patch("/{project_id}/video-localization/cues/{cue_id}", response_model=VideoLocalizationEditableDraftResponse)
def update_video_localization_cue(project_id: str, cue_id: str, patch: VideoLocalizationCueUpdate):
    updated = video_localization_service.update_cue(project_id, cue_id, patch)
    return require_resource(updated)


@router.delete("/{project_id}/video-localization/cues/{cue_id}", response_model=VideoLocalizationEditableDraftResponse)
async def delete_video_localization_source_cue(project_id: str, cue_id: str):
    updated = await asyncio.to_thread(video_localization_service.delete_source_cue, project_id, cue_id)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/cues/merge", response_model=VideoLocalizationEditableDraftResponse)
async def merge_video_localization_source_cues(project_id: str, request: SourceCueMergeRequest):
    updated = await asyncio.to_thread(
        video_localization_service.merge_source_cues,
        project_id,
        request.cue_ids,
        survivor_cue_id=request.survivor_cue_id,
    )
    return require_resource(updated)


@router.post("/{project_id}/video-localization/cues/{cue_id}/split", response_model=VideoLocalizationEditableDraftResponse)
async def split_video_localization_source_cue(
    project_id: str,
    cue_id: str,
    request: SourceCueSplitRequest,
):
    updated = await asyncio.to_thread(
        video_localization_service.split_source_cue,
        project_id,
        cue_id,
        request.replacements,
    )
    return require_resource(updated)


@router.post(
    "/{project_id}/video-localization/cues/{cue_id}/timing-confirmation",
    response_model=VideoLocalizationEditableDraftResponse,
)
def confirm_video_localization_cue_timing(
    project_id: str,
    cue_id: str,
    request: VideoLocalizationCueTimingConfirmationRequest,
):
    updated = video_localization_service.update_cue(
        project_id,
        cue_id,
        VideoLocalizationCueUpdate(
            confirm_timing=True,
            expected_start_ms=request.start_ms,
            expected_end_ms=request.end_ms,
            timing_confirmation_method=request.confirmation_method,
            timing_confirmation_evidence=request.automatic_evidence,
        ),
    )
    return require_resource(updated)


@router.post(
    "/{project_id}/video-localization/localized-bindings/repair",
    response_model=VideoLocalizationEditableDraftResponse,
    summary="Repair Localized Source Bindings",
    description=(
        "在项目版本、听写版本及当前分离人声指纹保护下，重新分配已核对的连续来源词。"
        "不修改中文正文或字幕 ID；时间和关联字幕由当前词级来源派生并一次保存。"
        "拒绝丢词、重复、乱序和已有配音依赖；重复提交已达到的目标不重复写入。"
    ),
)
def apply_video_localization_binding_repair(project_id: str, request: BindingRepairRequest):
    return require_resource(video_localization_service.apply_localized_binding_repair(project_id, request))


@router.post(
    "/{project_id}/video-localization/transcription/source-repairs",
    response_model=VideoLocalizationEditableDraftResponse,
    description=(
        "按独立音频证据原子剔除完整的误识别片段，同步英文词与字幕来源绑定，保留中文正文。"
        "需要项目版本、听写版本和当前分离人声指纹；同一 request_id 支持安全重试。"
        "不支持任意改写、重定时或删除已有中文/配音使用的来源词。"
    ),
)
def apply_video_localization_asr_source_repair(project_id: str, request: AsrSourceRepairRequest):
    return require_resource(video_localization_service.apply_asr_source_repair(project_id, request))


@router.post(
    "/{project_id}/video-localization/cues/asr-vad-timing-corrections",
    response_model=VideoLocalizationEditableDraftResponse,
    description=(
        "原子写入同一次分离人声 ASR/VAD 逐词校正、字幕范围与可追溯证据。"
        "文本、说话人身份和音频文件不会由该入口修改。"
    ),
)
def apply_video_localization_asr_vad_timing_correction(
    project_id: str,
    request: VideoLocalizationAsrVadTimingCorrectionRequest,
):
    updated = video_localization_service.apply_asr_vad_source_timing_correction(
        project_id, request
    )
    return require_resource(updated)


@router.patch(
    "/{project_id}/video-localization/localized-subtitles/{subtitle_id}", response_model=VideoLocalizationEditableDraftResponse
)
def update_video_localization_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
):
    updated = video_localization_service.update_localized_subtitle(project_id, subtitle_id, patch)
    return require_resource(updated)


@router.patch(
    "/{project_id}/video-localization/localized-subtitles/{subtitle_id}/edit",
    response_model=VideoLocalizationTimelineMutationResponse,
    description=(
        "提交一次交互式本土化字幕编辑，只返回受影响字幕、关联 cue/片段与仓库版本。"
        "数据库提交与恢复快照请求在同一事务中完成；大型恢复快照在响应后合并写入。"
    ),
)
def edit_video_localization_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    patch: VideoLocalizationSubtitleCueUpdate,
    background_tasks: BackgroundTasks,
):
    updated, affected_clip_ids = (
        video_localization_service.update_localized_subtitle_interactive(
            project_id,
            subtitle_id,
            patch,
        )
    )
    saved = require_resource(updated)
    saved_subtitle = next(
        (
            item
            for item in saved.localized_subtitles
            if item.subtitle_id == subtitle_id
        ),
        None,
    )
    affected_cue_ids = {
        str(value)
        for value in [
            saved_subtitle.linked_cue_id if saved_subtitle else None,
            *(saved_subtitle.source_cue_ids if saved_subtitle else []),
        ]
        if value
    }
    response = _timeline_mutation_response(
        project_id,
        saved,
        affected_clip_ids=affected_clip_ids,
        affected_cue_ids=affected_cue_ids,
        affected_subtitle_ids={subtitle_id},
    )
    background_tasks.add_task(project_snapshot_projection.flush, project_id)
    return response


@router.patch(
    "/{project_id}/video-localization/localized-spoken-segments/{segment_id}",
    response_model=VideoLocalizationEditableDraftResponse,
)
def update_video_localization_localized_spoken_segment(
    project_id: str,
    segment_id: str,
    patch: VideoLocalizationSpokenSegmentUpdate,
):
    updated = video_localization_service.update_localized_spoken_segment(
        project_id,
        segment_id,
        patch,
    )
    return require_resource(updated)


@router.delete(
    "/{project_id}/video-localization/localized-subtitles/{subtitle_id}",
    response_model=VideoLocalizationEditableDraftResponse,
)
async def delete_video_localization_localized_subtitle(project_id: str, subtitle_id: str):
    updated = await asyncio.to_thread(
        video_localization_service.delete_localized_subtitle,
        project_id,
        subtitle_id,
    )
    return require_resource(updated)


@router.post(
    "/{project_id}/video-localization/localized-subtitles/{subtitle_id}/split",
    response_model=VideoLocalizationEditableDraftResponse,
)
async def split_video_localization_localized_subtitle(
    project_id: str,
    subtitle_id: str,
    request: LocalizedSubtitleSplitRequest,
):
    updated = await asyncio.to_thread(
        video_localization_service.split_localized_subtitle,
        project_id,
        subtitle_id,
        request.children,
        source_word_ids_by_subtitle_id=request.source_word_ids_by_subtitle_id,
    )
    return require_resource(updated)


@router.post("/{project_id}/video-localization/speakers", response_model=VideoLocalizationEditableDraftResponse)
def create_video_localization_speaker(project_id: str, payload: VideoLocalizationSpeakerCreate):
    updated = video_localization_service.create_speaker(project_id, payload)
    return require_resource(updated)


@router.patch("/{project_id}/video-localization/speakers/{speaker_id}", response_model=VideoLocalizationEditableDraftResponse)
def update_video_localization_speaker(project_id: str, speaker_id: str, payload: VideoLocalizationSpeakerUpdate):
    updated = video_localization_service.update_speaker(project_id, speaker_id, payload)
    return require_resource(updated)


@router.post(
    "/{project_id}/video-localization/dubbing/plan",
    response_model=DubbingGenerationPlan,
)
async def create_video_localization_dubbing_plan(
    project_id: str,
    body: DubbingGenerationPlanInput,
):
    return await asyncio.to_thread(
        dubbing_production.create_generation_plan,
        project_id,
        body,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/plan/refresh-timing",
    response_model=DubbingGenerationPlan,
    summary="Refresh Dubbing Plan Timing",
)
async def refresh_video_localization_dubbing_plan_timing(project_id: str):
    return await asyncio.to_thread(
        dubbing_production.refresh_generation_plan_timing,
        project_id,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/timeline/reset-groups",
    response_model=VideoLocalizationEditableDraftResponse,
    summary="Reset Dubbing Timeline Groups",
)
async def reset_video_localization_dubbing_timeline_groups(
    project_id: str,
    body: DubbingTimelineGroupResetRequest,
):
    return await asyncio.to_thread(
        dubbing_production.clear_group_timeline_placements,
        project_id,
        body,
    )


@router.get(
    "/{project_id}/video-localization/dubbing/snapshot",
    response_model=DubbingProductionSnapshot,
)
async def get_video_localization_dubbing_snapshot(project_id: str):
    return await asyncio.to_thread(
        dubbing_production.read_snapshot,
        project_id,
    )


@router.get(
    "/{project_id}/video-localization/dubbing/completion",
    response_model=DubbingCompletionSnapshot,
    summary="Read physical dubbing completion without generating or editing media",
)
async def get_video_localization_dubbing_completion(
    project_id: str, start_ms: int = Query(default=0, ge=0), end_ms: int | None = Query(default=None, gt=0),
):
    return await asyncio.to_thread(dubbing_production.read_completion, project_id,
                                   start_ms=start_ms, end_ms=end_ms)


@router.get(
    "/{project_id}/video-localization/dubbing/production-run",
    response_model=DubbingProductionRunSnapshot,
    summary="读取可恢复的逐组配音生产进度",
)
async def get_video_localization_dubbing_production_run(project_id: str):
    return await asyncio.to_thread(
        dubbing_production.read_production_run,
        project_id,
    )


@router.get(
    "/{project_id}/video-localization/dubbing/candidates/{candidate_id}/semantic-boundaries",
    response_model=DubbingSemanticBoundaryAudit,
    summary="读取候选最终投影的逐边界断句证据",
)
async def get_video_localization_candidate_semantic_boundaries(
    project_id: str,
    candidate_id: str,
):
    return await asyncio.to_thread(
        dubbing_production.read_candidate_semantic_boundary_audit,
        project_id,
        candidate_id,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/candidates/{candidate_id}/semantic-boundaries/review",
    response_model=DubbingCandidateCqcReport,
    summary="提交 Agent 对候选逐边界断句的完整处置",
)
async def submit_video_localization_candidate_semantic_boundaries(
    project_id: str,
    candidate_id: str,
    body: DubbingCandidateReviewCommand,
):
    if body.candidate_id != candidate_id:
        raise AppException(
            422,
            "DUBBING_SEMANTIC_AUDIT_CANDIDATE_MISMATCH",
            "路径中的候选与断句处置中的候选不一致。",
        )
    return await asyncio.to_thread(
        dubbing_production.submit_candidate_semantic_boundary_review,
        project_id,
        body,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/candidates/{candidate_id}/content-evidence",
    response_model=DubbingCandidateContentEvidenceResponse,
    summary="取得当前配音候选的独立内容转写证据",
    description=(
        "对当前候选绑定的实际生成音频做无提示词内容转写。模型调用在 Project 条件写入"
        "之外完成；只有候选、音频哈希和 repository revision 仍未变化时才保存证据。"
        "该动作只取得内容观察，不代表语义、自然度或主轨验收通过。"
    ),
)
async def acquire_video_localization_candidate_content_evidence(
    project_id: str,
    candidate_id: str,
    body: DubbingCandidateContentEvidenceRequest,
):
    if body.candidate_id != candidate_id:
        raise AppException(
            422,
            "DUBBING_CONTENT_EVIDENCE_CANDIDATE_MISMATCH",
            "路径中的候选与内容核对请求中的候选不一致。",
        )
    return await asyncio.to_thread(
        dubbing_production.acquire_candidate_content_evidence,
        project_id,
        body,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/candidates/{candidate_id}/current-projection",
    response_model=DubbingCandidateCqcReport,
    summary="Refresh Current Dubbing Projection",
    description="只核对当前主轨片段，不生成、不移动或替换音频。复用未变化的边界决定；变化部分由 Agent 继续处置。",
)
async def refresh_video_localization_current_candidate_projection(
    project_id: str, candidate_id: str, body: DubbingCurrentProjectionRequest,
):
    if body.candidate_id != candidate_id:
        raise AppException(422, "DUBBING_CURRENT_PROJECTION_CANDIDATE_MISMATCH", "候选路径与请求不一致。")
    return await asyncio.to_thread(dubbing_production.refresh_current_candidate_projection, project_id, body)


@router.post(
    "/{project_id}/video-localization/dubbing/candidates/{candidate_id}/staged-split",
    response_model=DubbingCandidateCqcReport,
    summary="在正式采纳前安全切分暂存配音候选",
)
async def split_video_localization_staged_candidate(
    project_id: str,
    candidate_id: str,
    body: DubbingStagedCandidateSplitRequest,
):
    if body.candidate_id != candidate_id:
        raise AppException(
            422,
            "DUBBING_STAGED_SPLIT_CANDIDATE_MISMATCH",
            "路径中的候选与暂存切分请求不一致。",
        )
    return await asyncio.to_thread(
        dubbing_production.split_staged_candidate_projection,
        project_id,
        body,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/production-run/execute",
    response_model=DubbingProductionExecuteResponse,
    summary="按统一语义组流程继续配音",
)
async def execute_video_localization_dubbing_production_run(
    project_id: str,
    body: DubbingProductionExecuteRequest,
):
    execution_options = {}
    if body.start_group_id is not None:
        execution_options["start_group_id"] = body.start_group_id
    if body.end_group_id is not None:
        execution_options["end_group_id"] = body.end_group_id
    if body.max_in_flight_groups != 2:
        execution_options["max_in_flight_groups"] = body.max_in_flight_groups
    if body.ordinary_speed_baseline is not None:
        execution_options["ordinary_speed_baseline"] = body.ordinary_speed_baseline
    return await video_localization_dubbing_executor.advance(
        project_id,
        scope=body.scope,
        group_id=body.group_id,
        review_mode=body.review_mode,
        regenerate_existing=body.regenerate_existing,
        repair_timeline_capacity=body.repair_timeline_capacity,
        resource_priority=body.resource_priority,
        **execution_options,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/production-run/manual-timing-deferral",
    response_model=DubbingManualTimingDeferralResponse,
    summary="将完整但超出时间窗的声音停放到第二配音轨",
    description=(
        "复用当前计划已有的完整生成结果，在 Project revision 条件写入中将全长音频"
        "停放到 dub_lane=1，并保存有证据的容量恢复处置。该命令不接受主轨结果、"
        "不生成新音频，也不把候选标记为质检通过。"
    ),
)
async def defer_video_localization_dubbing_group_for_manual_timing(
    project_id: str,
    body: DubbingManualTimingDeferralRequest,
):
    return await asyncio.to_thread(
        dubbing_production.defer_group_for_manual_timing,
        project_id,
        body,
    )


@router.post(
    "/{project_id}/video-localization/dubbing/production-run/failure",
    response_model=VideoLocalizationEditableDraftResponse,
    summary="记录单个配音组真实失败并继续不受影响的后续组",
)
async def record_video_localization_dubbing_group_failure(
    project_id: str,
    body: DubbingProductionGroupFailureRequest,
):
    updated = await asyncio.to_thread(
        dubbing_production.record_group_failure,
        project_id,
        body,
    )
    return require_resource(updated)


@router.post("/{project_id}/video-localization/tts/parameter-pack", response_model=TtsParameterPack)
async def prepare_video_localization_tts_parameter_pack(
    project_id: str,
    body: TtsSelectionRequest,
):
    parameter_pack = await asyncio.to_thread(
        video_localization_service.build_tts_parameter_pack,
        project_id,
        target_subtitle_ids=body.target_subtitle_ids,
        source_cue_ids=body.source_cue_ids,
    )
    return require_resource(parameter_pack)


@router.post("/{project_id}/video-localization/tts/handoff/{segment_id}", response_model=GenerateRequest)
async def prepare_video_localization_tts_handoff(
    project_id: str,
    segment_id: str,
    body: TtsHandoffPrepareRequest,
):
    request = await asyncio.to_thread(
        video_localization_service.build_single_tts_handoff,
        project_id,
        segment_id,
        history_result_id=body.history_result_id,
        parameters=body.parameters,
        timeline_clip_id=body.timeline_clip_id,
        target_subtitle_ids=body.target_subtitle_ids,
        source_cue_ids=body.source_cue_ids,
        voice_library_voice_id=body.voice_library_voice_id,
        workflow_id=body.submission_id,
    )
    return require_resource(request)


@router.post(
    "/{project_id}/video-localization/tts/handoff-reserve/{segment_id}",
    response_model=VideoLocalizationTtsTask,
)
async def reserve_video_localization_tts_handoff(
    project_id: str,
    segment_id: str,
    body: TtsHandoffPrepareRequest,
):
    task = await asyncio.to_thread(
        video_localization_service.reserve_single_tts_handoff,
        project_id,
        segment_id,
        history_result_id=body.history_result_id,
        parameters=body.parameters,
        timeline_clip_id=body.timeline_clip_id,
        target_subtitle_ids=body.target_subtitle_ids,
        source_cue_ids=body.source_cue_ids,
        voice_library_voice_id=body.voice_library_voice_id,
        workflow_id=body.submission_id,
    )
    return require_resource(task)


@router.post("/{project_id}/video-localization/tts/handoff-preview/{segment_id}", response_model=GenerateRequest)
async def preview_video_localization_tts_handoff(
    project_id: str,
    segment_id: str,
    body: TtsHandoffPrepareRequest,
):
    request = await asyncio.to_thread(
        video_localization_service.preview_single_tts_handoff,
        project_id,
        segment_id,
        history_result_id=body.history_result_id,
        parameters=body.parameters,
        timeline_clip_id=body.timeline_clip_id,
        target_subtitle_ids=body.target_subtitle_ids,
        source_cue_ids=body.source_cue_ids,
        voice_library_voice_id=body.voice_library_voice_id,
    )
    return require_resource(request)


@router.get("/{project_id}/video-localization/tts/tasks", response_model=list[VideoLocalizationTtsTask])
async def list_video_localization_tts_tasks(project_id: str):
    tasks = await asyncio.to_thread(video_localization_service.list_tts_tasks, project_id)
    return require_resource(tasks)


@router.get(
    "/{project_id}/video-localization/tts/tasks/feed",
    response_model=VideoLocalizationTtsTaskFeed,
)
async def get_video_localization_tts_task_feed(
    project_id: str,
    after_revision: str | None = Query(default=None),
):
    feed = await asyncio.to_thread(
        video_localization_service.get_tts_task_feed,
        project_id,
        after_revision=after_revision,
    )
    return require_resource(feed)


@router.get("/{project_id}/video-localization/tts/tasks/{workflow_id}", response_model=VideoLocalizationTtsTask)
async def get_video_localization_tts_task(project_id: str, workflow_id: str):
    task = await asyncio.to_thread(video_localization_service.get_tts_task, project_id, workflow_id)
    return require_resource(
        task,
        code="VIDEO_LOCALIZATION_TTS_TASK_NOT_FOUND",
        message="TTS workflow task not found",
    )


@router.post("/{project_id}/video-localization/tts/tasks/{workflow_id}/cancel", response_model=VideoLocalizationTtsTask)
async def cancel_video_localization_tts_task(project_id: str, workflow_id: str):
    task = await asyncio.to_thread(video_localization_service.cancel_tts_task, project_id, workflow_id)
    return require_resource(
        task,
        code="VIDEO_LOCALIZATION_TTS_TASK_NOT_FOUND",
        message="TTS workflow task not found",
    )


@router.delete(
    "/{project_id}/video-localization/tts/tasks/{workflow_id}", response_model=VideoLocalizationEditableDraftResponse
)
async def delete_video_localization_tts_task(project_id: str, workflow_id: str):
    draft = await asyncio.to_thread(video_localization_service.delete_tts_task, project_id, workflow_id)
    return require_resource(
        draft,
        code="VIDEO_LOCALIZATION_TTS_TASK_NOT_FOUND",
        message="TTS workflow task not found",
    )


@router.post(
    "/{project_id}/video-localization/tts/history/cleanup-unused",
    response_model=TtsUnusedCleanupResponse,
)
async def cleanup_unused_video_localization_tts_history(
    project_id: str,
    body: TtsUnusedCleanupRequest | None = None,
):
    result = await asyncio.to_thread(
        video_localization_service.cleanup_unused_tts_history,
        project_id,
        body.segment_id if body else None,
    )
    return require_resource(result)


@router.post(
    "/{project_id}/video-localization/tts/history/delete",
    response_model=TtsHistoryDeleteResponse,
)
async def delete_video_localization_tts_history(
    project_id: str,
    body: TtsHistoryDeleteRequest,
):
    result = await asyncio.to_thread(
        tts_history.delete_history,
        project_id,
        scope=body.scope,
        result_ids=body.result_ids,
        segment_id=body.segment_id,
    )
    return require_resource(result)


@router.get("/{project_id}/video-localization/cues/{cue_id}/tts-audio")
def get_video_localization_cue_tts_audio(project_id: str, cue_id: str):
    audio_path = video_localization_service.tts_audio_file(project_id, cue_id)
    return audio_file_response(
        audio_path, code="VIDEO_LOCALIZATION_TTS_AUDIO_NOT_FOUND", message="TTS audio file not found"
    )


@router.get("/{project_id}/video-localization/candidates/{candidate_id}/audio")
def get_video_localization_candidate_audio(project_id: str, candidate_id: str):
    audio_path = video_localization_service.generated_candidate_audio_file(project_id, candidate_id)
    return audio_file_response(
        audio_path, code="VIDEO_LOCALIZATION_CANDIDATE_AUDIO_NOT_FOUND", message="Generated candidate audio not found"
    )


@router.get("/{project_id}/video-localization/timeline-clips/{clip_id}/audio")
async def get_video_localization_timeline_clip_audio(project_id: str, clip_id: str):
    audio_path = await asyncio.to_thread(
        video_localization_service.timeline_clip_audio_preview_file, project_id, clip_id
    )
    return audio_file_response(
        audio_path,
        code="VIDEO_LOCALIZATION_TIMELINE_CLIP_AUDIO_NOT_FOUND",
        message="Timeline clip audio not found",
        cache_control="private, max-age=3600",
    )


@router.post(
    "/{project_id}/video-localization/timeline-clips/{clip_id}/history/{result_id}/apply",
    response_model=VideoLocalizationTimelineMutationResponse,
)
def apply_video_localization_history_to_timeline_clip(
    project_id: str, clip_id: str, result_id: str, payload: TtsHistoryPlacementRequest,
):
    updated = video_localization_service.apply_tts_history_to_timeline_clip(
        project_id, clip_id, result_id, request_id=payload.request_id,
    )
    return _timeline_mutation_response(
        project_id,
        require_resource(updated),
        affected_clip_ids={clip_id},
    )


@router.post(
    "/{project_id}/video-localization/timeline-clips/history/{result_id}/apply",
    response_model=VideoLocalizationTimelineMutationResponse,
)
def apply_video_localization_history_to_timeline(
    project_id: str,
    result_id: str,
    payload: TtsHistoryTimelineApplyRequest,
):
    updated = video_localization_service.apply_tts_history_to_timeline(
        project_id,
        result_id,
        request_id=payload.request_id,
        segment_id=payload.segment_id,
        clip_id=payload.clip_id,
        new_clip_id=payload.new_clip_id,
        start_ms=payload.start_ms,
        dub_lane=payload.dub_lane,
        force_new=payload.force_new,
    )
    return _timeline_mutation_response(
        project_id,
        require_resource(updated),
        affected_clip_ids={str(payload.clip_id or payload.new_clip_id)},
    )


@router.post(
    "/{project_id}/video-localization/timeline-clips/{candidate_clip_id}/local-phrase-repair/commit",
    response_model=VideoLocalizationEditableDraftResponse,
)
def commit_video_localization_local_phrase_repair(
    project_id: str,
    candidate_clip_id: str,
    payload: TtsLocalPhraseRepairCommitRequest,
):
    """Commit a playable local phrase and remove superseded clips atomically."""

    updated = video_localization_service.commit_local_phrase_repair(
        project_id,
        candidate_clip_id=candidate_clip_id,
        replace_clip_ids=payload.replace_clip_ids,
        target_start_ms=payload.target_start_ms,
        target_end_ms=payload.target_end_ms,
        source_start_ms=payload.source_start_ms,
        source_end_ms=payload.source_end_ms,
        target_text=payload.target_text,
    )
    return require_resource(updated)


@router.get("/{project_id}/video-localization/timeline-clips/{clip_id}/waveform", response_model=WaveformPeaksResponse)
async def get_video_localization_timeline_clip_waveform(
    project_id: str,
    clip_id: str,
    bins: int | None = Query(default=None, ge=waveform_cache.MIN_BINS, le=waveform_cache.MAX_BINS),
    start_ms: int | None = Query(default=None, ge=0),
    end_ms: int | None = Query(default=None, ge=1),
):
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise AppException(422, "VIDEO_LOCALIZATION_WAVEFORM_RANGE_INVALID", "Waveform end must be after start")
    audio_path = await asyncio.to_thread(video_localization_service.timeline_clip_audio_file, project_id, clip_id)
    if not audio_path:
        raise AppException(404, "VIDEO_LOCALIZATION_TIMELINE_CLIP_AUDIO_NOT_FOUND", "Timeline clip audio not found")
    cache_id = f"video-localization-{project_id}-{clip_id}"
    return await asyncio.to_thread(
        waveform_cache.waveform_peaks,
        audio_path,
        result_id=cache_id,
        bins=bins,
        max_bins=waveform_cache.MAX_BINS,
        start_ms=start_ms,
        end_ms=end_ms,
    )


@router.get("/{project_id}/video-localization/cues/{cue_id}/source-audio")
def get_video_localization_cue_source_audio(project_id: str, cue_id: str):
    audio_path = video_localization_service.source_cue_audio_file(project_id, cue_id)
    return audio_file_response(
        audio_path, code="VIDEO_LOCALIZATION_SOURCE_CUE_AUDIO_NOT_FOUND", message="Source cue audio file not found"
    )


@router.get("/{project_id}/video-localization/reference-clips/{reference_clip_id}/audio")
def get_video_localization_reference_clip_audio(project_id: str, reference_clip_id: str):
    audio_path = video_localization_service.reference_clip_audio_file(project_id, reference_clip_id)
    return audio_file_response(
        audio_path, code="VIDEO_LOCALIZATION_REFERENCE_AUDIO_NOT_FOUND", message="Reference audio file not found"
    )


@router.get("/{project_id}/video-localization/subtitles/{kind}")
def export_video_localization_subtitles(
    project_id: str,
    kind: str,
    localized_variant: Literal["localized", "dub"] = Query(
        default="localized",
    ),
):
    srt = video_localization_service.export_subtitles(
        project_id,
        kind,
        localized_variant=localized_variant,
    )
    srt = require_resource(srt)
    content_label = (
        "ASR"
        if kind == "en"
        else ("配音" if kind == "zh" and localized_variant == "dub" else "本土化" if kind == "zh" else "双语")
    )
    filename = video_localization_exports.download_filename(
        project_id,
        ExportFilenameSpec(
            category="字幕",
            content=content_label,
            extension="srt",
        ),
        revision_source=srt,
    )
    return srt_attachment(srt, filename=filename)


@router.delete("/{project_id}/video-localization/subtitles/{kind}", response_model=VideoLocalizationEditableDraftResponse)
def clear_video_localization_subtitles(project_id: str, kind: Literal["en", "zh"]):
    updated = video_localization_service.clear_subtitles(project_id, kind)
    return require_resource(updated)


@router.post("/{project_id}/video-localization/subtitles/{kind}/import", response_model=VideoLocalizationEditableDraftResponse)
def import_video_localization_subtitles(project_id: str, kind: str, request: VideoLocalizationSubtitleImportRequest):
    updated = video_localization_service.import_subtitles(project_id, kind, request)
    return require_resource(updated)


@router.get("/{project_id}/video-localization/export")
def export_video_localization(project_id: str):
    data = video_localization_exports.export_bundle(project_id)
    data = require_resource(data)
    payload = public_video_localization_export(data)
    filename = video_localization_exports.download_filename(
        project_id,
        ExportFilenameSpec(
            category="工程数据",
            content="完整导出",
            extension="json",
        ),
        revision_source=payload.model_dump_json(),
    )
    return json_attachment(payload, filename=filename)


@router.get("/{project_id}/video-localization/export/timeline")
def export_video_localization_timeline(project_id: str):
    data = video_localization_exports.timeline_edl(project_id)
    data = require_resource(data)
    filename = video_localization_exports.download_filename(
        project_id,
        ExportFilenameSpec(
            category="工程数据",
            content="时间线EDL",
            extension="json",
        ),
        revision_source=json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return json_attachment(data, filename=filename)


@router.post(
    "/{project_id}/video-localization/export/filename-preview",
    response_model=VideoLocalizationMediaExportFilenamePreviewResponse,
    summary="预览媒体导出的默认文件名",
    description=("根据项目名称和当前导出设置生成默认 basename。该接口不创建文件，也不接受或返回保存路径。"),
)
def preview_video_localization_media_export_filename(
    project_id: str,
    request: VideoLocalizationMediaExportRequest,
):
    output_filename = video_localization_exports.default_media_export_filename(
        project_id,
        request,
    )
    return VideoLocalizationMediaExportFilenamePreviewResponse(
        output_filename=require_resource(
            output_filename,
            code="VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
            message="导出项目不存在。",
        )
    )


@router.post(
    "/{project_id}/video-localization/export/destination",
    response_model=VideoLocalizationExportDestinationResponse,
    summary="选择媒体导出保存目录",
    description=(
        "default 使用设置页配置的导出目录；choose 打开当前操作系统的"
        "原生目录选择器。公共接口只返回临时目录授权 ID，不接受任意路径。"
    ),
)
def select_video_localization_export_destination(
    project_id: str,
    request: VideoLocalizationExportDestinationRequest,
):
    require_resource(
        project_store.get_project_repository_revision(project_id),
        code="VIDEO_LOCALIZATION_PROJECT_NOT_FOUND",
        message="导出项目不存在。",
    )
    try:
        selected = video_localization_export_destinations.select_destination(
            request.mode,
        )
    except video_localization_export_destinations.ExportDestinationError as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_EXPORT_DESTINATION_UNAVAILABLE",
            str(exc),
        ) from exc
    return VideoLocalizationExportDestinationResponse(
        status=selected.status,
        destination_id=selected.destination_id,
        display_path=selected.display_path,
    )


@router.post(
    "/{project_id}/video-localization/export/render",
    response_model=VideoLocalizationOperation,
    summary="按所选内容生成单个视频、音频或字幕文件",
    description=(
        "提交后台导出任务，把明确勾选的字幕与音频轨道直接输出到"
        "用户已经选择的目录。任务状态提供真实渲染进度；"
        "不会通过浏览器下载整份成品。"
    ),
)
def create_video_localization_media_export(
    project_id: str,
    request: VideoLocalizationMediaExportCommandRequest,
):
    try:
        video_localization_export_destinations.resolve_destination(request.destination_id)
    except video_localization_export_destinations.ExportDestinationError as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_EXPORT_DESTINATION_UNAVAILABLE",
            str(exc),
        ) from exc
    operation = video_localization_operations.submit_operation(
        project_id,
        "media_export",
        {
            "destination_id": request.destination_id,
            "render": request.render.model_dump(mode="json"),
            "output_filename": request.output_filename,
        },
    )
    return require_resource(operation)


@router.get("/{project_id}/video-localization/readiness")
def export_video_localization_readiness(project_id: str):
    data = video_localization_service.production_readiness_audit(project_id)
    data = require_resource(data)
    filename = video_localization_exports.download_filename(
        project_id,
        ExportFilenameSpec(
            category="工程数据",
            content="生产检查",
            extension="json",
        ),
        revision_source=json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return json_attachment(data, filename=filename)


@router.post(
    "/{project_id}/video-localization/dubbing/recovery",
    response_model=DubbingProductionExecuteResponse,
    summary="按已确认分句或同说话人参考继续恢复配音",
)
async def recover_video_localization_dubbing(project_id: str, body: DubbingRecoveryDecision):
    return await video_localization_dubbing_executor.advance_recovery(project_id, body)


@router.get(
    "/{project_id}/video-localization/dubbing/groups/{group_id}/preflight",
    response_model=DubbingGroupPreflightResult,
    summary="生成前核对可用时间和同音色时长预估",
)
def get_video_localization_dubbing_preflight(project_id: str, group_id: str, speed: float = Query(default=1.25, ge=0.5, le=2.0)):
    return dubbing_production.read_group_preflight(project_id, group_id, speed=speed)
