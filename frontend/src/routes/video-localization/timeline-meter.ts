export function waveformMeterLevelAt(
	bars: readonly number[],
	positionMs: number,
	durationMs: number
) {
	if (!bars.length || !Number.isFinite(durationMs) || durationMs <= 0) return 0;
	const normalizedPosition = Math.max(0, Math.min(durationMs, positionMs));
	const exactIndex = Math.max(
		0,
		Math.min(bars.length - 1, (normalizedPosition / durationMs) * (bars.length - 1))
	);
	const lowerIndex = Math.floor(exactIndex);
	const upperIndex = Math.min(bars.length - 1, lowerIndex + 1);
	const lowerLevel = Number.isFinite(bars[lowerIndex]) ? bars[lowerIndex] : 0;
	const upperLevel = Number.isFinite(bars[upperIndex]) ? bars[upperIndex] : 0;
	return lowerLevel + (upperLevel - lowerLevel) * (exactIndex - lowerIndex);
}
