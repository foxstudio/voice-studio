"""Legacy stable schema path kept for the Voice Studio 1.x line.

New backend code should prefer ``app.schemas.voice_studio``. This module remains
the implementation source during the compatibility window so old imports keep
class identity unchanged.
"""

from __future__ import annotations

import uuid
import math
import os
import re
import urllib.parse
from datetime import datetime
from enum import Enum
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_serializer, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.video_localization_dubbing_production import (
    DubbingProductionState,
)
from app.schemas.tts_content import TtsContentEvidence
from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision
from app.schemas.video_localization_dubbing_preflight import DubbingGroupPreflightResult
from app.schemas.asr_uncertainty import AsrUnconfirmedTextSpan
from app.schemas.video_localization_asr_repair import AsrSourceRepairReceipt
from app.schemas.video_localization_binding_repair import BindingRepairReceipt


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
    localized = "本土化"
    localized_dub_source = "localized_dub_source"
    test_only = "test_only"
    unknown = "unknown"
    commercial_forbidden = "commercial_forbidden"


class EmotionMode(str, Enum):
    follow_reference = "follow_reference"
    emotion_vector = "emotion_vector"
    emotion_reference = "emotion_reference"
    emotion_text = "emotion_text"


class VoiceSource(str, Enum):
    voice_library = "voice_library"
    reference_audio = "reference_audio"
    model_preset = "model_preset"
    voice_design = "voice_design"


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
    options: list[dict[str, Any]] = Field(default_factory=list)
    required: bool = False
    capability: str | None = None


class EngineManifest(BaseModel):
    engine_id: str
    display_name: str
    engine_type: EngineType = EngineType.local
    provider: str = ""
    version: str = ""
    description: str = ""
    documentation_url: str | None = None
    documentation_label: str | None = None
    supported_languages: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    sample_rate: int | None = None
    max_tokens: int | None = None
    privacy_level: str = "local_only"
    default_use_case: str = ""
    parameter_schema: list[ParameterSchema] = Field(default_factory=list)
    input_modes: list[Literal["text", "audio", "image"]] = Field(default_factory=list)
    max_reference_audio: int = 0
    max_reference_image: int = 0
    mutually_exclusive_inputs: list[list[str]] = Field(default_factory=list)
    prompt_reference_syntax: str | None = None
    supported_output_formats: list[str] = Field(default_factory=list)
    supported_sample_rates: list[int] = Field(default_factory=list)
    max_prompt_chars: int | None = None
    max_output_seconds: int | None = None


class EngineState(BaseModel):
    engine_id: str
    status: EngineStatus = EngineStatus.stopped
    model_path: str | None = None
    error_message: str | None = None
    loaded_at: str | None = None


class EngineCompatibility(BaseModel):
    status: Literal["compatible", "unavailable", "unknown"] = "unknown"
    compatible: bool | None = None
    reason_code: str | None = None
    message: str = ""
    operating_system: str = "unknown"
    architecture: str = "unknown"
    supported_platforms: list[str] = Field(default_factory=list)
    available_devices: list[str] = Field(default_factory=list)


class EngineSpeaker(BaseModel):
    speaker_id: str
    name: str
    gender: str = ""
    description: str = ""
    label: str
    age: str = ""
    languages: list[str] = Field(default_factory=list)
    emotions: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    normal_labels: list[str] = Field(default_factory=list)
    special_labels: list[str] = Field(default_factory=list)
    trial_url: str | None = None
    short_trial_url: str | None = None
    preview_text: str = ""
    avatar_url: str | None = None
    resource_id: str | None = None
    catalog_source: str = "bundled"
    catalog_updated_at: str | None = None
    catalog_stale: bool = True
    authorization_status: str = "unknown"
    deprecated: bool = False


class EngineDetail(BaseModel):
    manifest: EngineManifest
    state: EngineState
    compatibility: EngineCompatibility = Field(default_factory=EngineCompatibility)


class AppSettings(BaseModel):
    data_dir: str = Field(default_factory=default_data_dir)
    model_dir: str = Field(default_factory=lambda: default_data_subdir("models"))
    model_dir_effective: str = Field(default_factory=lambda: default_data_subdir("models"))
    model_dir_source: Literal["settings", "environment"] = "settings"
    voice_dir: str = Field(default_factory=lambda: default_data_subdir("voices"))
    output_dir: str = Field(default_factory=lambda: default_data_subdir("outputs"))
    export_dir: str = Field(default_factory=lambda: default_data_subdir("exports"))
    project_dir: str = Field(default_factory=lambda: default_data_subdir("projects"))
    cache_dir: str = Field(default_factory=lambda: default_data_subdir("cache"))
    # 自动清理的保留天数。0 表示永不自动清理；删除统一走系统废纸篓，不做不可恢复的删除。
    storage_retention_cache_days: int = Field(default=30, ge=0, le=3650)
    storage_retention_artifact_days: int = Field(default=30, ge=0, le=3650)
    storage_retention_output_days: int = Field(default=0, ge=0, le=3650)
    video_preview_cache_mode: Literal["auto", "compact", "quality"] = "auto"
    video_preview_cache_max_gb: float = Field(default=4.0, ge=0.5, le=64.0)
    stem_separation_overlap: int = Field(default=8, ge=2, le=50)
    stem_separation_chunk_duration_seconds: int = Field(
        default=300,
        ge=60,
        le=1800,
    )
    video_localization_development_step_control_enabled: bool = False
    video_localization_ai_strategy: Literal[
        "auto",
        "quality",
        "cost",
    ] = "auto"
    video_localization_prompt_strategy: Literal[
        "adaptive",
        "fixed",
    ] = "adaptive"
    video_localization_understanding_profile_id: str | None = None
    video_localization_creation_profile_id: str | None = None
    video_localization_review_profile_id: str | None = None
    video_localization_alignment_profile_id: str | None = None
    log_dir: str = Field(default_factory=lambda: default_data_subdir("logs"))
    default_engine_id: str = "indextts-v2"
    default_asr_engine_id: Literal[
        "auto",
        "qwen3-asr-mlx",
        "faster-whisper-turbo",
        "vibevoice-asr-mlx-4bit",
        "vibevoice-asr-mlx-8bit",
    ] = "auto"
    default_voice_id: str | None = None
    default_language: str = "zh"
    default_output_format: Literal["wav", "mp3", "flac"] = "wav"
    device: Literal["auto", "cuda", "mps", "cpu"] = "auto"
    cloud_enabled: bool = False
    # The public MiMo API endpoint. Token Plan subscribers can still enter a
    # token-plan-{region} endpoint explicitly in Settings.
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_api_key_configured: bool = False
    mimo_default_voice: str = "mimo_default"
    mimo_voiceclone_confirm_upload: bool = True
    doubao_base_url: str = "https://openspeech.bytedance.com"
    doubao_api_key_configured: bool = False
    volcengine_access_key_id_configured: bool = False
    volcengine_secret_access_key_configured: bool = False
    doubao_default_tts_resource_id: str = "seed-tts-2.0"
    doubao_default_icl_resource_id: str = "seed-icl-2.0"
    doubao_upload_confirm: bool = True
    default_emotion: str = "calm"
    default_emo_alpha: float = 0.6
    theme: Literal["system", "dark", "light"] = "system"

    @field_validator(
        "video_localization_understanding_profile_id",
        "video_localization_creation_profile_id",
        "video_localization_review_profile_id",
        "video_localization_alignment_profile_id",
    )
    @classmethod
    def validate_video_localization_profile_id(
        cls,
        value: str | None,
    ) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if (
            len(normalized) > 64
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]*",
                normalized,
            )
        ):
            raise ValueError("本土化模型配置 ID 格式无效")
        return normalized

    @field_validator("doubao_base_url")
    @classmethod
    def validate_doubao_base_url(cls, value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "openspeech.bytedance.com"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("doubao_base_url 必须是火山引擎官方 HTTPS 地址")
        return value.rstrip("/")


class AppSettingsPatch(AppSettings):
    """Compatibility PATCH body; only ``model_fields_set`` is persisted."""

    model_config = ConfigDict(extra="forbid")


LlmReasoningEffort = Literal["default", "low", "high", "max"]


class LlmProviderProfile(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=80)
    protocol: Literal["openai_compatible", "codex_cli"] = "openai_compatible"
    base_url: str = Field(default="", max_length=500)
    model_id: str = Field(default="", max_length=200)
    reasoning_effort: LlmReasoningEffort = "default"
    enabled: bool = True
    api_key_configured: bool = False
    model_test_verified: bool = False

    @field_validator("name", "base_url", "model_id")
    @classmethod
    def strip_llm_profile_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_llm_profile_protocol_fields(self):
        if self.protocol == "openai_compatible" and not self.base_url:
            raise ValueError("OpenAI Compatible 配置必须填写 Base URL")
        if self.protocol == "codex_cli":
            self.base_url = ""
            self.api_key_configured = False
        return self


class LlmProviderProfileUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    protocol: Literal["openai_compatible", "codex_cli"] = "openai_compatible"
    base_url: str = Field(default="", max_length=500)
    model_id: str = Field(default="", max_length=200)
    reasoning_effort: LlmReasoningEffort = "default"
    enabled: bool = True
    api_key: str | None = None
    clear_api_key: bool = False

    @field_validator("name", "base_url", "model_id")
    @classmethod
    def strip_llm_upsert_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_llm_upsert_protocol_fields(self):
        if self.protocol == "openai_compatible" and not self.base_url:
            raise ValueError("OpenAI Compatible 配置必须填写 Base URL")
        if self.protocol == "codex_cli":
            if self.api_key is not None and self.api_key.strip():
                raise ValueError("本机 Codex 使用 ChatGPT 登录，不接受 API Key")
            self.base_url = ""
            self.api_key = None
            self.clear_api_key = True
        return self


class LlmProviderListResponse(BaseModel):
    profiles: list[LlmProviderProfile] = Field(default_factory=list)
    default_profile_id: str | None = None


class LlmModelInfo(BaseModel):
    model_id: str
    owned_by: str | None = None


class LlmModelListResponse(BaseModel):
    profile_id: str
    models: list[LlmModelInfo] = Field(default_factory=list)


class LlmConnectionTestResponse(BaseModel):
    profile_id: str
    status: Literal["connected"] = "connected"
    models_count: int | None = None
    selected_model_available: bool | None = None
    tested_model_id: str | None = None
    response_verified: bool = False
    billing_effect: Literal["none", "minimal"] = "none"
    message: str


class CodexCliStatusResponse(BaseModel):
    installed: bool
    executable_path: str | None = None
    version: str | None = None
    supports_image_input: bool = False
    logged_in: bool = False
    auth_type: Literal["chatgpt", "api_key", "access_token", "unknown"] | None = None
    subscription_usable: bool = False
    operation: Literal["idle", "running", "succeeded", "failed"] = "idle"
    operation_message: str | None = None
    operation_error: str | None = None


class CodexCliAuthActionResponse(BaseModel):
    status: Literal["started", "logged_out"]
    message: str


class WebSearchSettings(BaseModel):
    enabled: bool = False
    provider: Literal["wikipedia", "tavily", "searxng"] = "wikipedia"
    base_url: str = ""
    api_key_configured: bool = False
    max_queries: int = Field(
        default=3,
        ge=1,
        le=3,
        deprecated=True,
        description="兼容旧客户端的停用字段；查询预算由具体工作流决定。",
    )
    max_results_per_query: int = Field(default=5, ge=1, le=8)

    @field_validator("base_url")
    @classmethod
    def strip_search_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")


class WebSearchSettingsUpdate(BaseModel):
    enabled: bool = False
    provider: Literal["wikipedia", "tavily", "searxng"] = "wikipedia"
    base_url: str = ""
    api_key: str | None = None
    clear_api_key: bool = False
    max_queries: int = Field(
        default=3,
        ge=1,
        le=3,
        deprecated=True,
        description="兼容旧客户端的停用字段；提交值不会参与流程限制。",
    )
    max_results_per_query: int = Field(default=5, ge=1, le=8)

    @field_validator("base_url")
    @classmethod
    def strip_search_update_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            return ""
        parsed = urllib.parse.urlparse(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("SearXNG 地址必须是没有账号、密码、查询参数或片段的 HTTP(S) 地址")
        return normalized

    @model_validator(mode="after")
    def reject_search_key_replace_and_clear(self):
        if self.clear_api_key and self.api_key is not None and self.api_key.strip():
            raise ValueError("api_key 与 clear_api_key 不能同时提交")
        return self


class WebSearchTestResponse(BaseModel):
    provider: Literal["wikipedia", "tavily", "searxng"]
    status: Literal["connected"] = "connected"
    result_count: int = 0
    message: str


class CloudConnectionTestResponse(BaseModel):
    provider: Literal["mimo", "doubao", "volcengine_directory"]
    status: Literal["connected"] = "connected"
    message: str
    verified_scopes: list[str] = Field(default_factory=list)
    billing_effect: Literal["none", "minimal"] = "none"
    models_count: int | None = None
    request_id: str | None = None
    logid: str | None = None


class MimoSecretUpdate(BaseModel):
    api_key: str | None = None
    clear: bool = False

    @model_validator(mode="after")
    def reject_replace_and_clear(self):
        if self.clear and self.api_key is not None and self.api_key.strip():
            raise ValueError("api_key 与 clear 不能同时提交")
        return self


class DoubaoSecretUpdate(BaseModel):
    api_key: str | None = None
    clear: bool = False

    @model_validator(mode="after")
    def reject_replace_and_clear(self):
        if self.clear and self.api_key is not None and self.api_key.strip():
            raise ValueError("api_key 与 clear 不能同时提交")
        return self


class VolcengineDirectorySecretUpdate(BaseModel):
    access_key_id: str | None = None
    secret_access_key: str | None = None
    clear_access_key_id: bool = False
    clear_secret_access_key: bool = False

    @model_validator(mode="after")
    def reject_replace_and_clear(self):
        if self.clear_access_key_id and self.access_key_id is not None and self.access_key_id.strip():
            raise ValueError("access_key_id 与 clear_access_key_id 不能同时提交")
        if (
            self.clear_secret_access_key
            and self.secret_access_key is not None
            and self.secret_access_key.strip()
        ):
            raise ValueError("secret_access_key 与 clear_secret_access_key 不能同时提交")
        return self


class DoubaoVoiceCloneTrainRequest(BaseModel):
    custom_speaker_id: str | None = None
    speaker_id: str | None = None
    demo_text: str | None = None
    language: str = "zh"
    enable_audio_denoise: bool = True
    disable_volume_normalization: bool = False
    confirm_upload: bool = False

    @field_validator("language")
    @classmethod
    def normalize_doubao_clone_language(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"zh", "zh-cn", "cn", "chinese", "中文"}:
            return "zh"
        if normalized in {"en", "en-us", "english", "英文"}:
            return "en"
        raise ValueError("豆包声音复刻 2.0 训练音频只支持中文或英文")


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
    external_provider: str | None = None
    external_voice_id: str | None = None
    external_status: str | None = None
    external_metadata: dict[str, Any] = Field(default_factory=dict)


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
    external_provider: str | None = None
    external_voice_id: str | None = None
    external_status: str | None = None
    external_metadata: dict[str, Any] | None = None


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


class DoubaoVoiceCloneResponse(BaseModel):
    voice: VoiceAsset
    summary: dict[str, Any] = Field(default_factory=dict)


class VoiceFile(BaseModel):
    file_id: str = Field(default_factory=new_id)
    original_name: str
    path: str
    mime_type: str = "audio/wav"
    duration_ms: int | None = None
    sample_rate: int | None = None
    size_bytes: int = 0
    # For a video import, ``path`` is always the extracted audio used by TTS.
    # The managed source video is retained only so the pair can move and expire
    # together.
    source_media_path: str | None = None
    source_media_name: str | None = None
    created_at: str = Field(default_factory=now_iso)


class TranscriptionSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    language: str | None = None
    speaker_id: str | None = None
    confidence: float | None = None


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


class VoiceClipTranscribeRequest(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    language: Literal["auto", "zh", "en"] = "auto"
    engine_id: str = "qwen3-asr-mlx"


class VoiceClipRequest(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class VoiceClipResponse(BaseModel):
    file_id: str
    filename: str
    path: str
    quality: dict[str, Any] = Field(default_factory=dict)
    voice_file: VoiceFile


class VoiceClipTranscribeResponse(BaseModel):
    file_id: str
    filename: str
    path: str
    quality: dict[str, Any] = Field(default_factory=dict)
    voice_file: VoiceFile
    transcription: TranscriptionRecord


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


class EngineInputAsset(BaseModel):
    """Managed input reference used by model-specific request adapters.

    Raw local paths and Base64 are intentionally not part of this public
    envelope. A later resolver must turn stable file IDs into validated paths.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_id: str
    type: Literal["audio", "image", "speaker"]
    source: Literal["voice_library", "upload", "cloud_speaker", "preset"]
    file_id: str | None = None
    voice_id: str | None = None
    speaker_id: str | None = None
    display_name: str | None = None
    ref_text: str | None = None
    source_file_id: str | None = None
    clip_file_id: str | None = None
    trim_start_ms: int | None = Field(default=None, ge=0)
    trim_end_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    license_status: LicenseStatus | None = None

    @model_validator(mode="after")
    def validate_managed_identifier(self):
        if self.type == "speaker" and not self.speaker_id:
            raise PydanticCustomError("speaker_id_required", "speaker 类型素材必须提供 speaker_id")
        if self.type in {"audio", "image"} and not any((self.file_id, self.source_file_id, self.clip_file_id)):
            raise PydanticCustomError("file_id_required", "音频和图片素材必须提供受管理的 file_id")
        if self.trim_start_ms is not None and self.trim_end_ms is not None and self.trim_end_ms < self.trim_start_ms:
            raise PydanticCustomError("invalid_trim_range", "trim_end_ms must be greater than or equal to trim_start_ms")
        return self


class GenerateRequest(BaseModel):
    text: str
    engine_id: str = "indextts-v2"
    resource_priority: Literal[
        "foreground_resume",
        "normal",
        "background",
    ] = Field(
        default="normal",
        description="普通 TTS 单 worker 的非抢占排队优先级，不代表跨进程或全机 GPU 配额。",
    )
    source: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    localized_subtitle_id: str | None = None
    cue_id: str | None = None
    timeline_clip_id: str | None = None
    generation_id: str | None = None
    bind_to_video_localization: bool = False
    video_localization_workflow_id: str | None = None
    video_localization_submission_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{12}$",
    )
    video_localization_start_ms: int | None = Field(default=None, ge=0)
    video_localization_end_ms: int | None = Field(default=None, ge=0)
    video_localization_target_subtitle_ids: list[str] = Field(default_factory=list)
    video_localization_source_cue_ids: list[str] = Field(default_factory=list)
    video_localization_dubbing_plan_revision: int | None = Field(
        default=None,
        ge=1,
    )
    video_localization_dubbing_group_id: str | None = None
    # The dubbing executor owns continuation.  These fields travel with the
    # durable generation task so a completed group can resume the same run
    # without relying on an open browser tab or an external script.
    video_localization_execution_scope: Literal[
        "single_group",
        "all_remaining",
    ] | None = None
    video_localization_recovery: DubbingRecoveryDecision | None = None
    video_localization_preflight: DubbingGroupPreflightResult | None = None
    video_localization_generation_attempt: int | None = Field(
        default=None,
        ge=1,
        description="Monotonic generation ordinal within the current plan/group; automatic retry limits belong to the executor.",
    )
    video_localization_dubbing_review_mode: Literal[
        "full",
        "risk_based",
        "supervised",
    ] | None = None
    video_localization_execution_start_group_id: str | None = None
    video_localization_execution_end_group_id: str | None = None
    video_localization_max_in_flight_groups: int | None = Field(
        default=None,
        ge=1,
        le=3,
    )
    video_localization_ordinary_speed_baseline: float | None = Field(
        default=None,
        ge=0.5,
        le=2.0,
    )
    input_mode: Literal["text", "audio", "image"] | None = None
    input_assets: list[EngineInputAsset] = Field(default_factory=list)
    engine_parameters: dict[str, Any] = Field(default_factory=dict)
    voice_id: str | None = None
    reference_audio_path: str | None = None
    voice_source: VoiceSource | None = None
    reference_audio_license_status: LicenseStatus | None = None
    reference_audio_tags: list[str] = Field(default_factory=list)
    ref_text: str | None = None
    custom_reference_source_audio_path: str | None = None
    custom_reference_source_duration_ms: int | None = None
    custom_reference_trim_start_ms: int | None = None
    custom_reference_trim_end_ms: int | None = None
    language: str = "zh"
    emotion_mode: EmotionMode = EmotionMode.follow_reference
    emotion: str | None = None
    emotion_values: dict[str, float] | None = None
    emotion_reference_voice_id: str | None = None
    emotion_reference_audio_path: str | None = None
    emotion_reference_source_audio_path: str | None = None
    emotion_reference_source_duration_ms: int | None = Field(default=None, ge=0)
    emotion_reference_trim_start_ms: int | None = Field(default=None, ge=0)
    emotion_reference_trim_end_ms: int | None = Field(default=None, gt=0)
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
    # The F5 UI exposes 0.01–0.5 as the tested safe range.  Keep the API
    # contract identical so batch/API callers cannot request an unchecked
    # louder value that the visible product does not offer.
    target_rms: float = Field(default=0.1, ge=0.01, le=0.5)
    cross_fade_duration: float = Field(default=0.15, ge=0.0, le=1.0)
    sway_sampling_coef: float = Field(default=-1.0, ge=-1.0, le=1.0)
    # F5 currently supports a 30 s reference+generation total per inference
    # chunk.  The long-text wrapper runs this per chunk, not as a whole-file
    # duration target, so accepting 600 s here was a false promise.
    fix_duration: float = Field(default=0.0, ge=0.0, le=30.0)
    remove_silence: bool = False
    emo_alpha: float = Field(default=0.6, ge=0, le=1)
    # Every visible engine control and the IndexTTS/MLX runtime currently
    # share this safe range.  Do not accept 2.01–3.0 here: the UI cannot set
    # it and IndexTTS would only fail after the task had already queued.
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    content_speed_exception_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    content_speed_exception_evidence_ids: list[str] = Field(default_factory=list)
    pitch_rate: int | None = Field(default=None, ge=-12, le=12)
    sample_rate: Literal[8000, 16000, 22050, 24000, 32000, 44100, 48000] | None = None
    bit_rate: int | None = Field(default=None, ge=64000, le=160000)
    loudness_rate: int | None = Field(default=None, ge=-50, le=100)
    enable_subtitle: bool = False
    silence_duration: int = Field(default=0, ge=0, le=30000)
    aigc_watermark: bool = False
    temperature: float = Field(default=0.8, ge=0.1, le=2.0)
    top_p: float = Field(default=0.8, ge=0.0, le=1.0)
    # Qwen3-TTS MLX supports up to 200 candidates. Other engines expose their
    # narrower safe ranges in their own manifests, so the shared API must not
    # reject a valid Qwen setting from the visible control.
    top_k: int = Field(default=30, ge=1, le=200)
    repetition_penalty: float = Field(default=10.0, ge=1.0, le=20.0)
    seed: int | None = None
    # Keep the API default aligned with the IndexTTS manifest and generate-page
    # reset value. A lower API-only default silently shortened direct API jobs.
    # IndexTTS v2's shipped position embedding table is sized for 1815 mel
    # tokens.  A larger API-only value is not a usable option; it can fail in
    # inference after a user has waited for the job to start.
    max_mel_tokens: int = Field(default=1500, ge=100, le=1815)
    max_text_tokens_per_segment: int = Field(default=120, ge=10, le=500)
    interval_silence: int = Field(default=200, ge=0, le=2000)
    segment_overlap_ms: int = Field(default=50, ge=0, le=500)
    diffusion_steps: int = Field(default=25, ge=1, le=100)
    cfg_rate: float = Field(default=0.7, ge=0.0, le=1.0)
    guidance_scale: float = Field(default=2.0, ge=0.0, le=10.0)
    duration: float = Field(default=0.0, ge=0.0, le=600.0)
    audio_chunk_duration: float = Field(default=15.0, ge=1.0, le=120.0)
    audio_chunk_threshold: float = Field(default=30.0, ge=1.0, le=600.0)
    max_tokens: int = Field(default=1200, ge=100, le=4096)
    cfg_scale: float | None = Field(default=None, ge=0.0, le=20.0)
    ddpm_steps: int | None = Field(default=None, ge=1, le=200)
    output_format: Literal["wav", "mp3", "flac", "pcm", "ogg_opus"] = "wav"

    @model_validator(mode="after")
    def validate_mimo_output_format(self):
        if self.output_format in {"pcm", "ogg_opus"} and self.engine_id not in {"doubao-tts-preset", "doubao-tts-voiceclone"}:
            raise PydanticCustomError(
                "provider_output_format_unsupported",
                "PCM 和 OGG Opus 目前只由豆包 TTS 2.0 直出；其他引擎请选 WAV、MP3 或 FLAC",
            )
        if self.engine_id in {"mimo-v2.5-tts", "mimo-v2.5-tts-preset", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"} and self.output_format != "wav":
            # BatchGenerateRequest historically defaults to MP3 for local
            # engines.  A caller that omitted this generic field should get
            # MiMo's verified WAV default, while an explicit MP3/FLAC request
            # must still be rejected instead of creating a mislabeled file.
            if "output_format" not in self.model_fields_set:
                self.output_format = "wav"
            else:
                raise PydanticCustomError("mimo_output_format_unsupported", "MiMo TTS 当前仅支持 WAV 输出")
        has_speed_exception_reason = bool(
            (self.content_speed_exception_reason or "").strip()
        )
        has_speed_exception_evidence = bool(
            self.content_speed_exception_evidence_ids
        )
        if has_speed_exception_reason != has_speed_exception_evidence:
            raise PydanticCustomError(
                "content_speed_exception_incomplete",
                "内容倍速例外必须同时提供原因和证据 ID",
            )
        return self


class GenerateResponse(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.queued
    video_localization_workflow_id: str | None = None


class GeneratePlanRequest(BaseModel):
    text: str
    engine_id: str = "indextts-v2"
    planner_mode: Literal["auto", "rules", "llm"] = "auto"
    target_format: Literal["wav", "mp3", "flac", "pcm", "ogg_opus"] = "mp3"


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

    @model_validator(mode="after")
    def reject_single_only_engines(self):
        if self.generate_request.engine_id == "doubao-seed-audio-1.0":
            raise PydanticCustomError("single_generation_only", "Seed Audio 1.0 暂只支持单次生成")
        if self.generate_request.output_format in {"pcm", "ogg_opus"}:
            raise PydanticCustomError("longform_output_format_unsupported", "长文本合并暂不支持 PCM 或 OGG Opus；请使用 WAV、MP3 或 FLAC")
        return self


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
    voice_source: VoiceSource | None = None
    reference_audio_license_status: LicenseStatus | None = None
    reference_audio_tags: list[str] = Field(default_factory=list)
    ref_text: str | None = None
    language: str | None = None
    emotion: str | None = None
    emotion_text: str | None = None
    style_instruction: str | None = None
    voice_design_prompt: str | None = None
    mimo_voice: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=2.0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class BatchGenerateRequest(BaseModel):
    project_name: str = "批量语音项目"
    engine_id: str = "indextts-v2"
    voice_id: str | None = None
    reference_audio_path: str | None = None
    voice_source: VoiceSource | None = None
    reference_audio_license_status: LicenseStatus | None = None
    reference_audio_tags: list[str] = Field(default_factory=list)
    ref_text: str | None = None
    language: str = "zh"
    output_dir: str | None = None
    output_format: Literal["wav", "mp3", "flac", "pcm", "ogg_opus"] = "mp3"
    partial_success: bool = False
    segments: list[BatchSegmentInput]
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_single_only_engines(self):
        if self.engine_id == "doubao-seed-audio-1.0" or any(
            segment.engine_id == "doubao-seed-audio-1.0" for segment in self.segments
        ):
            raise PydanticCustomError("single_generation_only", "Seed Audio 1.0 暂只支持单次生成")
        if self.output_format in {"pcm", "ogg_opus"} and self.engine_id not in {"doubao-tts-preset", "doubao-tts-voiceclone"}:
            raise PydanticCustomError(
                "provider_output_format_unsupported",
                "PCM 和 OGG Opus 目前只由豆包 TTS 2.0 直出；其他引擎请选 WAV、MP3 或 FLAC",
            )
        if self.engine_id in {"mimo-v2.5-tts", "mimo-v2.5-tts-preset", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"} and self.output_format != "wav":
            # BatchGenerateRequest defaults to MP3 for the legacy local-model
            # flow.  If no format was supplied, normalize MiMo to its verified
            # WAV default; explicit MP3/FLAC requests remain a clear error.
            if "output_format" not in self.model_fields_set:
                self.output_format = "wav"
            else:
                raise PydanticCustomError("mimo_output_format_unsupported", "MiMo TTS 当前仅支持 WAV 输出")
        return self


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
    provider_attempt_id: str | None = None
    provider_request_id: str | None = None
    provider_log_id: str | None = None
    provider_state_uncertain: bool = False


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
    provider_state_uncertain: bool = False
    segments: list[BatchSegmentResult] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class GenerationTask(BaseModel):
    task_id: str = Field(default_factory=new_id)
    generation_id: str | None = None
    task_type: Literal["single", "segment", "batch", "export"] = "single"
    engine_id: str
    voice_id: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    localized_subtitle_id: str | None = None
    cue_id: str | None = None
    bind_to_video_localization: bool = False
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
    provider_request_id: str | None = None
    provider_log_id: str | None = None
    provider_state_uncertain: bool = False
    original_duration_ms: int | None = None
    subtitle: dict[str, Any] | None = None
    response_source: str | None = None
    verification: TTSVerificationResponse | None = None
    verification_error: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    artifacts_removed_at: str | None = None


class HistoryItem(BaseModel):
    result_id: str = Field(default_factory=new_id)
    task_id: str
    generation_id: str | None = None
    engine_id: str
    voice_id: str | None = None
    voice_name: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    localized_subtitle_id: str | None = None
    cue_id: str | None = None
    bind_to_video_localization: bool = False
    longform_task_id: str | None = None
    longform_segment_index: int | None = None
    longform_segment_count: int | None = None
    longform_export_id: str | None = None
    input_text: str
    output_audio_id: str | None = None
    output_path: str | None = None
    duration_ms: int | None = None
    generation_time_ms: int | None = None
    provider_request_id: str | None = None
    provider_log_id: str | None = None
    original_duration_ms: int | None = None
    subtitle: dict[str, Any] | None = None
    response_source: str | None = None
    verification: TTSVerificationResponse | None = None
    verification_error: str | None = None
    content_evidence: TtsContentEvidence | None = None
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


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_engine_id: str | None = None


class ProjectTranscriptionImportRequest(BaseModel):
    transcription_ids: list[str] = Field(default_factory=list)
    mode: Literal["append", "replace"] = "append"
    role_id: str | None = None
    default_engine_id: str | None = None
    default_voice_id: str | None = None


class Project(BaseModel):
    _repository_revision: int = PrivateAttr(default=0)

    project_id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    default_engine_id: str | None = "indextts-v2"
    parameters: dict[str, Any] = Field(default_factory=dict)
    roles: list[Role] = Field(default_factory=list)
    segments: list[ScriptSegment] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ProjectSummary(BaseModel):
    project_id: str
    name: str
    description: str = ""
    kind: Literal["script", "video_localization"]
    has_source_media: bool = False
    source_media_configured: bool = False
    source_media_status: Literal[
        "unknown",
        "unconfigured",
        "available",
        "missing",
        "not_file",
        "unreadable",
    ] = "unknown"
    has_local_package: bool = False
    package_status: Literal[
        "unknown",
        "available",
        "missing",
        "invalid",
        "repair_required",
    ] = "unknown"
    created_at: str
    updated_at: str


class ProjectTranscriptionImportResponse(BaseModel):
    project: Project
    imported_count: int = 0
    skipped_count: int = 0


class VideoLocalizationExtensibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class VideoLocalizationSourceMedia(VideoLocalizationExtensibleModel):
    filename: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    video_path: str | None = None
    audio_path: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    frame_rate: float | None = Field(default=None, ge=0)
    imported_at: str | None = None
    content_sha256: str | None = None
    audio_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoLocalizationStems(VideoLocalizationExtensibleModel):
    vocals_clean_path: str | None = None
    background_path: str | None = None
    original_audio_path: str | None = None
    separation_engine_id: str | None = None
    separation_status: Literal["pending", "running", "completed", "failed", "cancelled", "skipped"] = "pending"
    quality_flags: list[str] = Field(default_factory=list)
    original_audio_sha256: str | None = None
    vocals_clean_sha256: str | None = None
    background_sha256: str | None = None


class VideoLocalizationTimeRange(VideoLocalizationExtensibleModel):
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    source: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        return self


class VideoLocalizationAsrIncompleteRange(VideoLocalizationExtensibleModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError(
                "invalid_time_range",
                "end_ms must be greater than start_ms",
            )
        return self


class VideoLocalizationSpeaker(VideoLocalizationExtensibleModel):
    speaker_id: str
    display_name: str | None = None
    acoustic_cluster_ids: list[str] = Field(default_factory=list)
    identity_candidates: list["VideoLocalizationSpeakerIdentityCandidate"] = Field(default_factory=list)
    route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] = "manual_review"
    reference_clip_ids: list[str] = Field(default_factory=list)
    time_ranges: list[VideoLocalizationTimeRange] = Field(default_factory=list)
    review_status: Literal["needs_review", "ready", "blocked", "locked"] = "needs_review"
    notes: str | None = None


class VideoLocalizationSpeakerIdentityCandidate(VideoLocalizationExtensibleModel):
    name: str
    confidence: float = Field(ge=0, le=1)
    status: Literal["candidate", "confirmed", "rejected"] = "candidate"
    reason: str = ""
    evidence_source_ids: list[str] = Field(default_factory=list)


class VideoLocalizationSpeakerCluster(VideoLocalizationExtensibleModel):
    cluster_id: str
    source_label: str
    source_engine_id: str
    business_speaker_id: str | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    segment_count: int = Field(ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    merge_status: Literal["original", "auto_merged", "needs_review"] = "original"
    merged_source_labels: list[str] = Field(default_factory=list)
    time_ranges: list[VideoLocalizationTimeRange] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        if self.duration_ms != self.end_ms - self.start_ms:
            raise PydanticCustomError("invalid_duration", "duration_ms must match end_ms - start_ms")
        return self


class VideoLocalizationSpeakerCreate(BaseModel):
    speaker_id: str | None = None
    display_name: str | None = None
    route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] = "manual_review"
    review_status: Literal["needs_review", "ready", "blocked", "locked"] = "needs_review"
    notes: str | None = None


class VideoLocalizationSpeakerUpdate(BaseModel):
    display_name: str | None = None
    route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] | None = None
    review_status: Literal["needs_review", "ready", "blocked", "locked"] | None = None
    notes: str | None = None


class VideoLocalizationReferenceClip(VideoLocalizationExtensibleModel):
    reference_clip_id: str
    speaker_id: str | None = None
    source_stem: Literal["vocals_clean", "original_audio", "uploaded_reference", "generated_tts"] = "vocals_clean"
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    audio_path: str | None = None
    cleanliness: Literal["clean", "needs_review", "blocked", "mixed", "unknown"] = "unknown"
    asr_text: str | None = None
    asr_status: Literal["pending", "candidate", "verified", "failed", "skipped"] = "pending"
    license_status: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        return self


class VideoLocalizationTranscriptSegment(VideoLocalizationExtensibleModel):
    segment_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    raw_text: str
    speaker_cluster_id: str | None = None
    speaker_confidence: float | None = Field(default=None, ge=0, le=1)
    has_speaker_overlap: bool = False
    corrected_text: str | None = None
    review_candidate_text: str | None = None
    review_rejection_reason: str | None = None
    review_confidence: float | None = Field(default=None, ge=0, le=1)
    review_flags: list[str] = Field(default_factory=list)
    review_operations: list["VideoLocalizationTranscriptEditOperation"] = Field(default_factory=list)
    unconfirmed_text_spans: list[AsrUnconfirmedTextSpan] | None = None

    @model_serializer(mode="wrap")
    def _preserve_legacy_uncertainty_serialization(self, serialize):
        result = serialize(self)
        if self.unconfirmed_text_spans is None:
            result.pop("unconfirmed_text_spans", None)
        return result

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        return self


class VideoLocalizationTranscriptEditOperation(VideoLocalizationExtensibleModel):
    start_word_id: str
    end_word_id: str
    source_text: str
    replacement_text: str
    reason: str = ""
    confidence: float = Field(ge=0, le=1)
    status: Literal["accepted", "rejected"] = "rejected"
    rejection_reason: str | None = None
    evidence_source_ids: list[str] = Field(default_factory=list)


class VideoLocalizationResearchQuery(VideoLocalizationExtensibleModel):
    query_id: str
    query: str
    category: Literal["proper_noun", "background", "culture", "persona"] = "background"
    reason: str = ""
    target_terms: list[str] = Field(default_factory=list)


class VideoLocalizationResearchSource(VideoLocalizationExtensibleModel):
    source_id: str
    query_id: str
    title: str
    url: str
    snippet: str = ""
    provider: str
    retrieved_at: str = Field(default_factory=now_iso)


class VideoLocalizationResearchState(VideoLocalizationExtensibleModel):
    status: Literal["disabled", "not_configured", "not_needed", "completed", "partial", "failed"] = "disabled"
    prompt_version: str | None = None
    profile_id: str | None = None
    model_id: str | None = None
    provider: str | None = None
    reason: str = ""
    queries: list[VideoLocalizationResearchQuery] = Field(default_factory=list)
    sources: list[VideoLocalizationResearchSource] = Field(default_factory=list)
    cache_hits: int = 0
    duration_ms: int = 0
    error: str | None = None


AlignedWordTimingSource: TypeAlias = Literal[
    "forced_aligner",
    "asr_segment_interpolation",
    "asr_vad_verified",
]


class VideoLocalizationAlignedWord(VideoLocalizationExtensibleModel):
    word_id: str
    segment_id: str
    text: str
    speaker_cluster_id: str | None = None
    speaker_confidence: float | None = Field(default=None, ge=0, le=1)
    has_speaker_overlap: bool = False
    acoustic_support_start_ms: int | None = Field(default=None, ge=0)
    acoustic_support_end_ms: int | None = Field(default=None, ge=0)
    acoustic_support_source: Literal["speaker_diarization"] | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    timing_confidence: Literal["high", "medium", "low"] = "low"
    timing_source: AlignedWordTimingSource = "asr_segment_interpolation"

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        support_values = (
            self.acoustic_support_start_ms,
            self.acoustic_support_end_ms,
            self.acoustic_support_source,
        )
        if any(value is not None for value in support_values) and not all(
            value is not None for value in support_values
        ):
            raise PydanticCustomError(
                "invalid_acoustic_support",
                "acoustic support fields must be provided together",
            )
        if (
            self.acoustic_support_start_ms is not None
            and self.acoustic_support_end_ms is not None
            and (
                self.acoustic_support_start_ms < self.start_ms
                or self.acoustic_support_end_ms > self.end_ms
                or self.acoustic_support_end_ms <= self.acoustic_support_start_ms
            )
        ):
            raise PydanticCustomError(
                "invalid_acoustic_support_range",
                "acoustic support must be a non-empty range inside raw timing",
            )
        return self


class VideoLocalizationAudioBoundaryEvidence(VideoLocalizationExtensibleModel):
    boundary_id: str
    left_word_id: str
    right_word_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    gap_ms: int = Field(ge=0)
    low_energy_ms: int = Field(ge=0)
    low_energy_ratio: float = Field(ge=0, le=1)
    gap_rms_dbfs: float
    speech_reference_dbfs: float
    noise_floor_dbfs: float
    energy_drop_db: float
    confidence: Literal["none", "low", "medium", "high"] = "none"
    analysis_version: str = "energy-pause-v1"

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        if self.gap_ms != self.end_ms - self.start_ms:
            raise PydanticCustomError("invalid_gap_duration", "gap_ms must match end_ms - start_ms")
        if self.low_energy_ms > self.gap_ms:
            raise PydanticCustomError("invalid_low_energy_duration", "low_energy_ms must not exceed gap_ms")
        if self.boundary_id != f"{self.left_word_id}:{self.right_word_id}":
            raise PydanticCustomError("invalid_boundary_id", "boundary_id must match the adjacent word IDs")
        db_values = (
            self.gap_rms_dbfs,
            self.speech_reference_dbfs,
            self.noise_floor_dbfs,
            self.energy_drop_db,
        )
        if not all(math.isfinite(value) for value in db_values):
            raise PydanticCustomError("invalid_audio_boundary_db", "audio boundary dB values must be finite")
        return self


class VideoLocalizationAsrVadInterval(VideoLocalizationExtensibleModel):
    """Raw VAD interval retained with a bounded source-timing correction."""

    interval_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError(
                "invalid_vad_interval", "VAD interval end_ms must be greater than start_ms"
            )
        return self


class VideoLocalizationSourceTimingIssue(VideoLocalizationExtensibleModel):
    """Small, user-visible trace for a verified source-timing correction."""

    code: str = Field(min_length=1, max_length=128)
    result: str = Field(min_length=1, max_length=1_000)
    word_ids: list[str] = Field(default_factory=list)


class VideoLocalizationSupersededAcousticSupport(VideoLocalizationExtensibleModel):
    """Old diarization span retained after a newer word-timing measurement."""

    word_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    source: Literal["speaker_diarization"] = "speaker_diarization"

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError(
                "invalid_superseded_acoustic_support",
                "historical acoustic support must have a non-empty range",
            )
        return self


class VideoLocalizationSourceTimingWord(VideoLocalizationExtensibleModel):
    """The exact immutable token and timing accepted in a source correction."""

    word_id: str = Field(min_length=1)
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms < self.start_ms:
            raise PydanticCustomError(
                "invalid_source_timing_word_range",
                "source-timing word end_ms must be greater than or equal to start_ms",
            )
        return self


class VideoLocalizationAsrVadSourceTimingCorrection(VideoLocalizationExtensibleModel):
    """Auditable source-word timing update from one separated-vocals ASR/VAD pass."""

    correction_id: str = Field(default_factory=new_id)
    contract_version: Literal["asr-vad-source-timing-correction-v1"] = (
        "asr-vad-source-timing-correction-v1"
    )
    transcription_revision_id: str = Field(min_length=1)
    source_track_id: Literal["vocals"] = "vocals"
    audio_sha256: str = Field(min_length=64, max_length=64)
    analysis_protocol: str = Field(min_length=1, max_length=128)
    analysis_start_ms: int = Field(ge=0)
    analysis_end_ms: int = Field(ge=0)
    word_ids: list[str] = Field(min_length=1)
    word_timings: list[VideoLocalizationSourceTimingWord] = Field(min_length=1)
    vad_intervals: list[VideoLocalizationAsrVadInterval] = Field(default_factory=list)
    issues: list[VideoLocalizationSourceTimingIssue] = Field(default_factory=list)
    superseded_acoustic_support: list[VideoLocalizationSupersededAcousticSupport] = Field(
        default_factory=list
    )
    applied_at: str = Field(default_factory=now_iso)

    @model_validator(mode="after")
    def validate_correction(self):
        if self.analysis_end_ms <= self.analysis_start_ms:
            raise PydanticCustomError(
                "invalid_source_timing_analysis_range",
                "source-timing analysis range must be non-empty",
            )
        if len(self.word_ids) != len(set(self.word_ids)):
            raise PydanticCustomError(
                "duplicate_source_timing_word", "source-timing word references must be unique"
            )
        if self.word_ids != [item.word_id for item in self.word_timings]:
            raise PydanticCustomError(
                "source_timing_word_snapshot_mismatch",
                "source-timing correction words must match its word references",
            )
        if len(self.vad_intervals) != len({item.interval_id for item in self.vad_intervals}):
            raise PydanticCustomError(
                "duplicate_source_timing_vad_interval", "source-timing VAD interval references must be unique"
            )
        if any(
            item.start_ms < self.analysis_start_ms or item.end_ms > self.analysis_end_ms
            for item in self.vad_intervals
        ):
            raise PydanticCustomError(
                "source_timing_vad_outside_analysis_range",
                "source-timing VAD interval must stay inside its analysis range",
            )
        return self


class VideoLocalizationBoundaryReview(VideoLocalizationExtensibleModel):
    boundary_id: str
    left_word_id: str
    right_word_id: str
    decision: Literal["prefer", "allow", "avoid"]
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    prompt_version: str = "boundary-review-v1"
    model_id: str | None = None

    @model_validator(mode="after")
    def validate_boundary_id(self):
        if self.boundary_id != f"{self.left_word_id}:{self.right_word_id}":
            raise PydanticCustomError("invalid_boundary_id", "boundary_id must match the adjacent word IDs")
        return self


class VideoLocalizationTranscriptionState(VideoLocalizationExtensibleModel):
    revision_id: str = Field(default_factory=new_id)
    language: str = "en"
    source_track_id: str | None = None
    source_audio_sha256: str | None = None
    alignment_source_track_id: str | None = None
    alignment_audio_sha256: str | None = None
    engine_id: str | None = None
    raw_text: str = ""
    raw_asr_warning_codes: list[str] = Field(default_factory=list)
    asr_source_repairs: list[AsrSourceRepairReceipt] = Field(default_factory=list)
    raw_asr_incomplete_ranges: list[VideoLocalizationAsrIncompleteRange] = Field(
        default_factory=list
    )
    corrected_text: str = ""
    segments: list[VideoLocalizationTranscriptSegment] = Field(default_factory=list)
    words: list[VideoLocalizationAlignedWord] = Field(default_factory=list)
    diarization_status: Literal["not_run", "completed", "partial", "failed", "skipped"] = "not_run"
    diarization_engine_id: str | None = None
    diarization_model_id: str | None = None
    diarization_error: str | None = None
    speaker_clusters: list[VideoLocalizationSpeakerCluster] = Field(default_factory=list)
    review_status: Literal["not_configured", "skipped", "completed", "partial", "failed"] = "not_configured"
    review_profile_id: str | None = None
    review_model_id: str | None = None
    review_prompt_version: str | None = None
    review_error: str | None = None
    transcript_quality_cycle: dict[str, Any] = Field(default_factory=dict)
    research: VideoLocalizationResearchState = Field(default_factory=VideoLocalizationResearchState)
    alignment_status: Literal["not_run", "completed", "partial", "failed"] = "not_run"
    alignment_engine_id: str | None = None
    alignment_error: str | None = None
    timing_confidence: Literal["high", "medium", "low"] = "low"
    audio_boundary_status: Literal["not_run", "completed", "failed", "skipped"] = "not_run"
    audio_boundary_analysis_version: str | None = None
    audio_boundary_error: str | None = None
    audio_boundary_features: list[VideoLocalizationAudioBoundaryEvidence] = Field(default_factory=list)
    asr_vad_source_timing_corrections: list[
        VideoLocalizationAsrVadSourceTimingCorrection
    ] = Field(default_factory=list)
    boundary_review_status: Literal["not_configured", "skipped", "completed", "partial", "failed"] = "not_configured"
    boundary_review_profile_id: str | None = None
    boundary_review_model_id: str | None = None
    boundary_review_prompt_version: str | None = None
    boundary_review_error: str | None = None
    boundary_reviews: list[VideoLocalizationBoundaryReview] = Field(default_factory=list)
    subtitle_entry_by_word_id: dict[str, int] = Field(default_factory=dict)
    segmentation_profile_id: Literal["generic_zh", "short_video_large_text", "conservative_release"] = "generic_zh"
    quality_flags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_speech_onsets(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        legacy = migrated.pop("speech_onset_by_word_id", None)
        if (
            "subtitle_entry_by_word_id" not in migrated
            and isinstance(legacy, dict)
        ):
            migrated["subtitle_entry_by_word_id"] = legacy
        return migrated


class VideoLocalizationAutomaticTimingConfirmationEvidence(VideoLocalizationExtensibleModel):
    """ASR/VAD evidence that can confirm one current source-cue timing."""

    contract_version: Literal["asr-vad-cue-timing-confirmation-v1"] = (
        "asr-vad-cue-timing-confirmation-v1"
    )
    transcription_revision_id: str = Field(min_length=1)
    alignment_source_track_id: str = Field(min_length=1)
    alignment_audio_sha256: str = Field(min_length=64, max_length=64)
    source_word_ids: list[str] = Field(min_length=1)
    vad_boundary_ids: list[str] = Field(default_factory=list)
    source_timing_correction_id: str | None = None
    vad_interval_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self):
        if len(self.source_word_ids) != len(set(self.source_word_ids)):
            raise PydanticCustomError(
                "duplicate_timing_confirmation_word",
                "automatic timing confirmation word references must be unique",
            )
        if len(self.vad_boundary_ids) != len(set(self.vad_boundary_ids)):
            raise PydanticCustomError(
                "duplicate_timing_confirmation_boundary",
                "automatic timing confirmation VAD references must be unique",
            )
        if len(self.vad_interval_ids) != len(set(self.vad_interval_ids)):
            raise PydanticCustomError(
                "duplicate_timing_confirmation_vad_interval",
                "automatic timing confirmation VAD interval references must be unique",
            )
        if not self.vad_boundary_ids and not self.source_timing_correction_id:
            raise PydanticCustomError(
                "missing_timing_confirmation_vad_evidence",
                "automatic timing confirmation requires current VAD references",
            )
        return self


class VideoLocalizationCue(VideoLocalizationExtensibleModel):
    cue_id: str
    speaker_id: str | None = None
    speaker_cluster_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    audio_route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] = "manual_review"
    en_subtitle_text: str | None = None
    zh_localized_subtitle_text: str | None = None
    tts_recommended_text: str | None = None
    reference_clip_id: str | None = None
    tts_result_id: str | None = None
    tts_generation_id: str | None = None
    tts_audio_path: str | None = None
    tts_batch_task_id: str | None = None
    tts_batch_status: str | None = None
    tts_batch_error: str | None = None
    tts_attempted_at: str | None = None
    source_duration_ms: int | None = Field(default=None, ge=0)
    generated_duration_ms: int | None = Field(default=None, ge=0)
    source_word_ids: list[str] = Field(default_factory=list)
    source_text_raw: str | None = None
    timing_confidence: Literal["high", "medium", "low"] | None = None
    transcription_revision_id: str | None = None
    manual_timing_revision: int = Field(default=0, ge=0)
    manual_timing_review_status: Literal["not_reviewed", "required", "confirmed"] = "not_reviewed"
    manual_timing_confirmed_revision: int | None = Field(default=None, ge=0)
    manual_timing_confirmed_at: str | None = None
    manual_timing_confirmed_start_ms: int | None = Field(default=None, ge=0)
    manual_timing_confirmed_end_ms: int | None = Field(default=None, ge=0)
    manual_timing_confirmation_method: Literal["auditioned", "asr_vad_verified"] | None = None
    manual_timing_confirmation_evidence: VideoLocalizationAutomaticTimingConfirmationEvidence | None = None
    review_status: Literal["needs_review", "ready", "blocked", "locked"] = "needs_review"
    quality_flags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        if self.manual_timing_review_status == "confirmed":
            confirmation_complete = (
                self.start_ms is not None
                and self.end_ms is not None
                and self.manual_timing_confirmed_at is not None
                and self.manual_timing_confirmed_revision == self.manual_timing_revision
                and self.manual_timing_confirmed_start_ms == self.start_ms
                and self.manual_timing_confirmed_end_ms == self.end_ms
                and (
                    (
                        self.manual_timing_confirmation_method == "auditioned"
                        and self.manual_timing_confirmation_evidence is None
                    )
                    or (
                        self.manual_timing_confirmation_method == "asr_vad_verified"
                        and self.manual_timing_confirmation_evidence is not None
                        and self.manual_timing_confirmation_evidence.transcription_revision_id
                        == self.transcription_revision_id
                        and self.manual_timing_confirmation_evidence.source_word_ids
                        == self.source_word_ids
                    )
                )
            )
            if not confirmation_complete:
                raise PydanticCustomError(
                    "invalid_manual_timing_confirmation",
                    "confirmed manual timing review must match the current cue timing and revision",
                )
        return self


class VideoLocalizationSubtitleCue(VideoLocalizationExtensibleModel):
    subtitle_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(min_length=1)
    tts_text: str | None = None
    tts_result_id: str | None = None
    tts_generation_id: str | None = None
    tts_audio_path: str | None = None
    tts_batch_task_id: str | None = None
    tts_batch_status: str | None = None
    tts_batch_error: str | None = None
    tts_attempted_at: str | None = None
    generated_duration_ms: int | None = Field(default=None, ge=0)
    linked_cue_id: str | None = None
    source_cue_ids: list[str] = Field(default_factory=list)
    source_word_ids: list[str] = Field(default_factory=list)
    spoken_segment_id: str | None = None
    adaptation_note: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        return self


class VideoLocalizationDubSubtitleCue(VideoLocalizationExtensibleModel):
    """Subtitle reconstructed from the audible synthesized-dub lanes."""

    subtitle_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(min_length=1)
    speaker_id: str | None = None
    source_clip_ids: list[str] = Field(default_factory=list)
    dub_lanes: list[int] = Field(default_factory=list)
    source_audio_sha256: str = Field(min_length=64, max_length=64)
    needs_review: bool = False
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError(
                "invalid_time_range",
                "end_ms must be greater than start_ms",
            )
        return self


class VideoLocalizationDubSubtitleDirtyRange(VideoLocalizationExtensibleModel):
    """Persisted timeline boundary awaiting an incremental subtitle refresh."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError(
                "invalid_time_range",
                "end_ms must be greater than start_ms",
            )
        return self


class VideoLocalizationDubSubtitleDirtyScope(
    VideoLocalizationExtensibleModel
):
    """Backend-owned accumulated source edits for selective caption refresh."""

    schema_version: Literal["dub-subtitle-dirty-scope-v1"] = (
        "dub-subtitle-dirty-scope-v1"
    )
    affected_clip_ids: list[str] = Field(default_factory=list)
    affected_ranges: list[VideoLocalizationDubSubtitleDirtyRange] = Field(
        default_factory=list
    )
    replaceable_subtitle_fingerprints: dict[str, str] = Field(
        default_factory=dict
    )


class VideoLocalizationSpokenSegment(VideoLocalizationExtensibleModel):
    segment_id: str
    paragraph_id: str
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_cue_ids: list[str] = Field(min_length=1)
    source_word_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError(
                "invalid_time_range",
                "end_ms must be greater than start_ms",
            )
        return self


class VideoLocalizationSpokenSegmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1)


class VideoLocalizationCueUpdate(BaseModel):
    speaker_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    audio_route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] | None = None
    en_subtitle_text: str | None = None
    zh_localized_subtitle_text: str | None = None
    tts_recommended_text: str | None = None
    reference_clip_id: str | None = None
    review_status: Literal["needs_review", "ready", "blocked", "locked"] | None = None
    quality_flags: list[str] | None = None
    notes: str | None = None
    confirm_timing: bool = False
    expected_start_ms: int | None = Field(default=None, ge=0)
    expected_end_ms: int | None = Field(default=None, ge=0)
    timing_confirmation_method: Literal["auditioned", "asr_vad_verified"] = "auditioned"
    timing_confirmation_evidence: VideoLocalizationAutomaticTimingConfirmationEvidence | None = None


class VideoLocalizationCueTimingConfirmationRequest(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    confirmation_method: Literal["auditioned", "asr_vad_verified"] = "auditioned"
    automatic_evidence: VideoLocalizationAutomaticTimingConfirmationEvidence | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than start_ms")
        if self.confirmation_method == "asr_vad_verified" and self.automatic_evidence is None:
            raise PydanticCustomError(
                "automatic_timing_evidence_required",
                "ASR/VAD timing confirmation requires current evidence",
            )
        if self.confirmation_method == "auditioned" and self.automatic_evidence is not None:
            raise PydanticCustomError(
                "automatic_timing_evidence_unexpected",
                "auditioned timing confirmation must not claim automatic evidence",
            )
        return self


class VideoLocalizationAsrVadTimingWordRequest(BaseModel):
    """One unchanged source token with its newly verified timing."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    word_id: str = Field(min_length=1)
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms < self.start_ms:
            raise PydanticCustomError(
                "invalid_source_timing_word_range",
                "source-word end_ms must be greater than or equal to start_ms",
            )
        return self


class VideoLocalizationAsrVadTimingIntervalRequest(BaseModel):
    """One raw VAD interval from the same verified separated-vocals range."""

    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_ms - self.start_ms != self.duration_ms:
            raise PydanticCustomError(
                "invalid_source_timing_vad_duration",
                "VAD duration_ms must match end_ms - start_ms",
            )
        return self

    @property
    def interval_id(self) -> str:
        return f"vad_{self.start_ms}_{self.end_ms}"


class VideoLocalizationAsrVadTimingIssueRequest(BaseModel):
    """A concise finding retained with an applied source-timing correction."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=128)
    result: str = Field(min_length=1, max_length=1_000)
    word_ids: list[str] = Field(default_factory=list)


class VideoLocalizationAsrVadTimingCueCorrectionRequest(BaseModel):
    """One source cue updated in the same atomic ASR/VAD timing write."""

    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(min_length=1)
    expected_start_ms: int = Field(ge=0)
    expected_end_ms: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    source_word_ids: list[str] = Field(min_length=1)
    vad_interval_ids: list[str] = Field(default_factory=list)
    confirm_timing: bool = True

    @model_validator(mode="after")
    def validate_cue_range(self):
        if self.expected_end_ms <= self.expected_start_ms:
            raise PydanticCustomError(
                "invalid_expected_source_timing_cue_range",
                "expected cue end_ms must be greater than start_ms",
            )
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError(
                "invalid_source_timing_cue_range",
                "corrected cue end_ms must be greater than start_ms",
            )
        if len(self.source_word_ids) != len(set(self.source_word_ids)):
            raise PydanticCustomError(
                "duplicate_source_timing_cue_word",
                "cue source-word references must be unique",
            )
        if len(self.vad_interval_ids) != len(set(self.vad_interval_ids)):
            raise PydanticCustomError(
                "duplicate_source_timing_cue_vad_interval",
                "cue VAD interval references must be unique",
            )
        return self


class VideoLocalizationAsrVadTimingCorrectionRequest(BaseModel):
    """Atomic source-word/cue correction from one separated-vocals ASR/VAD pass."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-vad-source-timing-correction-v1"] = (
        "asr-vad-source-timing-correction-v1"
    )
    expected_project_revision: str = Field(min_length=1)
    transcription_revision_id: str = Field(min_length=1)
    source_track_id: Literal["vocals"] = "vocals"
    audio_sha256: str = Field(min_length=64, max_length=64)
    analysis_protocol: str = Field(min_length=1, max_length=128)
    analysis_start_ms: int = Field(ge=0)
    analysis_end_ms: int = Field(ge=0)
    word_timings: list[VideoLocalizationAsrVadTimingWordRequest] = Field(min_length=1)
    vad_intervals: list[VideoLocalizationAsrVadTimingIntervalRequest] = Field(default_factory=list)
    issues: list[VideoLocalizationAsrVadTimingIssueRequest] = Field(default_factory=list)
    cue_corrections: list[VideoLocalizationAsrVadTimingCueCorrectionRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_request(self):
        if self.analysis_end_ms <= self.analysis_start_ms:
            raise PydanticCustomError(
                "invalid_source_timing_analysis_range",
                "source-timing analysis range must be non-empty",
            )
        if len(self.word_timings) != len({item.word_id for item in self.word_timings}):
            raise PydanticCustomError(
                "duplicate_source_timing_word", "source-timing word IDs must be unique"
            )
        if len(self.vad_intervals) != len({item.interval_id for item in self.vad_intervals}):
            raise PydanticCustomError(
                "duplicate_source_timing_vad_interval", "source-timing VAD intervals must be unique"
            )
        if len(self.cue_corrections) != len({item.cue_id for item in self.cue_corrections}):
            raise PydanticCustomError(
                "duplicate_source_timing_cue", "source-timing cue IDs must be unique"
            )
        if any(
            item.start_ms < self.analysis_start_ms or item.end_ms > self.analysis_end_ms
            for item in [*self.word_timings, *self.vad_intervals]
        ):
            raise PydanticCustomError(
                "source_timing_outside_analysis_range",
                "source-timing words and VAD intervals must stay inside the analysis range",
            )
        return self


class VideoLocalizationSubtitleCueUpdate(BaseModel):
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, min_length=1)
    tts_text: str | None = Field(default=None, min_length=1)


class VideoLocalizationSubtitleImportRequest(BaseModel):
    srt_text: str = Field(min_length=1)
    update_timing: bool = True
    overwrite_tts: bool = False


class VideoLocalizationQualityIssue(VideoLocalizationExtensibleModel):
    code: str
    message: str
    severity: Literal["blocker", "warning", "info"] = "warning"
    cue_id: str | None = None
    speaker_id: str | None = None
    reference_clip_id: str | None = None


class VideoLocalizationQualityGate(VideoLocalizationExtensibleModel):
    status: Literal["unknown", "pass", "warning", "blocked"] = "unknown"
    pending_issues: int = Field(default=0, ge=0)
    blockers: list[VideoLocalizationQualityIssue] = Field(default_factory=list)
    warnings: list[VideoLocalizationQualityIssue] = Field(default_factory=list)
    checked_at: str | None = None


class VideoLocalizationOperation(VideoLocalizationExtensibleModel):
    operation_id: str = Field(default_factory=new_id)
    project_id: str
    kind: Literal[
        "source_audio",
        "stems",
        "english_asr",
        "dub_subtitle_generation",
        "speaker_diarization",
        "localization_draft",
        "reference_clips",
        "semantic_tts_grouping",
        "media_export",
    ]
    status: Literal["queued", "running", "success", "failed", "cancelled"] = "queued"
    label: str | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    result_summary: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class VideoLocalizationOperationSummary(VideoLocalizationOperation):
    """Polling projection; complete task results remain on the detail endpoint."""

    detail_available: Literal[True] = True


class VideoLocalizationOperationRequest(BaseModel):
    kind: Literal[
        "source_audio",
        "stems",
        "speaker_diarization",
        "reference_clips",
        "semantic_tts_grouping",
        "media_export",
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)


class VideoLocalizationTtsTaskStage(VideoLocalizationExtensibleModel):
    kind: Literal["generation", "placement"]
    status: Literal["pending", "queued", "running", "success", "failed", "cancelled"] = "pending"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class VideoLocalizationTtsTask(VideoLocalizationExtensibleModel):
    workflow_id: str = Field(default_factory=new_id)
    project_id: str
    segment_id: str
    subtitle_summary: str
    text: str
    source_cue_ids: list[str] = Field(default_factory=list)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    status: Literal[
        "prepared",
        "queued",
        "running",
        "needs_attention",
        "success",
        "failed",
        "cancelled",
    ] = "prepared"
    required_action: Literal[
        "process_gaps",
        "regenerate_candidate",
        "place_candidate",
        "edit_timeline",
        "review_semantic_boundaries",
        "resolve_capacity",
    ] | None = None
    generation_task_id: str | None = None
    result_id: str | None = None
    timeline_clip_id: str | None = None
    stages: list[VideoLocalizationTtsTaskStage] = Field(
        default_factory=lambda: [
            VideoLocalizationTtsTaskStage(kind="generation"),
            VideoLocalizationTtsTaskStage(kind="placement"),
        ]
    )
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    completed_at: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_ms <= self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than start_ms")
        stage_kinds = [stage.kind for stage in self.stages]
        if stage_kinds != ["generation", "placement"]:
            raise PydanticCustomError(
                "invalid_tts_task_stages",
                "TTS workflow stages must contain generation followed by placement",
            )
        return self


class VideoLocalizationTtsTaskFeed(BaseModel):
    schema_version: Literal["video-localization-tts-task-feed-v1"] = (
        "video-localization-tts-task-feed-v1"
    )
    revision: str
    changed: bool
    workflow_ids: list[str] = Field(default_factory=list)
    tasks: list[VideoLocalizationTtsTask] = Field(default_factory=list)


class VideoLocalizationGlossaryEntry(VideoLocalizationExtensibleModel):
    glossary_id: str = Field(default_factory=new_id)
    source_text: str = Field(min_length=1)
    corrected_source_text: str | None = None
    zh_text: str | None = None
    notes: str | None = None


class VideoLocalizationLanguageConfig(VideoLocalizationExtensibleModel):
    source_language: Literal["auto", "en", "zh"] = "auto"
    target_language: str = "zh-Hans"
    detected_source_language: Literal["en", "zh"] | None = None


class VideoLocalizationTimelineEditReceipt(BaseModel):
    """Server-owned replay receipt for one committed compact timeline edit."""

    schema_version: Literal["timeline-edit-receipt-v1"] = (
        "timeline-edit-receipt-v1"
    )
    request_fingerprint: str = Field(min_length=64, max_length=64)
    repository_revision: int = Field(ge=0)
    updated_at: str | None = None
    timeline_clips: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=1000,
    )
    dub_lane_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    cues: list[VideoLocalizationCue] | None = Field(default=None, max_length=10000)
    localized_subtitles: list[VideoLocalizationSubtitleCue] | None = Field(
        default=None,
        max_length=10000,
    )


class VideoLocalizationDraft(BaseModel):
    _repository_revision: int | None = PrivateAttr(default=None)
    _timeline_edit_response_receipt: VideoLocalizationTimelineEditReceipt | None = (
        PrivateAttr(default=None)
    )

    project_type: Literal["video_localization"] = "video_localization"
    schema_version: str = "v1"
    status: Literal["draft", "reviewing", "ready_for_tts", "tts_running", "candidate", "blocked"] = "draft"
    language_config: VideoLocalizationLanguageConfig = Field(default_factory=VideoLocalizationLanguageConfig)
    source_media: VideoLocalizationSourceMedia = Field(default_factory=VideoLocalizationSourceMedia)
    stems: VideoLocalizationStems = Field(default_factory=VideoLocalizationStems)
    speakers: list[VideoLocalizationSpeaker] = Field(default_factory=list)
    reference_clips: list[VideoLocalizationReferenceClip] = Field(default_factory=list)
    cues: list[VideoLocalizationCue] = Field(default_factory=list)
    transcription: VideoLocalizationTranscriptionState | None = None
    localized_subtitles: list[VideoLocalizationSubtitleCue] = Field(default_factory=list)
    dub_subtitles: list[VideoLocalizationDubSubtitleCue] = Field(
        default_factory=list
    )
    dub_subtitle_source_revision: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    dub_subtitle_dirty_scope: VideoLocalizationDubSubtitleDirtyScope | None = (
        None
    )
    localized_spoken_segments: list[
        VideoLocalizationSpokenSegment
    ] = Field(default_factory=list)
    localization_state: dict[str, Any] = Field(default_factory=dict)
    source_binding_repairs: list[BindingRepairReceipt] = Field(default_factory=list)
    dubbing_production: DubbingProductionState = Field(
        default_factory=DubbingProductionState
    )
    quality_gate: VideoLocalizationQualityGate = Field(default_factory=VideoLocalizationQualityGate)
    operations: list[VideoLocalizationOperation] = Field(default_factory=list)
    tts_tasks: list[VideoLocalizationTtsTask] = Field(default_factory=list)
    history_placement_receipts: dict[str, str] = Field(
        default_factory=dict,
        description="Server-owned completed history placement request IDs and parameter fingerprints; retained after clip edits or deletion.",
    )
    timeline_edit_receipts: dict[str, VideoLocalizationTimelineEditReceipt] = Field(
        default_factory=dict,
        max_length=64,
        description=(
            "Server-owned recent compact timeline request receipts for safe replay "
            "after an uncertain client response."
        ),
    )
    glossary: list[VideoLocalizationGlossaryEntry] = Field(default_factory=list)
    scene_context: str = ""
    ui_state: dict[str, Any] = Field(default_factory=dict)
    generated_candidates: list[dict[str, Any]] = Field(default_factory=list)
    timeline_clips: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str | None = None


class VideoLocalizationExport(VideoLocalizationDraft):
    project_id: str
    project_name: str
    exported_at: str = Field(default_factory=now_iso)
    export_summary: dict[str, Any] = Field(default_factory=dict)


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
    input_mode: Literal["text", "audio", "image"] | None = None
    input_assets: list[EngineInputAsset] = Field(default_factory=list)
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
    input_mode: Literal["text", "audio", "image"] | None = None
    input_assets: list[EngineInputAsset] = Field(default_factory=list)
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


class SERPredictFileRequest(BaseModel):
    file_id: str


class SERBatchPredictRequest(BaseModel):
    voice_ids: list[str] = Field(default_factory=list)
    all: bool = False


class SEREmotionResult(BaseModel):
    voice_id: str
    top_emotion: str | None = None
    emotion_scores: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
