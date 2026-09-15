import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import type { VideoPreviewCacheStatus } from '$lib/api/types';
import {
	PreviewCacheSessionController,
	VideoLocalizationPreviewCacheClient,
	type PreviewCacheSessionState
} from './preview-cache-session-controller';

function cache(
	overrides: Partial<VideoPreviewCacheStatus> = {}
): VideoPreviewCacheStatus {
	return {
		contract_version: 'video-preview-cache-status-v1',
		state: 'building',
		phase: 'rendering',
		active_chunk: 0,
		started_at: '2026-07-30T00:00:00Z',
		updated_at: '2026-07-30T00:00:01Z',
		retryable: true,
		profile: 'preview',
		revision: 'revision-1',
		mode: 'auto',
		duration_ms: 30_000,
		chunk_ms: 10_000,
		frame_interval_ms: 1_000,
		frame_width: 160,
		frame_height: 90,
		sprite_columns: 5,
		sprite_rows: 2,
		ready_chunks: 0,
		total_chunks: 3,
		progress: 0,
		cached_bytes: 0,
		capacity_bytes: 1_000_000,
		ranges: [
			{ start_ms: 0, end_ms: 10_000, status: 'empty', frame_count: 0, sprite_rows: 0 },
			{ start_ms: 10_000, end_ms: 20_000, status: 'empty', frame_count: 0, sprite_rows: 0 },
			{ start_ms: 20_000, end_ms: 30_000, status: 'empty', frame_count: 0, sprite_rows: 0 }
		],
		error: null,
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

function setup() {
	const transport = {
		buildVideoPreviewCache: vi.fn(async () => cache()),
		videoPreviewCacheStatus: vi.fn(async () => cache()),
		refreshVideoPreviewCache: vi.fn(async () => cache())
	};
	const states: PreviewCacheSessionState[] = [];
	const invalidatePlaybackCoverage = vi.fn();
	const controller = new PreviewCacheSessionController(
		new VideoLocalizationPreviewCacheClient(transport),
		{
			onStateChange: (state) => states.push(state),
			onPlaybackCoverageInvalidated: invalidatePlaybackCoverage
		}
	);
	return { controller, transport, states, invalidatePlaybackCoverage };
}

afterEach(() => {
	vi.useRealTimers();
});

describe('preview cache session controller', () => {
	it('builds only the active playhead window instead of eagerly rendering the whole project', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();

		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 3_000 });

		expect(transport.buildVideoPreviewCache).toHaveBeenCalledWith('project-a', {
			start_ms: 0,
			end_ms: 23_000,
			full: false
		});
		expect(controller.state.cache?.revision).toBe('revision-1');
		controller.dispose();
	});

	it('clears the previous project immediately and ignores a late build response', async () => {
		vi.useFakeTimers();
		const first = deferred<VideoPreviewCacheStatus>();
		const { controller, transport } = setup();
		transport.buildVideoPreviewCache
			.mockImplementationOnce(() => first.promise)
			.mockResolvedValueOnce(cache({ revision: 'revision-b' }));

		const projectA = controller.activate('project-a', {
			sourceVideoAvailable: true,
			playheadMs: 1_000
		});
		const projectB = controller.activate('project-b', {
			sourceVideoAvailable: true,
			playheadMs: 2_000
		});
		expect(controller.state.cache).toBeNull();
		await projectB;
		first.resolve(cache({ revision: 'revision-a' }));
		await projectA;

		expect(controller.state.cache?.revision).toBe('revision-b');
		controller.dispose();
	});

	it('does not call the cache API when source video is unavailable', async () => {
		const { controller, transport } = setup();

		await controller.activate('project-a', {
			sourceVideoAvailable: false,
			playheadMs: 1_000
		});

		expect(transport.buildVideoPreviewCache).not.toHaveBeenCalled();
		expect(controller.state.cache).toBeNull();
	});

	it('polls after a recoverable initial build failure', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();
		transport.buildVideoPreviewCache.mockRejectedValueOnce(new Error('not ready'));
		transport.videoPreviewCacheStatus.mockResolvedValueOnce(cache({ revision: 'polled' }));

		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });
		await vi.advanceTimersByTimeAsync(500);

		expect(transport.videoPreviewCacheStatus).toHaveBeenCalledWith('project-a');
		expect(controller.state.cache?.revision).toBe('polled');
		controller.dispose();
	});

	it('clears cache and playback coverage after a definitive polling 404', async () => {
		vi.useFakeTimers();
		const { controller, transport, invalidatePlaybackCoverage } = setup();
		transport.videoPreviewCacheStatus.mockRejectedValueOnce(
			new ApiError('missing', 404)
		);
		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });

		await vi.advanceTimersByTimeAsync(500);

		expect(controller.state.cache).toBeNull();
		expect(invalidatePlaybackCoverage).toHaveBeenCalledOnce();
		await vi.advanceTimersByTimeAsync(10_000);
		expect(transport.videoPreviewCacheStatus).toHaveBeenCalledOnce();
		controller.dispose();
	});

	it('keeps sprite metadata, invalidates playable coverage, and retries transient polling errors', async () => {
		vi.useFakeTimers();
		const { controller, transport, invalidatePlaybackCoverage } = setup();
		transport.videoPreviewCacheStatus
			.mockRejectedValueOnce(new Error('temporary'))
			.mockResolvedValueOnce(cache({ revision: 'recovered' }));
		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });

		await vi.advanceTimersByTimeAsync(500);
		expect(controller.state.cache?.revision).toBe('revision-1');
		expect(invalidatePlaybackCoverage).toHaveBeenCalledOnce();
		await vi.advanceTimersByTimeAsync(1_500);
		expect(controller.state.cache?.revision).toBe('recovered');
		controller.dispose();
	});

	it('deduplicates demand chunks and skips ready or rendering ranges', async () => {
		vi.useFakeTimers();
		const demand = deferred<VideoPreviewCacheStatus>();
		const { controller, transport } = setup();
		transport.buildVideoPreviewCache
			.mockResolvedValueOnce(cache({
				ranges: [
					{ start_ms: 0, end_ms: 10_000, status: 'ready', frame_count: 10, sprite_rows: 2 },
					{ start_ms: 10_000, end_ms: 20_000, status: 'rendering', frame_count: 0, sprite_rows: 0 },
					{ start_ms: 20_000, end_ms: 30_000, status: 'empty', frame_count: 0, sprite_rows: 0 }
				]
			}))
			.mockImplementationOnce(() => demand.promise);
		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });

		expect(controller.requestAt(1_000)).toBe(false);
		expect(controller.requestAt(11_000)).toBe(false);
		expect(controller.requestAt(21_000)).toBe(true);
		expect(controller.requestAt(21_500)).toBe(false);
		expect(transport.buildVideoPreviewCache).toHaveBeenLastCalledWith('project-a', {
			start_ms: 20_000,
			end_ms: 30_000
		});
		demand.resolve(cache({ revision: 'demand-ready' }));
		await demand.promise;
		controller.dispose();
	});

	it('ignores a late demand response after project switching', async () => {
		vi.useFakeTimers();
		const demand = deferred<VideoPreviewCacheStatus>();
		const { controller, transport } = setup();
		transport.buildVideoPreviewCache
			.mockResolvedValueOnce(cache({ revision: 'revision-a' }))
			.mockImplementationOnce(() => demand.promise)
			.mockResolvedValueOnce(cache({ revision: 'revision-b' }));
		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });
		expect(controller.requestAt(21_000)).toBe(true);
		await controller.activate('project-b', { sourceVideoAvailable: true, playheadMs: 0 });

		demand.resolve(cache({ revision: 'late-a' }));
		await demand.promise;

		expect(controller.state.cache?.revision).toBe('revision-b');
		controller.dispose();
	});

	it('keeps refresh pending until the refreshed revision has playable chunks', async () => {
		vi.useFakeTimers();
		const { controller, transport, invalidatePlaybackCoverage } = setup();
		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });
		transport.refreshVideoPreviewCache.mockResolvedValueOnce(cache({
			revision: 'revision-2',
			state: 'building'
		}));
		transport.videoPreviewCacheStatus
			.mockResolvedValueOnce(cache({
				revision: 'revision-2',
				state: 'building',
				ready_chunks: 0
			}))
			.mockResolvedValueOnce(cache({
				revision: 'revision-2',
				state: 'partial',
				ready_chunks: 1
			}));

		const result = await controller.refresh();
		expect(result.status).toBe('started');
		expect(controller.state).toMatchObject({ refreshing: false, refreshPending: true });
		expect(invalidatePlaybackCoverage).toHaveBeenCalledOnce();
		await vi.advanceTimersByTimeAsync(400);
		expect(controller.state.refreshPending).toBe(true);
		await vi.advanceTimersByTimeAsync(5_000);
		expect(controller.state.refreshPending).toBe(false);
		controller.dispose();
	});

	it('recovers a timed-out refresh from status and reports completion', async () => {
		vi.useFakeTimers();
		const { controller, transport } = setup();
		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });
		transport.refreshVideoPreviewCache.mockRejectedValueOnce(
			new ApiError('timed out', 0, 'TIMEOUT')
		);
		transport.videoPreviewCacheStatus.mockResolvedValueOnce(cache({
			revision: 'revision-2',
			state: 'ready',
			ready_chunks: 3
		}));

		const result = await controller.refresh();

		expect(result).toEqual({ status: 'completed', message: '缓存刷新已完成' });
		expect(controller.state).toMatchObject({ refreshing: false, refreshPending: false });
		controller.dispose();
	});

	it('surfaces a refresh failure and clears the pending state', async () => {
		const { controller, transport } = setup();
		await controller.activate('project-a', { sourceVideoAvailable: true, playheadMs: 0 });
		transport.refreshVideoPreviewCache.mockRejectedValueOnce(new Error('refresh failed'));

		const result = await controller.refresh();

		expect(result).toEqual({ status: 'failed', error: 'refresh failed' });
		expect(controller.state).toMatchObject({ refreshing: false, refreshPending: false });
		controller.dispose();
	});
});
