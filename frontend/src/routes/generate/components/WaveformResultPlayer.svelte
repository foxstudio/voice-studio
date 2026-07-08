<script module lang="ts">
	type WaveformLoadJob = {
		isCancelled: () => boolean;
		onStart: () => void;
		run: () => Promise<void>;
		onFinish: () => void;
	};

	const waveformLoadQueue: WaveformLoadJob[] = [];
	let waveformLoadActive = false;

	function enqueueWaveformLoad(job: WaveformLoadJob) {
		waveformLoadQueue.push(job);
		void drainWaveformLoadQueue();
	}

	async function drainWaveformLoadQueue() {
		if (waveformLoadActive) return;
		waveformLoadActive = true;
		try {
			while (waveformLoadQueue.length) {
				const job = waveformLoadQueue.shift();
				if (!job) continue;
				if (job.isCancelled()) {
					job.onFinish();
					continue;
				}
				job.onStart();
				try {
					await job.run();
				} finally {
					job.onFinish();
				}
				await nextPaint();
			}
		} finally {
			waveformLoadActive = false;
		}
	}

	function nextPaint() {
		return new Promise<void>((resolve) => {
			if (typeof requestAnimationFrame === 'function') {
				requestAnimationFrame(() => resolve());
			} else {
				setTimeout(resolve, 0);
			}
		});
	}
</script>

<script lang="ts">
	import type { GenerationTask } from '$lib/api/types';
	import { Download, Loader2, Play, Square } from 'lucide-svelte';
	import { onMount } from 'svelte';

	type WaveSurfer = import('wavesurfer.js').default;

	type Props = {
		task: GenerationTask;
		audioUrl: string;
		downloadUrl: string;
		downloadName: string;
		durationLabel: string;
		isPlaying: boolean;
		isPending: boolean;
		currentTime: number;
		onPlay: (task: GenerationTask) => void;
		onStop: (task: GenerationTask) => void;
		onSeek: (task: GenerationTask, timeSeconds: number) => void;
	};

	let {
		task,
		audioUrl,
		downloadUrl,
		downloadName,
		durationLabel,
		isPlaying,
		isPending,
		currentTime,
		onPlay,
		onStop,
		onSeek
	}: Props = $props();

	let shellEl: HTMLElement | null = $state(null);
	let waveformEl: HTMLElement | null = $state(null);
	let waveSurfer: WaveSurfer | null = null;
	let observer: IntersectionObserver | null = null;
	let loading = $state(false);
	let loadQueued = $state(false);
	let ready = $state(false);
	let loadError = $state('');
	let lastLoadedUrl = '';
	let loadToken = 0;
	let destroyed = false;

	const durationSeconds = $derived(Math.max(0, (task.result_duration_ms ?? 0) / 1000));
	const timeLabel = $derived(formatClock(isPlaying || currentTime > 0 ? currentTime : 0));
	const statusLabel = $derived(loadError ? '波形不可用' : loadQueued ? '排队读取' : loading ? '读取波形' : durationLabel || '播放结果');
	const progressPercent = $derived.by(() => {
		const duration = durationSeconds || 0;
		if (!duration || (!isPlaying && currentTime <= 0)) return 0;
		return Math.max(0, Math.min(100, (currentTime / duration) * 100));
	});

	$effect(() => {
		if (!waveSurfer || !audioUrl || audioUrl === lastLoadedUrl) return;
		queueWaveformLoad(audioUrl);
	});

	onMount(() => {
		if (!shellEl) return;
		const fallbackTimer = window.setTimeout(() => {
			if (waveSurfer) return;
			observer?.disconnect();
			observer = null;
			void mountWaveform();
		}, 180);
		observer = new IntersectionObserver((entries) => {
			if (entries.some((entry) => entry.isIntersecting)) {
				observer?.disconnect();
				observer = null;
				window.setTimeout(() => void mountWaveform(), 80);
			}
		}, { rootMargin: '280px 0px' });
		observer.observe(shellEl);

		return () => {
			window.clearTimeout(fallbackTimer);
			destroyed = true;
			loadToken += 1;
			observer?.disconnect();
			waveSurfer?.destroy();
			waveSurfer = null;
		};
	});

	async function mountWaveform() {
		if (waveSurfer || !waveformEl || !audioUrl) return;
		loading = true;
		loadQueued = false;
		loadError = '';
		try {
			const [{ default: WaveSurfer }, { default: HoverPlugin }] = await Promise.all([
				import('wavesurfer.js'),
				import('wavesurfer.js/plugins/hover')
			]);
			waveSurfer = WaveSurfer.create({
				container: waveformEl,
				height: 28,
				waveColor: 'rgba(75, 94, 116, 0.48)',
				progressColor: '#67e8f9',
				cursorColor: 'transparent',
				cursorWidth: 0,
				barWidth: 2,
				barGap: 2,
				barRadius: 2,
				barHeight: 0.82,
				barMinHeight: 2,
				normalize: true,
				hideScrollbar: true,
				autoCenter: false,
				autoScroll: false,
				dragToSeek: { debounceTime: 50 },
				plugins: [
					HoverPlugin.create({
						lineColor: 'rgba(110, 231, 248, 0.42)',
						lineWidth: 1,
						labelColor: '#07121f',
						labelBackground: '#f8fafc',
						labelSize: 10,
						formatTimeCallback: formatClock
					})
				]
			});
			waveSurfer.on('ready', () => {
				ready = true;
				loading = false;
				loadQueued = false;
			});
			waveSurfer.on('error', () => {
				loadError = '波形加载失败';
				loading = false;
				loadQueued = false;
				ready = false;
			});
			waveSurfer.on('interaction', (time) => {
				onSeek(task, time);
			});
			queueWaveformLoad(audioUrl);
		} catch (error) {
			loadError = error instanceof Error ? error.message : '波形加载失败';
			loading = false;
			loadQueued = false;
		}
	}

	function queueWaveformLoad(url: string) {
		if (!waveSurfer || !url) return;
		const surfer = waveSurfer;
		const token = ++loadToken;
		loading = false;
		loadQueued = true;
		ready = false;
		loadError = '';
		lastLoadedUrl = url;

		const isCurrent = () => !destroyed && token === loadToken && waveSurfer === surfer;
		enqueueWaveformLoad({
			isCancelled: () => !isCurrent(),
			onStart: () => {
				if (!isCurrent()) return;
				loadQueued = false;
				loading = true;
			},
			run: async () => {
				if (!isCurrent()) return;
				try {
					await surfer.load(url, undefined, durationSeconds || undefined);
					if (!isCurrent()) return;
					ready = true;
					loading = false;
					loadQueued = false;
				} catch (error) {
					if (!isCurrent()) return;
					loadError = error instanceof Error ? error.message : '波形加载失败';
					loading = false;
					loadQueued = false;
					ready = false;
				}
			},
			onFinish: () => {
				if (!isCurrent() || loadError) return;
				loading = false;
				loadQueued = false;
			}
		});
	}

	function togglePlayback() {
		if (isPlaying || isPending) {
			onStop(task);
		} else {
			onPlay(task);
		}
	}

	function handleWaveformKeydown(event: KeyboardEvent) {
		if (!waveSurfer || !ready) return;
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			togglePlayback();
			return;
		}
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
		event.preventDefault();
		const direction = event.key === 'ArrowRight' ? 1 : -1;
		const baseTime = isPlaying ? currentTime : waveSurfer.getCurrentTime();
		const duration = waveSurfer.getDuration() || durationSeconds || 0;
		const nextTime = Math.max(0, Math.min(duration, baseTime + direction * 2));
		onSeek(task, nextTime);
	}

	function formatClock(seconds: number) {
		const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
		const whole = Math.floor(safe);
		const m = Math.floor(whole / 60);
		const s = whole % 60;
		return `${m}:${String(s).padStart(2, '0')}`;
	}
</script>

<div bind:this={shellEl} class="result-waveform-player" class:playing={isPlaying} class:loading class:pending={isPending} class:error={Boolean(loadError)}>
	<button
		class="waveform-play-button"
		class:playing={isPlaying}
		type="button"
		aria-label={isPlaying || isPending ? '停止播放' : '播放音频'}
		data-tooltip={isPlaying || isPending ? '停止播放当前音频' : '播放当前音频'}
		onclick={togglePlayback}
	>
		{#if isPending}
			<Loader2 size={15} />
		{:else if isPlaying}
			<Square size={15} />
		{:else}
			<Play size={15} />
		{/if}
	</button>
	<a
		class="waveform-download-button"
		href={downloadUrl}
		download={downloadName}
		aria-label="下载音频"
		data-tooltip="直接下载这条记录生成的音频文件"
	>
		<Download size={14} />
	</a>
	<div class="waveform-main">
		<div
			bind:this={waveformEl}
			class="waveform-canvas"
			role="slider"
			tabindex="0"
			style={`--waveform-progress:${progressPercent}%`}
			aria-label="生成结果波形，可点击或用左右方向键定位"
			aria-valuemin="0"
			aria-valuemax={Math.round(durationSeconds)}
			aria-valuenow={Math.round(Math.max(0, currentTime))}
			onkeydown={handleWaveformKeydown}
		>
			{#if !ready}
				<div class="waveform-skeleton" aria-hidden="true">
					{#each Array.from({ length: 36 }) as _, index}
						<span style={`height:${10 + ((index * 13) % 24)}px`}></span>
					{/each}
				</div>
			{/if}
			<span class="waveform-inline-label">{isPlaying ? timeLabel : statusLabel}</span>
		</div>
	</div>
</div>

<style>
	.result-waveform-player {
		display: grid;
		grid-template-columns: 28px 28px minmax(0, 1fr);
		align-items: center;
		gap: 6px;
		width: 100%;
		min-width: 0;
		padding: 4px 5px;
		border: 1px solid rgba(95, 111, 130, 0.46);
		border-radius: 8px;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.012)),
			rgba(7, 13, 20, 0.42);
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
	}

	.result-waveform-player.playing {
		border-color: rgba(110, 231, 248, 0.52);
		background:
			linear-gradient(180deg, rgba(32, 52, 66, 0.82), rgba(9, 18, 29, 0.62)),
			rgba(7, 13, 20, 0.52);
	}

	.waveform-play-button,
	.waveform-download-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: rgba(255, 255, 255, 0.025);
		color: var(--text);
		cursor: pointer;
		transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
	}

	.waveform-play-button:hover,
	.waveform-download-button:hover,
	.waveform-play-button:focus-visible,
	.waveform-download-button:focus-visible {
		border-color: #3a4656;
		background: rgba(255, 255, 255, 0.06);
		outline: none;
	}

	.waveform-play-button.playing {
		border-color: var(--accent);
		background: var(--accent);
		color: #07121f;
	}

	.waveform-play-button :global(svg),
	.waveform-download-button :global(svg) {
		flex: 0 0 auto;
	}

	.result-waveform-player.pending .waveform-play-button :global(svg) {
		animation: waveform-spin 900ms linear infinite;
	}

	.waveform-main {
		min-width: 0;
	}

	.waveform-canvas {
		position: relative;
		min-width: 0;
		height: 28px;
		overflow: hidden;
		border-radius: 5px;
		cursor: pointer;
	}

	.waveform-canvas:focus-visible {
		outline: 2px solid rgba(110, 231, 248, 0.72);
		outline-offset: 2px;
	}

	.waveform-canvas > :global(div:not(.waveform-skeleton)) {
		overflow: hidden !important;
		border-radius: 5px;
	}

	.waveform-canvas > :global(div:not(.waveform-skeleton))::part(progress) {
		width: var(--waveform-progress, 0%) !important;
		filter: drop-shadow(0 0 5px rgba(103, 232, 249, 0.32));
	}

	.waveform-skeleton {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 2px;
		pointer-events: none;
	}

	.waveform-skeleton span {
		flex: 1 1 0;
		min-width: 2px;
		border-radius: 2px;
		background: rgba(127, 145, 166, 0.24);
	}

	.waveform-inline-label {
		position: absolute;
		left: 5px;
		bottom: 1px;
		z-index: 4;
		max-width: calc(100% - 10px);
		padding: 1px 4px;
		border-radius: 999px;
		background: rgba(7, 13, 20, 0.72);
		color: var(--muted);
		font-size: 10px;
		line-height: 1.05;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		pointer-events: none;
	}

	@keyframes waveform-spin {
		to {
			transform: rotate(360deg);
		}
	}

	@media (max-width: 720px) {
		.result-waveform-player {
			width: 100%;
			grid-template-columns: 28px 28px minmax(72px, 1fr);
		}
	}
</style>
