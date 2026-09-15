import { describe, expect, it } from 'vitest';
import {
	buildVisibleFrameTicks,
	buildVisibleSecondTicks,
	editAudioFrameInterval,
	editFrameInterval,
	frameCoverage,
	framePrecisionVisible,
	frameTimeMs,
	lastFrameStartMs,
	normalizeAudioFrameInterval,
	snapTimeToFrame,
	stepFrameTime,
	timelineRangeWidthPercent
} from './frame-timeline';

describe('frame timeline', () => {
	it('snaps pointer positions and frame stepping to source frame boundaries', () => {
		expect(snapTimeToFrame(51, 25)).toBe(40);
		expect(snapTimeToFrame(61, 25)).toBe(80);
		expect(stepFrameTime(80, -1, 25, 1000)).toBe(40);
		expect(stepFrameTime(960, 1, 25, 1000)).toBe(960);
		expect(lastFrameStartMs(1000, 25)).toBe(960);
	});

	it('supports fractional video frame rates without accumulating pointer drift', () => {
		expect(frameTimeMs(240, 23.976)).toBe(10010);
		expect(snapTimeToFrame(10009, 23.976)).toBe(10010);
	});

	it('keeps a one-frame clip at its exact timeline width without a visual minimum', () => {
		expect(
			timelineRangeWidthPercent(203_708, 203_750, 5_416_668)
		).toBeCloseTo((42 / 5_416_668) * 100, 12);
	});

	it('moves and trims subtitle intervals only by whole frames', () => {
		expect(editFrameInterval({
			mode: 'move', startMs: 100, endMs: 900, deltaMs: 63, frameRate: 25,
			minStartMs: 0, maxEndMs: 2000, minDurationMs: 600
		})).toEqual({ startMs: 200, endMs: 1000 });
		expect(editFrameInterval({
			mode: 'trim-end', startMs: 200, endMs: 1000, deltaMs: -51, frameRate: 25,
			minStartMs: 0, maxEndMs: 2000, minDurationMs: 600
		})).toEqual({ startMs: 200, endMs: 960 });
	});

	it('preserves exact subtitle timing when a pointer has not moved a frame', () => {
		for (const mode of ['move', 'trim-start', 'trim-end'] as const) {
			expect(editFrameInterval({ mode, startMs: 1003, endMs: 2013,
				deltaMs: 1, frameRate: 30, maxEndMs: 3000, minDurationMs: 600
			})).toEqual({ startMs: 1003, endMs: 2013 });
		}
	});

	it('does not quantize the untouched subtitle edge when trimming', () => {
		expect(editFrameInterval({ mode: 'trim-start', startMs: 1003, endMs: 2013,
			deltaMs: 100, frameRate: 30, maxEndMs: 3000, minDurationMs: 600
		})).toEqual({ startMs: 1100, endMs: 2013 });
		expect(editFrameInterval({ mode: 'trim-end', startMs: 1003, endMs: 2013,
			deltaMs: 100, frameRate: 30, maxEndMs: 3000, minDurationMs: 600
		})).toEqual({ startMs: 1003, endMs: 2100 });
	});

	it('keeps audio trim handles inside the decoded source duration', () => {
		const result = editAudioFrameInterval({
			mode: 'trim-end', startMs: 2000, endMs: 3200, sourceStartMs: 400,
			sourceEndMs: 1600, sourceDurationMs: 1800, deltaMs: 5000,
			frameRate: 25, timelineDurationMs: 12000
		});
		expect(result).toEqual({ startMs: 2000, endMs: 3400, sourceStartMs: 400, sourceEndMs: 1800 });
	});

	it('allows an audio clip to be trimmed down to one source frame', () => {
		const frameRate = 25;
		const result = editAudioFrameInterval({
			mode: 'trim-end',
			startMs: 2000,
			endMs: 3200,
			sourceStartMs: 400,
			sourceEndMs: 1600,
			sourceDurationMs: 1800,
			deltaMs: -5000,
			frameRate,
			timelineDurationMs: 12000
		});

		expect(result).toEqual({
			startMs: 2000,
			endMs: 2040,
			sourceStartMs: 400,
			sourceEndMs: 440
		});
	});

	it('repairs a one-frame timeline/source mismatch before trimming from the start edge', () => {
		const frameRate = 24;
		const initial = normalizeAudioFrameInterval({
			startMs: 203_708,
			endMs: 216_250,
			sourceStartMs: 375,
			sourceEndMs: 12_875,
			frameRate,
			timelineDurationMs: 5_416_668
		});
		expect(initial).toEqual({
			startMs: 203_708,
			endMs: 216_208,
			sourceStartMs: 375,
			sourceEndMs: 12_875
		});

		const result = editAudioFrameInterval({
			mode: 'trim-start',
			...initial,
			sourceDurationMs: 12_875,
			deltaMs: 60_000,
			frameRate,
			timelineDurationMs: 5_416_668
		});
		expect(result).toEqual({
			startMs: 216_167,
			endMs: 216_208,
			sourceStartMs: 12_833,
			sourceEndMs: 12_875
		});
	});

	it('lets a trimmed audio start expand only as far as source zero', () => {
		const result = editAudioFrameInterval({
			mode: 'trim-start', startMs: 2000, endMs: 3200, sourceStartMs: 400,
			sourceEndMs: 1600, sourceDurationMs: 1800, deltaMs: -1000,
			frameRate: 25, timelineDurationMs: 12000
		});
		expect(result).toEqual({ startMs: 1600, endMs: 3200, sourceStartMs: 0, sourceEndMs: 1600 });
	});

	it('shows sparse frame labels only after frames have useful screen width', () => {
		expect(framePrecisionVisible(10_000, 25, 10, 1000)).toBe(true);
		expect(framePrecisionVisible(600_000, 25, 10, 1000)).toBe(false);
		const ticks = buildVisibleFrameTicks({
			durationMs: 10_000, frameRate: 25, startMs: 0, endMs: 1000, zoom: 10, viewportWidth: 1000
		});
		expect(ticks.length).toBeGreaterThan(20);
		expect(ticks.filter((tick) => tick.label).map((tick) => tick.label).slice(0, 3)).toEqual(['0f', '2f', '4f']);
	});

	it('keeps whole-second labels available beside frame labels at high zoom', () => {
		const ticks = buildVisibleSecondTicks({
			durationMs: 120_000,
			startMs: 74_200,
			endMs: 77_200
		});
		expect(ticks.map((tick) => tick.label)).toEqual(['01:13', '01:14', '01:15', '01:16', '01:17', '01:18', '01:19']);
		expect(ticks.find((tick) => tick.label === '01:16')?.timeMs).toBe(76_000);
	});

	it('restarts frame labels from the same values inside every second', () => {
		const ticks = buildVisibleFrameTicks({
			durationMs: 3_000, frameRate: 24, startMs: 0, endMs: 3_000, zoom: 100, viewportWidth: 1000
		});
		const labelsBySecond = [0, 1, 2].map((second) => ticks
			.filter((tick) => Math.floor(tick.frame / 24) === second && tick.label !== '0f' && tick.label)
			.map((tick) => tick.label));
		expect(labelsBySecond[0]).toEqual(labelsBySecond[1]);
		expect(labelsBySecond[1]).toEqual(labelsBySecond[2]);
	});

	it('covers the frame to the right of a pointer and clamps the final frame', () => {
		expect(frameCoverage(100, 25, 1000)).toEqual({ frame: 2, startMs: 80, endMs: 120 });
		expect(frameCoverage(1000, 25, 1000)).toEqual({ frame: 24, startMs: 960, endMs: 1000 });
	});
});
