/** CSS content width and time geometry share these pixels; percentage widths amplify fractional viewport rounding. */
export function timelineContentPixelWidth(viewportWidth: number, zoom: number): number {
	return Math.max(0, viewportWidth) * Math.max(1, zoom);
}

/** Preserve the left-edge time when the timeline viewport is resized. */
export function resizedTimelineScrollLeft(
	previousLeft: number,
	previousWidth: number,
	nextWidth: number,
	contentWidth: number
): number {
	const left = previousWidth > 0 ? previousLeft * nextWidth / previousWidth : previousLeft;
	return Math.max(0, Math.min(left, contentWidth - nextWidth));
}
