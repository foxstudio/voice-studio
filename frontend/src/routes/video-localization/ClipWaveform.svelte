<script lang="ts">
	import { analyzeWaveformFromUrl } from '$lib/audio/waveform';

	let {
		audioSrc,
		sourceStartMs = 0,
		sourceEndMs = null,
		onAnalysis = undefined
	}: {
		audioSrc: string;
		sourceStartMs?: number;
		sourceEndMs?: number | null;
		onAnalysis?: (bars: number[], durationSeconds: number) => void;
	} = $props();

	let bars = $state<number[]>([]);
	let durationSeconds = $state(0);
	let loadSeq = 0;

	const visibleBars = $derived(buildClipBars(bars, durationSeconds, sourceStartMs, sourceEndMs));

	$effect(() => {
		void loadClipWaveform(audioSrc);
	});

	async function loadClipWaveform(url: string) {
		const seq = ++loadSeq;
		bars = [];
		durationSeconds = 0;
		if (!url) return;
		try {
			const analysis = await analyzeWaveformFromUrl(url, undefined, 900);
			if (seq !== loadSeq) return;
			bars = analysis.bars;
			durationSeconds = analysis.durationSeconds;
			onAnalysis?.(analysis.bars, analysis.durationSeconds);
		} catch {
			if (seq !== loadSeq) return;
			bars = [];
			durationSeconds = 0;
			onAnalysis?.([], 0);
		}
	}

	function buildClipBars(values: number[], duration: number, startMs: number, endMs: number | null) {
		if (!values.length || duration <= 0) return [];
		const durationMs = duration * 1000;
		const start = Math.max(0, Math.min(durationMs, startMs));
		const end = Math.max(start + 1, Math.min(durationMs, endMs ?? durationMs));
		const from = Math.floor((start / durationMs) * values.length);
		const to = Math.max(from + 1, Math.ceil((end / durationMs) * values.length));
		const clipped = values.slice(from, to);
		const target = Math.min(280, clipped.length);
		const bucket = Math.max(1, Math.ceil(clipped.length / Math.max(1, target)));
		const result: number[] = [];
		for (let index = 0; index < clipped.length; index += bucket) {
			let peak = 0;
			for (let cursor = index; cursor < Math.min(clipped.length, index + bucket); cursor += 1) peak = Math.max(peak, clipped[cursor] ?? 0);
			result.push(peak);
		}
		return result;
	}
</script>

<div class="clip-waveform" aria-hidden="true">
	{#if visibleBars.length}
		<svg viewBox={`0 0 ${visibleBars.length} 100`} preserveAspectRatio="none">
			{#each visibleBars as peak, index}
				{@const level = Math.pow(Math.min(1, peak), 0.72)}
				<rect x={index + 0.12} y={50 - level * 38} width="0.76" height={Math.max(4, level * 76)} rx="0.2" />
			{/each}
		</svg>
	{/if}
</div>

<style>
	.clip-waveform {
		position: absolute;
		inset: 4px 7px;
		opacity: 0.68;
		pointer-events: none;
		mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent);
	}

	.clip-waveform svg {
		width: 100%;
		height: 100%;
	}

	.clip-waveform rect {
		fill: rgba(174, 218, 255, 0.78);
	}
</style>
