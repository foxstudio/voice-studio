// ── Enums (string literal unions) ──────────────────────

export type EngineType = 'local' | 'cloud';

export type EngineStatusValue =
  | 'not_installed'
  | 'stopped'
  | 'loading'
  | 'loaded'
  | 'starting'
  | 'running'
  | 'error';

export type VoiceTypeValue =
  | 'real_person'
  | 'virtual_character'
  | 'host'
  | 'singer'
  | 'narrator'
  | 'emotion_reference'
  | 'test_sample';

export type LicenseStatusValue =
  | 'self_voice'
  | 'company_authorized'
  | 'authorized'
  | 'test_only'
  | 'unknown'
  | 'commercial_forbidden';

export type TaskStatusValue =
  | 'pending'
  | 'queued'
  | 'running'
  | 'postprocessing'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'retrying';

export type EmotionModeValue =
  | 'follow_reference'
  | 'emotion_reference'
  | 'emotion_vector'
  | 'emotion_text';

export type VoiceModeValue = 'clone' | 'design' | 'auto';

export type SegmentStatusValue =
  | 'empty'
  | 'ready'
  | 'queued'
  | 'generating'
  | 'completed'
  | 'failed'
  | 'locked';

export type EngineVersionValue = 'v1' | 'v2';

export type EmotionName =
  | 'happy' | 'sad' | 'angry' | 'afraid'
  | 'disgusted' | 'melancholic' | 'surprised' | 'calm';

// ── Engine ──────────────────────────────────────────────

export interface EngineManifest {
  engine_id: string;
  name: string;
  display_name: string;
  engine_type: EngineType;
  provider: string;
  version: string;
  description: string;
  supported_languages: string[];
  capabilities: string[];
  default_use_case: string;
  privacy_level: string;
  available_versions: string[];
}

export interface EngineState {
  engine_id: string;
  status: EngineStatusValue;
  model_path: string | null;
  error_message: string | null;
}

export interface EngineDetail {
  manifest: EngineManifest;
  state: EngineState;
}

// ── Voice Asset ─────────────────────────────────────────

export interface VoiceAssetCreate {
  name: string;
  voice_type: VoiceTypeValue;
  description: string;
  default_language: string;
  tags: string[];
  reference_text: string;
  recommended_engine_id: string | null;
  reference_audio_ids: string[];
  license_status: LicenseStatusValue;
}

export interface VoiceAsset {
  voice_id: string;
  name: string;
  voice_type: VoiceTypeValue;
  description: string;
  default_language: string;
  tags: string[];
  reference_audio_ids: string[];
  reference_text: string;
  recommended_engine_id: string | null;
  license_status: LicenseStatusValue;
  quality_status: string;
  quality_notes: string;
  favorite: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface UploadResult {
  file_id: string;
  filename: string;
  quality: AudioQuality | null;
}

export interface AudioQuality {
  passed?: boolean;
  warnings?: string[];
  [key: string]: unknown;
}

// ── Generate ────────────────────────────────────────────

export interface GenerateRequest {
  text: string;
  engine_id: string;
  engine_version: EngineVersionValue;
  voice_id?: string | null;
  reference_audio_path?: string | null;
  language: string;
  emotion_mode: EmotionModeValue;
  emotion_values?: Record<string, number> | null;
  emotion_text?: string | null;
  emotion?: EmotionName | null;
  emo_alpha: number;
  speed: number;
  temperature: number;
  top_p: number;
  top_k: number;
  repetition_penalty: number;
  seed?: number | null;
  max_mel_tokens: number;
  max_text_tokens_per_segment: number;
  interval_silence: number;
  segment_overlap_ms: number;
  diffusion_steps: number;
  cfg_rate: number;
  output_format: string;
}

export interface GenerateResponse {
  task_id: string;
  status: string;
}

// ── Error ───────────────────────────────────────────────

export interface ErrorDetail {
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

export interface ErrorResponse {
  error: ErrorDetail;
}

// ── Task ────────────────────────────────────────────────

export interface GenerationTask {
  task_id: string;
  task_type: string;
  engine_id: string;
  engine_version: string;
  voice_id: string | null;
  input_text: string;
  status: TaskStatusValue;
  progress: number;
  error_message: string | null;
  result_audio_id: string | null;
  result_duration_ms: number | null;
  generation_time_ms: number | null;
  parameters: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

// ── History ─────────────────────────────────────────────

export interface HistoryItem {
  result_id: string;
  task_id: string;
  engine_id: string;
  engine_version: string;
  voice_id: string | null;
  voice_name: string | null;
  input_text: string;
  output_audio_id: string | null;
  duration_ms: number | null;
  generation_time_ms: number | null;
  parameter_snapshot: Record<string, unknown>;
  favorite: boolean;
  created_at: string;
  completed_at: string | null;
}

// ── Settings ────────────────────────────────────────────

export interface AppSettings {
  default_engine_id: string;
  default_engine_version: string;
  default_language: string;
  default_output_format: string;
  model_dir: string;
  voice_dir: string;
  output_dir: string;
  export_dir: string;
  project_dir: string;
  cache_dir: string;
  log_dir: string;
  device: string;
  cloud_enabled: boolean;
}

// ── Health ──────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  version: string;
  data_dir: string;
  engines: Record<string, string>;
  uptime_seconds: number;
}

// ── Generic ─────────────────────────────────────────────

export interface DeleteResponse {
  status: string;
}

export interface RetryResponse {
  status: string;
  task_id: string;
}

export interface CancelResponse {
  status: string;
}

export interface TestGenerateResponse {
  task_id: string;
  status: string;
}
