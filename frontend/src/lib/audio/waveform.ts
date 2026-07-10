export type TimelineTick = {
	time: number;
	percent: number;
	label: string;
	major: boolean;
	level: 0 | 1 | 2;
};

export type VisibleWaveformBar = {
	x: number;
	width: number;
	level: number;
};

export type WaveformAnalysis = {
	bars: number[];
	durationSeconds: number;
};

export function formatDuration(valueMs: number | null) {
	if (!valueMs) return '未识别';
	const seconds = Math.max(0, valueMs / 1000);
	if (seconds < 60) {
		const rounded = Math.round(seconds * 10) / 10;
		return Number.isInteger(rounded) ? `${rounded}s` : `${rounded.toFixed(1)}s`;
	}
	const total = Math.round(seconds);
	const m = Math.floor(total / 60);
	const s = total % 60;
	return `${m}:${String(s).padStart(2, '0')}`;
}

export function formatTimecode(valueSeconds: number, fps = 30) {
	const safeSeconds = Math.max(0, Number.isFinite(valueSeconds) ? valueSeconds : 0);
	const totalFrames = Math.round(safeSeconds * fps);
	const frames = totalFrames % fps;
	const totalWholeSeconds = Math.floor(totalFrames / fps);
	const s = totalWholeSeconds % 60;
	const m = Math.floor(totalWholeSeconds / 60) % 60;
	const h = Math.floor(totalWholeSeconds / 3600);
	return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}:${String(frames).padStart(2, '0')}`;
}

export function formatTimelineZoom(value: number) {
	return value < 10 ? value.toFixed(1) : value.toFixed(0);
}

export function buildTimelineTicks(durationSeconds: number, zoom: number) {
	if (!durationSeconds) return [] as TimelineTick[];
	const normalizedZoom = Math.max(1, zoom);
	const target = Math.max(8, Math.min(36000, Math.round(28 * normalizedZoom)));
	const rawStep = durationSeconds / target;
	const frameStep = 1 / 30;
	const steps = [frameStep, frameStep * 2, frameStep * 5, frameStep * 10, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
	const step = steps.find((value) => value >= rawStep) ?? steps[steps.length - 1];
	const ticks: TimelineTick[] = [];
	const labelEvery = step < 1 ? Math.max(1, Math.round(1 / step)) : step < 5 ? 2 : 1;

	for (let time = 0; time <= durationSeconds + 0.001; time += step) {
		const index = Math.round(time / step);
		const isMajor = index % labelEvery === 0;
		ticks.push({
			time,
			percent: durationSeconds ? (time / durationSeconds) * 100 : 0,
			label: isMajor ? formatTimelineTick(time) : '',
			major: isMajor,
			level: isMajor ? 2 : index % Math.max(1, Math.ceil(labelEvery / 2)) === 0 ? 1 : 0
		});
	}

	return ticks;
}

export function buildVisibleWaveformBars(bars: number[], zoom: number, scrollLeft: number, viewportWidth: number) {
	if (!bars.length) return [] as VisibleWaveformBar[];
	const total = bars.length;
	const safeViewport = Math.max(1, viewportWidth || 900);
	const scrollWidth = Math.max(safeViewport, safeViewport * Math.max(1, zoom));
	const visibleStartRatio = Math.max(0, Math.min(1, scrollLeft / scrollWidth));
	const visibleEndRatio = Math.max(visibleStartRatio, Math.min(1, (scrollLeft + safeViewport) / scrollWidth));
	const padRatio = Math.min(0.02, Math.max(0.001, (visibleEndRatio - visibleStartRatio) * 0.35));
	const start = Math.max(0, Math.floor((visibleStartRatio - padRatio) * total));
	const end = Math.min(total, Math.ceil((visibleEndRatio + padRatio) * total));
	const span = Math.max(1, end - start);
	const targetBars = Math.max(180, Math.min(2600, Math.round(safeViewport * 1.35)));
	const bucket = Math.max(1, Math.ceil(span / targetBars));
	const result: VisibleWaveformBar[] = [];

	for (let index = start; index < end; index += bucket) {
		let peak = 0;
		const stop = Math.min(end, index + bucket);
		for (let cursor = index; cursor < stop; cursor++) peak = Math.max(peak, bars[cursor] ?? 0);
		result.push({ x: index, width: Math.max(1, (stop - index) * 0.82), level: peak });
	}

	return result;
}

export function loadAudioDuration(url: string): Promise<number> {
	return new Promise((resolve, reject) => {
		const audio = new Audio();
		audio.preload = 'metadata';
		audio.onloadedmetadata = () => {
			resolve(Number.isFinite(audio.duration) ? audio.duration : 0);
			audio.src = '';
		};
		audio.onerror = () => reject(new Error('无法读取音频时长'));
		audio.src = url;
	});
}

export async function buildWaveformBarsFromUrl(
	url: string,
	onProgress?: (bars: number[], progress: number) => void,
	count = 2400
) {
	const analysis = await analyzeWaveformFromUrl(url, onProgress, count);
	return analysis.bars;
}

export async function analyzeWaveformFromUrl(
	url: string,
	onProgress?: (bars: number[], progress: number) => void,
	count = 2400
) {
	const response = await fetch(url);
	if (!response.ok) throw new Error(`音频波形加载失败：HTTP ${response.status}`);
	const blob = await response.blob();
	return analyzeWaveformFromBlob(blob, onProgress, count);
}

export async function buildWaveformBarsFromBlob(
	blob: Blob,
	onProgress?: (bars: number[], progress: number) => void,
	count = 2400
) {
	const analysis = await analyzeWaveformFromBlob(blob, onProgress, count);
	return analysis.bars;
}

export async function analyzeWaveformFromBlob(
	blob: Blob,
	onProgress?: (bars: number[], progress: number) => void,
	count = 2400
) {
	const audioContext = new AudioContext();
	try {
		onProgress?.([], 0.04);
		const decoded = await audioContext.decodeAudioData(await blob.arrayBuffer());
		const channel = decoded.getChannelData(0);
		const dynamicCount = Math.max(count, Math.min(180000, Math.ceil(decoded.duration * 60), Math.round(decoded.length / 2048)));
		const bucketSize = Math.max(1, Math.floor(channel.length / dynamicCount));
		const rawBars = new Array<number>(dynamicCount).fill(0);
		const chunkSize = Math.max(360, Math.min(1800, Math.ceil(dynamicCount / 80)));

		for (let index = 0; index < dynamicCount; index++) {
			const start = index * bucketSize;
			const end = Math.min(channel.length, start + bucketSize);
			let peak = 0;
			for (let cursor = start; cursor < end; cursor++) peak = Math.max(peak, Math.abs(channel[cursor] ?? 0));
			rawBars[index] = peak;
			if (index % chunkSize === 0 || index === dynamicCount - 1) {
				const upto = index + 1;
				const peaks = rawBars.slice(0, upto).map((value) => Math.max(0, Math.min(1, value)));
				onProgress?.(peaks, Math.max(0.08, Math.min(0.98, upto / dynamicCount)));
				await nextAnimationFrame();
			}
		}

		const finalBars = rawBars.map((value) => Math.max(0, Math.min(1, value)));
		onProgress?.(finalBars, 1);
		return {
			bars: finalBars,
			durationSeconds: decoded.duration
		} satisfies WaveformAnalysis;
	} finally {
		void audioContext.close();
	}
}

function formatTimelineTick(valueSeconds: number, fps = 30) {
	if (valueSeconds <= 0.0001) return '00:00';
	if (valueSeconds < 1) return `${Math.round(valueSeconds * fps)}f`;
	const safe = Math.max(0, Math.round(valueSeconds * 10) / 10);
	const h = Math.floor(safe / 3600);
	const m = Math.floor((safe % 3600) / 60);
	const s = safe % 60;
	if (h) return `${h}:${String(m).padStart(2, '0')}:${String(Math.floor(s)).padStart(2, '0')}`;
	if (safe < 10 && !Number.isInteger(safe)) return `${safe.toFixed(1)}s`;
	return `${m}:${String(Math.floor(s)).padStart(2, '0')}`;
}

function nextAnimationFrame() {
	return new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
}
