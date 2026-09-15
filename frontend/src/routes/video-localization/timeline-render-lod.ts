import {
	OVERVIEW_WAVEFORM_BINS,
	waveformBinsForPixels,
	waveformPreviewBins
} from '$lib/audio/waveform-lod';

export { OVERVIEW_WAVEFORM_BINS, waveformPreviewBins };

export type TimelineRenderLevel = 'overview' | 'detail';

const MIN_DENSE_TIMELINE_ITEMS = 120;
const DETAIL_PIXELS_PER_SECOND = 6;

export function timelinePixelsPerSecond(durationMs: number, zoom: number, viewportWidth: number) {
	const durationSeconds = Math.max(0.001, finiteOr(durationMs, 0) / 1000);
	const contentWidth = Math.max(1, finiteOr(viewportWidth, 0)) * Math.max(1, finiteOr(zoom, 1));
	return contentWidth / durationSeconds;
}

export function resolveTimelineRenderLevel({
	durationMs,
	zoom,
	viewportWidth,
	itemCount
}: {
	durationMs: number;
	zoom: number;
	viewportWidth: number;
	itemCount: number;
}): TimelineRenderLevel {
	if (Math.max(0, finiteOr(itemCount, 0)) < MIN_DENSE_TIMELINE_ITEMS) return 'detail';
	return timelinePixelsPerSecond(durationMs, zoom, viewportWidth) < DETAIL_PIXELS_PER_SECOND
		? 'overview'
		: 'detail';
}

export function waveformBinsForClipPixels(pixelWidth: number, devicePixelRatio = 1) {
	return waveformBinsForPixels(pixelWidth, devicePixelRatio);
}

export function resolveVirtualizedClipRenderWindow({
	clipStartMs,
	clipEndMs,
	viewportStartMs,
	viewportEndMs
}: {
	clipStartMs: number;
	clipEndMs: number;
	viewportStartMs: number;
	viewportEndMs: number;
}) {
	const clipStart = Math.max(0, finiteOr(clipStartMs, 0));
	const clipEnd = Math.max(clipStart, finiteOr(clipEndMs, clipStart));
	const viewportStart = Math.max(0, finiteOr(viewportStartMs, 0));
	const viewportEnd = Math.max(viewportStart, finiteOr(viewportEndMs, viewportStart));
	const overscanMs = Math.max(1, (viewportEnd - viewportStart) / 2);
	const startMs = Math.max(clipStart, viewportStart - overscanMs);
	const endMs = Math.min(clipEnd, viewportEnd + overscanMs);
	return {
		startMs,
		endMs: Math.max(startMs, endMs),
		showStartEdge: startMs <= clipStart,
		showEndEdge: endMs >= clipEnd
	};
}

export function resolveVisibleWaveformRequest({
	clipStartMs,
	clipEndMs,
	sourceStartMs,
	sourceEndMs,
	timelineDurationMs,
	zoom,
	scrollLeft,
	viewportWidth,
	devicePixelRatio = 1
}: {
	clipStartMs: number;
	clipEndMs: number;
	sourceStartMs: number;
	sourceEndMs: number | null;
	timelineDurationMs: number;
	zoom: number;
	scrollLeft: number;
	viewportWidth: number;
	devicePixelRatio?: number;
}) {
	const timelineDuration = Math.max(1, finiteOr(timelineDurationMs, 1));
	const clipStart = Math.max(0, finiteOr(clipStartMs, 0));
	const clipEnd = Math.max(clipStart, finiteOr(clipEndMs, clipStart));
	const clipDuration = clipEnd - clipStart;
	if (clipDuration <= 0 || viewportWidth <= 0) return null;

	const sourceStart = Math.max(0, finiteOr(sourceStartMs, 0));
	const sourceEnd = Math.max(
		sourceStart,
		sourceEndMs === null ? sourceStart + clipDuration : finiteOr(sourceEndMs, sourceStart + clipDuration)
	);
	const sourceDuration = sourceEnd - sourceStart;
	if (sourceDuration <= 0) return null;

	const contentWidth = Math.max(1, finiteOr(viewportWidth, 1)) * Math.max(1, finiteOr(zoom, 1));
	const viewportStartMs = (Math.max(0, finiteOr(scrollLeft, 0)) / contentWidth) * timelineDuration;
	const viewportEndMs = ((Math.max(0, finiteOr(scrollLeft, 0)) + viewportWidth) / contentWidth) * timelineDuration;
	const visibleStartMs = Math.max(clipStart, viewportStartMs);
	const visibleEndMs = Math.min(clipEnd, viewportEndMs);
	if (visibleEndMs <= visibleStartMs) return null;

	const visibleSourceStart = sourceStart + ((visibleStartMs - clipStart) / clipDuration) * sourceDuration;
	const visibleSourceEnd = sourceStart + ((visibleEndMs - clipStart) / clipDuration) * sourceDuration;
	const tileSpan = quantizedWaveformTileSpan(visibleSourceEnd - visibleSourceStart);
	const firstVisibleTile = Math.floor((visibleSourceStart - sourceStart) / tileSpan);
	const lastVisibleTile = Math.floor(
		Math.max(0, visibleSourceEnd - sourceStart - 1) / tileSpan
	);
	const requestStart = Math.max(
		sourceStart,
		sourceStart + Math.max(0, firstVisibleTile - 1) * tileSpan
	);
	const requestEnd = Math.min(
		sourceEnd,
		sourceStart + (lastVisibleTile + 2) * tileSpan
	);
	const startMs = Math.floor(requestStart);
	const endMs = Math.max(startMs + 1, Math.ceil(requestEnd));
	const clipPixelWidth = (clipDuration / timelineDuration) * contentWidth;
	const requestPixelWidth = ((endMs - startMs) / sourceDuration) * clipPixelWidth;
	return {
		startMs,
		endMs,
		bins: waveformBinsForPixels(requestPixelWidth, devicePixelRatio)
	};
}

function quantizedWaveformTileSpan(visibleSpanMs: number) {
	const minimumSpanMs = 250;
	const safeSpan = Math.max(minimumSpanMs, finiteOr(visibleSpanMs, minimumSpanMs));
	const exponent = Math.round(Math.log2(safeSpan / minimumSpanMs));
	return minimumSpanMs * 2 ** Math.max(0, exponent);
}

function finiteOr(value: number, fallback: number) {
	return Number.isFinite(value) ? value : fallback;
}
