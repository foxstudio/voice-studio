import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import type {
	VideoLocalizationOperation,
	VideoLocalizationOperationFeedV2
} from '$lib/api/types';
import {
	OperationFeedController,
	VideoLocalizationOperationFeedClient,
	type OperationFeedUpdateReason
} from './operation-feed-controller';

function operation(
	id: string,
	status: VideoLocalizationOperation['status'] = 'queued',
	overrides: Partial<VideoLocalizationOperation> = {}
): VideoLocalizationOperation {
	return {
		operation_id: id,
		project_id: 'project-a',
		kind: 'source_audio',
		status,
		label: '抽取原音轨',
		progress: status === 'success' ? 1 : 0,
		error_code: null,
		error_message: null,
		cancel_requested: false,
		result_summary: {},
		parameters: {},
		created_at: id === 'newer' ? '2026-07-30T00:00:02Z' : '2026-07-30T00:00:01Z',
		started_at: null,
		completed_at: null,
		...overrides
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<T>((nextResolve, nextReject) => {
		resolve = nextResolve;
		reject = nextReject;
	});
	return { promise, resolve, reject };
}

function feed(
	operations: VideoLocalizationOperation[],
	revision = 1,
	changed = true
): VideoLocalizationOperationFeedV2 {
	const activeOperations = operations.filter(
		(item) => item.status === 'queued' || item.status === 'running'
	);
	const history = operations.filter(
		(item) => item.status !== 'queued' && item.status !== 'running'
	);
	return {
		schema_version: 'operation-feed-v2',
		revision,
		history_revision: revision,
		changed,
		active_operations: changed
			? activeOperations as VideoLocalizationOperationFeedV2['active_operations']
			: [],
		history: changed
			? history as VideoLocalizationOperationFeedV2['history']
			: [],
		history_total: changed ? history.length : 0,
		next_cursor: null
	};
}

function setup() {
	const transport = {
		videoLocalizationOperationFeedV2: vi.fn(async () => feed([])),
		submitVideoLocalizationOperation: vi.fn(async (
			projectId: string,
			kind: VideoLocalizationOperation['kind']
		) => operation('submitted', 'queued', { project_id: projectId, kind })),
		submitVideoLocalizationAsrOperation: vi.fn(async (
			projectId: string
		) => operation('submitted-asr', 'queued', {
			project_id: projectId,
			kind: 'english_asr'
		})),
		submitVideoLocalizationLocalizationOperation: vi.fn(async (
			projectId: string
		) => operation('submitted-localization', 'queued', {
			project_id: projectId,
			kind: 'localization_draft'
		})),
		submitVideoLocalizationDubSubtitleOperation: vi.fn(async (
			projectId: string
		) => operation('submitted-dub-subtitles', 'queued', {
			project_id: projectId,
			kind: 'dub_subtitle_generation'
		})),
		cancelVideoLocalizationOperation: vi.fn(async (
			projectId: string,
			operationId: string
		) => operation(operationId, 'cancelled', { project_id: projectId })),
		retryVideoLocalizationOperation: vi.fn(async (
			projectId: string
		) => operation('retry', 'queued', { project_id: projectId }))
	};
	const updates: Array<{
		projectId: string;
		operations: VideoLocalizationOperation[];
		reason: OperationFeedUpdateReason;
	}> = [];
	const refreshAfterTerminal = vi.fn(async () => undefined);
	const onPollingSynchronized = vi.fn();
	const onTimeout = vi.fn();
	const onError = vi.fn();
	const controller = new OperationFeedController(
		new VideoLocalizationOperationFeedClient(transport),
		{
			onOperations: (projectId, operations, reason) =>
				updates.push({ projectId, operations, reason }),
			refreshAfterTerminal,
			onPollingSynchronized,
			onTimeout,
			onError,
			activePollMs: 100,
			idlePollMs: 500
		}
	);
	return {
		controller,
		transport,
		updates,
		refreshAfterTerminal,
		onPollingSynchronized,
		onTimeout,
		onError
	};
}

afterEach(() => {
	vi.useRealTimers();
});

describe('operation feed controller', () => {
	it('loads and sorts the active project operation feed', async () => {
		vi.useFakeTimers();
		const { controller, transport, updates } = setup();
		transport.videoLocalizationOperationFeedV2.mockResolvedValueOnce(feed([
			operation('older'),
			operation('newer')
		]));

		await controller.activate('project-a');

		expect(controller.operations.map((item) => item.operation_id)).toEqual(['newer', 'older']);
		expect(updates.at(-1)?.reason).toBe('load');
		controller.dispose();
	});

	it('clears the old feed immediately and rejects a late project response', async () => {
		vi.useFakeTimers();
		const projectA = deferred<VideoLocalizationOperationFeedV2>();
		const { controller, transport } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockImplementationOnce(() => projectA.promise)
			.mockResolvedValueOnce(feed([
				operation('project-b', 'success', { project_id: 'project-b' })
			]));

		const firstLoad = controller.activate('project-a');
		const secondLoad = controller.activate('project-b');
		expect(controller.operations).toEqual([]);
		await secondLoad;
		projectA.resolve(feed([operation('late-a')]));
		await firstLoad;

		expect(controller.operations.map((item) => item.operation_id)).toEqual(['project-b']);
		controller.dispose();
	});

	it('pauses timers while hidden and refreshes immediately when visible again', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();
		await controller.activate('project-a');
		controller.setVisible(false);

		await vi.advanceTimersByTimeAsync(1_000);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledOnce();

		controller.setVisible(true);
		await vi.advanceTimersByTimeAsync(0);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledTimes(2);
		controller.dispose();
	});

	it('sends the last revision and keeps the current feed on an unchanged poll', async () => {
		vi.useFakeTimers();
		const { controller, transport, updates, onPollingSynchronized } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce(feed([operation('task', 'success')], 7))
			.mockResolvedValueOnce(feed([], 7, false));
		await controller.activate('project-a');
		const updateCount = updates.length;

		await vi.advanceTimersByTimeAsync(500);

		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenLastCalledWith(
			'project-a',
			{ afterRevision: 7, historyLimit: 50 }
		);
		expect(updates).toHaveLength(updateCount);
		expect(controller.operations.map((item) => item.operation_id)).toEqual(['task']);
		expect(onPollingSynchronized).toHaveBeenCalledWith(
			[expect.objectContaining({ operation_id: 'task' })],
			null
		);
		controller.dispose();
	});

	it('uses idle discovery polling and switches to the active interval', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce(feed([], 1))
			.mockResolvedValueOnce(feed([operation('active', 'running')], 2))
			.mockResolvedValueOnce(feed([operation('active', 'running')], 3));
		await controller.activate('project-a');

		await vi.advanceTimersByTimeAsync(499);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledOnce();
		await vi.advanceTimersByTimeAsync(1);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledTimes(2);
		await vi.advanceTimersByTimeAsync(100);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledTimes(3);
		controller.dispose();
	});

	it('refreshes the draft before announcing an active-to-terminal transition', async () => {
		vi.useFakeTimers();
		const {
			controller,
			transport,
			refreshAfterTerminal,
			onPollingSynchronized
		} = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce(feed([operation('task', 'running')], 1))
			.mockResolvedValueOnce(feed([operation('task', 'success')], 2));
		await controller.activate('project-a');

		await vi.advanceTimersByTimeAsync(100);

		expect(refreshAfterTerminal).toHaveBeenCalledWith('project-a');
		expect(onPollingSynchronized).toHaveBeenCalledWith(
			[expect.objectContaining({ operation_id: 'task', status: 'success' })],
			expect.objectContaining({ operation_id: 'task', status: 'success' })
		);
		expect(refreshAfterTerminal.mock.invocationCallOrder[0]).toBeLessThan(
			onPollingSynchronized.mock.invocationCallOrder[0]
		);
		controller.dispose();
	});

	it('maps polling timeouts to a retry notice and retries after three seconds', async () => {
		vi.useFakeTimers();
		const { controller, transport, onTimeout, onError } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce(feed([operation('task', 'running')], 1))
			.mockRejectedValueOnce(new ApiError('slow', 0, 'TIMEOUT'))
			.mockResolvedValueOnce(feed([operation('task', 'running')], 2));
		await controller.activate('project-a');

		await vi.advanceTimersByTimeAsync(100);
		expect(onTimeout).toHaveBeenCalledOnce();
		expect(onError).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(3_000);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledTimes(3);
		controller.dispose();
	});

	it('reports unknown polling errors without dropping the last feed', async () => {
		vi.useFakeTimers();
		const { controller, transport, onError } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce(feed([operation('task', 'running')], 1))
			.mockRejectedValueOnce(new Error('backend offline'));
		await controller.activate('project-a');

		await vi.advanceTimersByTimeAsync(100);

		expect(onError).toHaveBeenCalledWith('backend offline');
		expect(controller.operations).toHaveLength(1);
		controller.dispose();
	});

	it('serializes overlapping polls and retries the current generation', async () => {
		vi.useFakeTimers();
		const inFlight = deferred<VideoLocalizationOperationFeedV2>();
		const { controller, transport } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce(feed([operation('task', 'running')], 1))
			.mockImplementationOnce(() => inFlight.promise)
			.mockResolvedValueOnce(feed([operation('task', 'running')], 3));
		await controller.activate('project-a');
		await vi.advanceTimersByTimeAsync(100);

		controller.setVisible(false);
		controller.setVisible(true);
		await vi.advanceTimersByTimeAsync(0);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledTimes(2);
		inFlight.resolve(feed([operation('task', 'running')], 2));
		await inFlight.promise;
		await vi.advanceTimersByTimeAsync(250);
		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenCalledTimes(3);
		controller.dispose();
	});

	it('appends keyset history pages without repeating active operations', async () => {
		vi.useFakeTimers();
		const { controller, transport, updates } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce({
				...feed([
					operation('active', 'running'),
					operation('history-4', 'success'),
					operation('history-3', 'success')
				], 7),
				history_revision: 3,
				history_total: 4,
				next_cursor: 'page-2'
			})
			.mockResolvedValueOnce({
				...feed([
					operation('history-2', 'success'),
					operation('history-1', 'success')
				], 7),
				history_revision: 3,
				history_total: 4,
				next_cursor: null
			});

		await controller.activate('project-a');
		expect(controller.historyState).toEqual({
			total: 4,
			loaded: 2,
			hasMore: true,
			loading: false
		});

		await controller.loadMoreHistory();

		expect(transport.videoLocalizationOperationFeedV2).toHaveBeenLastCalledWith(
			'project-a',
			{ cursor: 'page-2', historyLimit: 50 }
		);
		expect(controller.operations.filter((item) => item.status === 'running')).toHaveLength(1);
		expect(controller.historyState).toEqual({
			total: 4,
			loaded: 4,
			hasMore: false,
			loading: false
		});
		expect(updates.at(-1)?.reason).toBe('history');
		controller.dispose();
	});

	it('reloads the head after a stale history cursor', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();
		transport.videoLocalizationOperationFeedV2
			.mockResolvedValueOnce({
				...feed([operation('history-old', 'success')], 7),
				history_revision: 3,
				history_total: 2,
				next_cursor: 'stale-page'
			})
			.mockRejectedValueOnce(new ApiError(
				'stale',
				409,
				'VIDEO_LOCALIZATION_OPERATION_HISTORY_CURSOR_STALE'
			))
			.mockResolvedValueOnce({
				...feed([operation('history-new', 'success')], 8),
				history_revision: 4,
				history_total: 1,
				next_cursor: null
			});

		await controller.activate('project-a');
		await controller.loadMoreHistory();

		expect(controller.operations.map((item) => item.operation_id)).toEqual([
			'history-new'
		]);
		expect(controller.historyState).toEqual({
			total: 1,
			loaded: 1,
			hasMore: false,
			loading: false
		});
		controller.dispose();
	});

	it('owns submit, cancel, and retry mutations and updates one authoritative feed', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();
		await controller.activate('project-a');

		await controller.submit('project-a', 'stems', { source: 'manual' });
		await controller.cancel('project-a', 'submitted');
		await controller.retry('project-a', 'submitted');

		expect(transport.submitVideoLocalizationOperation).toHaveBeenCalledWith(
			'project-a',
			'stems',
			{ source: 'manual' }
		);
		expect(controller.operations.map((item) => item.operation_id)).toEqual([
			'retry',
			'submitted'
		]);
		controller.dispose();
	});

	it('can submit the first operation for a newly imported project before its feed loads', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();

		const submitted = await controller.submit('project-new', 'source_audio');

		expect(transport.submitVideoLocalizationOperation).toHaveBeenCalledWith(
			'project-new',
			'source_audio',
			{}
		);
		expect(submitted?.project_id).toBe('project-new');
		expect(controller.operations.map((item) => item.operation_id)).toEqual(['submitted']);
		controller.dispose();
	});

	it('submits ASR only through the typed ASR endpoint', async () => {
		const { controller, transport } = setup();

		await controller.submit('project-a', 'english_asr', {
			engine_id: 'auto',
			source_track_id: 'vocals'
		});

		expect(transport.submitVideoLocalizationAsrOperation).toHaveBeenCalledWith(
			'project-a',
			{ engine_id: 'auto', source_track_id: 'vocals' }
		);
		expect(transport.submitVideoLocalizationOperation).not.toHaveBeenCalled();
		controller.dispose();
	});

	it('submits localization only through the typed localization endpoint', async () => {
		const { controller, transport } = setup();

		await controller.submit('project-a', 'localization_draft', {
			source_language: 'en',
			target_language: 'zh-Hans'
		});

		expect(transport.submitVideoLocalizationLocalizationOperation).toHaveBeenCalledWith(
			'project-a',
			{ source_language: 'en', target_language: 'zh-Hans' }
		);
		expect(transport.submitVideoLocalizationOperation).not.toHaveBeenCalled();
		controller.dispose();
	});

	it('submits dub subtitles only through the typed workflow endpoint', async () => {
		const { controller, transport } = setup();

		await controller.submit('project-a', 'dub_subtitle_generation', {
			engine_id: 'qwen3-asr-mlx',
			execution_mode: 'full'
		});

		expect(
			transport.submitVideoLocalizationDubSubtitleOperation
		).toHaveBeenCalledWith('project-a', {
			engine_id: 'qwen3-asr-mlx',
			execution_mode: 'full'
		});
		expect(
			transport.submitVideoLocalizationOperation
		).not.toHaveBeenCalled();
		controller.dispose();
	});

	it('rejects a late mutation after deactivation', async () => {
		const submission = deferred<VideoLocalizationOperation>();
		const { controller, transport } = setup();
		transport.submitVideoLocalizationOperation.mockImplementationOnce(() => submission.promise);
		await controller.activate('project-a');

		const result = controller.submit('project-a', 'source_audio');
		controller.deactivate();
		submission.resolve(operation('late-submit'));

		await expect(result).resolves.toBeNull();
		expect(controller.operations).toEqual([]);
	});
});
