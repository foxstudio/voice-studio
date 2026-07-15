import type { VideoLocalizationOperation } from '$lib/api/types';
import type { VideoLocalizationTrackId } from './studio-state';

export type ActivityTaskStatus = 'queued' | 'running' | 'success' | 'failed' | 'cancelled';

export type ActivityTaskStepStatus = 'todo' | 'running' | 'success' | 'failed' | 'cancelled';

export type ActivityTaskStep = {
	id: string;
	label: string;
	status: ActivityTaskStepStatus;
};

export type ActivityTaskScope = {
	trackIds: VideoLocalizationTrackId[];
	itemIds: string[];
	area: 'project' | 'timeline' | 'voice' | 'generate' | 'subtitle';
	exclusive: boolean;
};

export type ActivityTask = {
	id: string;
	operationId?: string;
	kind?: VideoLocalizationOperation['kind'];
	label: string;
	stage?: string;
	detail?: string;
	progress?: number | null;
	status: ActivityTaskStatus;
	scope?: ActivityTaskScope;
	cancellable?: boolean;
	cancelPending?: boolean;
	createdAt?: string | null;
	startedAt?: string | null;
	completedAt?: string | null;
	engineId?: string | null;
	sourceTrackId?: string | null;
	resultCount?: number | null;
	resultUnit?: string;
	steps?: ActivityTaskStep[];
};

const OPERATION_LABELS: Record<VideoLocalizationOperation['kind'], string> = {
	source_audio: '从视频提取原音轨',
	stems: '分离人声与背景音乐',
	english_asr: '从人声轨生成 ASR 字幕',
	reference_clips: '生成参考音候选'
};

export function asrSubtitleActionLabel(hasExistingSubtitles: boolean) {
	return hasExistingSubtitles ? '重新生成 ASR 字幕' : '从人声轨生成 ASR 字幕';
}

const FALLBACK_SCOPES: Record<VideoLocalizationOperation['kind'], ActivityTaskScope> = {
	source_audio: { trackIds: ['original'], itemIds: [], area: 'timeline', exclusive: true },
	stems: { trackIds: ['vocals', 'background'], itemIds: [], area: 'timeline', exclusive: true },
	english_asr: { trackIds: ['subtitles'], itemIds: [], area: 'subtitle', exclusive: true },
	reference_clips: { trackIds: [], itemIds: [], area: 'voice', exclusive: false }
};

const TRACK_IDS = new Set<VideoLocalizationTrackId>(['original', 'vocals', 'background', 'subtitles', 'localizedSubtitles', 'dub']);
const AREAS = new Set<ActivityTaskScope['area']>(['project', 'timeline', 'voice', 'generate', 'subtitle']);

const ASR_STEP_DEFINITIONS = [
	{ id: 'recognize', label: '识别人声内容', stages: ['准备处理', '识别人声'] },
	{ id: 'review', label: '校对识别文本', stages: ['校对识别', '文本校对'] },
	{ id: 'timestamps', label: '生成逐词时间码', stages: ['逐词时间码', '强制对齐'] },
	{ id: 'boundaries', label: '分析边界并复核断句', stages: ['声学边界', '字幕断句', '复核断句'] },
	{ id: 'subtitles', label: '生成字幕轨', stages: ['生成字幕轨'] }
] as const;

const TRACK_LABELS: Record<string, string> = {
	auto: '自动选择',
	original: '原始音轨',
	vocals: '人声音轨',
	background: '背景音轨',
	dub: '配音轨',
	subtitles: '字幕轨'
};

function stringValue(value: unknown): string | null {
	return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function numberValue(value: unknown): number | null {
	if (value === null || value === undefined || value === '') return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : null;
}

function asrCurrentStepIndex(operation: VideoLocalizationOperation, stage: string) {
	const previewPhase = operation.result_summary?.preview_phase;
	if (previewPhase === 'asr_draft') return 0;
	if (previewPhase === 'text_review') return 1;
	if (previewPhase === 'timing_segmentation') return 3;
	const matched = ASR_STEP_DEFINITIONS.findIndex((step) => step.stages.some((candidate) => stage.includes(candidate)));
	return matched >= 0 ? matched : 0;
}

function operationSteps(operation: VideoLocalizationOperation, stage: string): ActivityTaskStep[] | undefined {
	if (operation.kind !== 'english_asr') return undefined;
	const currentIndex = asrCurrentStepIndex(operation, stage);
	return ASR_STEP_DEFINITIONS.map((step, index) => {
		let status: ActivityTaskStepStatus = 'todo';
		if (operation.status === 'success') status = 'success';
		else if (operation.status === 'running') status = index < currentIndex ? 'success' : index === currentIndex ? 'running' : 'todo';
		else if (operation.status === 'failed') status = index < currentIndex ? 'success' : index === currentIndex ? 'failed' : 'todo';
		else if (operation.status === 'cancelled') status = index < currentIndex ? 'success' : index === currentIndex ? 'cancelled' : 'todo';
		return { id: step.id, label: step.label, status };
	});
}

function operationResult(operation: VideoLocalizationOperation) {
	if (operation.kind === 'english_asr') {
		return { count: numberValue(operation.result_summary?.cue_count ?? operation.result_summary?.segment_count), unit: '条字幕' };
	}
	if (operation.kind === 'reference_clips') {
		return { count: numberValue(operation.result_summary?.reference_clip_count), unit: '个候选' };
	}
	return { count: null, unit: '' };
}

export function operationActivityScope(operation: VideoLocalizationOperation): ActivityTaskScope {
	const rawScope = operation.parameters?.scope;
	if (!rawScope || typeof rawScope !== 'object' || Array.isArray(rawScope)) return FALLBACK_SCOPES[operation.kind];
	const scope = rawScope as Record<string, unknown>;
	const tracks = Array.isArray(scope.tracks) ? scope.tracks : [];
	const trackIds = tracks.flatMap((entry) => {
		if (typeof entry === 'string') return TRACK_IDS.has(entry as VideoLocalizationTrackId) ? [entry as VideoLocalizationTrackId] : [];
		if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return [];
		const item = entry as Record<string, unknown>;
		return item.role !== 'input' && TRACK_IDS.has(item.id as VideoLocalizationTrackId) ? [item.id as VideoLocalizationTrackId] : [];
	});
	const itemIds = Array.isArray(scope.items) ? scope.items.map(String).filter(Boolean) : [];
	const area = AREAS.has(scope.area as ActivityTaskScope['area']) ? scope.area as ActivityTaskScope['area'] : FALLBACK_SCOPES[operation.kind].area;
	return {
		trackIds: [...new Set(trackIds)],
		itemIds: [...new Set(itemIds)],
		area,
		exclusive: scope.exclusive !== false
	};
}

export function operationActivityTask(operation: VideoLocalizationOperation, cancelPending = false): ActivityTask {
	const stage = typeof operation.result_summary?.stage === 'string'
		? operation.result_summary.stage.trim()
		: '';
	const result = operationResult(operation);
	return {
		id: `operation:${operation.operation_id}`,
		operationId: operation.operation_id,
		kind: operation.kind,
		label: operation.label?.trim() || OPERATION_LABELS[operation.kind],
		stage: cancelPending || operation.cancel_requested
			? '正在取消，将在当前步骤结束后停止'
			: stage || activityTaskStatusLabel(operation.status),
		detail: operation.error_message ?? '',
		progress: operation.status === 'running' && operation.kind === 'english_asr'
			? Math.max(0, Math.min(1, operation.progress ?? 0))
			: null,
		status: operation.status,
		scope: operationActivityScope(operation),
		cancellable: (operation.status === 'queued' || operation.status === 'running') && operation.kind === 'english_asr' && !operation.cancel_requested,
		cancelPending: cancelPending || operation.cancel_requested,
		createdAt: operation.created_at,
		startedAt: operation.started_at,
		completedAt: operation.completed_at,
		engineId: stringValue(operation.result_summary?.engine_id) ?? stringValue(operation.parameters?.engine_id),
		sourceTrackId: stringValue(operation.result_summary?.source_track_id) ?? stringValue(operation.parameters?.source_track_id),
		resultCount: result.count,
		resultUnit: result.unit,
		steps: operationSteps(operation, stage)
	};
}

export function activityTaskStatusLabel(status: ActivityTaskStatus) {
	return {
		queued: '等待执行',
		running: '处理中',
		success: '已完成',
		failed: '处理失败',
		cancelled: '已取消'
	}[status];
}

export function isActiveActivityTask(task: ActivityTask) {
	return task.status === 'queued' || task.status === 'running';
}

export function activityTaskAffectsTrack(task: ActivityTask, trackId: VideoLocalizationTrackId, itemId?: string) {
	if (!isActiveActivityTask(task) || !task.scope?.exclusive) return false;
	if (itemId && task.scope.itemIds.length) return task.scope.itemIds.includes(itemId);
	return task.scope.trackIds.includes(trackId);
}

export function activityTaskProgress(task: ActivityTask): number | null {
	if (typeof task.progress !== 'number' || !Number.isFinite(task.progress)) return null;
	return Math.round(Math.max(0, Math.min(1, task.progress)) * 100);
}

export function activityTaskSourceLabel(trackId: string | null | undefined) {
	if (!trackId) return '';
	return TRACK_LABELS[trackId] ?? trackId;
}

export function activityTaskResultLabel(task: ActivityTask) {
	if (task.resultCount === null || task.resultCount === undefined) return '';
	return `${task.resultCount} ${task.resultUnit || '项结果'}`;
}

export function activityTaskDisplayName(task: ActivityTask) {
	return task.kind ? OPERATION_LABELS[task.kind] : task.label;
}

function validTimeMs(value: string | null | undefined) {
	if (!value) return null;
	const time = new Date(value).getTime();
	return Number.isFinite(time) ? time : null;
}

export function activityTaskElapsedMs(task: ActivityTask, nowMs = Date.now()) {
	const start = validTimeMs(task.startedAt) ?? validTimeMs(task.createdAt);
	if (start === null) return null;
	const end = isActiveActivityTask(task) ? nowMs : validTimeMs(task.completedAt);
	if (end === null) return null;
	return Math.max(0, end - start);
}

export function formatActivityTaskDuration(valueMs: number | null | undefined) {
	if (typeof valueMs !== 'number' || !Number.isFinite(valueMs) || valueMs < 0) return '';
	const totalSeconds = Math.max(0, Math.floor(valueMs / 1000));
	if (totalSeconds < 60) return `${totalSeconds} 秒`;
	const totalMinutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	if (totalMinutes < 60) return seconds ? `${totalMinutes} 分 ${seconds} 秒` : `${totalMinutes} 分`;
	const hours = Math.floor(totalMinutes / 60);
	const minutes = totalMinutes % 60;
	return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
}

export function formatActivityTaskTime(value: string | null | undefined) {
	if (!value) return '';
	const date = new Date(value);
	if (!Number.isFinite(date.getTime())) return '';
	const pad = (part: number) => String(part).padStart(2, '0');
	return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function activityTaskSummary(tasks: ActivityTask[]) {
	const primary = tasks[0] ?? null;
	if (!primary) return { primary: null, text: '', countLabel: '' };
	const percent = activityTaskProgress(primary);
	const stage = primary.stage?.trim();
	const status = percent === null ? (stage || activityTaskStatusLabel(primary.status)) : `${stage || '处理中'} · ${percent}%`;
	return {
		primary,
		text: `${activityTaskDisplayName(primary)} · ${status}`,
		countLabel: tasks.length > 1 ? `${tasks.length} 项运行中` : ''
	};
}
