import { describe, it, expect } from 'vitest';
import {
	statusIsActive,
	taskIsActive,
	taskIsWaiting,
	taskIsProcessing,
	taskIsSuccess,
	taskIsFailed,
	taskCanDelete,
	taskIsLongformSegment,
	taskIsLongformExport,
	longformResultLabel,
	displayTitle,
	requestFromTask,
	formatSeconds,
	formatAudioDuration,
} from './helpers';
import type { GenerationTask } from '$lib/api/types';

function makeTask(overrides: Partial<GenerationTask> = {}): GenerationTask {
	return {
		task_id: 't1',
		input_text: '测试文本',
		status: 'success',
		engine_id: 'indextts-v2',
		voice_id: '',
		created_at: '2026-01-01T00:00:00Z',
		parameters: {},
		...overrides,
	} as GenerationTask;
}

describe('statusIsActive', () => {
	it('returns true for active statuses', () => {
		expect(statusIsActive('pending')).toBe(true);
		expect(statusIsActive('queued')).toBe(true);
		expect(statusIsActive('running')).toBe(true);
		expect(statusIsActive('postprocessing')).toBe(true);
		expect(statusIsActive('retrying')).toBe(true);
	});

	it('returns false for inactive statuses', () => {
		expect(statusIsActive('success')).toBe(false);
		expect(statusIsActive('failed')).toBe(false);
		expect(statusIsActive('cancelled')).toBe(false);
	});
});

describe('task helpers', () => {
	it('taskIsActive', () => {
		expect(taskIsActive(makeTask({ status: 'running' }))).toBe(true);
		expect(taskIsActive(makeTask({ status: 'success' }))).toBe(false);
	});

	it('taskIsWaiting', () => {
		expect(taskIsWaiting(makeTask({ status: 'pending' }))).toBe(true);
		expect(taskIsWaiting(makeTask({ status: 'queued' }))).toBe(true);
		expect(taskIsWaiting(makeTask({ status: 'running' }))).toBe(false);
	});

	it('taskIsProcessing', () => {
		expect(taskIsProcessing(makeTask({ status: 'running' }))).toBe(true);
		expect(taskIsProcessing(makeTask({ status: 'postprocessing' }))).toBe(true);
		expect(taskIsProcessing(makeTask({ status: 'queued' }))).toBe(false);
	});

	it('taskIsSuccess', () => {
		expect(taskIsSuccess(makeTask({ status: 'success' }))).toBe(true);
		expect(taskIsSuccess(makeTask({ status: 'failed' }))).toBe(false);
	});

	it('taskIsFailed', () => {
		expect(taskIsFailed(makeTask({ status: 'failed' }))).toBe(true);
		expect(taskIsFailed(makeTask({ status: 'cancelled' }))).toBe(true);
		expect(taskIsFailed(makeTask({ status: 'success' }))).toBe(false);
	});

	it('taskCanDelete', () => {
		expect(taskCanDelete(makeTask({ status: 'success' }))).toBe(true);
		expect(taskCanDelete(makeTask({ status: 'running' }))).toBe(false);
	});
});

describe('longform helpers', () => {
	it('taskIsLongformSegment', () => {
		expect(taskIsLongformSegment(makeTask({ longform_task_id: 'lf1', longform_segment_index: 1, longform_segment_count: 3 }))).toBe(true);
		expect(taskIsLongformSegment(makeTask())).toBe(false);
	});

	it('taskIsLongformExport', () => {
		expect(taskIsLongformExport(makeTask({ longform_task_id: 'lf1', task_type: 'export' }))).toBe(true);
		expect(taskIsLongformExport(makeTask({ longform_task_id: 'lf1' }))).toBe(false);
	});

	it('longformResultLabel', () => {
		expect(longformResultLabel(makeTask({ longform_task_id: 'lf1', task_type: 'export' }))).toBe('完整片段');
		expect(longformResultLabel(makeTask({ longform_task_id: 'lf1', longform_segment_index: 2, longform_segment_count: 5 }))).toBe('片段 2/5');
		expect(longformResultLabel(makeTask())).toBe('');
	});
});

describe('displayTitle', () => {
	it('returns input_text', () => {
		expect(displayTitle(makeTask({ input_text: '你好' }))).toBe('你好');
	});

	it('returns fallback for empty text', () => {
		expect(displayTitle(makeTask({ input_text: '' }))).toBe('未命名任务');
	});

	it('prefixes longform export', () => {
		expect(displayTitle(makeTask({ input_text: '长文', longform_task_id: 'lf1', task_type: 'export' }))).toBe('完整长文本：长文');
	});
});

describe('requestFromTask', () => {
	it('rebuilds a full request from a normal task parameter snapshot', () => {
		const request = requestFromTask(makeTask({
			engine_id: 'omnivoice',
			voice_id: 'voice-1',
			input_text: '任务文本',
			parameters: {
				text: '参数文本',
				engine_id: 'omnivoice',
				voice_id: 'voice-2',
				language: 'en',
				emotion: 'happy',
				speed: 1.2,
				output_format: 'mp3'
			}
		}));

		expect(request.text).toBe('参数文本');
		expect(request.engine_id).toBe('omnivoice');
		expect(request.voice_id).toBe('voice-2');
		expect(request.language).toBe('en');
		expect(request.emotion_mode).toBe('emotion_vector');
		expect(request.output_format).toBe('mp3');
		expect(request.speed).toBe(1.2);
	});

	it('falls back to task fields for longform export snapshots without request fields', () => {
		const request = requestFromTask(makeTask({
			task_type: 'export',
			longform_task_id: 'lf1',
			engine_id: 'omnivoice',
			voice_id: 'voice-1',
			input_text: '完整长文本',
			parameters: {
				language: 'auto',
				output_format: 'wav',
				source_result_ids: ['r1', 'r2']
			}
		}));

		expect(request.text).toBe('完整长文本');
		expect(request.engine_id).toBe('omnivoice');
		expect(request.voice_id).toBe('voice-1');
		expect(request.language).toBe('auto');
		expect(request.emotion_mode).toBe('follow_reference');
		expect(request.output_format).toBe('wav');
	});
});

describe('formatSeconds', () => {
	it('formats correctly', () => {
		expect(formatSeconds(0)).toBe('0:00');
		expect(formatSeconds(65)).toBe('1:05');
		expect(formatSeconds(3661)).toBe('61:01');
	});
});

describe('formatAudioDuration', () => {
	it('formats ms to seconds', () => {
		expect(formatAudioDuration(null)).toBe('');
		expect(formatAudioDuration(0)).toBe('');
		expect(formatAudioDuration(1500)).toBe('1.5s');
		expect(formatAudioDuration(60000)).toBe('60.0s');
	});
});
