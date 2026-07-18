import { api } from './client';
import type {
	AppSettings,
	BatchTask,
	CommunityVoicePack,
	CloudConnectionTestResponse,
	CloudProviderId,
	DoubaoCloudRefreshResponse,
	DoubaoCloudVoiceListResponse,
	DoubaoVoiceCloneResponse,
	EngineDetail,
	EngineInstallation,
	EngineGenerateRequest,
	EngineSpeaker,
	DoubaoSpeakerCatalogStatus,
	ExportRecord,
	EvaluationReport,
	EngineAudioDiagnosis,
	GeneratePlanRequest,
	GeneratePlanResponse,
	GenerateRequest,
	GenerateResponse,
	GenerationTask,
	HistoryItem,
	LongformGenerateRequest,
	LongformTask,
	LlmConnectionTestResponse,
	LlmModelListResponse,
	LlmProviderListResponse,
	LlmProviderProfileUpsert,
	WebSearchSettings,
	WebSearchSettingsUpdate,
	WebSearchTestResponse,
	PresetTemplate,
	PresetTemplateInput,
	Project,
	ProjectUpdate,
	ProjectTranscriptionImportResponse,
	Role,
	ScriptSegment,
	TranscriptionRecord,
	TranscriptionTask,
	StorageAudit,
	StorageCleanupResponse,
	StorageOpenResponse,
	TTSVerificationRequest,
	TTSVerificationResponse,
	TaskPageParams,
	TaskPageResponse,
	UploadResult,
	VideoLocalizationSpeakerCreate,
	VideoLocalizationSpeakerUpdate,
	VideoLocalizationDraft,
	VideoLocalizationExport,
	VideoLocalizationCueUpdate,
	VideoLocalizationOperation,
	VideoLocalizationReferenceClipCreate,
	VideoLocalizationReferenceClipUpdate,
	VideoLocalizationSubtitleImportRequest,
	VideoLocalizationSubtitleCueUpdate,
	VoiceAsset,
	VoiceAssetCreate,
	VoiceClipTranscribeResponse,
	SEREmotionResult,
	SeedAudioImageUploadResult,
	VoiceAssetUpdate,
	VoiceSeed
} from './types';

export * from './client';
export type * from './types';

export const Api = {
	health: () => api.get<{ status: string; version: string; engines: Record<string, string>; uptime_seconds: number }>('/health'),
	settings: () => api.get<AppSettings>('/settings'),
	saveSettings: (settings: AppSettings) => api.patch<AppSettings>('/settings', settings),
	saveMimoSecret: (body: { api_key?: string | null; clear?: boolean }) => api.patch<AppSettings>('/settings/mimo-secret', body),
	saveDoubaoSecret: (body: { api_key?: string | null; clear?: boolean }) => api.patch<AppSettings>('/settings/doubao-secret', body),
	saveVolcengineDirectorySecret: (body: {
		access_key_id?: string | null;
		secret_access_key?: string | null;
		clear_access_key_id?: boolean;
		clear_secret_access_key?: boolean;
	}) => api.patch<AppSettings>('/settings/volcengine-directory-secret', body),
	testCloudConnection: (provider: CloudProviderId) =>
		api.post<CloudConnectionTestResponse>(`/settings/cloud-connections/${encodeURIComponent(provider)}/test`),
	settingsStorage: () => api.get<StorageAudit>('/settings/storage'),
	cleanupSettingsStorage: (targets: string[]) => api.post<StorageCleanupResponse>('/settings/storage/cleanup', { targets }),
	openSettingsStorageLocation: (key: string) => api.post<StorageOpenResponse>('/settings/storage/open', { key }),
	llmProfiles: () => api.get<LlmProviderListResponse>('/settings/llm-profiles'),
	saveLlmProfile: (id: string, body: LlmProviderProfileUpsert) =>
		api.put<LlmProviderListResponse>(`/settings/llm-profiles/${encodeURIComponent(id)}`, body),
	deleteLlmProfile: (id: string) =>
		api.delete<LlmProviderListResponse>(`/settings/llm-profiles/${encodeURIComponent(id)}`),
	setDefaultLlmProfile: (id: string) =>
		api.post<LlmProviderListResponse>(`/settings/llm-profiles/${encodeURIComponent(id)}/default`),
	llmProfileModels: (id: string) =>
		api.post<LlmModelListResponse>(`/settings/llm-profiles/${encodeURIComponent(id)}/models`),
	testLlmProfile: (id: string) =>
		api.post<LlmConnectionTestResponse>(`/settings/llm-profiles/${encodeURIComponent(id)}/test`),
	webSearchSettings: () => api.get<WebSearchSettings>('/settings/web-search'),
	saveWebSearchSettings: (body: WebSearchSettingsUpdate) => api.put<WebSearchSettings>('/settings/web-search', body),
	testWebSearch: () => api.post<WebSearchTestResponse>('/settings/web-search/test'),
	engines: () => api.get<EngineDetail[]>('/engines'),
	engineInstallations: () => api.get<EngineInstallation[]>('/engines/installations'),
	engineSpeakers: (id: string, params: { q?: string; gender?: 'all' | 'F' | 'M'; limit?: number } = {}) => {
		const search = new URLSearchParams();
		if (params.q) search.set('q', params.q);
		if (params.gender && params.gender !== 'all') search.set('gender', params.gender);
		if (params.limit) search.set('limit', String(params.limit));
		const suffix = search.toString() ? `?${search}` : '';
		return api.get<EngineSpeaker[]>(`/engines/${id}/speakers${suffix}`);
	},
	doubaoSpeakerCatalogStatus: () => api.get<DoubaoSpeakerCatalogStatus>('/engines/doubao-tts-preset/speaker-catalog/status'),
	syncDoubaoSpeakerCatalog: () => api.post<DoubaoSpeakerCatalogStatus>('/engines/doubao-tts-preset/speaker-catalog/sync'),
	doubaoSpeakerPreviewUrl: (speakerId: string) => `/api/engines/doubao-tts-preset/speakers/${encodeURIComponent(speakerId)}/preview`,
	startEngine: (id: string) => api.post<EngineDetail>(`/engines/${id}/start`),
	stopEngine: (id: string) => api.post<EngineDetail>(`/engines/${id}/stop`),
	healthEngine: (id: string) => api.post<Record<string, unknown>>(`/engines/${id}/health-check`),
	diagnoseEngineAudio: (id: string, body: { reference_audio_path?: string | null; voice_id?: string | null; text?: string; emotion?: string | null }) => api.post<EngineAudioDiagnosis>(`/engines/${id}/diagnose-audio`, body),
	voices: (params?: { offset?: number; limit?: number }) => {
		const search = new URLSearchParams();
		if (params?.offset !== undefined) search.set('offset', String(params.offset));
		if (params?.limit !== undefined) search.set('limit', String(params.limit));
		const suffix = search.toString() ? `?${search}` : '';
		return api.get<VoiceAsset[]>(`/voices${suffix}`);
	},
	createVoice: (voice: VoiceAssetCreate) => api.post<VoiceAsset>('/voices', voice),
	updateVoice: (id: string, voice: VoiceAssetUpdate) => api.patch<VoiceAsset>(`/voices/${id}`, voice),
	deleteVoice: (id: string) => api.delete<{ status: string }>(`/voices/${id}`),
	trainDoubaoVoiceClone: (id: string, body: { confirm_upload: boolean; demo_text?: string | null; custom_speaker_id?: string | null; language?: string; enable_audio_denoise?: boolean; disable_volume_normalization?: boolean }) =>
		api.post<DoubaoVoiceCloneResponse>(`/voices/${id}/doubao/clone-train`, body),
	refreshDoubaoVoiceStatus: (id: string) => api.post<DoubaoVoiceCloneResponse>(`/voices/${id}/doubao/status`),
	doubaoCloudVoices: () => api.get<DoubaoCloudVoiceListResponse>('/voices/doubao/cloud'),
	refreshDoubaoCloudVoices: () => api.post<DoubaoCloudRefreshResponse>('/voices/doubao/cloud/refresh'),
	unbindDoubaoVoice: (id: string) => api.delete<VoiceAsset>(`/voices/${id}/doubao/binding`),
	uploadVoice: (file: File) => api.upload<UploadResult>('/voices/upload', file),
	uploadSeedAudioImage: (file: File, licenseStatus: 'self_voice' | 'authorized' | 'company_authorized' | 'test_only' = 'self_voice') =>
		api.upload<SeedAudioImageUploadResult>('/seed-audio/assets/image', file, { license_status: licenseStatus }),
	clipTranscribeVoice: (fileId: string, body: { start_ms: number; end_ms: number; language?: 'auto' | 'zh' | 'en'; engine_id?: string }) =>
		api.post<VoiceClipTranscribeResponse>(`/voices/files/${encodeURIComponent(fileId)}/clip-transcribe`, body),
	generatePlan: (body: GeneratePlanRequest) => api.post<GeneratePlanResponse>('/generate/plan', body),
	generate: (body: GenerateRequest | EngineGenerateRequest) => api.post<GenerateResponse>('/generate', body),
	generateLongform: (body: LongformGenerateRequest) => api.post<LongformTask>('/longform/generate', body),
	longformTasks: (params: { includeCompleted?: boolean; limit?: number } = {}) => {
		const search = new URLSearchParams();
		if (params.includeCompleted !== undefined) search.set('include_completed', String(params.includeCompleted));
		if (params.limit !== undefined) search.set('limit', String(params.limit));
		const suffix = search.toString() ? `?${search}` : '';
		return api.get<LongformTask[]>(`/longform${suffix}`);
	},
	longformTask: (id: string) => api.get<LongformTask>(`/longform/${id}`),
	retryLongformFailed: (id: string) => api.post<LongformTask>(`/longform/${id}/retry-failed`),
	generateBatch: (body: unknown) => api.post<BatchTask>('/batches/generate', body),
	batches: () => api.get<BatchTask[]>('/batches'),
	batch: (id: string) => api.get<BatchTask>(`/batches/${id}`),
	tasks: () => api.get<GenerationTask[]>('/tasks'),
	taskPage: (params: TaskPageParams = {}) => {
		const search = new URLSearchParams();
		if (params.offset !== undefined) search.set('offset', String(params.offset));
		if (params.limit !== undefined) search.set('limit', String(params.limit));
		if (params.status) search.set('status', params.status);
		if (params.engine_ids?.length) search.set('engine_ids', params.engine_ids.join(','));
		if (params.voice_ids?.length) search.set('voice_ids', params.voice_ids.join(','));
		if (params.q) search.set('q', params.q);
		if (params.created_after) search.set('created_after', params.created_after);
		if (params.sort) search.set('sort', params.sort);
		const suffix = search.toString() ? `?${search}` : '';
		return api.get<TaskPageResponse>(`/tasks/page${suffix}`);
	},
	task: (id: string) => api.get<GenerationTask>(`/tasks/${id}`),
	cancelTask: (id: string) => api.post<{ status: string }>(`/tasks/${id}/cancel`),
	cancelLongform: (id: string) => api.post<{ longform_task_id: string; status: string }>(`/longform/${id}/cancel`),
	cancelLongformSegment: (id: string, segmentIndex: number) => api.post<{ longform_task_id: string; segment_index: number; status: string }>(`/longform/${id}/segments/${segmentIndex}/cancel`),
	dismissLongform: (id: string) => api.delete<{ longform_task_id: string; status: string }>(`/longform/${id}`),
	retryTask: (id: string) => api.post<{ task_id: string; status: string }>(`/tasks/${id}/retry`),
	deleteTask: (id: string) => api.delete<{ task_id: string; status: string }>(`/tasks/${id}`),
	history: (params?: { limit?: number; offset?: number; project_id?: string; segment_id?: string; source?: string }) => {
		const query = new URLSearchParams();
		if (params?.limit !== undefined) query.set('limit', String(params.limit));
		if (params?.offset !== undefined) query.set('offset', String(params.offset));
		if (params?.project_id) query.set('project_id', params.project_id);
		if (params?.segment_id) query.set('segment_id', params.segment_id);
		if (params?.source) query.set('source', params.source);
		return api.get<HistoryItem[]>(`/history${query.size ? `?${query.toString()}` : ''}`);
	},
	deleteHistory: (id: string) => api.delete<{ status: string }>(`/history/${id}`),
	presets: () => api.get<PresetTemplate[]>('/presets'),
	createPreset: (preset: PresetTemplateInput) => api.post<PresetTemplate>('/presets', preset),
	updatePreset: (id: string, preset: PresetTemplateInput) => api.patch<PresetTemplate>(`/presets/${id}`, preset),
	deletePreset: (id: string) => api.delete<{ status: string; preset_id: string }>(`/presets/${id}`),
	voiceSeeds: () => api.get<VoiceSeed[]>('/voice-seeds'),
	importVoiceSeed: (seed_id: string) => api.post<VoiceSeed>('/voice-seeds/import', { seed_id }),
	communityVoicePacks: () => api.get<CommunityVoicePack[]>('/community-voice-packs'),
	importCommunityVoicePack: (pack_id: string, candidate_ids: string[] = []) => api.post<CommunityVoicePack>('/community-voice-packs/import', { pack_id, candidate_ids }),
	projects: () => api.get<Project[]>('/projects'),
	syncVideoLocalizationProjects: () => api.post<Project[]>('/projects/video-localization/sync-projects'),
	createProject: (name: string, description = '', default_engine_id: string | null = 'indextts-v2') => api.post<Project>('/projects', { name, description, default_engine_id }),
	updateProject: (id: string, patch: ProjectUpdate) => api.patch<Project>(`/projects/${id}`, patch),
	deleteProject: (id: string) => api.delete<{ status: string }>(`/projects/${id}`),
	addRole: (id: string, role: Role) => api.post<Project>(`/projects/${id}/roles`, role),
	putSegments: (id: string, segments: ScriptSegment[]) => api.put<Project>(`/projects/${id}/segments`, segments),
	videoLocalizationDraft: (id: string) => api.get<VideoLocalizationDraft>(`/projects/${id}/video-localization`),
	saveVideoLocalizationDraft: (id: string, draft: VideoLocalizationDraft) => api.put<VideoLocalizationDraft>(`/projects/${id}/video-localization`, draft),
	updateVideoLocalizationUiState: (id: string, patch: Record<string, unknown>) => api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/ui-state`, patch),
	resetVideoLocalizationDraft: (id: string) => api.delete<VideoLocalizationDraft>(`/projects/${id}/video-localization`),
	openVideoLocalizationProjectDirectory: (id: string) => api.post<StorageOpenResponse>(`/projects/${id}/video-localization/open-directory`),
	importVideoLocalizationSource: (id: string, file: File) => api.upload<VideoLocalizationDraft>(`/projects/${id}/video-localization/source-media`, file),
	videoLocalizationOperations: (id: string) => api.get<VideoLocalizationOperation[]>(`/projects/${id}/video-localization/operations`),
	videoLocalizationOperationSummaries: (id: string) => api.get<VideoLocalizationOperation[]>(`/projects/${id}/video-localization/operations/summaries`),
	videoLocalizationOperation: (id: string, operationId: string) => api.get<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/${operationId}`),
	submitVideoLocalizationOperation: (id: string, kind: VideoLocalizationOperation['kind'], parameters: Record<string, unknown> = {}) =>
		api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations`, { kind, parameters }),
	cancelVideoLocalizationOperation: (id: string, operationId: string) => api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/${operationId}/cancel`),
	retryVideoLocalizationOperation: (id: string, operationId: string) => api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/${operationId}/retry`),
	extractVideoLocalizationAudio: (id: string) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/source-audio`),
	separateVideoLocalizationStems: (id: string) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/stems`),
	transcribeVideoLocalizationEnglish: (id: string) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/asr/en`),
	createVideoLocalizationReferences: (id: string, body?: VideoLocalizationReferenceClipCreate) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/reference-clips`, body),
	createVideoLocalizationSpeaker: (id: string, body: VideoLocalizationSpeakerCreate) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/speakers`, body),
	updateVideoLocalizationSpeaker: (id: string, speakerId: string, body: VideoLocalizationSpeakerUpdate) =>
		api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/speakers/${speakerId}`, body),
	updateVideoLocalizationCue: (id: string, cueId: string, body: VideoLocalizationCueUpdate) => api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/cues/${cueId}`, body),
	updateVideoLocalizationLocalizedSubtitle: (id: string, subtitleId: string, body: VideoLocalizationSubtitleCueUpdate) =>
		api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/localized-subtitles/${subtitleId}`, body),
	updateVideoLocalizationReference: (id: string, referenceClipId: string, body: VideoLocalizationReferenceClipUpdate) =>
		api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/reference-clips/${referenceClipId}`, body),
	deleteVideoLocalizationReference: (id: string, referenceClipId: string) => api.delete<VideoLocalizationDraft>(`/projects/${id}/video-localization/reference-clips/${referenceClipId}`),
	applyVideoLocalizationCandidate: (id: string, candidateId: string) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/candidates/${candidateId}/apply`),
	applyVideoLocalizationHistoryToTimelineClip: (id: string, clipId: string, resultId: string) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/timeline-clips/${encodeURIComponent(clipId)}/history/${encodeURIComponent(resultId)}/apply`),
	applyVideoLocalizationHistoryToTimeline: (id: string, resultId: string, body: { segment_id: string; clip_id?: string | null; start_ms?: number | null; dub_lane?: number | null; force_new?: boolean }) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/timeline-clips/history/${encodeURIComponent(resultId)}/apply`, body),
	generateVideoLocalizationChineseDraft: (id: string) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/localize/zh`),
	submitVideoLocalizationBatchTts: (id: string) => api.post<BatchTask>(`/projects/${id}/video-localization/tts/batch`),
	prepareVideoLocalizationTtsHandoff: (id: string, segmentId: string) =>
		api.post<GenerateRequest>(`/projects/${id}/video-localization/tts/handoff/${encodeURIComponent(segmentId)}`),
	syncVideoLocalizationBatchTts: (id: string, batchId: string) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/tts/batch/${batchId}/sync`),
	importVideoLocalizationSubtitles: (id: string, kind: 'en' | 'zh' | 'tts', body: VideoLocalizationSubtitleImportRequest) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/subtitles/${kind}/import`, body),
	clearVideoLocalizationSubtitles: (id: string, kind: 'en' | 'zh') =>
		api.delete<VideoLocalizationDraft>(`/projects/${id}/video-localization/subtitles/${kind}`),
	exportVideoLocalizationDraft: (id: string) => api.get<VideoLocalizationExport>(`/projects/${id}/video-localization/export`),
	exportVideoLocalizationTimeline: (id: string) => api.get<Record<string, unknown>>(`/projects/${id}/video-localization/export/timeline`),
	videoLocalizationReadiness: (id: string) => api.get<Record<string, unknown>>(`/projects/${id}/video-localization/readiness`),
	importTranscriptionsToProject: (
		id: string,
		body: { transcription_ids: string[]; mode?: 'append' | 'replace'; role_id?: string | null; default_engine_id?: string | null; default_voice_id?: string | null }
	) => api.post<ProjectTranscriptionImportResponse>(`/projects/${id}/transcriptions/import`, body),
	generateProject: (id: string) => api.post<{ task_ids: string[]; status: string }>(`/projects/${id}/generate`),
	exports: () => api.get<ExportRecord[]>('/exports'),
	latestEvaluation: () => api.get<EvaluationReport>('/evaluations/latest'),
	verifyTTSOutput: (body: TTSVerificationRequest) => api.post<TTSVerificationResponse>('/evaluations/tts-verification', body),
	createExport: (body: { result_ids?: string[]; audio_ids?: string[]; project_id?: string | null; format: string; silence_ms: number; normalize: boolean }) => api.post<ExportRecord>('/exports', body),
	transcribeAudio: (file: File, language: 'auto' | 'zh' | 'en' = 'auto', engineId = 'mimo-v2.5-asr') => {
		const form = new FormData();
		form.append('file', file);
		form.append('language', language);
		form.append('engine_id', engineId);
		return api.postForm<TranscriptionRecord>('/asr/transcribe', form);
	},
	createTranscriptionTask: (file: File, language: 'auto' | 'zh' | 'en' = 'auto', engineId = 'mimo-v2.5-asr') => {
		const form = new FormData();
		form.append('file', file);
		form.append('language', language);
		form.append('engine_id', engineId);
		return api.postForm<TranscriptionTask>('/asr/tasks', form);
	},
	transcription: (transcriptionId: string) => api.get<TranscriptionRecord>(`/asr/${transcriptionId}`),
	supplementTranscriptionTimestamps: (
		transcriptionId: string,
		body: { strategy?: 'auto' | 'forced_aligner' | 'qwen3-asr-mlx'; overwrite?: boolean } = {}
	) => api.post<TranscriptionRecord>(`/asr/${transcriptionId}/timestamps`, body),
	batchSupplementTranscriptionTimestamps: (
		transcriptionIds: string[],
		body: { strategy?: 'auto' | 'forced_aligner' | 'qwen3-asr-mlx'; overwrite?: boolean } = {}
	) => api.post<TranscriptionRecord[]>('/asr/timestamps/batch', { transcription_ids: transcriptionIds, ...body }),
	deleteTranscription: (transcriptionId: string) => api.delete<{ status: string; transcription_id: string }>(`/asr/${transcriptionId}`),
	batchDeleteTranscriptions: (transcriptionIds: string[]) => api.post<{ status: string; deleted_ids: string[] }>('/asr/batch-delete', { transcription_ids: transcriptionIds }),
	transcriptionTasks: () => api.get<TranscriptionTask[]>('/asr/tasks'),
	transcriptionTask: (taskId: string) => api.get<TranscriptionTask>(`/asr/tasks/${taskId}`),
	cancelTranscriptionTask: (taskId: string) => api.post<{ status: string; task_id: string }>(`/asr/tasks/${taskId}/cancel`),
	retryTranscriptionTask: (taskId: string) => api.post<{ task_id: string; status: string }>(`/asr/tasks/${taskId}/retry`),
	deleteTranscriptionTask: (taskId: string) => api.delete<{ status: string; task_id: string }>(`/asr/tasks/${taskId}`),
	transcriptionHistory: () => api.get<TranscriptionRecord[]>('/asr/history'),
	predictEmotion: (voiceId: string) => api.post<SEREmotionResult>('/ser/predict', { voice_id: voiceId }),
	predictEmotionForFile: (fileId: string) => api.post<SEREmotionResult>('/ser/predict-file', { file_id: fileId }),
	batchPredictAllEmotions: () => api.post<{ results: SEREmotionResult[] }>('/ser/batch-predict', { all: true }),
	splitText: (text: string) => api.post<{ segments: string[] }>('/text-tools/split', { text }),
	cleanText: (text: string) => api.post<{ text: string }>('/text-tools/clean', { text }),
	normalizeNumbers: (text: string) => api.post<{ text: string }>('/text-tools/normalize-numbers', { text })
};
