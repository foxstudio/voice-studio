<script lang="ts">
	import { onDestroy } from 'svelte';
	import { Download, Loader2, Pause, Play } from 'lucide-svelte';
	import { getCachedSubtitleWaveform, loadCachedSubtitleWaveform } from './subtitle-audio-waveform-cache';
	import { formatTimecode, waveformBars } from './subtitle-workbench';

	let {
		audioUrl,
		waveformUrl,
		downloadUrl,
		label,
		frameRate = 24
	}: {
		audioUrl: string;
		waveformUrl: string;
		downloadUrl: string;
		label: string;
		frameRate?: number;
	} = $props();

	let audioEl: HTMLAudioElement | null = $state(null);
	let peaks = $state<number[]>([]);
	let loading = $state(false);
	let audioPending = $state(false);
	let playing = $state(false);
	let currentTime = $state(0);
	let duration = $state(0);
	let loadToken = 0;
	let activeAudioUrl = '';

	const bars = $derived(waveformBars(peaks));
	const displayBars = $derived(bars.length ? bars : Array.from({ length: 36 }, (_, index) => 0.22 + ((index * 17) % 52) / 100));
	const progress = $derived(duration > 0 ? Math.min(100, Math.max(0, (currentTime / duration) * 100)) : 0);

	$effect(() => {
		const nextAudioUrl = audioUrl;
		const nextWaveformUrl = waveformUrl;
		if (nextAudioUrl !== activeAudioUrl) {
			audioEl?.pause();
			activeAudioUrl = nextAudioUrl;
			currentTime = 0;
			duration = 0;
			playing = false;
			audioPending = false;
		}
		void loadWaveform(nextWaveformUrl);
	});

	async function loadWaveform(url: string) {
		const token = ++loadToken;
		if (!url) {
			peaks = [];
			loading = false;
			return;
		}
		const cached = getCachedSubtitleWaveform(url);
		if (cached) {
			peaks = cached.peaks;
			if (cached.duration > 0) duration = cached.duration;
			loading = false;
			return;
		}
		peaks = [];
		loading = true;
		try {
			const payload = await loadCachedSubtitleWaveform(url);
			if (token === loadToken) {
				peaks = payload.peaks;
				if (payload.duration > 0) duration = payload.duration;
			}
		} catch {
			if (token === loadToken) peaks = [];
		} finally {
			if (token === loadToken) loading = false;
		}
	}

	async function togglePlayback(event: MouseEvent) {
		const audio = audioEl ?? (event.currentTarget as HTMLElement | null)?.closest('.audio-waveform')?.querySelector('audio') ?? null;
		if (!audio) return;
		audioEl = audio;
		if (!audio.paused) {
			audio.pause();
			return;
		}
		audioPending = true;
		try {
			if (audio.readyState === HTMLMediaElement.HAVE_NOTHING) audio.load();
			await audio.play();
		} catch {
			playing = false;
		} finally {
			audioPending = false;
			syncTime();
		}
	}

	function seek(event: MouseEvent) {
		if (!audioEl || !duration) return;
		const target = event.currentTarget as HTMLElement | null;
		if (!target) return;
		const rect = target.getBoundingClientRect();
		audioEl.currentTime = Math.max(0, Math.min(duration, ((event.clientX - rect.left) / rect.width) * duration));
		void audioEl.play();
	}

	function seekByKeyboard(event: KeyboardEvent) {
		if (!audioEl) return;
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
		event.preventDefault();
		audioEl.currentTime = Math.max(0, Math.min(duration, audioEl.currentTime + (event.key === 'ArrowRight' ? 1 : -1)));
	}

	function syncTime() {
		if (!audioEl) return;
		currentTime = audioEl.currentTime;
		duration = Number.isFinite(audioEl.duration) ? audioEl.duration : duration;
		playing = !audioEl.paused;
	}

	function markAudioReady() {
		audioPending = false;
		syncTime();
	}

	onDestroy(() => {
		loadToken += 1;
		if (audioEl) {
			audioEl.pause();
			audioEl.removeAttribute('src');
			audioEl.load();
		}
	});
</script>

<div class="audio-waveform" class:playing aria-label={label}>
	<audio
		bind:this={audioEl}
		preload="none"
		src={audioUrl}
		onloadedmetadata={syncTime}
		ontimeupdate={syncTime}
		onplay={markAudioReady}
		onplaying={markAudioReady}
		oncanplay={markAudioReady}
		onwaiting={() => (audioPending = true)}
		onerror={() => (audioPending = false)}
		onpause={syncTime}
		onended={syncTime}
	></audio>
	<button class="wave-button" type="button" aria-label={playing ? `暂停${label}` : audioPending ? `正在加载${label}` : `播放${label}`} aria-busy={loading || audioPending} data-tooltip={playing ? `暂停${label}` : `播放${label}`} onclick={togglePlayback}>
		{#if loading || audioPending}<span class="spin"><Loader2 size={13} /></span>{:else if playing}<Pause size={13} />{:else}<Play size={13} />{/if}
	</button>
	<div
		class="wave-track"
		role="slider"
		tabindex="0"
		aria-label={`${label}波形`}
		aria-valuemin="0"
		aria-valuemax={Math.round(duration)}
		aria-valuenow={Math.round(currentTime)}
		style={`--progress:${progress}%`}
		onclick={seek}
		onkeydown={seekByKeyboard}
	>
		<div class="wave-bars wave-bars-base" aria-hidden="true">
			{#each displayBars as height}
				<span style={`--bar-height:${Math.max(10, height * 100)}%`}></span>
			{/each}
		</div>
		<div class="wave-bars wave-bars-progress" aria-hidden="true">
			{#each displayBars as height}
				<span style={`--bar-height:${Math.max(10, height * 100)}%`}></span>
			{/each}
		</div>
		<small>{formatTimecode(currentTime * 1000, frameRate)}</small>
	</div>
	<a class="download-button" href={downloadUrl} download aria-label={`下载${label}`} data-tooltip={`下载${label}`}><Download size={13} /></a>
</div>

<style>
	.audio-waveform {
		display: grid;
		grid-template-columns: 25px minmax(0, 1fr) 25px;
		align-items: center;
		gap: 6px;
		min-width: 0;
	}

	.audio-waveform audio {
		display: none;
	}

	.wave-button,
	.download-button {
		display: inline-grid;
		place-items: center;
		width: 25px;
		height: 25px;
		box-sizing: border-box;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: #151b20;
		color: #d4e3e7;
		cursor: pointer;
	}

	.wave-button:hover,
	.wave-button:focus-visible,
	.download-button:hover,
	.download-button:focus-visible {
		border-color: rgba(87, 208, 200, 0.64);
		color: #8bf1e7;
		outline: none;
	}

	.wave-track {
		position: relative;
		height: 29px;
		min-width: 0;
		overflow: hidden;
		border: 0;
		background: transparent;
		cursor: pointer;
	}

	.wave-track:focus-visible {
		outline: 2px solid rgba(87, 208, 200, 0.72);
		outline-offset: 1px;
	}

	.wave-bars {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 5px;
		pointer-events: none;
	}

	.wave-bars span {
		flex: 1 1 0;
		min-width: 1px;
		height: var(--bar-height);
		border-radius: 1px;
		background: #536770;
	}

	.wave-bars-base {
		opacity: 0.78;
	}

	.wave-bars-progress {
		clip-path: inset(0 calc(100% - var(--progress)) 0 0);
	}

	.wave-bars-progress span {
		background: #67d9d0;
		filter: drop-shadow(0 0 3px rgba(103, 217, 208, 0.28));
	}

	.wave-track small {
		position: absolute;
		right: 5px;
		bottom: 2px;
		padding: 0 2px;
		background: rgba(10, 14, 18, 0.72);
		color: #c6d8dc;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 8px;
		line-height: 11px;
		pointer-events: none;
	}

	.spin {
		display: inline-grid;
		place-items: center;
		width: 14px;
		height: 14px;
		line-height: 0;
		transform-origin: 50% 50%;
		animation: spin 900ms linear infinite;
	}

	.spin :global(svg) {
		display: block;
		width: 13px;
		height: 13px;
		transform-origin: 50% 50%;
	}

	@keyframes spin { to { transform: rotate(360deg); } }

	@media (prefers-reduced-motion: reduce) {
		.spin { animation: none; }
	}
</style>
