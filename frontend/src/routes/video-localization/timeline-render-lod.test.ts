import { describe, expect, it } from 'vitest';
import {
	OVERVIEW_WAVEFORM_BINS,
	resolveVirtualizedClipRenderWindow,
	resolveVisibleWaveformRequest,
	resolveTimelineRenderLevel,
	timelinePixelsPerSecond,
	waveformBinsForClipPixels,
	waveformPreviewBins
} from './timeline-render-lod';

describe('timeline render level', () => {
	it('uses an overview for a dense long-form timeline whose items are sub-pixel at full view', () => {
		expect(resolveTimelineRenderLevel({
			durationMs: 665_088,
			zoom: 1,
			viewportWidth: 498,
			itemCount: 834
		})).toBe('overview');
		expect(timelinePixelsPerSecond(665_088, 1, 498)).toBeCloseTo(0.7487, 3);
	});

	it('restores editable detail once zoom provides useful horizontal precision', () => {
		expect(resolveTimelineRenderLevel({
			durationMs: 665_088,
			zoom: 10,
			viewportWidth: 498,
			itemCount: 834
		})).toBe('detail');
	});

	it('keeps sparse timelines editable even when the media is long', () => {
		expect(resolveTimelineRenderLevel({
			durationMs: 665_088,
			zoom: 1,
			viewportWidth: 498,
			itemCount: 20
		})).toBe('detail');
	});
});

describe('timeline waveform precision', () => {
	it('renders only the visible neighborhood of a very long clip', () => {
		expect(resolveVirtualizedClipRenderWindow({
			clipStartMs: 0,
			clipEndMs: 900_000,
			viewportStartMs: 300_000,
			viewportEndMs: 310_000
		})).toEqual({
			startMs: 295_000,
			endMs: 315_000,
			showStartEdge: false,
			showEndEdge: false
		});
	});

	it('keeps real trim edges when they enter the rendered neighborhood', () => {
		expect(resolveVirtualizedClipRenderWindow({
			clipStartMs: 100_000,
			clipEndMs: 140_000,
			viewportStartMs: 98_000,
			viewportEndMs: 108_000
		})).toEqual({
			startMs: 100_000,
			endMs: 113_000,
			showStartEdge: true,
			showEndEdge: false
		});
	});

	it('requests only a stable overscanned source window for a long clip', () => {
		const request = resolveVisibleWaveformRequest({
			clipStartMs: 0,
			clipEndMs: 2_400_000,
			sourceStartMs: 0,
			sourceEndMs: 2_400_000,
			timelineDurationMs: 2_400_000,
			zoom: 240,
			scrollLeft: 24_000,
			viewportWidth: 2_000,
			devicePixelRatio: 2
		});

		expect(request).toEqual({ startMs: 112_000, endMs: 144_000, bins: 19_200 });
	});

	it('reuses the same source request when a fully visible clip is only moved', () => {
		const base = { clipStartMs: 1000, clipEndMs: 3000, sourceStartMs: 500,
			sourceEndMs: 2500, timelineDurationMs: 10000, zoom: 1,
			scrollLeft: 0, viewportWidth: 1000, devicePixelRatio: 2 };
		expect(resolveVisibleWaveformRequest({ ...base, clipStartMs: 6000, clipEndMs: 8000 }))
			.toEqual(resolveVisibleWaveformRequest(base));
	});

	it('keeps the waveform request stable while scrolling inside the same viewport tile', () => {
		const base = {
			clipStartMs: 0,
			clipEndMs: 2_400_000,
			sourceStartMs: 0,
			sourceEndMs: 2_400_000,
			timelineDurationMs: 2_400_000,
			zoom: 240,
			viewportWidth: 2_000,
			devicePixelRatio: 2
		};
		expect(resolveVisibleWaveformRequest({ ...base, scrollLeft: 24_000 }))
			.toEqual(resolveVisibleWaveformRequest({ ...base, scrollLeft: 24_100 }));
	});

	it('keeps the waveform request stable for zoom changes inside the same detail band', () => {
		const base = {
			clipStartMs: 0,
			clipEndMs: 2_400_000,
			sourceStartMs: 0,
			sourceEndMs: 2_400_000,
			timelineDurationMs: 2_400_000,
			viewportWidth: 2_000,
			devicePixelRatio: 2
		};
		const anchorRatio = (24_000 + base.viewportWidth / 2) / (base.viewportWidth * 230);
		const nextScrollLeft = anchorRatio * base.viewportWidth * 235 - base.viewportWidth / 2;

		expect(resolveVisibleWaveformRequest({ ...base, zoom: 230, scrollLeft: 24_000 }))
			.toEqual(resolveVisibleWaveformRequest({ ...base, zoom: 235, scrollLeft: nextScrollLeft }));
	});

	it('selects a stable peak bucket from the rendered clip width instead of global zoom', () => {
		expect(waveformBinsForClipPixels(12, 2)).toBe(64);
		expect(waveformBinsForClipPixels(100, 2)).toBe(512);
		expect(waveformBinsForClipPixels(4_000, 2)).toBe(19_200);
	});

	it('keeps peak requests inside the backend contract', () => {
		expect(waveformBinsForClipPixels(0, 1)).toBe(32);
		expect(waveformBinsForClipPixels(1_000_000, 4)).toBe(180_000);
	});

	it('keeps a reusable low-resolution waveform while high zoom refines in the background', () => {
		expect(OVERVIEW_WAVEFORM_BINS).toBe(4_800);
		expect(waveformPreviewBins(76_800)).toBe(4_800);
		expect(waveformPreviewBins(9_600)).toBe(4_800);
		expect(waveformPreviewBins(512)).toBe(128);
		expect(waveformPreviewBins(128)).toBe(128);
	});

	it('scales monotonically across the complete zoom range without endpoint-specific branches', () => {
		let previousBins = 0;
		for (let zoom = 1; zoom <= 1_200; zoom = Math.min(1_200, zoom * 1.35)) {
			const detailBins = waveformBinsForClipPixels(498 * zoom, 2);
			expect(detailBins).toBeGreaterThanOrEqual(previousBins);
			expect(waveformPreviewBins(detailBins)).toBeLessThanOrEqual(detailBins);
			previousBins = detailBins;
			if (zoom === 1_200) break;
		}
		expect(previousBins).toBe(180_000);
	});

	it('moves a dense timeline from overview to detail only once as zoom increases', () => {
		const levels = [1, 1.4, 1.9, 2.6, 3.5, 4.7, 6.3, 8.5, 11.5, 21, 120, 1_200]
			.map((zoom) => resolveTimelineRenderLevel({
				durationMs: 665_088,
				zoom,
				viewportWidth: 498,
				itemCount: 834
			}));
		const firstDetail = levels.indexOf('detail');
		expect(firstDetail).toBeGreaterThan(0);
		expect(levels.slice(0, firstDetail)).toEqual(
			new Array(firstDetail).fill('overview')
		);
		expect(levels.slice(firstDetail)).toEqual(
			new Array(levels.length - firstDetail).fill('detail')
		);
	});
});
