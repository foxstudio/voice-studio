"""Voice Studio Pydantic 数据模型"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────

class EngineType(str, Enum):
    local = "local"
    cloud = "cloud"


class EngineStatus(str, Enum):
    not_installed = "not_installed"
    stopped = "stopped"
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
    voice_id: str | None = None
    language: str = "zh"
    emotion_mode: EmotionMode = EmotionMode.follow_reference
    emotion_values: dict[str, float] | None = None
    emotion_text: str | None = None
    speed: float = 1.0
    temperature: float = 0.8
    top_p: float = 0.8
    seed: int = 0
    output_format: str = "wav"


class GenerateResponse(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"


# ── Task ───────────────────────────────────────────────

class GenerationTask(BaseModel):
    task_id: str
    task_type: str = "single"
    engine_id: str
    voice_id: str | None = None
    input_text: str
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    result_audio_id: str | None = None
    result_duration_ms: int | None = None
    generation_time_ms: int | None = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None


# ── History ────────────────────────────────────────────

class HistoryItem(BaseModel):
    result_id: str
    task_id: str
    engine_id: str
    voice_id: str | None = None
    voice_name: str | None = None
    input_text: str
    output_audio_id: str | None = None
    duration_ms: int | None = None
    generation_time_ms: int | None = None
    parameter_snapshot: dict[str, Any] = Field(default_factory=dict)
    favorite: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Settings ───────────────────────────────────────────

class AppSettings(BaseModel):
    default_engine_id: str = "indextts"
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


# ── Project / Script Studio ────────────────────────────

class Role(BaseModel):
    role_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    color: str = "#3B82F6"
    default_voice_id: str | None = None
    default_engine_id: str | None = None
    default_language: str | None = None
    default_emotion: str | None = None
    default_speed: float = 1.0


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


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    default_engine_id: str | None = None
    default_language: str = "zh"


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
