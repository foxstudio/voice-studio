export type TimelineWheelIntent = 'trackpad-zoom' | 'wheel-zoom' | 'pan' | 'native-scroll';

export type TimelineWheelInput = {
	ctrlKey: boolean;
	metaKey: boolean;
	shiftKey: boolean;
	deltaX: number;
	deltaY: number;
	deltaMode: number;
	wheelDeltaY?: number;
};

export function timelineWheelIntent(input: TimelineWheelInput): TimelineWheelIntent {
	if (input.ctrlKey || input.metaKey) return 'trackpad-zoom';
	if (input.shiftKey || Math.abs(input.deltaX) > Math.abs(input.deltaY)) return 'pan';
	return discreteMouseWheel(input) ? 'wheel-zoom' : 'native-scroll';
}

// Chromium reports native Mac trackpad pinch with a modifier flag, while
// Command + vertical scrolling arrives through the same wheel-event path.
// Keep that shared path gentler than physical mouse-wheel notches.
const TRACKPAD_PINCH_SENSITIVITY = 0.01;
const MAX_TRACKPAD_PINCH_DELTA = 24;

export function trackpadPinchZoomScale(deltaY: number) {
	if (!Number.isFinite(deltaY)) return 1;
	const boundedDelta = Math.max(-MAX_TRACKPAD_PINCH_DELTA, Math.min(MAX_TRACKPAD_PINCH_DELTA, deltaY));
	return Math.exp(-boundedDelta * TRACKPAD_PINCH_SENSITIVITY);
}

function discreteMouseWheel(input: TimelineWheelInput) {
	if (input.deltaMode !== 0) return true;
	const legacyDelta = Math.abs(input.wheelDeltaY ?? 0);
	if (legacyDelta >= 120 && Math.abs(legacyDelta % 120) < 0.01) return true;
	return false;
}
