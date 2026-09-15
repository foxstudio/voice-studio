import { describe, expect, it } from 'vitest';
import {
	VIDEO_FRAME_MIN_ADVANCE_SECONDS,
	VIDEO_FRAME_PRESENTATION_TIMEOUT_MS,
	VIDEO_HAVE_FUTURE_DATA,
	videoFramePixelsVisible,
	videoFramePresentationStalled
} from './preview-video-health';

describe('preview video frame health', () => {
	it('detects a silent renderer stall when media time advances without a presented frame', () => {
		expect(videoFramePresentationStalled({
			nowMs: VIDEO_FRAME_PRESENTATION_TIMEOUT_MS + 50,
			lastPresentedAtMs: 0,
			mediaTimeSeconds: VIDEO_FRAME_MIN_ADVANCE_SECONDS + 0.1,
			lastPresentedMediaTimeSeconds: 0,
			playing: true,
			seeking: false,
			readyState: VIDEO_HAVE_FUTURE_DATA
		})).toBe(true);
	});

	it('does not recover ordinary paused, seeking, buffering, or freshly painted video', () => {
		const healthy = {
			nowMs: VIDEO_FRAME_PRESENTATION_TIMEOUT_MS + 50,
			lastPresentedAtMs: 0,
			mediaTimeSeconds: VIDEO_FRAME_MIN_ADVANCE_SECONDS + 0.1,
			lastPresentedMediaTimeSeconds: 0,
			playing: true,
			seeking: false,
			readyState: VIDEO_HAVE_FUTURE_DATA
		};
		expect(videoFramePresentationStalled({ ...healthy, playing: false })).toBe(false);
		expect(videoFramePresentationStalled({ ...healthy, seeking: true })).toBe(false);
		expect(videoFramePresentationStalled({ ...healthy, readyState: VIDEO_HAVE_FUTURE_DATA - 1 })).toBe(false);
		expect(videoFramePresentationStalled({
			...healthy,
			lastPresentedAtMs: healthy.nowMs - 50
		})).toBe(false);
	});

	it('requires meaningful media-clock progress before declaring a black frame', () => {
		expect(videoFramePresentationStalled({
			nowMs: VIDEO_FRAME_PRESENTATION_TIMEOUT_MS + 50,
			lastPresentedAtMs: 0,
			mediaTimeSeconds: VIDEO_FRAME_MIN_ADVANCE_SECONDS - 0.01,
			lastPresentedMediaTimeSeconds: 0,
			playing: true,
			seeking: false,
			readyState: VIDEO_HAVE_FUTURE_DATA
		})).toBe(false);
	});

	it('detects a stalled frame after the editor jumps backward on the media timeline', () => {
		expect(videoFramePresentationStalled({
			nowMs: VIDEO_FRAME_PRESENTATION_TIMEOUT_MS + 50,
			lastPresentedAtMs: 0,
			mediaTimeSeconds: 10,
			lastPresentedMediaTimeSeconds: 120,
			playing: true,
			seeking: false,
			readyState: VIDEO_HAVE_FUTURE_DATA
		})).toBe(true);
	});

	it('detects a play request that never presents its first frame', () => {
		expect(videoFramePresentationStalled({
			nowMs: VIDEO_FRAME_PRESENTATION_TIMEOUT_MS + 50,
			lastPresentedAtMs: 0,
			mediaTimeSeconds: 10,
			lastPresentedMediaTimeSeconds: 10,
			playing: true,
			seeking: false,
			readyState: VIDEO_HAVE_FUTURE_DATA,
			firstFramePending: true
		})).toBe(true);
	});

	it('supports a more conservative timeout for pixel-sampling fallback', () => {
		expect(videoFramePresentationStalled({
			nowMs: VIDEO_FRAME_PRESENTATION_TIMEOUT_MS + 50,
			lastPresentedAtMs: 0,
			mediaTimeSeconds: 1,
			lastPresentedMediaTimeSeconds: 0,
			playing: true,
			seeking: false,
			readyState: VIDEO_HAVE_FUTURE_DATA,
			timeoutMs: VIDEO_FRAME_PRESENTATION_TIMEOUT_MS + 500,
			minAdvanceSeconds: 0.8
		})).toBe(false);
	});

	it('distinguishes an empty black rendering surface from visible pixels', () => {
		expect(videoFramePixelsVisible(new Uint8ClampedArray([
			0, 0, 0, 255,
			2, 2, 2, 255
		]))).toBe(false);
		expect(videoFramePixelsVisible(new Uint8ClampedArray([
			0, 0, 0, 255,
			18, 8, 4, 255
		]))).toBe(true);
	});
});
