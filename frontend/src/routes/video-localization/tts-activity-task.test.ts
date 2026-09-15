import { describe, expect, it } from 'vitest';
import type { ActivityTask } from './activity-notice';
import { upsertTtsGenerationActivity } from './tts-activity-task';

describe('TTS activity task handoff', () => {
	it('creates one short-lived generation card', () => {
		const updated = upsertTtsGenerationActivity([], {
			taskId: 'generation-1',
			status: 'queued',
			stage: '等待生成声音',
			progress: 0
		});

		expect(updated).toEqual([
			expect.objectContaining({
				id: 'tts:generation-1',
				label: '生成合成配音',
				stage: '等待生成声音'
			})
		]);
	});

	it('updates one generation card without touching another queued voice request', () => {
		const first: ActivityTask = {
			id: 'tts:generation-1',
			label: '生成合成配音 · 第一条',
			status: 'queued'
		};
		const second: ActivityTask = {
			id: 'operation:other',
			label: '其他任务',
			status: 'queued'
		};

		const updated = upsertTtsGenerationActivity([first, second], {
			taskId: 'generation-1',
			status: 'running',
			stage: '正在生成声音',
			progress: 0.4
		});

		expect(updated).toHaveLength(2);
		expect(updated).toEqual(expect.arrayContaining([
			second,
			expect.objectContaining({
				id: 'tts:generation-1',
				status: 'running',
				progress: 0.4
			})
		]));
	});
});
