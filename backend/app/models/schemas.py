from __future__ import annotations

import uuid
import os
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_data_dir() -> str:
    return os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio")


def default_data_subdir(name: str) -> str:
    return os.environ.get(f"VOICE_STUDIO_{name.upper()}_DIR", f"{default_data_dir()}/{name}")


class EngineType(str, Enum):
    local = "local"
    cloud = "cloud"


class EngineStatus(str, Enum):
    not_installed = "not_installed"
    stopped = "stopped"
    loading = "loading"
    loaded = "loaded"
    running = "running"
    error = "error"


class TaskStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    running = "running"
    postprocessing = "postprocessing"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"
    retrying = "retrying"


class TimestampMode(str, Enum):
    none = "none"
    native = "native"
    supplemented = "supplemented"


class VoiceType(str, Enum):
    real_person = "real_person"
    virtual_character = "virtual_character"
    host = "host"
    singer = "singer"
    narrator = "narrator"
    emotion_reference = "emotion_reference"
    test_sample = "test_sample"


class LicenseStatus(str, Enum):
    self_voice = "self_voice"
    company_authorized = "company_authorized"
    authorized = "authorized"
    test_only = "test_only"
    unknown = "unknown"
    commercial_forbidden = "commercial_forbidden"


class EmotionMode(str, Enum):
    follow_reference = "follow_reference"
    emotion_vector = "emotion_vector"
    emotion_text = "emotion_text"


EMOTIONS = ["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]


class ParameterSchema(BaseModel):
    key: str
    label: str
    description: str | None = None
    type: Literal["text", "textarea", "number", "slider", "select", "toggle", "file"]
    level: Literal["basic", "advanced", "developer"] = "basic"
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[dict[str, str]] = Field(default_factory=list)
    required: bool = False
    capability: str | None = None


class EngineManifest(BaseModel):
    engine_id: str
    display_name: str
    engine_type: EngineType = EngineType.local
    provider: str = ""
    version: str = ""
    description: str = ""
    supported_languages: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    sample_rate: int | None = None
    max_tokens: int | None = None
    privacy_level: str = "local_only"
    default_use_case: str = ""
    parameter_schema: list[ParameterSchema] = Field(default_factory=list)


class EngineState(BaseModel):
    engine_id: str
    status: EngineStatus = EngineStatus.stopped
    model_path: str | None = None
    error_message: str | None = None
    loaded_at: str | None = None


class EngineSpeaker(BaseModel):
    speaker_id: str
    name: str
    gender: str = ""
    description: str = ""
    label: str


class EngineDetail(BaseModel):
    manifest: EngineManifest
    state: EngineState


class AppSettings(BaseModel):
    data_dir: str = Field(default_factory=default_data_dir)
    model_dir: str = "models"
    voice_dir: str = Field(default_factory=lambda: default_data_subdir("voices"))
    output_dir: str = Field(default_factory=lambda: default_data_subdir("outputs"))
    export_dir: str = Field(default_factory=lambda: default_data_subdir("exports"))
    project_dir: str = Field(default_factory=lambda: default_data_subdir("projects"))
    cache_dir: str = Field(default_factory=lambda: default_data_subdir("cache"))
    log_dir: str = Field(default_factory=lambda: default_data_subdir("logs"))
    default_engine_id: str = "indextts-v2"
    default_voice_id: str | None = None
    default_language: str = "zh"
    default_output_format: Literal["wav", "mp3", "flac"] = "wav"
    device: Literal["auto", "mps", "cpu"] = "auto"
    cloud_enabled: bool = False
    mimo_base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    mimo_api_key_configured: bool = False
    mimo_default_voice: str = "mimo_default"
    mimo_voiceclone_confirm_upload: bool = True
    default_emotion: str = "calm"
    default_emo_alpha: float = 0.6
    theme: Literal["system", "dark", "light"] = "system"


class MimoSecretUpdate(BaseModel):
    api_key: str | None = None
    clear: bool = False


class VoiceAssetCreate(BaseModel):
    name: str
    voice_type: VoiceType = VoiceType.test_sample
    description: str = ""
    default_language: str = "zh"
    tags: list[str] = Field(default_factory=list)
    reference_text: str = ""
    recommended_engine_id: str | None = None
    reference_audio_ids: list[str] = Field(default_factory=list)
    license_status: LicenseStatus = LicenseStatus.unknown


class VoiceAssetUpdate(BaseModel):
    name: str | None = None
    voice_type: VoiceType | None = None
    description: str | None = None
    default_language: str | None = None
    tags: list[str] | None = None
    reference_text: str | None = None
    recommended_engine_id: str | None = None
    reference_audio_ids: list[str] | None = None
    license_status: LicenseStatus | None = None
    quality_status: str | None = None
    quality_notes: str | None = None
    favorite: bool | None = None
    emotion_tags: list[str] | None = None


class VoiceEngineBinding(BaseModel):
    engine_id: str
    mode: Literal["reference_audio", "preset_voice", "voice_design", "voice_clone"]
    available: bool
    reason: str = ""
    external_voice_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class VoiceAsset(VoiceAssetCreate):
    voice_id: str = Field(default_factory=new_id)
    quality_status: str = "unchecked"
    quality_notes: str = ""
    favorite: bool = False
    emotion_tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    last_used_at: str | None = None
    engine_bindings: list[VoiceEngineBinding] = Field(default_factory=list)


class VoiceFile(BaseModel):
    file_id: str = Field(default_factory=new_id)
    original_name: str
    path: str
    mime_type: str = "audio/wav"
    duration_ms: int | None = None
    sample_rate: int | None = None
    size_bytes: int = 0
    created_at: str = Field(default_factory=now_iso)


class TranscriptionSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    language: str | None = None


class TranscriptionRecord(BaseModel):
    transcription_id: str = Field(default_factory=new_id)
    engine_id: str = "mimo-v2.5-asr"
    filename: str
    language: Literal["auto", "zh", "en"] = "auto"
    text: str
    segments: list[TranscriptionSegment] = Field(default_factory=list)
    has_source_audio: bool = False
    timestamp_mode: TimestampMode = TimestampMode.none
    timestamp_source_engine_id: str | None = None
    duration_ms: int | None = None
    size_bytes: int = 0
    usage_seconds: int | None = None
    provider_response_id: str | None = None
    created_at: str = Field(default_factory=now_iso)


class TranscriptionTask(BaseModel):
    task_id: str = Field(default_factory=new_id)
    engine_id: str = "mimo-v2.5-asr"
    filename: str
    language: Literal["auto", "zh", "en"] = "auto"
    status: TaskStatus = TaskStatus.queued
    text: str | None = None
    segments: list[TranscriptionSegment] = Field(default_factory=list)
    has_source_audio: bool = False
    timestamp_mode: TimestampMode = TimestampMode.none
    timestamp_source_engine_id: str | None = None
    transcription_id: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    size_bytes: int = 0
    usage_seconds: int | None = None
    provider_response_id: str | None = None
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class TimestampSupplementRequest(BaseModel):
    strategy: Literal["auto", "forced_aligner", "qwen3-asr-mlx"] = "auto"
    overwrite: bool = False


class TranscriptionBatchDeleteRequest(BaseModel):
    transcription_ids: list[str] = Field(default_factory=list)


class TranscriptionBatchSupplementRequest(TimestampSupplementRequest):
    transcription_ids: list[str] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    text: str
    engine_id: str = "indextts-v2"
    voice_id: str | None = None
    reference_audio_path: str | None = None
    ref_text: str | None = None
    language: str = "zh"
    emotion_mode: EmotionMode = EmotionMode.follow_reference
    emotion: str | None = None
    emotion_values: dict[str, float] | None = None
    emotion_text: str | None = None
    style_instruction: str | None = None
    voice_design_prompt: str | None = None
    optimize_text_preview: bool = False
    mimo_voice: str | None = None
    idempotency_marker: str | None = None
    speaker_id: str | None = None
    prompt: str | None = None
    nfe_step: int = Field(default=32, ge=4, le=64)
    cfg_strength: float = Field(default=2.0, ge=0.1, le=5.0)
    target_rms: float = Field(default=0.1, ge=0.01, le=1.0)
    cross_fade_duration: float = Field(default=0.15, ge=0.0, le=1.0)
    remove_silence: bool = False
    emo_alpha: float = Field(default=0.6, ge=0, le=1)
    speed: float = Field(default=1.0, ge=0.5, le=3.0)
    temperature: float = Field(default=0.8, ge=0.1, le=2.0)
    top_p: float = Field(default=0.8, ge=0.0, le=1.0)
    top_k: int = Field(default=30, ge=1, le=100)
    repetition_penalty: float = Field(default=10.0, ge=1.0, le=20.0)
    seed: int | None = None
    max_mel_tokens: int = Field(default=800, ge=100, le=2500)
    max_text_tokens_per_segment: int = Field(default=120, ge=10, le=500)
    interval_silence: int = Field(default=200, ge=0, le=2000)
    segment_overlap_ms: int = Field(default=50, ge=0, le=500)
    diffusion_steps: int = Field(default=25, ge=1, le=100)
    cfg_rate: float = Field(default=0.7, ge=0.0, le=1.0)
    output_format: Literal["wav", "mp3", "flac"] = "wav"


class GenerateResponse(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.queued


class GeneratePlanRequest(BaseModel):
    text: str
    engine_id: str = "indextts-v2"
    planner_mode: Literal["auto", "rules", "llm"] = "auto"
    target_format: Literal["wav", "mp3", "flac"] = "mp3"


class PlannedTextSegment(BaseModel):
    index: int
    text: str
    char_count: int
    segment_reason: str = "sentence_boundary"


class GeneratePlanResponse(BaseModel):
    planner: Literal["rules", "llm"] = "rules"
    llm_available: bool = False
    mode: Literal["direct", "longform_recommended", "longform_strongly_recommended"] = "direct"
    recommended_action: Literal["direct_generate", "direct_generate_with_verification", "split_generate", "split_verify_merge"] = "direct_generate"
    requires_user_confirmation: bool = False
    text_length: int = 0
    threshold: int = 0
    hard_threshold: int = 0
    warnings: list[str] = Field(default_factory=list)
    privacy_notice: str = ""
    planner_reason: str = ""
    segments: list[PlannedTextSegment] = Field(default_factory=list)


class TTSVerificationRequest(BaseModel):
    result_id: str | None = None
    expected_text: str | None = None
    transcript_text: str | None = None
    asr_engine_id: str = "qwen3-asr-mlx"
    language: Literal["auto", "zh", "en"] = "auto"


class TTSVerificationSegment(BaseModel):
    index: int
    expected_text: str
    normalized_expected: str
    coverage: float
    status: Literal["passed", "warning", "failed"]


class TTSVerificationResponse(BaseModel):
    status: Literal["passed", "warning", "failed", "skipped"]
    coverage: float
    similarity: float
    expected_text: str
    transcript_text: str
    normalized_expected: str
    normalized_transcript: str
    missing_segments: list[TTSVerificationSegment] = Field(default_factory=list)
    segment_results: list[TTSVerificationSegment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    result_id: str | None = None
    transcription_id: str | None = None
    asr_engine_id: str | None = None


class LongformGenerateRequest(BaseModel):
    generate_request: GenerateRequest
    segments: list[PlannedTextSegment] | None = None
    verify_enabled: bool = True
    merge_enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=5)
    stop_merge_on_verification_failed: bool = True
    asr_engine_id: str = "qwen3-asr-mlx"
    silence_ms: int = Field(default=300, ge=0, le=5000)
    normalize: bool = False


class LongformSegmentTask(BaseModel):
    index: int
    text: str
    char_count: int
    status: TaskStatus = TaskStatus.pending
    attempts: int = 0
    task_id: str | None = None
    result_id: str | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    verification: TTSVerificationResponse | None = None


class LongformTask(BaseModel):
    longform_task_id: str = Field(default_factory=new_id)
    engine_id: str
    voice_id: str | None = None
    input_text: str
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    segments: list[LongformSegmentTask] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    export_id: str | None = None
    export_path: str | None = None
    verify_enabled: bool = True
    merge_enabled: bool = True
    max_retries: int = 2
    stop_merge_on_verification_failed: bool = True
    asr_engine_id: str = "qwen3-asr-mlx"
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class BatchSegmentInput(BaseModel):
    segment_id: str | None = None
    chapter: str | None = None
    step: int | None = None
    text: str
    audio: str | None = None
    engine_id: str | None = None
    voice_id: str | None = None
    reference_audio_path: str | None = None
    ref_text: str | None = None
    language: str | None = None
    emotion: str | None = None
    emotion_text: str | None = None
    style_instruction: str | None = None
    voice_design_prompt: str | None = None
    mimo_voice: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=3.0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class BatchGenerateRequest(BaseModel):
    project_name: str = "批量语音项目"
    engine_id: str = "indextts-v2"
    voice_id: str | None = None
    reference_audio_path: str | None = None
    ref_text: str | None = None
    language: str = "zh"
    output_dir: str | None = None
    output_format: Literal["wav", "mp3", "flac"] = "mp3"
    partial_success: bool = False
    segments: list[BatchSegmentInput]
    parameters: dict[str, Any] = Field(default_factory=dict)


class BatchSegmentResult(BaseModel):
    segment_id: str
    chapter: str | None = None
    step: int | None = None
    text: str
    audio: str | None = None
    output_path: str | None = None
    duration_ms: int | None = None
    status: TaskStatus = TaskStatus.pending
    error_message: str | None = None


class BatchTask(BaseModel):
    batch_task_id: str = Field(default_factory=new_id)
    project_name: str = "批量语音项目"
    engine_id: str = "indextts-v2"
    voice_id: str | None = None
    output_dir: str | None = None
    output_format: str = "mp3"
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    segments: list[BatchSegmentResult] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class GenerationTask(BaseModel):
    task_id: str = Field(default_factory=new_id)
    task_type: Literal["single", "segment", "batch", "export"] = "single"
    engine_id: str
    voice_id: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    longform_task_id: str | None = None
    longform_segment_index: int | None = None
    longform_segment_count: int | None = None
    longform_export_id: str | None = None
    input_text: str
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    result_audio_id: str | None = None
    result_id: str | None = None
    result_duration_ms: int | None = None
    generation_time_ms: int | None = None
    verification: TTSVerificationResponse | None = None
    verification_error: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class HistoryItem(BaseModel):
    result_id: str = Field(default_factory=new_id)
    task_id: str
    engine_id: str
    voice_id: str | None = None
    voice_name: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    longform_task_id: str | None = None
    longform_segment_index: int | None = None
    longform_segment_count: int | None = None
    longform_export_id: str | None = None
    input_text: str
    output_audio_id: str | None = None
    output_path: str | None = None
    duration_ms: int | None = None
    generation_time_ms: int | None = None
    verification: TTSVerificationResponse | None = None
    verification_error: str | None = None
    parameter_snapshot: dict[str, Any] = Field(default_factory=dict)
    favorite: bool = False
    created_at: str = Field(default_factory=now_iso)


class Role(BaseModel):
    role_id: str = Field(default_factory=new_id)
    name: str
    color: str = "#3B82F6"
    default_voice_id: str | None = None
    default_engine_id: str | None = None
    default_language: str = "zh"
    default_emotion: str | None = None
    default_speed: float = 1.0
    default_parameters: dict[str, Any] = Field(default_factory=dict)


class SegmentStatus(str, Enum):
    empty = "empty"
    ready = "ready"
    queued = "queued"
    generating = "generating"
    completed = "completed"
    failed = "failed"
    locked = "locked"


class ScriptSegment(BaseModel):
    segment_id: str = Field(default_factory=new_id)
    index: int
    text: str = ""
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    role_id: str | None = None
    voice_id: str | None = None
    engine_id: str | None = None
    language: str = "zh"
    emotion: str | None = None
    speed: float | None = None
    status: SegmentStatus = SegmentStatus.empty
    result_audio_id: str | None = None
    result_id: str | None = None
    error_message: str | None = None
    locked: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    default_engine_id: str | None = None


class ProjectTranscriptionImportRequest(BaseModel):
    transcription_ids: list[str] = Field(default_factory=list)
    mode: Literal["append", "replace"] = "append"
    role_id: str | None = None
    default_engine_id: str | None = None
    default_voice_id: str | None = None


class Project(BaseModel):
    project_id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    default_engine_id: str | None = "indextts-v2"
    parameters: dict[str, Any] = Field(default_factory=dict)
    roles: list[Role] = Field(default_factory=list)
    segments: list[ScriptSegment] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ProjectTranscriptionImportResponse(BaseModel):
    project: Project
    imported_count: int = 0
    skipped_count: int = 0


class ExportRequest(BaseModel):
    result_ids: list[str] = Field(default_factory=list)
    audio_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None
    format: Literal["wav", "mp3", "flac"] = "wav"
    silence_ms: int = Field(default=300, ge=0, le=5000)
    normalize: bool = False


class ExportRecord(BaseModel):
    export_id: str = Field(default_factory=new_id)
    path: str
    format: str
    source_count: int
    created_at: str = Field(default_factory=now_iso)


class AudioQualityResult(BaseModel):
    duration_ms: int = 0
    sample_rate: int = 0
    peak: float = 0.0
    rms: float = 0.0
    silence_ratio: float = 1.0
    size_bytes: int = 0
    passed: bool = False
    warnings: list[str] = Field(default_factory=list)


class PresetTemplate(BaseModel):
    preset_id: str
    name: str
    scene: str
    description: str
    engine_id: str
    sample_text: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_test_id: str | None = None
    recommended_voice_type: str = "reference_voice"
    tags: list[str] = Field(default_factory=list)


class PresetTemplateUpsert(BaseModel):
    preset_id: str | None = None
    name: str
    scene: str = ""
    description: str = ""
    engine_id: str = "indextts-v2"
    sample_text: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_test_id: str | None = None
    recommended_voice_type: str = "reference_voice"
    tags: list[str] = Field(default_factory=list)


class VoiceSeed(BaseModel):
    seed_id: str
    name: str
    description: str = ""
    source: str
    download_url: str
    recommended_engine_id: str = "indextts-v2"
    reference_text: str = ""
    tags: list[str] = Field(default_factory=list)
    license_status: LicenseStatus = LicenseStatus.test_only
    imported_voice_id: str | None = None
    quality: AudioQualityResult | None = None


class VoiceSeedImportRequest(BaseModel):
    seed_id: str


class CommunityVoiceCandidate(BaseModel):
    candidate_id: str
    name: str
    description: str = ""
    source: str
    download_url: str
    recommended_engine_id: str = "indextts-v2"
    reference_text: str = ""
    tags: list[str] = Field(default_factory=list)
    license_status: LicenseStatus = LicenseStatus.test_only
    imported_voice_id: str | None = None
    quality: AudioQualityResult | None = None


class CommunityVoicePack(BaseModel):
    pack_id: str
    name: str
    description: str
    source: str
    license_summary: str
    tags: list[str] = Field(default_factory=list)
    candidates: list[CommunityVoiceCandidate] = Field(default_factory=list)
    imported_count: int = 0


class CommunityVoicePackImportRequest(BaseModel):
    pack_id: str
    candidate_ids: list[str] = Field(default_factory=list)


class EngineAudioDiagnosisRequest(BaseModel):
    text: str = "这是本地引擎音频诊断测试，用来确认生成结果是否清晰可听。"
    reference_audio_path: str | None = None
    voice_id: str | None = None
    language: str = "zh"
    emotion: str | None = None
    emotion_text: str | None = "女，青年，中音调"


class SERPredictRequest(BaseModel):
    voice_id: str


class SERBatchPredictRequest(BaseModel):
    voice_ids: list[str] = Field(default_factory=list)
    all: bool = False


class SEREmotionResult(BaseModel):
    voice_id: str
    top_emotion: str | None = None
    emotion_scores: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
