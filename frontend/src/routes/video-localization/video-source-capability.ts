type ProbeVideo = {
	muted: boolean;
	preload: string;
	src: string;
	videoWidth: number;
	addEventListener: (type: string, listener: () => void, options?: AddEventListenerOptions) => void;
	removeEventListener: (type: string, listener: () => void) => void;
	removeAttribute: (name: string) => void;
	load: () => void;
	pause: () => void;
};

type ProbeOptions = {
	timeoutMs?: number;
	createVideo?: () => ProbeVideo;
};

/** Ask the actual browser decoder to produce a source frame before choosing a proxy. */
export function probeDirectVideoSource(
	url: string,
	options: ProbeOptions = {}
): Promise<boolean> {
	if (!url || (!options.createVideo && typeof document === 'undefined')) {
		return Promise.resolve(false);
	}
	const video = options.createVideo?.() ?? document.createElement('video');
	const timeoutMs = Math.max(250, options.timeoutMs ?? 4_000);
	video.muted = true;
	video.preload = 'auto';
	return new Promise((resolve) => {
		let settled = false;
		const finish = (playable: boolean) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			video.removeEventListener('loadeddata', handleLoadedData);
			video.removeEventListener('error', handleError);
			video.pause();
			video.removeAttribute('src');
			video.load();
			resolve(playable);
		};
		const handleLoadedData = () => {
			if (video.videoWidth <= 0) {
				finish(false);
				return;
			}
			// `loadeddata` means the browser has decoded the current video frame.
			// Do not call play() here: autoplay policy is unrelated to codec support
			// and would incorrectly route a playable source into the proxy fallback.
			finish(true);
		};
		const handleError = () => finish(false);
		const timer = setTimeout(() => finish(false), timeoutMs);
		video.addEventListener('loadeddata', handleLoadedData, { once: true });
		video.addEventListener('error', handleError, { once: true });
		video.src = url;
		video.load();
	});
}
