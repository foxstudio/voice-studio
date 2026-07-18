export type TimelinePointerIntent = 'ignore' | 'pan' | 'seek' | 'marquee-select' | 'range-create';

export function isRepeatedPrimaryPress({
	detail,
	elapsedMs,
	distancePx,
	maximumDelayMs = 360,
	maximumDistancePx = 7
}: {
	detail: number;
	elapsedMs: number | null;
	distancePx: number | null;
	maximumDelayMs?: number;
	maximumDistancePx?: number;
}) {
	if (detail >= 2) return true;
	return elapsedMs !== null && distancePx !== null
		&& elapsedMs >= 0 && elapsedMs <= maximumDelayMs
		&& distancePx <= maximumDistancePx;
}

export function timelinePointerIntent({
	button,
	detail = 1,
	overTimeline,
	overTrack,
	interactive
}: {
	button: number;
	detail?: number;
	overTimeline: boolean;
	overTrack: boolean;
	interactive: boolean;
}): TimelinePointerIntent {
	if (button === 1) return overTimeline ? 'pan' : 'ignore';
	if (button !== 0 || interactive) return 'ignore';
	if (overTrack) return detail >= 2 ? 'range-create' : 'marquee-select';
	return 'seek';
}
