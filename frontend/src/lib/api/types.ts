export type EngineStatus = 'not_installed' | 'stopped' | 'loading' | 'loaded' | 'running' | 'error';
export type TaskStatus = 'pending' | 'queued' | 'running' | 'postprocessing' | 'success' | 'failed' | 'cancelled' | 'retrying';
export type OutputFormat = 'wav' | 'mp3' | 'flac';

export interface AudioQualityResult {
	duration_ms: number;
	sample_rate: number;
	peak: number;
	rms: number;
	silence_ratio: number;
	size_bytes: number;
	passed: boolean;
	warnings: string[];
}

export interface ParameterSchema {
	key: string;
	label: string;
	type: 'text' | 'textarea' | 'number' | 'slider' | 'select' | 'toggle' | 'file';
	level: 'basic' | 'advanced' | 'developer';
	default: unknown;
	min: number | null;
	max: number | null;
	step: number | null;
	options: { label: string; value: string }[];
	required: boolean;
	capability: string | null;
}

export interface EngineDetail {
	manifest: {
		engine_id: string;
		display_name: string;
		engine_type: 'local' | 'cloud';
		provider: string;
		version: string;
		description: string;
		supported_languages: string[];
		capabilities: string[];
		sample_rate: number | null;
		max_tokens: number | null;
		privacy_level: string;
		default_use_case: string;
		parameter_schema: ParameterSchema[];
	};
	state: {
		engine_id: string;
		status: EngineStatus;
		model_path: string | null;
		error_message: string | null;
		loaded_at: string | null;
	};
}

export interface EngineSpeaker {
	speaker_id: string;
	name: string;
	gender: string;
	description: string;
	label: string;
}

export interface AppSettings {
	data_dir: string;
	model_dir: string;
	voice_dir: string;
	output_dir: string;
	export_dir: string;
	project_dir: string;
	cache_dir: string;
	log_dir: string;
	default_engine_id: string;
	default_voice_id: string | null;
	default_language: string;
	default_output_format: OutputFormat;
	device: 'auto' | 'mps' | 'cpu';
	cloud_enabled: boolean;
	mimo_base_url: string;
	mimo_api_key_configured: boolean;
	mimo_default_voice: string;
	mimo_voiceclone_confirm_upload: boolean;
	default_emotion: string;
	default_emo_alpha: number;
	theme: 'system' | 'dark' | 'light';
}

export interface VoiceAssetCreate {
	name: string;
	voice_type: string;
	description: string;
	default_language: string;
	tags: string[];
	reference_text: string;
	recommended_engine_id: string | null;
	reference_audio_ids: string[];
	license_status: string;
}

export type VoiceAssetUpdate = Partial<VoiceAssetCreate> & {
	quality_status?: string;
	quality_notes?: string;
	favorite?: boolean;
	emotion_tags?: string[];
};

export interface VoiceEngineBinding {
	engine_id: string;
	mode: 'reference_audio' | 'preset_voice' | 'voice_design' | 'voice_clone';
	available: boolean;
	reason: string;
	external_voice_id: string | null;
	parameters: Record<string, unknown>;
}

export interface VoiceAsset extends VoiceAssetCreate {
	voice_id: string;
	quality_status: string;
	quality_notes: string;
	favorite: boolean;
	emotion_tags: string[];
	created_at: string;
	updated_at: string;
	last_used_at: string | null;
	engine_bindings: VoiceEngineBinding[];
}

export interface UploadResult {
	file_id: string;
	filename: string;
	quality: { passed: boolean; warnings: string[] };
}

export interface TranscriptionSegment {
	start_ms: number;
	end_ms: number;
	text: string;
	language: string | null;
}

export interface TranscriptionRecord {
	transcription_id: string;
	engine_id: string;
	filename: string;
	language: 'auto' | 'zh' | 'en';
	text: string;
	segments: TranscriptionSegment[];
	has_source_audio: boolean;
	timestamp_mode: 'none' | 'native' | 'supplemented';
	timestamp_source_engine_id: string | null;
	duration_ms: number | null;
	size_bytes: number;
	usage_seconds: number | null;
	provider_response_id: string | null;
	created_at: string;
}

export interface TranscriptionTask {
	task_id: string;
	engine_id: string;
	filename: string;
	language: 'auto' | 'zh' | 'en';
	status: TaskStatus;
	text: string | null;
	segments: TranscriptionSegment[];
	has_source_audio: boolean;
	timestamp_mode: 'none' | 'native' | 'supplemented';
	timestamp_source_engine_id: string | null;
	transcription_id: string | null;
	error_message: string | null;
	duration_ms: number | null;
	size_bytes: number;
	usage_seconds: number | null;
	provider_response_id: string | null;
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
}

export interface GenerateRequest {
	text: string;
	engine_id: string;
	voice_id?: string | null;
	reference_audio_path?: string | null;
	ref_text?: string | null;
	language: string;
	emotion_mode: 'follow_reference' | 'emotion_vector' | 'emotion_text';
	emotion?: string | null;
	emotion_values?: Record<string, number> | null;
	emotion_text?: string | null;
	style_instruction?: string | null;
	voice_design_prompt?: string | null;
	optimize_text_preview?: boolean;
	mimo_voice?: string | null;
	speaker_id?: string | null;
	prompt?: string | null;
	nfe_step: number;
	cfg_strength: number;
	target_rms: number;
	cross_fade_duration: number;
	remove_silence: boolean;
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
	output_format: OutputFormat;
}

export interface GenerateResponse {
	task_id: string;
	status: TaskStatus;
}

export interface GeneratePlanRequest {
	text: string;
	engine_id: string;
	planner_mode?: 'auto' | 'rules' | 'llm';
	target_format?: OutputFormat;
}

export interface PlannedTextSegment {
	index: number;
	text: string;
	char_count: number;
	segment_reason: string;
}

export interface GeneratePlanResponse {
	planner: 'rules' | 'llm';
	llm_available: boolean;
	mode: 'direct' | 'longform_recommended' | 'longform_strongly_recommended';
	recommended_action: 'direct_generate' | 'direct_generate_with_verification' | 'split_generate' | 'split_verify_merge';
	requires_user_confirmation: boolean;
	text_length: number;
	threshold: number;
	hard_threshold: number;
	warnings: string[];
	privacy_notice: string;
	planner_reason: string;
	segments: PlannedTextSegment[];
}

export interface TTSVerificationRequest {
	result_id?: string | null;
	expected_text?: string | null;
	transcript_text?: string | null;
	asr_engine_id?: string;
	language?: 'auto' | 'zh' | 'en';
}

export interface TTSVerificationSegment {
	index: number;
	expected_text: string;
	normalized_expected: string;
	coverage: number;
	status: 'passed' | 'warning' | 'failed';
}

export interface TTSVerificationResponse {
	status: 'passed' | 'warning' | 'failed' | 'skipped';
	coverage: number;
	similarity: number;
	expected_text: string;
	transcript_text: string;
	normalized_expected: string;
	normalized_transcript: string;
	missing_segments: TTSVerificationSegment[];
	segment_results: TTSVerificationSegment[];
	warnings: string[];
	suggestions: string[];
	result_id: string | null;
	transcription_id: string | null;
	asr_engine_id: string | null;
}

export interface LongformGenerateRequest {
	generate_request: GenerateRequest;
	segments?: PlannedTextSegment[] | null;
	verify_enabled?: boolean;
	merge_enabled?: boolean;
	max_retries?: number;
	stop_merge_on_verification_failed?: boolean;
	asr_engine_id?: string;
	silence_ms?: number;
	normalize?: boolean;
}

export interface LongformSegmentTask {
	index: number;
	text: string;
	char_count: number;
	status: TaskStatus;
	attempts: number;
	task_id: string | null;
	result_id: string | null;
	duration_ms: number | null;
	error_message: string | null;
	verification: TTSVerificationResponse | null;
}

export interface LongformTask {
	longform_task_id: string;
	engine_id: string;
	voice_id: string | null;
	input_text: string;
	status: TaskStatus;
	progress: number;
	error_message: string | null;
	segments: LongformSegmentTask[];
	result_ids: string[];
	export_id: string | null;
	export_path: string | null;
	verify_enabled: boolean;
	merge_enabled: boolean;
	max_retries: number;
	stop_merge_on_verification_failed: boolean;
	asr_engine_id: string;
	parameters: Record<string, unknown>;
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
}

export interface BatchSegmentInput {
	segment_id?: string | null;
	chapter?: string | null;
	step?: number | null;
	text: string;
	audio?: string | null;
	engine_id?: string | null;
	voice_id?: string | null;
	reference_audio_path?: string | null;
	ref_text?: string | null;
	language?: string | null;
	emotion?: string | null;
	emotion_text?: string | null;
	style_instruction?: string | null;
	voice_design_prompt?: string | null;
	optimize_text_preview?: boolean;
	mimo_voice?: string | null;
	speed?: number | null;
	parameters?: Record<string, unknown>;
}

export interface BatchTask {
	batch_task_id: string;
	project_name: string;
	engine_id: string;
	voice_id: string | null;
	output_dir: string | null;
	output_format: string;
	status: TaskStatus;
	progress: number;
	error_message: string | null;
	segments: {
		segment_id: string;
		chapter: string | null;
		step: number | null;
		text: string;
		audio: string | null;
		output_path: string | null;
		duration_ms: number | null;
		status: TaskStatus;
		error_message: string | null;
	}[];
	parameters: Record<string, unknown>;
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
}

export interface GenerationTask {
	task_id: string;
	task_type: 'single' | 'segment' | 'batch' | 'export';
	engine_id: string;
	voice_id: string | null;
	project_id: string | null;
	segment_id: string | null;
	longform_task_id: string | null;
	longform_segment_index: number | null;
	longform_segment_count: number | null;
	longform_export_id: string | null;
	input_text: string;
	status: TaskStatus;
	progress: number;
	error_message: string | null;
	result_audio_id: string | null;
	result_id: string | null;
	result_duration_ms: number | null;
	generation_time_ms: number | null;
	verification: TTSVerificationResponse | null;
	verification_error: string | null;
	parameters: Record<string, unknown>;
	logs: string[];
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
}

export interface HistoryItem {
	result_id: string;
	task_id: string;
	engine_id: string;
	voice_id: string | null;
	voice_name: string | null;
	project_id: string | null;
	segment_id: string | null;
	longform_task_id: string | null;
	longform_segment_index: number | null;
	longform_segment_count: number | null;
	longform_export_id: string | null;
	input_text: string;
	output_audio_id: string | null;
	output_path: string | null;
	duration_ms: number | null;
	generation_time_ms: number | null;
	verification: TTSVerificationResponse | null;
	verification_error: string | null;
	parameter_snapshot: Record<string, unknown>;
	favorite: boolean;
	created_at: string;
}

export interface Role {
	role_id: string;
	name: string;
	color: string;
	default_voice_id: string | null;
	default_engine_id: string | null;
	default_language: string;
	default_emotion: string | null;
	default_speed: number;
	default_parameters: Record<string, unknown>;
}

export interface ScriptSegment {
	segment_id: string;
	index: number;
	text: string;
	source_start_ms: number | null;
	source_end_ms: number | null;
	role_id: string | null;
	voice_id: string | null;
	engine_id: string | null;
	language: string;
	emotion: string | null;
	speed: number | null;
	status: 'empty' | 'ready' | 'queued' | 'generating' | 'completed' | 'failed' | 'locked';
	result_audio_id: string | null;
	result_id: string | null;
	error_message: string | null;
	locked: boolean;
	parameters: Record<string, unknown>;
}

export interface Project {
	project_id: string;
	name: string;
	description: string;
	default_engine_id: string | null;
	parameters: Record<string, unknown>;
	roles: Role[];
	segments: ScriptSegment[];
	created_at: string;
	updated_at: string;
}

export interface ProjectTranscriptionImportResponse {
	project: Project;
	imported_count: number;
	skipped_count: number;
}

export interface ExportRecord {
	export_id: string;
	path: string;
	format: string;
	source_count: number;
	created_at: string;
}

export interface PresetTemplate {
	preset_id: string;
	name: string;
	scene: string;
	description: string;
	engine_id: string;
	sample_text: string;
	parameters: Record<string, unknown>;
	source_test_id: string | null;
	recommended_voice_type: string;
	tags: string[];
}

export type PresetTemplateInput = Omit<PresetTemplate, 'preset_id' | 'source_test_id'> & {
	preset_id?: string | null;
	source_test_id?: string | null;
};

export interface VoiceSeed {
	seed_id: string;
	name: string;
	description: string;
	source: string;
	download_url: string;
	recommended_engine_id: string;
	reference_text: string;
	tags: string[];
	license_status: string;
	imported_voice_id: string | null;
	quality: AudioQualityResult | null;
}

export interface CommunityVoiceCandidate {
	candidate_id: string;
	name: string;
	description: string;
	source: string;
	download_url: string;
	recommended_engine_id: string;
	reference_text: string;
	tags: string[];
	license_status: string;
	imported_voice_id: string | null;
	quality: AudioQualityResult | null;
}

export interface CommunityVoicePack {
	pack_id: string;
	name: string;
	description: string;
	source: string;
	license_summary: string;
	tags: string[];
	candidates: CommunityVoiceCandidate[];
	imported_count: number;
}

export interface EngineAudioDiagnosis {
	engine_id: string;
	status: 'passed' | 'failed';
	output_path: string | null;
	audio_url?: string | null;
	quality: Partial<AudioQualityResult>;
	generation_time_ms: number | null;
}

export interface EvaluationAudioSample {
	id: string;
	title: string;
	engine_id: string;
	text: string;
	expectation: string;
	status: string;
	params: Record<string, unknown>;
	metrics: {
		duration_sec?: number;
		sample_rate?: number;
		peak?: number;
		rms?: number;
		silence_ratio?: number;
		zero_crossing_rate?: number;
		size_bytes?: number;
	};
	audio_file: string;
	audio_url: string;
}

export interface EvaluationReport {
	run_id: string;
	report_dir: string;
	success_count: number;
	total_count: number;
	report_markdown: string;
	files: {
		markdown: string;
		docx: string;
		metrics: string;
		manifest: string;
	};
	audio_samples: EvaluationAudioSample[];
	file_sizes: Record<string, number>;
}

export interface SEREmotionResult {
	voice_id: string;
	top_emotion: string | null;
	emotion_scores: Record<string, number>;
	error?: string;
}
