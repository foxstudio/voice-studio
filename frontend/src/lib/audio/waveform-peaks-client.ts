export type WaveformPeaksResponse = {
	peaks: number[];
	duration: number;
	bins: number;
	window_start_ms?: number | null;
	window_end_ms?: number | null;
};

const waveformPayloadCache = new Map<string, WaveformPeaksResponse>();
const waveformPayloadRequests = new Map<string, SharedWaveformRequest>();
const waveformRequestQueue: QueuedWaveformRequest[] = [];

const MAX_CACHED_WAVEFORMS = 256;
const MAX_CACHED_WAVEFORM_POINTS = 1_500_000;
const MAX_CONCURRENT_WAVEFORM_REQUESTS = 6;
const ORPHAN_REQUEST_ABORT_DELAY_MS = 300;

let cachedWaveformPoints = 0;
let activeWaveformRequests = 0;

type QueuedWaveformRequest = {
	url: string;
	signal: AbortSignal;
	started: boolean;
	resolve: (payload: WaveformPeaksResponse) => void;
	reject: (error: unknown) => void;
	abortListener: () => void;
};

type SharedWaveformRequest = {
	controller: AbortController;
	promise: Promise<WaveformPeaksResponse>;
	subscribers: Set<symbol>;
	settled: boolean;
	abortTimer: ReturnType<typeof setTimeout> | null;
};

export function waveformUrlWithBins(baseUrl: string, bins: number) {
	const separator = baseUrl.includes('?') ? '&' : '?';
	return `${baseUrl}${separator}bins=${Math.max(32, Math.round(bins))}`;
}

/**
 * The waveform window and bin count are render requests, not media identity.
 * Keeping this boundary explicit lets a mounted clip retain its last good
 * peaks while the same immutable audio is refined for another viewport tile.
 */
export function waveformResourceIdentity(url: string) {
	if (!url) return '';
	const [path, query = ''] = url.split('?', 2);
	const parameters = new URLSearchParams(query);
	parameters.delete('bins');
	parameters.delete('start_ms');
	parameters.delete('end_ms');
	const stableQuery = parameters.toString();
	return stableQuery ? `${path}?${stableQuery}` : path;
}

export function cachedWaveform(url: string) {
	const payload = waveformPayloadCache.get(url);
	if (!payload) return null;
	waveformPayloadCache.delete(url);
	waveformPayloadCache.set(url, payload);
	return payload;
}

export function cachedCoveringWaveform(url: string) {
	const parameters = new URLSearchParams(url.split('?', 2)[1] || '');
	if (!parameters.has('start_ms') || !parameters.has('end_ms') || !parameters.has('bins')) return null;
	const startMs = Number(parameters.get('start_ms'));
	const endMs = Number(parameters.get('end_ms'));
	const bins = Number(parameters.get('bins'));
	if (![startMs, endMs, bins].every(Number.isFinite) || endMs <= startMs || bins <= 0) return null;
	const identity = waveformResourceIdentity(url);
	let bestKey = '';
	let bestMsPerPeak = (endMs - startMs) / bins;
	for (const [key, payload] of waveformPayloadCache) {
		if (waveformResourceIdentity(key) !== identity || !payload.peaks.length) continue;
		const start = payload.window_start_ms ?? 0;
		const end = payload.window_end_ms ?? payload.duration * 1000;
		const msPerPeak = (end - start) / payload.peaks.length;
		if (start <= startMs && end >= endMs && msPerPeak > 0 && msPerPeak <= bestMsPerPeak) {
			bestKey = key;
			bestMsPerPeak = msPerPeak;
		}
	}
	return bestKey ? cachedWaveform(bestKey) : null;
}

function rememberWaveform(url: string, payload: WaveformPeaksResponse) {
	const previous = waveformPayloadCache.get(url);
	if (previous) cachedWaveformPoints -= previous.peaks.length;
	waveformPayloadCache.delete(url);
	waveformPayloadCache.set(url, payload);
	cachedWaveformPoints += payload.peaks.length;
	while (
		waveformPayloadCache.size > MAX_CACHED_WAVEFORMS
		|| cachedWaveformPoints > MAX_CACHED_WAVEFORM_POINTS
	) {
		const oldest = waveformPayloadCache.keys().next().value;
		if (typeof oldest !== 'string') break;
		cachedWaveformPoints -= waveformPayloadCache.get(oldest)?.peaks.length ?? 0;
		waveformPayloadCache.delete(oldest);
	}
}

export function requestWaveform(url: string, signal: AbortSignal) {
	if (signal.aborted) return Promise.reject(abortError());
	const cached = cachedWaveform(url);
	if (cached) return Promise.resolve(cached);
	let shared = waveformPayloadRequests.get(url);
	if (shared && (shared.controller.signal.aborted || shared.settled)) {
		if (waveformPayloadRequests.get(url) === shared) waveformPayloadRequests.delete(url);
		shared = undefined;
	}
	if (!shared) {
		const controller = new AbortController();
		shared = {
			controller,
			promise: Promise.resolve({ peaks: [], duration: 0, bins: 0 }),
			subscribers: new Set(),
			settled: false,
			abortTimer: null
		};
		const request = queueWaveformRequest(url, controller.signal)
			.finally(() => {
				shared!.settled = true;
				if (shared!.abortTimer) clearTimeout(shared!.abortTimer);
				shared!.abortTimer = null;
				if (waveformPayloadRequests.get(url) === shared) waveformPayloadRequests.delete(url);
			});
		shared.promise = request;
		waveformPayloadRequests.set(url, shared);
	}

	const subscriber = Symbol(url);
	if (shared.abortTimer) clearTimeout(shared.abortTimer);
	shared.abortTimer = null;
	shared.subscribers.add(subscriber);
	return new Promise<WaveformPeaksResponse>((resolve, reject) => {
		let finished = false;
		const release = () => {
			shared!.subscribers.delete(subscriber);
			if (
				!shared!.settled
				&& shared!.subscribers.size === 0
				&& !shared!.abortTimer
			) {
				shared!.abortTimer = setTimeout(() => {
					shared!.abortTimer = null;
					if (!shared!.settled && shared!.subscribers.size === 0) shared!.controller.abort();
				}, ORPHAN_REQUEST_ABORT_DELAY_MS);
			}
		};
		const abortListener = () => {
			if (finished) return;
			finished = true;
			release();
			reject(abortError());
		};
		signal.addEventListener('abort', abortListener, { once: true });
		void shared!.promise.then(
			(payload) => {
				if (finished) return;
				finished = true;
				signal.removeEventListener('abort', abortListener);
				release();
				resolve(payload);
			},
			(error) => {
				if (finished) return;
				finished = true;
				signal.removeEventListener('abort', abortListener);
				release();
				reject(error);
			}
		);
	});
}

function queueWaveformRequest(url: string, signal: AbortSignal) {
	if (signal.aborted) return Promise.reject(abortError());
	let task: QueuedWaveformRequest;
	const request = new Promise<WaveformPeaksResponse>((resolve, reject) => {
		const abortListener = () => {
			if (task.started) return;
			const index = waveformRequestQueue.indexOf(task);
			if (index >= 0) waveformRequestQueue.splice(index, 1);
			reject(abortError());
			drainWaveformRequestQueue();
		};
		task = { url, signal, started: false, resolve, reject, abortListener };
		signal.addEventListener('abort', abortListener, { once: true });
		waveformRequestQueue.push(task);
		drainWaveformRequestQueue();
	});
	return request.finally(() => signal.removeEventListener('abort', task.abortListener));
}

function drainWaveformRequestQueue() {
	while (activeWaveformRequests < MAX_CONCURRENT_WAVEFORM_REQUESTS && waveformRequestQueue.length) {
		const task = waveformRequestQueue.shift();
		if (!task) return;
		if (task.signal.aborted) {
			task.reject(abortError());
			continue;
		}
		task.started = true;
		activeWaveformRequests += 1;
		void fetchWaveform(task.url, task.signal)
			.then(task.resolve, task.reject)
			.finally(() => {
				activeWaveformRequests -= 1;
				drainWaveformRequestQueue();
			});
	}
}

async function fetchWaveform(url: string, signal: AbortSignal) {
	let lastError: unknown = null;
	for (let attempt = 0; attempt < 3; attempt += 1) {
		try {
			const response = await fetch(url, { cache: 'default', signal });
			if (!response.ok) {
				const error = new Error(`HTTP ${response.status}`);
				if (response.status < 500) throw Object.assign(error, { retryable: false });
				throw error;
			}
			const payload = await response.json() as WaveformPeaksResponse;
			if (
				!Array.isArray(payload.peaks)
				|| !payload.peaks.length
				|| !Number.isFinite(payload.duration)
				|| payload.duration <= 0
			) {
				throw Object.assign(new Error('Invalid waveform payload'), { retryable: false });
			}
			rememberWaveform(url, payload);
			return payload;
		} catch (error) {
			if (signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) throw abortError();
			lastError = error;
			if ((error as { retryable?: boolean })?.retryable === false || attempt >= 2) break;
			await new Promise((resolve) => setTimeout(resolve, 220 * (attempt + 1)));
		}
	}
	throw lastError instanceof Error ? lastError : new Error('Waveform request failed');
}

function abortError() {
	return new DOMException('Waveform request aborted', 'AbortError');
}
