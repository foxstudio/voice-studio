import { describe, expect, it } from 'vitest';
import { ApiError } from '$lib/api/client';
import type { ActivityTask } from './activity-notice';
import {
	DEFAULT_VISIBLE_HISTORY_TASKS,
	defaultHistoryTaskLimit,
	finalActivityTaskResult,
	historyPaginationAction,
	historyTaskShouldLoadDetail,
	hiddenHistoryTaskCount,
	newlyCompletedTaskIds,
	operationDetailCacheKey,
	operationDetailCacheIsFresh,
	operationDetailLoadErrorMessage,
	operationDetailRequestIsCurrent,
	resolvedHistoryTaskCount,
	synchronizeTerminalTaskDetail,
	taskCanDelete,
	taskDetailFreshnessSignature,
	sortActivityTasksByRecency,
	taskFailureSummary,
	taskStageLabel
} from './TaskProgressPanel.svelte';

describe('task progress history visibility', () => {
	it('shows the actual failure reason instead of the task input text', () => {
		const task: ActivityTask = {
			id: 'tts-failed',
			label: '生成合成配音',
			stage: '对齐字幕并放入轨道失败',
			detail: '这是很长的一整段配音台词，不应该显示成错误。',
			progress: 1,
			status: 'failed',
			createdAt: '2026-08-02T17:19:54',
			completedAt: '2026-08-02T17:20:13',
			steps: [{
				id: 'placement',
				label: '对齐字幕并放入轨道',
				status: 'failed'
			}],
			failureResult: {
				status: 'failed',
				summary: '声音已经生成，但台词覆盖校对未通过，所以没有放入配音轨。',
				metrics: [],
				sections: [],
				notes: []
			}
		};

		expect(taskStageLabel(task)).toBe('失败在：对齐字幕并放入轨道');
		expect(taskFailureSummary(task)).toBe(
			'声音已经生成，但台词覆盖校对未通过，所以没有放入配音轨。'
		);
		expect(taskFailureSummary(task)).not.toContain('这是很长的一整段配音台词');
	});

	it('scopes operation detail cache entries by project and operation', () => {
		expect(operationDetailCacheKey('project-a', 'operation-shared'))
			.not.toBe(operationDetailCacheKey('project-b', 'operation-shared'));
		expect(operationDetailCacheKey('project:a', 'operation:shared'))
			.not.toBe(operationDetailCacheKey('project', 'a:operation:shared'));
	});

	it('surfaces repair-required detail failures instead of silently falling back', () => {
		expect(operationDetailLoadErrorMessage(new ApiError(
			'任务详情需要修复',
			409,
			'VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED'
		))).toBe('任务详情需要修复');
		expect(operationDetailLoadErrorMessage(new Error('network')))
			.toBe('完整任务详情加载失败，请稍后重试。');
	});

	it('rejects detail responses from an older project epoch', () => {
		expect(operationDetailRequestIsCurrent(
			{ projectId: 'project-a', epoch: 3 },
			{ projectId: 'project-a', epoch: 3 }
		)).toBe(true);
		expect(operationDetailRequestIsCurrent(
			{ projectId: 'project-a', epoch: 3 },
			{ projectId: 'project-b', epoch: 4 }
		)).toBe(false);
		expect(operationDetailRequestIsCurrent(
			{ projectId: 'project-a', epoch: 3 },
			{ projectId: 'project-a', epoch: 5 }
		)).toBe(false);
	});

	it('refreshes cached detail once a child step completes without polling every running update', () => {
		const runningTask: ActivityTask = {
			id: 'operation-1',
			operationId: 'operation-1',
			label: '生成 ASR 字幕',
			status: 'running',
			stages: [{
				id: 'review',
				label: '理解与校对全文',
				status: 'running',
				steps: [{
					id: 'research',
					label: '查询资料',
					status: 'running',
					result: {
						status: 'running',
						summary: '已完成 1 / 3 批',
						metrics: [],
						sections: [],
						notes: []
					}
				}]
			}]
		};
		const nextRunningUpdate: ActivityTask = {
			...runningTask,
			stages: [{
				...runningTask.stages![0],
				steps: [{
					...runningTask.stages![0].steps[0],
					result: {
						status: 'running',
						summary: '已完成 2 / 3 批',
						metrics: [],
						sections: [],
						notes: []
					}
				}]
			}]
		};
		const completedTask: ActivityTask = {
			...nextRunningUpdate,
			stage: '正在汇总',
			stages: [{
				...nextRunningUpdate.stages![0],
				steps: [{
					...nextRunningUpdate.stages![0].steps[0],
					status: 'success',
					result: {
						status: 'success',
						summary: '资料查询完成',
						metrics: [{ label: '来源', value: '3 条' }],
						sections: [],
						notes: []
					}
				}]
			}]
		};
		const runningSignature = taskDetailFreshnessSignature(runningTask);

		expect(taskDetailFreshnessSignature(nextRunningUpdate)).toBe(runningSignature);
		expect(taskDetailFreshnessSignature(completedTask)).not.toBe(runningSignature);
		expect(operationDetailCacheIsFresh(
			{ task: runningTask, sourceSignature: runningSignature },
			completedTask
		)).toBe(false);
		expect(operationDetailCacheIsFresh(
			{
				task: completedTask,
				sourceSignature: taskDetailFreshnessSignature(completedTask)
			},
			completedTask
		)).toBe(true);
	});

	it('loads terminal detail only on the first expansion of a polling summary', () => {
		const summaryTask = {
			operationId: 'operation-1',
			detailAvailable: true
		};
		expect(historyTaskShouldLoadDetail(summaryTask, false, false)).toBe(true);
		expect(historyTaskShouldLoadDetail(summaryTask, true, false)).toBe(false);
		expect(historyTaskShouldLoadDetail(summaryTask, false, true)).toBe(false);
		expect(historyTaskShouldLoadDetail(
			{ operationId: 'operation-1' },
			false,
			false
		)).toBe(false);
	});

	it('shows ten history tasks by default in the full task panel', () => {
		expect(DEFAULT_VISIBLE_HISTORY_TASKS).toBe(10);
		expect(defaultHistoryTaskLimit(true, 0)).toBe(10);
		expect(defaultHistoryTaskLimit(true, 3)).toBe(10);
	});

	it('keeps the compact panel within the shared default task budget', () => {
		expect(defaultHistoryTaskLimit(false, 3)).toBe(7);
		expect(defaultHistoryTaskLimit(false, 10)).toBe(0);
	});

	it('reports exactly how many history tasks remain hidden', () => {
		expect(hiddenHistoryTaskCount(45, 10)).toBe(35);
		expect(hiddenHistoryTaskCount(8, 10)).toBe(0);
	});

	it('adds only server-side operation records that are not loaded yet', () => {
		expect(resolvedHistoryTaskCount(13, 10, 50)).toBe(53);
		expect(resolvedHistoryTaskCount(13, 10, 8)).toBe(13);
	});

	it('expands loaded records before paging and collapses after the last page', () => {
		expect(historyPaginationAction(false, 40, true)).toBe('expand');
		expect(historyPaginationAction(true, 0, true)).toBe('load');
		expect(historyPaginationAction(true, 0, false)).toBe('collapse');
		expect(historyPaginationAction(false, 0, false)).toBeNull();
	});

	it('keeps a task expanded when it moves from running into history', () => {
		expect(newlyCompletedTaskIds(
			['localization', 'asr'],
			[
				{ id: 'localization', status: 'success' },
				{ id: 'asr', status: 'running' },
				{ id: 'older', status: 'success' }
			]
		)).toEqual(['localization']);
	});

	it('closes stale cached child states as soon as the parent fails', () => {
		const summary = {
			id: 'localization',
			label: '生成本土化字幕',
			stage: '终审失败',
			progress: 0.5,
			status: 'failed',
			completedAt: '2026-08-01T20:00:00',
			failureResult: { status: 'failed', summary: '终审未通过。', sections: [] }
		} as const;
		const detail = {
			...summary,
			status: 'failed',
			completedAt: undefined,
			stages: [{
				id: 'creation',
				label: '创作',
				description: '',
				order: 1,
				steps: [
					{ id: 'current', label: '终审', order: 1, status: 'running' },
					{ id: 'next', label: '时间映射', order: 2, status: 'todo' }
				]
			}]
		} as never;

		const synchronized = synchronizeTerminalTaskDetail(
			summary as never,
			detail
		);

		expect(synchronized.status).toBe('failed');
		expect(synchronized.stages?.[0].steps.map((step) => step.status))
			.toEqual(['failed', 'cancelled']);
		expect(synchronized.completedAt).toBe('2026-08-01T20:00:00');
	});

	it('closes every unfinished child when a cancelled parent is already cached as cancelled', () => {
		const summary = {
			id: 'localization',
			label: '生成本土化字幕',
			stage: '已取消',
			progress: 0.5,
			status: 'cancelled',
			completedAt: '2026-08-01T20:01:00'
		} as const;
		const detail = {
			...summary,
			stages: [{
				id: 'creation',
				label: '创作',
				description: '',
				order: 1,
				steps: [
					{ id: 'done', label: '准备', order: 1, status: 'success' },
					{ id: 'current', label: '生成', order: 2, status: 'running' },
					{ id: 'next', label: '时间映射', order: 3, status: 'todo' }
				]
			}]
		} as never;

		const synchronized = synchronizeTerminalTaskDetail(
			summary as never,
			detail
		);

		expect(synchronized.stages?.[0].steps.map((step) => step.status))
			.toEqual(['success', 'cancelled', 'cancelled']);
	});

	it('shows the newest operation first even when task sources were merged in groups', () => {
		const sorted = sortActivityTasksByRecency([
			{ id: 'tts-old', label: '配音', stage: '完成', progress: 1, status: 'success', createdAt: '2026-07-20T12:32:06', completedAt: '2026-07-20T18:00:00' },
			{ id: 'asr-new', label: 'ASR', stage: '失败', progress: 1, status: 'failed', createdAt: '2026-07-20T11:00:00', completedAt: '2026-07-20T18:13:30' },
			{ id: 'import-oldest', label: '导入', stage: '完成', progress: 1, status: 'success', createdAt: '2026-07-19T00:12:09' }
		]);

		expect(sorted.map((task) => task.id)).toEqual(['asr-new', 'tts-old', 'import-oldest']);
	});

	it('keeps source order stable when task timestamps are absent or equal', () => {
		const sorted = sortActivityTasksByRecency([
			{ id: 'one', label: '一', stage: '等待', progress: null, status: 'queued' },
			{ id: 'two', label: '二', stage: '等待', progress: null, status: 'queued' }
		]);

		expect(sorted.map((task) => task.id)).toEqual(['one', 'two']);
	});

	it('prefers a task-level final result and falls back to the last step for old tasks', () => {
		const oldStepResult = { status: 'success' as const, summary: '字幕轨已写入', metrics: [], sections: [], notes: [] };
		const taskFinalResult = { status: 'warning' as const, summary: '任务完成，建议试听两条字幕', metrics: [], sections: [], notes: [] };
		const baseTask = {
			id: 'localization',
			label: '生成本土化字幕',
			status: 'success' as const,
			steps: [
				{ id: 'localize', label: '整体生成中文', status: 'success' as const },
				{ id: 'write_track', label: '写入字幕轨', status: 'success' as const, result: oldStepResult }
			]
		};

		expect(finalActivityTaskResult({ ...baseTask, finalResult: taskFinalResult })).toEqual({
			result: taskFinalResult
		});
		expect(finalActivityTaskResult(baseTask)).toEqual({
			result: oldStepResult,
			step: baseTask.steps[1]
		});
	});

	it('does not expose a final-result dialog for simple media preparation tasks', () => {
		const finalResult = { status: 'success' as const, summary: '音频已提取', metrics: [], sections: [], notes: [] };
		expect(finalActivityTaskResult({
			id: 'source-audio', kind: 'source_audio', label: '提取音轨', status: 'success', finalResult
		})).toBeUndefined();
		expect(finalActivityTaskResult({
			id: 'stems', kind: 'stems', label: '分离音轨', status: 'success', finalResult
		})).toBeUndefined();
		expect(finalActivityTaskResult({
			id: 'asr-partial',
			kind: 'english_asr',
			label: '原始听写（开发单步）',
			status: 'success',
			executionScope: 'partial',
			steps: [{ id: 'recognize', label: '生成原始听写稿', status: 'success', result: finalResult }]
		})).toBeUndefined();
	});

	it('exposes delete only for tasks that own a removable workflow record', () => {
		expect(taskCanDelete({ status: 'running', deletable: true })).toBe(true);
		expect(taskCanDelete({ status: 'queued', deletable: true })).toBe(true);
		expect(taskCanDelete({ status: 'failed', deletable: true })).toBe(true);
		expect(taskCanDelete({ status: 'cancelled', deletable: true })).toBe(true);
		expect(taskCanDelete({ status: 'running' })).toBe(false);
		expect(taskCanDelete({ status: 'success', deletable: true })).toBe(true);
	});
});
