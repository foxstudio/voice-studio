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
}

export interface EngineInstallation {
	engine_id: string;
	source_url: string;
	source_label: string;
	install_kind: string;
	license_note: string;
	preferred_path: string | null;
	installed: boolean;
	installation_status: string;
	discovered_paths: Array<{
		path: string;
		exists: boolean;
		is_symlink: boolean;
		resolved_path: string | null;
	}>;
	automatic_download_supported: boolean;
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

export type LlmProviderProtocol = 'openai_compatible';

export interface LlmProviderProfile {
	profile_id: string;
	name: string;
	protocol: LlmProviderProtocol;
	base_url: string;
	model_id: string;
	enabled: boolean;
	api_key_configured: boolean;
	model_test_verified: boolean;
}

export interface LlmProviderProfileUpsert {
	name: string;
	protocol: LlmProviderProtocol;
	base_url: string;
	model_id: string;
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

export type WebSearchProvider = 'wikipedia' | 'tavily' | 'searxng';

export interface WebSearchSettings {
	enabled: boolean;
	provider: WebSearchProvider;
	base_url: string;
	api_key_configured: boolean;
	max_queries: number;
	max_results_per_query: number;
}

export interface WebSearchSettingsUpdate {
	enabled: boolean;
	provider: WebSearchProvider;
	base_url: string;
	api_key?: string | null;
	clear_api_key?: boolean;
	max_queries: number;
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

export interface VideoLocalizationTimeRange {
	start_ms: number | null;
	end_ms: number | null;
	source: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationSpeaker {
	speaker_id: string;
	display_name: string | null;
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
	title: string | null;
	person_name: string | null;
	emotion: string | null;
	tags: string[];
	description: string | null;
	cover_frame_path: string | null;
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

export interface VideoLocalizationReferenceClipCreate {
	cue_id?: string | null;
	speaker_id?: string | null;
	start_ms?: number | null;
	end_ms?: number | null;
	asr_text?: string | null;
	title?: string | null;
	person_name?: string | null;
	emotion?: string | null;
	tags?: string[] | null;
	description?: string | null;
	cover_frame_path?: string | null;
}

export interface VideoLocalizationReferenceClipUpdate {
	title?: string | null;
	person_name?: string | null;
	emotion?: string | null;
	tags?: string[] | null;
	description?: string | null;
	cover_frame_path?: string | null;
	cleanliness?: VideoLocalizationReferenceClip['cleanliness'] | null;
	asr_status?: VideoLocalizationReferenceClip['asr_status'] | null;
	asr_text?: string | null;
	notes?: string | null;
}

export interface VideoLocalizationVoiceRecipe {
	recipe_id: string;
	reference_clip_id: string;
	name: string;
	description?: string | null;
	engine_id: string;
	parameter_snapshot: Record<string, unknown>;
	tags: string[];
	created_from_task_id?: string | null;
	created_at?: string | null;
	updated_at?: string | null;
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
	created_at?: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationTimelineClip {
	clip_id: string;
	cue_id?: string | null;
	candidate_id?: string | null;
	track_id: string;
	start_ms?: number | null;
	end_ms?: number | null;
	source_start_ms?: number | null;
	source_end_ms?: number | null;
	audio_path?: string | null;
	status?: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationTranscriptSegment {
	segment_id: string;
	start_ms: number;
	end_ms: number;
	raw_text: string;
	corrected_text: string | null;
	review_candidate_text?: string | null;
	review_rejection_reason?: string | null;
	review_confidence: number | null;
	review_flags: string[];
	review_operations: VideoLocalizationTranscriptEditOperation[];
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
	start_ms: number;
	end_ms: number;
	timing_confidence: 'high' | 'medium' | 'low';
	timing_source: 'forced_aligner' | 'asr_segment_interpolation';
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
	linked_cue_id?: string | null;
	quality_flags: string[];
	[key: string]: unknown;
}

export type VideoLocalizationSubtitleCueUpdate = Partial<
	Pick<VideoLocalizationSubtitleCue, 'start_ms' | 'end_ms'>
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

export interface VideoLocalizationExportState {
	production_json_path: string | null;
	subtitle_paths: Record<string, string>;
	timeline_audio_package_path: string | null;
	timeline_audio_manifest_path: string | null;
	localized_video_path: string | null;
	last_exported_at: string | null;
	[key: string]: unknown;
}

export interface VideoLocalizationOperation {
	operation_id: string;
	project_id: string;
	kind: 'source_audio' | 'stems' | 'english_asr' | 'reference_clips';
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

export interface VideoLocalizationDraft {
	project_type: 'video_localization';
	schema_version: string;
	status: 'draft' | 'reviewing' | 'ready_for_tts' | 'tts_running' | 'candidate' | 'blocked';
	source_media: VideoLocalizationSourceMedia;
	stems: VideoLocalizationStems;
	speakers: VideoLocalizationSpeaker[];
	reference_clips: VideoLocalizationReferenceClip[];
	cues: VideoLocalizationCue[];
	transcription: VideoLocalizationTranscriptionState | null;
	localized_subtitles: VideoLocalizationSubtitleCue[];
	quality_gate: VideoLocalizationQualityGate;
	exports: VideoLocalizationExportState;
	operations: VideoLocalizationOperation[];
	glossary: VideoLocalizationGlossaryEntry[];
	scene_context: string;
	ui_state: Record<string, unknown>;
	project_voice_samples: Record<string, unknown>[];
	voice_recipes: VideoLocalizationVoiceRecipe[];
	generated_candidates: VideoLocalizationGeneratedCandidate[];
	timeline_clips: VideoLocalizationTimelineClip[];
	updated_at: string | null;
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

export interface ProjectUpdate {
	name?: string | null;
	description?: string | null;
	default_engine_id?: string | null;
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
