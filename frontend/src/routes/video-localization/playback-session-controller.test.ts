import { describe, expect, it, vi } from 'vitest';
import {
	PlaybackSessionController,
	type PlaybackDriver,
	type PlaybackLoopRange
} from './playback-session-controller';

describe('playback session controller', () => {
	function setup() {
		let projectId = 'project-a';
		let draftProjectId = 'project-a';
		let hasDraft = true;
		let timeMs = 1_000;
		let playing = false;
		let preparing = false;
		let hoverTimeMs: number | null = 800;
		let loopRange: PlaybackLoopRange | null = null;
		let restored = false;
		const persisted: Array<{ projectId: string; playheadMs: number }> = [];
		const driver: PlaybackDriver = {
			playPause: vi.fn(),
			play: vi.fn(),
			seek: vi.fn(),
			scrub: vi.fn(),
			endScrub: vi.fn(),
			refreshMediaCache: vi.fn()
		};
		const controller = new PlaybackSessionController({
			getProjectId: () => projectId,
			getDraftProjectId: () => draftProjectId,
			hasDraft: () => hasDraft,
			getTimeMs: () => timeMs,
			getPlaying: () => playing,
			setTimeMs: (value) => (timeMs = value),
			setPlaying: (value) => (playing = value),
			setPreparing: (value) => (preparing = value),
			setHoverTimeMs: (value) => (hoverTimeMs = value),
			setLoopRange: (value) => (loopRange = value),
			setRestored: (value) => (restored = value),
			persistPlayhead: (activeProjectId, playheadMs) => persisted.push({ projectId: activeProjectId, playheadMs })
		});
		return {
			controller,
			driver,
			persisted,
			state: () => ({ projectId, draftProjectId, hasDraft, timeMs, playing, preparing, hoverTimeMs, loopRange, restored }),
			setProjectId: (value: string) => (projectId = value),
			setDraftProjectId: (value: string) => (draftProjectId = value)
		};
	}

	it('restores the playhead only for the active loaded project', () => {
		const { controller, driver, state, setDraftProjectId } = setup();
		controller.register(driver);

		expect(controller.restore('project-a')).toBe(true);
		expect(driver.seek).toHaveBeenCalledWith(1_000);
		expect(state().restored).toBe(true);

		setDraftProjectId('project-b');
		controller.register(driver);
		expect(controller.restore('project-a')).toBe(false);
		expect(state().restored).toBe(false);
	});

	it('ignores media time updates before restoration and persists them afterwards', () => {
		const { controller, driver, state, persisted } = setup();
		controller.register(driver);

		controller.updateTime(1_250);
		expect(state().timeMs).toBe(1_000);

		controller.restore('project-a');
		controller.updateTime(1_250);
		expect(state().timeMs).toBe(1_250);
		expect(persisted.at(-1)).toEqual({ projectId: 'project-a', playheadMs: 1_250 });
	});

	it('clears hover on play and persists the final playhead on pause', () => {
		const { controller, driver, state, persisted } = setup();
		controller.register(driver);
		controller.restore('project-a');

		controller.updatePlaying(true);
		expect(state().playing).toBe(true);
		expect(state().hoverTimeMs).toBeNull();

		controller.updatePlaying(false);
		expect(state().playing).toBe(false);
		expect(persisted.at(-1)).toEqual({ projectId: 'project-a', playheadMs: 1_000 });
	});

	it('plays a committed range from its exact start', () => {
		const { controller, driver, state } = setup();
		controller.register(driver);
		controller.restore('project-a');

		controller.playRange({ start_ms: 2_000, end_ms: 3_200 });

		expect(state().loopRange).toEqual({ start_ms: 2_000, end_ms: 3_200 });
		expect(state().timeMs).toBe(2_000);
		expect(driver.endScrub).toHaveBeenCalledOnce();
		expect(driver.seek).toHaveBeenCalledWith(2_000);
		expect(driver.play).toHaveBeenCalledOnce();
	});

	it('uses the selected range as a loop only when playback starts inside it', () => {
		const { controller, driver, state } = setup();
		controller.register(driver);
		controller.restore('project-a');

		controller.togglePlayback({ start_ms: 500, end_ms: 1_500 });
		expect(state().loopRange).toEqual({ start_ms: 500, end_ms: 1_500 });
		expect(driver.playPause).toHaveBeenCalledOnce();

		controller.seek(2_000);
		controller.togglePlayback({ start_ms: 500, end_ms: 1_500 });
		expect(state().loopRange).toBeNull();
	});

	it('keeps hover scrubbing out of the committed playhead', () => {
		const { controller, driver, state } = setup();
		controller.register(driver);

		controller.hoverScrub(2_400);
		expect(state().hoverTimeMs).toBe(2_400);
		expect(state().timeMs).toBe(1_000);
		expect(driver.scrub).toHaveBeenCalledWith(2_400);

		controller.endHoverScrub();
		expect(state().hoverTimeMs).toBeNull();
		expect(driver.endScrub).toHaveBeenCalledOnce();
	});

	it('previews a dragged timeline seek without repeatedly committing media loads', () => {
		const { controller, driver, state, persisted } = setup();
		controller.register(driver);
		controller.restore('project-a');
		vi.mocked(driver.seek).mockClear();
		persisted.length = 0;

		controller.previewSeek(2_400);
		controller.previewSeek(2_800);

		expect(state().timeMs).toBe(2_800);
		expect(driver.scrub).toHaveBeenNthCalledWith(1, 2_400);
		expect(driver.scrub).toHaveBeenNthCalledWith(2, 2_800);
		expect(driver.seek).not.toHaveBeenCalled();
		expect(persisted).toEqual([]);

		controller.commitSeek(2_800);

		expect(driver.endScrub).toHaveBeenCalledOnce();
		expect(driver.seek).toHaveBeenCalledOnce();
		expect(driver.seek).toHaveBeenCalledWith(2_800);
		expect(persisted).toEqual([{ projectId: 'project-a', playheadMs: 2_800 }]);
	});

	it('throttles playback-clock persistence and flushes the newest position', () => {
		vi.useFakeTimers();
		try {
			const { controller, driver, persisted } = setup();
			controller.register(driver);
			controller.restore('project-a');

			controller.updateTime(1_100);
			controller.updateTime(1_200);
			controller.updateTime(1_300);
			expect(persisted).toEqual([{ projectId: 'project-a', playheadMs: 1_100 }]);

			vi.advanceTimersByTime(250);
			expect(persisted.at(-1)).toEqual({ projectId: 'project-a', playheadMs: 1_300 });
			expect(persisted).toHaveLength(2);
		} finally {
			vi.useRealTimers();
		}
	});

	it('flushes a pending playhead before replacing the playback driver', () => {
		vi.useFakeTimers();
		try {
			const { controller, driver, persisted } = setup();
			controller.register(driver);
			controller.restore('project-a');
			controller.updateTime(1_100);
			controller.updateTime(1_900);

			controller.register(null);

			expect(persisted.at(-1)).toEqual({ projectId: 'project-a', playheadMs: 1_900 });
		} finally {
			vi.useRealTimers();
		}
	});
});
