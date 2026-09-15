export function canRetainClipWaveformDetail({
	resourceIdentity, nextResourceIdentity, resolution, hasPeaks,
	windowStartMs, windowEndMs, visibleStartMs, visibleEndMs
}: {
	resourceIdentity: string;
	nextResourceIdentity: string;
	resolution: 'none' | 'preview' | 'detail';
	hasPeaks: boolean;
	windowStartMs: number;
	windowEndMs: number;
	visibleStartMs: number;
	visibleEndMs: number;
}) {
	return Boolean(resourceIdentity)
		&& resourceIdentity === nextResourceIdentity
		&& resolution !== 'none'
		&& hasPeaks
		&& [windowStartMs, windowEndMs, visibleStartMs, visibleEndMs].every(Number.isFinite)
		&& visibleEndMs > visibleStartMs
		&& windowEndMs > windowStartMs
		&& windowStartMs < visibleEndMs
		&& windowEndMs > visibleStartMs;
}

export function resolveClipWaveformSourceWindow({
	audioDurationMs,
	sourceStartMs,
	sourceEndMs,
	clipStartMs,
	clipEndMs
}: {
	audioDurationMs: number;
	sourceStartMs: number;
	sourceEndMs: number | null;
	clipStartMs: number;
	clipEndMs: number | null;
}) {
	const duration = Math.max(1, finiteOr(audioDurationMs, 1));
	const sourceStart = clamp(finiteOr(sourceStartMs, 0), 0, duration);
	const clipDuration = clipEndMs === null
		? 0
		: Math.max(0, finiteOr(clipEndMs, clipStartMs) - finiteOr(clipStartMs, 0));
	const fallbackEnd = clipDuration > 0 ? sourceStart + clipDuration : duration;
	const sourceEnd = clamp(sourceEndMs === null ? fallbackEnd : finiteOr(sourceEndMs, fallbackEnd), sourceStart, duration);
	return { sourceStartMs: sourceStart, sourceEndMs: sourceEnd };
}

function finiteOr(value: number, fallback: number) {
	return Number.isFinite(value) ? value : fallback;
}

function clamp(value: number, min: number, max: number) {
	return Math.max(min, Math.min(max, value));
}
