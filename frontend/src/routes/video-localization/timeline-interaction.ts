export type TimelinePointerIntent = 'ignore' | 'pan' | 'seek' | 'range-create';
export type TimelinePointerPress = { time: number; x: number; y: number };

export function isRepeatedTimelinePress(
	previous: TimelinePointerPress | null,
	current: TimelinePointerPress,
	maxDelayMs = 500,
	maxDistancePx = 8
) {
	return Boolean(
		previous &&
		current.time - previous.time >= 0 &&
		current.time - previous.time <= maxDelayMs &&
		Math.hypot(current.x - previous.x, current.y - previous.y) <= maxDistancePx
	);
}

export function timelinePointerIntent({
	button,
	clickCount,
	overTimeline,
	overTrack,
	interactive
}: {
	button: number;
	clickCount: number;
	overTimeline: boolean;
	overTrack: boolean;
	interactive: boolean;
}): TimelinePointerIntent {
	if (button === 1) return overTimeline ? 'pan' : 'ignore';
	if (button !== 0 || interactive) return 'ignore';
	if (overTrack && clickCount >= 2) return 'range-create';
	return 'seek';
}
