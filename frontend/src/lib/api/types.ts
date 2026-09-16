export type EngineStatus = 'not_installed' | 'stopped' | 'loading' | 'loaded' | 'running' | 'error';
export type TaskStatus = 'pending' | 'queued' | 'running' | 'postprocessing' | 'success' | 'failed' | 'cancelled' | 'retrying';
export type OutputFormat = 'wav' | 'mp3' | 'flac' | 'pcm' | 'ogg_opus';

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
	description?: string | null;
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
		documentation_url?: string | null;
		documentation_label?: string | null;
		supported_languages: string[];
		capabilities: string[];
		sample_rate: number | null;
		max_tokens: number | null;
		privacy_level: string;
		default_use_case: string;
		parameter_schema: ParameterSchema[];
		supported_output_formats?: OutputFormat[];
	};
	state: {
		engine_id: string;
		status: EngineStatus;
		model_path: string | null;
		error_message: string | null;
		loaded_at: string | null;
	};
	compatibility?: {
		status: 'compatible' | 'unavailable' | 'unknown';
		compatible: boolean | null;
		reason_code: string | null;
		message: string;
		operating_system: string;
		architecture: string;
		supported_platforms: string[];
		available_devices: string[];
	};
}

export interface EngineInstallation {
	engine_id: string;
	runtime_engine_id?: string | null;
	reference_only?: boolean;
	family_id?: string;
	variant_id?: '4bit' | '8bit' | 'official' | string;
	display_name?: string;
	category?: 'media_tool' | string;
	source_url: string;
	source_label: string;
	runtime_url?: string;
	runtime_label?: string;
	install_kind: string;
	license_note: string;
	code_license?: string;
	model_license?: string;
	model_license_status?: 'verified' | 'unverified' | 'unknown' | string;
	license_acceptance_required?: boolean;
	license_acceptance_id?: string;
	model_revision?: string;
	model_filename?: string;
	model_sha256?: string;
	architecture?: string;
	recommended_for?: string;
	benchmark_note?: string;
	runtime?: 'mlx' | 'pytorch' | string;
	auto_selectable?: boolean;
	preferred_path: string | null;
	installed: boolean;
	installation_status: string;
	runtime_ready?: boolean;
	runtime_status?: string;
	runtime_detail?: string | string[] | null;
	integrity?: string;
	progress?: number;
	downloaded_bytes?: number;
	total_bytes?: number;
	size_bytes?: number;
	error?: string | null;
	discovered_paths: Array<{
		path: string;
		exists: boolean;
		has_payload?: boolean;
		is_symlink: boolean;
		resolved_path: string | null;
	}>;
	automatic_download_supported: boolean;
	automatic_download_blockers: string[];
	download_sources: Array<{
		provider: string;
		label: string;
		url: string;
		region: 'cn' | 'global' | string;
		preferred: boolean;
		compatibility_note: string;
	}>;
	download_policy: string;
	reuse_note: string;
}

export interface AsrSelectionStatus {
	mode: string;
	engine_id: string;
	diarization_engine_id: string | null;
	reason: string;
	physical_memory_bytes: number | null;
	policy: {
		short_or_single_speaker: string;
		long_multi_speaker_preference: string[];
		official_full_model_auto_selected: boolean;
	};
}

export interface EngineSpeaker {
	speaker_id: string;
	name: string;
	gender: string;
	description: string;
	label: string;
	age?: string;
	resource_id?: string;
	languages?: Array<string | { code?: string; language?: string; text?: string; flag?: string }>;
	emotions?: Array<string | { value?: string; label?: string; icon?: string }>;
	categories?: string[];
	normal_labels?: string[];
	special_labels?: string[];
	avatar_url?: string;
	trial_url?: string;
	short_trial_url?: string;
	preview_text?: string;
	catalog_source?: 'official' | 'cache' | 'bundled' | string;
	catalog_updated_at?: string;
	catalog_stale?: boolean;
	authorization_status?: 'unknown' | 'verified' | 'denied' | string;
	deprecated?: boolean;
}

export interface DoubaoSpeakerCatalogStatus {
	source?: 'official' | 'cache' | 'bundled' | string;
	total?: number;
	count?: number;
	complete?: boolean;
	stale?: boolean;
	last_synced_at?: string | null;
	fetched_at?: string | null;
	ttl_seconds?: number;
	last_error?: string | null;
	sync_available?: boolean;
	credentials_configured?: boolean;
	message?: string | null;
}

export interface AppSettings {
	data_dir: string;
	model_dir: string;
	model_dir_effective: string;
	model_dir_source: 'settings' | 'environment';
	voice_dir: string;
	output_dir: string;
	export_dir: string;
	project_dir: string;
	cache_dir: string;
	video_preview_cache_mode: 'auto' | 'compact' | 'quality';
	video_preview_cache_max_gb: number;
	stem_separation_overlap: number;
	stem_separation_chunk_duration_seconds: number;
	video_localization_development_step_control_enabled: boolean;
	video_localization_ai_strategy: 'auto' | 'quality' | 'cost';
	video_localization_prompt_strategy: 'adaptive' | 'fixed';
	video_localization_understanding_profile_id: string | null;
	video_localization_creation_profile_id: string | null;
	video_localization_review_profile_id: string | null;
	video_localization_alignment_profile_id: string | null;
	log_dir: string;
	default_engine_id: string;
	default_asr_engine_id: 'auto' | 'qwen3-asr-mlx' | 'faster-whisper-turbo' | 'vibevoice-asr-mlx-4bit' | 'vibevoice-asr-mlx-8bit';
	default_voice_id: string | null;
	default_language: string;
	default_output_format: OutputFormat;
	device: 'auto' | 'cuda' | 'mps' | 'cpu';
	cloud_enabled: boolean;
	mimo_base_url: string;
	mimo_api_key_configured: boolean;
	mimo_default_voice: string;
	mimo_voiceclone_confirm_upload: boolean;
	doubao_base_url: string;
	doubao_api_key_configured: boolean;
	volcengine_access_key_id_configured: boolean;
	volcengine_secret_access_key_configured: boolean;
	doubao_default_tts_resource_id: string;
	doubao_default_icl_resource_id: string;
	doubao_upload_confirm: boolean;
	default_emotion: string;
	default_emo_alpha: number;
	theme: 'system' | 'dark' | 'light';
}

export interface VideoPreviewCacheRange {
	start_ms: number;
	end_ms: number;
	status: 'empty' | 'rendering' | 'ready' | 'failed';
	frame_count: number;
	sprite_rows: number;
}

export interface VideoPreviewCacheStatus {
	contract_version: 'video-preview-cache-status-v1';
	state: 'empty' | 'building' | 'partial' | 'ready' | 'failed';
	phase: 'idle' | 'queued' | 'rendering' | 'failed' | 'cancelled';
	active_chunk: number | null;
	started_at: string | null;
	updated_at: string | null;
	retryable: boolean;
	profile: string;
	revision: string;
	mode: 'auto' | 'compact' | 'quality';
	duration_ms: number;
	chunk_ms: number;
	frame_interval_ms: number;
	frame_width: number;
	frame_height: number;
	sprite_columns: number;
	sprite_rows: number;
	ready_chunks: number;
	total_chunks: number;
	progress: number;
	cached_bytes: number;
	capacity_bytes: number;
	ranges: VideoPreviewCacheRange[];
	error: string | null;
}

export interface VideoPlaybackRange {
	start_ms: number;
	end_ms: number;
}

export interface VideoPlaybackProxyRequest {
	source_playable: boolean;
	start_ms?: number;
	end_ms?: number;
	fill_background?: boolean;
}

export interface VideoPlaybackProxyStatus {
	contract_version: 'video-playback-proxy-status-v2';
	state: 'idle' | 'building' | 'partial' | 'ready' | 'failed';
	mode: 'source' | 'segmented';
	variant: 'source' | 'segments';
	playable: boolean;
	profile: string;
	revision: string;
	duration_ms: number;
	segment_ms: number;
	requested_range: VideoPlaybackRange;
	ready_ranges: VideoPlaybackRange[];
	active_segment: number | null;
	ready_segments: number;
	total_segments: number;
	progress: number;
	updated_at: string | null;
	retryable: boolean;
	error: string | null;
}

export type LlmProviderProtocol = 'openai_compatible' | 'codex_cli';
export type LlmReasoningEffort = 'default' | 'low' | 'high' | 'max';

export interface LlmProviderProfile {
	profile_id: string;
	name: string;
	protocol: LlmProviderProtocol;
	base_url: string;
	model_id: string;
	reasoning_effort: LlmReasoningEffort;
	enabled: boolean;
	api_key_configured: boolean;
	model_test_verified: boolean;
}

export interface LlmProviderProfileUpsert {
	name: string;
	protocol: LlmProviderProtocol;
	base_url: string;
	model_id: string;
	reasoning_effort: LlmReasoningEffort;
	enabled: boolean;
	api_key?: string;
	clear_api_key?: boolean;
}

export interface LlmProviderListResponse {
	profiles: LlmProviderProfile[];
	default_profile_id: string | null;
}

export interface LlmModelInfo {
	model_id: string;
	owned_by: string | null;
}

export interface LlmModelListResponse {
	profile_id: string;
	models: LlmModelInfo[];
}

export interface LlmConnectionTestResponse {
	profile_id: string;
	status: 'connected';
	models_count: number | null;
	selected_model_available: boolean | null;
	tested_model_id: string | null;
	response_verified: boolean;
	billing_effect: 'none' | 'minimal';
	message: string;
}

export interface CodexCliStatus {
	installed: boolean;
	executable_path: string | null;
	version: string | null;
	supports_image_input: boolean;
	logged_in: boolean;
	auth_type: 'chatgpt' | 'api_key' | 'access_token' | 'unknown' | null;
	subscription_usable: boolean;
	operation: 'idle' | 'running' | 'succeeded' | 'failed';
	operation_message: string | null;
	operation_error: string | null;
}

export interface CodexCliAuthActionResponse {
	status: 'started' | 'logged_out';
	message: string;
}

export type WebSearchProvider = 'wikipedia' | 'tavily' | 'searxng';

export interface WebSearchSettings {
	enabled: boolean;
	provider: WebSearchProvider;
	base_url: string;
	api_key_configured: boolean;
	max_results_per_query: number;
}

export interface WebSearchSettingsUpdate {
	enabled: boolean;
	provider: WebSearchProvider;
	base_url: string;
	api_key?: string | null;
	clear_api_key?: boolean;
	max_results_per_query: number;
}

export interface WebSearchTestResponse {
	provider: WebSearchProvider;
	status: 'connected';
	result_count: number;
	message: string;
}

export type CloudProviderId = 'mimo' | 'doubao' | 'volcengine_directory';

export interface CloudConnectionTestResponse {
	provider: CloudProviderId;
	status: 'connected';
	message: string;
	verified_scopes: string[];
	billing_effect: 'none' | 'minimal';
	models_count: number | null;
	request_id: string | null;
	logid: string | null;
}

export interface StorageLocation {
	key: string;
	label: string;
	path: string;
	category: string;
	description: string;
	exists: boolean;
	size_bytes: number;
	file_count: number;
	truncated: boolean;
	cleanup_key: string | null;
	cleanup_label: string | null;
	cleanup_risk: 'low' | 'medium' | 'high' | string | null;
}

export interface StorageFlow {
	name: string;
	path: string;
	description: string;
}

export interface StorageAudit {
	locations: StorageLocation[];
	flows: StorageFlow[];
	total_bytes: number;
}

export interface StorageCleanupResponse {
	cleaned: {
		target: string;
		path: string;
		before_bytes: number;
		after_bytes: number;
		removed_bytes: number;
		before_files: number;
		after_files: number;
	}[];
	skipped: string[];
	removed_bytes: number;
}

export interface StorageOpenResponse {
	status: string;
	key: string;
	path: string;
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
	external_provider?: string | null;
	external_voice_id?: string | null;
	external_status?: string | null;
	external_metadata?: Record<string, unknown>;
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

export interface DoubaoVoiceCloneResponse {
	voice: VoiceAsset;
	summary: Record<string, unknown>;
}

export interface DoubaoCloudVoiceListResponse {
	voices: VoiceAsset[];
	count: number;
	management: {
		local_unbind_supported: boolean;
		cloud_delete_supported: boolean;
		cloud_delete_note: string;
		official_docs: string[];
	};
}

export interface DoubaoCloudRefreshResponse {
	voices: VoiceAsset[];
	failed: { voice_id: string; voice_name: string; message: string }[];
	count: number;
}

export interface UploadResult {
	file_id: string;
	filename: string;
	path: string;
	quality: { passed: boolean; warnings: string[] };
	duration_ms?: number | null;
	size_bytes?: number;
	source_kind?: 'audio' | 'video';
	source_filename?: string | null;
}

export interface VoiceFile {
	file_id: string;
	original_name: string;
	path: string;
	mime_type: string;
	duration_ms: number | null;
	sample_rate: number | null;
	size_bytes: number;
	created_at: string;
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

export interface VoiceClipTranscribeResponse extends UploadResult {
	voice_file: VoiceFile;
	transcription: TranscriptionRecord;
}

export interface VoiceClipResponse {
	file_id: string;
	filename: string;
	path: string;
	quality: { passed?: boolean; warnings?: string[] };
	voice_file: VoiceFile;
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

export interface VideoLocalizationSourceMedia {
	filename: string | null;
	duration_ms: number | null;
	video_path: string | null;
	audio_path: string | null;
	size_bytes: number | null;
	width: number | null;
	height: number | null;
	frame_rate: number | null;
	imported_at: string | null;
	content_sha256?: string | null;
	audio_sha256?: string | null;
	metadata: Record<string, unknown>;
	[key: string]: unknown;
}

export interface VideoLocalizationStems {
	vocals_clean_path: string | null;
	background_path: string | null;
	original_audio_path: string | null;
	separation_engine_id: string | null;
	separation_status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'skipped';
	quality_flags: string[];
	original_audio_sha256?: string | null;
	vocals_clean_sha256?: string | null;
	background_sha256?: string | null;
	[key: string]: unknown;
}

export type ProjectMediaAssetStatus =
	| 'unknown'
	| 'unconfigured'
	| 'available'
	| 'missing'
	| 'not_file'
	| 'unreadable';

export type ProjectPackageStatus =
	| 'unknown'
	| 'available'
	| 'missing'
	| 'invalid'
	| 'repair_required';

export type ProjectMediaRecoveryAction =
	| 'none'
	| 'rescan_project'
	| 'relink_source'
	| 'import_source'
	| 'extract_source_audio'
	| 'separate_stems';

export interface ProjectMediaAssetHealth {
	asset: 'source_video' | 'source_audio' | 'vocals' | 'background';
	status: ProjectMediaAssetStatus;
	resource_id: string | null;
	revision: string | null;
	reason_code: string | null;
	recovery_action: ProjectMediaRecoveryAction;
}

export interface ProjectMediaCandidateHealth {
	candidate: string;
	status: ProjectMediaAssetStatus;
	reason_code: string | null;
}

export interface ProjectSourceAudioHealth extends ProjectMediaAssetHealth {
	asset: 'source_audio';
	selected_source: 'source_media' | 'original_stem' | null;
	candidates: ProjectMediaCandidateHealth[];
}

export interface ProjectMediaHealth {
	contract_version: 'project-media-health-v1';
	package_status: ProjectPackageStatus;
	source_video: ProjectMediaAssetHealth;
	source_audio: ProjectSourceAudioHealth;
	vocals: ProjectMediaAssetHealth;
	background: ProjectMediaAssetHealth;
	stems_status: 'unconfigured' | 'complete' | 'partial' | 'missing' | 'stale';
}

export interface VideoLocalizationTimeRange {
	start_ms: number | null;
	end_ms: number | null;
	source: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationSpeakerIdentityCandidate {
	name: string;
	confidence: number;
	status: 'candidate' | 'confirmed' | 'rejected';
	reason: string;
	evidence_source_ids: string[];
	[key: string]: unknown;
}

export interface VideoLocalizationSpeakerCluster {
	cluster_id: string;
	source_label: string;
	source_engine_id: string;
	business_speaker_id: string | null;
	start_ms: number;
	end_ms: number;
	duration_ms: number;
	segment_count: number;
	confidence: number | null;
	merge_status: 'original' | 'auto_merged' | 'needs_review';
	merged_source_labels: string[];
	time_ranges: VideoLocalizationTimeRange[];
	[key: string]: unknown;
}

export interface VideoLocalizationSpeaker {
	speaker_id: string;
	display_name: string | null;
	acoustic_cluster_ids?: string[];
	identity_candidates?: VideoLocalizationSpeakerIdentityCandidate[];
	route: 'clone_from_source' | 'preset_tts' | 'preserve_original_audio' | 'manual_review';
	reference_clip_ids: string[];
	time_ranges: VideoLocalizationTimeRange[];
	review_status: 'needs_review' | 'ready' | 'blocked' | 'locked';
	notes: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationSpeakerCreate {
	speaker_id?: string | null;
	display_name?: string | null;
	route?: VideoLocalizationSpeaker['route'];
	review_status?: VideoLocalizationSpeaker['review_status'];
	notes?: string | null;
}

export interface VideoLocalizationSpeakerUpdate {
	display_name?: string | null;
	route?: VideoLocalizationSpeaker['route'] | null;
	review_status?: VideoLocalizationSpeaker['review_status'] | null;
	notes?: string | null;
}

export interface VideoLocalizationReferenceClip {
	reference_clip_id: string;
	speaker_id: string | null;
	source_stem: 'vocals_clean' | 'original_audio' | 'uploaded_reference' | 'generated_tts';
	start_ms: number | null;
	end_ms: number | null;
	duration_ms: number | null;
	audio_path: string | null;
	cleanliness: 'clean' | 'needs_review' | 'blocked' | 'mixed' | 'unknown';
	asr_text: string | null;
	asr_status: 'pending' | 'candidate' | 'verified' | 'failed' | 'skipped';
	license_status: string | null;
	quality_flags: string[];
	[key: string]: unknown;
}

export interface VideoLocalizationGeneratedCandidate {
	candidate_id: string;
	recipe_id: string;
	reference_clip_id?: string | null;
	cue_id?: string | null;
	audio_path?: string | null;
	duration_ms?: number | null;
	text_used?: string | null;
	task_id?: string | null;
	notes?: string | null;
	status: string;
	cqc_status?: DubbingReviewStatus;
	cqc_report_version?: 'dubbing-candidate-cqc-v1' | null;
	cqc_report?: DubbingCandidateCqcReport;
	created_at?: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationTimelineClip {
	has_audio_source?: boolean;
	clip_id: string;
	generation_identity?: string | null;
	media_source_clip_id?: string | null;
	cue_id?: string | null;
	subtitle_id?: string | null;
	source_cue_ids?: string[];
	candidate_id?: string | null;
	track_id: string;
	start_ms?: number | null;
	end_ms?: number | null;
	source_start_ms?: number | null;
	source_end_ms?: number | null;
	audio_path?: string | null;
	status?: string | null;
	cqc_status?: DubbingReviewStatus;
	cqc_report_version?: 'dubbing-candidate-cqc-v1' | null;
	[key: string]: unknown;
}

export interface VideoLocalizationTimelineClipEditPatch {
	clip_id: string;
	expected_generation_identity: string;
	expected_editable_fields: VideoLocalizationTimelineClipExpectedEditableFields;
	start_ms?: number | null;
	end_ms?: number | null;
	source_start_ms?: number | null;
	source_end_ms?: number | null;
	media_source_clip_id?: string | null;
	dub_lane?: number | null;
}

export interface VideoLocalizationTimelineClipDelete {
	clip_id: string;
	expected_generation_identity: string;
	expected_editable_fields: VideoLocalizationTimelineClipExpectedEditableFields;
}

export interface VideoLocalizationTimelineClipExpectedEditableFields {
	start_ms: number | null;
	end_ms: number | null;
	source_start_ms: number | null;
	source_end_ms: number | null;
	media_source_clip_id: string | null;
	dub_lane: number | null;
}

export interface VideoLocalizationTimelineClipAdd {
	clip_id: string;
	media_source_clip_id: string;
	start_ms: number;
	end_ms: number;
	source_start_ms: number;
	source_end_ms: number;
	dub_lane: number;
}

export interface VideoLocalizationDubLaneStateEditPatch {
	lane: number;
	muted?: boolean;
	solo?: boolean;
	volume?: number;
	label?: string;
	locked?: boolean;
	remove?: boolean;
}

export interface VideoLocalizationTimelineEditPatchRequest {
	schema_version: 'timeline-edit-patch-v2';
	request_id?: string;
	clip_patches: VideoLocalizationTimelineClipEditPatch[];
	added_clips?: VideoLocalizationTimelineClipAdd[];
	dub_lane_state_patches: VideoLocalizationDubLaneStateEditPatch[];
	deleted_clips?: VideoLocalizationTimelineClipDelete[];
	cue_collection_change?: {
		expected: VideoLocalizationCue[];
		desired: VideoLocalizationCue[];
	};
	localized_subtitle_collection_change?: {
		expected: VideoLocalizationSubtitleCue[];
		desired: VideoLocalizationSubtitleCue[];
	};
	ui_state_patch?: {
		disabled_media_tracks?: string[];
		discarded_tts_task_ids?: string[];
	};
}

export interface VideoLocalizationTimelineEditPatchResponse {
	schema_version: 'timeline-edit-patch-v2';
	updated_at: string | null;
	revision: string;
	timeline_clips: VideoLocalizationTimelineClip[];
	dub_lane_states: Record<string, Record<string, unknown>>;
	cues?: VideoLocalizationCue[] | null;
	localized_subtitles?: VideoLocalizationSubtitleCue[] | null;
}

export interface VideoLocalizationMutationAckResponse {
	schema_version: 'video-localization-mutation-ack-v1';
	updated_at: string | null;
	revision: string;
}

export interface VideoLocalizationUiStatePatchResponse {
	schema_version: 'video-localization-ui-state-patch-v1';
	updated_at: string | null;
	revision: string;
	ui_state_patch: Record<string, unknown>;
}

export interface VideoLocalizationTimelineMutationResponse {
	schema_version: 'video-localization-timeline-mutation-v1';
	updated_at: string | null;
	revision: string;
	affected_clip_ids: string[];
	timeline_clips: VideoLocalizationTimelineClip[];
	cues: VideoLocalizationCue[];
	localized_subtitles: VideoLocalizationSubtitleCue[];
	dub_lane_states: Record<string, Record<string, unknown>>;
	discarded_tts_task_ids: string[];
}

export interface VideoLocalizationTranscriptSegment {
	segment_id: string;
	start_ms: number;
	end_ms: number;
	raw_text: string;
	speaker_cluster_id?: string | null;
	speaker_confidence?: number | null;
	has_speaker_overlap?: boolean;
	corrected_text: string | null;
	review_candidate_text?: string | null;
	review_rejection_reason?: string | null;
	review_confidence: number | null;
	review_flags: string[];
	review_operations: VideoLocalizationTranscriptEditOperation[];
	[key: string]: unknown;
}

export interface VideoLocalizationTranscribeRawInput {
	contract_version: 'asr-raw-v2';
	audio_path: string;
	audio_sha256: string;
	engine_id: string;
	source_track_id: string;
	requested_language: string;
	duration_ms: number | null;
	context_terms: string[];
	[key: string]: unknown;
}

export interface VideoLocalizationDevelopmentAsrResult {
	contract_version: 'asr-raw-v2';
	input: VideoLocalizationTranscribeRawInput;
	raw_text: string;
	language: string;
	segments: VideoLocalizationTranscriptSegment[];
	incomplete_chunk_ranges: Record<string, unknown>[];
	usage_seconds: number | null;
	provider_response_id: string | null;
	stage_timing: Record<string, unknown>;
	quality_summary?: Record<string, unknown> | null;
	[key: string]: unknown;
}

export interface VideoLocalizationDevelopmentInitialAnalysisResult {
	contract_version: 'asr-initial-analysis-snapshot-v1';
	analysis: {
		contract_version: 'asr-initial-analysis-v1';
		raw_asr: VideoLocalizationDevelopmentAsrResult;
		diarization?: Record<string, unknown> | null;
		diarization_error?: string | null;
	};
	joined_transcript: {
		contract_version: 'asr-joined-transcript-v1';
		raw_asr: VideoLocalizationDevelopmentAsrResult;
		diarization?: Record<string, unknown> | null;
		segments: VideoLocalizationTranscriptSegment[];
		warnings: string[];
	};
	[key: string]: unknown;
}

export interface VideoLocalizationDevelopmentEntityNormalizationResult {
	contract_version: 'asr-entity-normalization-v1';
	input: {
		contract_version:
			| 'asr-entity-normalization-input-v1'
			| 'asr-entity-normalization-input-v2';
		source_track_id: string;
		source_audio_sha256: string;
		[key: string]: unknown;
	};
	status: 'not_needed' | 'completed' | 'partial' | 'failed';
	updated_segments: VideoLocalizationTranscriptSegment[];
	resolutions: Record<string, unknown>[];
	changes: Record<string, unknown>[];
	warnings: Record<string, unknown>[];
	duration_ms: number;
	[key: string]: unknown;
}

export interface VideoLocalizationDevelopmentReviewDecisionsResult {
	contract_version: 'asr-review-decisions-v4';
	input: {
		contract_version: 'asr-review-decisions-input-v4';
		source_track_id: string;
		source_audio_sha256: string;
		[key: string]: unknown;
	};
	status: 'completed' | 'partial' | 'failed';
	updated_segments: VideoLocalizationTranscriptSegment[];
	decisions: Record<string, unknown>[];
	changes: Record<string, unknown>[];
	warnings: Record<string, unknown>[];
	duration_ms: number;
	[key: string]: unknown;
}

export interface VideoLocalizationDevelopmentWholeRecheckResult {
	contract_version: 'asr-whole-recheck-v3';
	input: {
		contract_version: 'asr-whole-recheck-input-v3';
		source_track_id: string;
		source_audio_sha256: string;
		segments: VideoLocalizationTranscriptSegment[];
		[key: string]: unknown;
	};
	status: 'completed' | 'partial' | 'failed';
	passed: boolean;
	next_action: 'finish' | 'review_next_round' | 'manual_review';
	next_sections: Record<string, unknown>[];
	unresolved_items: Record<string, unknown>[];
	warnings: string[];
	duration_ms: number;
	[key: string]: unknown;
}

export interface VideoLocalizationDevelopmentTranscriptQualityGateResult {
	contract_version: 'asr-transcript-quality-gate-v1' | 'asr-transcript-quality-gate-v2';
	input: {
		contract_version: 'asr-transcript-quality-gate-input-v1' | 'asr-transcript-quality-gate-input-v2';
		source_track_id: string;
		source_audio_sha256: string;
		segments: VideoLocalizationTranscriptSegment[];
		[key: string]: unknown;
	};
	decision: 'ready_for_alignment' | 'manual_review_required' | 'failed';
	can_start_alignment: boolean;
	blockers: Record<string, unknown>[];
	warnings: Record<string, unknown>[];
	review_targets: Record<string, unknown>[];
	duration_ms: number;
	[key: string]: unknown;
}

export interface VideoLocalizationTranscriptEditOperation {
	start_word_id: string;
	end_word_id: string;
	source_text: string;
	replacement_text: string;
	reason: string;
	confidence: number;
	status: 'accepted' | 'rejected';
	rejection_reason: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationGlossaryEntry {
	glossary_id: string;
	source_text: string;
	corrected_source_text: string | null;
	zh_text: string | null;
	notes: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationAlignedWord {
	word_id: string;
	segment_id: string;
	text: string;
	speaker_cluster_id?: string | null;
	speaker_confidence?: number | null;
	has_speaker_overlap?: boolean;
	start_ms: number;
	end_ms: number;
	timing_confidence: 'high' | 'medium' | 'low';
	timing_source: 'forced_aligner' | 'asr_segment_interpolation' | 'asr_vad_verified';
	[key: string]: unknown;
}

export interface VideoLocalizationAudioBoundaryEvidence {
	boundary_id: string;
	left_word_id: string;
	right_word_id: string;
	start_ms: number;
	end_ms: number;
	gap_ms: number;
	low_energy_ms: number;
	low_energy_ratio: number;
	gap_rms_dbfs: number;
	speech_reference_dbfs: number;
	noise_floor_dbfs: number;
	energy_drop_db: number;
	confidence: 'none' | 'low' | 'medium' | 'high';
	analysis_version: string;
	[key: string]: unknown;
}

export interface VideoLocalizationBoundaryReview {
	boundary_id: string;
	left_word_id: string;
	right_word_id: string;
	decision: 'prefer' | 'allow' | 'avoid';
	confidence: number;
	reason: string;
	prompt_version: string;
	model_id: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationTranscriptionState {
	revision_id: string;
	language: string;
	source_track_id: string | null;
	source_audio_sha256?: string | null;
	alignment_source_track_id?: string | null;
	alignment_audio_sha256?: string | null;
	engine_id: string | null;
	raw_text: string;
	corrected_text: string;
	segments: VideoLocalizationTranscriptSegment[];
	words: VideoLocalizationAlignedWord[];
	raw_asr_warning_codes?: string[];
	raw_asr_incomplete_ranges?: {
		start_ms: number;
		end_ms: number;
		reason: string | null;
	}[];
	diarization_status?: 'not_run' | 'completed' | 'partial' | 'failed' | 'skipped';
	diarization_engine_id?: string | null;
	diarization_model_id?: string | null;
	diarization_error?: string | null;
	speaker_clusters?: VideoLocalizationSpeakerCluster[];
	review_status: 'not_configured' | 'skipped' | 'completed' | 'partial' | 'failed';
	review_profile_id: string | null;
	review_model_id: string | null;
	review_prompt_version: string | null;
	review_error: string | null;
	alignment_status: 'not_run' | 'completed' | 'partial' | 'failed';
	alignment_engine_id: string | null;
	alignment_error: string | null;
	timing_confidence: 'high' | 'medium' | 'low';
	audio_boundary_status: 'not_run' | 'completed' | 'failed' | 'skipped';
	audio_boundary_analysis_version: string | null;
	audio_boundary_error: string | null;
	audio_boundary_features: VideoLocalizationAudioBoundaryEvidence[];
	asr_vad_source_timing_corrections?: Array<{
		correction_id: string;
		contract_version: 'asr-vad-source-timing-correction-v1';
		transcription_revision_id: string;
		source_track_id: 'vocals';
		audio_sha256: string;
		analysis_protocol: string;
		analysis_start_ms: number;
		analysis_end_ms: number;
		word_ids: string[];
		word_timings: Array<{ word_id: string; text: string; start_ms: number; end_ms: number }>;
		vad_intervals: Array<{ interval_id: string; start_ms: number; end_ms: number }>;
		issues: Array<{ code: string; result: string; word_ids: string[] }>;
	}>;
	boundary_review_status: 'not_configured' | 'skipped' | 'completed' | 'partial' | 'failed';
	boundary_review_profile_id: string | null;
	boundary_review_model_id: string | null;
	boundary_review_prompt_version: string | null;
	boundary_review_error: string | null;
	boundary_reviews: VideoLocalizationBoundaryReview[];
	segmentation_profile_id: 'generic_zh' | 'short_video_large_text' | 'conservative_release';
	quality_flags: string[];
	created_at: string;
	[key: string]: unknown;
}

export interface VideoLocalizationCue {
	cue_id: string;
	speaker_id: string | null;
	speaker_cluster_id?: string | null;
	start_ms: number | null;
	end_ms: number | null;
	audio_route: 'clone_from_source' | 'preset_tts' | 'preserve_original_audio' | 'manual_review';
	en_subtitle_text: string | null;
	zh_localized_subtitle_text: string | null;
	tts_recommended_text: string | null;
	reference_clip_id: string | null;
	tts_result_id: string | null;
	tts_audio_path: string | null;
	tts_batch_task_id: string | null;
	tts_batch_status: string | null;
	tts_batch_error: string | null;
	tts_attempted_at: string | null;
	source_duration_ms: number | null;
	generated_duration_ms: number | null;
	source_word_ids: string[];
	source_text_raw: string | null;
	timing_confidence: 'high' | 'medium' | 'low' | null;
	transcription_revision_id: string | null;
	manual_timing_revision?: number;
	manual_timing_review_status?: 'not_reviewed' | 'required' | 'confirmed';
	manual_timing_confirmed_revision?: number | null;
	manual_timing_confirmed_at?: string | null;
	manual_timing_confirmed_start_ms?: number | null;
	manual_timing_confirmed_end_ms?: number | null;
	manual_timing_confirmation_method?: 'auditioned' | 'asr_vad_verified' | null;
	manual_timing_confirmation_evidence?: {
		contract_version: 'asr-vad-cue-timing-confirmation-v1';
		transcription_revision_id: string;
		alignment_source_track_id: string;
		alignment_audio_sha256: string;
		source_word_ids: string[];
		vad_boundary_ids: string[];
		source_timing_correction_id?: string | null;
		vad_interval_ids?: string[];
	} | null;
	review_status: 'needs_review' | 'ready' | 'blocked' | 'locked';
	quality_flags: string[];
	notes: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationSubtitleCue {
	subtitle_id: string;
	start_ms: number;
	end_ms: number;
	text: string;
	tts_text?: string | null;
	tts_result_id?: string | null;
	tts_audio_path?: string | null;
	tts_batch_task_id?: string | null;
	tts_batch_status?: string | null;
	tts_batch_error?: string | null;
	tts_attempted_at?: string | null;
	generated_duration_ms?: number | null;
	linked_cue_id?: string | null;
	source_cue_ids?: string[];
	source_word_ids?: string[];
	spoken_segment_id?: string | null;
	adaptation_note?: string | null;
	quality_flags: string[];
	[key: string]: unknown;
}

export interface VideoLocalizationDubSubtitleCue {
	subtitle_id: string;
	start_ms: number;
	end_ms: number;
	text: string;
	speaker_id: string | null;
	source_clip_ids: string[];
	dub_lanes: number[];
	source_audio_sha256: string | null;
	needs_review: boolean;
	quality_flags: string[];
	[key: string]: unknown;
}

export interface VideoLocalizationSpokenSegment {
	segment_id: string;
	paragraph_id: string;
	text: string;
	start_ms: number;
	end_ms: number;
	source_cue_ids: string[];
	source_word_ids: string[];
	[key: string]: unknown;
}

export type VideoLocalizationSubtitleCueUpdate = Partial<
	Pick<VideoLocalizationSubtitleCue, 'start_ms' | 'end_ms' | 'text' | 'tts_text'>
>;

export type VideoLocalizationCueUpdate = Partial<
	Pick<
		VideoLocalizationCue,
		| 'speaker_id'
		| 'start_ms'
		| 'end_ms'
		| 'audio_route'
		| 'en_subtitle_text'
		| 'zh_localized_subtitle_text'
		| 'tts_recommended_text'
		| 'reference_clip_id'
		| 'review_status'
		| 'quality_flags'
		| 'notes'
		>
> & {
	confirm_timing?: boolean;
};

export interface VideoLocalizationQualityIssue {
	code: string;
	message: string;
	severity: 'blocker' | 'warning' | 'info';
	cue_id: string | null;
	speaker_id: string | null;
	reference_clip_id: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationQualityGate {
	status: 'unknown' | 'pass' | 'warning' | 'blocked';
	pending_issues: number;
	blockers: VideoLocalizationQualityIssue[];
	warnings: VideoLocalizationQualityIssue[];
	checked_at: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationOperation {
	operation_id: string;
	project_id: string;
	kind: 'source_audio' | 'stems' | 'english_asr' | 'speaker_diarization' | 'localization_draft' | 'dub_subtitle_generation' | 'reference_clips' | 'semantic_tts_grouping' | 'media_export';
	status: 'queued' | 'running' | 'success' | 'failed' | 'cancelled';
	label: string | null;
	progress: number;
	error_code: string | null;
	error_message: string | null;
	cancel_requested: boolean;
	result_summary: Record<string, unknown>;
	parameters: Record<string, unknown>;
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationAsrOperationRequest {
	execution_mode?: 'full' | 'stop_after';
	stop_after_step?:
		| 'asr'
		| 'initial_analysis'
		| 'understand_document'
		| 'visual_evidence'
		| 'research'
		| 'normalize_entities'
		| 'section_review_r1'
		| 'review_decisions_r1'
		| 'whole_recheck_r1'
		| 'transcript_quality_gate'
		| null;
	engine_id?: string;
	source_track_id?: 'auto' | 'vocals' | 'original';
	source_language?: string;
	[key: string]: unknown;
}

export interface VideoLocalizationLocalizationOperationRequest {
	source_language?: 'auto' | 'en' | 'zh';
	target_language?: 'zh-Hans';
	profile_id?: string | null;
	localization_requirements_id?: string | null;
	execution_mode?: 'full' | 'development_target';
	development_target_step_id?: string | null;
	development_session_id?: string | null;
	force_development_target?: boolean;
}

export interface VideoLocalizationDubSubtitleOperationRequest {
	engine_id?: string;
	regeneration_mode?: 'auto' | 'full';
	execution_mode?: 'full' | 'development_target';
	development_target_step_id?:
		| 'prepare_track'
		| 'transcribe_track'
		| 'proofread_text'
		| 'align_words'
		| 'segment_subtitles'
		| 'commit'
		| null;
	development_session_id?: string | null;
}

export interface VideoLocalizationDubSubtitleReviewCueInput {
	subtitle_id: string;
	source_subtitle_ids: string[];
	start_ms: number;
	end_ms: number;
	text: string;
}

export interface VideoLocalizationDubSubtitleReviewRequest {
	source_revision: string;
	cues: VideoLocalizationDubSubtitleReviewCueInput[];
}

export interface VideoLocalizationOperationSummary extends VideoLocalizationOperation {
	detail_available: true;
}

export interface VideoLocalizationOperationFeedV2 {
	schema_version: 'operation-feed-v2';
	revision: number;
	history_revision: number;
	changed: boolean;
	active_operations: VideoLocalizationOperationSummary[];
	history: VideoLocalizationOperationSummary[];
	history_total: number;
	next_cursor: string | null;
}

export interface VideoLocalizationTtsTaskStage {
	kind: 'generation' | 'placement';
	status: 'pending' | 'queued' | 'running' | 'success' | 'failed' | 'cancelled';
	progress: number;
	parameters: Record<string, unknown>;
	error_code: string | null;
	error_message: string | null;
	started_at: string | null;
	completed_at: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationTtsTask {
	workflow_id: string;
	project_id: string;
	segment_id: string;
	subtitle_summary: string;
	text: string;
	source_cue_ids: string[];
	start_ms: number;
	end_ms: number;
	status: 'prepared' | 'queued' | 'running' | 'needs_attention' | 'success' | 'failed' | 'cancelled';
	required_action?: 'process_gaps' | 'regenerate_candidate' | 'place_candidate' | 'edit_timeline' | 'review_semantic_boundaries' | 'resolve_capacity' | null;
	generation_task_id: string | null;
	result_id: string | null;
	timeline_clip_id: string | null;
	stages: VideoLocalizationTtsTaskStage[];
	created_at: string;
	updated_at: string;
	completed_at: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationTtsTaskFeed {
	schema_version: 'video-localization-tts-task-feed-v1';
	revision: string;
	changed: boolean;
	workflow_ids: string[];
	tasks: VideoLocalizationTtsTask[];
}

export interface VideoLocalizationTtsParameterPack {
	version: string;
	project_id: string;
	target: {
		subtitle_ids: string[];
		segment_id: string;
		text: string;
		start_ms: number;
		end_ms: number;
		binding_fingerprint: string;
	};
	source: {
		cue_ids: string[];
		word_ids: string[];
		start_ms: number;
		end_ms: number;
		speaker_id: string;
		transcription_revision_id: string | null;
		source_audio_sha256: string | null;
		ref_text: string;
	};
	request: GenerateRequest;
}

export type DubbingReviewStatus = 'passed' | 'warning' | 'failed' | 'needs_review' | 'not_reviewed';

export interface DubbingSemanticUnit {
	unit_id: string;
	subtitle_ids: string[];
	source_cue_ids: string[];
	source_word_ids: string[];
	speaker_id: string;
	scene_id: string | null;
	scene_end_ms: number | null;
	start_ms: number;
	end_ms: number;
	source_anchor_start_ms: number;
	source_anchor_end_ms: number;
	display_text: string;
	spoken_text: string;
	speech_policy: 'translate' | 'preserve_original' | 'omit_non_speech' | 'needs_review';
	decision_reason_codes: string[];
}

export interface DubbingBoundaryEvidence {
	boundary_id: string;
	left_unit_id: string;
	right_unit_id: string;
	gap_ms: number;
	same_speaker: boolean;
	same_scene: boolean | null;
	speech_between: boolean | null;
	pause_classification: 'continuous' | 'natural_pause' | 'long_silence' | 'unknown';
	low_energy_confidence: 'none' | 'low' | 'medium' | 'high';
	semantic_relation: 'continuous' | 'break' | 'unknown';
	hard_boundary: boolean;
	no_break_with_next: boolean;
	evidence_codes: string[];
}

export interface DubbingProductionSnapshot {
	schema_version: 'dubbing-production-snapshot-v1';
	source_revision: string;
	semantic_units: DubbingSemanticUnit[];
	boundaries: DubbingBoundaryEvidence[];
	evidence_warnings: string[];
}

export interface DubbingQualityFinding {
	code: string;
	severity: 'info' | 'warning' | 'blocking';
	message: string;
	entity_ids: string[];
	recommended_action: string | null;
}

export interface DubbingGenerationPlanInput {
	schema_version?: 'dubbing-generation-plan-input-v1';
	source_revision: string;
	semantic_units: DubbingSemanticUnit[];
	boundaries: DubbingBoundaryEvidence[];
	policy?: {
		preferred_group_units?: 1 | 2;
		hard_max_group_units?: 2 | 3;
		allow_three_units_only_for_no_break_phrase?: boolean;
		maximum_group_characters?: number;
		maximum_group_subtitles?: number;
		maximum_effective_speech_ms?: number;
		maximum_text_pressure?: number;
	};
}

export interface DubbingGenerationPlan {
	schema_version: 'dubbing-generation-plan-v1';
	source_revision: string;
	plan_revision?: number;
	status: DubbingReviewStatus;
	semantic_units: DubbingSemanticUnit[];
	speech_islands: Array<{
		island_id: string;
		unit_ids: string[];
		speaker_id: string;
		scene_id: string | null;
		start_ms: number;
		end_ms: number;
	}>;
	groups: Array<{
		group_id: string;
		island_id: string;
		unit_ids: string[];
		subtitle_ids: string[];
		speaker_id: string;
		scene_id: string | null;
		spoken_text: string;
		target_start_ms: number;
		target_end_ms: number;
		source_reference_start_ms: number;
		source_reference_end_ms: number;
	}>;
	findings: DubbingQualityFinding[];
}

export interface DubbingSubjectiveReview {
	dimension: 'meaning' | 'pronunciation' | 'prosody_parse' | 'voice_match' | 'naturalness';
	status: 'passed' | 'failed' | 'not_reviewed';
	note?: string;
	evidence_id?: string | null;
}

export interface TtsContentEvidence {
	schema_version: 'tts-content-evidence-v1';
	audio_sha256: string;
	engine_id: string;
	protocol: 'open-asr-auto-no-hints-v1';
	status: 'complete' | 'unavailable';
	transcript: string;
	error_code?: string | null;
}

export interface DubbingCandidateCqcInput {
	schema_version?: 'dubbing-candidate-cqc-input-v1';
	source_revision: string;
	plan_revision?: number;
	plan_schema_version?: 'dubbing-generation-plan-v1';
	group_id: string;
	candidate_id: string;
	task_status: 'prepared' | 'queued' | 'running' | 'success' | 'failed' | 'cancelled';
	artifact_id?: string | null;
	audio_sha256?: string | null;
	expected_spoken_text: string;
	reference_transcript?: string;
	candidate_transcript?: string;
	content_evidence?: TtsContentEvidence | null;
	target_start_ms: number;
	target_end_ms: number;
	planned_scene_end_ms?: number | null;
	placement_start_ms?: number | null;
	placement_end_ms?: number | null;
	frame_tolerance_ms?: number;
	audio?: DubbingAutomaticAudioEvidence | null;
	policy?: Record<string, number>;
	subjective_reviews?: DubbingSubjectiveReview[];
}

export interface DubbingCandidateReviewCommand {
	schema_version: 'dubbing-candidate-review-command-v2';
	source_revision: string;
	plan_revision: number;
	candidate_id: string;
	audio_sha256: string;
	candidate_evidence_fingerprint: string;
	candidate_clip_projection_fingerprint: string;
	semantic_boundary_reviews: DubbingSemanticBoundaryReview[];
}

export interface DubbingSemanticBoundaryReview {
	boundary_id: string;
	semantic_role: 'semantic_boundary' | 'continuous_phrase';
	disposition: 'acceptable' | 'recover' | 'uncertain';
	reason: string;
	evidence_id?: string | null;
}

export interface DubbingSemanticBoundaryEvidence {
	boundary_id: string;
	left_word_id: string;
	right_word_id: string;
	left_text: string;
	right_text: string;
	left_source_start_ms: number;
	left_source_end_ms: number;
	right_source_start_ms: number;
	right_source_end_ms: number;
	source_relation: 'separated' | 'touching' | 'overlapping';
	source_gap_ms: number;
	source_overlap_ms: number;
	final_relation: 'separated' | 'touching' | 'overlapping' | 'cut';
	final_gap_ms: number;
	final_overlap_ms: number;
	left_render_status: 'fully_retained' | 'partially_cut' | 'removed' | 'zero_width_anchor';
	right_render_status: 'fully_retained' | 'partially_cut' | 'removed' | 'zero_width_anchor';
	left_final_fragments: Array<[number, number]>;
	right_final_fragments: Array<[number, number]>;
	low_energy_evidence: DubbingAudioGapEvidence[];
	safe_edit_boundary?: boolean | null;
}

export interface DubbingSemanticBoundaryAudit {
	schema_version: 'dubbing-semantic-boundary-audit-v1';
	source_revision: string;
	plan_revision: number;
	candidate_id: string;
	audio_sha256: string;
	candidate_evidence_fingerprint: string;
	candidate_clip_projection_fingerprint: string;
	expected_spoken_text: string;
	aligned_words: DubbingCandidateAlignedWord[];
	audio_gap_evidence: DubbingAudioGapEvidence[];
	status: 'pending_agent' | 'accepted' | 'recovery_required';
	boundaries: DubbingSemanticBoundaryEvidence[];
	agent_reviews: DubbingSemanticBoundaryReview[];
}

export interface DubbingStagedCandidateProjection {
	schema_version: 'dubbing-staged-candidate-projection-v1';
	candidate_id: string;
	target_projection_fingerprint: string;
	candidate_clip_projection_fingerprint: string;
	clips: Array<{
		clip_id: string;
		candidate_id: string;
		track_id: 'dub';
		status: 'ready';
		start_ms: number;
		end_ms: number;
		source_start_ms: number;
		source_end_ms: number;
		target_subtitle_ids: string[];
		dubbing_group_id: string;
	}>;
}

export interface DubbingVoicedSpan {
	start_ms: number;
	end_ms: number;
}

export interface DubbingAudioGapEvidence {
	gap_id: string;
	kind: 'leading' | 'internal' | 'trailing';
	start_ms: number;
	end_ms: number;
	duration_ms: number;
	evidence_sources: Array<'waveform' | 'energy' | 'vad' | 'word_alignment' | 'listening'>;
	evidence_ids: string[];
	boundary_confidence: 'clear' | 'ambiguous';
	edit_decision: 'remove' | 'shorten' | 'retain' | 'extend';
	retained_duration_ms: number;
	decision_reason: string;
	safe_edit_boundary?: boolean | null;
	review_evidence_ids: string[];
}

export interface DubbingCandidateAlignedWord {
	word_id: string;
	text: string;
	start_ms: number;
	end_ms: number;
}

export interface DubbingAutomaticAudioEvidence {
	duration_ms: number;
	peak_dbfs: number;
	clipping_ratio: number;
	leading_silence_ms: number;
	trailing_silence_ms: number;
	speech_start_ms?: number | null;
	speech_end_ms?: number | null;
	speech_span_ms?: number | null;
	voiced_spans: DubbingVoicedSpan[];
	aligned_words: Array<{
		word_id: string;
		text: string;
		start_ms: number;
		end_ms: number;
	}>;
	voiced_duration_ms?: number | null;
	speaking_rate_ratio?: number | null;
	project_speaking_rate_ratio_min?: number | null;
	project_speaking_rate_ratio_max?: number | null;
	content_speed_exception_reason?: string | null;
	content_speed_exception_evidence_ids: string[];
	gap_evidence: DubbingAudioGapEvidence[];
	internal_pauses: Array<Record<string, unknown>>;
	expected_pause_baseline_ms?: number | null;
	max_leading_silence_ms?: number | null;
	max_trailing_silence_ms?: number | null;
}

export interface DubbingCandidateCqcReport {
	schema_version: 'dubbing-candidate-cqc-v1';
	source_revision: string;
	plan_revision?: number;
	group_id: string;
	candidate_id: string;
	automatic_status: DubbingReviewStatus;
	subjective_status: DubbingReviewStatus;
	overall_status: DubbingReviewStatus;
	evidence_fingerprint?: string | null;
	audio_evidence?: DubbingAutomaticAudioEvidence | null;
	semantic_boundary_audit?: DubbingSemanticBoundaryAudit | null;
	staged_candidate_projection?: DubbingStagedCandidateProjection | null;
	transcript: {
		expected_tokens: number;
		matched_tokens: number;
		missing_tokens: string[];
		extra_tokens: string[];
		reference_only_extra_tokens: string[];
		coverage_ratio: number;
		extra_ratio: number;
	};
	findings: DubbingQualityFinding[];
	recommended_action: 'accept' | 'listen_and_review' | 'regenerate' | 'repair_text_or_grouping' | 'edit_timeline';
}

export interface DubbingProductionGroupProgress {
	group_id: string;
	group_index: number;
	target_subtitle_ids: string[];
	stage: 'ready_to_generate' | 'generating' | 'needs_gap_processing' | 'needs_regeneration' | 'needs_timeline_work' | 'needs_timeline_edit' | 'needs_semantic_review' | 'accepted' | 'deferred_manual_timing' | 'failed';
	recommended_action: 'generate_candidate' | 'wait_for_generation' | 'process_gaps' | 'regenerate_candidate' | 'place_candidate' | 'edit_timeline' | 'review_semantic_boundaries' | 'complete';
	workflow_ids: string[];
	candidate_ids: string[];
	passed_candidate_id?: string | null;
	formal_clip_ids: string[];
	deferred_clip_ids?: string[];
	existing_timeline_clip_ids?: string[];
	uncovered_target_subtitle_ids?: string[];
	timeline_requires_reconciliation?: boolean;
	speaking_rate_ratio?: number | null;
	content_speed_exception_applied: boolean;
	attempt_count: number;
	last_error?: string | null;
}

export interface DubbingProductionRunSnapshot {
	schema_version: 'dubbing-production-run-v1';
	source_revision: string;
	plan_revision: number;
	status: 'needs_plan' | 'running' | 'needs_attention' | 'completed' | 'completed_with_failures' | 'completed_with_deferred';
	group_count: number;
	accepted_group_count: number;
	deferred_group_count: number;
	failed_group_count: number;
	active_group_count: number;
	attention_group_count: number;
	next_group_id?: string | null;
	next_action: 'create_plan' | 'generate_candidate' | 'wait_for_generation' | 'process_gaps' | 'regenerate_candidate' | 'place_candidate' | 'edit_timeline' | 'review_semantic_boundaries' | 'complete';
	groups: DubbingProductionGroupProgress[];
}

export interface DubbingCompletionSnapshot {
	schema_version: 'dubbing-completion-v1';
	source_revision: string;
	start_ms: number;
	end_ms: number;
	status: 'complete' | 'incomplete' | 'complete_with_warnings';
	expected_target_subtitle_ids: string[];
	covered_target_subtitle_ids: string[];
	missing_target_subtitle_ids: string[];
	unplanned_target_subtitle_ids: string[];
	invalid_clip_ids: string[];
	overlapping_clip_pairs: [string, string][];
	repeated_clip_pairs: [string, string][];
	missing_prerequisites: string[];
	pending_workflow_ids: string[];
	unplaced_workflow_ids: string[];
	unresolved_group_ids: string[];
	unchecked_clip_ids: string[];
	deferred_manual_timing_group_ids: string[];
	deferred_manual_timing_clip_ids: string[];
	automated_production_status: 'resolved' | 'unresolved';
}

export type DubbingCapacityRecoveryStrategy =
	| 'verify_window_and_group'
	| 'safe_gap_edit'
	| 'allowed_speed'
	| 'whole_regeneration'
	| 'semantic_split'
	| 'equivalent_text_compression';

export interface DubbingCapacityRecoveryEvidence {
	strategy: DubbingCapacityRecoveryStrategy;
	outcome: 'applied' | 'not_applicable' | 'exhausted' | 'no_improvement';
	evidence_ids: string[];
	reason: string;
	attempt_count?: number;
	before_duration_ms?: number | null;
	after_duration_ms?: number | null;
}

export interface DubbingCandidateContentEvidenceRequest {
	schema_version?: 'dubbing-candidate-content-evidence-request-v1';
	expected_repository_revision: number;
	source_revision: string;
	plan_revision: number;
	group_id: string;
	candidate_id: string;
	result_id: string;
}

export interface DubbingCandidateContentEvidenceResponse {
	schema_version: 'dubbing-candidate-content-evidence-response-v1';
	repository_revision: number;
	source_revision: string;
	plan_revision: number;
	group_id: string;
	candidate_id: string;
	result_id: string;
	evidence_id: string;
	evidence: TtsContentEvidence;
}

export interface DubbingManualTimingDeferralRequest {
	schema_version?: 'dubbing-manual-timing-deferral-request-v1';
	request_id: string;
	expected_repository_revision: number;
	source_revision: string;
	plan_revision: number;
	group_id: string;
	candidate_id: string;
	result_id: string;
	parked_clip_id: string;
	available_duration_ms: number;
	candidate_duration_ms: number;
	audio_sha256: string;
	content_verification_status: 'verified_complete';
	content_verification_evidence_ids: string[];
	semantic_boundary_review: 'agent_asserted_complete';
	semantic_boundary_evidence_ids: string[];
	naturalness_review: 'agent_listened_acceptable' | 'not_claimed';
	recovery_evidence: DubbingCapacityRecoveryEvidence[];
}

export interface DubbingManualTimingDeferral {
	schema_version: 'dubbing-manual-timing-deferral-v1';
	request_id: string;
	request_fingerprint: string;
	source_revision: string;
	plan_revision: number;
	evidence_origin_source_revision: string;
	evidence_origin_plan_revision: number;
	group_id: string;
	candidate_id: string;
	result_id: string;
	parked_clip_id: string;
	dub_lane: 1;
	placement_failure_reason?: 'duration_overflow' | 'protected_audio_conflict';
	conflicting_clip_ids?: string[];
	target_subtitle_ids: string[];
	source_cue_ids: string[];
	available_duration_ms: number;
	candidate_duration_ms: number;
	audio_sha256: string;
	source_context_fingerprint: string;
	candidate_spoken_text_fingerprint: string;
	clip_projection_fingerprint: string;
	content_verification_status: 'verified_complete';
	content_verification_evidence_ids: string[];
	semantic_boundary_review: 'agent_asserted_complete';
	semantic_boundary_evidence_ids: string[];
	naturalness_review: 'agent_listened_acceptable' | 'not_claimed';
	recovery_evidence: DubbingCapacityRecoveryEvidence[];
	created_at: string;
}

export interface DubbingManualTimingDeferralResponse {
	schema_version: 'dubbing-manual-timing-deferral-response-v1';
	repository_revision: number;
	disposition: DubbingManualTimingDeferral;
	production_run: DubbingProductionRunSnapshot;
}

export interface DubbingProductionExecuteResponse {
	completion?: DubbingCompletionSnapshot | null;
	preflight?: DubbingGroupPreflightResult | null;
	schema_version: 'dubbing-production-execution-v1';
	status: 'queued' | 'waiting' | 'complete' | 'needs_attention';
	scope: 'single_group' | 'all_remaining';
	group_id?: string | null;
	task_id?: string | null;
	workflow_id?: string | null;
	queued_group_ids: string[];
	task_ids: string[];
	workflow_ids: string[];
	required_action?: 'resolve_capacity' | null;
	message: string;
}

export interface DubbingProductionExecutionOptions {
	start_group_id?: string | null;
	end_group_id?: string | null;
	max_in_flight_groups?: number;
	ordinary_speed_baseline?: number | null;
	review_mode?: 'full' | 'risk_based' | 'supervised';
}

export interface DubbingProductionGroupFailureRequest {
	schema_version: 'dubbing-production-group-failure-request-v1';
	source_revision: string;
	plan_revision: number;
	group_id: string;
	candidate_id?: string | null;
	title: string;
	note: string;
	reason_code: string;
	attempted_strategy_codes: string[];
	attempt_count: number;
}

export interface DubbingTimelineExpectedUnit {
	unit_id: string;
	speaker_id: string;
	scene_id?: string | null;
	speech_policy: 'translate' | 'preserve_original' | 'omit_non_speech' | 'needs_review';
	source_anchor_start_ms: number;
	source_anchor_end_ms: number;
	scene_end_ms?: number | null;
}

export interface DubbingTimelineClip {
	clip_id: string;
	candidate_id: string;
	group_id: string;
	unit_ids: string[];
	speaker_id: string;
	scene_id?: string | null;
	dub_lane: number;
	timeline_start_ms: number;
	timeline_end_ms: number;
	source_revision: string;
	plan_revision: number;
	cqc_status: DubbingReviewStatus;
	expected_audio_sha256?: string | null;
	current_audio_sha256?: string | null;
	intentional_overlap?: boolean;
}

export interface DubbingTimelineAuditInput {
	schema_version?: 'dubbing-timeline-audit-input-v1';
	current_source_revision: string;
	current_timeline_revision: string;
	phase?: 'timeline' | 'delivery';
	dub_subtitle_source_revision?: string | null;
	expected_units: DubbingTimelineExpectedUnit[];
	clips?: DubbingTimelineClip[];
	dub_subtitle_clip_ids?: string[];
	frame_tolerance_ms?: number;
}

export interface DubbingTimelineAuditReport {
	schema_version: 'dubbing-timeline-audit-v1';
	current_source_revision: string;
	current_timeline_revision: string;
	phase: 'timeline' | 'delivery';
	status: DubbingReviewStatus;
	covered_unit_ids: string[];
	missing_unit_ids: string[];
	duplicate_unit_ids: string[];
	unexpected_unit_ids: string[];
	findings: DubbingQualityFinding[];
}

export interface VideoLocalizationDraft {
	project_type: 'video_localization';
	schema_version: string;
	status: 'draft' | 'reviewing' | 'ready_for_tts' | 'tts_running' | 'candidate' | 'blocked';
	language_config?: {
		source_language: 'auto' | 'en' | 'zh';
		target_language: string;
		detected_source_language: 'en' | 'zh' | null;
		[key: string]: unknown;
	};
	source_media: VideoLocalizationSourceMedia;
	stems: VideoLocalizationStems;
	speakers: VideoLocalizationSpeaker[];
	reference_clips: VideoLocalizationReferenceClip[];
	cues: VideoLocalizationCue[];
	transcription: VideoLocalizationTranscriptionState | null;
	localized_subtitles: VideoLocalizationSubtitleCue[];
	dub_subtitles?: VideoLocalizationDubSubtitleCue[];
	dub_subtitle_source_revision?: string | null;
	dub_subtitle_dirty_scope?: {
		schema_version: 'dub-subtitle-dirty-scope-v1';
		affected_clip_ids: string[];
		affected_ranges: Array<{ start_ms: number; end_ms: number }>;
		replaceable_subtitle_fingerprints: Record<string, string>;
	} | null;
	localized_spoken_segments: VideoLocalizationSpokenSegment[];
	localization_state?: Record<string, unknown>;
	dubbing_production?: {
		schema_version: 'dubbing-production-state-v2';
		enforcement_mode: 'legacy' | 'planned';
		plan_revision_counter?: number;
		active_plan: DubbingGenerationPlan | null;
		candidate_reports: DubbingCandidateCqcReport[];
		candidate_inputs: DubbingCandidateCqcInput[];
		manual_timing_deferrals?: DubbingManualTimingDeferral[];
		latest_timeline_audit: DubbingTimelineAuditReport | null;
	};
	quality_gate: VideoLocalizationQualityGate;
	operations: VideoLocalizationOperation[];
	tts_tasks?: VideoLocalizationTtsTask[];
	glossary: VideoLocalizationGlossaryEntry[];
	scene_context: string;
	ui_state: Record<string, unknown>;
	generated_candidates: VideoLocalizationGeneratedCandidate[];
	timeline_clips: VideoLocalizationTimelineClip[];
	updated_at: string | null;
}

export type VideoLocalizationWorkspaceDraft = Omit<
	VideoLocalizationDraft,
	'operations' | 'tts_tasks'
> & {
	operations: [];
	tts_tasks: [];
};

export interface VideoLocalizationWorkspaceReadModel {
	revision: string;
	draft: VideoLocalizationWorkspaceDraft;
	media_health: ProjectMediaHealth;
	cue_timing_confirmations: Array<{
		cue_id: string;
		confirmation_current: boolean;
	}>;
	semantic_tts_groups: Array<{
		group_id: string;
		subtitle_ids: string[];
		text_preview: string;
		char_count: number;
	}>;
	omitted_sections: VideoLocalizationWorkspaceDetailSection[];
}

export type VideoLocalizationWorkspaceDetailSection =
	| 'transcription'
	| 'reference_clips'
	| 'generated_candidates'
	| 'dubbing_production';

export interface VideoLocalizationWorkspaceDetail {
	section: VideoLocalizationWorkspaceDetailSection;
	transcription: VideoLocalizationTranscriptionState | null;
	reference_clips: VideoLocalizationReferenceClip[] | null;
	generated_candidates: VideoLocalizationGeneratedCandidate[] | null;
	dubbing_production: VideoLocalizationDraft['dubbing_production'] | null;
}

export interface VideoLocalizationExport extends VideoLocalizationDraft {
	project_id: string;
	project_name: string;
	exported_at: string;
	export_summary: Record<string, unknown>;
}

export interface VideoLocalizationSubtitleImportRequest {
	srt_text: string;
	update_timing?: boolean;
	overwrite_tts?: boolean;
}

export interface GenerateRequest {
	text: string;
	engine_id: string;
	/** Additive engine envelope. Legacy fields remain during migration. */
	input_mode?: string | null;
	input_assets?: EngineInputAsset[];
	engine_parameters?: Record<string, unknown>;
	source?: string | null;
	project_id?: string | null;
	segment_id?: string | null;
	localized_subtitle_id?: string | null;
	cue_id?: string | null;
	timeline_clip_id?: string | null;
	generation_id?: string | null;
	bind_to_video_localization?: boolean;
	video_localization_workflow_id?: string | null;
	video_localization_submission_id?: string | null;
	video_localization_start_ms?: number | null;
	video_localization_end_ms?: number | null;
	video_localization_target_subtitle_ids?: string[];
	video_localization_source_cue_ids?: string[];
	video_localization_dubbing_plan_revision?: number | null;
	video_localization_dubbing_group_id?: string | null;
	voice_id?: string | null;
	voice_source?: 'voice_library' | 'reference_audio' | 'model_preset' | 'voice_design' | null;
	reference_audio_path?: string | null;
	reference_audio_license_status?: string | null;
	reference_audio_tags?: string[];
	ref_text?: string | null;
	custom_reference_source_audio_path?: string | null;
	custom_reference_source_duration_ms?: number | null;
	custom_reference_trim_start_ms?: number | null;
	custom_reference_trim_end_ms?: number | null;
	language: string;
	emotion_mode: 'follow_reference' | 'emotion_vector' | 'emotion_text' | 'emotion_reference';
	emotion?: string | null;
	emotion_values?: Record<string, number> | null;
	emotion_text?: string | null;
	emotion_reference_voice_id?: string | null;
	emotion_reference_audio_path?: string | null;
	emotion_reference_source_audio_path?: string | null;
	emotion_reference_source_duration_ms?: number | null;
	emotion_reference_trim_start_ms?: number | null;
	emotion_reference_trim_end_ms?: number | null;
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
	sway_sampling_coef: number;
	fix_duration: number;
	remove_silence: boolean;
	emo_alpha: number;
	speed: number;
	pitch_rate?: number | null;
	sample_rate?: 8000 | 16000 | 22050 | 24000 | 32000 | 44100 | 48000 | null;
	bit_rate?: number | null;
	loudness_rate?: number | null;
	enable_subtitle?: boolean;
	silence_duration?: number;
	aigc_watermark?: boolean;
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
	guidance_scale: number;
	duration: number;
	audio_chunk_duration?: number;
	audio_chunk_threshold?: number;
	max_tokens?: number;
	cfg_scale?: number | null;
	ddpm_steps?: number | null;
	output_format: OutputFormat;
}

export type EngineInputAssetType = 'audio' | 'image' | 'speaker';
export type EngineInputAssetSource = 'voice_library' | 'upload' | 'cloud_speaker' | 'preset';

export interface EngineInputAsset {
	asset_id: string;
	type: EngineInputAssetType;
	source: EngineInputAssetSource;
	file_id?: string | null;
	voice_id?: string | null;
	speaker_id?: string | null;
	display_name?: string | null;
	ref_text?: string | null;
	source_file_id?: string | null;
	clip_file_id?: string | null;
	trim_start_ms?: number | null;
	trim_end_ms?: number | null;
	duration_ms?: number | null;
	mime_type?: string | null;
	size_bytes?: number | null;
	license_status?: string | null;
}

export interface EngineGenerateRequest {
	text: string;
	engine_id: string;
	input_mode: 'text' | 'audio' | 'image';
	input_assets: EngineInputAsset[];
	engine_parameters: Record<string, unknown>;
}

export interface SeedAudioImageUploadResult {
	file_id: string;
	asset_type: 'seed_audio_image';
	source: 'upload' | 'preset';
	license_status: 'self_voice' | 'authorized' | 'company_authorized' | 'test_only';
	original_name: string;
	mime_type: string;
	media_format: 'jpeg' | 'png' | 'webp';
	size_bytes: number;
	created_at: string;
}

export interface GenerateResponse {
	task_id: string;
	status: TaskStatus;
	video_localization_workflow_id?: string | null;
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
	provider_state_uncertain?: boolean;
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
	generation_id?: string | null;
	task_type: 'single' | 'segment' | 'batch' | 'export';
	engine_id: string;
	voice_id: string | null;
	project_id: string | null;
	segment_id: string | null;
	localized_subtitle_id?: string | null;
	cue_id?: string | null;
	bind_to_video_localization?: boolean;
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
	provider_request_id?: string | null;
	provider_log_id?: string | null;
	provider_state_uncertain?: boolean;
	verification: TTSVerificationResponse | null;
	verification_error: string | null;
	parameters: Record<string, unknown>;
	logs: string[];
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
	artifacts_removed_at?: string | null;
}

export interface TaskSummary {
	all: number;
	active: number;
	processing: number;
	waiting: number;
	success: number;
	failed: number;
}

export interface TaskPageResponse {
	items: GenerationTask[];
	total: number;
	offset: number;
	limit: number;
	summary: TaskSummary;
	download_sequences: Record<string, number>;
}

export interface TaskPageParams {
	offset?: number;
	limit?: number;
	status?: 'all' | 'active' | 'success' | 'failed';
	engine_ids?: string[];
	voice_ids?: string[];
	q?: string;
	created_after?: string;
	sort?: 'latest' | 'oldest' | 'duration_desc';
}

export interface HistoryItem {
	result_id: string;
	task_id: string;
	generation_id?: string | null;
	engine_id: string;
	voice_id: string | null;
	voice_name: string | null;
	project_id: string | null;
	segment_id: string | null;
	localized_subtitle_id?: string | null;
	cue_id?: string | null;
	bind_to_video_localization?: boolean;
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

export interface HistoryPage {
	items: HistoryItem[];
	total: number;
	offset: number;
	limit: number;
}

export type VideoLocalizationTtsHistoryDeleteRequest =
	| { scope: 'result_ids'; result_ids: string[]; segment_id?: never }
	| { scope: 'segment'; segment_id: string; result_ids?: never }
	| { scope: 'project'; result_ids?: never; segment_id?: never };

export interface VideoLocalizationTtsHistoryDeleteResponse {
	removed_records: number;
	cleanup_failures: number;
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

export interface ProjectSummary {
	project_id: string;
	name: string;
	description: string;
	kind: 'script' | 'video_localization';
	has_source_media: boolean;
	source_media_configured: boolean;
	source_media_status: ProjectMediaAssetStatus;
	has_local_package: boolean;
	package_status: ProjectPackageStatus;
	created_at: string;
	updated_at: string;
}

export interface ProjectUpdate {
	name?: string | null;
	description?: string | null;
	default_engine_id?: string | null;
}

export interface VideoLocalizationWorkspaceRevision {
	revision: string;
}

export interface VideoLocalizationTimelineProjection {
	revision: string;
	timeline_clips: VideoLocalizationTimelineClip[];
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
	input_mode?: 'text' | 'audio' | 'image' | null;
	input_assets?: EngineInputAsset[];
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
	task_id?: string;
	result_id?: string;
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


export interface DubbingRecoveryDecision {
    schema_version: 'dubbing-recovery-decision-v1';
    recovery_id: string;
    source_revision: string;
    plan_revision: number;
    group_id: string;
    stage: 'semantic_phrases' | 'nearby_reference';
    phrases: string[];
    reference_cue_ids: string[];
    reason: string;
}

export interface DubbingGroupPreflightResult {
    schema_version: 'dubbing-group-preflight-v1';
    group_id: string;
    frozen_speed: number;
    usable_start_ms: number;
    usable_end_ms: number;
    usable_duration_ms: number;
    estimated_speech_duration_ms: number | null;
    status: 'ready' | 'warning' | 'blocked';
    reason_codes: string[];
    message: string;
}
