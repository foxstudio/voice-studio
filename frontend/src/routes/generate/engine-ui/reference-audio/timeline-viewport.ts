export function maximumReferenceTimelineZoom(durationSeconds: number) {
	return Math.max(1, Math.min(1200, durationSeconds / 0.5));
}

function formatTimelineTick(valueSeconds: number) {
	const safe = Math.max(0, Math.round(valueSeconds * 100) / 100);
	if (safe < 60) return `${Number(safe.toFixed(2))}s`;
	const minutes = Math.floor(safe / 60);
	const seconds = Number((safe % 60).toFixed(2)).toString().padStart(2, '0');
	return `${minutes}:${seconds}`;
}
export function buildReferenceTimelineTicks(duration: number, zoom: number, viewportWidth = 900) {
	if (!duration) return [];
	const target = Math.max(8, Math.min(36000, Math.round(Math.max(1, viewportWidth) * Math.max(1, zoom) / 24)));
	const rawStep = duration / target;
	const frameStep = 1 / 30;
	const steps = [frameStep, frameStep * 2, frameStep * 5, frameStep * 10, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
	const step = steps.find((value) => value >= rawStep) ?? steps[steps.length - 1];
	const pixelsPerTick = step / duration * Math.max(1, viewportWidth) * Math.max(1, zoom);
	const labelEvery = Math.max(1, Math.ceil(70 / pixelsPerTick));
	const ticks: Array<{ time: number; percent: number; label: string; major: boolean }> = [];
	for (let time = 0; time <= duration + 0.001; time += step) {
		const index = Math.round(time / step);
		ticks.push({ time, percent: (time / duration) * 100, label: index % labelEvery === 0 ? formatTimelineTick(time) : '', major: index % labelEvery === 0 });
	}
	if (Math.abs((ticks[ticks.length - 1]?.time ?? -1) - duration) > 0.001) ticks.push({ time: duration, percent: 100, label: formatTimelineTick(duration), major: true });
	return ticks;
}
