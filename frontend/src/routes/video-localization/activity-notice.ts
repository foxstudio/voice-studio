import type { VideoLocalizationOperation } from '$lib/api/types';
import type { VideoLocalizationTrackId } from './studio-state';

export type ActivityTaskStatus = 'queued' | 'running' | 'needs_attention' | 'success' | 'failed' | 'cancelled';

export type ActivityTaskStepStatus = 'todo' | 'running' | 'success' | 'failed' | 'cancelled';

export type ActivityTaskStepResultStatus = 'todo' | 'running' | 'success' | 'warning' | 'failed' | 'skipped' | 'not_needed';
export type ActivityTaskStepAttentionKind = 'advisory' | 'manual_review';

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
	openByDefault?: boolean;
};

export type ActivityTaskStepResultCoverage = {
	mode: 'complete' | 'focused' | 'summary';
	shownCount: number;
	totalCount: number;
	unit: string;
	reason?: string;
};

export type ActivityTaskStepResultDocument = {
	title: string;
	format: 'markdown' | 'text';
	content: string;
};

export type ActivityTaskStepResult = {
	status: ActivityTaskStepResultStatus;
	attentionKind?: ActivityTaskStepAttentionKind;
	detailMode?: 'workflow_summary';
	purpose?: string;
	summary: string;
	metrics: ActivityTaskStepResultMetric[];
	sections: ActivityTaskStepResultSection[];
	notes: string[];
	coverage?: ActivityTaskStepResultCoverage;
	reviewTargets?: ActivityTaskReviewTarget[];
	document?: ActivityTaskStepResultDocument;
	debug?: ActivityTaskStepDebug;
};

export type ActivityTaskStepDebug = {
	description?: string;
	metrics: ActivityTaskStepResultMetric[];
	sections: ActivityTaskStepResultSection[];
	notes: string[];
};

export type ActivityTaskStep = {
	id: string;
	label: string;
	description?: string;
	execution?: 'parallel' | 'serial' | 'join';
	dependsOn?: string[];
	dependencyMode?: 'all' | 'latest_completed';
	optional?: boolean;
	status: ActivityTaskStepStatus;
	durationMs?: number;
	startedElapsedMs?: number;
	roundCount?: number;
	batchCount?: number;
	result?: ActivityTaskStepResult;
};

export type ActivityTaskStage = {
	id: string;
	label: string;
	description?: string;
	status: ActivityTaskStepStatus;
	durationMs?: number;
	layout?: 'linear' | 'parallel-join';
	steps: ActivityTaskStep[];
};

export type ActivityTaskScope = {
	trackIds: VideoLocalizationTrackId[];
	itemIds: string[];
	area: 'project' | 'timeline' | 'voice' | 'generate' | 'subtitle' | 'development' | 'delivery';
	exclusive: boolean;
};

export type ActivityTask = {
	id: string;
	operationId?: string;
	workflowId?: string;
	kind?: VideoLocalizationOperation['kind'];
	label: string;
	stage?: string;
	detail?: string;
	progress?: number | null;
	status: ActivityTaskStatus;
	scope?: ActivityTaskScope;
	cancellable?: boolean;
	deletable?: boolean;
	cancelPending?: boolean;
	actionPending?: 'cancel' | 'delete' | 'retry';
	createdAt?: string | null;
	startedAt?: string | null;
	completedAt?: string | null;
	engineId?: string | null;
	semanticModelId?: string | null;
	sourceTrackId?: string | null;
	resultCount?: number | null;
	resultUnit?: string;
	durationMs?: number | null;
	executionScope?: 'full' | 'partial';
	detailAvailable?: boolean;
	summaryFacts?: ActivityTaskStepResultMetric[];
	stages?: ActivityTaskStage[];
	steps?: ActivityTaskStep[];
	finalResult?: ActivityTaskStepResult;
	failureResult?: ActivityTaskStepResult;
};

export type ActivityTaskReviewTarget = {
	title: string;
	location?: string;
	detail: string;
	excerpt?: string;
	startMs?: number;
	endMs?: number;
};

const LOCALIZATION_ADVISORY_STEP_IDS = new Set([
	'transcript_quality_gate',
	'collect_localization_research_evidence_v3',
	'collect_localization_visual_evidence_v3',
	'adjudicate_localization_evidence_v3',
	'review_localization_fidelity',
	'review_localization_naturalness',
	'finalize_localization_spoken_script',
	'adjudicate_localization_alignment',
	'validate_localization_tracks'
]);

const OPERATION_LABELS: Record<VideoLocalizationOperation['kind'], string> = {
	source_audio: '从视频提取原音轨',
	stems: '分离人声与背景音乐',
	english_asr: '从人声轨生成 ASR 字幕',
	speaker_diarization: '说话人区分（开发单步）',
	localization_draft: '生成本土化字幕',
	dub_subtitle_generation: '根据合成配音生成字幕',
	reference_clips: '生成参考音候选',
	semantic_tts_grouping: '按语义组合配音字幕',
	media_export: '导出成品'
};

export function asrSubtitleActionLabel(hasExistingSubtitles: boolean) {
	return hasExistingSubtitles ? '重新生成 ASR 字幕' : '从人声轨生成 ASR 字幕';
}

export function localizationSubtitleActionLabel(hasExistingSubtitles: boolean) {
	return hasExistingSubtitles ? '重新生成本土化字幕' : '生成本土化字幕';
}

const FALLBACK_SCOPES: Record<VideoLocalizationOperation['kind'], ActivityTaskScope> = {
	source_audio: { trackIds: ['original'], itemIds: [], area: 'timeline', exclusive: true },
	stems: { trackIds: ['vocals', 'background'], itemIds: [], area: 'timeline', exclusive: true },
	english_asr: { trackIds: ['subtitles'], itemIds: [], area: 'subtitle', exclusive: true },
	speaker_diarization: { trackIds: [], itemIds: [], area: 'development', exclusive: true },
	localization_draft: { trackIds: ['localizedSubtitles'], itemIds: [], area: 'subtitle', exclusive: true },
	dub_subtitle_generation: { trackIds: ['dub', 'localizedSubtitles'], itemIds: [], area: 'subtitle', exclusive: true },
	reference_clips: { trackIds: [], itemIds: [], area: 'voice', exclusive: false },
	semantic_tts_grouping: { trackIds: [], itemIds: [], area: 'subtitle', exclusive: false },
	media_export: { trackIds: [], itemIds: [], area: 'delivery', exclusive: false }
};

export function pendingOperationActivityTask(
	kind: VideoLocalizationOperation['kind'],
	id: string,
	stage = '正在提交任务'
): ActivityTask {
	return {
		id,
		kind,
		label: OPERATION_LABELS[kind],
		stage,
		progress: null,
		status: 'queued',
		scope: FALLBACK_SCOPES[kind],
		cancellable: false,
		createdAt: new Date().toISOString(),
		...(kind === 'localization_draft' || kind === 'english_asr' ? {
			steps: (
				kind === 'localization_draft'
					? LOCALIZATION_LANGUAGE_FLOW_STEPS
					: ASR_REQUIRED_FLOW_STEPS
			).map((step) => ({
				id: step.id,
				label: step.label,
				status: 'todo' as const
			}))
		} : {})
	};
}

const TRACK_IDS = new Set<VideoLocalizationTrackId>(['original', 'vocals', 'background', 'subtitles', 'localizedSubtitles', 'dub']);
const AREAS = new Set<ActivityTaskScope['area']>(['project', 'timeline', 'voice', 'generate', 'subtitle', 'development', 'delivery']);

type DevelopmentAsrMode =
	| 'raw_asr'
	| 'initial_analysis'
	| 'document_understanding'
	| 'research'
	| 'visual_evidence'
	| 'entity_normalization'
	| 'section_review'
	| 'review_decisions'
	| 'whole_recheck'
	| 'transcript_quality_gate';

function developmentAsrMode(operation: VideoLocalizationOperation): DevelopmentAsrMode | null {
	if (operation.kind !== 'english_asr') return null;
	const executionMode = stringValue(operation.parameters?.execution_mode);
	const stopAfterStep = stringValue(operation.parameters?.stop_after_step);
	if (executionMode === 'stop_after' && stopAfterStep === 'asr') return 'raw_asr';
	if (executionMode === 'stop_after' && stopAfterStep === 'initial_analysis') return 'initial_analysis';
	if (executionMode === 'stop_after' && stopAfterStep === 'understand_document') return 'document_understanding';
	if (executionMode === 'stop_after' && stopAfterStep === 'research') return 'research';
	if (executionMode === 'stop_after' && stopAfterStep === 'visual_evidence') return 'visual_evidence';
	if (executionMode === 'stop_after' && stopAfterStep === 'normalize_entities') return 'entity_normalization';
	if (executionMode === 'stop_after' && stopAfterStep === 'section_review_r1') return 'section_review';
	if (executionMode === 'stop_after' && stopAfterStep === 'review_decisions_r1') return 'review_decisions';
	if (executionMode === 'stop_after' && stopAfterStep === 'whole_recheck_r1') return 'whole_recheck';
	if (executionMode === 'stop_after' && stopAfterStep === 'transcript_quality_gate') return 'transcript_quality_gate';
	if (stringValue(operation.result_summary?.execution_scope) !== 'partial') return null;
	const stageId = stringValue(operation.result_summary?.stage_id);
	if (stageId === 'initial_analysis') return 'initial_analysis';
	if (stageId === 'understand_document') return 'document_understanding';
	if (stageId === 'research') return 'research';
	if (stageId === 'visual_evidence') return 'visual_evidence';
	if (stageId === 'normalize_entities') return 'entity_normalization';
	if (stageId === 'section_review_r1') return 'section_review';
	if (stageId === 'review_decisions_r1') return 'review_decisions';
	if (stageId === 'whole_recheck_r1') return 'whole_recheck';
	if (stageId === 'transcript_quality_gate') return 'transcript_quality_gate';
	return 'raw_asr';
}

function isPartialAsrOperation(operation: VideoLocalizationOperation) {
	return developmentAsrMode(operation) !== null;
}

type RawAsrDisplaySegment = {
	segmentId: string;
	startMs: number | null;
	endMs: number | null;
	text: string;
};

type RawAsrQualitySummary = {
	status: 'passed' | 'warning' | 'failed';
	hasText: boolean;
	hasSegments: boolean;
	timestampsMonotonic: boolean;
	incompleteRangeCount: number;
	trailingGapMs: number | null;
	warningCodes: string[];
};

function normalizedRawAsrQuality(value: unknown): RawAsrQualitySummary | null {
	const quality = recordValue(value);
	const status = stringValue(quality?.status);
	if (!quality || (status !== 'passed' && status !== 'warning' && status !== 'failed')) return null;
	return {
		status,
		hasText: quality.has_text === true,
		hasSegments: quality.has_segments === true,
		timestampsMonotonic: quality.timestamps_monotonic === true,
		incompleteRangeCount: numberValue(quality.incomplete_range_count) ?? 0,
		trailingGapMs: numberValue(quality.trailing_gap_ms),
		warningCodes: stringList(quality.warning_codes)
	};
}

function partialAsrQualityNote(quality: RawAsrQualitySummary | null, totalCount: number) {
	if (!quality) return '当前任务尚未记录完整性自检信息。';
	if (quality.status === 'passed') {
		const trailing = quality.trailingGapMs === null
			? ''
			: `；末段距音频结束 ${formatActivityTimelineDuration(quality.trailingGapMs)}`;
		return `原始听写完整性检查通过：${totalCount} 个片段均有内容、时间顺序正常，未发现未完成区间${trailing}。`;
	}
	const issues = [
		...(!quality.hasText ? ['听写全文为空'] : []),
		...(!quality.hasSegments ? ['没有生成听写片段'] : []),
		...(!quality.timestampsMonotonic ? ['片段时间顺序异常'] : []),
		...(quality.incompleteRangeCount > 0 ? [`有 ${quality.incompleteRangeCount} 个区间未完成`] : []),
		...(quality.warningCodes.includes('leading_gap_review_required') ? ['开头存在较长未覆盖区间'] : []),
		...(quality.warningCodes.includes('terminal_fragment_review_required') ? ['结尾存在较长未覆盖区间'] : []),
		...(quality.warningCodes.includes('media_end_clipped') ? ['末段时间超过音频长度'] : [])
	];
	return `原始听写完整性检查${quality.status === 'failed' ? '未通过' : '需要留意'}：${issues.join('；') || '发现需要人工确认的完整性提醒'}。`;
}

function partialAsrStepResult({
	language,
	totalCount,
	sampleCount,
	sampleRatio,
	quality,
	segments,
	contextNotes = [],
	downstreamNote = '后续全文复核、时间对齐、断句和字幕轨均未执行。'
}: {
	language: string;
	totalCount: number;
	sampleCount: number | null;
	sampleRatio: number | null;
	quality: RawAsrQualitySummary | null;
	segments: RawAsrDisplaySegment[];
	contextNotes?: string[];
	downstreamNote?: string;
}): ActivityTaskStepResult {
	const shownCount = sampleCount ?? segments.length;
	const previewPercent = sampleRatio !== null
		? Math.min(100, Math.round(sampleRatio * 100))
		: totalCount > 0
			? Math.min(100, Math.round(shownCount / totalCount * 100))
		: 0;
	const items = segments.map((segment) => ({
		title: segment.segmentId || '原始片段',
		text: segment.text,
		facts: segment.startMs !== null && segment.endMs !== null
			? [{ label: '粗时间', value: formatActivityTimelineRange(segment.startMs, segment.endMs) }]
			: [],
		links: []
	}));
	const defaultItems = items.slice(0, 3);
	const expandedItems = items.slice(3);
	return {
		status: quality?.status === 'failed'
			? 'failed'
			: quality?.status === 'warning'
				? 'warning'
				: 'success',
		purpose: '把人声音频转换为未经后续校对的原始文本和粗时间片段。',
		summary: shownCount > defaultItems.length
			? `已生成 ${totalCount} 个原始语音片段，本次从全文抽查 ${shownCount} 个样例，默认展示其中 ${defaultItems.length} 个。`
			: `已生成 ${totalCount} 个原始语音片段，本次从全文抽查 ${shownCount} 个样例。`,
		metrics: [
			{ label: '原始片段', value: String(totalCount) },
			{ label: '抽查比例', value: `${previewPercent}%` },
			...(language ? [{ label: '识别语言', value: language }] : [])
		],
		sections: [
			...(defaultItems.length ? [{
				title: '全文抽查样例',
				items: defaultItems,
				openByDefault: true
			}] : []),
			...(expandedItems.length ? [{
				title: '展开详情',
				items: expandedItems,
				openByDefault: false
			}] : [])
		],
		notes: [
			partialAsrQualityNote(quality, totalCount),
			`本次从全文 ${totalCount} 个原始片段中抽查 ${shownCount} 个，覆盖约 ${previewPercent}%；样例随机分布在全文不同位置。`,
			...contextNotes,
			downstreamNote
		],
		coverage: {
			mode: 'summary',
			shownCount,
			totalCount,
			unit: '个原始片段',
			reason: `本次从全文随机抽查 ${shownCount} 个原始片段。`
		}
	};
}

function rawAsrDisplaySegments(value: unknown): RawAsrDisplaySegment[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((entry) => {
		const segment = recordValue(entry);
		const text = stringValue(segment?.text);
		if (!text) return [];
		return [{
			segmentId: stringValue(segment?.segment_id) ?? '原始片段',
			startMs: numberValue(segment?.start_ms),
			endMs: numberValue(segment?.end_ms),
			text
		}];
	});
}

const ASR_REQUIRED_FLOW_STEPS = [
	{ id: 'asr', label: '生成原始听写', order: 10 },
	{ id: 'understand_document', label: '理解全文并规划复查', order: 30 },
	{ id: 'research', label: '核对名称与背景', order: 40 },
	{ id: 'normalize_entities', label: '统一名称与术语', order: 45 },
	{ id: 'section_review_r1', label: '定位听写疑点', order: 50 },
	{ id: 'review_decisions_r1', label: '重听并应用明确修改', order: 60 },
	{ id: 'whole_recheck_r1', label: '本地收尾检查', order: 70 },
	{ id: 'transcript_quality_gate', label: '进入校时前检查', order: 290 },
	{ id: 'alignment', label: '对齐逐词时间', order: 300 },
	{ id: 'audio_boundaries', label: '分析声音停顿', order: 310 },
	{ id: 'boundary_review', label: '本地确定字幕断句', order: 320 },
	{ id: 'subtitle_track', label: '生成并检查字幕轨', order: 330 }
] as const;

const ASR_INITIAL_ANALYSIS_FLOW_STEPS = [
	{ id: 'asr', label: '生成原始听写', order: 10 },
	{ id: 'diarization', label: '区分说话人', order: 20 },
	{ id: 'initial_analysis_join', label: '汇合听写与说话人', order: 25 }
] as const;

const MANAGED_RAW_ASR_FLOW_STEPS = [
	{ id: 'asr', label: '生成并校验原始听写', order: 10 }
] as const;

const LOCALIZATION_LANGUAGE_FLOW_STEPS = [
	{ id: 'lock_localization_source', label: '固定本次英文源数据', order: 10 },
	{ id: 'lock_localization_context_intent', label: '固定本次本土化要求', order: 20 },
	{ id: 'analyze_localization_document', label: '建立全文本土化创作提纲', order: 30 },
	{ id: 'collect_localization_research_evidence_v3', label: '查询必要资料', order: 40 },
	{ id: 'collect_localization_visual_evidence_v3', label: '查看必要画面', order: 41 },
	{ id: 'adjudicate_localization_evidence_v3', label: '确认资料与画面结论', order: 50 },
	{ id: 'generate_localization_spoken_script', label: '生成全文本土化初稿', order: 60 },
	{ id: 'review_localization_fidelity', label: '复核原意与事实', order: 70 },
	{ id: 'review_localization_naturalness', label: '盲测中文自然度', order: 71 },
	{ id: 'finalize_localization_spoken_script', label: '本土化台词终审', order: 80 },
	{ id: 'align_localization_semantics', label: '本地映射语义时间', order: 90 },
	{ id: 'adjudicate_localization_alignment', label: '复核时间歧义', order: 100 },
	{ id: 'build_localization_dual_tracks', label: '生成台词轨与上屏字幕', order: 110 },
	{ id: 'validate_localization_tracks', label: '检查本土化结果', order: 120 },
	{ id: 'commit_localization_tracks', label: '保存正式本土化双轨', order: 130 }
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

function decimalValue(value: unknown): number | null {
	if (value === null || value === undefined || value === '') return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function recordValue(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value)
		? value as Record<string, unknown>
		: null;
}

function stringList(value: unknown, limit = 50) {
	return Array.isArray(value)
		? value.map(stringValue).filter((item): item is string => item !== null).slice(0, limit)
		: [];
}

function stringValues(value: unknown, limit = 50) {
	const single = stringValue(value);
	return single ? [single] : stringList(value, limit);
}

const ERROR_LIMIT_LABELS: Record<string, string> = {
	max_cps: '阅读速度上限',
	target_cps: '目标阅读速度',
	max_duration_ms: '最长时长',
	min_duration_ms: '最短时长',
	max_visible_chars: '字幕字数上限',
	max_chars: '字幕字数上限',
	max_chars_per_line: '每行字数上限',
	max_lines: '最多行数',
	max_items: '单次处理上限'
};

function errorLimitLabel(key: string) {
	return ERROR_LIMIT_LABELS[key] ?? key.replaceAll('_', ' ');
}

function formattedNumber(value: number, maximumFractionDigits = 2) {
	return new Intl.NumberFormat('zh-CN', { maximumFractionDigits }).format(value);
}

function errorValueLabel(key: string, value: unknown): string | null {
	const number = decimalValue(value);
	if (number !== null) {
		if (key.endsWith('_ms')) return formatActivityTimelineDuration(number);
		if (key.includes('cps')) return `${formattedNumber(number)} 字/秒`;
		return formattedNumber(number);
	}
	const text = stringValue(value);
	if (text) return text;
	if (typeof value === 'boolean') return value ? '是' : '否';
	return null;
}

function operationErrorItem(value: unknown, index: number): ActivityTaskStepResultItem | null {
	const item = recordValue(value);
	if (!item) return null;
	const id = stringValue(item.id ?? item.subtitle_id ?? item.localized_subtitle_id ?? item.cue_id);
	const cueIds = [
		...stringValues(item.source_cue_ids),
		...stringValues(item.source_cue_id),
		...stringValues(item.cue_ids),
		...stringValues(item.cue_id)
	].filter((cueId, cueIndex, values) => values.indexOf(cueId) === cueIndex);
	const segmentIds = [
		...stringValues(item.segment_ids),
		...stringValues(item.segment_id)
	].filter((segmentId, segmentIndex, values) => values.indexOf(segmentId) === segmentIndex);
	const subtitleIds = stringValues(item.subtitle_ids ?? item.localized_subtitle_ids);
	const severity = stringValue(item.severity);
	const category = stringValue(item.category);
	const suggestion = stringValue(item.suggestion);
	const text = stringValue(item.text ?? item.localized_text ?? item.display_text ?? item.content ?? item.reason);
	const durationMs = decimalValue(item.duration_ms);
	const visibleChars = numberValue(item.visible_chars ?? item.char_count ?? item.reading_units);
	const cps = decimalValue(item.cps);
	const violations = stringValues(item.violations ?? item.violation ?? item.errors);
	const maxChars = numberValue(item.max_chars_for_duration ?? item.max_visible_chars ?? item.max_chars);
	const suggestedSegments = numberValue(item.suggested_min_segments);
	const facts: ActivityTaskStepResultFact[] = [
		...(subtitleIds.length ? [{ label: '涉及字幕', value: subtitleIds.join('、') }] : []),
		...(segmentIds.length ? [{ label: 'ASR 片段', value: segmentIds.join('、') }] : []),
		...(cueIds.length ? [{ label: '原文 cue', value: cueIds.join('、') }] : []),
		...(severity ? [{ label: '问题级别', value: severity }] : []),
		...(category ? [{ label: '问题类型', value: category }] : []),
		...(durationMs === null ? [] : [{ label: '时长', value: formatActivityTimelineDuration(durationMs) }]),
		...(visibleChars === null ? [] : [{ label: '字数', value: `${visibleChars} 字` }]),
		...(cps === null ? [] : [{ label: 'CPS', value: `${formattedNumber(cps)} 字/秒` }]),
		...(maxChars === null ? [] : [{ label: '当前时长建议上限', value: `${maxChars} 字` }]),
		...(suggestedSegments === null || suggestedSegments <= 1 ? [] : [{ label: '建议分段', value: `至少 ${suggestedSegments} 段` }]),
		...(violations.length ? [{ label: '问题', value: violations.join('；') }] : []),
		...(suggestion ? [{ label: '修改建议', value: suggestion }] : [])
	];
	if (!id && !text && !facts.length) return null;
	return {
		title: id ? `字幕 ${id}` : `问题 ${index + 1}`,
		text: text ?? undefined,
		meta: cueIds.length ? cueIds.join('、') : segmentIds.length ? segmentIds.join('、') : undefined,
		tone: 'warning',
		facts,
		links: []
	};
}

function operationErrorAdvice(detail: Record<string, unknown>, items: ActivityTaskStepResultItem[]) {
	const explicit = [
		...stringValues(detail.advice),
		...stringValues(detail.suggestions),
		...stringValues(detail.handling_suggestions)
	];
	const violations = items.flatMap((item) => item.facts.find((fact) => fact.label === '问题')?.value.split('；') ?? []);
	const inferred = [
		...(violations.some((item) => item.includes('阅读速度') || item.includes('精简'))
			? ['优先精简对应字幕的中文表达，保留事实、数字和语气重点后再重试。']
			: []),
		...(violations.some((item) => item.includes('拆分') || item.includes('时长') || item.includes('字'))
			? ['需要拆分时，请在完整语义边界处分段，并保持字幕时间连续、不重叠。']
			: []),
		...(violations.some((item) => item.includes('数字') || item.includes('单位'))
			? ['对照原文恢复数字、单位和符号，不要用近似说法替代。']
			: [])
	];
	const fallback = items.length
		? ['逐项修正后重新生成本土化字幕；若原文或时间码有误，请先修正 ASR 字幕。']
		: ['根据详情中的错误统计和限制修正输入内容，然后使用原参数重试任务。'];
	const defaultAdvice = explicit.length || inferred.length ? [] : fallback;
	return [...new Set([...explicit, ...inferred, ...defaultAdvice])].slice(0, 8);
}

function safeDiagnosticNotes(value: unknown): string[] {
	return stringValue(value)?.match(/（安全诊断：[^）]*）/g) ?? [];
}

function withoutSafeDiagnostics(value: string): string {
	return value.replace(/（安全诊断：[^）]*）/g, '').trim();
}

export function normalizeOperationErrorDetail(errorMessage: string | null | undefined, value: unknown): ActivityTaskStepResult | undefined {
	const detail = recordValue(value);
	const plainError = normalizeResultDisplayText(errorMessage) ?? undefined;
	const diagnosticNotes = [...new Set([...safeDiagnosticNotes(errorMessage), ...safeDiagnosticNotes(detail?.message ?? detail?.summary)])];
	const debug = diagnosticNotes.length ? {
		description: '本机调用的安全诊断，不包含原始输入或模型回复。',
		metrics: [], sections: [], notes: diagnosticNotes
	} : undefined;
	if (!detail || !Object.keys(detail).length) {
		if (!plainError) return undefined;
		return {
			status: 'failed',
			purpose: '说明任务在哪一步停止、为什么停止，以及修正后应该如何继续。',
			summary: plainError,
			...(debug ? { debug } : {}),
			metrics: [],
			sections: [],
			notes: ['修正上方原因后重新提交或重试任务。']
		};
	}
	const rawItems = [detail.items, detail.remaining, detail.issues]
		.find((entries): entries is unknown[] => Array.isArray(entries) && entries.length > 0) ?? [];
	const items = rawItems.flatMap((item, index) => {
		const normalized = operationErrorItem(item, index);
		return normalized ? [normalized] : [];
	});
	const count = numberValue(detail.count) ?? (items.length ? items.length : null);
	const remainingCount = Array.isArray(detail.remaining)
		? detail.remaining.length
		: numberValue(detail.remaining ?? detail.remaining_count);
	const limits = recordValue(detail.limits);
	const limitMetrics = limits
		? Object.entries(limits).flatMap(([key, rawValue]) => {
			const formatted = errorValueLabel(key, rawValue);
			return formatted === null ? [] : [{ label: errorLimitLabel(key), value: formatted }];
		})
		: [];
	const metrics: ActivityTaskStepResultMetric[] = [
		...(count === null ? [] : [{ label: '问题条目', value: count.toString() }]),
		...(remainingCount === null || remainingCount === count ? [] : [{ label: '仍需处理', value: remainingCount.toString() }]),
		...limitMetrics
	];
	const summary = plainError
		?? normalizeResultDisplayText(detail.message ?? detail.summary)
		?? '任务失败，详情中保留了可定位的问题。';
	const notes = operationErrorAdvice(detail, items);
	const totalCount = count ?? items.length;
	return {
		status: 'failed',
		purpose: items.length
			? '定位导致任务中止的具体字幕，并提供可执行的修正方向。'
			: '定位导致任务中止的原因，并提供可执行的处理方向。',
		summary,
		...(debug ? { debug } : {}),
		metrics,
		sections: items.length ? [{ title: '错误条目', items }] : [],
		notes,
		...(items.length && totalCount > 0 ? {
			coverage: {
				mode: items.length < totalCount ? 'focused' : 'complete',
				shownCount: items.length,
				totalCount,
				unit: '条字幕',
				...(items.length < totalCount ? { reason: '后端仅保留了部分错误条目；总数以任务统计为准。' } : {})
			}
		} : {})
	};
}

function stepResultStatus(value: unknown, fallback: ActivityTaskStepStatus): ActivityTaskStepResultStatus {
	if (value === 'todo' || value === 'success' || value === 'warning' || value === 'failed' || value === 'skipped' || value === 'not_needed' || value === 'running') return value;
	if (value === 'cancelled') return 'skipped';
	if (fallback === 'todo') return 'todo';
	if (fallback === 'running') return 'running';
	if (fallback === 'failed' || fallback === 'cancelled') return 'failed';
	return 'success';
}

function normalizeResultDisplayText(value: unknown) {
	const rawText = stringValue(value);
	const text = rawText ? withoutSafeDiagnostics(rawText) : null;
	if (!text) return null;
	const qualityIssueLabels: Record<string, string> = {
		LOCALIZED_SUBTITLE_CPS_HIGH: '阅读速度偏快',
		LOCALIZED_SUBTITLE_DURATION_ABOVE_TARGET: '单条字幕时间偏长',
		LOCALIZED_SUBTITLE_DURATION_BELOW_TARGET: '单条字幕时间偏短',
		LOCALIZED_SUBTITLE_WORD_COVERAGE_INVALID: '英文逐词时间覆盖不完整',
		LOCALIZED_SUBTITLE_CRITICAL_NUMBER_CHANGED: '关键数字发生变化',
		LOCALIZED_SUBTITLE_SOURCE_TIME_DISCONNECTED: '字幕与原文时间没有交集',
		LOCALIZED_SUBTITLE_TEXT_COVERAGE_INVALID: '锁定中文没有完整保留'
	};
	if (qualityIssueLabels[text]) return qualityIssueLabels[text];
	return formatActivityTimelineText(text)
		.replace(/[。.!！?？]+；/g, '；')
		.replace(/([。.!！?？；;，,])。+(?=\s|$)/g, '$1')
		.replace(/([。！？!?；;，,])\1+/g, '$1');
}

function safeDebugText(value: string) {
	if (/^(?:\/|~\/|[A-Za-z]:[\\/])/.test(value.trim())) return '已记录（本地路径不在界面展示）';
	return value;
}

function normalizeStepResult(
	value: unknown,
	fallbackStatus: ActivityTaskStepStatus
): ActivityTaskStepResult | null {
	const result = recordValue(value);
	if (!result) return null;
	const summary = normalizeResultDisplayText(result.summary);
	if (!summary) return null;
	const purpose = normalizeResultDisplayText(result.purpose);
	const metrics = Array.isArray(result.metrics)
		? result.metrics.flatMap((entry) => {
			const metric = recordValue(entry);
			const label = stringValue(metric?.label);
			const metricValue = stringValue(metric?.value) ?? numberValue(metric?.value)?.toString() ?? null;
			return label && metricValue !== null ? [{ label, value: metricValue }] : [];
		})
		: [];
	let omittedItemCount = 0;
	const normalizedSections = Array.isArray(result.sections)
		? result.sections.flatMap((entry) => {
			const section = recordValue(entry);
			const title = stringValue(section?.title);
			if (!title || !Array.isArray(section?.items)) return [];
			const rawItems = section.items;
			const visibleRawItems = rawItems.slice(0, 500);
			omittedItemCount += Math.max(0, rawItems.length - visibleRawItems.length);
			const items = visibleRawItems.flatMap((rawItem) => {
				const item = recordValue(rawItem);
				if (!item) return [];
				const facts = Array.isArray(item.facts)
					? item.facts.flatMap((rawFact) => {
						const fact = recordValue(rawFact);
						const label = stringValue(fact?.label);
						const value = stringValue(fact?.value) ?? numberValue(fact?.value)?.toString() ?? null;
						return label && value !== null ? [{
							label,
							value: normalizeResultDisplayText(value) ?? value
						}] : [];
					})
					: [];
				const links = Array.isArray(item.links)
					? item.links.flatMap((rawLink) => {
						const link = recordValue(rawLink);
						const title = stringValue(link?.title);
						const url = stringValue(link?.url);
						return title && url ? [{
							title: normalizeResultDisplayText(title) ?? title,
							url,
							meta: normalizeResultDisplayText(link?.meta) ?? undefined,
							text: normalizeResultDisplayText(link?.text) ?? undefined
						}] : [];
					})
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
					title: normalizeResultDisplayText(item.title) ?? undefined,
					text: normalizeResultDisplayText(item.text) ?? undefined,
					before: normalizeResultDisplayText(item.before) ?? undefined,
					after: normalizeResultDisplayText(item.after) ?? undefined,
					beforeLabel: stringValue(item.before_label) ?? stringValue(item.beforeLabel) ?? undefined,
					afterLabel: stringValue(item.after_label) ?? stringValue(item.afterLabel) ?? undefined,
					meta: normalizeResultDisplayText(item.meta) ?? undefined,
					url: stringValue(item.url) ?? undefined,
					tone,
					facts,
					links,
					visual
				};
				return Object.values(normalized).some((entry) => Array.isArray(entry) ? entry.length > 0 : Boolean(entry)) ? [normalized] : [];
			});
			const rawOpen = section.open_by_default ?? section.openByDefault;
			return items.length ? [{
				title,
				items,
				...(typeof rawOpen === 'boolean' ? { openByDefault: rawOpen } : {})
			}] : [];
		})
		: [];
	const sections = normalizedSections;
	const rawCoverage = recordValue(result.coverage);
	const coverageMode = stringValue(rawCoverage?.mode);
	const shownCount = numberValue(rawCoverage?.shown_count ?? rawCoverage?.shownCount);
	const totalCount = numberValue(rawCoverage?.total_count ?? rawCoverage?.totalCount);
	const coverageUnit = stringValue(rawCoverage?.unit);
	const coverage = (
		(coverageMode === 'complete' || coverageMode === 'focused' || coverageMode === 'summary')
		&& shownCount !== null
		&& totalCount !== null
		&& coverageUnit
	) ? {
		mode: coverageMode,
		shownCount,
		totalCount,
		unit: coverageUnit,
		reason: stringValue(rawCoverage?.reason) ?? undefined
	} satisfies ActivityTaskStepResultCoverage : undefined;
	const notes = stringList(result.notes);
	const rawAttentionKind = stringValue(
		result.attention_kind ?? result.attentionKind
	);
	const attentionKind: ActivityTaskStepAttentionKind | undefined = (
		rawAttentionKind === 'advisory'
		|| rawAttentionKind === 'manual_review'
	) ? rawAttentionKind : undefined;
	const detailMode = stringValue(result.detail_mode ?? result.detailMode) === 'workflow_summary'
		? 'workflow_summary' as const
		: undefined;
	const rawReviewTargets = Array.isArray(result.review_targets)
		? result.review_targets
		: Array.isArray(result.reviewTargets)
			? result.reviewTargets
			: [];
	const reviewTargets = rawReviewTargets.flatMap((entry) => {
		const target = recordValue(entry);
		const title = stringValue(target?.title);
		const detail = stringValue(target?.detail);
		return title && detail ? [{
			title,
			location: stringValue(target?.location) ?? undefined,
			detail,
			excerpt: normalizeResultDisplayText(target?.excerpt) ?? undefined,
			startMs: numberValue(target?.start_ms ?? target?.startMs) ?? undefined,
			endMs: numberValue(target?.end_ms ?? target?.endMs) ?? undefined
		}] : [];
	});
	const rawDebug = recordValue(result.debug);
	const rawDebugSections = [
		...(Array.isArray(rawDebug?.sections) ? rawDebug.sections : []),
		...(Array.isArray(rawDebug?.items) && rawDebug.items.length
			? [{ title: '节点诊断', items: rawDebug.items }]
			: [])
	];
	const normalizedDebug = rawDebug
		? normalizeStepResult(
			{
				status: 'success',
				summary: '调试信息',
				metrics: rawDebug.metrics,
				sections: rawDebugSections,
				notes: rawDebug.notes
			},
			'success'
		)
		: null;
	const debugDescription = normalizeResultDisplayText(rawDebug?.description);
	const debugSections = (normalizedDebug?.sections ?? []).filter((section, index, all) =>
		all.findIndex((candidate) =>
			candidate.title === section.title
			&& JSON.stringify(candidate.items) === JSON.stringify(section.items)
		) === index
	);
	const debug = {
		description: safeDebugText(
			debugDescription
			?? (rawDebug
				? '用于核对输入来源、模型配置、调用记录和结果依据。'
				: '当前记录未保存单独的调试信息。')
		),
		metrics: (normalizedDebug?.metrics ?? []).map((metric) => ({
			...metric,
			value: safeDebugText(metric.value)
		})),
		sections: debugSections.map((section) => ({
			...section,
			items: section.items.map((item) => ({
				...item,
				text: item.text ? safeDebugText(item.text) : undefined,
				before: item.before ? safeDebugText(item.before) : undefined,
				after: item.after ? safeDebugText(item.after) : undefined,
				facts: item.facts.map((fact) => ({ ...fact, value: safeDebugText(fact.value) }))
			}))
		})),
		notes: [...new Set([...(normalizedDebug?.notes ?? []), ...safeDiagnosticNotes(result.summary)])].map(safeDebugText)
	} satisfies ActivityTaskStepDebug;
	const rawDocument = recordValue(result.document);
	const documentTitle = stringValue(rawDocument?.title);
	const documentContent = stringValue(rawDocument?.content);
	const rawDocumentFormat = stringValue(rawDocument?.format);
	const document = documentTitle && documentContent
		&& (rawDocumentFormat === 'markdown' || rawDocumentFormat === 'text')
		? {
			title: documentTitle,
			format: rawDocumentFormat,
			content: documentContent
		} satisfies ActivityTaskStepResultDocument
		: undefined;
	if (omittedItemCount) notes.push(`这份结果超过界面安全展示上限，另有 ${omittedItemCount} 项机器记录未在弹窗中展开。`);
	return {
		status: stepResultStatus(result.status, fallbackStatus),
		...(attentionKind ? { attentionKind } : {}),
		...(detailMode ? { detailMode } : {}),
		...(purpose ? { purpose } : {}),
		summary,
		metrics,
		sections,
		notes,
		...(coverage ? { coverage } : {}),
		...(reviewTargets.length ? { reviewTargets } : {}),
		...(document ? { document } : {}),
		debug
	};
}

export function activityTaskStepAttentionKind(
	result?: ActivityTaskStepResult,
	stepId = ''
): ActivityTaskStepAttentionKind | null {
	if (!result || result.status !== 'warning') return null;
	if (LOCALIZATION_ADVISORY_STEP_IDS.has(stepId)) return 'advisory';
	if (result.attentionKind) return result.attentionKind;
	return result.reviewTargets?.length
		? 'manual_review'
		: 'advisory';
}

const TIMING_METRIC_LABELS: Record<string, string> = {
	segment_count: '原始片段',
	cluster_count: '说话人数',
	query_count: '搜索查询',
	source_count: '资料来源',
	cache_hits: '缓存命中',
	batch_count: '请求批次',
	request_count: '模型请求',
	problem_count: '待调整字幕',
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
	timing: Record<string, unknown> | null,
	taskStatus?: VideoLocalizationOperation['status']
): ActivityTaskStepResult {
	const metrics = Object.entries(TIMING_METRIC_LABELS).flatMap(([key, label]) => {
		const value = numberValue(timing?.[key]);
		return value === null ? [] : [{ label, value: value.toString() }];
	});
	const running = status === 'running';
	return {
		status: stepResultStatus(null, status),
		summary: status === 'todo'
			? taskStatus === 'success'
				? `${stepLabel}本次未执行；当前任务已在前面的步骤结束。`
				: `${stepLabel}尚未开始；前置步骤完成后会自动执行。`
			: running
			? `${stepLabel}正在处理，步骤完成后会补充可核验的结果。`
			: taskStatus === 'running' && status === 'success'
				? `${stepLabel}已经完成。完整结果会在整项任务结束并保存后自动补充。`
			: `该步骤已${status === 'success' ? '完成' : '结束'}，但没有保存可展示的详细产物。`,
		metrics,
		sections: [],
		notes: []
	};
}

function localizationOperationSteps(operation: VideoLocalizationOperation): ActivityTaskStep[] {
	const taskStageTimings = recordValue(operation.result_summary?.task_stage_timings);
	const rawStepResults = recordValue(operation.result_summary?.task_step_results);
	return dynamicOperationSteps(
		operation,
		taskStageTimings,
		rawStepResults
	) ?? [];
}

function workflowPlannedSteps(
	operation: VideoLocalizationOperation
): { id: string; label: string; order: number }[] {
	const rawGroups = Array.isArray(operation.result_summary?.task_stage_groups)
		? operation.result_summary.task_stage_groups
		: [];
	const seen = new Set<string>();
	return rawGroups.flatMap((rawGroup) => {
		const group = recordValue(rawGroup);
		const tasks = Array.isArray(group?.atomic_tasks)
			? group.atomic_tasks
			: Array.isArray(group?.atomicTasks)
				? group.atomicTasks
				: [];
		return tasks.flatMap((rawTask) => {
			const task = recordValue(rawTask);
			const id = stringValue(task?.id);
			const label = stringValue(task?.label);
			const order = numberValue(task?.order);
			if (!id || !label || order === null || seen.has(id)) return [];
			seen.add(id);
			return [{ id, label, order }];
		});
	});
}

function dynamicOperationSteps(
	operation: VideoLocalizationOperation,
	taskStageTimings: Record<string, unknown> | null,
	rawStepResults: Record<string, unknown> | null,
	plannedSteps: readonly { id: string; label: string; order: number }[] = []
): ActivityTaskStep[] | null {
	const effectivePlannedSteps = plannedSteps.length
		? plannedSteps
		: workflowPlannedSteps(operation);
	const plannedById = new Map(effectivePlannedSteps.map((step) => [step.id, step]));
	const observedEntries = Object.entries(rawStepResults ?? {}).flatMap(([id, rawResult], insertionIndex) => {
		const result = recordValue(rawResult);
		const planned = plannedById.get(id);
		const label = planned?.label ?? stringValue(result?.label);
		const order = planned?.order ?? numberValue(result?.order);
		return label && order !== null ? [{
			id,
			label,
			order,
			insertionIndex,
			rawResult,
			observed: true
		}] : [];
	});
	const observedIds = new Set(observedEntries.map((entry) => entry.id));
	const dynamicEntries = [
		...observedEntries,
		...effectivePlannedSteps.flatMap((step, index) => observedIds.has(step.id) ? [] : [{
			...step,
			insertionIndex: observedEntries.length + index,
			rawResult: { label: step.label, order: step.order, status: 'todo' },
			observed: false
		}])
	];
	if (dynamicEntries.length) {
		const currentStageId = stringValue(operation.result_summary?.stage_id);
		const ordered = dynamicEntries.sort((left, right) => left.order - right.order || left.insertionIndex - right.insertionIndex);
		const currentIndex = ordered.findIndex((entry) => entry.id === currentStageId);
		return ordered.map((entry, index) => {
			const rawStatus = stringValue(
				recordValue(entry.rawResult)?.status
			);
			let status: ActivityTaskStepStatus = 'todo';
			if (operation.status === 'cancelled') {
				if (rawStatus === 'failed') status = 'failed';
				else if (
					rawStatus === 'todo'
					|| rawStatus === 'running'
					|| rawStatus === 'cancelled'
					|| rawStatus === null
				) status = 'cancelled';
				else status = 'success';
			}
			else if (operation.status === 'failed') {
				if (rawStatus === 'failed' || rawStatus === 'running') status = 'failed';
				else if (rawStatus === 'todo' || rawStatus === 'cancelled' || rawStatus === null) status = 'cancelled';
				else status = 'success';
			}
			else if (operation.status === 'success') {
				if (rawStatus === 'failed' || rawStatus === 'running') status = 'failed';
				else if (
					entry.observed
					&& (rawStatus === 'todo' || rawStatus === 'cancelled')
				) status = 'cancelled';
				else status = 'success';
			}
			else if (rawStatus === 'success' || rawStatus === 'warning' || rawStatus === 'skipped' || rawStatus === 'not_needed') {
				status = 'success';
			} else if (rawStatus === 'failed') {
				status = 'failed';
			} else if (rawStatus === 'running') {
				status = 'running';
			} else if (index === currentIndex) {
				status = 'running';
			} else if (rawStatus === 'todo') {
				status = 'todo';
			} else if (index < currentIndex) {
				status = 'success';
			} else {
				status = 'todo';
			}
			const timing = recordValue(taskStageTimings?.[entry.id]);
			const durationMs = numberValue(timing?.duration_ms);
			const startedElapsedMs = numberValue(timing?.started_elapsed_ms);
			const result = normalizeStepResult(entry.rawResult, status)
				?? fallbackStepResult(entry.label, status, timing, operation.status);
			return {
				id: entry.id,
				label: entry.label,
				status,
				...(durationMs === null ? {} : { durationMs }),
				...(startedElapsedMs === null ? {} : { startedElapsedMs }),
				...(result ? { result } : {})
			};
		});
	}
	return null;
}

function diarizationClusterSections(sample: Record<string, unknown> | null): ActivityTaskStepResultSection[] {
	const clusters = Array.isArray(sample?.clusters) ? sample.clusters : [];
	if (!clusters.length) return [];
	return [{
		title: '匿名说话人分组',
		items: clusters.flatMap((entry, index) => {
			const cluster = recordValue(entry);
			if (!cluster) return [];
			const startMs = numberValue(cluster.start_ms);
			const endMs = numberValue(cluster.end_ms);
			const durationMs = numberValue(cluster.duration_ms);
			return [{
				title: stringValue(cluster.cluster_id) ?? `说话人 ${index + 1}`,
				meta: startMs === null || endMs === null
					? undefined
					: formatActivityTimelineRange(startMs, endMs),
				facts: [
					...(durationMs === null ? [] : [{ label: '累计范围', value: formatActivityTimelineDuration(durationMs) }]),
					{ label: '片段数量', value: `${numberValue(cluster.segment_count) ?? 0}` },
					{ label: '合并检查', value: stringValue(cluster.merge_status) ?? '已记录' }
				],
				links: []
			}];
		})
	}];
}

function developmentDiarizationStep(operation: VideoLocalizationOperation): ActivityTaskStep[] {
	const quality = recordValue(operation.result_summary?.quality_summary);
	const guidance = recordValue(operation.result_summary?.count_guidance);
	const sample = recordValue(operation.result_summary?.sample);
	const clusters = Array.isArray(sample?.clusters) ? sample.clusters : [];
	const speakerCount = numberValue(operation.result_summary?.speaker_count) ?? clusters.length;
	const segmentCount = numberValue(quality?.segment_count);
	const overlapCount = numberValue(quality?.overlap_segment_count);
	const coverageRatio = decimalValue(quality?.coverage_ratio);
	const requestedMax = numberValue(guidance?.requested_max_speakers);
	const evaluation = stringValue(guidance?.evaluation);
	const qualityStatus = stringValue(quality?.status);
	const resultStatus: ActivityTaskStepResultStatus = operation.status === 'failed'
		? 'failed'
		: qualityStatus === 'warning' || qualityStatus === 'failed'
			? 'warning'
			: operation.status === 'success'
				? 'success'
				: 'running';
	const stepStatus: ActivityTaskStepStatus = operation.status === 'success'
		? 'success'
		: operation.status === 'failed'
			? 'failed'
			: operation.status === 'cancelled'
				? 'cancelled'
				: operation.status === 'queued'
					? 'todo'
					: 'running';
	const countNote = requestedMax === null
		? '说话人数由引擎自动判断。'
		: evaluation === 'above_maximum'
			? `检测到 ${speakerCount} 位，超过设置的最多 ${requestedMax} 位；这里只标记复核，不会强行合并声纹。`
			: `最多 ${requestedMax} 位仅用于结果复核，当前引擎仍自动判断人数。`;
	const result: ActivityTaskStepResult = {
		status: resultStatus,
		purpose: '只根据声音特征生成匿名说话人时间段，不依赖 ASR 文字，也不猜测真实姓名。',
		summary: operation.status === 'success'
			? `已区分 ${speakerCount} 位匿名说话人。`
			: operation.status === 'failed'
				? operation.error_message || '说话人区分未完成。'
				: '正在分析不同说话人的声音特征。',
		metrics: [
			{ label: '说话人数', value: `${speakerCount}` },
			...(segmentCount === null ? [] : [{ label: '讲话片段', value: `${segmentCount}` }]),
			...(coverageRatio === null ? [] : [{ label: '音频覆盖', value: `${Math.round(coverageRatio * 100)}%` }]),
			...(overlapCount === null ? [] : [{ label: '重叠讲话', value: `${overlapCount}` }])
		],
		sections: diarizationClusterSections(sample),
		notes: [countNote]
	};
	return [{
		id: 'diarization',
		label: '区分说话人',
		status: stepStatus,
		...(operation.status === 'queued' ? {} : { result })
	}];
}

function initialAnalysisOperationSteps(operation: VideoLocalizationOperation): ActivityTaskStep[] {
	const persistedSteps = dynamicOperationSteps(
		operation,
		recordValue(operation.result_summary?.task_stage_timings),
		recordValue(operation.result_summary?.task_step_results)
	);
	if (persistedSteps) return persistedSteps;
	const qualitySummary = recordValue(operation.result_summary?.quality_summary);
	const rawQuality = normalizedRawAsrQuality(qualitySummary?.raw_asr);
	const diarizationQuality = recordValue(qualitySummary?.diarization);
	const sample = recordValue(operation.result_summary?.sample);
	const rawSample = recordValue(sample?.raw_asr);
	const diarizationSample = recordValue(sample?.diarization);
	const branchDurations = recordValue(operation.result_summary?.branch_duration_ms);
	const rawDurationMs = numberValue(branchDurations?.raw_asr);
	const diarizationDurationMs = numberValue(branchDurations?.diarization);
	const rawSegmentCount = numberValue(operation.result_summary?.raw_asr_segment_count) ?? 0;
	const speakerCount = numberValue(operation.result_summary?.speaker_count)
		?? numberValue(diarizationQuality?.cluster_count)
		?? 0;
	const diarizationSegmentCount = numberValue(operation.result_summary?.diarization_segment_count)
		?? numberValue(diarizationQuality?.segment_count)
		?? 0;
	const coverageRatio = decimalValue(diarizationQuality?.coverage_ratio);
	const overlapCount = numberValue(diarizationQuality?.overlap_segment_count);
	const rawQualityStatus = stringValue(rawQuality?.status);
	const diarizationQualityStatus = stringValue(diarizationQuality?.status);
	const branchStepStatus: ActivityTaskStepStatus = operation.status === 'success'
		? 'success'
		: operation.status === 'failed'
			? 'failed'
			: operation.status === 'cancelled'
				? 'cancelled'
				: operation.status === 'queued'
					? 'todo'
					: 'running';
	const branchResultStatus = (
		qualityStatus: string | null,
		branchStatus?: string | null
	): ActivityTaskStepResultStatus => {
		if (operation.status === 'failed') return 'failed';
		if (qualityStatus === 'warning' || qualityStatus === 'failed' || branchStatus === 'failed') return 'warning';
		return operation.status === 'success' ? 'success' : 'running';
	};
	const sampledRawResult = rawSample
		? partialAsrStepResult({
			language: stringValue(rawSample.language) ?? '',
			totalCount: numberValue(rawSample.segment_count) ?? rawSegmentCount,
			sampleCount: numberValue(rawSample.sample_count),
			sampleRatio: decimalValue(rawSample.sample_ratio),
			quality: normalizedRawAsrQuality(rawSample.quality_summary) ?? rawQuality,
			segments: rawAsrDisplaySegments(rawSample.segments),
			contextNotes: ['原始听写与说话人区分使用同一份人声音频并行执行。'],
			downstreamNote: '全文复核、逐词对齐、停顿分析、字幕断句和字幕轨均未执行。'
		})
		: null;
	const rawResult: ActivityTaskStepResult = sampledRawResult ? {
		...sampledRawResult,
		metrics: [
			...sampledRawResult.metrics,
			...(rawDurationMs === null ? [] : [{ label: '分支耗时', value: formatActivityTaskDuration(rawDurationMs) }])
		]
	} : {
		status: branchResultStatus(rawQualityStatus),
		purpose: '把人声音频转换为未经后续校对的原始文本和粗时间片段。',
		summary: operation.status === 'success'
			? `已生成 ${rawSegmentCount} 个原始语音片段。`
			: operation.status === 'failed'
				? operation.error_message || '原始听写未完成。'
				: '正在并行生成原始听写稿。',
		metrics: [
			{ label: '原始片段', value: `${rawSegmentCount}` },
			...(rawDurationMs === null ? [] : [{ label: '分支耗时', value: formatActivityTaskDuration(rawDurationMs) }])
		],
		sections: [],
		notes: [
			partialAsrQualityNote(rawQuality, rawSegmentCount),
			'原始听写与说话人区分使用同一份人声音频并行执行。',
			'全文复核、逐词对齐、停顿分析、字幕断句和字幕轨均未执行。'
		]
	};
	const diarizationResult: ActivityTaskStepResult = {
		status: branchResultStatus(
			diarizationQualityStatus,
			stringValue(operation.result_summary?.diarization_status)
		),
		purpose: '只根据声音特征生成匿名说话人时间段，不依赖 ASR 文字，也不猜测真实姓名。',
		summary: operation.status === 'success'
			? `已区分 ${speakerCount} 位匿名说话人，共 ${diarizationSegmentCount} 个讲话片段。`
			: operation.status === 'failed'
				? operation.error_message || '说话人区分未完成。'
				: '正在并行分析不同说话人的声音特征。',
		metrics: [
			{ label: '说话人数', value: `${speakerCount}` },
			{ label: '讲话片段', value: `${diarizationSegmentCount}` },
			...(coverageRatio === null ? [] : [{ label: '音频覆盖', value: `${Math.round(coverageRatio * 100)}%` }]),
			...(overlapCount === null ? [] : [{ label: '重叠讲话', value: `${overlapCount}` }]),
			...(diarizationDurationMs === null ? [] : [{ label: '分支耗时', value: formatActivityTaskDuration(diarizationDurationMs) }])
		],
		sections: diarizationClusterSections(diarizationSample),
		notes: [
			'说话人数由引擎根据声纹自动判断；设置的最多人数只用于结果复核。',
			'结果已与原始听写在联合快照中汇合，后续开发可直接从该快照继续。'
		]
	};
	return ASR_INITIAL_ANALYSIS_FLOW_STEPS.map((step, index) => ({
		id: step.id,
		label: step.label,
		status: index < 3 ? branchStepStatus : 'todo',
		...(index === 0 && rawDurationMs !== null ? { durationMs: rawDurationMs } : {}),
		...(index === 1 && diarizationDurationMs !== null ? { durationMs: diarizationDurationMs } : {}),
		...(index === 0 ? { result: rawResult } : {}),
		...(index === 1 ? { result: diarizationResult } : {}),
		...(index === 2 ? {
			result: {
				status: operation.status === 'success' ? 'success' : 'running',
				purpose: '确认两份结果来自同一音轨，再整理成谁在什么时候说了什么。',
				summary: operation.status === 'success'
					? `已汇合 ${rawSegmentCount} 个听写片段。`
					: '正在等待听写和说话人结果汇合。',
				metrics: [
					{ label: '听写片段', value: `${rawSegmentCount}` },
					{ label: '声纹片段', value: `${diarizationSegmentCount}` },
					{ label: '匿名说话人', value: `${speakerCount}` }
				],
				sections: [],
				notes: ['汇合完成后会显示听写、声纹和共同输入版本。']
			}
		} : {}),
		...(index > 2 ? {
			result: fallbackStepResult(
				step.label,
				'todo',
				null,
				operation.status,
			)
		} : {})
	}));
}

function operationSteps(operation: VideoLocalizationOperation, _stage: string): ActivityTaskStep[] | undefined {
	if (operation.kind === 'localization_draft') return localizationOperationSteps(operation);
	if (operation.kind === 'dub_subtitle_generation') {
		return dynamicOperationSteps(
			operation,
			recordValue(operation.result_summary?.task_stage_timings),
			recordValue(operation.result_summary?.task_step_results)
		) ?? undefined;
	}
	if (operation.kind === 'speaker_diarization') return developmentDiarizationStep(operation);
	if (operation.kind === 'semantic_tts_grouping') {
		return dynamicOperationSteps(
			operation,
			recordValue(operation.result_summary?.task_stage_timings),
			recordValue(operation.result_summary?.task_step_results)
		) ?? undefined;
	}
	if (operation.kind === 'media_export') {
		return dynamicOperationSteps(
			operation,
			recordValue(operation.result_summary?.task_stage_timings),
			recordValue(operation.result_summary?.task_step_results)
		) ?? undefined;
	}
	if (operation.kind !== 'english_asr') return undefined;
	if (developmentAsrMode(operation) === 'initial_analysis') {
		return initialAnalysisOperationSteps(operation);
	}
	if (
		developmentAsrMode(operation) === 'document_understanding'
		|| developmentAsrMode(operation) === 'research'
		|| developmentAsrMode(operation) === 'visual_evidence'
		|| developmentAsrMode(operation) === 'entity_normalization'
		|| developmentAsrMode(operation) === 'section_review'
		|| developmentAsrMode(operation) === 'review_decisions'
		|| developmentAsrMode(operation) === 'whole_recheck'
		|| developmentAsrMode(operation) === 'transcript_quality_gate'
	) {
		return dynamicOperationSteps(
			operation,
			recordValue(operation.result_summary?.task_stage_timings),
			recordValue(operation.result_summary?.task_step_results)
		) ?? undefined;
	}
	if (isPartialAsrOperation(operation)) {
		const managedSteps = dynamicOperationSteps(
			operation,
			recordValue(operation.result_summary?.task_stage_timings),
			recordValue(operation.result_summary?.task_step_results)
		);
		if (managedSteps) return managedSteps;
		if (
			stringValue(
				operation.result_summary?.workflow_schema_version
			) === 'asr-raw-development-workflow-v1'
			|| stringValue(
				operation.result_summary?.workflow_id
			) === 'asr-raw-development'
		) {
			return dynamicOperationSteps(
				operation,
				null,
				null,
				MANAGED_RAW_ASR_FLOW_STEPS
			) ?? undefined;
		}
		const sample = recordValue(operation.result_summary?.sample);
		const segmentCount = numberValue(sample?.segment_count);
		const sampleCount = numberValue(sample?.sample_count);
		const sampleRatio = decimalValue(sample?.sample_ratio);
		const language = stringValue(sample?.language);
		const quality = normalizedRawAsrQuality(sample?.quality_summary);
		const sampleSegments = rawAsrDisplaySegments(sample?.segments);
		const firstResult = partialAsrStepResult({
			language: language ?? '',
			totalCount: segmentCount ?? sampleSegments.length,
			sampleCount,
			sampleRatio,
			quality,
			segments: sampleSegments
		});
		return ASR_REQUIRED_FLOW_STEPS.map((step, index) => ({
			id: step.id,
			label: step.label,
			status: index === 0 ? 'success' : 'todo',
			result: index === 0
				? firstResult
				: fallbackStepResult(step.label, 'todo', null, operation.status)
		}));
	}
	const diagnosticStageTimings = recordValue(operation.result_summary?.stage_timings);
	const taskStageTimings = recordValue(operation.result_summary?.task_stage_timings);
	const stageTimings = diagnosticStageTimings
		? { ...(taskStageTimings ?? {}), ...diagnosticStageTimings }
		: taskStageTimings;
	const rawStepResults = recordValue(operation.result_summary?.task_step_results);
	const dynamicSteps = dynamicOperationSteps(operation, stageTimings, rawStepResults);
	if (dynamicSteps) {
		const currentLabels = new Map<string, string>(ASR_REQUIRED_FLOW_STEPS.map((step) => [step.id, step.label]));
		return dynamicSteps.map((step) => ({ ...step, label: currentLabels.get(step.id) ?? step.label }));
	}
	return undefined;
}

export function resolveActivityTaskStageStatus(steps: readonly ActivityTaskStep[]): ActivityTaskStepStatus {
	if (steps.some((step) => step.status === 'failed')) return 'failed';
	if (steps.some((step) => step.status === 'running')) return 'running';
	if (steps.length && steps.every((step) => step.status === 'success')) return 'success';
	if (steps.some((step) => step.status === 'cancelled') && !steps.some((step) => step.status === 'running')) return 'cancelled';
	return 'todo';
}

function stageDuration(
	steps: readonly ActivityTaskStep[],
	layout: ActivityTaskStage['layout'],
	allowMissing = false
) {
	if (!steps.length || (!allowMissing && steps.some((step) => step.durationMs === undefined))) {
		return undefined;
	}
	if (layout === 'parallel-join') {
		const stepById = new Map(steps.map((step) => [step.id, step]));
		const finishById = new Map<string, number>();
		const visiting = new Set<string>();
		const finishAt = (step: ActivityTaskStep): number => {
			const cached = finishById.get(step.id);
			if (cached !== undefined) return cached;
			if (visiting.has(step.id)) return step.durationMs ?? 0;
			visiting.add(step.id);
			const dependencyFinish = (step.dependsOn ?? [])
				.flatMap((dependencyId) => {
					const dependency = stepById.get(dependencyId);
					return dependency ? [finishAt(dependency)] : [];
				});
			visiting.delete(step.id);
			const finish = Math.max(0, ...dependencyFinish)
				+ (step.durationMs ?? 0);
			finishById.set(step.id, finish);
			return finish;
		};
		return Math.max(0, ...steps.map(finishAt));
	}
	return steps.reduce((total, step) => total + (step.durationMs ?? 0), 0);
}

function recordedStageDuration(
	operation: VideoLocalizationOperation,
	stageId: string,
	steps: readonly ActivityTaskStep[],
	layout: ActivityTaskStage['layout']
) {
	if (
		stageId === 'initial_analysis'
		&& stringValue(operation.result_summary?.execution_scope) === 'partial'
	) {
		const branchDurations = recordValue(operation.result_summary?.branch_duration_ms);
		const wallDurationMs = numberValue(branchDurations?.wall)
			?? numberValue(operation.result_summary?.task_duration_ms);
		if (wallDurationMs !== null) return wallDurationMs;
	}
	return stageDuration(steps, layout);
}

function operationStages(
	operation: VideoLocalizationOperation,
	steps: ActivityTaskStep[] | undefined
): ActivityTaskStage[] | undefined {
	if (!steps?.length) return undefined;
	const rawGroups = Array.isArray(operation.result_summary?.task_stage_groups)
		? operation.result_summary.task_stage_groups
		: [];
	if (!rawGroups.length) return undefined;
	const stepById = new Map(steps.map((step) => [step.id, step]));
	const assigned = new Set<string>();
	const stages: ActivityTaskStage[] = rawGroups.flatMap((entry) => {
		const group = recordValue(entry);
		const id = stringValue(group?.id);
		const label = stringValue(group?.label);
		const tasks = Array.isArray(group?.atomic_tasks)
			? group.atomic_tasks
			: Array.isArray(group?.atomicTasks)
				? group.atomicTasks
				: [];
		if (!id || !label) return [];
		const stageSteps = tasks.flatMap((rawTask) => {
			const task = recordValue(rawTask);
			const taskId = stringValue(task?.id);
			if (!taskId) return [];
			const step = stepById.get(taskId);
			if (!step) return [];
			assigned.add(taskId);
			const execution = stringValue(task?.execution);
			const normalizedExecution: ActivityTaskStep['execution'] = (
				execution === 'parallel' || execution === 'join' || execution === 'serial'
			) ? execution : 'serial';
			const dependencyMode = stringValue(
				task?.dependency_mode ?? task?.dependencyMode
			);
			const normalizedDependencyMode: NonNullable<
				ActivityTaskStep['dependencyMode']
			> = dependencyMode === 'latest_completed'
				? 'latest_completed'
				: 'all';
			const description = stringValue(task?.description) ?? undefined;
			const dependsOn = stringList(task?.depends_on ?? task?.dependsOn);
			const optional = task?.optional === true;
			const pendingResult = step.result?.status === 'todo'
				? {
					...step.result,
					...(description ? { purpose: description } : {}),
					notes: optional
						? ['这是按需子任务；前置步骤判断确有必要时会自动执行，否则会明确标为跳过。']
						: dependsOn.length
							? ['等待前置步骤完成后会自动执行，无需手动选择模式。']
							: ['任务开始后会自动执行，无需手动选择模式。']
				}
				: step.result;
			return [{
				...step,
				label: stringValue(task?.label) ?? step.label,
				description,
				execution: normalizedExecution,
				dependsOn,
				dependencyMode: normalizedDependencyMode,
				optional,
				...(pendingResult ? { result: pendingResult } : {})
			}];
		});
		if (!stageSteps.length) return [];
		const layout: ActivityTaskStage['layout'] = (
			stageSteps.some((step) => step.execution === 'parallel')
			&& stageSteps.some((step) => step.execution === 'join')
		) ? 'parallel-join' : 'linear';
		return [{
			id,
			label,
			description: stringValue(group?.description) ?? undefined,
			status: resolveActivityTaskStageStatus(stageSteps),
			durationMs: recordedStageDuration(operation, id, stageSteps, layout),
			layout,
			steps: stageSteps
		}];
	});
	const unassigned = steps.filter((step) => !assigned.has(step.id));
	if (unassigned.length) {
		stages.push({
			id: 'other_steps',
			label: '未归类步骤',
			description: '任务记录中存在工作流定义未包含的步骤，请检查任务契约。',
			status: resolveActivityTaskStageStatus(unassigned),
			layout: 'linear',
			steps: unassigned
		});
	}
	return stages.length ? stages : undefined;
}

export function activityTaskStepEntries(task: Pick<ActivityTask, 'stages' | 'steps'>) {
	if (task.stages?.length) {
		return task.stages.flatMap((stage) => stage.steps.map((step) => ({ stage, step })));
	}
	return (task.steps ?? []).map((step) => ({ stage: undefined, step }));
}

function operationResult(operation: VideoLocalizationOperation) {
	if (operation.kind === 'english_asr') {
		if (developmentAsrMode(operation) === 'visual_evidence') {
			return {
				count: numberValue(operation.result_summary?.frame_count),
				unit: '张截图'
			};
		}
		if (developmentAsrMode(operation) === 'research') {
			return {
				count: numberValue(operation.result_summary?.evidence_count),
				unit: '条资料'
			};
		}
		if (developmentAsrMode(operation) === 'document_understanding') {
			return {
				count: numberValue(operation.result_summary?.segment_count),
				unit: '个讲话片段'
			};
		}
		if (developmentAsrMode(operation) === 'entity_normalization') {
			return {
				count: numberValue(operation.result_summary?.change_count),
				unit: '处文字修改'
			};
		}
		if (developmentAsrMode(operation) === 'section_review') {
			return {
				count: numberValue(operation.result_summary?.issue_count),
				unit: '个可能问题'
			};
		}
		if (developmentAsrMode(operation) === 'review_decisions') {
			return {
				count: numberValue(operation.result_summary?.applied_change_count),
				unit: '处实际修改'
			};
		}
		if (developmentAsrMode(operation) === 'whole_recheck') {
			const legacyStopAfter = stringValue(operation.parameters?.execution_mode) === 'stop_after';
			return {
				count: numberValue(legacyStopAfter
					? operation.result_summary?.next_section_count
					: operation.result_summary?.unresolved_item_count),
				unit: legacyStopAfter ? '个下一轮区块' : '处建议复听'
			};
		}
		if (developmentAsrMode(operation) === 'transcript_quality_gate') {
			return {
				count: numberValue(operation.result_summary?.review_target_count),
				unit: '处建议复听'
			};
		}
		return { count: numberValue(operation.result_summary?.cue_count ?? operation.result_summary?.segment_count), unit: '条字幕' };
	}
	if (operation.kind === 'localization_draft') {
		return { count: numberValue(operation.result_summary?.localized_subtitle_count), unit: '条字幕' };
	}
	if (operation.kind === 'dub_subtitle_generation') {
		return { count: numberValue(operation.result_summary?.dub_subtitle_count), unit: '条字幕' };
	}
	if (operation.kind === 'speaker_diarization') {
		return { count: numberValue(operation.result_summary?.speaker_count), unit: '位说话人' };
	}
	if (operation.kind === 'reference_clips') {
		return { count: numberValue(operation.result_summary?.reference_clip_count), unit: '个候选' };
	}
	if (operation.kind === 'semantic_tts_grouping') {
		return { count: numberValue(operation.result_summary?.semantic_group_count), unit: '个配音组' };
	}
	if (operation.kind === 'media_export') {
		return {
			count: stringValue(operation.result_summary?.filename) ? 1 : null,
			unit: '个成品'
		};
	}
	return { count: null, unit: '' };
}

function mediaOperationSummaryFacts(operation: VideoLocalizationOperation): ActivityTaskStepResultMetric[] {
	if (operation.kind === 'media_export') {
		const filename = stringValue(operation.result_summary?.filename);
		const sizeBytes = numberValue(operation.result_summary?.size_bytes);
		const exportKind = stringValue(operation.result_summary?.export_kind);
		const mixedTrackCount = numberValue(operation.result_summary?.mixed_track_count);
		const sizeLabel = sizeBytes === null
			? ''
			: sizeBytes >= 1024 * 1024 * 1024
				? `${formattedNumber(sizeBytes / (1024 * 1024 * 1024), 2)} GB`
				: sizeBytes >= 1024 * 1024
					? `${formattedNumber(sizeBytes / (1024 * 1024), 2)} MB`
					: sizeBytes >= 1024
						? `${formattedNumber(sizeBytes / 1024, 1)} KB`
						: `${sizeBytes} B`;
		return [
			...(filename ? [{ label: '成品文件', value: filename }] : []),
			...(sizeLabel ? [{ label: '文件大小', value: sizeLabel }] : []),
			...(exportKind ? [{
				label: '导出类型',
				value: exportKind === 'video'
					? '视频'
					: exportKind === 'audio'
						? '音频'
						: '字幕'
			}] : []),
			...(exportKind === 'subtitle' || mixedTrackCount === null
				? []
				: [{ label: '音频片段', value: `${mixedTrackCount} 个` }])
		];
	}
	if (operation.kind !== 'source_audio' && operation.kind !== 'stems') return [];
	const durationMs = numberValue(operation.result_summary?.duration_ms);
	const sampleRate = numberValue(operation.result_summary?.sample_rate);
	const channels = numberValue(operation.result_summary?.channels);
	const trackCount = numberValue(operation.result_summary?.track_count);
	const engineId = stringValue(operation.result_summary?.separation_engine_id);
	const rawStatus = stringValue(
		operation.result_summary?.media_status
		?? operation.result_summary?.audio_extract_status
		?? operation.result_summary?.separation_status
	);
	const status = rawStatus ? ({
		completed: '已完成',
		running: '处理中',
		queued: '等待执行',
		failed: '失败'
	}[rawStatus] ?? rawStatus) : '';
	const channelLabel = channels === 1 ? '单声道' : channels === 2 ? '立体声' : channels === null ? '' : `${channels} 声道`;
	const sampleRateLabel = sampleRate === null
		? ''
		: sampleRate >= 1000
			? `${formattedNumber(sampleRate / 1000, 1)} kHz`
			: `${sampleRate} Hz`;
	return [
		...(durationMs === null ? [] : [{ label: '音频时长', value: formatActivityTaskDuration(durationMs) }]),
		...(sampleRateLabel ? [{ label: '采样率', value: sampleRateLabel }] : []),
		...(channelLabel ? [{ label: '声道', value: channelLabel }] : []),
		...(engineId ? [{ label: '分离引擎', value: engineId }] : []),
		...(trackCount === null ? [] : [{ label: '生成轨道', value: `${trackCount} 条` }]),
		...(status ? [{ label: '处理状态', value: status }] : [])
	];
}

export function activityTaskReviewAction(stepLabel: string) {
	if (/校时前检查/.test(stepLabel)) return '自动流程已经继续；建议结合原音复听下面片段，必要时再修改原文。';
	if (/本地映射语义时间/.test(stepLabel)) return '这些低把握边界已经交给下一步自动复核；这里用于了解过程，不需要中断流程。';
	if (/裁决.*证据|证据.*裁决/.test(stepLabel)) return '检查证据引用和补查建议是否对得上原文；未确认的事实先保持保守写法，不要提前补进中文。';
	if (/限定补查|补充相邻画面/.test(stepLabel)) return '检查新资料或相邻截图是否对准了原文疑点；它们还要经过下一次裁决，暂时不要直接写进中文。';
	if (/资料证据|画面证据|资料查询|画面取证/.test(stepLabel)) return '检查候选资料或截图是否确实对应原文问题；这些材料本身还不是最终翻译结论。';
	if (/说话人|人物归属/.test(stepLabel)) return '试听下面标出的时间段，确认声音属于哪位说话人。';
	if (/时间|对齐|停顿|断句|边界|字幕轨|时间轴/.test(stepLabel)) return '试听下面标出的时间段，确认字幕入点、出点和停顿位置是否自然。';
	if (/本土化|翻译|中文|口语/.test(stepLabel)) return '对照英文原意和前后文，确认中文说法自然、意思没有改变。';
	if (/查证|专名|术语|名称/.test(stepLabel)) return '对照原音、上下文和查证资料，确认名称与术语的正确写法。';
	return '对照原音和前后文，确认下面这些内容是否准确；拿不准的条目可以保留给人工修改。';
}

export function activityTaskReviewTargets(result: ActivityTaskStepResult, limit = 12): ActivityTaskReviewTarget[] {
	if (result.status !== 'warning' && result.status !== 'failed') return [];
	const dedupeTargets = (items: ActivityTaskReviewTarget[]) => {
		const seen = new Set<string>();
		return items.filter((item) => {
			const key = [item.title, item.location ?? '', item.detail]
				.map((value) => value.trim().replace(/\s+/g, ' '))
				.join('\u0000');
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	};
	if (result.reviewTargets?.length) {
		return dedupeTargets(result.reviewTargets).slice(0, Math.max(1, limit));
	}
	const targets = result.sections.flatMap((section) => section.items.flatMap((item, index) => {
		if (item.tone !== 'warning' && result.status !== 'failed') return [];
		const locationFact = item.facts.find((fact) => /字幕|片段|时间|原文 cue/.test(fact.label));
		const issueFact = item.facts.find((fact) => /问题|修改建议|原因/.test(fact.label));
		const detail = item.text
			?? issueFact?.value
			?? (item.before || item.after ? '核对系统更正后的内容是否符合原音和上下文。' : result.summary);
		return [{
			title: item.title || `${section.title} ${index + 1}`,
			location: item.meta || locationFact?.value || undefined,
			detail
		}];
	}));
	if (targets.length) return dedupeTargets(targets).slice(0, Math.max(1, limit));
	return dedupeTargets(result.notes.map((note, index) => ({
		title: `提醒 ${index + 1}`,
		detail: note
	}))).slice(0, Math.max(1, limit));
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

export function operationActivityTask(operation: VideoLocalizationOperation, actionPending?: 'cancel' | 'retry'): ActivityTask {
	const stage = typeof operation.result_summary?.stage === 'string'
		? operation.result_summary.stage.trim()
		: '';
	const stageId = stringValue(operation.result_summary?.stage_id) ?? stage;
	const developmentMode = developmentAsrMode(operation);
	const partialAsr = isPartialAsrOperation(operation);
	const partialDiarization = operation.kind === 'speaker_diarization';
	const partialLocalization = (
		operation.kind === 'localization_draft'
		&& (
			stringValue(operation.parameters?.execution_mode) === 'development_target'
			|| stringValue(operation.result_summary?.execution_mode) === 'development_target'
		)
	);
	const result = operationResult(operation);
	const cancellationInProgress = actionPending === 'cancel'
		|| (
			operation.cancel_requested
			&& (operation.status === 'queued' || operation.status === 'running')
		);
	const finalResult = operation.status === 'success' && !partialAsr && !partialLocalization
		? normalizeStepResult(operation.result_summary?.task_final_result, 'success')
		: null;
	const summaryFacts = mediaOperationSummaryFacts(operation);
	const steps = operationSteps(operation, stageId);
	const stages = operationStages(operation, steps);
	const effectiveStatus: ActivityTaskStatus = (
		operation.status === 'success'
		&& (
			steps?.some((step) => step.status === 'failed')
			|| stages?.some((taskStage) => taskStage.status === 'failed')
		)
	) ? 'failed' : operation.status;
	const partialLocalizationLabel = partialLocalization
		? steps?.find((step) => step.id === stageId)?.label
		: null;
	return {
		id: `operation:${operation.operation_id}`,
		operationId: operation.operation_id,
		kind: operation.kind,
		label: developmentMode === 'initial_analysis'
			? '原始听写 + 说话人区分（并行开发）'
			: developmentMode === 'raw_asr'
				? '原始听写（开发单步）'
				: developmentMode === 'document_understanding'
					? '理解全文并规划复查（开发单步）'
				: developmentMode === 'research'
					? '核对名称与背景（开发单步）'
				: developmentMode === 'visual_evidence'
					? '画面取证（开发单步）'
				: developmentMode === 'entity_normalization'
					? '统一名称与术语（开发单步）'
				: developmentMode === 'section_review'
					? '第 1 轮分段复查（开发单步）'
				: developmentMode === 'review_decisions'
					? '汇总第 1 轮修改（开发单步）'
				: developmentMode === 'whole_recheck'
					? stringValue(operation.parameters?.execution_mode) === 'stop_after'
						? '第 1 轮全文复核（历史开发单步）'
						: '本地收尾检查（开发单步）'
				: developmentMode === 'transcript_quality_gate'
					? '进入校时前检查（开发单步）'
				: partialDiarization
					? '说话人区分（开发单步）'
				: partialLocalization && partialLocalizationLabel
					? `${partialLocalizationLabel}（开发单步）`
				: operation.label?.trim() || OPERATION_LABELS[operation.kind],
		stage: actionPending === 'retry'
			? '正在重新提交'
			: cancellationInProgress
			? '正在取消，将在当前步骤结束后停止'
			: operation.status === 'cancelled'
			? activityTaskStatusLabel(operation.status)
			: stage || activityTaskStatusLabel(operation.status),
		detail: operation.error_message ?? '',
		progress: operation.status === 'running' && (operation.kind === 'english_asr' || operation.kind === 'speaker_diarization' || operation.kind === 'localization_draft' || operation.kind === 'dub_subtitle_generation' || operation.kind === 'semantic_tts_grouping' || operation.kind === 'media_export')
			? Math.max(0, Math.min(1, operation.progress ?? 0))
			: null,
		status: effectiveStatus,
		scope: operationActivityScope(operation),
		cancellable: (operation.status === 'queued' || operation.status === 'running')
			&& (operation.kind === 'english_asr' || operation.kind === 'speaker_diarization' || operation.kind === 'localization_draft' || operation.kind === 'dub_subtitle_generation' || operation.kind === 'semantic_tts_grouping' || operation.kind === 'media_export')
			&& !operation.cancel_requested,
		cancelPending: cancellationInProgress,
		actionPending,
		createdAt: operation.created_at,
		startedAt: operation.started_at,
		completedAt: operation.completed_at,
		engineId: (
			developmentMode === 'document_understanding'
			|| developmentMode === 'research'
			|| developmentMode === 'visual_evidence'
			|| developmentMode === 'entity_normalization'
			|| developmentMode === 'section_review'
			|| developmentMode === 'review_decisions'
			|| developmentMode === 'whole_recheck'
			|| developmentMode === 'transcript_quality_gate'
		)
			? undefined
			: stringValue(operation.result_summary?.engine_id) ?? stringValue(operation.parameters?.engine_id),
		semanticModelId: stringValue(operation.result_summary?.llm_model_id),
		sourceTrackId: stringValue(operation.result_summary?.source_track_id) ?? stringValue(operation.parameters?.source_track_id),
		resultCount: result.count,
		resultUnit: result.unit,
		durationMs: numberValue(
			operation.result_summary?.task_duration_ms
			?? (operation.kind === 'english_asr' || operation.kind === 'speaker_diarization' || operation.kind === 'localization_draft' || operation.kind === 'dub_subtitle_generation'
				? operation.result_summary?.duration_ms
				: null)
		),
		executionScope: partialAsr || partialDiarization || partialLocalization ? 'partial' : 'full',
		detailAvailable: operation.detail_available === true,
		...(summaryFacts.length ? { summaryFacts } : {}),
		...(stages ? { stages } : {}),
		steps,
		...(finalResult ? { finalResult } : {}),
		failureResult: operation.status === 'failed'
			? normalizeOperationErrorDetail(operation.error_message, operation.result_summary?.error_detail)
			: undefined
	};
}

export function activityTaskStatusLabel(status: ActivityTaskStatus) {
	return {
		queued: '等待执行',
		running: '处理中',
		needs_attention: '需要处理',
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
	if (task.executionScope === 'partial') return task.label;
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
	if (valueMs > 0 && valueMs < 1000) return '<1 秒';
	const totalSeconds = Math.max(0, Math.floor(valueMs / 1000));
	if (totalSeconds < 60) return `${totalSeconds} 秒`;
	const totalMinutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	if (totalMinutes < 60) return seconds ? `${totalMinutes} 分 ${seconds} 秒` : `${totalMinutes} 分`;
	const hours = Math.floor(totalMinutes / 60);
	const minutes = totalMinutes % 60;
	return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
}

export function formatActivityTimelinePosition(
	valueMs: number | null | undefined,
	frameRate = 30
) {
	if (typeof valueMs !== 'number' || !Number.isFinite(valueMs) || valueMs < 0) return '';
	const fps = Number.isFinite(frameRate) && frameRate > 0 ? frameRate : 30;
	const nominalFps = Math.max(1, Math.round(fps));
	let wholeSeconds = Math.floor(valueMs / 1000);
	const remainderMs = valueMs - wholeSeconds * 1000;
	let frame = Math.round(remainderMs * fps / 1000);
	if (frame >= nominalFps) {
		wholeSeconds += 1;
		frame = 0;
	}
	const hours = Math.floor(wholeSeconds / 3600);
	const minutes = Math.floor(wholeSeconds / 60) % 60;
	const seconds = wholeSeconds % 60;
	const parts = [
		hours ? `${hours}时` : '',
		minutes ? `${minutes}分` : '',
		seconds ? `${seconds}秒` : '',
		frame ? `${frame}帧` : ''
	].filter(Boolean);
	return parts.join('') || '0帧';
}

export function formatActivityTimelineDuration(
	valueMs: number | null | undefined,
	frameRate = 30
) {
	if (typeof valueMs !== 'number' || !Number.isFinite(valueMs) || valueMs < 0) return '';
	const fps = Number.isFinite(frameRate) && frameRate > 0 ? frameRate : 30;
	if (valueMs > 0 && valueMs < 1000 / fps) return '<1帧';
	return formatActivityTimelinePosition(valueMs, fps);
}

export function formatActivityTimelineRange(
	startMs: number | null | undefined,
	endMs: number | null | undefined,
	frameRate = 30
) {
	if (
		typeof startMs !== 'number'
		|| typeof endMs !== 'number'
		|| !Number.isFinite(startMs)
		|| !Number.isFinite(endMs)
	) return '';
	const safeStart = Math.max(0, startMs);
	const safeEnd = Math.max(safeStart, endMs);
	return `${formatActivityTimelinePosition(safeStart, frameRate)} – ${formatActivityTimelinePosition(safeEnd, frameRate)}`;
}

export function formatActivityTimelineText(value: string, frameRate = 30) {
	const milliseconds = (raw: string) => Number(raw.replaceAll(',', ''));
	const seconds = (raw: string) => Math.round(Number(raw) * 1000);
	return value
		.replace(
			/(?<![A-Za-z0-9_])(\d[\d,]*)\s*(?:-|–|—|~|～|至|到)\s*(\d[\d,]*)\s*(?:ms|毫秒)(?![A-Za-z_])/gi,
			(_match, start: string, end: string) => formatActivityTimelineRange(
				milliseconds(start),
				milliseconds(end),
				frameRate
			)
		)
		.replace(
			/(?<![A-Za-z0-9_])(\d[\d,]*)\s*(?:ms|毫秒)(?![A-Za-z_])/gi,
			(_match, raw: string) => formatActivityTimelinePosition(milliseconds(raw), frameRate)
		)
		.replace(
			/(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)\s*s\s*(?:-|–|—|~|～|至|到)\s*(\d+(?:\.\d+)?)\s*s(?![A-Za-z_])/gi,
			(_match, start: string, end: string) => formatActivityTimelineRange(
				seconds(start),
				seconds(end),
				frameRate
			)
		)
		.replace(
			/(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)\s*s(?![A-Za-z_])/gi,
			(_match, raw: string) => formatActivityTimelinePosition(seconds(raw), frameRate)
		);
}

function activityTaskStepElapsedMs(step: ActivityTaskStep, task?: ActivityTask, nowMs = Date.now()) {
	let durationMs = step.durationMs;
	if (step.status === 'running' && task) {
		const taskElapsedMs = activityTaskElapsedMs(task, nowMs);
		if (taskElapsedMs !== null && step.startedElapsedMs !== undefined) {
			durationMs = Math.max(
				durationMs ?? 0,
				taskElapsedMs - step.startedElapsedMs
			);
		} else {
			const completedStepMs = completedDurationBeforeStep(task, step.id);
			if (taskElapsedMs !== null) durationMs = Math.max(durationMs ?? 0, taskElapsedMs - completedStepMs);
		}
	}
	return durationMs;
}

export function activityTaskStepTimingLabel(step: ActivityTaskStep, task?: ActivityTask, nowMs = Date.now()) {
	const durationMs = activityTaskStepElapsedMs(step, task, nowMs);
	const duration = formatActivityTaskDuration(durationMs);
	const counts = [
		step.roundCount && step.roundCount > 0 ? `${step.roundCount} 轮` : '',
		step.batchCount && step.batchCount > 0 ? `${step.batchCount} 批` : ''
	].filter(Boolean);
	if (duration) return [duration, ...counts].join(' · ');
	return counts.join(' · ');
}

export function activityTaskStageTimingLabel(
	stage: ActivityTaskStage,
	task: ActivityTask,
	nowMs = Date.now()
) {
	if (stage.status !== 'running' || !stage.steps.some((step) => step.status === 'running')) {
		return formatActivityTaskDuration(stage.durationMs);
	}
	const liveSteps = stage.steps.map((step) => ({
		...step,
		durationMs: activityTaskStepElapsedMs(step, task, nowMs)
	}));
	return formatActivityTaskDuration(
		stageDuration(liveSteps, stage.layout, true) ?? stage.durationMs
	);
}

function completedDurationBeforeStep(task: ActivityTask, stepId: string) {
	if (!task.stages?.length) {
		return (task.steps ?? [])
			.filter((candidate) => candidate.status === 'success')
			.reduce((total, candidate) => total + (candidate.durationMs ?? 0), 0);
	}
	let elapsedMs = 0;
	for (const stage of task.stages) {
		const targetIndex = stage.steps.findIndex((candidate) => candidate.id === stepId);
		if (targetIndex < 0) {
			if (stage.status === 'success') {
				elapsedMs += stage.durationMs ?? stageDuration(stage.steps, stage.layout, true) ?? 0;
			}
			continue;
		}
		const completedBeforeTarget = stage.steps.slice(0, targetIndex)
			.filter((candidate) => candidate.status === 'success');
		if (stage.layout === 'parallel-join') {
			const parallel = completedBeforeTarget.filter((candidate) => candidate.execution === 'parallel');
			const joined = completedBeforeTarget.filter((candidate) => candidate.execution === 'join');
			elapsedMs += Math.max(0, ...parallel.map((candidate) => candidate.durationMs ?? 0));
			elapsedMs += joined.reduce((total, candidate) => total + (candidate.durationMs ?? 0), 0);
		} else {
			elapsedMs += completedBeforeTarget.reduce(
				(total, candidate) => total + (candidate.durationMs ?? 0),
				0
			);
		}
		return elapsedMs;
	}
	return elapsedMs;
}

export function formatActivityTaskTime(value: string | null | undefined) {
	if (!value) return '';
	const date = new Date(value);
	if (!Number.isFinite(date.getTime())) return '';
	const pad = (part: number) => String(part).padStart(2, '0');
	return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function activityTaskIsActive(task: ActivityTask) {
	return task.status === 'queued' || task.status === 'running';
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
