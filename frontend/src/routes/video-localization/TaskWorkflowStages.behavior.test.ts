import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import TaskWorkflowStages from './TaskWorkflowStages.svelte';
import type { ActivityTask } from './activity-notice';

function workflowTask(): ActivityTask {
	return {
		id: 'operation-1',
		label: '生成 ASR 字幕',
		stage: '正在复查',
		progress: 0.7,
		status: 'running',
		createdAt: '2026-07-30T00:00:00Z',
		stages: [
			{
				id: 'review',
				label: '理解与校对全文',
				description: '先并行收集信息，再汇合检查。',
				status: 'running',
				durationMs: 2500,
				layout: 'parallel-join',
				steps: [
					{
						id: 'understand_document',
						label: '理解全文',
						description: '不应直接显示的子任务说明',
						status: 'success',
						execution: 'parallel',
						durationMs: 1200
					},
					{
						id: 'research',
						label: '查询资料',
						status: 'success',
						execution: 'parallel',
						durationMs: 900,
						result: {
							status: 'warning',
							summary: '有一项资料仍需确认',
							metrics: [],
							sections: [],
							notes: []
						}
					},
					{
						id: 'transcript_quality_gate',
						label: '进入校时前检查',
						status: 'success',
						execution: 'join',
						durationMs: 400,
						result: {
							status: 'warning',
							summary: '建议复听一处',
							metrics: [],
							sections: [],
							notes: [],
							attentionKind: 'advisory'
						}
					}
				]
			}
		]
	};
}

describe('TaskWorkflowStages rendered behavior', () => {
	it('keeps a running parent stage duration in sync with the live task clock', () => {
		const task: ActivityTask = {
			id: 'running-operation',
			label: '听写字幕',
			stage: '正在识别人声内容',
			progress: 0.15,
			status: 'running',
			createdAt: '2026-07-30T00:00:00Z',
			startedAt: '2026-07-30T00:00:00Z',
			stages: [
				{
					id: 'initial_analysis',
					label: '生成原始听写',
					status: 'running',
					durationMs: 1054,
					layout: 'linear',
					steps: [
						{
							id: 'asr',
							label: '生成原始听写',
							status: 'running',
							durationMs: 1054
						}
					]
				}
			]
		};

		const { body } = render(TaskWorkflowStages, {
			props: {
				task,
				nowMs: Date.parse('2026-07-30T00:02:44Z'),
				onShowResult: vi.fn()
			}
		});

		expect(body).toContain('aria-label="父级总耗时 2 分 44 秒"');
		expect(body).not.toContain('aria-label="父级总耗时 1 秒"');
	});

	it('renders parallel and join steps with reader-facing state and timing labels', () => {
		const { body } = render(TaskWorkflowStages, {
			props: {
				task: workflowTask(),
				nowMs: Date.parse('2026-07-30T00:00:04Z'),
				onShowResult: vi.fn()
			}
		});

		expect(body).toContain('共 3 项');
		expect(body).toMatch(/class="stage-steps [^"]*parallel-layout"/);
		expect(body.match(/class="[^"]*parallel-step/g)).toHaveLength(2);
		expect(body).toMatch(/class="[^"]*join-step step-warning"/);
		expect(body).toContain('aria-label="查看“理解全文”的结果"');
		expect(body).toContain('aria-haspopup="dialog"');
		expect(body).toContain('aria-label="父级总耗时 2 秒"');
		expect(body).toContain('aria-label="子任务耗时 1 秒"');
		expect(body).toContain('已完成，有建议');
		expect(body).not.toContain('不应直接显示的子任务说明');
		expect(body).not.toContain('disabled');
	});

	it('renders flat single-step tasks through the same result buttons', () => {
		const task: ActivityTask = {
			id: 'single-step-operation',
			label: '单步任务',
			stage: '已完成',
			progress: 1,
			status: 'success',
			steps: [
				{
					id: 'single-step',
					label: '处理步骤',
					status: 'success',
					durationMs: 800
				}
			]
		};

		const { body } = render(TaskWorkflowStages, {
			props: {
				task,
				nowMs: 0,
				onShowResult: vi.fn()
			}
		});

		expect(body).toContain('aria-label="单步任务处理步骤"');
		expect(body).toContain('aria-label="查看“处理步骤”的结果"');
		expect(body).toContain('已完成');
		expect(body).toContain('&lt;1 秒');
	});
});
