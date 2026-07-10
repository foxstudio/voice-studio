<script lang="ts">
	import { analyzeWaveformFromUrl, buildVisibleWaveformBars } from '$lib/audio/waveform';

	let {
		audioSrc,
		tone = 'source',
		timelineZoom = 1,
		scrollLeft = 0,
		viewportWidth = 0,
		gain = 1,
		onAnalysis = undefined
	}: {
		audioSrc: string;
		tone?: 'source' | 'vocals' | 'music';
		timelineZoom?: number;
		scrollLeft?: number;
		viewportWidth?: number;
		gain?: number;
		onAnalysis?: (bars: number[]) => void;
	} = $props();

	let waveformBars: number[] = $state([]);
	let waveformLoading = $state(false);
	let waveformProgress = $state(0);
	let waveformError = $state(false);
	let loadSeq = 0;
	const CLIPPING_THRESHOLD = Math.pow(10, -1 / 20);

	const visibleWaveformBars = $derived(buildVisibleWaveformBars(waveformBars, timelineZoom, scrollLeft, viewportWidth));
	const waveformGain = $derived(Math.max(0, Math.min(2, Number.isFinite(gain) ? gain : 1)));

	$effect(() => {
		void loadWaveform(audioSrc);
	});

	async function loadWaveform(nextAudioSrc: string) {
		const seq = ++loadSeq;
		waveformBars = [];
		waveformProgress = 0;
		waveformError = false;
		waveformLoading = Boolean(nextAudioSrc);
		if (!nextAudioSrc) {
			waveformLoading = false;
			return;
		}
		try {
			const analysis = await analyzeWaveformFromUrl(nextAudioSrc, (bars, progress) => {
				if (seq !== loadSeq) return;
				waveformBars = bars;
				waveformProgress = progress;
				onAnalysis?.(bars);
			});
			if (seq !== loadSeq) return;
			waveformBars = analysis.bars;
			waveformProgress = 1;
			onAnalysis?.(analysis.bars);
		} catch {
			if (seq !== loadSeq) return;
			waveformError = true;
			waveformBars = [];
			waveformProgress = 0;
			onAnalysis?.([]);
		} finally {
			if (seq === loadSeq) waveformLoading = false;
		}
	}
</script>

<div class={`track-waveform ${tone}`} class:loading={waveformLoading} class:error={waveformError} style={`--waveform-progress:${Math.round(waveformProgress * 100)}%`} aria-hidden="true">
	{#if waveformBars.length}
		<svg class="track-waveform-svg" viewBox={`0 0 ${waveformBars.length} 100`} preserveAspectRatio="none">
			<line class="waveform-midline" x1="0" y1="50" x2={waveformBars.length} y2="50" />
			{#each visibleWaveformBars as bar}
				{@const scaledPeak = Math.min(1.12, bar.level * waveformGain)}
				{@const visibleLevel = Math.pow(Math.min(1, scaledPeak), 0.72)}
				<rect
					x={bar.x + 0.08}
					y={50 - visibleLevel * 43}
					width={bar.width}
					height={Math.max(6, visibleLevel * 86)}
					rx="0.2"
				/>
				{#if scaledPeak > CLIPPING_THRESHOLD}
					<rect
						class="clip-cap"
						x={bar.x + 0.08}
						y={50 - visibleLevel * 43}
						width={bar.width}
						height={Math.max(3.4, (scaledPeak - CLIPPING_THRESHOLD) * 43)}
						rx="0.2"
					/>
				{/if}
			{/each}
		</svg>
	{:else}
		<span class="waveform-empty"></span>
	{/if}
</div>

<style>
	.track-waveform {
		position: absolute;
		inset: 7px 0;
		opacity: 0.94;
		mask-image: linear-gradient(90deg, transparent 0, #000 2%, #000 98%, transparent 100%);
		--waveform-fill: rgba(88, 209, 200, 0.9);
		--waveform-mid: rgba(88, 209, 200, 0.22);
	}

	.track-waveform.vocals {
		--waveform-fill: rgba(125, 164, 255, 0.92);
		--waveform-mid: rgba(125, 164, 255, 0.24);
	}

	.track-waveform.music {
		--waveform-fill: rgba(217, 180, 95, 0.88);
		--waveform-mid: rgba(217, 180, 95, 0.22);
	}

	.track-waveform.loading::after {
		content: "";
		position: absolute;
		inset: 0;
		background:
			linear-gradient(90deg, rgba(13, 18, 24, 0.25) var(--waveform-progress), rgba(13, 18, 24, 0.68) var(--waveform-progress)),
			repeating-linear-gradient(90deg, transparent 0 9px, var(--waveform-mid) 9px 10px, transparent 10px 16px);
		background-position: 0 0, var(--waveform-progress) 0;
		animation: waveform-scan 1s linear infinite;
	}

	@keyframes waveform-scan {
		to { background-position: 0 0, calc(var(--waveform-progress) + 28px) 0; }
	}

	.track-waveform-svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
	}

	.track-waveform-svg rect {
		fill: var(--waveform-fill);
	}

	.track-waveform-svg rect.clip-cap {
		fill: #ff9b45;
		filter: drop-shadow(0 0 2px rgba(255, 155, 69, 0.42));
	}

	.track-waveform-svg .waveform-midline {
		stroke: var(--waveform-mid);
		stroke-width: 1;
	}

	.waveform-empty {
		position: absolute;
		inset: 14px 4%;
		border: 1px dashed rgba(255, 255, 255, 0.14);
		border-radius: 6px;
	}

	.track-waveform.error .waveform-empty {
		border-color: rgba(255, 128, 128, 0.35);
	}
</style>
