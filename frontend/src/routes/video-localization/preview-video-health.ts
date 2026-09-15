export const VIDEO_FRAME_PRESENTATION_TIMEOUT_MS = 1_800;
export const VIDEO_FRAME_MIN_ADVANCE_SECONDS = 0.3;
export const VIDEO_BLACK_SURFACE_TIMEOUT_MS = 3_200;
export const VIDEO_BLACK_SURFACE_MIN_ADVANCE_SECONDS = 0.8;
export const VIDEO_HAVE_FUTURE_DATA = 3;

export type VideoFrameHealthSample = {
	nowMs: number;
	lastPresentedAtMs: number;
	mediaTimeSeconds: number;
	lastPresentedMediaTimeSeconds: number;
	playing: boolean;
	seeking: boolean;
	readyState: number;
	firstFramePending?: boolean;
	timeoutMs?: number;
	minAdvanceSeconds?: number;
};

/**
 * Detects the browser-media failure where the playback clock keeps moving but
 * the video element stops presenting decoded frames. It deliberately ignores
 * paused, seeking, and buffering states so ordinary media loading cannot
 * trigger a decoder reconnect.
 */
export function videoFramePresentationStalled(sample: VideoFrameHealthSample) {
	if (!sample.playing || sample.seeking || sample.readyState < VIDEO_HAVE_FUTURE_DATA) return false;
	if (sample.nowMs - sample.lastPresentedAtMs < (sample.timeoutMs ?? VIDEO_FRAME_PRESENTATION_TIMEOUT_MS)) return false;
	if (sample.firstFramePending) return true;
	return Math.abs(sample.mediaTimeSeconds - sample.lastPresentedMediaTimeSeconds) >= (sample.minAdvanceSeconds ?? VIDEO_FRAME_MIN_ADVANCE_SECONDS);
}

export function videoFramePixelsVisible(pixels: Uint8ClampedArray, channelThreshold = 8) {
	for (let index = 0; index + 3 < pixels.length; index += 4) {
		if (
			pixels[index] > channelThreshold
			|| pixels[index + 1] > channelThreshold
			|| pixels[index + 2] > channelThreshold
		) return true;
	}
	return false;
}
