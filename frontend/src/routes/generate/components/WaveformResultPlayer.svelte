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
		peaksUrl: string;
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
		peaksUrl,
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
	let progressUrl = '';
	let decodedDuration = $state(0);
	let displayedProgressPercent = $state(0);
	let lastSurferSyncTime = -1;

	const durationSeconds = $derived(Math.max(0, (task.result_duration_ms ?? 0) / 1000 || decodedDuration));
	const timeLabel = $derived(formatClock(isPlaying || currentTime > 0 ? currentTime : 0));
	const statusLabel = $derived(loadError ? '波形不可用' : loadQueued ? '排队读取' : loading ? '读取波形' : durationLabel || '播放结果');

	$effect(() => {
		const loadKey = `${audioUrl}|${peaksUrl}`;
		if (!waveSurfer || !audioUrl || loadKey === lastLoadedUrl) return;
		queueWaveformLoad(audioUrl, peaksUrl);
	});

	$effect(() => {
		if (audioUrl === progressUrl) return;
		progressUrl = audioUrl;
		decodedDuration = 0;
		displayedProgressPercent = 0;
		lastSurferSyncTime = -1;
	});

	$effect(() => {
		if (!isPlaying && !isPending && currentTime <= 0) return;
		updateDisplayedProgress(currentTime);
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
				updateDisplayedProgress(currentTime, true);
			});
			waveSurfer.on('error', () => {
				loadError = '波形加载失败';
				loading = false;
				loadQueued = false;
				ready = false;
			});
			waveSurfer.on('interaction', (time) => {
				updateDisplayedProgress(time);
				onSeek(task, time);
			});
			queueWaveformLoad(audioUrl, peaksUrl);
		} catch (error) {
			loadError = error instanceof Error ? error.message : '波形加载失败';
			loading = false;
			loadQueued = false;
		}
	}

	function queueWaveformLoad(url: string, waveformUrl: string) {
		if (!waveSurfer || !url) return;
		const surfer = waveSurfer;
		const token = ++loadToken;
		loading = false;
		loadQueued = true;
		ready = false;
		loadError = '';
		lastLoadedUrl = `${url}|${waveformUrl}`;

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
					const response = await fetch(waveformUrl);
					if (!response.ok) throw new Error('波形峰值读取失败');
					const waveform = await response.json() as { peaks: number[]; duration: number };
					decodedDuration = Math.max(0, Number(waveform.duration) || 0);
					await surfer.load(url, [waveform.peaks], waveform.duration || durationSeconds || undefined);
					if (!isCurrent()) return;
					ready = true;
					loading = false;
					loadQueued = false;
					updateDisplayedProgress(currentTime, true);
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
		updateDisplayedProgress(nextTime);
		onSeek(task, nextTime);
	}

	function updateDisplayedProgress(timeSeconds: number, forceSurferSync = false) {
		const duration = durationSeconds || waveSurfer?.getDuration() || 0;
		const safeTime = Number.isFinite(timeSeconds) ? Math.max(0, timeSeconds) : 0;
		if (!duration || !Number.isFinite(duration)) return;
		const boundedTime = Math.min(duration, safeTime);
		displayedProgressPercent = Math.max(0, Math.min(100, (boundedTime / duration) * 100));
		if (!waveSurfer || !ready) return;
		const surferTime = waveSurfer.getCurrentTime();
		if (forceSurferSync || !Number.isFinite(surferTime) || Math.abs(boundedTime - lastSurferSyncTime) >= 0.1) {
			waveSurfer.setTime(boundedTime);
			lastSurferSyncTime = boundedTime;
		}
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
	<div class="waveform-main">
		<div
			bind:this={waveformEl}
			class="waveform-canvas"
			role="slider"
			tabindex="0"
			style={`--waveform-progress:${displayedProgressPercent}%`}
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
			<span class="waveform-inline-label">{isPlaying || currentTime > 0 ? timeLabel : statusLabel}</span>
		</div>
	</div>
	<a
		class="waveform-download-button"
		href={downloadUrl}
		download={downloadName}
		aria-label="下载音频"
		data-tooltip="直接下载这条记录生成的音频文件"
	>
		<Download size={14} />
	</a>
</div>

<style>
	.result-waveform-player {
		display: grid;
		grid-template-columns: 32px minmax(0, 1fr) 32px;
		align-items: center;
		gap: 6px;
		width: 100%;
		min-width: 0;
		padding: 0;
		border: 0;
		background: transparent;
		box-shadow: none;
	}

	.result-waveform-player.playing {
		background: transparent;
	}

	.waveform-play-button,
	.waveform-download-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 32px;
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

	@media (prefers-reduced-motion: reduce) {
		.waveform-play-button,
		.waveform-download-button {
			transition: none;
		}

		.result-waveform-player.pending .waveform-play-button :global(svg) {
			animation: none;
		}
	}

	@media (max-width: 720px) {
		.result-waveform-player {
			width: 100%;
			grid-template-columns: 32px minmax(72px, 1fr) 32px;
		}
	}
</style>
