import { afterEach, describe, expect, it, vi } from 'vitest';
import { probeDirectVideoSource } from './video-source-capability';

class FakeVideo {
	muted = false;
	preload = '';
	src = '';
	videoWidth = 1920;
	listeners = new Map<string, () => void>();
	loads = 0;
	pauses = 0;
	plays = 0;

	addEventListener(type: string, listener: () => void) {
		this.listeners.set(type, listener);
	}

	removeEventListener(type: string) {
		this.listeners.delete(type);
	}

	removeAttribute(name: string) {
		if (name === 'src') this.src = '';
	}

	load() {
		this.loads += 1;
	}

	play() {
		this.plays += 1;
		return Promise.reject(new Error('autoplay blocked'));
	}

	pause() {
		this.pauses += 1;
	}

	emit(type: string) {
		this.listeners.get(type)?.();
	}
}

describe('direct video source capability probe', () => {
	afterEach(() => vi.useRealTimers());

	it('accepts a decoded frame without confusing autoplay policy with codec support', async () => {
		const video = new FakeVideo();
		const result = probeDirectVideoSource('/source.webm', {
			createVideo: () => video
		});
		video.emit('loadeddata');

		await expect(result).resolves.toBe(true);
		expect(video.preload).toBe('auto');
		expect(video.src).toBe('');
		expect(video.loads).toBe(2);
		expect(video.pauses).toBe(1);
		expect(video.plays).toBe(0);
	});

	it('does not accept audio-only readiness without a decoded video frame', async () => {
		vi.useFakeTimers();
		const video = new FakeVideo();
		video.videoWidth = 0;
		const result = probeDirectVideoSource('/unsupported-video.mkv', {
			timeoutMs: 250,
			createVideo: () => video
		});
		video.emit('loadeddata');
		await vi.advanceTimersByTimeAsync(250);

		await expect(result).resolves.toBe(false);
	});

	it('falls back on decoder errors or a bounded timeout', async () => {
		const failedVideo = new FakeVideo();
		const failed = probeDirectVideoSource('/source.mov', {
			createVideo: () => failedVideo
		});
		failedVideo.emit('error');
		await expect(failed).resolves.toBe(false);

		vi.useFakeTimers();
		const timedVideo = new FakeVideo();
		const timed = probeDirectVideoSource('/slow-source.mkv', {
			timeoutMs: 250,
			createVideo: () => timedVideo
		});
		await vi.advanceTimersByTimeAsync(250);
		await expect(timed).resolves.toBe(false);
	});
});
