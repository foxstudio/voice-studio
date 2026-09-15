import { render } from 'svelte/server';
import { describe, expect, it, vi } from 'vitest';
import type { ActivityTask } from './activity-notice';
import TaskWorkflowStages from './TaskWorkflowStages.svelte';

describe('task workflow stage states', () => {
	it('labels a successful no-op as unnecessary instead of completed', () => {
		const task: ActivityTask = {
			id: 'task-1',
			label: '按需补充证据',
			status: 'success',
			steps: [{
				id: 'collect_localization_research_evidence_v3',
				label: '查询必要资料',
				status: 'success',
				result: {
					status: 'not_needed',
					summary: '全文没有需要联网确认的问题，本步骤无需执行。',
					metrics: [],
					sections: [],
					notes: ['未调用搜索服务。']
				}
			}]
		};

		const { body } = render(TaskWorkflowStages, {
			props: {
				task,
				nowMs: Date.now(),
				onShowResult: vi.fn()
			}
		});

		expect(body).toContain('查询必要资料');
		expect(body).toContain('无需执行');
		expect(body).not.toContain('已完成');
	});

	it('labels an all-no-op evidence stage as unnecessary', () => {
		const notNeededResult = {
			status: 'not_needed' as const,
			summary: '本步骤无需执行。',
			metrics: [],
			sections: [],
			notes: []
		};
		const task: ActivityTask = {
			id: 'task-2',
			label: '确认资料与画面结论（开发单步）',
			status: 'success',
			stages: [{
				id: 'localization_evidence',
				label: '按需补充证据',
				status: 'success',
				steps: [
					{ id: 'research', label: '查询必要资料', status: 'success', result: notNeededResult },
					{ id: 'visual', label: '查看必要画面', status: 'success', result: notNeededResult },
					{ id: 'join', label: '确认资料与画面结论', status: 'success', result: notNeededResult }
				]
			}]
		};

		const { body } = render(TaskWorkflowStages, {
			props: {
				task,
				nowMs: Date.now(),
				onShowResult: vi.fn()
			}
		});

		expect(body).toContain('按需补充证据');
		expect(body.match(/无需执行/g)).toHaveLength(4);
		expect(body).not.toContain('已完成');
	});

	it('keeps serial work full-width around one parallel review fork', () => {
		const task: ActivityTask = {
			id: 'task-3',
			label: '本土化字幕',
			status: 'success',
			stages: [{
				id: 'localization_creation',
				label: '创作并验收中文台词',
				status: 'success',
				layout: 'parallel-join',
				steps: [
					{
						id: 'generate_localization_spoken_script',
						label: '生成全文本土化初稿',
						status: 'success',
						execution: 'serial',
						dependsOn: ['lock_localization_creation_context']
					},
					{
						id: 'review_localization_fidelity',
						label: '复核原意与事实',
						status: 'success',
						execution: 'parallel',
						dependsOn: ['generate_localization_spoken_script']
					},
					{
						id: 'review_localization_naturalness',
						label: '盲测中文自然度',
						status: 'success',
						execution: 'parallel',
						dependsOn: ['generate_localization_spoken_script']
					},
					{
						id: 'finalize_localization_spoken_script',
						label: '本土化台词终审',
						status: 'success',
						execution: 'join',
						dependsOn: [
							'review_localization_fidelity',
							'review_localization_naturalness'
						]
					}
				]
			}]
		};

		const { body } = render(TaskWorkflowStages, {
			props: {
				task,
				nowMs: Date.now(),
				onShowResult: vi.fn()
			}
		});

		expect(body.match(/\bfull-row\b/g)).toHaveLength(2);
		expect(body.match(/\bparallel-step\b/g)).toHaveLength(2);
		expect(body.match(/\bjoin-step\b/g)).toHaveLength(1);
	});
});
