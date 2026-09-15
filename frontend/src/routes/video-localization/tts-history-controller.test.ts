import { describe, expect, it, vi } from 'vitest';
import type { HistoryItem } from '$lib/api/types';
import {
	historyBelongsToSegment,
	TtsHistoryController,
	VideoLocalizationTtsHistoryClient
} from './tts-history-controller';

function history(resultId: string, projectId = 'project-a'): HistoryItem {
	return {
		result_id: resultId,
		task_id: `task-${resultId}`,
		generation_id: null,
		engine_id: 'test-engine',
		voice_id: null,
		voice_name: null,
		project_id: projectId,
		segment_id: null,
		localized_subtitle_id: null,
		cue_id: null,
		bind_to_video_localization: true,
		longform_task_id: null,
		longform_segment_index: null,
		longform_segment_count: null,
		longform_export_id: null,
		input_text: resultId,
		output_audio_id: null,
		output_path: null,
		duration_ms: null,
		generation_time_ms: null,
		verification: null,
		verification_error: null,
		parameter_snapshot: {},
		favorite: false,
		created_at: '2026-07-30T00:00:00Z'
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

function deletion(removedRecords: number) {
	return {
		removed_records: removedRecords,
		cleanup_failures: 0
	};
}

function page(items: HistoryItem[], total = items.length, offset = 0) {
	return { items, total, offset, limit: 40 };
}

function setup() {
	const historyApi = vi.fn(async (_params?: Record<string, unknown>) => [] as HistoryItem[]);
	const transport = {
		history: historyApi,
		historyPage: vi.fn(async (params: { offset?: number }) =>
			page(await historyApi(params), undefined, params.offset ?? 0)
		),
		deleteVideoLocalizationTtsHistory: vi.fn(
			async () => deletion(1)
		)
	};
	const updates: HistoryItem[][] = [];
	const controller = new TtsHistoryController(
		new VideoLocalizationTtsHistoryClient(transport),
		{ onItems: (items) => updates.push(items) }
	);
	return { controller, transport, updates };
}

describe('TTS history controller', () => {
	it('matches every localized subtitle frozen into a multi-subtitle generation', () => {
		const grouped = {
			...history('group-result'),
			segment_id: 'group-localized-1-localized-3',
			localized_subtitle_id: 'localized-1',
			cue_id: 'cue-1',
			parameter_snapshot: {
				source: 'video_localization',
				video_localization_target_subtitle_ids: [
					'localized-1',
					'localized-2',
					'localized-3'
				]
			}
		};

		expect(historyBelongsToSegment(grouped, 'localized-1')).toBe(true);
		expect(historyBelongsToSegment(grouped, 'localized-2')).toBe(true);
		expect(historyBelongsToSegment(grouped, 'localized-3')).toBe(true);
		expect(historyBelongsToSegment(grouped, 'localized-4')).toBe(false);
	});

	it('loads only the first project page', async () => {
		const { controller, transport } = setup();
		transport.history.mockResolvedValueOnce(
			Array.from({ length: 40 }, (_, index) => history(`result-${index}`))
		);

		await controller.loadProject('project-a');

		expect(transport.history).toHaveBeenCalledWith({
			limit: 40,
			offset: 0,
			project_id: 'project-a',
			source: 'video_localization'
		});
		expect(controller.items).toHaveLength(40);
	});

	it('appends a later page and exposes the server total', async () => {
		const { controller, transport } = setup();
		transport.historyPage
			.mockResolvedValueOnce(page(Array.from({ length: 40 }, (_, index) => history(`result-${index}`)), 85, 0))
			.mockResolvedValueOnce(page(Array.from({ length: 40 }, (_, index) => history(`result-${index + 40}`)), 85, 40));

		await controller.loadProject('project-a');
		await controller.loadMore('project-a');

		expect(controller.items).toHaveLength(80);
		expect(controller.total).toBe(85);
		expect(controller.hasMore).toBe(true);
		expect(transport.historyPage).toHaveBeenLastCalledWith({
			limit: 40,
			offset: 40,
			project_id: 'project-a',
			source: 'video_localization'
		});
	});

	it('rejects a project A response after project B becomes active', async () => {
		const projectA = deferred<HistoryItem[]>();
		const { controller, transport } = setup();
		transport.history
			.mockImplementationOnce(() => projectA.promise)
			.mockResolvedValueOnce([history('result-b', 'project-b')]);

		const loadA = controller.loadProject('project-a');
		await controller.loadProject('project-b');
		projectA.resolve([history('result-a')]);
		await loadA;

		expect(controller.projectId).toBe('project-b');
		expect(controller.items.map((item) => item.result_id)).toEqual(['result-b']);
	});

	it('lets only the newest history read publish', async () => {
		const older = deferred<HistoryItem[]>();
		const newer = deferred<HistoryItem[]>();
		const { controller, transport } = setup();
		transport.history
			.mockImplementationOnce(() => older.promise)
			.mockImplementationOnce(() => newer.promise);

		const first = controller.loadProject('project-a');
		const second = controller.refresh('project-a');
		newer.resolve([history('newer')]);
		await second;
		older.resolve([history('older')]);
		await first;

		expect(controller.items.map((item) => item.result_id)).toEqual(['newer']);
	});

	it('does not let a read started before deletion resurrect the record', async () => {
		const staleRead = deferred<HistoryItem[]>();
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a')])
			.mockImplementationOnce(() => staleRead.promise)
			.mockResolvedValueOnce([]);
		await controller.loadProject('project-a');

		const refresh = controller.refresh('project-a');
		await controller.deleteMany('project-a', ['result-a']);
		staleRead.resolve([history('result-a')]);
		await refresh;

		expect(controller.items).toEqual([]);
	});

	it('invalidates a read started while deletion is running', async () => {
		const pendingDelete = deferred<ReturnType<typeof deletion>>();
		const staleRead = deferred<HistoryItem[]>();
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a')])
			.mockImplementationOnce(() => staleRead.promise)
			.mockResolvedValueOnce([]);
		transport.deleteVideoLocalizationTtsHistory.mockImplementationOnce(
			() => pendingDelete.promise
		);
		await controller.loadProject('project-a');

		const deleteRequest = controller.deleteMany('project-a', ['result-a']);
		const refresh = controller.refresh('project-a');
		pendingDelete.resolve(deletion(1));
		await deleteRequest;
		staleRead.resolve([history('result-a')]);
		await refresh;

		expect(controller.items).toEqual([]);
	});

	it('treats an ambiguous delete error as success when reconciliation proves absence', async () => {
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a')])
			.mockResolvedValueOnce([]);
		transport.deleteVideoLocalizationTtsHistory.mockRejectedValueOnce(
			new Error('connection closed')
		);
		await controller.loadProject('project-a');

		await expect(controller.deleteMany('project-a', ['result-a'])).resolves.toEqual(
			deletion(1)
		);
		expect(controller.items).toEqual([]);
		expect(transport.history).toHaveBeenLastCalledWith({
			limit: -1,
			project_id: 'project-a',
			source: 'video_localization'
		});
	});

	it('preserves the original error when reconciliation still finds the record', async () => {
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a')])
			.mockResolvedValueOnce([history('result-a')]);
		const deleteError = new Error('delete failed');
		transport.deleteVideoLocalizationTtsHistory.mockRejectedValueOnce(deleteError);
		await controller.loadProject('project-a');

		await expect(controller.deleteMany('project-a', ['result-a'])).rejects.toBe(deleteError);
		expect(controller.items.map((item) => item.result_id)).toEqual(['result-a']);
	});

	it('deletes many explicit records with one project-scoped command', async () => {
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a'), history('result-b')])
			.mockResolvedValueOnce([]);
		transport.deleteVideoLocalizationTtsHistory.mockResolvedValueOnce(deletion(2));
		await controller.loadProject('project-a');

		await expect(
			controller.deleteMany('project-a', ['result-a', 'result-b'])
		).resolves.toEqual(deletion(2));
		expect(transport.deleteVideoLocalizationTtsHistory).toHaveBeenCalledTimes(1);
		expect(transport.deleteVideoLocalizationTtsHistory).toHaveBeenCalledWith(
			'project-a',
			{
				scope: 'result_ids',
				result_ids: ['result-a', 'result-b']
			}
		);
		expect(controller.items).toEqual([]);
	});

	it('deletes an entire project scope without relying on the 500-item projection', async () => {
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a')])
			.mockResolvedValueOnce([]);
		transport.deleteVideoLocalizationTtsHistory.mockResolvedValueOnce(deletion(501));
		await controller.loadProject('project-a');

		await expect(controller.deleteAll('project-a')).resolves.toEqual(deletion(501));

		expect(transport.deleteVideoLocalizationTtsHistory).toHaveBeenCalledWith(
			'project-a',
			{ scope: 'project' }
		);
		expect(controller.items).toEqual([]);
	});

	it('deletes every record linked to the requested subtitle scope', async () => {
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([
				{
					...history('result-a'),
					localized_subtitle_id: 'localized-other',
					cue_id: 'localized-1'
				},
				{ ...history('result-b'), cue_id: 'localized-2' }
			])
			.mockResolvedValueOnce([
				{ ...history('result-b'), cue_id: 'localized-2' }
			]);
		await controller.loadProject('project-a');

		await expect(
			controller.deleteSegment('project-a', 'localized-1')
		).resolves.toEqual(deletion(1));

		expect(transport.deleteVideoLocalizationTtsHistory).toHaveBeenCalledWith(
			'project-a',
			{ scope: 'segment', segment_id: 'localized-1' }
		);
		expect(controller.items.map((item) => item.result_id)).toEqual(['result-b']);
	});

	it('publishes the final server state after out-of-order bulk completions', async () => {
		const olderDelete = deferred<ReturnType<typeof deletion>>();
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a')])
			.mockResolvedValueOnce([])
			.mockResolvedValueOnce([]);
		transport.deleteVideoLocalizationTtsHistory
			.mockImplementationOnce(() => olderDelete.promise)
			.mockResolvedValueOnce(deletion(1));
		await controller.loadProject('project-a');

		const first = controller.deleteMany('project-a', ['result-a']);
		await controller.deleteMany('project-a', ['result-a']);
		olderDelete.resolve(deletion(1));

		await expect(first).resolves.toEqual(deletion(1));
		expect(controller.items).toEqual([]);
	});

	it('does not report an old-project deletion after its final refresh switches projects', async () => {
		const projectARefresh = deferred<HistoryItem[]>();
		const { controller, transport } = setup();
		transport.history
			.mockResolvedValueOnce([history('result-a')])
			.mockImplementationOnce(() => projectARefresh.promise)
			.mockResolvedValueOnce([history('result-b', 'project-b')]);
		await controller.loadProject('project-a');

		const deletionRequest = controller.deleteAll('project-a');
		await vi.waitFor(() => {
			expect(transport.history).toHaveBeenCalledTimes(2);
		});
		await controller.loadProject('project-b');
		projectARefresh.resolve([]);

		await expect(deletionRequest).resolves.toBeNull();
		expect(controller.items.map((item) => item.result_id)).toEqual(['result-b']);
	});
});
