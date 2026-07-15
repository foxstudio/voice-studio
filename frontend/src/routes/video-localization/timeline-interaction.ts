export type TimelinePointerIntent = 'ignore' | 'pan' | 'seek' | 'range-create';

export function timelinePointerIntent({
	button,
	overTimeline,
	overTrack,
	interactive
}: {
	button: number;
	overTimeline: boolean;
	overTrack: boolean;
	interactive: boolean;
}): TimelinePointerIntent {
	if (button === 1) return overTimeline ? 'pan' : 'ignore';
	if (button !== 0 || interactive) return 'ignore';
	if (overTrack) return 'range-create';
	return 'seek';
}
