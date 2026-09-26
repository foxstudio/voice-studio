import { describe, expect, it, vi } from 'vitest';
import type { VideoLocalizationDraft } from '$lib/api/types';
import { TtsSubmissionQueue, withTtsSubmissionQueueLabels } from './tts-submission-queue';
import { createTtsInitializationPlaceholder } from './tts-initialization-clips';

function deferred() {
	let resolve!: () => void;
	const promise = new Promise<void>((nextResolve) => {
		resolve = nextResolve;
	});
	return { promise, resolve };
}

describe('TTS submission queue', () => {
	it('projects active and queued labels without removing timeline clips', () => {
		const draft = {
			timeline_clips: [
				{ clip_id: 'a', optimistic_tts_workflow_id: 'init:submission-a', status_label: '等待' },
				{ clip_id: 'b', optimistic_tts_workflow_id: 'init:submission-b', status_label: '等待' },
				{ clip_id: 'ready', status_label: null }
			]
		};
		const projected = withTtsSubmissionQueueLabels(draft as unknown as VideoLocalizationDraft, {
			activeSubmissionId: 'submission-a',
			queuedSubmissionIds: ['submission-b']
		});

		expect(projected.timeline_clips).toEqual([
			{ clip_id: 'a', optimistic_tts_workflow_id: 'init:submission-a', status_label: '准备提交' },
			{ clip_id: 'b', optimistic_tts_workflow_id: 'init:submission-b', status_label: '排队中' },
			{ clip_id: 'ready', status_label: null }
		]);
	});

	it('keeps every immediately created disjoint placeholder visible while the first submission is held', async () => {
		const first = deferred();
		const ids = ['a', 'b', 'c', 'd'];
		let clips = ids.map((id, index) => createTtsInitializationPlaceholder([], {
			clientId: id,
			segmentId: `segment-${id}`,
			primaryCueId: null,
			sourceCueIds: [],
			startMs: index * 1000,
			endMs: index * 1000 + 800
		}));
		const queue = new TtsSubmissionQueue({
			onStateChange: (state) => {
				clips = withTtsSubmissionQueueLabels(
					{ timeline_clips: clips } as unknown as VideoLocalizationDraft,
					state
				).timeline_clips as typeof clips;
			}
		});

		for (const [index, id] of ids.entries()) {
			queue.enqueue(id, index === 0
				? async () => { await first.promise; }
				: async () => {});
		}

		expect(clips.map((clip) => clip.clip_id)).toEqual(ids.map((id) => `pending_tts_init_${id}`));
		expect(clips.map((clip) => clip.status_label)).toEqual(['准备提交', '排队中', '排队中', '排队中']);
		expect(new Set(clips.map((clip) => clip.clip_id)).size).toBe(ids.length);

		first.resolve();
		await queue.whenIdle();
	});

	it('submits clicks one at a time in click order', async () => {
		const first = deferred();
		const second = deferred();
		const started: string[] = [];
		const queue = new TtsSubmissionQueue();

		expect(queue.enqueue('submission-a', async () => {
			started.push('submission-a');
			await first.promise;
		})).toBe(true);
		expect(queue.enqueue('submission-b', async () => {
			started.push('submission-b');
			await second.promise;
		})).toBe(true);
		expect(queue.enqueue('submission-c', async () => {
			started.push('submission-c');
		})).toBe(true);

		expect(started).toEqual(['submission-a']);
		expect(queue.state).toEqual({
			activeSubmissionId: 'submission-a',
			queuedSubmissionIds: ['submission-b', 'submission-c']
		});

		first.resolve();
		await vi.waitFor(() => expect(started).toEqual(['submission-a', 'submission-b']));
		second.resolve();
		await queue.whenIdle();

		expect(started).toEqual(['submission-a', 'submission-b', 'submission-c']);
		expect(queue.state).toEqual({ activeSubmissionId: '', queuedSubmissionIds: [] });
	});

	it('continues after one submission fails and can discard work from an old project session', async () => {
		const first = deferred();
		const started: string[] = [];
		const errors: Array<[string, unknown]> = [];
		const queue = new TtsSubmissionQueue({
			onError: (submissionId, error) => errors.push([submissionId, error])
		});
		const failure = new Error('prepare failed');

		queue.enqueue('submission-a', async () => {
			started.push('submission-a');
			await first.promise;
			throw failure;
		});
		queue.enqueue('submission-b', async () => {
			started.push('submission-b');
		});
		queue.reset();
		queue.enqueue('submission-c', async () => {
			started.push('submission-c');
		});

		first.resolve();
		await queue.whenIdle();

		expect(started).toEqual(['submission-a', 'submission-c']);
		expect(errors).toEqual([['submission-a', failure]]);
	});
});
