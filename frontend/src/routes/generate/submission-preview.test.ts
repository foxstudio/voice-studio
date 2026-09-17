import { describe, expect, it } from 'vitest';
import {
	attachSubmissionTask,
	clearSubmissionPreview,
	completeSubmissionTask,
	createSubmissionPreview
} from './submission-preview';

describe('generation submission preview', () => {
	it('freezes the visible request summary at click time', () => {
		const draft = {
			inputText: '第一版台词',
			engineId: 'omnivoice',
			engineName: 'OmniVoice',
			engineKind: 'local' as const,
			voiceLabel: '示例音色'
		};
		const preview = createSubmissionPreview(draft, {
			submissionId: 'submission-1',
			createdAt: '2026-07-22T10:00:00.000Z'
		});

		draft.inputText = '后来编辑的台词';
		draft.voiceLabel = '另一个音色';

		expect(preview).toEqual({
			submissionId: 'submission-1',
			taskId: null,
			inputText: '第一版台词',
			engineId: 'omnivoice',
			engineName: 'OmniVoice',
			engineKind: 'local',
			voiceLabel: '示例音色',
			createdAt: '2026-07-22T10:00:00.000Z',
			stage: 'initializing'
		});
	});

	it('stays visible until its formal task is observed', () => {
		const preview = createSubmissionPreview({
			inputText: '测试',
			engineId: 'indextts-v2',
			engineName: 'IndexTTS',
			engineKind: 'local',
			voiceLabel: '测试音色'
		}, { submissionId: 'submission-2', createdAt: '2026-07-22T10:00:00.000Z' });
		const attached = attachSubmissionTask(preview, 'submission-2', 'task-123');

		expect(attached?.taskId).toBe('task-123');
		expect(completeSubmissionTask(attached, 'another-task')).toBe(attached);
		expect(completeSubmissionTask(attached, 'task-123')).toBeNull();
	});

	it('clears only the matching failed submission', () => {
		const preview = createSubmissionPreview({
			inputText: '测试',
			engineId: 'omnivoice',
			engineName: 'OmniVoice',
			engineKind: 'local',
			voiceLabel: '测试音色'
		}, { submissionId: 'submission-3', createdAt: '2026-07-22T10:00:00.000Z' });

		expect(clearSubmissionPreview(preview, 'older-submission')).toBe(preview);
		expect(clearSubmissionPreview(preview, 'submission-3')).toBeNull();
	});
});
