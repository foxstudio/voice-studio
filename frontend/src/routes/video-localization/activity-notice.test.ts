import { describe, expect, it } from 'vitest';
import type { VideoLocalizationOperation } from '$lib/api/types';
import {
	activityTaskAffectsTrack,
	activityTaskDisplayName,
	activityTaskElapsedMs,
	activityTaskIsActive,
	activityTaskProgress,
	activityTaskResultLabel,
	activityTaskReviewAction,
	activityTaskReviewTargets,
	activityTaskStepAttentionKind,
	activityTaskSourceLabel,
	activityTaskStepEntries,
	activityTaskStepTimingLabel,
	activityTaskSummary,
	formatActivityTaskDuration,
	formatActivityTimelineDuration,
	formatActivityTimelinePosition,
	formatActivityTimelineRange,
	formatActivityTimelineText,
	normalizeOperationErrorDetail,
	operationActivityTask,
	pendingOperationActivityTask,
	resolveActivityTaskStageStatus
} from './activity-notice';

describe('activity notice tasks', () => {
	it('keeps dubbing subtitle alignment visible on the localized subtitle track', () => {
		const task = operationActivityTask({
			operation_id: 'dub-subtitles',
			project_id: 'project',
			kind: 'dub_subtitle_generation',
			status: 'running',
			label: null,
			progress: 0.4,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: { engine_id: 'qwen3-asr-mlx' },
			result_summary: {},
			created_at: '2026-08-03T00:00:00Z',
			started_at: '2026-08-03T00:00:01Z',
			completed_at: null
		});

		expect(task).toMatchObject({
			label: '根据合成配音生成字幕',
			status: 'running',
			progress: 0.4,
			cancellable: true,
			scope: { trackIds: ['dub', 'localizedSubtitles'], area: 'subtitle', exclusive: true }
		});
		expect(activityTaskAffectsTrack(task, 'dub')).toBe(true);
		expect(activityTaskAffectsTrack(task, 'localizedSubtitles')).toBe(true);
	});

	it('shows all six synthesized-dub subtitle atomic steps in workflow order', () => {
		const stepIds = [
			'prepare_track',
			'transcribe_track',
			'proofread_text',
			'align_words',
			'segment_subtitles',
			'commit'
		];
		const task = operationActivityTask({
			operation_id: 'dub-subtitles-six-steps',
			project_id: 'project',
			kind: 'dub_subtitle_generation',
			status: 'running',
			label: null,
			progress: 0.5,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: { engine_id: 'qwen3-asr-mlx' },
			result_summary: {
				task_stage_groups: [{
					id: 'dub_subtitles',
					label: '根据合成配音生成字幕',
					order: 10,
					atomic_tasks: stepIds.map((id, index) => ({
						id,
						label: id,
						description: id,
						order: (index + 1) * 10,
						execution: 'serial',
						optional: false,
						depends_on: index ? [stepIds[index - 1]] : [],
						output_contract_version: `${id}-output-v1`
					}))
				}],
				task_step_results: Object.fromEntries(stepIds.map((id, index) => [
					id,
					{
						label: id,
						order: (index + 1) * 10,
						status: index < 3 ? 'success' : 'pending',
						summary: `${id} result`
					}
				]))
			},
			created_at: '',
			started_at: '',
			completed_at: null
		});

		expect(task.steps?.map((step) => step.id)).toEqual(stepIds);
		expect(task.stages?.[0].steps.map((step) => step.id)).toEqual(stepIds);
	});

	it('shows dub subtitle results separately from folded debug information', () => {
		const task = operationActivityTask({
			operation_id: 'dub-subtitles-detail',
			project_id: 'project',
			kind: 'dub_subtitle_generation',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: { engine_id: 'qwen3-asr-mlx' },
			result_summary: {
				stage: '准确的配音字幕已保存',
				stage_id: 'commit',
				dub_subtitle_count: 2,
				task_step_results: {
					proofread_text: {
						label: '用本土化台词校对文字',
						order: 3,
						status: 'success',
						purpose: '以本土化台词为文字基准。',
						summary: '已纠正 1 处 ASR 差异。',
						metrics: [{ label: '已纠正差异', value: '1' }],
						sections: [{
							title: '校对示例',
							items: [{
								title: '文字校对',
								before: '三集',
								after: '分三级',
								before_label: 'ASR 听写',
								after_label: '最终字幕'
							}]
						}],
						debug: {
							description: '用于核对 ASR 与参考台词。',
							metrics: [{ label: 'ASR 调用', value: '1' }],
							sections: [],
							notes: ['差异不是最终字幕错误。']
						}
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		const result = task.steps?.find((step) => step.id === 'proofread_text')?.result;
		expect(result).toMatchObject({
			purpose: '以本土化台词为文字基准。',
			summary: '已纠正 1 处 ASR 差异。',
			metrics: [{ label: '已纠正差异', value: '1' }],
			sections: [{
				title: '校对示例',
				items: [{
					before: '三集',
					after: '分三级',
					beforeLabel: 'ASR 听写',
					afterLabel: '最终字幕'
				}]
			}],
			debug: {
				description: '用于核对 ASR 与参考台词。',
				metrics: [{ label: 'ASR 调用', value: '1' }],
				notes: ['差异不是最终字幕错误。']
			}
		});
	});

	it('does not count failed foreground work as still running', () => {
		expect(activityTaskIsActive({
			id: 'tts-init:failed',
			label: '生成合成配音',
			stage: '提交失败',
			progress: 0,
			status: 'failed',
			scope: { trackIds: ['dub'], itemIds: [], area: 'generate', exclusive: false },
			createdAt: '2026-08-02T15:00:00Z'
		})).toBe(false);
		expect(activityTaskIsActive({
			id: 'tts-init:queued',
			label: '生成合成配音',
			stage: '准备提交',
			progress: 0,
			status: 'queued',
			scope: { trackIds: ['dub'], itemIds: [], area: 'generate', exclusive: false },
			createdAt: '2026-08-02T15:00:00Z'
		})).toBe(true);
	});

	it('uses backend stage groups for parallel ASR branches and their explicit join', () => {
		const task = operationActivityTask({
			operation_id: 'initial-analysis-v1',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'initial_analysis'
			},
			result_summary: {
				stage_id: 'initial_analysis',
				execution_scope: 'partial',
				task_duration_ms: 43_538,
				branch_duration_ms: {
					raw_asr: 30_000,
					diarization: 43_000,
					wall: 43_538
				},
				task_stage_groups: [{
					id: 'initial_analysis',
					label: '初始语音分析',
					description: '先同时生成原始听写并区分声音，再汇合结果。',
					atomic_tasks: [
						{ id: 'asr', label: '生成原始听写', execution: 'parallel', depends_on: [] },
						{ id: 'diarization', label: '区分说话人', execution: 'parallel', depends_on: [] },
						{
							id: 'initial_analysis_join',
							label: '汇合听写与说话人',
							execution: 'join',
							depends_on: ['asr', 'diarization']
						}
					]
				}],
				task_stage_timings: {
					asr: { duration_ms: 30_000 },
					diarization: { duration_ms: 43_000 },
					initial_analysis_join: { duration_ms: 3 }
				},
				task_step_results: {
					asr: { label: '生成原始听写', order: 10, status: 'success', summary: '听写完成。' },
					diarization: { label: '区分说话人', order: 20, status: 'success', summary: '说话人完成。' },
					initial_analysis_join: {
						label: '汇合听写与说话人',
						order: 25,
						status: 'success',
						summary: '已经汇合。',
						debug: {
							description: '用于定位输入。',
							metrics: [
								{ label: '输出契约', value: 'asr-joined-transcript-v1' },
								{ label: '内部路径', value: '/private/tmp/secret.json' }
							],
							sections: [],
							notes: []
						}
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.stages).toHaveLength(1);
		expect(task.stages?.[0]).toMatchObject({
			id: 'initial_analysis',
			label: '初始语音分析',
			status: 'success',
			durationMs: 43_538,
			layout: 'parallel-join'
		});
		expect(task.stages?.[0].steps.map(({ id, execution, dependsOn }) => ({ id, execution, dependsOn }))).toEqual([
			{ id: 'asr', execution: 'parallel', dependsOn: [] },
			{ id: 'diarization', execution: 'parallel', dependsOn: [] },
			{ id: 'initial_analysis_join', execution: 'join', dependsOn: ['asr', 'diarization'] }
		]);
		expect(activityTaskStepEntries(task).map(({ step }) => step.id)).toEqual([
			'asr',
			'diarization',
			'initial_analysis_join'
		]);
		expect(task.stages?.[0].steps[2].result?.debug?.metrics[1].value)
			.toBe('已记录（本地路径不在界面展示）');
		expect(resolveActivityTaskStageStatus(task.stages?.[0].steps ?? [])).toBe('success');
	});

	it('uses the dependency critical path for a serial-parallel-join stage duration', () => {
		const task = operationActivityTask({
			operation_id: 'localization-creation-duration',
			project_id: 'project',
			kind: 'localization_draft',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				task_stage_groups: [{
					id: 'localization_creation',
					label: '创作并验收中文台词',
					atomic_tasks: [
						{
							id: 'lock_localization_creation_context',
							label: '锁定本土化创作策略',
							execution: 'join',
							depends_on: ['adjudicate_localization_evidence_v3']
						},
						{
							id: 'generate_localization_spoken_script',
							label: '生成全文本土化初稿',
							execution: 'serial',
							depends_on: ['lock_localization_creation_context']
						},
						{
							id: 'review_localization_fidelity',
							label: '复核原意与事实',
							execution: 'parallel',
							depends_on: ['generate_localization_spoken_script']
						},
						{
							id: 'review_localization_naturalness',
							label: '盲测中文自然度',
							execution: 'parallel',
							depends_on: ['generate_localization_spoken_script']
						},
						{
							id: 'finalize_localization_spoken_script',
							label: '本土化台词终审',
							execution: 'join',
							depends_on: [
								'review_localization_fidelity',
								'review_localization_naturalness'
							]
						}
					]
				}],
				task_stage_timings: {
					lock_localization_creation_context: { duration_ms: 100 },
					generate_localization_spoken_script: { duration_ms: 200 },
					review_localization_fidelity: { duration_ms: 300 },
					review_localization_naturalness: { duration_ms: 400 },
					finalize_localization_spoken_script: { duration_ms: 50 }
				},
				task_step_results: {
					lock_localization_creation_context: {
						label: '锁定本土化创作策略',
						order: 55,
						status: 'success'
					},
					generate_localization_spoken_script: {
						label: '生成全文本土化初稿',
						order: 60,
						status: 'success'
					},
					review_localization_fidelity: {
						label: '复核原意与事实',
						order: 70,
						status: 'success'
					},
					review_localization_naturalness: {
						label: '盲测中文自然度',
						order: 71,
						status: 'success'
					},
					finalize_localization_spoken_script: {
						label: '本土化台词终审',
						order: 80,
						status: 'success'
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.stages?.[0]).toMatchObject({
			id: 'localization_creation',
			layout: 'parallel-join',
			durationMs: 750
		});
	});

	it('keeps explicit parallel child states when the latest progress message comes from its sibling', () => {
		const task = operationActivityTask({
			operation_id: 'localization-parallel-live-state',
			project_id: 'project',
			kind: 'localization_draft',
			status: 'running',
			label: '生成本土化字幕',
			progress: 0.55,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				stage_id: 'review_localization_naturalness',
				task_stage_groups: [{
					id: 'localization_creation',
					label: '创作并验收本土化初稿',
					atomic_tasks: [
						{
							id: 'review_localization_fidelity',
							label: '复核原意与事实',
							execution: 'parallel',
							depends_on: ['generate_localization_spoken_script']
						},
						{
							id: 'review_localization_naturalness',
							label: '盲测中文自然度',
							execution: 'parallel',
							depends_on: ['generate_localization_spoken_script']
						}
					]
				}],
				task_stage_timings: {
					review_localization_fidelity: {
						duration_ms: 0,
						started_elapsed_ms: 367_676,
						running: true
					},
					review_localization_naturalness: {
						duration_ms: 24_679,
						started_elapsed_ms: 368_663
					}
				},
				task_step_results: {
					review_localization_fidelity: {
						label: '复核原意与事实',
						order: 70,
						status: 'running'
					},
					review_localization_naturalness: {
						label: '盲测中文自然度',
						order: 71,
						status: 'success'
					}
				}
			},
			created_at: '2026-08-09T02:00:15',
			started_at: '2026-08-09T02:00:15',
			completed_at: ''
		});

		expect(Object.fromEntries(
			activityTaskStepEntries(task).map(({ step }) => [step.id, step.status])
		)).toEqual({
			review_localization_fidelity: 'running',
			review_localization_naturalness: 'success'
		});
	});

	it('closes every unfinished child step when its parent task is cancelled', () => {
		const task = operationActivityTask({
			operation_id: 'cancelled-localization',
			project_id: 'project',
			kind: 'localization_draft',
			status: 'cancelled',
			label: '生成本土化字幕',
			progress: 0.55,
			error_code: null,
			error_message: '已取消',
			cancel_requested: true,
			parameters: {},
			result_summary: {
				stage_id: 'finalize_localization_spoken_script',
				task_stage_groups: [
					{
						id: 'localization_creation',
						label: '创作并验收本土化初稿',
						atomic_tasks: [
							{
								id: 'generate_localization_spoken_script',
								label: '生成全文本土化初稿',
								execution: 'serial',
								depends_on: []
							},
							{
								id: 'finalize_localization_spoken_script',
								label: '本土化台词终审',
								execution: 'serial',
								depends_on: ['generate_localization_spoken_script']
							}
						]
					},
					{
						id: 'localization_alignment',
						label: '映射语义时间并生成双轨',
						atomic_tasks: [{
							id: 'align_localization_semantics',
							label: '本地映射语义时间',
							execution: 'serial',
							depends_on: ['finalize_localization_spoken_script']
						}]
					}
				],
				task_step_results: {
					generate_localization_spoken_script: {
						label: '生成全文本土化初稿',
						order: 60,
						status: 'success',
						summary: '初稿已生成。'
					},
					finalize_localization_spoken_script: {
						label: '本土化台词终审',
						order: 80,
						status: 'running',
						summary: '正在终审。'
					},
					align_localization_semantics: {
						label: '本地映射语义时间',
						order: 90,
						status: 'todo',
						summary: '等待前置步骤。'
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(activityTaskStepEntries(task).map(({ step }) => [
			step.id,
			step.status
		])).toEqual([
			['generate_localization_spoken_script', 'success'],
			['finalize_localization_spoken_script', 'cancelled'],
			['align_localization_semantics', 'cancelled']
		]);
		expect(task.stages?.map(({ id, status }) => [id, status])).toEqual([
			['localization_creation', 'cancelled'],
			['localization_alignment', 'cancelled']
		]);
	});

	it('never leaves waiting child steps active after the parent task fails', () => {
		const task = operationActivityTask({
			operation_id: 'failed-localization',
			project_id: 'project',
			kind: 'localization_draft',
			status: 'failed',
			label: '生成本土化字幕',
			progress: 1,
			error_code: 'VIDEO_LOCALIZATION_TRACK_QUALITY_GATE_BLOCKED',
			error_message: '质量门未通过',
			cancel_requested: false,
			parameters: {},
			result_summary: {
				stage_id: 'validate_localization_tracks',
				task_stage_groups: [{
					id: 'localization_tracks',
					label: '生成并验收字幕轨',
					atomic_tasks: [
						{
							id: 'validate_localization_tracks',
							label: '验收本土化字幕轨',
							execution: 'serial',
							depends_on: []
						},
						{
							id: 'commit_localization_tracks',
							label: '保存正式字幕轨',
							execution: 'serial',
							depends_on: ['validate_localization_tracks']
						}
					]
				}],
				task_step_results: {
					review_localization_fidelity: {
						label: '复核原意与事实',
						order: 120,
						status: 'needs_review',
						summary: '发现一个需要终审修正的问题。'
					},
					validate_localization_tracks: {
						label: '验收本土化字幕轨',
						order: 130,
						status: 'failed',
						summary: '质量门未通过。'
					},
					commit_localization_tracks: {
						label: '保存正式字幕轨',
						order: 140,
						status: 'todo',
						summary: ''
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(Object.fromEntries(
			activityTaskStepEntries(task).map(({ step }) => [
				step.id,
				step.status
			])
		)).toMatchObject({
			review_localization_fidelity: 'success',
			validate_localization_tracks: 'failed',
			commit_localization_tracks: 'cancelled'
		});
		expect(task.stages?.[0]?.status).toBe('failed');
	});

	it('fills formal ASR branch timings from atomic diagnostics and keeps parallel wall time', () => {
		const task = operationActivityTask({
			operation_id: 'formal-asr-timings',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: { execution_mode: 'full' },
			result_summary: {
				stage_timings: {
					asr: { duration_ms: 30_000 },
					diarization: { duration_ms: 43_000 },
					initial_analysis_join: { duration_ms: 3 }
				},
				task_stage_timings: {
					asr: { duration_ms: 43_500 }
				},
				task_stage_groups: [{
					id: 'initial_analysis',
					label: '初始语音分析',
					atomic_tasks: [
						{ id: 'asr', label: '生成原始听写', execution: 'parallel', depends_on: [] },
						{ id: 'diarization', label: '区分说话人', execution: 'parallel', depends_on: [] },
						{
							id: 'initial_analysis_join',
							label: '汇合听写与说话人',
							execution: 'join',
							depends_on: ['asr', 'diarization']
						}
					]
				}],
				task_step_results: {
					asr: { label: '生成原始听写', order: 10, status: 'success', summary: '听写完成。' },
					diarization: { label: '区分说话人', order: 20, status: 'success', summary: '说话人完成。' },
					initial_analysis_join: {
						label: '汇合听写与说话人',
						order: 25,
						status: 'success',
						summary: '已经汇合。'
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.steps?.map(({ id, durationMs }) => [id, durationMs])).toEqual([
			['asr', 30_000],
			['diarization', 43_000],
			['initial_analysis_join', 3]
		]);
		expect(task.stages?.[0].durationMs).toBe(43_003);
		expect(activityTaskStepTimingLabel(task.steps![1], task)).toBe('43 秒');
	});

	it('keeps optional workflow steps visible when they were not executed', () => {
		const task = operationActivityTask({
			operation_id: 'optional-asr-steps',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				stage_id: 'whole_recheck_r1',
				task_stage_groups: [{
					id: 'transcript_review',
					label: '理解与校对全文',
					description: '按需要继续复查。',
					atomic_tasks: [
						{
							id: 'whole_recheck_r1',
							label: '第 1 轮全文复核',
							execution: 'serial',
							depends_on: []
						},
						{
							id: 'section_review_r2',
							label: '第 2 轮分段复查',
							execution: 'serial',
							depends_on: [],
							optional: true
						}
					]
				}],
				task_step_results: {
					whole_recheck_r1: {
						label: '第 1 轮全文复核',
						order: 70,
						status: 'success',
						summary: '第 1 轮已经完成。'
					},
					section_review_r2: {
						label: '第 2 轮分段复查',
						order: 80,
						status: 'skipped',
						summary: '本次没有触发。'
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.steps?.map(({ id }) => id)).toEqual([
			'whole_recheck_r1',
			'section_review_r2'
		]);
		expect(task.stages?.[0].steps.map(({ id }) => id)).toEqual([
			'whole_recheck_r1',
			'section_review_r2'
		]);
		expect(activityTaskStepEntries(task).map(({ step }) => step.id)).toEqual([
			'whole_recheck_r1',
			'section_review_r2'
		]);
	});

	it('does not infer an unstarted optional review round as completed after the workflow advances', () => {
		const task = operationActivityTask({
			operation_id: 'running-after-optional-round',
			project_id: 'project',
			kind: 'english_asr',
			status: 'running',
			label: null,
			progress: 0.58,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				stage_id: 'alignment',
				stage: '正在生成逐词时间码',
				task_stage_groups: [
					{
						id: 'transcript_review',
						label: '理解与校对全文',
						atomic_tasks: [
							{
								id: 'whole_recheck_r1',
								label: '确定定点收尾范围',
								execution: 'serial',
								depends_on: []
							},
							{
								id: 'section_review_r2',
								label: '定点检查剩余问题',
								execution: 'serial',
								depends_on: [],
								optional: true
							}
						]
					},
					{
						id: 'timing',
						label: '时间与字幕整理',
						atomic_tasks: [
							{
								id: 'alignment',
								label: '对齐逐词时间',
								execution: 'serial',
								depends_on: ['whole_recheck_r1']
							}
						]
					}
				],
				task_step_results: {
					whole_recheck_r1: {
						label: '确定定点收尾范围',
						order: 100,
						status: 'success',
						summary: '第 2 轮已经完成。'
					},
					section_review_r2: {
						label: '定点检查剩余问题',
						order: 110,
						status: 'todo'
					},
					alignment: {
						label: '对齐逐词时间',
						order: 200,
						status: 'running',
						summary: '正在生成逐词时间码。'
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(activityTaskStepEntries(task).map(({ step }) => [
			step.id,
			step.status
		])).toEqual([
			['whole_recheck_r1', 'success'],
			['section_review_r2', 'todo'],
			['alignment', 'running']
		]);
	});

	it('keeps an optional workflow step after it actually ran and retains its debugging details', () => {
		const task = operationActivityTask({
			operation_id: 'executed-optional-asr-step',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				stage_id: 'visual_evidence',
				task_stage_groups: [{
					id: 'context',
					label: '理解内容并补充证据',
					description: '只在确实需要时查看画面。',
					atomic_tasks: [{
						id: 'visual_evidence',
						label: '画面取证',
						execution: 'serial',
						depends_on: ['understand_document'],
						optional: true
					}]
				}],
				task_step_results: {
					visual_evidence: {
						label: '画面取证',
						order: 35,
						status: 'success',
						summary: '已经查看必要画面。',
						debug: {
							description: '用于核对截图输入和识图结果。',
							metrics: [{ label: '截图数量', value: '2' }],
							sections: [],
							notes: []
						}
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.steps?.map(({ id }) => id)).toEqual(['visual_evidence']);
		expect(task.stages?.[0].steps.map(({ id }) => id)).toEqual(['visual_evidence']);
		expect(task.steps?.[0].result?.debug).toEqual({
			description: '用于核对截图输入和识图结果。',
			metrics: [{ label: '截图数量', value: '2' }],
			sections: [],
			notes: []
		});
	});

	it('keeps a skipped optional step when it records a real degraded run', () => {
		const task = operationActivityTask({
			operation_id: 'degraded-optional-asr-step',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				stage_id: 'visual_evidence',
				task_stage_groups: [{
					id: 'context',
					label: '理解内容并补充证据',
					description: '只在确实需要时查看画面。',
					atomic_tasks: [{
						id: 'visual_evidence',
						label: '画面取证',
						execution: 'serial',
						depends_on: ['understand_document'],
						optional: true
					}]
				}],
				task_step_results: {
					visual_evidence: {
						label: '画面取证',
						order: 35,
						status: 'skipped',
						summary: '识图模型不可用，本步骤已降级跳过。',
						debug: {
							description: '已经尝试执行，用于核对降级原因。',
							metrics: [{ label: '模型请求', value: '1' }],
							sections: [],
							notes: ['模型不支持图片输入。']
						}
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.steps?.map(({ id }) => id)).toEqual(['visual_evidence']);
		expect(task.stages?.[0].steps.map(({ id }) => id)).toEqual(['visual_evidence']);
		expect(activityTaskStepEntries(task).map(({ step }) => step.id)).toEqual(['visual_evidence']);
		expect(task.steps?.[0].result?.debug?.metrics).toEqual([
			{ label: '模型请求', value: '1' }
		]);
	});

	it('shows document understanding as one isolated development task', () => {
		const task = operationActivityTask({
			operation_id: 'document-understanding-v1',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'understand_document',
				engine_id: 'auto',
				input_initial_analysis_operation_id: 'initial-analysis-v1'
			},
			result_summary: {
				stage: '理解全文并规划复查已完成（开发单步）',
				stage_id: 'understand_document',
				execution_scope: 'partial',
				task_duration_ms: 12_000,
				segment_count: 80,
				llm_model_id: 'deepseek-chat',
				task_stage_groups: [{
					id: 'transcript_review',
					label: '理解与校对全文',
					description: '理解整段对话、核对名称，并按需要复查原始听写。',
					atomic_tasks: [{
						id: 'understand_document',
						label: '理解全文并规划复查',
						description: '只通读全文并规划后续复查。',
						execution: 'serial',
						depends_on: ['initial_analysis_join']
					}]
				}],
				task_stage_timings: {
					understand_document: { duration_ms: 12_000 }
				},
				task_step_results: {
					understand_document: {
						label: '理解全文并规划复查',
						order: 30,
						status: 'success',
						purpose: '只通读全文，不联网也不修改听写。',
						summary: '已规划 4 个连续复查区块。',
						metrics: [
							{ label: '讲话片段', value: '80' },
							{ label: '复查区块', value: '4' }
						],
						sections: [{
							title: '全文概览',
							items: [{ title: '内容概述', text: '这是一段 AI 芯片访谈。' }]
						}],
						notes: ['后续任务未执行。']
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.label).toBe('理解全文并规划复查（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.finalResult).toBeUndefined();
		expect(task.resultCount).toBe(80);
		expect(task.resultUnit).toBe('个讲话片段');
		expect(task.engineId).toBeUndefined();
		expect(task.semanticModelId).toBe('deepseek-chat');
		expect(task.stages).toEqual([
			expect.objectContaining({
				id: 'transcript_review',
				label: '理解与校对全文',
				status: 'success',
				durationMs: 12_000,
				layout: 'linear',
				steps: [
					expect.objectContaining({
						id: 'understand_document',
						status: 'success',
						durationMs: 12_000,
						dependsOn: ['initial_analysis_join']
					})
				]
			})
		]);
		expect(task.steps?.[0].result?.sections[0].items[0].text)
			.toBe('这是一段 AI 芯片访谈。');
	});

	it('shows research as one isolated development task from either request parameters or its persisted stage', () => {
		const resultSummary = {
			stage: '资料查询已完成（开发单步）',
			stage_id: 'research',
			execution_scope: 'partial',
			task_duration_ms: 9_000,
			evidence_count: 3,
			query_count: 4,
			llm_model_id: 'deepseek-chat',
			task_stage_groups: [{
				id: 'transcript_review',
				label: '理解与校对全文',
				description: '本次开发单步只查询全文理解提出的疑点。',
				atomic_tasks: [{
					id: 'research',
					label: '核对名称与背景',
					description: '查询需要核对的名称与背景。',
					execution: 'serial',
					depends_on: ['understand_document']
				}]
			}],
			task_stage_timings: {
				research: { duration_ms: 9_000 }
			},
			task_step_results: {
				research: {
					label: '核对名称与背景',
					order: 40,
					status: 'success',
					purpose: '只查询需要核对的疑点，不修改听写文字。',
					summary: '已形成 3 条可追溯资料。',
					metrics: [
						{ label: '查询次数', value: '4' },
						{ label: '有效资料', value: '3' }
					],
					sections: [{
						title: '资料与结论',
						items: [{
							title: 'Seedance 2',
							text: '找到与 Seedance 2 直接相关的官方资料，交由下一步判断规范名称。',
							facts: [{ label: '来源', value: '官方文档' }],
							links: [{ title: '官方文档', url: 'https://example.com/seedance-2' }]
						}]
					}],
					notes: ['本步骤没有修改任何听写片段。']
				}
			}
		};
		const task = operationActivityTask({
			operation_id: 'research-v1',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'research',
				engine_id: 'auto',
				input_document_understanding_operation_id: 'document-understanding-v1'
			},
			result_summary: resultSummary,
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.label).toBe('核对名称与背景（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.finalResult).toBeUndefined();
		expect(task.resultCount).toBe(3);
		expect(task.resultUnit).toBe('条资料');
		expect(task.engineId).toBeUndefined();
		expect(task.semanticModelId).toBe('deepseek-chat');
		expect(task.stages).toEqual([
			expect.objectContaining({
				id: 'transcript_review',
				status: 'success',
				durationMs: 9_000,
				layout: 'linear',
				steps: [
					expect.objectContaining({
						id: 'research',
						label: '核对名称与背景',
						status: 'success',
						durationMs: 9_000,
						dependsOn: ['understand_document']
					})
				]
			})
		]);
		expect(task.steps?.[0].result).toMatchObject({
			summary: '已形成 3 条可追溯资料。',
			sections: [{
				title: '资料与结论',
				items: [expect.objectContaining({
					title: 'Seedance 2',
					text: '找到与 Seedance 2 直接相关的官方资料，交由下一步判断规范名称。'
				})]
			}]
		});

		const persistedStageTask = operationActivityTask({
			operation_id: 'research-v1-stage-only',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: { engine_id: 'auto' },
			result_summary: resultSummary,
			created_at: '',
			started_at: '',
			completed_at: ''
		});
		expect(persistedStageTask.label).toBe('核对名称与背景（开发单步）');
		expect(persistedStageTask.steps?.map((step) => step.id)).toEqual(['research']);
		expect(persistedStageTask.engineId).toBeUndefined();
	});

	it('shows entity normalization as one isolated development task from either request parameters or its persisted stage', () => {
		const resultSummary = {
			stage: '统一名称与术语已完成（开发单步）',
			stage_id: 'normalize_entities',
			execution_scope: 'partial',
			task_duration_ms: 7_000,
			change_count: 2,
			llm_model_id: 'kimi-k3',
			task_stage_groups: [{
				id: 'transcript_review',
				label: '理解与校对全文',
				description: '本次开发单步只统一有证据支持的名称与术语。',
				atomic_tasks: [{
					id: 'normalize_entities',
					label: '统一名称与术语',
					description: '根据资料证据统一规范写法。',
					execution: 'serial',
					depends_on: ['research']
				}]
			}],
			task_stage_timings: {
				normalize_entities: { duration_ms: 7_000 }
			},
			task_step_results: {
				normalize_entities: {
					label: '统一名称与术语',
					order: 45,
					status: 'success',
					purpose: '只修改有明确依据的名称和项目术语。',
					summary: '确认 1 个规范名称，修改 2 处文字。',
					metrics: [
						{ label: '规范名称', value: '1' },
						{ label: '文字修改', value: '2' }
					],
					sections: [{
						title: '文字修改',
						items: [{
							title: 'asr_0001',
							text: 'Duan Feeny → JoAnne Feeney'
						}]
					}],
					notes: []
				}
			}
		};
		const operation = {
			operation_id: 'normalize-entities-v1',
			project_id: 'project',
			kind: 'english_asr' as const,
			status: 'success' as const,
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'normalize_entities',
				input_research_evidence_operation_id: 'research-v1'
			},
			result_summary: resultSummary,
			created_at: '',
			started_at: '',
			completed_at: ''
		};

		const task = operationActivityTask(operation);
		expect(task.label).toBe('统一名称与术语（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.resultCount).toBe(2);
		expect(task.resultUnit).toBe('处文字修改');
		expect(task.engineId).toBeUndefined();
		expect(task.semanticModelId).toBe('kimi-k3');
		expect(task.steps?.map((step) => step.id)).toEqual(['normalize_entities']);
		expect(task.steps?.[0]).toMatchObject({
			label: '统一名称与术语',
			status: 'success',
			durationMs: 7_000
		});
		expect(task.stages?.[0].steps[0].dependsOn).toEqual(['research']);
		expect(task.steps?.[0].result?.sections[0].items[0].text)
			.toBe('Duan Feeny → JoAnne Feeney');

		const persistedStageTask = operationActivityTask({
			...operation,
			operation_id: 'normalize-entities-v1-stage-only',
			parameters: {}
		});
		expect(persistedStageTask.label).toBe('统一名称与术语（开发单步）');
		expect(persistedStageTask.steps?.map((step) => step.id)).toEqual(['normalize_entities']);
	});

	it('shows section review as one read-only development task', () => {
		const operation = {
			operation_id: 'section-review-v1',
			project_id: 'project',
			kind: 'english_asr' as const,
			status: 'success' as const,
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'section_review_r1',
				input_document_understanding_operation_id: 'understanding-v1',
				input_entity_normalization_operation_id: 'entities-v1'
			},
			result_summary: {
				stage: '第 1 轮分段复查已完成（开发单步）',
				stage_id: 'section_review_r1',
				execution_scope: 'partial',
				task_duration_ms: 8_000,
				issue_count: 3,
				llm_model_id: 'kimi-k3',
				task_stage_groups: [{
					id: 'transcript_review',
					label: '理解与校对全文',
					description: '本次只逐段列出可能的听写问题。',
					atomic_tasks: [{
						id: 'section_review_r1',
						label: '第 1 轮分段复查',
						description: '只提问题，不修改字幕。',
						execution: 'serial',
						depends_on: ['normalize_entities', 'understand_document']
					}]
				}],
				task_stage_timings: {
					section_review_r1: { duration_ms: 8_000 }
				},
				task_step_results: {
					section_review_r1: {
						label: '第 1 轮分段复查',
						order: 50,
						status: 'success',
						purpose: '逐段检查可能的听写错误。',
						summary: '已检查 4/4 个区块，找出 3 个可能问题。',
						metrics: [],
						sections: [],
						notes: ['这一步没有修改字幕。']
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		};

		const task = operationActivityTask(operation);
		expect(task.label).toBe('第 1 轮分段复查（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.resultCount).toBe(3);
		expect(task.resultUnit).toBe('个可能问题');
		expect(task.engineId).toBeUndefined();
		expect(task.semanticModelId).toBe('kimi-k3');
		expect(task.steps?.map((step) => step.id)).toEqual(['section_review_r1']);
		expect(task.stages?.[0].steps[0].dependsOn).toEqual([
			'normalize_entities',
			'understand_document'
		]);
	});

	it('shows review decisions as one isolated development task with applied changes', () => {
		const operation = {
			operation_id: 'review-decisions-v1',
			project_id: 'project',
			kind: 'english_asr' as const,
			status: 'success' as const,
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'review_decisions_r1',
				input_section_review_operation_id: 'section-review-v1'
			},
			result_summary: {
				stage: '汇总第 1 轮修改已完成（开发单步）',
				stage_id: 'review_decisions_r1',
				execution_scope: 'partial',
				task_duration_ms: 6_000,
				applied_change_count: 2,
				llm_model_id: 'kimi-k3',
				task_stage_groups: [{
					id: 'transcript_review',
					label: '理解与校对全文',
					description: '逐轮检查并安全应用听写修改。',
					atomic_tasks: [{
						id: 'review_decisions_r1',
						label: '汇总第 1 轮修改',
						description: '判断上一轮疑点并应用通过安全校验的修改。',
						execution: 'serial',
						depends_on: ['section_review_r1']
					}]
				}],
				task_stage_timings: {
					review_decisions_r1: { duration_ms: 6_000 }
				},
				task_step_results: {
					review_decisions_r1: {
						label: '汇总第 1 轮修改',
						order: 60,
						status: 'success',
						purpose: '判断上一轮疑点，只有通过安全规则的修改才写入当前快照。',
						summary: '已判断 8 个疑点，实际修改 2 处。',
						metrics: [],
						sections: [],
						notes: []
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		};

		const task = operationActivityTask(operation);
		expect(task.label).toBe('汇总第 1 轮修改（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.resultCount).toBe(2);
		expect(task.resultUnit).toBe('处实际修改');
		expect(task.engineId).toBeUndefined();
		expect(task.semanticModelId).toBe('kimi-k3');
		expect(task.steps?.map((step) => step.id)).toEqual(['review_decisions_r1']);
		expect(task.stages?.[0].steps[0].dependsOn).toEqual(['section_review_r1']);
	});

	it('shows whole recheck as one read-only development task', () => {
		const operation = {
			operation_id: 'whole-recheck-v1',
			project_id: 'project',
			kind: 'english_asr' as const,
			status: 'success' as const,
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'whole_recheck_r1',
				input_review_decisions_operation_id: 'review-decisions-v1',
				input_document_understanding_operation_id: 'understanding-v1'
			},
			result_summary: {
				stage: '第 1 轮全文复核已完成（开发单步）',
				stage_id: 'whole_recheck_r1',
				execution_scope: 'partial',
				task_duration_ms: 7_000,
				next_section_count: 2,
				llm_model_id: 'kimi-k3',
				task_stage_groups: [{
					id: 'transcript_review',
					label: '理解与校对全文',
					description: '重新通读本轮修改后的完整字幕。',
					atomic_tasks: [{
						id: 'whole_recheck_r1',
						label: '第 1 轮全文复核',
						description: '只决定是否结束或进入下一轮。',
						execution: 'serial',
						depends_on: ['review_decisions_r1', 'understand_document']
					}]
				}],
				task_stage_timings: {
					whole_recheck_r1: { duration_ms: 7_000 }
				},
				task_step_results: {
					whole_recheck_r1: {
						label: '第 1 轮全文复核',
						order: 70,
						status: 'warning',
						purpose: '重新通读完整字幕。',
						summary: '建议再检查两个区块。',
						metrics: [],
						sections: [],
						notes: ['本步骤不修改字幕。']
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		};

		const task = operationActivityTask(operation);
		expect(task.label).toBe('第 1 轮全文复核（历史开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.resultCount).toBe(2);
		expect(task.resultUnit).toBe('个下一轮区块');
		expect(task.engineId).toBeUndefined();
		expect(task.semanticModelId).toBe('kimi-k3');
		expect(task.steps?.map((step) => step.id)).toEqual(['whole_recheck_r1']);
		expect(task.stages?.[0].steps[0].dependsOn).toEqual([
			'review_decisions_r1',
			'understand_document'
		]);
	});

	it('shows current whole recheck as a local closure with review locations', () => {
		const operation = {
			operation_id: 'whole-recheck-current',
			project_id: 'project',
			kind: 'english_asr' as const,
			status: 'success' as const,
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'development_target',
				development_source_operation_id: 'formal-asr',
				development_target_step_id: 'whole_recheck_r1'
			},
			result_summary: {
				stage: '本地收尾检查已完成（开发单步）',
				stage_id: 'whole_recheck_r1',
				execution_scope: 'partial',
				unresolved_item_count: 2,
				task_stage_groups: [{
					id: 'transcript_review',
					label: '理解与校对全文',
					description: '只按本地规则收尾检查并保留建议复听项。',
					atomic_tasks: [{
						id: 'whole_recheck_r1',
						label: '本地收尾检查',
						description: '不调用模型，不修改字幕。',
						execution: 'serial',
						depends_on: ['review_decisions_r1', 'understand_document']
					}]
				}]
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		};

		const task = operationActivityTask(operation);

		expect(task.label).toBe('本地收尾检查（开发单步）');
		expect(task.resultCount).toBe(2);
		expect(task.resultUnit).toBe('处建议复听');
	});

	it('shows the pre-alignment quality gate as one local development task', () => {
		const operation = {
			operation_id: 'quality-gate-v1',
			project_id: 'project',
			kind: 'english_asr' as const,
			status: 'success' as const,
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'transcript_quality_gate',
				input_whole_recheck_operation_id: 'whole-recheck-v1'
			},
			result_summary: {
				stage: '进入校时前检查已完成（开发单步）',
				stage_id: 'transcript_quality_gate',
				execution_scope: 'partial',
				task_duration_ms: 8,
				decision: 'ready_for_alignment',
				can_start_alignment: true,
				task_stage_groups: [{
					id: 'transcript_review',
					label: '理解与校对全文',
					description: '只判断是否可以开始校时。',
					atomic_tasks: [{
						id: 'transcript_quality_gate',
						label: '进入校时前检查',
						description: '不调用模型，不修改字幕。',
						execution: 'serial',
						depends_on: [
							'whole_recheck_r1',
							'whole_recheck_r2'
						],
						dependency_mode: 'latest_completed'
					}]
				}],
				task_stage_timings: {
					transcript_quality_gate: { duration_ms: 8 }
				},
				task_step_results: {
					transcript_quality_gate: {
						label: '进入校时前检查',
						order: 290,
						status: 'warning',
						purpose: '确认文字是否已经稳定。',
						summary: '已自动继续校时，建议复听 1 处。',
						metrics: [{ label: '模型调用', value: '0' }],
						sections: [],
						review_targets: [{
							title: '确认专名发音',
							location: '00:00:12:03 – 00:00:14:18',
							detail: '请结合原音确认。',
							excerpt: 'C dance 2.0',
							start_ms: 12100,
							end_ms: 14600
						}],
						notes: ['本步骤不修改字幕。']
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		};

		const task = operationActivityTask(operation);
		expect(task.label).toBe('进入校时前检查（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.engineId).toBeUndefined();
		expect(task.steps?.map((step) => step.id)).toEqual([
			'transcript_quality_gate'
		]);
		expect(task.stages?.[0].steps[0].dependsOn).toEqual([
			'whole_recheck_r1',
			'whole_recheck_r2'
		]);
		expect(task.stages?.[0].steps[0].dependencyMode).toBe(
			'latest_completed'
		);
		expect(task.steps?.[0].result?.reviewTargets).toEqual([{
			title: '确认专名发音',
			location: '00:00:12:03 – 00:00:14:18',
			detail: '请结合原音确认。',
			excerpt: 'C dance 2.0',
			startMs: 12100,
			endMs: 14600
		}]);
	});

	it('shows visual evidence as one isolated development task from either request parameters or its persisted stage', () => {
		const resultSummary = {
			stage: '画面取证已完成（开发单步）',
			stage_id: 'visual_evidence',
			execution_scope: 'partial',
			task_duration_ms: 6_000,
			frame_count: 5,
			llm_model_id: 'qwen-vl',
			task_stage_groups: [{
				id: 'context_evidence',
				label: '上下文取证',
				description: '补充后续校对需要的外部资料和画面证据。',
				atomic_tasks: [{
					id: 'visual_evidence',
					label: '画面取证',
					description: '从相关时间段抽取代表性画面并整理证据。',
					execution: 'serial',
					depends_on: ['research']
				}]
			}],
			task_stage_timings: {
				visual_evidence: { duration_ms: 6_000 }
			},
			task_step_results: {
				visual_evidence: {
					label: '画面取证',
					order: 50,
					status: 'success',
					purpose: '从相关时间段抽取画面证据，不修改听写文字。',
					summary: '已抽取 5 张代表性截图。',
					metrics: [
						{ label: '截图数量', value: '5' },
						{ label: '覆盖时间段', value: '3' }
					],
					sections: [{
						title: '画面证据',
						items: [{
							title: '截图 1',
							text: '画面中出现产品名称 Seedance 2。',
							meta: '00:01:20',
							facts: [{ label: '关联疑点', value: '产品名称' }],
							links: []
						}]
					}],
					notes: ['本步骤没有修改任何听写片段。']
				}
			}
		};
		const task = operationActivityTask({
			operation_id: 'visual-evidence-v1',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'visual_evidence',
				engine_id: 'auto'
			},
			result_summary: resultSummary,
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.label).toBe('画面取证（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.finalResult).toBeUndefined();
		expect(task.resultCount).toBe(5);
		expect(task.resultUnit).toBe('张截图');
		expect(task.engineId).toBeUndefined();
		expect(task.semanticModelId).toBe('qwen-vl');
		expect(task.stages).toEqual([
			expect.objectContaining({
				id: 'context_evidence',
				status: 'success',
				durationMs: 6_000,
				layout: 'linear',
				steps: [
					expect.objectContaining({
						id: 'visual_evidence',
						label: '画面取证',
						status: 'success',
						durationMs: 6_000,
						dependsOn: ['research']
					})
				]
			})
		]);
		expect(task.steps?.[0].result).toMatchObject({
			summary: '已抽取 5 张代表性截图。',
			sections: [{
				title: '画面证据',
				items: [expect.objectContaining({
					title: '截图 1',
					text: '画面中出现产品名称 Seedance 2。',
					meta: '00:01:20'
				})]
			}]
		});

		const persistedStageTask = operationActivityTask({
			operation_id: 'visual-evidence-v1-stage-only',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: { engine_id: 'auto' },
			result_summary: resultSummary,
			created_at: '',
			started_at: '',
			completed_at: ''
		});
		expect(persistedStageTask.label).toBe('画面取证（开发单步）');
		expect(persistedStageTask.steps?.map((step) => step.id)).toEqual(['visual_evidence']);
		expect(persistedStageTask.engineId).toBeUndefined();
	});

	it('removes duplicated sentence punctuation from persisted task result descriptions', () => {
		const task = operationActivityTask({
			operation_id: 'legacy-punctuation',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				stage: '已完成',
				task_step_results: {
					understand_document: {
						label: '理解全文并核对名称',
						order: 30,
						status: 'success',
						purpose: '理解全文。。',
						summary: '已形成全文理解卡。。',
						sections: [{
							title: '分段重点',
							items: [{
								text: "重点看：确认 'Adil' 是否为正确拼写。；统一 'Seedance' 的指代。。"
							}]
						}]
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.steps?.find((step) => step.id === 'understand_document')?.result).toMatchObject({
			purpose: '理解全文。',
			summary: '已形成全文理解卡。',
			sections: [{
				items: [{
					text: "重点看：确认 'Adil' 是否为正确拼写；统一 'Seedance' 的指代。"
				}]
			}]
		});
	});

	it('keeps safe CLI diagnostics in debug rather than the error summary', () => {
		const diagnostic = '（安全诊断：events=2;exit_code=1;failure_reason=model）';
		for (const detail of [undefined, { message: `模型不可用${diagnostic}` }]) {
			const result = normalizeOperationErrorDetail(`模型不可用${diagnostic}`, detail);
			expect(result?.summary).toBe('模型不可用');
			expect(result?.debug?.notes).toEqual([diagnostic]);
		}
	});

	it('shows final localization quality failures with affected subtitles and advice', () => {
		const result = normalizeOperationErrorDetail(
			'整片中文终审达到 3 轮上限后仍有问题。',
			{
				remaining_count: 1,
				items: [{
					id: 'quality_003',
					severity: 'major',
					category: 'coherence',
					reason: '这句把下一段的反应提前说了。',
					suggestion: '按原文顺序把反应放回下一条字幕。',
					subtitle_ids: ['localized_0012', 'localized_0013'],
					source_cue_ids: ['cue_0012', 'cue_0013']
				}],
				advice: ['现有字幕轨未被覆盖。']
			}
		);

		expect(result?.status).toBe('failed');
		expect(result?.sections[0].items[0]).toMatchObject({
			title: '字幕 quality_003',
			text: '这句把下一段的反应提前说了。',
			facts: [
				{ label: '涉及字幕', value: 'localized_0012、localized_0013' },
				{ label: '原文 cue', value: 'cue_0012、cue_0013' },
				{ label: '问题级别', value: 'major' },
				{ label: '问题类型', value: 'coherence' },
				{ label: '修改建议', value: '按原文顺序把反应放回下一条字幕。' }
			]
		});
		expect(result?.notes).toContain('现有字幕轨未被覆盖。');
	});

	it('reads issues and exposes ASR segment and cue ids', () => {
		const result = normalizeOperationErrorDetail('整篇 ASR 终审未通过。', {
			items: [],
			issues: [{
				id: 'quality_004',
				segment_ids: ['asr_0011', 'asr_0012'],
				cue_ids: ['cue_0011'],
				reason: '专名仍与上下文不一致。'
			}]
		});

		expect(result?.sections[0].items[0]).toMatchObject({
			title: '字幕 quality_004',
			text: '专名仍与上下文不一致。',
			meta: 'cue_0011',
			facts: [
				{ label: 'ASR 片段', value: 'asr_0011、asr_0012' },
				{ label: '原文 cue', value: 'cue_0011' }
			]
		});
	});

	it('normalizes structured operation failures into readable subtitle details', () => {
		const result = normalizeOperationErrorDetail(
			'时间线终审后仍有字幕阅读过快。',
			{
				count: 3,
				limits: { max_cps: 12.1, max_visible_chars: 32 },
				remaining: 3,
				items: [{
					id: 'localized_0050',
					source_cue_ids: ['cue_0063'],
					text: '而是把视频读成一帧帧的画面 就像这样',
					duration_ms: 1140,
					visible_chars: 17,
					cps: 14.91,
					max_chars_for_duration: 13,
					violations: ['阅读速度超过每秒12.1字，需要精简表达']
				}]
			}
		);

		expect(result).toMatchObject({
			status: 'failed',
			summary: '时间线终审后仍有字幕阅读过快。',
			metrics: [
				{ label: '问题条目', value: '3' },
				{ label: '阅读速度上限', value: '12.1 字/秒' },
				{ label: '字幕字数上限', value: '32' }
			],
			coverage: { mode: 'focused', shownCount: 1, totalCount: 3, unit: '条字幕' }
		});
		expect(result?.sections[0].items[0]).toMatchObject({
			title: '字幕 localized_0050',
			text: '而是把视频读成一帧帧的画面 就像这样',
			meta: 'cue_0063',
			facts: expect.arrayContaining([
				{ label: '原文 cue', value: 'cue_0063' },
				{ label: '时长', value: '1秒4帧' },
				{ label: '字数', value: '17 字' },
				{ label: 'CPS', value: '14.91 字/秒' },
				{ label: '问题', value: '阅读速度超过每秒12.1字，需要精简表达' }
			])
		});
		expect(result?.notes).toContain('优先精简对应字幕的中文表达，保留事实、数字和语气重点后再重试。');
	});

	it('uses remaining entries when a structured failure has no items field', () => {
		const result = normalizeOperationErrorDetail('仍有问题', {
			count: 1,
			remaining: [{ subtitle_id: 'localized_0008', source_cue_id: 'cue_0010', text: '测试字幕' }]
		});

		expect(result?.sections[0].items[0]).toMatchObject({
			title: '字幕 localized_0008',
			meta: 'cue_0010',
			text: '测试字幕'
		});
	});

	it('turns a simple operation error into a readable failure result', () => {
		const task = operationActivityTask({
			operation_id: 'failed-simple', project_id: 'project', kind: 'stems', status: 'failed',
			label: null, progress: 0, error_code: 'FAILED', error_message: '分离失败',
			cancel_requested: false, result_summary: {}, parameters: {},
			created_at: '', started_at: '', completed_at: ''
		});

		expect(task.detail).toBe('分离失败');
		expect(task.failureResult).toMatchObject({
			status: 'failed',
			summary: '分离失败',
			notes: ['修正上方原因后重新提交或重试任务。']
		});
	});

	it('describes non-subtitle prerequisite failures without subtitle-specific copy', () => {
		const result = normalizeOperationErrorDetail(
			'Import a source video before extracting audio',
			{
				code: 'VIDEO_LOCALIZATION_SOURCE_MISSING',
				rule_id: 'source_media_prerequisite',
				rule: '任务必须在源媒体准备完成后才能继续。',
				advice: ['先导入源视频，再重新提交抽取原音轨任务。']
			}
		);

		expect(result?.purpose).toBe('定位导致任务中止的原因，并提供可执行的处理方向。');
		expect(result?.notes).toEqual(['先导入源视频，再重新提交抽取原音轨任务。']);
		expect(result?.purpose).not.toContain('字幕');
		expect(result?.notes.join('')).not.toContain('ASR 字幕');
	});

	it('normalizes a task-level final result independently from step results', () => {
		const task = operationActivityTask({
			operation_id: 'localization-final', project_id: 'project', kind: 'localization_draft', status: 'success',
			label: null, progress: 1, error_code: null, error_message: null, cancel_requested: false,
			parameters: {}, result_summary: {
				stage: '已完成',
				task_final_result: {
					status: 'warning',
					purpose: '汇总整项任务结果。',
					summary: '字幕已写入，另有 2 条建议试听。',
					metrics: [{ label: '建议试听', value: 2 }],
					sections: [],
					notes: ['发布前完成试听。']
				}
			},
			created_at: '', started_at: '', completed_at: ''
		});

		expect(task.finalResult).toMatchObject({
			status: 'warning',
			purpose: '汇总整项任务结果。',
			summary: '字幕已写入，另有 2 条建议试听。',
			metrics: [{ label: '建议试听', value: '2' }],
			notes: ['发布前完成试听。']
		});
	});

	it('shows localization target execution as a development node instead of a formal run', () => {
		const task = operationActivityTask({
			operation_id: 'localization-development-target',
			project_id: 'project',
			kind: 'localization_draft',
			status: 'success',
			label: '本土化流程开发',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'development_target',
				development_target_step_id: 'lock_localization_source',
				development_session_id: 'session-1',
				scope: {
					area: 'development',
					exclusive: true,
					tracks: [
						{ id: 'subtitles', role: 'input' },
						{ id: 'development_snapshot', role: 'output' }
					]
				}
			},
			result_summary: {
				stage: '开发节点已完成',
				stage_id: 'lock_localization_source',
				execution_mode: 'development_target',
				task_step_results: {
					lock_localization_source: {
						label: '固定本次英文源数据',
						order: 10,
						status: 'success',
						summary: '源输入已锁定。',
						metrics: [],
						sections: [],
						notes: []
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.label).toBe('固定本次英文源数据（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(activityTaskDisplayName(task)).toBe(
			'固定本次英文源数据（开发单步）'
		);
		expect(task.finalResult).toBeUndefined();
		expect(task.scope?.area).toBe('development');
	});

	it('preserves an evidence step that was not needed instead of calling it completed', () => {
		const task = operationActivityTask({
			operation_id: 'localization-research-not-needed',
			project_id: 'project',
			kind: 'localization_draft',
			status: 'success',
			label: '本土化流程开发',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'development_target',
				development_target_step_id: 'collect_localization_research_evidence_v3',
				development_session_id: 'session-1'
			},
			result_summary: {
				stage: '开发节点已结束',
				stage_id: 'collect_localization_research_evidence_v3',
				execution_mode: 'development_target',
				task_step_results: {
					collect_localization_research_evidence_v3: {
						label: '查询必要资料',
						order: 40,
						status: 'not_needed',
						summary: '全文没有需要联网确认的问题，本步骤无需执行。',
						metrics: [
							{ label: '查询问题', value: 0 },
							{ label: '找到资料', value: 0 }
						],
						sections: [],
						notes: ['未调用搜索服务。']
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.steps?.[0]).toMatchObject({
			id: 'collect_localization_research_evidence_v3',
			status: 'success',
			result: {
				status: 'not_needed',
				summary: '全文没有需要联网确认的问题，本步骤无需执行。',
				notes: ['未调用搜索服务。']
			}
		});
	});

	it('does not hide a failed child when a legacy parent says success', () => {
		const task = operationActivityTask({
			operation_id: 'legacy-inconsistent-success',
			project_id: 'project',
			kind: 'localization_draft',
			status: 'success',
			label: '本土化流程开发',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'development_target',
				development_target_step_id: 'validate_localization_tracks',
				development_session_id: 'session-1'
			},
			result_summary: {
				stage: '开发节点已完成',
				stage_id: 'validate_localization_tracks',
				task_stage_groups: [{
					id: 'quality',
					label: '质量检查',
					order: 10,
					atomic_tasks: [{
						id: 'validate_localization_tracks',
						label: '检查本土化结果',
						order: 10,
						execution: 'serial'
					}]
				}],
				task_step_results: {
					validate_localization_tracks: {
						label: '检查本土化结果',
						order: 10,
						status: 'failed',
						summary: '发现阻断项。',
						metrics: [],
						sections: [],
						notes: []
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.status).toBe('failed');
		expect(task.steps?.[0].status).toBe('failed');
		expect(task.stages?.[0].status).toBe('failed');
	});

	it('turns warning details into a clear manual review checklist', () => {
		const result = {
			status: 'warning' as const,
			summary: '发现一个专名写法不一致。',
			metrics: [],
			sections: [{
				title: '专名问题',
				items: [{
					title: '字幕 cue_0012',
					meta: '00:12.400 - 00:15.200',
					text: 'CineSense 2 与已查证的 Seedance 2 不一致。',
					tone: 'warning' as const,
					facts: [],
					links: []
				}]
			}],
			notes: []
		};

		expect(activityTaskReviewAction('理解全文并核对名称')).toContain('名称与术语的正确写法');
		expect(activityTaskReviewAction('进入校时前检查')).toBe(
			'自动流程已经继续；建议结合原音复听下面片段，必要时再修改原文。'
		);
		expect(activityTaskReviewAction('裁决本土化证据')).toBe(
			'检查证据引用和补查建议是否对得上原文；未确认的事实先保持保守写法，不要提前补进中文。'
		);
		expect(activityTaskReviewAction('执行限定补查')).toBe(
			'检查新资料或相邻截图是否对准了原文疑点；它们还要经过下一次裁决，暂时不要直接写进中文。'
		);
		expect(activityTaskReviewTargets(result)).toEqual([{
			title: '字幕 cue_0012',
			location: '00:12.400 - 00:15.200',
			detail: 'CineSense 2 与已查证的 Seedance 2 不一致。'
		}]);
		expect(activityTaskStepAttentionKind({
			...result,
			reviewTargets: activityTaskReviewTargets(result)
		})).toBe('manual_review');
		expect(activityTaskStepAttentionKind({
			...result,
			attentionKind: 'advisory'
		})).toBe('advisory');
		expect(activityTaskStepAttentionKind({
			status: 'warning',
			summary: '有一项非阻断预算提示。',
			metrics: [],
			sections: [],
			notes: []
		})).toBe('advisory');
		expect(activityTaskStepAttentionKind({
			...result,
			reviewTargets: activityTaskReviewTargets(result)
		}, 'analyze_localization_risks')).toBe('manual_review');
	});

	it('de-duplicates identical review targets before rendering the checklist', () => {
		const repeated = {
			title: '语义段 0059',
			location: '04:57.039–04:58.559 · 低把握',
			detail: '核对系统更正后的内容是否符合原音和上下文。'
		};
		const result = {
			status: 'warning' as const,
			summary: '有一个低把握边界。',
			metrics: [],
			sections: [],
			notes: [],
			reviewTargets: [repeated, { ...repeated }]
		};

		expect(activityTaskReviewTargets(result)).toEqual([repeated]);
	});

	it('summarizes a non-blocking quality gate with replay advisories', () => {
		const task = operationActivityTask({
			operation_id: 'quality-gate',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: '进入校时前检查（开发单步）',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'transcript_quality_gate'
			},
			result_summary: {
				stage_id: 'transcript_quality_gate',
				execution_scope: 'partial',
				review_target_count: 3,
				can_start_alignment: true
			},
			created_at: '2026-07-25T15:36:06Z',
			started_at: '2026-07-25T15:36:06Z',
			completed_at: '2026-07-25T15:36:07Z'
		});

		expect(activityTaskResultLabel(task)).toBe('3 处建议复听');
	});

	it('shows reliable media facts directly on completed task cards', () => {
		const task = operationActivityTask({
			operation_id: 'stems-facts', project_id: 'project', kind: 'stems', status: 'success',
			label: null, progress: 1, error_code: null, error_message: null, cancel_requested: false,
			parameters: {}, result_summary: {
				duration_ms: 66_000,
				sample_rate: 48_000,
				channels: 2,
				separation_engine_id: 'bs-roformer-viperx-1297:residual-v1',
				track_count: 2,
				separation_status: '已完成'
			},
			created_at: '', started_at: '', completed_at: ''
		});

		expect(task.summaryFacts).toEqual([
			{ label: '音频时长', value: '1 分 6 秒' },
			{ label: '采样率', value: '48 kHz' },
			{ label: '声道', value: '立体声' },
			{ label: '分离引擎', value: 'bs-roformer-viperx-1297:residual-v1' },
			{ label: '生成轨道', value: '2 条' },
			{ label: '处理状态', value: '已完成' }
		]);
	});

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

	it('locks the destination track while an operation is being submitted', () => {
		const task = pendingOperationActivityTask(
			'localization_draft',
			'submit:project:localization',
			'正在保存修改并提交任务'
		);

		expect(task).toMatchObject({
			label: '生成本土化字幕',
			stage: '正在保存修改并提交任务',
			status: 'queued',
			progress: null
		});
			expect(activityTaskAffectsTrack(task, 'localizedSubtitles')).toBe(true);
			expect(task.cancellable).toBe(false);
			expect(task.steps?.map(({ id }) => id)).toEqual([
				'lock_localization_source', 'lock_localization_context_intent',
				'analyze_localization_document', 'collect_localization_research_evidence_v3',
				'collect_localization_visual_evidence_v3', 'adjudicate_localization_evidence_v3',
				'generate_localization_spoken_script', 'review_localization_fidelity',
				'review_localization_naturalness', 'finalize_localization_spoken_script',
				'align_localization_semantics', 'adjudicate_localization_alignment',
				'build_localization_dual_tracks', 'validate_localization_tracks',
				'commit_localization_tracks'
			]);
	});

	it('shows required ASR steps while the operation is being submitted', () => {
		const task = pendingOperationActivityTask('english_asr', 'submit:project:asr', '正在提交 ASR 任务');

		expect(task.steps?.map(({ id }) => id)).toEqual([
			'asr', 'understand_document', 'research', 'normalize_entities',
			'section_review_r1', 'review_decisions_r1', 'whole_recheck_r1',
			'transcript_quality_gate',
			'alignment', 'audio_boundaries', 'boundary_review', 'subtitle_track'
		]);
	});

	it('keeps legacy ASR diarization steps when the saved task still records them', () => {
		const task = operationActivityTask({
			operation_id: 'legacy-asr-with-diarization',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: null,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				task_step_results: {
					asr: {
						label: '生成原始听写',
						order: 10,
						status: 'success',
						summary: '听写完成。'
					},
					diarization: {
						label: '区分说话人',
						order: 20,
						status: 'success',
						summary: '说话人区分完成。'
					},
					initial_analysis_join: {
						label: '汇合听写与说话人',
						order: 25,
						status: 'success',
						summary: '汇合完成。'
					}
				}
			},
			created_at: '',
			started_at: '',
			completed_at: ''
		});

		expect(task.steps?.map(({ id }) => id)).toEqual([
			'asr',
			'diarization',
			'initial_analysis_join'
		]);
	});

	it('uses the workflow contract when a live step update only contains status', () => {
		const task = operationActivityTask({
			operation_id: 'live-asr-status-only-update',
			project_id: 'project',
			kind: 'english_asr',
			status: 'running',
			label: null,
			progress: 0.2,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {},
			result_summary: {
				task_stage_groups: [{
					id: 'initial_analysis',
					label: '生成原始听写',
					description: '并行分析听写和声音角色。',
					order: 10,
					atomic_tasks: [
						{ id: 'asr', label: '生成原始听写', description: '生成文字。', order: 10, execution: 'parallel' },
						{ id: 'diarization', label: '区分匿名说话人', description: '区分声音角色。', order: 20, execution: 'parallel' },
						{ id: 'initial_analysis_join', label: '汇合听写与说话人', description: '汇合两路结果。', order: 25, execution: 'join', depends_on: ['asr', 'diarization'] }
					]
				}],
				task_step_results: {
					asr: { label: '生成原始听写', order: 10, status: 'running' },
					diarization: { status: 'running' },
					initial_analysis_join: { status: 'todo' }
				}
			},
			created_at: '',
			started_at: '',
			completed_at: null
		});

		expect(task.steps?.map(({ id, label }) => [id, label])).toEqual([
			['asr', '生成原始听写'],
			['diarization', '区分匿名说话人'],
			['initial_analysis_join', '汇合听写与说话人']
		]);
		expect(task.stages?.[0].steps.map(({ id }) => id)).toEqual([
			'asr',
			'diarization',
			'initial_analysis_join'
		]);
	});

	it('waits for the backend workflow definition before showing dub steps', () => {
		const task = pendingOperationActivityTask(
			'dub_subtitle_generation',
			'submit:project:dub-subtitles',
			'正在提交配音字幕任务'
		);

		expect(task.label).toBe('根据合成配音生成字幕');
		expect(task.steps).toBeUndefined();
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
		});

	it('shows a partial ASR success as one completed development step only', () => {
		const task = operationActivityTask({
			operation_id: 'asr-partial',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: '听写字幕',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'asr',
				source_track_id: 'vocals'
			},
			result_summary: {
				stage: '原始听写已完成（开发断点）',
				stage_id: 'asr',
				execution_scope: 'partial',
				sample: {
					language: 'en',
					segment_count: 80,
					sample_count: 8,
					sample_ratio: 0.1,
					quality_summary: {
						status: 'passed',
						has_text: true,
						has_segments: true,
						timestamps_monotonic: true,
						incomplete_range_count: 0,
						trailing_gap_ms: 16,
						warning_codes: []
					},
					raw_text: 'Duan Feeny',
					segments: Array.from({ length: 8 }, (_, index) => ({
						segment_id: `asr_${String(index + 1).padStart(4, '0')}`,
						start_ms: index * 1000,
						end_ms: (index + 1) * 1000,
						text: `Segment ${index + 1}.`
					}))
				}
			},
			created_at: '2026-07-24T11:27:21',
			started_at: '2026-07-24T11:27:21',
			completed_at: '2026-07-24T11:27:48'
		});

		expect(task.label).toBe('原始听写（开发单步）');
		expect(activityTaskDisplayName(task)).toBe('原始听写（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.finalResult).toBeUndefined();
		expect(task.steps?.map(({ id, status }) => [id, status])).toEqual([
				['asr', 'success'],
				['understand_document', 'todo'],
				['research', 'todo'],
				['normalize_entities', 'todo'],
				['section_review_r1', 'todo'],
				['review_decisions_r1', 'todo'],
				['whole_recheck_r1', 'todo'],
				['transcript_quality_gate', 'todo'],
				['alignment', 'todo'],
				['audio_boundaries', 'todo'],
				['boundary_review', 'todo'],
				['subtitle_track', 'todo']
		]);
		expect(task.steps?.[0].result).toMatchObject({
			status: 'success',
			summary: '已生成 80 个原始语音片段，本次从全文抽查 8 个样例，默认展示其中 3 个。',
			coverage: {
				mode: 'summary',
				shownCount: 8,
				totalCount: 80
			},
			notes: [
				'原始听写完整性检查通过：80 个片段均有内容、时间顺序正常，未发现未完成区间；末段距音频结束 <1帧。',
				'本次从全文 80 个原始片段中抽查 8 个，覆盖约 10%；样例随机分布在全文不同位置。',
				'后续全文复核、时间对齐、断句和字幕轨均未执行。'
			]
		});
		expect(task.steps?.[0].result?.metrics[1]).toEqual({ label: '抽查比例', value: '10%' });
		expect(task.steps?.[0].result?.sections).toEqual([
			expect.objectContaining({
				title: '全文抽查样例',
				openByDefault: true,
				items: expect.arrayContaining([
					expect.objectContaining({ title: 'asr_0001' }),
					expect.objectContaining({ title: 'asr_0003' })
				])
			}),
			expect.objectContaining({
				title: '展开详情',
				openByDefault: false,
				items: expect.arrayContaining([
					expect.objectContaining({ title: 'asr_0004' }),
					expect.objectContaining({ title: 'asr_0008' })
				])
			})
		]);
		expect(task.steps?.[0].result?.sections[0].items).toHaveLength(3);
		expect(task.steps?.[0].result?.sections[1].items).toHaveLength(5);
	});

	it('uses the managed raw ASR workflow definition instead of formal ASR fallback steps', () => {
		const task = operationActivityTask({
			operation_id: 'asr-raw-managed',
			project_id: 'project',
			kind: 'english_asr',
			status: 'running',
			label: '原始听写（开发单步）',
			progress: 0.15,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'asr',
				engine_id: 'qwen3-asr-mlx',
				source_track_id: 'original',
				source_language: 'auto'
			},
			result_summary: {
				stage: '正在生成原始听写',
				stage_id: 'asr',
				execution_scope: 'partial',
				workflow_schema_version: 'asr-raw-development-workflow-v1',
				workflow_id: 'asr-raw-development',
				task_stage_groups: [{
					id: 'raw_asr',
					label: '生成原始听写',
					description: '只生成未经校对的原始听写。',
					atomic_tasks: [{
						id: 'asr',
						label: '生成并校验原始听写',
						description: '生成并保存无路径的版本化结果。',
						execution: 'serial',
						depends_on: []
					}]
				}],
				task_step_results: {
					asr: {
						label: '生成并校验原始听写',
						order: 10,
						status: 'running',
						summary: '正在生成并校验原始听写。',
						metrics: [],
						sections: [],
						notes: []
					}
				}
			},
			created_at: '2026-07-31T18:19:25Z',
			started_at: '2026-07-31T18:19:26Z',
			completed_at: null
		});

		expect(task.steps?.map(({ id, status }) => [
			id,
			status
		])).toEqual([['asr', 'running']]);
		expect(task.stages).toEqual([
			expect.objectContaining({
				id: 'raw_asr',
				status: 'running',
				steps: [
					expect.objectContaining({
						id: 'asr',
						status: 'running'
					})
				]
			})
		]);
		expect(
			task.stages?.some(
				(stage) => stage.id === 'other_steps'
			)
		).toBe(false);
	});

	it('keeps the managed raw ASR step when a compact feed omits full detail', () => {
		const task = operationActivityTask({
			operation_id: 'asr-raw-managed-summary',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: '原始听写（开发单步）',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'asr',
				engine_id: 'qwen3-asr-mlx',
				source_track_id: 'original'
			},
			result_summary: {
				stage: '原始听写已完成（开发断点）',
				stage_id: 'asr',
				execution_scope: 'partial',
				workflow_schema_version: 'asr-raw-development-workflow-v1',
				workflow_id: 'asr-raw-development',
				segment_count: 0,
				task_duration_ms: 1277
			},
			created_at: '2026-07-31T18:17:16Z',
			started_at: '2026-07-31T18:17:16Z',
			completed_at: '2026-07-31T18:17:17Z',
			detail_available: true
		});

		expect(task.steps?.map(({ id, label, status }) => [
			id,
			label,
			status
		])).toEqual([[
			'asr',
			'生成并校验原始听写',
			'success'
		]]);
		expect(task.stages).toBeUndefined();
	});

	it('shows parallel initial analysis as one task with both branches completed', () => {
		const task = operationActivityTask({
			operation_id: 'initial-analysis-partial',
			project_id: 'project',
			kind: 'english_asr',
			status: 'success',
			label: '听写字幕',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'initial_analysis',
				source_track_id: 'vocals'
			},
			result_summary: {
				stage: '原始听写与说话人区分已完成（并行开发断点）',
				stage_id: 'initial_analysis',
				execution_scope: 'partial',
				parallel: true,
				workflow_schema_version: 'asr-initial-analysis-development-workflow-v1',
				workflow_id: 'asr-initial-analysis-development',
				task_duration_ms: 43_538,
				raw_asr_segment_count: 80,
				diarization_segment_count: 109,
				speaker_count: 2,
				diarization_status: 'completed',
				branch_duration_ms: {
					raw_asr: 30_288,
					diarization: 43_489,
					wall: 43_538
				},
				quality_summary: {
					raw_asr: {
						status: 'passed',
						has_text: true,
						has_segments: true,
						timestamps_monotonic: true,
						incomplete_range_count: 0,
						trailing_gap_ms: 16,
						warning_codes: []
					},
					diarization: {
						status: 'passed',
						segment_count: 109,
						cluster_count: 2,
						overlap_segment_count: 0,
						coverage_ratio: 0.9974,
						warning_codes: []
					},
					warnings: []
				},
				sample_schema_version: 'initial-analysis-sample-v1',
				sample: {
					raw_asr: {
						language: 'en',
						segment_count: 4,
						sample_count: 4,
						sample_ratio: 1,
						quality_summary: {
							status: 'passed',
							has_text: true,
							has_segments: true,
							timestamps_monotonic: true,
							incomplete_range_count: 0,
							trailing_gap_ms: 16,
							warning_codes: []
						},
						segments: Array.from({ length: 4 }, (_, index) => ({
							segment_id: `asr_${String(index + 1).padStart(4, '0')}`,
							start_ms: index * 1000,
							end_ms: (index + 1) * 1000,
							text: `Segment ${index + 1}.`
						}))
					},
					diarization: {
						clusters: [
							{
								cluster_id: 'cluster_01',
								start_ms: 0,
								end_ms: 200_000,
								duration_ms: 190_000,
								segment_count: 55,
								merge_status: 'original'
							},
							{
								cluster_id: 'cluster_02',
								start_ms: 20_000,
								end_ms: 385_000,
								duration_ms: 180_000,
								segment_count: 54,
								merge_status: 'original'
							}
						],
						segments: []
					}
				}
			},
			created_at: '2026-07-24T17:00:57',
			started_at: '2026-07-24T17:00:57',
			completed_at: '2026-07-24T17:01:41'
		});

		expect(task.label).toBe('原始听写 + 说话人区分（并行开发）');
		expect(activityTaskDisplayName(task)).toBe('原始听写 + 说话人区分（并行开发）');
		expect(task.executionScope).toBe('partial');
		expect(task.finalResult).toBeUndefined();
		expect(task.steps?.map(({ id, status }) => [id, status])).toEqual([
				['asr', 'success'],
				['diarization', 'success'],
				['initial_analysis_join', 'success']
		]);
		expect(task.steps?.[0].result).toMatchObject({
			status: 'success',
			summary: '已生成 4 个原始语音片段，本次从全文抽查 4 个样例，默认展示其中 3 个。',
			metrics: expect.arrayContaining([
				{ label: '原始片段', value: '4' },
				{ label: '分支耗时', value: '30 秒' }
			]),
			sections: expect.arrayContaining([
				expect.objectContaining({ title: '全文抽查样例' }),
				expect.objectContaining({ title: '展开详情' })
			])
		});
		expect(task.steps?.[1].result).toMatchObject({
			status: 'success',
			summary: '已区分 2 位匿名说话人，共 109 个讲话片段。',
			metrics: expect.arrayContaining([
				{ label: '说话人数', value: '2' },
				{ label: '讲话片段', value: '109' },
				{ label: '音频覆盖', value: '100%' },
				{ label: '分支耗时', value: '43 秒' }
			]),
			sections: [
				expect.objectContaining({
					title: '匿名说话人分组',
					items: expect.arrayContaining([
						expect.objectContaining({ title: 'cluster_01' }),
						expect.objectContaining({ title: 'cluster_02' })
					])
				})
			]
		});
	});

	it('shows standalone speaker diarization as an audio-only development step', () => {
		const task = operationActivityTask({
			operation_id: 'diarization-partial',
			project_id: 'project',
			kind: 'speaker_diarization',
			status: 'success',
			label: '区分说话人',
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			parameters: {
				source_track_id: 'vocals',
				max_speakers: 2,
				scope: { area: 'development', exclusive: true, tracks: [] }
			},
			result_summary: {
				stage: '说话人区分已完成（开发单步）',
				stage_id: 'diarization',
				execution_scope: 'partial',
				speaker_count: 2,
				count_guidance: {
					requested_max_speakers: 2,
					detected_speaker_count: 2,
					engine_applied: false,
					usage: 'quality_check_only',
					evaluation: 'within_range'
				},
				quality_summary: {
					status: 'passed',
					segment_count: 12,
					overlap_segment_count: 0,
					coverage_ratio: 0.84
				},
				sample: {
					clusters: [
						{
							cluster_id: 'cluster_01',
							start_ms: 0,
							end_ms: 10_000,
							duration_ms: 6_000,
							segment_count: 6,
							merge_status: 'original'
						},
						{
							cluster_id: 'cluster_02',
							start_ms: 1_000,
							end_ms: 9_000,
							duration_ms: 4_000,
							segment_count: 6,
							merge_status: 'original'
						}
					]
				}
			},
			created_at: '2026-07-24T12:00:00',
			started_at: '2026-07-24T12:00:00',
			completed_at: '2026-07-24T12:00:10'
		});

		expect(task.label).toBe('说话人区分（开发单步）');
		expect(task.executionScope).toBe('partial');
		expect(task.scope?.area).toBe('development');
		expect(task.resultCount).toBe(2);
		expect(task.resultUnit).toBe('位说话人');
		expect(task.steps).toHaveLength(1);
		expect(task.steps?.[0]).toMatchObject({
			id: 'diarization',
			label: '区分说话人',
			status: 'success',
			result: {
				status: 'success',
				summary: '已区分 2 位匿名说话人。',
				notes: ['最多 2 位仅用于结果复核，当前引擎仍自动判断人数。']
			}
		});
		expect(task.steps?.[0].result?.metrics).toEqual([
			{ label: '说话人数', value: '2' },
			{ label: '讲话片段', value: '12' },
			{ label: '音频覆盖', value: '84%' },
			{ label: '重叠讲话', value: '0' }
		]);
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

	it('does not describe a terminal cancelled task as still cancelling', () => {
		const task = operationActivityTask({
			operation_id: 'localization-cancelled', project_id: 'project', kind: 'localization_draft', status: 'cancelled',
			label: null, progress: 0.3, error_code: null, error_message: null, cancel_requested: true,
			parameters: {}, result_summary: { stage: '正在取消，将在当前步骤结束后停止' },
			created_at: '', started_at: '', completed_at: ''
		});

		expect(task.cancellable).toBe(false);
		expect(task.cancelPending).toBe(false);
		expect(task.stage).toBe('已取消');
	});

	it('distinguishes cancel and retry command feedback', () => {
		const operation = {
			operation_id: 'localization-action', project_id: 'project', kind: 'localization_draft' as const,
			status: 'failed' as const, label: null, progress: 0.3, error_code: null, error_message: 'failed',
			cancel_requested: false, parameters: {}, result_summary: { stage: 'research' },
			created_at: '', started_at: '', completed_at: ''
		};

		expect(operationActivityTask(operation, 'retry')).toMatchObject({
			actionPending: 'retry',
			cancelPending: false,
			stage: '正在重新提交'
		});
		expect(operationActivityTask({ ...operation, status: 'running', completed_at: null }, 'cancel')).toMatchObject({
			actionPending: 'cancel',
			cancelPending: true,
			stage: '正在取消，将在当前步骤结束后停止'
		});
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

	it('uses the recorded step start for an exact running duration', () => {
		const task = {
			id: 'task',
			label: '生成本土化字幕',
			status: 'running' as const,
			startedAt: '2026-07-15T08:00:00Z',
			stages: []
		};
		const step = {
			id: 'review',
			label: '复核原意',
			status: 'running' as const,
			startedElapsedMs: 60_000
		};

		expect(activityTaskStepTimingLabel(
			step,
			task,
			Date.parse('2026-07-15T08:01:30Z')
		)).toBe('30 秒');
	});

	it('keeps completed durations when one earlier step has no recorded timing', () => {
		const task = {
			id: 'task',
			label: '生成 ASR 字幕',
			status: 'running' as const,
			startedAt: '2026-07-15T08:00:00Z',
			stages: [
				{
					id: 'initial',
					label: '生成原始听写',
					status: 'success' as const,
					layout: 'linear' as const,
					durationMs: 100_000,
					steps: [{
						id: 'asr',
						label: '生成原始听写',
						status: 'success' as const,
						durationMs: 100_000
					}]
				},
				{
					id: 'review',
					label: '理解与校对全文',
					status: 'success' as const,
					layout: 'linear' as const,
					steps: [
						{
							id: 'understand',
							label: '理解全文',
							status: 'success' as const,
							durationMs: 900_000
						},
						{
							id: 'quality_gate',
							label: '质量门禁',
							status: 'success' as const
						}
					]
				},
				{
					id: 'timing',
					label: '时间与字幕整理',
					status: 'running' as const,
					layout: 'linear' as const,
					steps: [{
						id: 'alignment',
						label: '对齐逐词时间',
						status: 'running' as const
					}]
				}
			]
		};

		expect(activityTaskStepTimingLabel(
			task.stages[2].steps[0],
			task,
			Date.parse('2026-07-15T08:17:10Z')
		)).toBe('30 秒');
	});

	it('formats media positions with compact Chinese frame time', () => {
		expect(formatActivityTimelinePosition(45_000, 29.97002997)).toBe('45秒');
		expect(formatActivityTimelinePosition(60_000, 29.97002997)).toBe('1分');
		expect(formatActivityTimelinePosition(65_100, 30)).toBe('1分5秒3帧');
		expect(formatActivityTimelineDuration(16, 29.97002997)).toBe('<1帧');
		expect(formatActivityTimelineRange(60_000, 62_500, 30)).toBe('1分 – 1分2秒15帧');
		expect(formatActivityTimelineText('第4帧（45000ms）可见姓名；约210091毫秒出现图表。', 30))
			.toBe('第4帧（45秒）可见姓名；约3分30秒3帧出现图表。');
		expect(formatActivityTimelineText('查看0-12620ms，并补看208000至210000毫秒。', 30))
			.toBe('查看0帧 – 12秒19帧，并补看3分28秒 – 3分30秒。');
		expect(formatActivityTimelineText('内部字段 duration_ms 保持不变。', 30))
			.toBe('内部字段 duration_ms 保持不变。');
		expect(formatActivityTimelineText('抽查 29.2s - 31.2s，另看 321.3s。', 30))
			.toBe('抽查 29秒6帧 – 31秒6帧，另看 5分21秒9帧。');
	});

	it('uses ordered dynamic ASR steps from the whole-document review flow', () => {
		const task = operationActivityTask({
			operation_id: 'asr-flow', project_id: 'project', kind: 'english_asr', status: 'running',
			label: null, progress: 0.5, error_code: null, error_message: null, cancel_requested: false,
			parameters: {}, result_summary: {
				stage: '第 1 轮分段复查',
				stage_id: 'section_review_r1',
				task_stage_timings: {
					asr: { duration_ms: 800 },
					understand_document: { duration_ms: 400 },
					section_review_r1: { duration_ms: 200, running: true }
				},
				task_step_results: {
					section_review_r1: {
						label: '定位听写疑点', order: 50, status: 'running',
						summary: '正在按各段关注点复查。', metrics: [], sections: []
					},
					asr: {
						label: '生成原始听写稿', order: 10, status: 'success',
						summary: '原始讲话已经转写。', metrics: [], sections: []
					},
					understand_document: {
						label: '理解全文并规划复查', order: 30, status: 'success',
						summary: '已形成全文理解卡。', metrics: [], sections: []
					}
				}
			},
			created_at: '', started_at: '', completed_at: null
		});

		expect(task.steps?.map(({ id, label, status, durationMs }) => ({ id, label, status, durationMs }))).toEqual([
			{ id: 'asr', label: '生成原始听写', status: 'success', durationMs: 800 },
			{ id: 'understand_document', label: '理解全文并规划复查', status: 'success', durationMs: 400 },
			{ id: 'section_review_r1', label: '定位听写疑点', status: 'running', durationMs: 200 }
		]);
		expect(task.steps?.[2].result?.summary).toBe('正在按各段关注点复查。');
	});

	it('uses the current name-check label for completed ASR history', () => {
		const task = operationActivityTask({
			operation_id: 'asr-history-label', project_id: 'project', kind: 'english_asr', status: 'success',
			label: null, progress: 1, error_code: null, error_message: null, cancel_requested: false,
			parameters: {}, result_summary: {
				task_step_results: {
					research: {
						label: '核对专名与背景', order: 40, status: 'success',
						summary: '已完成名称与背景核对。'
					}
				}
			},
			created_at: '', started_at: '', completed_at: ''
		});

		expect(task.steps?.find((step) => step.id === 'research')?.label).toBe('核对名称与背景');
	});

	it('shows semantic TTS grouping as a cancellable background task with ordered steps', () => {
		const task = operationActivityTask({
			operation_id: 'semantic-groups', project_id: 'project', kind: 'semantic_tts_grouping', status: 'running',
			label: null, progress: 0.6, error_code: null, error_message: null, cancel_requested: false,
			parameters: { scope: { area: 'subtitle', exclusive: false, tracks: [{ id: 'localizedSubtitles', role: 'input' }] } },
			result_summary: {
				stage: '判断语义和场景',
				stage_id: 'group',
				task_step_results: {
					prepare: { label: '整理字幕和说话人', order: 10, status: 'success', summary: '已整理。' },
					group: { label: '判断语义和场景', order: 20, status: 'running', summary: '分组中。' }
				}
			},
			created_at: '', started_at: '', completed_at: null
		});

		expect(task.label).toBe('按语义组合配音字幕');
		expect(task.cancellable).toBe(true);
		expect(task.scope?.trackIds).toEqual([]);
		expect(task.steps?.map((step) => [step.id, step.status])).toEqual([
			['prepare', 'success'],
			['group', 'running']
		]);
	});

	it('maps media export progress, delivery scope, steps, and result facts into the shared task panel', () => {
		const task = operationActivityTask({
			operation_id: 'media-export', project_id: 'project', kind: 'media_export', status: 'running',
			label: null, progress: 0.42, error_code: null, error_message: null, cancel_requested: false,
			parameters: { scope: { area: 'delivery', exclusive: false, tracks: [] } },
			result_summary: {
				stage: '正在渲染视频',
				stage_id: 'render',
				task_step_results: {
					prepare: { label: '检查导出内容', order: 10, status: 'success', summary: '已检查。' },
					render: { label: '渲染成品', order: 20, status: 'running', summary: '渲染中。' },
					validate: { label: '核对并保存', order: 30, status: 'todo', summary: '等待渲染。' }
				}
			},
			created_at: '', started_at: '', completed_at: null
		});

		expect(task.label).toBe('导出成品');
		expect(task.progress).toBe(0.42);
		expect(task.cancellable).toBe(true);
		expect(task.scope).toEqual({
			trackIds: [],
			itemIds: [],
			area: 'delivery',
			exclusive: false
		});
		expect(task.steps?.map((step) => [step.id, step.status])).toEqual([
			['prepare', 'success'],
			['render', 'running'],
			['validate', 'todo']
		]);

		const completed = operationActivityTask({
			...({
				operation_id: 'media-export', project_id: 'project', kind: 'media_export', status: 'success',
				label: null, progress: 1, error_code: null, error_message: null, cancel_requested: false,
				parameters: {},
				result_summary: {
					stage: '成品已保存',
					filename: 'demo.mp4',
					size_bytes: 1_048_576,
					export_kind: 'video',
					mixed_track_count: 2,
					task_step_results: {
						prepare: { label: '检查导出内容', order: 10, status: 'success', summary: '已检查。' },
						render: { label: '渲染成品', order: 20, status: 'success', summary: '已渲染。' },
						validate: { label: '核对并保存', order: 30, status: 'success', summary: '已保存。' }
					},
					task_final_result: {
						status: 'success',
						summary: '成品已写入用户选择的目录。',
						metrics: [{ label: '文件', value: 'demo.mp4' }],
						sections: [],
						notes: []
					}
				},
				created_at: '', started_at: '', completed_at: ''
			} satisfies VideoLocalizationOperation)
		});
		expect(completed.scope?.area).toBe('delivery');
		expect(completed.resultCount).toBe(1);
		expect(completed.resultUnit).toBe('个成品');
		expect(completed.summaryFacts).toEqual([
			{ label: '成品文件', value: 'demo.mp4' },
			{ label: '文件大小', value: '1 MB' },
			{ label: '导出类型', value: '视频' },
			{ label: '音频片段', value: '2 个' }
		]);
		expect(completed.finalResult?.summary).toBe('成品已写入用户选择的目录。');
		expect(completed.steps?.every((step) => step.status === 'success')).toBe(true);
	});

	it('describes standalone subtitle export without an audio metric', () => {
		const task = operationActivityTask({
			...({
				operation_id: 'subtitle-export',
				project_id: 'project',
				kind: 'media_export',
				status: 'success',
				label: null,
				progress: 1,
				error_code: null,
				error_message: null,
				cancel_requested: false,
				parameters: {},
				result_summary: {
					filename: '最终字幕.srt',
					size_bytes: 2048,
					export_kind: 'subtitle',
					mixed_track_count: 0
				},
				created_at: '',
				started_at: '',
				completed_at: ''
			} satisfies VideoLocalizationOperation)
		});

		expect(task.summaryFacts).toEqual([
			{ label: '成品文件', value: '最终字幕.srt' },
			{ label: '文件大小', value: '2 KB' },
			{ label: '导出类型', value: '字幕' }
		]);
	});

	it('covers duration formatting boundaries and invalid values', () => {
		expect(formatActivityTaskDuration(0)).toBe('0 秒');
		expect(formatActivityTaskDuration(49)).toBe('<1 秒');
		expect(formatActivityTaskDuration(59_999)).toBe('59 秒');
		expect(formatActivityTaskDuration(60_000)).toBe('1 分');
		expect(formatActivityTaskDuration(3_600_000)).toBe('1 小时');
		expect(formatActivityTaskDuration(null)).toBe('');
		expect(formatActivityTaskDuration(Number.NaN)).toBe('');
	});
});
