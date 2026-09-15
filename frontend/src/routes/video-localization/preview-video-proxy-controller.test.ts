import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VideoPlaybackProxyStatus } from '$lib/api/types';
import { PreviewVideoProxyController } from './preview-video-proxy-controller';

function status(
	overrides: Partial<VideoPlaybackProxyStatus> = {}
): VideoPlaybackProxyStatus {
	return {
		contract_version: 'video-playback-proxy-status-v2',
		state: 'building',
		mode: 'segmented',
		variant: 'segments',
		playable: false,
		profile: 'segmented-h264-fmp4-v1',
		revision: 'build-1',
		duration_ms: 20_000,
		segment_ms: 4_000,
		requested_range: { start_ms: 0, end_ms: 12_000 },
		ready_ranges: [],
		active_segment: 0,
		ready_segments: 0,
		total_segments: 5,
		progress: 0,
		updated_at: null,
		retryable: false,
		error: null,
		...overrides
	};
}

describe('PreviewVideoProxyController', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it('uses the source immediately when the actual browser probe succeeds', async () => {
		const prepare = vi.fn(async () => status({
			state: 'ready',
			mode: 'source',
			variant: 'source',
			playable: true,
			profile: 'source',
			progress: 1,
			ready_segments: 5,
			ready_ranges: [{ start_ms: 0, end_ms: 20_000 }]
		}));
		const controller = new PreviewVideoProxyController({
			prepare,
			probeSource: vi.fn(async () => true)
		});

		controller.activate('project-a', 'video-r1');
		await vi.waitFor(() => expect(controller.state.playable).toBe(true));

		expect(prepare).toHaveBeenCalledWith('project-a', {
			source_playable: true,
			start_ms: 0,
			fill_background: true
		});
		expect(controller.state).toMatchObject({
			status: 'ready',
			mode: 'source',
			variant: 'source'
		});
		controller.dispose();
	});

	it('publishes a segmented range before background completion', async () => {
		vi.useFakeTimers();
		const prepare = vi.fn()
			.mockResolvedValueOnce(status())
			.mockResolvedValue(status({
				state: 'partial',
				playable: true,
				ready_ranges: [{ start_ms: 0, end_ms: 4_000 }],
				ready_segments: 1,
				progress: 0.2,
				active_segment: 1
			}));
		const controller = new PreviewVideoProxyController({
			prepare,
			probeSource: vi.fn(async () => false),
			pollIntervalMs: 50
		});

		controller.activate('project-a', 'video-r1');
		await vi.runOnlyPendingTimersAsync();
		await vi.runOnlyPendingTimersAsync();

		expect(prepare.mock.calls.length).toBeGreaterThanOrEqual(2);
		expect(controller.state).toMatchObject({
			status: 'partial',
			mode: 'segmented',
			playable: true,
			readySegments: 1
		});
		controller.dispose();
	});

	it('prioritizes a new seek range and discards the older poll generation', async () => {
		const prepare = vi.fn(async (_projectId, request) => status({
			state: 'partial',
			playable: true,
			requested_range: {
				start_ms: request.start_ms ?? 0,
				end_ms: (request.start_ms ?? 0) + 12_000
			},
			ready_ranges: [{
				start_ms: request.start_ms ?? 0,
				end_ms: (request.start_ms ?? 0) + 4_000
			}],
			ready_segments: 1
		}));
		const controller = new PreviewVideoProxyController({
			prepare,
			probeSource: vi.fn(async () => false)
		});

		controller.activate('project-a', 'video-r1');
		await vi.waitFor(() => expect(controller.state.playable).toBe(true));
		controller.ensureRange(48_000);
		await vi.waitFor(() => expect(controller.state.requestedRange.start_ms).toBe(48_000));

		expect(prepare).toHaveBeenLastCalledWith('project-a', {
			source_playable: false,
			start_ms: 48_000,
			fill_background: true
		});
		controller.dispose();
	});

	it('keeps an initial playhead request pending until the browser probe finishes', async () => {
		let resolveProbe: (value: boolean) => void = () => {};
		const probeSource = vi.fn(() => new Promise<boolean>((resolve) => {
			resolveProbe = resolve;
		}));
		const prepare = vi.fn(async () => status());
		const controller = new PreviewVideoProxyController({ prepare, probeSource });

		controller.activate('project-a', 'video-r1');
		controller.ensureRange(48_000);
		await Promise.resolve();

		expect(prepare).not.toHaveBeenCalled();
		resolveProbe(false);
		await vi.waitFor(() => expect(prepare).toHaveBeenCalledOnce());
		expect(prepare).toHaveBeenCalledWith('project-a', {
			source_playable: false,
			start_ms: 48_000,
			fill_background: true
		});
		controller.dispose();
	});

	it('reports a backend contract mismatch instead of remaining at zero percent', async () => {
		const onStateChange = vi.fn();
		const prepare = vi.fn(async () => ({
			contract_version: 'video-playback-proxy-status-v1',
			status: 'ready',
			variant: 'preview',
			profile: '720p-h264-v1',
			revision: 'legacy-preview.mp4',
			retryable: false,
			error: null
		}) as unknown as VideoPlaybackProxyStatus);
		const controller = new PreviewVideoProxyController({
			prepare,
			probeSource: vi.fn(async () => false),
			onStateChange
		});

		controller.activate('project-a', 'video-r1');
		await vi.waitFor(() => expect(controller.state.status).toBe('failed'));

		expect(controller.state).toMatchObject({
			playable: false,
			retryable: true,
			readyRanges: []
		});
		expect(controller.state.error).toContain('版本不一致');
		expect(prepare).toHaveBeenCalledTimes(1);
		controller.dispose();
	});
});
