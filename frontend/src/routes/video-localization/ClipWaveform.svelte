<script module lang="ts">
	type CachedWaveformResponse = {
		peaks: number[];
		duration: number;
		bins: number;
	};

	const waveformPayloadCache = new Map<string, CachedWaveformResponse>();
	const MAX_CACHED_WAVEFORMS = 128;

	function cachedWaveform(url: string) {
		const payload = waveformPayloadCache.get(url);
		if (!payload) return null;
		waveformPayloadCache.delete(url);
		waveformPayloadCache.set(url, payload);
		return payload;
	}

	function rememberWaveform(url: string, payload: CachedWaveformResponse) {
		waveformPayloadCache.delete(url);
		waveformPayloadCache.set(url, payload);
		while (waveformPayloadCache.size > MAX_CACHED_WAVEFORMS) {
			const oldest = waveformPayloadCache.keys().next().value;
			if (typeof oldest !== 'string') break;
			waveformPayloadCache.delete(oldest);
		}
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';

	type PeakLevel = {
		values: Float32Array;
		msPerPeak: number;
	};

	type WaveformResponse = {
		peaks: number[];
		duration: number;
		bins: number;
	};

	let {
		waveformSrc,
		sourceStartMs = 0,
		sourceEndMs = null,
		tone = 'dub',
		timelineZoom = 1,
		timelineScrollLeft = 0,
		timelineViewportWidth = 0,
		timelineDurationMs = 0,
		clipStartMs = 0,
		clipEndMs = null,
		onAnalysis = undefined,
		onLoadError = undefined,
		onLoadSuccess = undefined
	}: {
		waveformSrc: string;
		sourceStartMs?: number;
		sourceEndMs?: number | null;
		tone?: 'source' | 'vocals' | 'music' | 'dub';
		timelineZoom?: number;
		timelineScrollLeft?: number;
		timelineViewportWidth?: number;
		timelineDurationMs?: number;
		clipStartMs?: number;
		clipEndMs?: number | null;
		onAnalysis?: (bars: number[], durationSeconds: number) => void;
		onLoadError?: () => void;
		onLoadSuccess?: () => void;
	} = $props();

	let waveformEl: HTMLDivElement;
	let canvasEl: HTMLCanvasElement;
	let waveformWidth = $state(0);
	let waveformHeight = $state(0);
	let canvasLeft = $state(0);
	let canvasWidth = $state(0);
	let dataRevision = $state(0);
	let waveformLoading = $state(false);
	let waveformError = $state(false);
	let loadSeq = 0;
	let drawFrame = 0;
	let audioDurationSeconds = 0;
	let peakLevels: PeakLevel[] = [];

	const MAX_ANALYSIS_BARS = 180_000;

	$effect(() => {
		const url = waveformSrc;
		const seq = ++loadSeq;
		const controller = new AbortController();
		void untrack(() => loadClipWaveform(url, seq, controller.signal));
		return () => controller.abort();
	});

	$effect(() => {
		if (!waveformEl) return;
		const updateSize = () => {
			waveformWidth = waveformEl.clientWidth;
			waveformHeight = waveformEl.clientHeight;
		};
		updateSize();
		const observer = new ResizeObserver(updateSize);
		observer.observe(waveformEl);
		return () => observer.disconnect();
	});

	$effect(() => {
		dataRevision;
		waveformWidth;
		waveformHeight;
		timelineZoom;
		timelineScrollLeft;
		timelineViewportWidth;
		timelineDurationMs;
		clipStartMs;
		clipEndMs;
		sourceStartMs;
		sourceEndMs;
		tone;
		scheduleDraw();
		return () => cancelAnimationFrame(drawFrame);
	});

	async function loadClipWaveform(url: string, seq: number, signal: AbortSignal) {
		const cached = cachedWaveform(url);
		if (cached) {
			applyWaveformPayload(cached, seq, signal);
			return;
		}
		clearWaveformData();
		waveformError = false;
		waveformLoading = Boolean(url);
		if (!url) {
			onAnalysis?.([], 0);
			waveformLoading = false;
			return;
		}

		let lastError: unknown = null;
		for (let attempt = 0; attempt < 3; attempt += 1) {
			try {
				const response = await fetch(url, { signal });
				if (!response.ok) throw new Error(`HTTP ${response.status}`);
				const payload = (await response.json()) as WaveformResponse;
				if (seq !== loadSeq || signal.aborted) return;
				rememberWaveform(url, payload);
				applyWaveformPayload(payload, seq, signal);
				return;
			} catch (error) {
				if (seq !== loadSeq || signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) return;
				lastError = error;
				if (attempt < 2) await waitForRetry(220 * (attempt + 1), signal);
			}
		}

		if (seq !== loadSeq || signal.aborted) return;
		console.warn('Timeline clip waveform failed', { url, error: lastError });
		clearWaveformData();
		waveformError = true;
		waveformLoading = false;
		onAnalysis?.([], 0);
		onLoadError?.();
	}

	function applyWaveformPayload(payload: WaveformResponse, seq: number, signal: AbortSignal) {
		if (seq !== loadSeq || signal.aborted) return;
		const duration = Number(payload.duration);
		if (!Number.isFinite(duration) || duration <= 0 || !Array.isArray(payload.peaks) || !payload.peaks.length) {
			throw new Error('Invalid waveform payload');
		}
		const base = Float32Array.from(payload.peaks, (value) => clamp(Number(value), 0, 1));
		peakLevels = buildPeakPyramid(base, duration);
		audioDurationSeconds = duration;
		dataRevision += 1;
		onAnalysis?.(buildAnalysisBars(base), duration);
		onLoadSuccess?.();
		waveformError = false;
		waveformLoading = false;
	}

	function waitForRetry(delayMs: number, signal: AbortSignal) {
		return new Promise<void>((resolve) => {
			const timer = window.setTimeout(resolve, delayMs);
			signal.addEventListener('abort', () => {
				window.clearTimeout(timer);
				resolve();
			}, { once: true });
		});
	}

	function buildPeakPyramid(base: Float32Array, durationSeconds: number) {
		const levels: PeakLevel[] = [{ values: base, msPerPeak: (durationSeconds * 1000) / base.length }];
		while (levels[levels.length - 1].values.length > 1) {
			const previous = levels[levels.length - 1];
			const values = new Float32Array(Math.ceil(previous.values.length / 2));
			for (let index = 0; index < values.length; index += 1) {
				values[index] = Math.max(previous.values[index * 2] ?? 0, previous.values[index * 2 + 1] ?? 0);
			}
			levels.push({ values, msPerPeak: previous.msPerPeak * 2 });
		}
		return levels;
	}

	function buildAnalysisBars(base: Float32Array) {
		if (base.length <= MAX_ANALYSIS_BARS) return Array.from(base);
		const result = new Array<number>(MAX_ANALYSIS_BARS).fill(0);
		for (let index = 0; index < MAX_ANALYSIS_BARS; index += 1) {
			const start = Math.floor((index / MAX_ANALYSIS_BARS) * base.length);
			const end = Math.max(start + 1, Math.ceil(((index + 1) / MAX_ANALYSIS_BARS) * base.length));
			let peak = 0;
			for (let cursor = start; cursor < end; cursor += 1) peak = Math.max(peak, base[cursor] ?? 0);
			result[index] = peak;
		}
		return result;
	}

	function clearWaveformData() {
		audioDurationSeconds = 0;
		peakLevels = [];
		dataRevision += 1;
	}

	function scheduleDraw() {
		cancelAnimationFrame(drawFrame);
		drawFrame = requestAnimationFrame(drawWaveform);
	}

	function drawWaveform() {
		if (!canvasEl || !waveformEl || audioDurationSeconds <= 0 || !peakLevels.length || waveformWidth <= 0 || waveformHeight <= 0) {
			canvasLeft = 0;
			canvasWidth = 0;
			return;
		}

		const geometry = visibleGeometry();
		canvasLeft = geometry.left;
		canvasWidth = geometry.width;
		if (geometry.width <= 0) return;

		const pixelRatio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
		const bitmapWidth = Math.max(1, Math.ceil(geometry.width * pixelRatio));
		const bitmapHeight = Math.max(1, Math.ceil(waveformHeight * pixelRatio));
		if (canvasEl.width !== bitmapWidth) canvasEl.width = bitmapWidth;
		if (canvasEl.height !== bitmapHeight) canvasEl.height = bitmapHeight;

		const context = canvasEl.getContext('2d');
		if (!context) return;
		context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
		context.clearRect(0, 0, geometry.width, waveformHeight);
		context.fillStyle = getComputedStyle(waveformEl).getPropertyValue('--waveform-color').trim() || 'rgba(174, 218, 255, 0.78)';

		const columnCount = Math.max(1, Math.ceil(geometry.width));
		const sourceSpanMs = geometry.sourceEndMs - geometry.sourceStartMs;
		for (let column = 0; column < columnCount; column += 1) {
			const startMs = geometry.sourceStartMs + (column / columnCount) * sourceSpanMs;
			const endMs = geometry.sourceStartMs + ((column + 1) / columnCount) * sourceSpanMs;
			const peak = peakBetween(startMs, endMs);
			const level = Math.pow(Math.min(1, peak), 0.72);
			const height = Math.max(2, level * waveformHeight * 0.76);
			context.fillRect(column + 0.1, (waveformHeight - height) / 2, 0.8, height);
		}
	}

	function visibleGeometry() {
		const audioDurationMs = Math.max(1, audioDurationSeconds * 1000);
		const sourceStart = clamp(sourceStartMs, 0, audioDurationMs);
		const sourceEnd = clamp(sourceEndMs ?? audioDurationMs, sourceStart, audioDurationMs);
		const endMs = clipEndMs ?? timelineDurationMs;
		const hasTimelineGeometry = timelineDurationMs > 0 && timelineViewportWidth > 0 && endMs > clipStartMs;
		if (!hasTimelineGeometry) {
			return { left: 0, width: waveformWidth, sourceStartMs: sourceStart, sourceEndMs: sourceEnd };
		}

		const timelinePixelWidth = Math.max(timelineViewportWidth, timelineViewportWidth * Math.max(1, timelineZoom));
		const clipLeftPx = (clipStartMs / timelineDurationMs) * timelinePixelWidth;
		const clipRightPx = (endMs / timelineDurationMs) * timelinePixelWidth;
		const visibleLeftPx = Math.max(clipLeftPx, timelineScrollLeft);
		const visibleRightPx = Math.min(clipRightPx, timelineScrollLeft + timelineViewportWidth);
		if (visibleRightPx <= visibleLeftPx || clipRightPx <= clipLeftPx) {
			return { left: 0, width: 0, sourceStartMs: sourceStart, sourceEndMs: sourceStart };
		}

		const clipPixelWidth = clipRightPx - clipLeftPx;
		const startRatio = clamp((visibleLeftPx - clipLeftPx) / clipPixelWidth, 0, 1);
		const endRatio = clamp((visibleRightPx - clipLeftPx) / clipPixelWidth, startRatio, 1);
		return {
			left: startRatio * waveformWidth,
			width: Math.max(0, (endRatio - startRatio) * waveformWidth),
			sourceStartMs: sourceStart + startRatio * (sourceEnd - sourceStart),
			sourceEndMs: sourceStart + endRatio * (sourceEnd - sourceStart)
		};
	}

	function peakBetween(startMs: number, endMs: number) {
		if (!peakLevels.length) return 0;
		const spanMs = Math.max(0.001, endMs - startMs);

		let level = peakLevels[0];
		for (const candidate of peakLevels) {
			if (candidate.msPerPeak > spanMs) break;
			level = candidate;
		}
		const from = clamp(Math.floor(startMs / level.msPerPeak), 0, level.values.length - 1);
		const to = clamp(Math.ceil(endMs / level.msPerPeak), from + 1, level.values.length);
		let peak = 0;
		for (let index = from; index < to; index += 1) peak = Math.max(peak, level.values[index] ?? 0);
		return peak;
	}

	function clamp(value: number, min: number, max: number) {
		return Math.max(min, Math.min(max, Number.isFinite(value) ? value : min));
	}

</script>

<div class="clip-waveform tone-{tone}" class:loading={waveformLoading} class:error={waveformError} bind:this={waveformEl} aria-hidden="true">
	<canvas bind:this={canvasEl} style={`left:${canvasLeft}px;width:${canvasWidth}px`} class:hidden={canvasWidth <= 0}></canvas>
</div>

<style>
	.clip-waveform {
		position: absolute;
		inset: 4px 0;
		overflow: hidden;
		opacity: 0.68;
		pointer-events: none;
	}

	.clip-waveform.loading {
		background: repeating-linear-gradient(90deg, transparent 0 10px, color-mix(in srgb, var(--waveform-color) 20%, transparent) 10px 11px, transparent 11px 17px);
		background-size: 34px 100%;
		animation: waveform-loading 0.8s linear infinite;
	}

	.clip-waveform.error {
		background: repeating-linear-gradient(-45deg, transparent 0 7px, rgba(255, 120, 120, 0.18) 7px 8px);
	}

	@keyframes waveform-loading {
		to { background-position: 34px 0; }
	}

	.clip-waveform canvas {
		position: absolute;
		top: 0;
		height: 100%;
	}

	.clip-waveform canvas.hidden {
		display: none;
	}

	.tone-source { --waveform-color: rgba(105, 228, 218, 0.84); }
	.tone-vocals { --waveform-color: rgba(139, 174, 255, 0.88); }
	.tone-music { --waveform-color: rgba(231, 193, 103, 0.86); }
	.tone-dub { --waveform-color: rgba(190, 177, 255, 0.86); }
</style>
