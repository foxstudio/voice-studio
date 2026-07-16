import { describe, expect, it } from 'vitest';
import {
	activityTaskAffectsTrack,
	activityTaskDisplayName,
	activityTaskElapsedMs,
	activityTaskProgress,
	activityTaskResultLabel,
	activityTaskSourceLabel,
	activityTaskStepTimingLabel,
	activityTaskSummary,
	formatActivityTaskDuration,
	operationActivityTask
} from './activity-notice';

describe('activity notice tasks', () => {
	it('summarizes a running task with real progress', () => {
		expect(activityTaskSummary([
			{ id: 'one', label: '听写 ASR 字幕', stage: '校准时间戳', progress: 0.426, status: 'running' }
		])).toMatchObject({ text: '听写 ASR 字幕 · 校准时间戳 · 43%', countLabel: '' });
	});

	it('uses an indeterminate state for short foreground work and exposes a multi-task count', () => {
		expect(activityTaskSummary([
			{ id: 'one', label: '清空 ASR 字幕轨', progress: null, status: 'running' },
			{ id: 'two', label: '分离人声与背景', progress: 0.5, status: 'running' }
		])).toMatchObject({ text: '清空 ASR 字幕轨 · 处理中', countLabel: '2 项运行中' });
	});

	it('normalizes backend operations and clamps progress', () => {
		const task = operationActivityTask({
			operation_id: 'op-1', project_id: 'project', kind: 'english_asr', status: 'running',
			label: null, progress: 1.4, error_code: null, error_message: null,
			cancel_requested: false, result_summary: { stage: '断句校对' }, parameters: {},
			created_at: '', started_at: '', completed_at: null
		});
		expect(task).toMatchObject({ id: 'operation:op-1', label: '从人声轨生成 ASR 字幕', stage: '断句校对' });
		expect(activityTaskDisplayName(task)).toBe('从人声轨生成 ASR 字幕');
		expect(activityTaskProgress(task)).toBe(100);
	});

	it('does not pretend coarse media operations have measurable progress', () => {
		const task = operationActivityTask({
			operation_id: 'op-2', project_id: 'project', kind: 'stems', status: 'running',
			label: null, progress: 0.05, error_code: null, error_message: null,
			cancel_requested: false, result_summary: { stage: '准备处理' }, parameters: {},
			created_at: '', started_at: '', completed_at: null
		});
		expect(activityTaskProgress(task)).toBeNull();
		expect(activityTaskSummary([task]).text).toBe('分离人声与背景音乐 · 准备处理');
	});

	it('does not confuse source media duration with operation elapsed time', () => {
		const task = operationActivityTask({
			operation_id: 'source-audio', project_id: 'project', kind: 'source_audio', status: 'success',
			label: null, progress: 1, error_code: null, error_message: null, cancel_requested: false,
			result_summary: { duration_ms: 665_000 }, parameters: {},
			created_at: '2026-07-15T08:00:00Z', started_at: '2026-07-15T08:00:01Z', completed_at: '2026-07-15T08:00:04Z'
		});

		expect(task.durationMs).toBeNull();
		expect(activityTaskElapsedMs(task)).toBe(3_000);
	});

	it('maps operation scope to the tracks that must be temporarily locked', () => {
		const task = operationActivityTask({
			operation_id: 'op-3', project_id: 'project', kind: 'english_asr', status: 'running',
			label: null, progress: 0.4, error_code: null, error_message: null,
			cancel_requested: false, result_summary: {}, parameters: {
				scope: { area: 'subtitle', exclusive: true, tracks: [{ id: 'vocals', role: 'input' }, { id: 'subtitles', role: 'output' }] }
			}, created_at: '', started_at: '', completed_at: null
		});
		expect(activityTaskAffectsTrack(task, 'subtitles')).toBe(true);
		expect(activityTaskAffectsTrack(task, 'vocals')).toBe(false);
		expect(task.cancellable).toBe(true);
	});

	it('keeps terminal tasks out of runtime locks', () => {
		expect(activityTaskAffectsTrack({
			id: 'done', label: '听写 ASR 字幕', status: 'success',
			scope: { trackIds: ['subtitles'], itemIds: [], area: 'subtitle', exclusive: true }
		}, 'subtitles')).toBe(false);
	});

	it('keeps repeated ASR operations as independent history runs with their own metadata', () => {
		const base = {
			project_id: 'project', kind: 'english_asr' as const, label: '听写字幕', progress: 1,
			error_code: null, error_message: null, cancel_requested: false, created_at: '2026-07-15T08:00:00Z',
			started_at: '2026-07-15T08:00:01Z', completed_at: '2026-07-15T08:01:00Z'
		};
		const first = operationActivityTask({
			...base, operation_id: 'asr-1', status: 'success',
			parameters: { engine_id: 'qwen3-asr-mlx', source_track_id: 'original' },
			result_summary: { engine_id: 'qwen3-asr-mlx', source_track_id: 'original', cue_count: 12 }
		});
		const second = operationActivityTask({
			...base, operation_id: 'asr-2', status: 'success',
			parameters: { engine_id: 'faster-whisper-turbo', source_track_id: 'vocals' },
			result_summary: { engine_id: 'faster-whisper-turbo', source_track_id: 'vocals', cue_count: 9 }
		});

		expect([first.id, second.id]).toEqual(['operation:asr-1', 'operation:asr-2']);
		expect(first).toMatchObject({ engineId: 'qwen3-asr-mlx', sourceTrackId: 'original', resultCount: 12 });
		expect(second).toMatchObject({ engineId: 'faster-whisper-turbo', sourceTrackId: 'vocals', resultCount: 9 });
		expect(activityTaskSourceLabel(second.sourceTrackId)).toBe('人声音轨');
		expect(activityTaskResultLabel(first)).toBe('12 条字幕');
		expect(first.steps).toHaveLength(7);
		expect(first.steps?.every((step) => step.status === 'success')).toBe(true);
	});

	it('maps only the current ASR run stage to a running todo step', () => {
		const task = operationActivityTask({
			operation_id: 'asr-running', project_id: 'project', kind: 'english_asr', status: 'running',
			label: '听写字幕', progress: 0.7, error_code: null, error_message: null, cancel_requested: false,
			parameters: { engine_id: 'qwen3-asr-mlx', source_track_id: 'vocals' },
			result_summary: { stage: '正在分析停顿与声学边界', preview_phase: 'timing_segmentation' },
			created_at: '2026-07-15T08:00:00Z', started_at: '2026-07-15T08:00:01Z', completed_at: null
		});

		expect(task.steps?.map((step) => step.status)).toEqual(['success', 'success', 'success', 'success', 'running', 'todo', 'todo']);
	});

	it('shows web research as its own ASR step', () => {
		const task = operationActivityTask({
			operation_id: 'asr-research', project_id: 'project', kind: 'english_asr', status: 'running',
			label: '听写字幕', progress: 0.3, error_code: null, error_message: null, cancel_requested: false,
			parameters: {}, result_summary: { stage: '正在判断是否需要联网核验' },
			created_at: '2026-07-15T08:00:00Z', started_at: '2026-07-15T08:00:01Z', completed_at: null
		});

		expect(task.steps?.map((step) => [step.id, step.status])).toEqual([
			['recognize', 'success'],
			['research', 'running'],
			['review', 'todo'],
			['timestamps', 'todo'],
			['boundaries', 'todo'],
			['boundary-review', 'todo'],
			['subtitles', 'todo']
		]);
	});

	it('maps completed ASR stage timings to their exact todo steps', () => {
		const task = operationActivityTask({
			operation_id: 'asr-timed', project_id: 'project', kind: 'english_asr', status: 'success',
			label: '听写字幕', progress: 1, error_code: null, error_message: null, cancel_requested: false,
			parameters: {},
			result_summary: {
				duration_ms: 86_345,
				task_duration_ms: 86_345,
				llm_model_id: 'deepseek-chat',
				task_stage_timings: {
					asr: { duration_ms: 12_345 },
					web_research: { duration_ms: 1_000 },
					text_review: { duration_ms: 60_000 },
					alignment: { duration_ms: 2_500 },
					audio_boundaries: { duration_ms: 1_250 },
					boundary_review: { duration_ms: 8_750 },
					subtitle_track: { duration_ms: 500 }
				},
				stage_timings: {
					asr: { duration_ms: 12_345 },
					web_research: { duration_ms: 1_000, status: 'completed', source_count: 3 },
					text_review: { duration_ms: 60_000 },
					alignment: { duration_ms: 2_500 },
					audio_boundaries: { duration_ms: 1_250 },
					boundary_review: {
						duration_ms: 8_750,
						rounds: [
							{ duration_ms: 5_000, batch_count: 2 },
							{ duration_ms: 3_000, batch_count: 1 }
						]
					}
				},
				task_step_results: {
					asr: {
						status: 'success',
						summary: '识别到 185 个原始语音片段。',
						metrics: [{ label: '原始片段', value: '185' }],
						sections: [{
							title: '识别样例',
							items: [{ title: '片段 1', text: 'A sample result.', meta: '00:00.000 - 00:01.200' }]
						}],
						notes: []
					},
					web_research: {
						status: 'warning',
						summary: '联网核验部分完成。',
						metrics: [{ label: '资料来源', value: 3 }],
						sections: [{
							title: '逐项查证结果',
							items: Array.from({ length: 8 }, (_, index) => ({
								title: `问题 ${index + 1}`,
								text: '查证结论',
								facts: [{ label: '产生的作用', value: index ? '背景参考' : '已用于修正识别文本' }],
								links: [{ title: 'Source', url: 'https://example.com', meta: 'web-search' }],
								tone: index ? 'neutral' : 'positive'
							}))
						}],
						notes: ['一个查询未返回结果']
					}
				}
			},
			created_at: '2026-07-15T08:00:00Z', started_at: '2026-07-15T08:00:01Z', completed_at: '2026-07-15T08:02:00Z'
		});

		expect(task.steps?.map(({ id, durationMs }) => [id, durationMs])).toEqual([
			['recognize', 12_345],
			['research', 1_000],
			['review', 60_000],
			['timestamps', 2_500],
			['boundaries', 1_250],
			['boundary-review', 8_750],
			['subtitles', 500]
		]);
		expect(task.steps?.[5]).toMatchObject({ roundCount: 2, batchCount: 3 });
		expect(task.steps?.[0].result).toMatchObject({
			status: 'success',
			summary: '识别到 185 个原始语音片段。',
			metrics: [{ label: '原始片段', value: '185' }]
		});
		expect(task.steps?.[1].result).toMatchObject({
			status: 'warning',
			notes: ['一个查询未返回结果']
		});
		expect(task.steps?.[1].result?.sections[0].title).toBe('逐项查证结果');
		expect(task.steps?.[1].result?.sections[0].items).toHaveLength(8);
		expect(task.steps?.[1].result?.sections[0].items[0]).toMatchObject({
			title: '问题 1',
			tone: 'positive',
			facts: [{ label: '产生的作用', value: '已用于修正识别文本' }],
			links: [{ title: 'Source', url: 'https://example.com', meta: 'web-search' }]
		});
		expect(task.steps?.[2].result?.summary).toContain('旧任务仅保留了状态和统计');
		expect(task.semanticModelId).toBe('deepseek-chat');
		expect(activityTaskStepTimingLabel(task.steps![5], task)).toBe('8 秒 · 2 轮 · 3 批');
	});

	it('maps localization draft progress, scope, timings, results and output count', () => {
		const task = operationActivityTask({
			operation_id: 'localization-running', project_id: 'project', kind: 'localization_draft', status: 'running',
			label: null, progress: 0.82, error_code: null, error_message: null, cancel_requested: false,
			parameters: { source_track_id: 'subtitles' },
			result_summary: {
				stage: '正在检查中文意思和阅读体验',
				stage_id: 'quality_review',
				localized_subtitle_count: 18,
				llm_model_id: 'deepseek-chat',
				task_stage_timings: {
					prepare_context: { duration_ms: 1_200 },
					research: { duration_ms: 2_300 },
					localize: { duration_ms: 4_500 },
					fit_segments: { duration_ms: 600 },
					segment_timing: { duration_ms: 800 },
					quality_review: { duration_ms: 400, running: true }
				},
				task_step_results: {
					prepare_context: {
						status: 'success',
						summary: '已梳理主题、人物关系和表达习惯。',
						metrics: [{ label: '人物', value: 2 }],
						sections: [],
						notes: []
					},
					localize: {
						status: 'success',
						summary: '已生成符合人物语气的中文初稿。',
						metrics: [{ label: '中文片段', value: '18' }],
						sections: [{
							title: '表达对比',
							items: [{ before: 'You nailed it.', after: '这事你办得漂亮', facts: [], links: [] }]
						}],
						notes: []
					}
				}
			},
			created_at: '2026-07-16T08:00:00Z', started_at: '2026-07-16T08:00:01Z', completed_at: null
		});

		expect(task).toMatchObject({
			label: '生成本土化字幕初稿',
			progress: 0.82,
			cancellable: true,
			semanticModelId: 'deepseek-chat',
			resultCount: 18,
			resultUnit: '条字幕',
			scope: { trackIds: ['localizedSubtitles'], area: 'subtitle', exclusive: true }
		});
		expect(activityTaskDisplayName(task)).toBe('生成本土化字幕初稿');
		expect(activityTaskResultLabel(task)).toBe('18 条字幕');
		expect(activityTaskAffectsTrack(task, 'localizedSubtitles')).toBe(true);
		expect(activityTaskAffectsTrack(task, 'subtitles')).toBe(false);
		expect(task.steps?.map(({ id, label, status, durationMs }) => ({ id, label, status, durationMs }))).toEqual([
			{ id: 'prepare_context', label: '理解原文与人物', status: 'success', durationMs: 1_200 },
			{ id: 'research', label: '查证文化与背景', status: 'success', durationMs: 2_300 },
			{ id: 'localize', label: '生成中文表达', status: 'success', durationMs: 4_500 },
			{ id: 'fit_segments', label: '调整字幕长度', status: 'success', durationMs: 600 },
			{ id: 'segment_timing', label: '安排字幕分段与时间', status: 'success', durationMs: 800 },
			{ id: 'quality_review', label: '复核语义与可读性', status: 'running', durationMs: 400 },
			{ id: 'write_track', label: '写入本土化字幕轨', status: 'todo', durationMs: undefined }
		]);
		expect(task.steps?.[0].result?.summary).toBe('已梳理主题、人物关系和表达习惯。');
		expect(task.steps?.[2].result?.sections[0].items[0]).toMatchObject({
			before: 'You nailed it.',
			after: '这事你办得漂亮'
		});
	});

	it('keeps localization draft cancellation disabled once cancellation is requested', () => {
		const task = operationActivityTask({
			operation_id: 'localization-cancelling', project_id: 'project', kind: 'localization_draft', status: 'running',
			label: null, progress: 0.3, error_code: null, error_message: null, cancel_requested: true,
			parameters: {}, result_summary: { stage: 'research' },
			created_at: '', started_at: '', completed_at: null
		});

		expect(task.cancellable).toBe(false);
		expect(task.cancelPending).toBe(true);
		expect(task.stage).toBe('正在取消，将在当前步骤结束后停止');
	});

	it('derives only the current step elapsed time from completed live step timings', () => {
		const task = operationActivityTask({
			operation_id: 'asr-live', project_id: 'project', kind: 'english_asr', status: 'running',
			label: '听写字幕', progress: 0.74, error_code: null, error_message: null, cancel_requested: false,
			parameters: {}, result_summary: {
				stage: '正在分析停顿与声学边界',
				task_stage_timings: {
					asr: { duration_ms: 12_000 },
					web_research: { duration_ms: 1_000 },
					text_review: { duration_ms: 20_000 },
					alignment: { duration_ms: 2_500 },
					audio_boundaries: { duration_ms: 1_000, running: true }
				}
			},
			created_at: '2026-07-15T08:00:00Z', started_at: '2026-07-15T08:00:01Z', completed_at: null
		});

		expect(task.steps?.[4].durationMs).toBe(1_000);
		expect(activityTaskStepTimingLabel(
			task.steps![4],
			task,
			Date.parse('2026-07-15T08:01:09Z')
		)).toBe('32 秒');
		expect(activityTaskStepTimingLabel(task.steps![5], task)).toBe('');
	});

	it('formats running and completed task durations without inventing missing end times', () => {
		const running = {
			id: 'running', label: '生成 ASR 字幕', status: 'running' as const,
			startedAt: '2026-07-15T08:00:00Z'
		};
		const completed = {
			id: 'completed', label: '生成 ASR 字幕', status: 'success' as const,
			startedAt: '2026-07-15T08:00:00Z', completedAt: '2026-07-15T09:02:03Z'
		};

		expect(formatActivityTaskDuration(activityTaskElapsedMs(running, Date.parse('2026-07-15T08:01:08Z')))).toBe('1 分 8 秒');
		expect(formatActivityTaskDuration(activityTaskElapsedMs(completed))).toBe('1 小时 2 分');
		expect(activityTaskElapsedMs({ ...completed, completedAt: null })).toBeNull();
	});

	it('covers duration formatting boundaries and invalid values', () => {
		expect(formatActivityTaskDuration(0)).toBe('0 秒');
		expect(formatActivityTaskDuration(59_999)).toBe('59 秒');
		expect(formatActivityTaskDuration(60_000)).toBe('1 分');
		expect(formatActivityTaskDuration(3_600_000)).toBe('1 小时');
		expect(formatActivityTaskDuration(null)).toBe('');
		expect(formatActivityTaskDuration(Number.NaN)).toBe('');
	});
});
