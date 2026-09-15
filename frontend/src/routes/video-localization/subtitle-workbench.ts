export function snapToFrame(valueMs: number, frameRate = 24) {
	const safeRate = Number.isFinite(frameRate) && frameRate > 0 ? frameRate : 24;
	return Math.round((Math.max(0, valueMs) / 1000) * safeRate) / safeRate * 1000;
}

export function formatTimecode(valueMs: number | null | undefined, frameRate = 24) {
	if (valueMs === null || valueMs === undefined || !Number.isFinite(valueMs)) return '--:--:--:--';
	const safeRate = Number.isFinite(frameRate) && frameRate > 0 ? frameRate : 24;
	const totalFrames = Math.max(0, Math.round((valueMs / 1000) * safeRate));
	const frames = totalFrames % Math.round(safeRate);
	const totalSeconds = Math.floor(totalFrames / safeRate);
	const seconds = totalSeconds % 60;
	const totalMinutes = Math.floor(totalSeconds / 60);
	const minutes = totalMinutes % 60;
	const hours = Math.floor(totalMinutes / 60);
	return [hours, minutes, seconds, frames].map((part) => String(part).padStart(2, '0')).join(':');
}

export function waveformBarCount(width: number) {
	return Math.max(1, Math.min(320, Math.floor((Number.isFinite(width) ? Math.max(0, width) : 0) / 3)));
}

export function waveformBars(peaks: number[], count = 72) {
	if (!peaks.length) return [];
	const size = Math.min(peaks.length, Math.max(1, Math.floor(Number.isFinite(count) ? count : 1)));
	const bars = Array.from({ length: size }, (_, index) => {
		const start = Math.floor((index / size) * peaks.length);
		const end = Math.max(start + 1, Math.floor(((index + 1) / size) * peaks.length));
		const slice = peaks.slice(start, end);
		return slice.reduce((max, peak) => Math.max(max, Number.isFinite(peak) ? Math.abs(peak) : 0), 0);
	});
	// History previews show the clip's shape, not an absolute loudness meter.
	const maximum = Math.max(...bars);
	return maximum > 0 ? bars.map((peak) => peak / maximum) : bars;
}
