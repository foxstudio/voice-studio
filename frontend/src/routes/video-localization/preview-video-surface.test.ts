import { describe, expect, it } from 'vitest';
import {
	PreviewVideoSurfaceResumeController,
	previewVideoSurfaceKey
} from './preview-video-surface';

describe('preview video surface resume controller', () => {
	it('remounts the video element without changing the media URL', () => {
		const source = '/api/projects/project-1/video-localization/source-media/preview-video?revision=video-r1';

		expect(previewVideoSurfaceKey(source, 0)).toBe(`${source}:surface:0`);
		expect(previewVideoSurfaceKey(source, 1)).toBe(`${source}:surface:1`);
	});

	it('reconnects a paused frame after the host window was hidden', () => {
		const controller = new PreviewVideoSurfaceResumeController();
		controller.markSuspended();

		expect(controller.consumeResume({
			visible: true,
			hasVideo: true,
			paused: true,
			ended: false,
			currentTimeSeconds: 12.5
		})).toEqual({ restoreTimeSeconds: 12.5, resumePlayback: false });
	});

	it('preserves active playback and consumes duplicate focus events only once', () => {
		const controller = new PreviewVideoSurfaceResumeController();
		controller.markSuspended();

		expect(controller.consumeResume({
			visible: true,
			hasVideo: true,
			paused: false,
			ended: false,
			currentTimeSeconds: 7
		})).toEqual({ restoreTimeSeconds: 7, resumePlayback: true });
		expect(controller.consumeResume({
			visible: true,
			hasVideo: true,
			paused: false,
			ended: false,
			currentTimeSeconds: 7.1
		})).toBeNull();
	});

	it('waits for a visible host and ignores ended media', () => {
		const controller = new PreviewVideoSurfaceResumeController();
		controller.markSuspended();
		expect(controller.consumeResume({
			visible: false,
			hasVideo: true,
			paused: true,
			ended: false,
			currentTimeSeconds: 3
		})).toBeNull();
		expect(controller.consumeResume({
			visible: true,
			hasVideo: true,
			paused: true,
			ended: true,
			currentTimeSeconds: 3
		})).toBeNull();
	});
});
