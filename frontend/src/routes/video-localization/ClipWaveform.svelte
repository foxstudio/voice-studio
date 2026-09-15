<script lang="ts">
	import {
		cachedWaveform,
		cachedCoveringWaveform,
		requestWaveform,
		waveformResourceIdentity,
		type WaveformPeaksResponse
	} from '$lib/audio/waveform-peaks-client';
	import { untrack } from 'svelte';
	import { applyWaveformGain, projectedPeakState } from './audio-gain';
	import { canRetainClipWaveformDetail, resolveClipWaveformSourceWindow } from './clip-waveform-geometry';

	type PeakLevel = {
		values: Float32Array;
		msPerPeak: number;
	};

	let {
		waveformSrc,
		previewWaveformSrc = '',
		sourceStartMs = 0,
		sourceEndMs = null,
		tone = 'dub',
		gain = 1,
		timelineZoom = 1,
		timelineScrollLeft = 0,
		timelineViewportWidth = 0,
		timelineDurationMs = 0,
		clipStartMs = 0,
		clipEndMs = null,
		renderStartMs = null,
		renderEndMs = null,
		onAnalysis = undefined
	}: {
		waveformSrc: string;
		previewWaveformSrc?: string;
		sourceStartMs?: number;
		sourceEndMs?: number | null;
		tone?: 'source' | 'vocals' | 'music' | 'dub';
		gain?: number;
		timelineZoom?: number;
		timelineScrollLeft?: number;
		timelineViewportWidth?: number;
		timelineDurationMs?: number;
		clipStartMs?: number;
		clipEndMs?: number | null;
		renderStartMs?: number | null;
		renderEndMs?: number | null;
		onAnalysis?: (bars: number[], durationSeconds: number) => void;
	} = $props();

	let waveformEl: HTMLDivElement;
	let canvasEl: HTMLCanvasElement;
	let waveformWidth = $state(0);
	let waveformHeight = $state(0);
	let canvasLeft = $state(0);
	let canvasWidth = $state(0);
	let dataRevision = $state(0);
	let waveformLoading = $state(false);
	let waveformRefining = $state(false);
	let waveformError = $state(false);
	let waveformResolution = $state<'none' | 'preview' | 'detail'>('none');
	let loadSeq = 0;
	let audioDurationSeconds = 0;
	let waveformWindowStartMs = 0;
	let waveformWindowEndMs = 0;
	let peakLevels: PeakLevel[] = [];
	let activeResourceIdentity = '';

	const MAX_ANALYSIS_BARS = 2_048;
	const DETAIL_REFINEMENT_DELAY_MS = 240;

	$effect(() => {
		// The immutable media/window URL is this component's resource identity.
		// Stable keyed clip components therefore keep their peaks through unrelated
		// timeline edits; only a changed clip or viewport tile starts a new request.
		const url = waveformSrc;
		const previewUrl = previewWaveformSrc;
		const seq = ++loadSeq;
		const controller = new AbortController();
		void untrack(() => loadClipWaveform(url, previewUrl, seq, controller.signal));
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
		renderStartMs;
		renderEndMs;
		sourceStartMs;
		sourceEndMs;
		tone;
		gain;
		// Effects run after DOM updates and before paint. Deferring again to rAF
		// leaves a remounted, cached clip with a zero-width canvas for one frame.
		untrack(drawWaveform);
	});

	async function loadClipWaveform(
		url: string,
		previewUrl: string,
		seq: number,
		signal: AbortSignal
	) {
		const nextResourceIdentity = waveformResourceIdentity(url || previewUrl);
		const visible = visibleGeometry();
		let retainExisting = canRetainClipWaveformDetail({
			resourceIdentity: activeResourceIdentity,
			nextResourceIdentity,
			resolution: waveformResolution,
			hasPeaks: peakLevels.length > 0,
			windowStartMs: waveformWindowStartMs,
			windowEndMs: waveformWindowEndMs,
			visibleStartMs: visible.sourceStartMs,
			visibleEndMs: visible.sourceEndMs
		});
		activeResourceIdentity = nextResourceIdentity;
		const cached = cachedWaveform(url);
		if (cached) {
			applyWaveformPayload(cached, seq, signal, 'detail');
			return;
		}
		const covering = cachedCoveringWaveform(url);
		if (covering) {
			applyWaveformPayload(covering, seq, signal, 'detail');
			return;
		}
		const fallbackUrl = previewUrl && previewUrl !== url ? previewUrl : '';
		const preview = fallbackUrl ? cachedWaveform(fallbackUrl) : null;
		if (preview && !retainExisting) applyWaveformPayload(preview, seq, signal, 'preview');
		else if (!retainExisting) clearWaveformData();
		waveformError = false;
		waveformLoading = Boolean(url) && !preview && !retainExisting;
		waveformRefining = Boolean(url) && Boolean(preview || retainExisting);
		if (!url) {
			onAnalysis?.([], 0);
			waveformLoading = false;
			waveformRefining = false;
			return;
		}

		let previewAvailable = Boolean(preview || retainExisting);
		if (!previewAvailable && fallbackUrl) {
			try {
				const payload = await requestWaveform(fallbackUrl, signal);
				if (seq !== loadSeq || signal.aborted) return;
				applyWaveformPayload(payload, seq, signal, 'preview');
				previewAvailable = true;
				waveformRefining = true;
			} catch (error) {
				if (seq !== loadSeq || signal.aborted) return;
				console.warn('Timeline clip preview waveform failed', { url: fallbackUrl, error });
			}
		}

		if (previewAvailable && fallbackUrl) {
			const ready = await waitForRefinement(signal);
			if (!ready || seq !== loadSeq) return;
		}

		try {
			const payload = await requestWaveform(url, signal);
			if (seq !== loadSeq || signal.aborted) return;
			applyWaveformPayload(payload, seq, signal, 'detail');
		} catch (error) {
			if (seq !== loadSeq || signal.aborted) return;
			console.warn('Timeline clip waveform failed', { url, error });
			if (!previewAvailable) {
				clearWaveformData();
				waveformError = true;
				onAnalysis?.([], 0);
			}
			waveformLoading = false;
			waveformRefining = false;
		}
	}

	function waitForRefinement(signal: AbortSignal) {
		if (signal.aborted) return Promise.resolve(false);
		return new Promise<boolean>((resolve) => {
			const timer = setTimeout(() => {
				signal.removeEventListener('abort', abortListener);
				resolve(true);
			}, DETAIL_REFINEMENT_DELAY_MS);
			const abortListener = () => {
				clearTimeout(timer);
				resolve(false);
			};
			signal.addEventListener('abort', abortListener, { once: true });
		});
	}

	function applyWaveformPayload(
		payload: WaveformPeaksResponse,
		seq: number,
		signal: AbortSignal,
		resolution: 'preview' | 'detail'
	) {
		if (seq !== loadSeq || signal.aborted) return;
		const duration = Number(payload.duration);
		if (!Number.isFinite(duration) || duration <= 0 || !Array.isArray(payload.peaks) || !payload.peaks.length) {
			throw new Error('Invalid waveform payload');
		}
		const durationMs = duration * 1000;
		const windowStartMs = clamp(Number(payload.window_start_ms ?? 0), 0, durationMs);
		const windowEndMs = clamp(Number(payload.window_end_ms ?? durationMs), windowStartMs, durationMs);
		if (windowEndMs <= windowStartMs) throw new Error('Invalid waveform window');
		const base = Float32Array.from(payload.peaks, (value) => clamp(Number(value), 0, 1));
		peakLevels = buildPeakPyramid(base, windowEndMs - windowStartMs);
		audioDurationSeconds = duration;
		waveformWindowStartMs = windowStartMs;
		waveformWindowEndMs = windowEndMs;
		dataRevision += 1;
		onAnalysis?.(buildAnalysisBars(base), duration);
		waveformError = false;
		waveformLoading = false;
		waveformRefining = resolution === 'preview';
		waveformResolution = resolution;
	}

	function buildPeakPyramid(base: Float32Array, durationMs: number) {
		const levels: PeakLevel[] = [{ values: base, msPerPeak: durationMs / base.length }];
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
		waveformWindowStartMs = 0;
		waveformWindowEndMs = 0;
		peakLevels = [];
		waveformResolution = 'none';
		dataRevision += 1;
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
		const waveformStyle = getComputedStyle(waveformEl);
		const normalColor = waveformStyle.getPropertyValue('--waveform-color').trim() || 'rgba(174, 218, 255, 0.78)';
		const riskColor = waveformStyle.getPropertyValue('--waveform-risk-color').trim() || 'rgba(242, 165, 66, 0.96)';
		const clippingColor = waveformStyle.getPropertyValue('--waveform-clipping-color').trim() || 'rgba(255, 108, 66, 1)';

		const columnCount = Math.max(1, Math.ceil(geometry.width));
		let activeColor = '';
		for (let column = 0; column < columnCount; column += 1) {
			const startMs = geometry.sourceStartMs + column * geometry.sourceMsPerPixel;
			const endMs = Math.min(geometry.sourceEndMs, startMs + geometry.sourceMsPerPixel);
			const peak = peakBetween(startMs, endMs);
			const projectedPeak = applyWaveformGain(peak, gain);
			const peakState = projectedPeakState(peak, gain);
			const color = peakState === 'clipping'
				? clippingColor
				: peakState === 'risk'
					? riskColor
					: normalColor;
			if (color !== activeColor) {
				context.fillStyle = color;
				activeColor = color;
			}
			const level = Math.pow(Math.min(1, projectedPeak), 0.72);
			const height = Math.max(2, level * waveformHeight * 0.76);
			context.fillRect(column + 0.1, (waveformHeight - height) / 2, 0.8, height);
		}
	}

	function visibleGeometry() {
		const audioDurationMs = Math.max(1, audioDurationSeconds * 1000);
		const endMs = clipEndMs ?? timelineDurationMs;
		const sourceWindow = resolveClipWaveformSourceWindow({
			audioDurationMs,
			sourceStartMs,
			sourceEndMs,
			clipStartMs,
			clipEndMs: endMs
		});
		const sourceStart = sourceWindow.sourceStartMs;
		const sourceEnd = sourceWindow.sourceEndMs;
		const hasTimelineGeometry = timelineDurationMs > 0 && timelineViewportWidth > 0 && endMs > clipStartMs;
		if (!hasTimelineGeometry) {
			return { left: 0, width: waveformWidth, sourceStartMs: sourceStart, sourceEndMs: sourceEnd, sourceMsPerPixel: (sourceEnd - sourceStart) / Math.max(1, waveformWidth) };
		}

		const renderStart = clamp(renderStartMs ?? clipStartMs, clipStartMs, endMs);
		const renderEnd = clamp(renderEndMs ?? endMs, renderStart, endMs);
		const timelinePixelWidth = Math.max(timelineViewportWidth, timelineViewportWidth * Math.max(1, timelineZoom));
		const clipLeftPx = (clipStartMs / timelineDurationMs) * timelinePixelWidth;
		const clipRightPx = (endMs / timelineDurationMs) * timelinePixelWidth;
		const renderLeftPx = (renderStart / timelineDurationMs) * timelinePixelWidth;
		const renderRightPx = (renderEnd / timelineDurationMs) * timelinePixelWidth;
		const visibleLeftPx = Math.max(renderLeftPx, timelineScrollLeft);
		const visibleRightPx = Math.min(renderRightPx, timelineScrollLeft + timelineViewportWidth);
		if (visibleRightPx <= visibleLeftPx || clipRightPx <= clipLeftPx || renderRightPx <= renderLeftPx) {
			return { left: 0, width: 0, sourceStartMs: sourceStart, sourceEndMs: sourceStart, sourceMsPerPixel: 1 };
		}

		const clipPixelWidth = clipRightPx - clipLeftPx;
		const renderPixelWidth = renderRightPx - renderLeftPx;
		const sourceMsPerPixel = (sourceEnd - sourceStart) / clipPixelWidth;
		const startRatio = clamp((visibleLeftPx - clipLeftPx) / clipPixelWidth, 0, 1);
		const endRatio = clamp((visibleRightPx - clipLeftPx) / clipPixelWidth, startRatio, 1);
		const renderVisibleStartRatio = clamp((visibleLeftPx - renderLeftPx) / renderPixelWidth, 0, 1);
		const renderVisibleEndRatio = clamp((visibleRightPx - renderLeftPx) / renderPixelWidth, renderVisibleStartRatio, 1);
		return {
			left: renderVisibleStartRatio * waveformWidth,
			width: Math.max(0, (renderVisibleEndRatio - renderVisibleStartRatio) * waveformWidth),
			sourceStartMs: sourceStart + startRatio * (sourceEnd - sourceStart),
			sourceEndMs: sourceStart + endRatio * (sourceEnd - sourceStart),
			sourceMsPerPixel
		};
	}

	function peakBetween(startMs: number, endMs: number) {
		if (!peakLevels.length) return 0;
		if (endMs <= waveformWindowStartMs || startMs >= waveformWindowEndMs) return 0;
		const localStartMs = Math.max(waveformWindowStartMs, startMs) - waveformWindowStartMs;
		const localEndMs = Math.min(waveformWindowEndMs, endMs) - waveformWindowStartMs;
		const spanMs = Math.max(0.001, localEndMs - localStartMs);

		let level = peakLevels[0];
		for (const candidate of peakLevels) {
			if (candidate.msPerPeak > spanMs) break;
			level = candidate;
		}
		const from = clamp(Math.floor(localStartMs / level.msPerPeak), 0, level.values.length - 1);
		const to = clamp(Math.ceil(localEndMs / level.msPerPeak), from + 1, level.values.length);
		let peak = 0;
		for (let index = from; index < to; index += 1) peak = Math.max(peak, level.values[index] ?? 0);
		return peak;
	}

	function clamp(value: number, min: number, max: number) {
		return Math.max(min, Math.min(max, Number.isFinite(value) ? value : min));
	}

</script>

<div
	class="clip-waveform tone-{tone}"
	class:loading={waveformLoading}
	class:refining={waveformRefining}
	class:error={waveformError}
	data-waveform-resolution={waveformResolution}
	data-waveform-gain={gain}
	bind:this={waveformEl}
	aria-hidden="true"
>
	<canvas bind:this={canvasEl} style={`left:${canvasLeft}px;width:${canvasWidth}px`} class:hidden={canvasWidth <= 0}></canvas>
</div>

<style>
	.clip-waveform {
		--waveform-risk-color: rgba(242, 165, 66, 0.96);
		--waveform-clipping-color: rgba(255, 108, 66, 1);
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
