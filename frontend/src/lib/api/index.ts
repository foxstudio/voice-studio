import { api } from './client';
import type {
	AppSettings,
	BatchTask,
	EngineDetail,
	ExportRecord,
	EvaluationReport,
	EngineAudioDiagnosis,
	GenerateRequest,
	GenerateResponse,
	GenerationTask,
	HistoryItem,
	PresetTemplate,
	Project,
	Role,
	ScriptSegment,
	TranscriptionRecord,
	TranscriptionTask,
	UploadResult,
	VoiceAsset,
	VoiceAssetCreate,
	VoiceSeed
} from './types';

export * from './client';
export type * from './types';

export const Api = {
	health: () => api.get<{ status: string; version: string; engines: Record<string, string>; uptime_seconds: number }>('/health'),
	settings: () => api.get<AppSettings>('/settings'),
	saveSettings: (settings: AppSettings) => api.patch<AppSettings>('/settings', settings),
	saveMimoSecret: (body: { api_key?: string | null; clear?: boolean }) => api.patch<AppSettings>('/settings/mimo-secret', body),
	engines: () => api.get<EngineDetail[]>('/engines'),
	startEngine: (id: string) => api.post<EngineDetail>(`/engines/${id}/start`),
	stopEngine: (id: string) => api.post<EngineDetail>(`/engines/${id}/stop`),
	healthEngine: (id: string) => api.post<Record<string, unknown>>(`/engines/${id}/health-check`),
	diagnoseEngineAudio: (id: string, body: { reference_audio_path?: string | null; voice_id?: string | null; text?: string }) => api.post<EngineAudioDiagnosis>(`/engines/${id}/diagnose-audio`, body),
	voices: () => api.get<VoiceAsset[]>('/voices'),
	createVoice: (voice: VoiceAssetCreate) => api.post<VoiceAsset>('/voices', voice),
	updateVoice: (id: string, voice: VoiceAssetCreate) => api.patch<VoiceAsset>(`/voices/${id}`, voice),
	deleteVoice: (id: string) => api.delete<{ status: string }>(`/voices/${id}`),
	uploadVoice: (file: File) => api.upload<UploadResult>('/voices/upload', file),
	generate: (body: GenerateRequest) => api.post<GenerateResponse>('/generate', body),
	generateBatch: (body: unknown) => api.post<BatchTask>('/batches/generate', body),
	batch: (id: string) => api.get<BatchTask>(`/batches/${id}`),
	tasks: () => api.get<GenerationTask[]>('/tasks'),
	task: (id: string) => api.get<GenerationTask>(`/tasks/${id}`),
	cancelTask: (id: string) => api.post<{ status: string }>(`/tasks/${id}/cancel`),
	retryTask: (id: string) => api.post<{ task_id: string; status: string }>(`/tasks/${id}/retry`),
	history: () => api.get<HistoryItem[]>('/history'),
	deleteHistory: (id: string) => api.delete<{ status: string }>(`/history/${id}`),
	presets: () => api.get<PresetTemplate[]>('/presets'),
	voiceSeeds: () => api.get<VoiceSeed[]>('/voice-seeds'),
	importVoiceSeed: (seed_id: string) => api.post<VoiceSeed>('/voice-seeds/import', { seed_id }),
	projects: () => api.get<Project[]>('/projects'),
	createProject: (name: string, description = '', default_engine_id: string | null = 'indextts-v2') => api.post<Project>('/projects', { name, description, default_engine_id }),
	deleteProject: (id: string) => api.delete<{ status: string }>(`/projects/${id}`),
	addRole: (id: string, role: Role) => api.post<Project>(`/projects/${id}/roles`, role),
	putSegments: (id: string, segments: ScriptSegment[]) => api.put<Project>(`/projects/${id}/segments`, segments),
	generateProject: (id: string) => api.post<{ task_ids: string[]; status: string }>(`/projects/${id}/generate`),
	exports: () => api.get<ExportRecord[]>('/exports'),
	latestEvaluation: () => api.get<EvaluationReport>('/evaluations/latest'),
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
	transcriptionHistory: () => api.get<TranscriptionRecord[]>('/asr/history'),
	splitText: (text: string) => api.post<{ segments: string[] }>('/text-tools/split', { text }),
	cleanText: (text: string) => api.post<{ text: string }>('/text-tools/clean', { text }),
	normalizeNumbers: (text: string) => api.post<{ text: string }>('/text-tools/normalize-numbers', { text })
};
