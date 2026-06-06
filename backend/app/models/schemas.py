"""Voice Studio Pydantic 数据模型"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from enum import Enum

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────

class EngineType(str, Enum):
    local = "local"
    cloud = "cloud"


class EngineStatus(str, Enum):
    not_installed = "not_installed"
    stopped = "stopped"
    loading = "loading"
    loaded = "loaded"
    starting = "starting"
    running = "running"
    error = "error"


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


class TaskStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    running = "running"
    postprocessing = "postprocessing"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"
    retrying = "retrying"


class EmotionMode(str, Enum):
    follow_reference = "follow_reference"
    emotion_reference = "emotion_reference"
    emotion_vector = "emotion_vector"
    emotion_text = "emotion_text"


class VoiceMode(str, Enum):
    clone = "clone"
    design = "design"
    auto = "auto"


class SegmentStatus(str, Enum):
    empty = "empty"
    ready = "ready"
    queued = "queued"
    generating = "generating"
    completed = "completed"
    failed = "failed"
    locked = "locked"


class EngineVersion(str, Enum):
    indextts_v1 = "indextts-v1"
    indextts = "indextts"
    omnivoice = "omnivoice"


# ── Emotion ────────────────────────────────────────────

EMOTION_DIMENSIONS = {
    "happy": "高兴",
    "angry": "愤怒",
    "sad": "悲伤",
    "afraid": "恐惧",
    "disgusted": "反感",
    "melancholic": "低落",
    "surprised": "惊讶",
    "calm": "自然",
}


# ── Engine ─────────────────────────────────────────────

class EngineManifest(BaseModel):
    engine_id: str
    name: str
    display_name: str
    engine_type: EngineType = EngineType.local
    provider: str = ""
    version: str = "0.1.0"
    description: str = ""
    supported_languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    capabilities: list[str] = Field(default_factory=list)
    default_use_case: str = ""
    privacy_level: str = "local_only"
    available_versions: list[str] = Field(default_factory=list)

    sample_rate: int | None = Field(default=None, description="Audio sample rate in Hz")
    max_tokens: int | None = Field(default=None, description="Maximum mel tokens")

class EngineState(BaseModel):
    engine_id: str
    status: EngineStatus = EngineStatus.stopped
    model_path: str | None = None
    error_message: str | None = None


class EngineDetail(BaseModel):
    manifest: EngineManifest
    state: EngineState


# ── Voice Asset ────────────────────────────────────────

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


class VoiceAsset(BaseModel):
    voice_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    voice_type: VoiceType = VoiceType.test_sample
    description: str = ""
    default_language: str = "zh"
    tags: list[str] = Field(default_factory=list)
    reference_audio_ids: list[str] = Field(default_factory=list)
    reference_text: str = ""
    recommended_engine_id: str | None = None
    license_status: LicenseStatus = LicenseStatus.unknown
    quality_status: str = "unchecked"
    quality_notes: str = ""
    favorite: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_used_at: str | None = None


# ── Generate ───────────────────────────────────────────

class GenerateRequest(BaseModel):
    text: str
    engine_id: str = "indextts"
    engine_version: EngineVersion = EngineVersion.indextts
    voice_id: str | None = None
    reference_audio_path: str | None = None
    ref_audio_path: str | None = None
    ref_text: str | None = None
    language: str = "zh"
    # 情绪控制（v2 only）
    emotion_mode: EmotionMode = EmotionMode.follow_reference
    emotion_values: dict[str, float] | None = None
    emotion_text: str | None = None
    # v2 直接情绪名（优先级高于 emotion_mode）
    emotion: str | None = Field(default=None, description="Direct emotion name")
    emo_alpha: float = Field(default=0.6, ge=0.0, le=0.8, description="Emotion intensity 0.0-0.8")
    # 基础参数
    speed: float = Field(default=1.0, ge=0.5, le=3.0)
    temperature: float = Field(default=0.8, ge=0.1, le=2.0)
    top_p: float = Field(default=0.8, ge=0.0, le=1.0)
    top_k: int = Field(default=30, ge=1, le=100)
    repetition_penalty: float = Field(default=10.0, ge=1.0, le=20.0)
    seed: int | None = None
    # 高级参数
    max_mel_tokens: int = 600
    max_text_tokens_per_segment: int = Field(default=120, ge=10, le=500)
    interval_silence: int = Field(default=200, ge=0, le=2000)
    segment_overlap_ms: int = 50
    # v2 专属
    diffusion_steps: int = Field(default=25, ge=1, le=100)
    cfg_rate: float = Field(default=0.7, ge=0.0, le=1.0)
    # 输出
    output_format: str = "wav"


class GenerateResponse(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"


# ── Error ──────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ── Task ───────────────────────────────────────────────

class GenerationTask(BaseModel):
    task_id: str
    task_type: str = "single"
    engine_id: str
    engine_version: str = "indextts"
    voice_id: str | None = None
    input_text: str
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    result_audio_id: str | None = None
    result_duration_ms: int | None = None
    generation_time_ms: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None


# ── History ────────────────────────────────────────────

class HistoryItem(BaseModel):
    result_id: str
    task_id: str
    engine_id: str
    engine_version: str = "indextts"
    voice_id: str | None = None
    voice_name: str | None = None
    input_text: str
    output_audio_id: str | None = None
    duration_ms: int | None = None
    generation_time_ms: int | None = None
    parameter_snapshot: dict[str, Any] = Field(default_factory=dict)
    favorite: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str | None = None

# ── Settings ───────────────────────────────────────────

class AppSettings(BaseModel):
    default_engine_id: str = "indextts"
    default_engine_version: str = "indextts"
    default_language: str = "zh"
    default_output_format: str = "wav"
    model_dir: str = "~/VoiceStudio/models"
    voice_dir: str = "~/VoiceStudio/voices"
    output_dir: str = "~/VoiceStudio/outputs"
    export_dir: str = "~/VoiceStudio/exports"
    project_dir: str = "~/VoiceStudio/projects"
    cache_dir: str = "~/VoiceStudio/cache"
    log_dir: str = "~/VoiceStudio/logs"
    device: str = "auto"
    cloud_enabled: bool = False
    # v2 emotion defaults
    default_emotion: str = "calm"
    default_emo_alpha: float = 0.6
    theme: str = "system"


# ── Project / Script Studio ────────────────────────────

# TODO(post-phase): not implemented in current scope

class Role(BaseModel):
    role_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    color: str = "#3B82F6"
    default_voice_id: str | None = None
    default_engine_id: str | None = None
    default_language: str | None = None
    default_emotion: str | None = None
    default_speed: float = 1.0


# TODO(post-phase): not implemented in current scope

class ScriptSegment(BaseModel):
    segment_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    index: int
    text: str = ""
    role_id: str | None = None
    voice_id: str | None = None
    engine_id: str | None = None
    language: str | None = None
    emotion_mode: EmotionMode | None = None
    speed: float = 1.0
    status: SegmentStatus = SegmentStatus.empty
    result_audio_id: str | None = None
    locked: bool = False


# TODO(post-phase): not implemented in current scope

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    default_engine_id: str | None = None
    default_language: str = "zh"


# TODO(post-phase): not implemented in current scope

class Project(BaseModel):
    project_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    description: str = ""
    default_engine_id: str | None = None
    default_voice_id: str | None = None
    default_language: str = "zh"
    roles: list[Role] = Field(default_factory=list)
    segments: list[ScriptSegment] = Field(default_factory=list)
    status: str = "draft"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
