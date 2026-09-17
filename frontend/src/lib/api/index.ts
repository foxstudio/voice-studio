import { api } from './client';
import type {
	AppSettings,
	BatchTask,
	CommunityVoicePack,
	CloudConnectionTestResponse,
	CloudProviderId,
	CodexCliAuthActionResponse,
	CodexCliStatus,
	DoubaoCloudRefreshResponse,
	DoubaoCloudVoiceListResponse,
	DoubaoVoiceCloneResponse,
	EngineDetail,
	EngineInstallation,
	AsrSelectionStatus,
	EngineGenerateRequest,
	EngineSpeaker,
	DoubaoSpeakerCatalogStatus,
	ExportRecord,
	EngineAudioDiagnosis,
	GeneratePlanRequest,
	GeneratePlanResponse,
	GenerateRequest,
	GenerateResponse,
	GenerationTask,
	HistoryItem,
	HistoryPage,
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
	ProjectSummary,
	ProjectUpdate,
	ProjectTranscriptionImportResponse,
	Role,
	ScriptSegment,
	TranscriptionHistoryPage,
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
	VideoLocalizationWorkspaceReadModel,
	VideoLocalizationWorkspaceDetail,
	VideoLocalizationWorkspaceDetailSection,
	VideoLocalizationWorkspaceRevision,
	VideoLocalizationTimelineProjection,
	VideoLocalizationTimelineEditPatchRequest,
	VideoLocalizationTimelineEditPatchResponse,
	VideoLocalizationTimelineMutationResponse,
	VideoLocalizationMutationAckResponse,
	VideoLocalizationUiStatePatchResponse,
	VideoLocalizationCue,
	VideoLocalizationSubtitleCue,
	VideoLocalizationExport,
	VideoLocalizationCueUpdate,
	VideoLocalizationDevelopmentAsrResult,
	VideoLocalizationDevelopmentInitialAnalysisResult,
	VideoLocalizationDevelopmentEntityNormalizationResult,
	VideoLocalizationDevelopmentReviewDecisionsResult,
	VideoLocalizationDevelopmentWholeRecheckResult,
	VideoLocalizationDevelopmentTranscriptQualityGateResult,
	VideoLocalizationOperation,
	VideoLocalizationAsrOperationRequest,
	VideoLocalizationLocalizationOperationRequest,
	VideoLocalizationDubSubtitleOperationRequest,
	VideoLocalizationDubSubtitleReviewRequest,
	VideoLocalizationOperationFeedV2,
	VideoLocalizationSubtitleImportRequest,
	VideoLocalizationSubtitleCueUpdate,
	VideoLocalizationTtsHistoryDeleteRequest,
	VideoLocalizationTtsHistoryDeleteResponse,
	VideoLocalizationTtsTask,
	VideoLocalizationTtsTaskFeed,
	VideoLocalizationTtsParameterPack,
	DubbingGenerationPlan,
	DubbingGenerationPlanInput,
	DubbingProductionSnapshot,
	DubbingRecoveryDecision,
	DubbingGroupPreflightResult,
	DubbingProductionRunSnapshot,
	DubbingProductionExecuteResponse,
	DubbingProductionExecutionOptions,
	DubbingProductionGroupFailureRequest,
	DubbingCandidateCqcReport,
	DubbingCandidateContentEvidenceRequest,
	DubbingCandidateContentEvidenceResponse,
	DubbingCandidateReviewCommand,
	DubbingManualTimingDeferralRequest,
	DubbingManualTimingDeferralResponse,
	DubbingSemanticBoundaryAudit,
	VideoPreviewCacheStatus,
	VideoPlaybackProxyRequest,
	VideoPlaybackProxyStatus,
	VoiceAsset,
	VoiceAssetCreate,
	VoiceClipResponse,
	VoiceClipTranscribeResponse,
	SEREmotionResult,
	SeedAudioImageUploadResult,
	VoiceAssetUpdate,
	VoiceSeed
} from './types';

export * from './client';
export type * from './types';

export const Api = {
	health: () => api.get<{
		status: string;
		version: string;
		engines: Record<string, string>;
		uptime_seconds: number;
		runtime_ready: boolean;
		optional_runtime_ready: boolean;
		runtime_capabilities: Record<string, boolean>;
		platform_capabilities: {
			schema_version: number;
			operating_system: string;
			architecture: string;
			python_version: string;
			available_devices: string[];
			preferred_device: string;
			frameworks: Record<string, boolean>;
			framework_devices?: Record<string, string[]>;
			optional_capabilities: Record<string, boolean>;
		};
	}>('/health', { timeoutMs: 3500 }),
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
	codexCliStatus: () => api.get<CodexCliStatus>('/settings/codex-cli/status'),
	loginCodexCli: () => api.post<CodexCliAuthActionResponse>('/settings/codex-cli/login'),
	logoutCodexCli: () => api.post<CodexCliAuthActionResponse>('/settings/codex-cli/logout'),
	reloginCodexCli: () => api.post<CodexCliAuthActionResponse>('/settings/codex-cli/relogin'),
	webSearchSettings: () => api.get<WebSearchSettings>('/settings/web-search'),
	saveWebSearchSettings: (body: WebSearchSettingsUpdate) => api.put<WebSearchSettings>('/settings/web-search', body),
	testWebSearch: () => api.post<WebSearchTestResponse>('/settings/web-search/test'),
	engines: () => api.get<EngineDetail[]>('/engines'),
	engineInstallations: () => api.get<EngineInstallation[]>('/engines/installations'),
	asrSelection: () => api.get<AsrSelectionStatus>('/engines/asr-selection'),
	installEngineModel: (id: string, acceptedLicenseId?: string) =>
		api.post<EngineInstallation>(
			`/engines/installations/${encodeURIComponent(id)}/install`,
			acceptedLicenseId ? { accepted_license_id: acceptedLicenseId } : undefined
		),
	uninstallEngineModel: (id: string) =>
		api.delete<EngineInstallation>(`/engines/installations/${encodeURIComponent(id)}`),
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
	clipVoice: (fileId: string, body: { start_ms: number; end_ms: number }) =>
		api.post<VoiceClipResponse>(`/voices/files/${encodeURIComponent(fileId)}/clip`, body),
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
	retryLongformFailed: (id: string, confirmCloudReplay = false) =>
		api.post<LongformTask>(
			`/longform/${id}/retry-failed`,
			{ confirm_cloud_replay: confirmCloudReplay }
		),
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
	retryTask: (id: string, confirmCloudReplay = false) =>
		api.post<{ task_id: string; status: string }>(
			`/tasks/${id}/retry`,
			{ confirm_cloud_replay: confirmCloudReplay }
		),
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
	historyPage: (params?: { limit?: number; offset?: number; project_id?: string; segment_id?: string; source?: string }) => {
		const query = new URLSearchParams();
		if (params?.limit !== undefined) query.set('limit', String(params.limit));
		if (params?.offset !== undefined) query.set('offset', String(params.offset));
		if (params?.project_id) query.set('project_id', params.project_id);
		if (params?.segment_id) query.set('segment_id', params.segment_id);
		if (params?.source) query.set('source', params.source);
		return api.get<HistoryPage>(`/history/page${query.size ? `?${query.toString()}` : ''}`);
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
	project: (id: string) => api.get<Project>(`/projects/${encodeURIComponent(id)}`),
	projectSummaries: (kind?: 'script' | 'video_localization') => {
		const suffix = kind ? `?kind=${encodeURIComponent(kind)}` : '';
		return api.get<ProjectSummary[]>(`/projects/summaries${suffix}`);
	},
	syncVideoLocalizationProjects: () => api.post<Project[]>('/projects/video-localization/sync-projects'),
	syncVideoLocalizationProjectSummaries: () =>
		api.post<ProjectSummary[]>('/projects/video-localization/sync-project-summaries'),
	createProject: (name: string, description = '', default_engine_id: string | null = 'indextts-v2') => api.post<Project>('/projects', { name, description, default_engine_id }),
	updateProject: (id: string, patch: ProjectUpdate) => api.patch<Project>(`/projects/${id}`, patch),
	deleteProject: (id: string) => api.delete<{ status: string; cleanup_status?: 'pending' }>(
		`/projects/${id}`,
		{ timeoutMs: 30_000 }
	),
	addRole: (id: string, role: Role) => api.post<Project>(`/projects/${id}/roles`, role),
	putSegments: (id: string, segments: ScriptSegment[]) => api.put<Project>(`/projects/${id}/segments`, segments),
	videoLocalizationDraft: (id: string) => api.get<VideoLocalizationDraft>(`/projects/${id}/video-localization`),
	videoLocalizationWorkspace: (id: string) =>
		api.get<VideoLocalizationWorkspaceReadModel>(
			`/projects/${id}/video-localization/workspace`
		),
	videoLocalizationWorkspaceDetail: (
		id: string,
		section: VideoLocalizationWorkspaceDetailSection
	) => api.get<VideoLocalizationWorkspaceDetail>(
		`/projects/${id}/video-localization/workspace-details/${section}`,
		{ timeoutMs: 30_000 }
	),
	videoLocalizationWorkspaceRevision: (id: string) =>
		api.get<VideoLocalizationWorkspaceRevision>(
			`/projects/${id}/video-localization/workspace-revision`,
			{ timeoutMs: 8_000 }
		),
	videoLocalizationTimelineProjection: (id: string) =>
		api.get<VideoLocalizationTimelineProjection>(
			`/projects/${id}/video-localization/timeline-projection`,
			{ timeoutMs: 8_000 }
		),
	autoNameVideoLocalizationProject: (id: string) => api.post<Project>(`/projects/${id}/video-localization/auto-name`),
	saveVideoLocalizationDraft: (id: string, draft: VideoLocalizationDraft) => api.put<VideoLocalizationDraft>(
		`/projects/${id}/video-localization`,
		draft,
		{ timeoutMs: 120_000 }
	),
	saveVideoLocalizationWorkspace: (id: string, draft: VideoLocalizationDraft) => api.put<VideoLocalizationDraft>(
		`/projects/${id}/video-localization/workspace`,
		draft,
		{ timeoutMs: 120_000 }
	),
	updateVideoLocalizationUiState: (id: string, patch: Record<string, unknown>) => api.patch<VideoLocalizationUiStatePatchResponse>(
		`/projects/${id}/video-localization/ui-state`,
		patch,
		{ timeoutMs: 120_000 }
	),
	updateVideoLocalizationTimelineEdit: (id: string, patch: VideoLocalizationTimelineEditPatchRequest) =>
		api.patch<VideoLocalizationTimelineEditPatchResponse>(
			`/projects/${id}/video-localization/timeline-edit`,
			patch,
			{ timeoutMs: 30_000 }
		),
	resetVideoLocalizationDraft: (id: string) => api.delete<VideoLocalizationDraft>(`/projects/${id}/video-localization`),
	openVideoLocalizationProjectDirectory: (id: string) => api.post<StorageOpenResponse>(`/projects/${id}/video-localization/open-directory`),
	importVideoLocalizationSource: (id: string, file: File) => api.upload<VideoLocalizationMutationAckResponse>(`/projects/${id}/video-localization/source-media`, file),
	videoPreviewCacheStatus: (id: string) => api.get<VideoPreviewCacheStatus>(`/projects/${id}/video-localization/source-media/preview-cache`, { timeoutMs: 8_000 }),
	buildVideoPreviewCache: (id: string, body: { start_ms?: number; end_ms?: number; full?: boolean } = {}) =>
		api.post<VideoPreviewCacheStatus>(`/projects/${id}/video-localization/source-media/preview-cache`, body),
	refreshVideoPreviewCache: (id: string) =>
		api.post<VideoPreviewCacheStatus>(`/projects/${id}/video-localization/source-media/preview-cache/refresh`, undefined, { timeoutMs: 120_000 }),
	prepareVideoLocalizationPreviewVideo: (id: string, body: VideoPlaybackProxyRequest) =>
		api.post<VideoPlaybackProxyStatus>(
			`/projects/${id}/video-localization/source-media/preview-video`,
			body
		),
	prepareVideoLocalizationSourceAudioPreview: (id: string) =>
		api.post<{ changed?: boolean; profile?: string; variant?: 'source' | 'preview' }>(
			`/projects/${id}/video-localization/source-media/audio-preview`
		),
	prepareVideoLocalizationStemAudioPreview: (id: string, kind: 'vocals' | 'background') =>
		api.post<{ changed?: boolean; profile?: string; variant?: 'source' | 'preview' }>(
			`/projects/${id}/video-localization/stems/${kind}/audio-preview`
		),
	videoLocalizationOperationFeedV2: (
		id: string,
		options: {
			afterRevision?: number;
			cursor?: string;
			historyLimit?: number;
		} = {}
	) => {
		const query = new URLSearchParams();
		if (options.afterRevision !== undefined) {
			query.set('after_revision', String(options.afterRevision));
		}
		if (options.cursor) query.set('cursor', options.cursor);
		if (options.historyLimit !== undefined) {
			query.set('history_limit', String(options.historyLimit));
		}
		return api.get<VideoLocalizationOperationFeedV2>(
			`/projects/${id}/video-localization/operations/feed-v2${query.size ? `?${query.toString()}` : ''}`,
			{ timeoutMs: 8_000 }
		);
	},
	videoLocalizationOperation: (id: string, operationId: string) => api.get<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/${operationId}`),
	videoLocalizationDevelopmentAsrResult: (id: string, operationId: string) =>
		api.get<VideoLocalizationDevelopmentAsrResult>(
			`/projects/${id}/video-localization/operations/${encodeURIComponent(operationId)}/development-asr-result`
		),
	videoLocalizationDevelopmentInitialAnalysisResult: (id: string, operationId: string) =>
		api.get<VideoLocalizationDevelopmentInitialAnalysisResult>(
			`/projects/${id}/video-localization/operations/${encodeURIComponent(operationId)}/development-initial-analysis-result`
		),
	videoLocalizationDevelopmentEntityNormalizationResult: (id: string, operationId: string) =>
		api.get<VideoLocalizationDevelopmentEntityNormalizationResult>(
			`/projects/${id}/video-localization/operations/${encodeURIComponent(operationId)}/development-entity-normalization-result`
		),
	videoLocalizationDevelopmentReviewDecisionsResult: (id: string, operationId: string) =>
		api.get<VideoLocalizationDevelopmentReviewDecisionsResult>(
			`/projects/${id}/video-localization/operations/${encodeURIComponent(operationId)}/development-review-decisions-result`
		),
	videoLocalizationDevelopmentWholeRecheckResult: (id: string, operationId: string) =>
		api.get<VideoLocalizationDevelopmentWholeRecheckResult>(
			`/projects/${id}/video-localization/operations/${encodeURIComponent(operationId)}/development-whole-recheck-result`
		),
	videoLocalizationDevelopmentTranscriptQualityGateResult: (id: string, operationId: string) =>
		api.get<VideoLocalizationDevelopmentTranscriptQualityGateResult>(
			`/projects/${id}/video-localization/operations/${encodeURIComponent(operationId)}/development-transcript-quality-gate-result`
		),
	submitVideoLocalizationOperation: (id: string, kind: VideoLocalizationOperation['kind'], parameters: Record<string, unknown> = {}) =>
		api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations`, { kind, parameters }),
	submitVideoLocalizationAsrOperation: (id: string, request: VideoLocalizationAsrOperationRequest) =>
		api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/english-asr`, request),
	submitVideoLocalizationLocalizationOperation: (id: string, request: VideoLocalizationLocalizationOperationRequest) =>
		api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/localization`, request),
	submitVideoLocalizationDubSubtitleOperation: (id: string, request: VideoLocalizationDubSubtitleOperationRequest) =>
		api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/dub-subtitles`, request),
	reviewVideoLocalizationDubSubtitles: (id: string, request: VideoLocalizationDubSubtitleReviewRequest) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/dub-subtitles/review`, request),
	cancelVideoLocalizationOperation: (id: string, operationId: string) => api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/${operationId}/cancel`),
	retryVideoLocalizationOperation: (id: string, operationId: string) => api.post<VideoLocalizationOperation>(`/projects/${id}/video-localization/operations/${operationId}/retry`),
	createVideoLocalizationSpeaker: (id: string, body: VideoLocalizationSpeakerCreate) => api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/speakers`, body),
	updateVideoLocalizationSpeaker: (id: string, speakerId: string, body: VideoLocalizationSpeakerUpdate) =>
		api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/speakers/${speakerId}`, body),
	updateVideoLocalizationCue: (id: string, cueId: string, body: VideoLocalizationCueUpdate) => api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/cues/${cueId}`, body),
	deleteVideoLocalizationCue: (id: string, cueId: string) =>
		api.delete<VideoLocalizationDraft>(`/projects/${id}/video-localization/cues/${encodeURIComponent(cueId)}`),
	mergeVideoLocalizationCues: (id: string, body: { cue_ids: string[]; survivor_cue_id?: string | null }) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/cues/merge`, body),
	splitVideoLocalizationCue: (id: string, cueId: string, body: { replacements: VideoLocalizationCue[] }) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/cues/${encodeURIComponent(cueId)}/split`, body),
	updateVideoLocalizationLocalizedSubtitle: (id: string, subtitleId: string, body: VideoLocalizationSubtitleCueUpdate) =>
		api.patch<VideoLocalizationDraft>(`/projects/${id}/video-localization/localized-subtitles/${subtitleId}`, body),
	editVideoLocalizationLocalizedSubtitle: (id: string, subtitleId: string, body: VideoLocalizationSubtitleCueUpdate) =>
		api.patch<VideoLocalizationTimelineMutationResponse>(`/projects/${id}/video-localization/localized-subtitles/${encodeURIComponent(subtitleId)}/edit`, body),
	deleteVideoLocalizationLocalizedSubtitle: (id: string, subtitleId: string) =>
		api.delete<VideoLocalizationDraft>(`/projects/${id}/video-localization/localized-subtitles/${encodeURIComponent(subtitleId)}`),
	splitVideoLocalizationLocalizedSubtitle: (id: string, subtitleId: string, body: { children: VideoLocalizationSubtitleCue[]; source_word_ids_by_subtitle_id?: Record<string, string[]> | null }) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/localized-subtitles/${encodeURIComponent(subtitleId)}/split`, body),
	videoLocalizationDubbingSnapshot: (id: string) =>
		api.get<DubbingProductionSnapshot>(`/projects/${id}/video-localization/dubbing/snapshot`),
	videoLocalizationDubbingPreflight: (id: string, groupId: string, speed = 1.25) =>
        api.get<DubbingGroupPreflightResult>(`/projects/${id}/video-localization/dubbing/groups/${encodeURIComponent(groupId)}/preflight?speed=${speed}`),
    recoverVideoLocalizationDubbing: (id: string, body: DubbingRecoveryDecision) =>
        api.post<DubbingProductionExecuteResponse>(`/projects/${id}/video-localization/dubbing/recovery`, body),
	videoLocalizationDubbingProductionRun: (id: string) =>
		api.get<DubbingProductionRunSnapshot>(`/projects/${id}/video-localization/dubbing/production-run`),
	readVideoLocalizationCandidateSemanticBoundaries: (id: string, candidateId: string) =>
		api.get<DubbingSemanticBoundaryAudit>(`/projects/${id}/video-localization/dubbing/candidates/${encodeURIComponent(candidateId)}/semantic-boundaries`),
	submitVideoLocalizationCandidateSemanticBoundaries: (id: string, candidateId: string, body: DubbingCandidateReviewCommand) =>
		api.post<DubbingCandidateCqcReport>(`/projects/${id}/video-localization/dubbing/candidates/${encodeURIComponent(candidateId)}/semantic-boundaries/review`, body),
	acquireVideoLocalizationCandidateContentEvidence: (id: string, candidateId: string, body: DubbingCandidateContentEvidenceRequest) =>
		api.post<DubbingCandidateContentEvidenceResponse>(`/projects/${id}/video-localization/dubbing/candidates/${encodeURIComponent(candidateId)}/content-evidence`, body),
	executeVideoLocalizationDubbingProductionRun: (
		id: string,
		scope: 'single_group' | 'all_remaining',
		groupId?: string | null,
		options?: DubbingProductionExecutionOptions
	) => api.post<DubbingProductionExecuteResponse>(
		`/projects/${id}/video-localization/dubbing/production-run/execute`,
		{
			schema_version: 'dubbing-production-execute-v1',
			scope,
			...(groupId ? { group_id: groupId } : {}),
			...(options ?? {})
		}
	),
	deferVideoLocalizationDubbingGroupForManualTiming: (id: string, body: DubbingManualTimingDeferralRequest) =>
		api.post<DubbingManualTimingDeferralResponse>(`/projects/${id}/video-localization/dubbing/production-run/manual-timing-deferral`, body),
	recordVideoLocalizationDubbingGroupFailure: (id: string, body: DubbingProductionGroupFailureRequest) =>
		api.post<VideoLocalizationDraft>(`/projects/${id}/video-localization/dubbing/production-run/failure`, body),
	createVideoLocalizationDubbingPlan: (id: string, body: DubbingGenerationPlanInput) =>
		api.post<DubbingGenerationPlan>(`/projects/${id}/video-localization/dubbing/plan`, body),
	applyVideoLocalizationHistoryToTimelineClip: (id: string, clipId: string, resultId: string, requestId: string) =>
		api.post<VideoLocalizationTimelineMutationResponse>(`/projects/${id}/video-localization/timeline-clips/${encodeURIComponent(clipId)}/history/${encodeURIComponent(resultId)}/apply`, { request_id: requestId }),
	applyVideoLocalizationHistoryToTimeline: (id: string, resultId: string, body: { request_id: string; segment_id: string; clip_id?: string | null; new_clip_id?: string | null; start_ms?: number | null; dub_lane?: number | null; force_new?: boolean }) =>
		api.post<VideoLocalizationTimelineMutationResponse>(`/projects/${id}/video-localization/timeline-clips/history/${encodeURIComponent(resultId)}/apply`, body),
	prepareVideoLocalizationTtsParameterPack: (id: string, body: { target_subtitle_ids: string[]; source_cue_ids?: string[] }) =>
		api.post<VideoLocalizationTtsParameterPack>(`/projects/${id}/video-localization/tts/parameter-pack`, body),
	prepareVideoLocalizationTtsHandoff: (id: string, segmentId: string, body: { submission_id?: string | null; history_result_id?: string | null; parameters?: Record<string, unknown> | null; timeline_clip_id?: string | null; target_subtitle_ids: string[]; source_cue_ids?: string[] }) =>
		api.post<GenerateRequest>(`/projects/${id}/video-localization/tts/handoff/${encodeURIComponent(segmentId)}`, body),
	reserveVideoLocalizationTtsHandoff: (id: string, segmentId: string, body: { submission_id?: string | null; history_result_id?: string | null; parameters?: Record<string, unknown> | null; timeline_clip_id?: string | null; target_subtitle_ids: string[]; source_cue_ids?: string[] }) =>
		api.post<VideoLocalizationTtsTask>(`/projects/${id}/video-localization/tts/handoff-reserve/${encodeURIComponent(segmentId)}`, body),
	previewVideoLocalizationTtsHandoff: (id: string, segmentId: string, body: { history_result_id?: string | null; parameters?: Record<string, unknown> | null; timeline_clip_id?: string | null; target_subtitle_ids: string[]; source_cue_ids?: string[] }) =>
		api.post<GenerateRequest>(
			`/projects/${id}/video-localization/tts/handoff-preview/${encodeURIComponent(segmentId)}`,
			body,
			{ timeoutMs: 120_000 }
		),
	videoLocalizationTtsTasks: (id: string) =>
		api.get<VideoLocalizationTtsTask[]>(`/projects/${id}/video-localization/tts/tasks`),
	videoLocalizationTtsTaskFeed: (id: string, afterRevision?: string | null) =>
		api.get<VideoLocalizationTtsTaskFeed>(
			`/projects/${id}/video-localization/tts/tasks/feed${afterRevision ? `?after_revision=${encodeURIComponent(afterRevision)}` : ''}`
		),
	videoLocalizationTtsTask: (id: string, workflowId: string) =>
		api.get<VideoLocalizationTtsTask>(`/projects/${id}/video-localization/tts/tasks/${encodeURIComponent(workflowId)}`),
	cancelVideoLocalizationTtsTask: (id: string, workflowId: string) =>
		api.post<VideoLocalizationTtsTask>(`/projects/${id}/video-localization/tts/tasks/${encodeURIComponent(workflowId)}/cancel`),
	deleteVideoLocalizationTtsTask: (id: string, workflowId: string) =>
		api.delete<VideoLocalizationDraft>(`/projects/${id}/video-localization/tts/tasks/${encodeURIComponent(workflowId)}`),
	cleanupUnusedVideoLocalizationTtsHistory: (id: string, segmentId?: string | null) =>
		api.post<{ removed_records: number; removed_files: number; kept_used_records: number }>(
			`/projects/${id}/video-localization/tts/history/cleanup-unused`,
			segmentId ? { segment_id: segmentId } : {}
		),
	deleteVideoLocalizationTtsHistory: (
		id: string,
		body: VideoLocalizationTtsHistoryDeleteRequest
	) =>
		api.post<VideoLocalizationTtsHistoryDeleteResponse>(
			`/projects/${id}/video-localization/tts/history/delete`,
			body
		),
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
	transcriptionHistory: (params?: { limit?: number; offset?: number }) => {
		const search = new URLSearchParams();
		if (params?.limit !== undefined) search.set('limit', String(params.limit));
		if (params?.offset !== undefined) search.set('offset', String(params.offset));
		return api.get<TranscriptionHistoryPage>(`/asr/history${search.size ? `?${search.toString()}` : ''}`);
	},
	predictEmotion: (voiceId: string) => api.post<SEREmotionResult>('/ser/predict', { voice_id: voiceId }),
	predictEmotionForFile: (fileId: string) => api.post<SEREmotionResult>('/ser/predict-file', { file_id: fileId }),
	batchPredictAllEmotions: () => api.post<{ results: SEREmotionResult[] }>('/ser/batch-predict', { all: true }),
	splitText: (text: string) => api.post<{ segments: string[] }>('/text-tools/split', { text }),
	cleanText: (text: string) => api.post<{ text: string }>('/text-tools/clean', { text }),
	normalizeNumbers: (text: string) => api.post<{ text: string }>('/text-tools/normalize-numbers', { text })
};
