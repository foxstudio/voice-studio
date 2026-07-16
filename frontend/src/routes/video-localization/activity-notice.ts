import type { VideoLocalizationOperation } from '$lib/api/types';
import type { VideoLocalizationTrackId } from './studio-state';

export type ActivityTaskStatus = 'queued' | 'running' | 'success' | 'failed' | 'cancelled';

export type ActivityTaskStepStatus = 'todo' | 'running' | 'success' | 'failed' | 'cancelled';

export type ActivityTaskStepResultStatus = 'running' | 'success' | 'warning' | 'failed' | 'skipped';

export type ActivityTaskStepResultMetric = {
	label: string;
	value: string;
};

export type ActivityTaskStepResultItem = {
	title?: string;
	text?: string;
	before?: string;
	after?: string;
	beforeLabel?: string;
	afterLabel?: string;
	meta?: string;
	url?: string;
	tone?: 'positive' | 'warning' | 'muted' | 'neutral';
	facts: ActivityTaskStepResultFact[];
	links: ActivityTaskStepResultLink[];
	visual?: ActivityTaskStepResultVisual;
};

export type ActivityTaskStepResultFact = { label: string; value: string };
export type ActivityTaskStepResultLink = { title: string; url: string; meta?: string; text?: string };
export type ActivityTaskStepResultVisual = { label: string; value: number; max: number };

export type ActivityTaskStepResultSection = {
	title: string;
	items: ActivityTaskStepResultItem[];
};

export type ActivityTaskStepResult = {
	status: ActivityTaskStepResultStatus;
	summary: string;
	metrics: ActivityTaskStepResultMetric[];
	sections: ActivityTaskStepResultSection[];
	notes: string[];
};

export type ActivityTaskStep = {
	id: string;
	label: string;
	status: ActivityTaskStepStatus;
	durationMs?: number;
	roundCount?: number;
	batchCount?: number;
	result?: ActivityTaskStepResult;
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
	semanticModelId?: string | null;
	sourceTrackId?: string | null;
	resultCount?: number | null;
	resultUnit?: string;
	durationMs?: number | null;
	steps?: ActivityTaskStep[];
};

const OPERATION_LABELS: Record<VideoLocalizationOperation['kind'], string> = {
	source_audio: '从视频提取原音轨',
	stems: '分离人声与背景音乐',
	english_asr: '从人声轨生成 ASR 字幕',
	localization_draft: '生成本土化字幕初稿',
	reference_clips: '生成参考音候选'
};

export function asrSubtitleActionLabel(hasExistingSubtitles: boolean) {
	return hasExistingSubtitles ? '重新生成 ASR 字幕' : '从人声轨生成 ASR 字幕';
}

export function localizationSubtitleActionLabel(hasExistingSubtitles: boolean) {
	return hasExistingSubtitles ? '重新生成本土化字幕初稿' : '生成本土化字幕初稿';
}

const FALLBACK_SCOPES: Record<VideoLocalizationOperation['kind'], ActivityTaskScope> = {
	source_audio: { trackIds: ['original'], itemIds: [], area: 'timeline', exclusive: true },
	stems: { trackIds: ['vocals', 'background'], itemIds: [], area: 'timeline', exclusive: true },
	english_asr: { trackIds: ['subtitles'], itemIds: [], area: 'subtitle', exclusive: true },
	localization_draft: { trackIds: ['localizedSubtitles'], itemIds: [], area: 'subtitle', exclusive: true },
	reference_clips: { trackIds: [], itemIds: [], area: 'voice', exclusive: false }
};

const TRACK_IDS = new Set<VideoLocalizationTrackId>(['original', 'vocals', 'background', 'subtitles', 'localizedSubtitles', 'dub']);
const AREAS = new Set<ActivityTaskScope['area']>(['project', 'timeline', 'voice', 'generate', 'subtitle']);

const ASR_STEP_DEFINITIONS = [
	{ id: 'recognize', label: '识别人声内容', stages: ['准备处理', '识别人声'], timingStages: ['asr'] },
	{ id: 'research', label: '查证名称与背景', stages: ['判断是否需要联网核验', '联网核验'], timingStages: ['web_research'] },
	{ id: 'review', label: '校对识别文本', stages: ['校对识别', '文本校对'], timingStages: ['text_review'] },
	{ id: 'timestamps', label: '给每个词定位', stages: ['逐词时间码', '强制对齐'], timingStages: ['alignment'] },
	{ id: 'boundaries', label: '找出声音停顿', stages: ['声学边界'], timingStages: ['audio_boundaries'] },
	{ id: 'boundary-review', label: '检查字幕断句', stages: ['字幕断句', '复核断句'], timingStages: ['boundary_review'] },
	{ id: 'subtitles', label: '写入 ASR 字幕轨', stages: ['生成字幕轨'], timingStages: ['subtitle_track'] }
] as const;

const LOCALIZATION_STEP_DEFINITIONS = [
	{ id: 'prepare_context', label: '理解原文与人物', stages: ['prepare_context', '理解原文与人物'] },
	{ id: 'research', label: '查证文化与背景', stages: ['research', '查证文化与背景'] },
	{ id: 'localize', label: '生成中文表达', stages: ['localize', '生成中文表达'] },
	{ id: 'segment_timing', label: '安排字幕分段与时间', stages: ['segment_timing', '安排字幕分段与时间'] },
	{ id: 'quality_review', label: '复核语义与可读性', stages: ['quality_review', '复核语义与可读性'] },
	{ id: 'write_track', label: '写入本土化字幕轨', stages: ['write_track', '写入本土化字幕轨'] }
] as const;

const TRACK_LABELS: Record<string, string> = {
	auto: '自动选择',
	original: '原始音轨',
	vocals: '人声音轨',
	background: '背景音轨',
	dub: '配音轨',
	subtitles: 'ASR 字幕轨',
	localizedSubtitles: '本土化字幕轨'
};

function stringValue(value: unknown): string | null {
	return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function numberValue(value: unknown): number | null {
	if (value === null || value === undefined || value === '') return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : null;
}

function recordValue(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value)
		? value as Record<string, unknown>
		: null;
}

function stringList(value: unknown, limit = 8) {
	return Array.isArray(value)
		? value.map(stringValue).filter((item): item is string => item !== null).slice(0, limit)
		: [];
}

function stepResultStatus(value: unknown, fallback: ActivityTaskStepStatus): ActivityTaskStepResultStatus {
	if (value === 'success' || value === 'warning' || value === 'failed' || value === 'skipped' || value === 'running') return value;
	if (fallback === 'running') return 'running';
	if (fallback === 'failed' || fallback === 'cancelled') return 'failed';
	return 'success';
}

function normalizeStepResult(value: unknown, fallbackStatus: ActivityTaskStepStatus): ActivityTaskStepResult | null {
	const result = recordValue(value);
	if (!result) return null;
	const summary = stringValue(result.summary);
	if (!summary) return null;
	const metrics = Array.isArray(result.metrics)
		? result.metrics.flatMap((entry) => {
			const metric = recordValue(entry);
			const label = stringValue(metric?.label);
			const metricValue = stringValue(metric?.value) ?? numberValue(metric?.value)?.toString() ?? null;
			return label && metricValue !== null ? [{ label, value: metricValue }] : [];
		}).slice(0, 12)
		: [];
	const sections = Array.isArray(result.sections)
		? result.sections.flatMap((entry) => {
			const section = recordValue(entry);
			const title = stringValue(section?.title);
			if (!title || !Array.isArray(section?.items)) return [];
			const items = section.items.flatMap((rawItem) => {
				const item = recordValue(rawItem);
				if (!item) return [];
				const facts = Array.isArray(item.facts)
					? item.facts.flatMap((rawFact) => {
						const fact = recordValue(rawFact);
						const label = stringValue(fact?.label);
						const value = stringValue(fact?.value) ?? numberValue(fact?.value)?.toString() ?? null;
						return label && value !== null ? [{ label, value }] : [];
					}).slice(0, 12)
					: [];
				const links = Array.isArray(item.links)
					? item.links.flatMap((rawLink) => {
						const link = recordValue(rawLink);
						const title = stringValue(link?.title);
						const url = stringValue(link?.url);
						return title && url ? [{
							title,
							url,
							meta: stringValue(link?.meta) ?? undefined,
							text: stringValue(link?.text) ?? undefined
						}] : [];
					}).slice(0, 12)
					: [];
				const rawVisual = recordValue(item.visual);
				const visualLabel = stringValue(rawVisual?.label);
				const visualValue = numberValue(rawVisual?.value);
				const visualMax = numberValue(rawVisual?.max);
				const visual = visualLabel && visualValue !== null && visualMax !== null && visualMax > 0
					? { label: visualLabel, value: Math.min(visualValue, visualMax), max: visualMax }
					: undefined;
				const rawTone = stringValue(item.tone);
				const tone: ActivityTaskStepResultItem['tone'] = rawTone === 'positive' || rawTone === 'warning' || rawTone === 'muted' || rawTone === 'neutral'
					? rawTone
					: undefined;
				const normalized = {
					title: stringValue(item.title) ?? undefined,
					text: stringValue(item.text) ?? undefined,
					before: stringValue(item.before) ?? undefined,
					after: stringValue(item.after) ?? undefined,
					beforeLabel: stringValue(item.before_label) ?? stringValue(item.beforeLabel) ?? undefined,
					afterLabel: stringValue(item.after_label) ?? stringValue(item.afterLabel) ?? undefined,
					meta: stringValue(item.meta) ?? undefined,
					url: stringValue(item.url) ?? undefined,
					tone,
					facts,
					links,
					visual
				};
				return Object.values(normalized).some((entry) => Array.isArray(entry) ? entry.length > 0 : Boolean(entry)) ? [normalized] : [];
			}).slice(0, 50);
			return items.length ? [{ title, items }] : [];
		}).slice(0, 8)
		: [];
	return {
		status: stepResultStatus(result.status, fallbackStatus),
		summary,
		metrics,
		sections,
		notes: stringList(result.notes, 6)
	};
}

const TIMING_METRIC_LABELS: Record<string, string> = {
	segment_count: '原始片段',
	query_count: '搜索查询',
	source_count: '资料来源',
	cache_hits: '缓存命中',
	batch_count: '请求批次',
	word_count: '逐词时间码',
	boundary_count: '边界数量',
	refined_onset_count: '修正入点',
	candidate_count: '候选边界',
	round_count: '复核轮数',
	cue_count: '字幕数量'
};

function fallbackStepResult(
	stepLabel: string,
	status: ActivityTaskStepStatus,
	timing: Record<string, unknown> | null
): ActivityTaskStepResult | undefined {
	if (status === 'todo') return undefined;
	const metrics = Object.entries(TIMING_METRIC_LABELS).flatMap(([key, label]) => {
		const value = numberValue(timing?.[key]);
		return value === null ? [] : [{ label, value: value.toString() }];
	});
	const running = status === 'running';
	return {
		status: stepResultStatus(null, status),
		summary: running
			? `${stepLabel}正在处理，步骤完成后会补充可核验的结果。`
			: `该步骤已${status === 'success' ? '完成' : '结束'}。这条旧任务仅保留了状态和统计，没有保存详细产物。`,
		metrics,
		sections: [],
		notes: []
	};
}

function stageDurationMs(stageTimings: Record<string, unknown> | null, stageIds: readonly string[]) {
	if (!stageTimings || !stageIds.length) return null;
	const durations = stageIds.map((stageId) => numberValue(recordValue(stageTimings[stageId])?.duration_ms));
	if (durations.some((duration) => duration === null)) return null;
	return durations.reduce<number>((total, duration) => total + (duration ?? 0), 0);
}

function boundaryReviewCounts(stageTimings: Record<string, unknown> | null) {
	const timing = recordValue(stageTimings?.boundary_review);
	const rounds = Array.isArray(timing?.rounds) ? timing.rounds : null;
	const roundCount = numberValue(timing?.round_count) ?? (rounds ? rounds.length : null);
	const roundBatchCounts = rounds?.map((round) => numberValue(recordValue(round)?.batch_count)) ?? [];
	const batchCount = numberValue(timing?.batch_count)
		?? (roundBatchCounts.length && roundBatchCounts.every((count) => count !== null)
			? roundBatchCounts.reduce<number>((total, count) => total + (count ?? 0), 0)
			: null);
	return { roundCount, batchCount };
}

function asrCurrentStepIndex(operation: VideoLocalizationOperation, stage: string) {
	const matched = ASR_STEP_DEFINITIONS.findIndex((step) => step.stages.some((candidate) => stage.includes(candidate)));
	if (matched >= 0) return matched;
	const previewPhase = operation.result_summary?.preview_phase;
	if (previewPhase === 'asr_draft') return 0;
	if (previewPhase === 'text_review') return ASR_STEP_DEFINITIONS.findIndex((step) => step.id === 'review');
	if (previewPhase === 'timing_segmentation') return ASR_STEP_DEFINITIONS.findIndex((step) => step.id === 'boundaries');
	return 0;
}

function localizationCurrentStepIndex(stage: string) {
	const normalizedStage = stage.trim();
	const matched = LOCALIZATION_STEP_DEFINITIONS.findIndex((step) =>
		step.stages.some((candidate) => normalizedStage === candidate || normalizedStage.includes(candidate))
	);
	return matched >= 0 ? matched : 0;
}

function stepStatus(
	operationStatus: VideoLocalizationOperation['status'],
	stepIndex: number,
	currentIndex: number
): ActivityTaskStepStatus {
	if (operationStatus === 'success') return 'success';
	if (operationStatus === 'running') return stepIndex < currentIndex ? 'success' : stepIndex === currentIndex ? 'running' : 'todo';
	if (operationStatus === 'failed') return stepIndex < currentIndex ? 'success' : stepIndex === currentIndex ? 'failed' : 'todo';
	if (operationStatus === 'cancelled') return stepIndex < currentIndex ? 'success' : stepIndex === currentIndex ? 'cancelled' : 'todo';
	return 'todo';
}

function localizationOperationSteps(operation: VideoLocalizationOperation, stage: string): ActivityTaskStep[] {
	const currentIndex = localizationCurrentStepIndex(stage);
	const taskStageTimings = recordValue(operation.result_summary?.task_stage_timings);
	const rawStepResults = recordValue(operation.result_summary?.task_step_results);
	return LOCALIZATION_STEP_DEFINITIONS.map((step, index) => {
		const status = stepStatus(operation.status, index, currentIndex);
		const timing = recordValue(taskStageTimings?.[step.id]);
		const durationMs = numberValue(timing?.duration_ms);
		const result = normalizeStepResult(rawStepResults?.[step.id], status)
			?? fallbackStepResult(step.label, status, timing);
		return {
			id: step.id,
			label: step.label,
			status,
			...(durationMs === null ? {} : { durationMs }),
			...(result ? { result } : {})
		};
	});
}

function operationSteps(operation: VideoLocalizationOperation, stage: string): ActivityTaskStep[] | undefined {
	if (operation.kind === 'localization_draft') return localizationOperationSteps(operation, stage);
	if (operation.kind !== 'english_asr') return undefined;
	const currentIndex = asrCurrentStepIndex(operation, stage);
	const diagnosticStageTimings = recordValue(operation.result_summary?.stage_timings);
	const taskStageTimings = recordValue(operation.result_summary?.task_stage_timings);
	const stageTimings = taskStageTimings ?? diagnosticStageTimings;
	const rawStepResults = recordValue(operation.result_summary?.task_step_results);
	const boundaryCounts = boundaryReviewCounts(diagnosticStageTimings);
	const legacyMeasuredDurations = ASR_STEP_DEFINITIONS
		.filter((step) => step.id !== 'subtitles')
		.flatMap((step) => step.timingStages)
		.map((stageId) => numberValue(recordValue(diagnosticStageTimings?.[stageId])?.duration_ms));
	const measuredStageDuration = legacyMeasuredDurations.reduce<number>((total, duration) => total + (duration ?? 0), 0);
	const operationDuration = numberValue(operation.result_summary?.duration_ms);
	const legacySubtitleTrackDuration = operationDuration === null || legacyMeasuredDurations.some((duration) => duration === null)
		? null
		: Math.max(0, operationDuration - measuredStageDuration);
	return ASR_STEP_DEFINITIONS.map((step, index) => {
		const status = stepStatus(operation.status, index, currentIndex);
		const recordedDurationMs = stageDurationMs(stageTimings, step.timingStages);
		const durationMs = step.id === 'subtitles' && recordedDurationMs === null
			? legacySubtitleTrackDuration
			: recordedDurationMs;
		const timing = step.timingStages.length === 1 ? recordValue(diagnosticStageTimings?.[step.timingStages[0]]) : null;
		const result = normalizeStepResult(rawStepResults?.[step.timingStages[0]], status)
			?? fallbackStepResult(step.label, status, timing);
		return {
			id: step.id,
			label: step.label,
			status,
			...(durationMs === null ? {} : { durationMs }),
			...(step.id === 'boundary-review' && boundaryCounts.roundCount !== null
				? { roundCount: boundaryCounts.roundCount }
				: {}),
			...(step.id === 'boundary-review' && boundaryCounts.batchCount !== null
				? { batchCount: boundaryCounts.batchCount }
				: {}),
			...(result ? { result } : {})
		};
	});
}

function operationResult(operation: VideoLocalizationOperation) {
	if (operation.kind === 'english_asr') {
		return { count: numberValue(operation.result_summary?.cue_count ?? operation.result_summary?.segment_count), unit: '条字幕' };
	}
	if (operation.kind === 'localization_draft') {
		return { count: numberValue(operation.result_summary?.localized_subtitle_count), unit: '条字幕' };
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
	const stageId = stringValue(operation.result_summary?.stage_id) ?? stage;
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
		progress: operation.status === 'running' && (operation.kind === 'english_asr' || operation.kind === 'localization_draft')
			? Math.max(0, Math.min(1, operation.progress ?? 0))
			: null,
		status: operation.status,
		scope: operationActivityScope(operation),
		cancellable: (operation.status === 'queued' || operation.status === 'running')
			&& (operation.kind === 'english_asr' || operation.kind === 'localization_draft')
			&& !operation.cancel_requested,
		cancelPending: cancelPending || operation.cancel_requested,
		createdAt: operation.created_at,
		startedAt: operation.started_at,
		completedAt: operation.completed_at,
		engineId: stringValue(operation.result_summary?.engine_id) ?? stringValue(operation.parameters?.engine_id),
		semanticModelId: stringValue(operation.result_summary?.llm_model_id),
		sourceTrackId: stringValue(operation.result_summary?.source_track_id) ?? stringValue(operation.parameters?.source_track_id),
		resultCount: result.count,
		resultUnit: result.unit,
		durationMs: numberValue(
			operation.result_summary?.task_duration_ms
			?? (operation.kind === 'english_asr' || operation.kind === 'localization_draft'
				? operation.result_summary?.duration_ms
				: null)
		),
		steps: operationSteps(operation, stageId)
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
	if (!isActiveActivityTask(task) && typeof task.durationMs === 'number' && Number.isFinite(task.durationMs)) {
		return Math.max(0, task.durationMs);
	}
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

export function activityTaskStepTimingLabel(step: ActivityTaskStep, task?: ActivityTask, nowMs = Date.now()) {
	let durationMs = step.durationMs;
	if (step.status === 'running' && task) {
		const taskElapsedMs = activityTaskElapsedMs(task, nowMs);
		const completedStepMs = (task.steps ?? [])
			.filter((candidate) => candidate.status === 'success')
			.reduce((total, candidate) => total + (candidate.durationMs ?? 0), 0);
		if (taskElapsedMs !== null) durationMs = Math.max(durationMs ?? 0, taskElapsedMs - completedStepMs);
	}
	const duration = formatActivityTaskDuration(durationMs);
	const counts = [
		step.roundCount && step.roundCount > 0 ? `${step.roundCount} 轮` : '',
		step.batchCount && step.batchCount > 0 ? `${step.batchCount} 批` : ''
	].filter(Boolean);
	if (duration) return [duration, ...counts].join(' · ');
	return counts.join(' · ');
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
