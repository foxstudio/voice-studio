export type SubtitleWaveformPayload = {
	peaks: number[];
	duration: number;
};

type WaveformLoader = () => Promise<SubtitleWaveformPayload>;

export class SubtitleWaveformCache {
	readonly maxEntries: number;
	private readonly entries = new Map<string, SubtitleWaveformPayload>();
	private readonly inFlight = new Map<string, Promise<SubtitleWaveformPayload>>();

	constructor(maxEntries = 96) {
		this.maxEntries = Math.max(1, Math.floor(maxEntries));
	}

	get size() {
		return this.entries.size;
	}

	get pendingCount() {
		return this.inFlight.size;
	}

	get(key: string) {
		const payload = this.entries.get(key);
		if (!payload) return null;
		this.entries.delete(key);
		this.entries.set(key, payload);
		return payload;
	}

	load(key: string, loader: WaveformLoader) {
		const cached = this.get(key);
		if (cached) return Promise.resolve(cached);

		const pending = this.inFlight.get(key);
		if (pending) return pending;

		const request = Promise.resolve()
			.then(loader)
			.then((payload) => {
				this.set(key, payload);
				return payload;
			})
			.finally(() => {
				if (this.inFlight.get(key) === request) this.inFlight.delete(key);
			});
		this.inFlight.set(key, request);
		return request;
	}

	private set(key: string, payload: SubtitleWaveformPayload) {
		this.entries.delete(key);
		this.entries.set(key, payload);
		while (this.entries.size > this.maxEntries) {
			const oldest = this.entries.keys().next().value;
			if (typeof oldest !== 'string') break;
			this.entries.delete(oldest);
		}
	}
}

const sharedWaveformCache = new SubtitleWaveformCache();

export function getCachedSubtitleWaveform(url: string) {
	return sharedWaveformCache.get(url);
}

export function loadCachedSubtitleWaveform(url: string, fetcher: typeof fetch = fetch) {
	return sharedWaveformCache.load(url, async () => {
		const response = await fetcher(url);
		if (!response.ok) throw new Error('waveform unavailable');
		const payload = (await response.json()) as { peaks?: unknown; duration?: unknown };
		return {
			peaks: Array.isArray(payload.peaks)
				? payload.peaks.map((peak) => Number(peak)).filter(Number.isFinite)
				: [],
			duration: Number.isFinite(Number(payload.duration)) ? Math.max(0, Number(payload.duration)) : 0
		};
	});
}
