<script lang="ts">
	import { analyzeWaveformFromUrl, buildTimelineTicks, buildVisibleWaveformBars, formatDuration, formatTimecode, formatTimelineZoom } from '$lib/audio/waveform';
	import type { VideoLocalizationCue } from '$lib/api/types';
	import { ChevronsLeft, ChevronsRight, Play, Repeat, Square } from 'lucide-svelte';

	let {
		selectedCue,
		audioSrc,
		audioLabel,
		audioDurationMs,
		onUpdateCueTime
	}: {
		selectedCue: VideoLocalizationCue | null;
		audioSrc: string;
		audioLabel: string;
		audioDurationMs: number | null;
		onUpdateCueTime: (field: 'start_ms' | 'end_ms', valueMs: number) => void;
	} = $props();

	let previewAudio: HTMLAudioElement | null = $state(null);
	let timelinePanel: HTMLElement | null = $state(null);
	let timelineWindow: HTMLElement | null = $state(null);
	let sourceDurationMs = $state<number | null>(null);
	let trimStart = $state(0);
	let trimEnd = $state(0);
	let playhead = $state(0);
	let waveformBars: number[] = $state([]);
	let waveformLoading = $state(false);
	let waveformProgress = $state(0);
	let loopPreview = $state(false);
	let loopEnabled = $state(true);
	let timelineScrollLeft = $state(0);
	let timelineViewportWidth = $state(0);
	let timelineZoom = $state(1);
	let trimPanelActive = $state(false);
	let trimFocusWithin = $state(false);

	const durationSeconds = $derived(Math.max(0, (sourceDurationMs ?? 0) / 1000));
	const selectionDurationMs = $derived(Math.max(0, Math.round((trimEnd - trimStart) * 1000)));
	const trimStartPercent = $derived(durationSeconds ? Math.max(0, Math.min(100, (trimStart / durationSeconds) * 100)) : 0);
	const trimEndPercent = $derived(durationSeconds ? Math.max(0, Math.min(100, (trimEnd / durationSeconds) * 100)) : 0);
	const playheadPercent = $derived(durationSeconds ? Math.max(0, Math.min(100, (playhead / durationSeconds) * 100)) : trimStartPercent);
	const timelineTicks = $derived(buildTimelineTicks(durationSeconds, timelineZoom));
	const visibleWaveformBars = $derived(buildVisibleWaveformBars(waveformBars, timelineZoom, timelineScrollLeft, timelineViewportWidth));
	const trimHotkeysActive = $derived(trimPanelActive || trimFocusWithin);
	const audioReady = $derived(Boolean(audioSrc && sourceDurationMs));

	$effect(() => {
		void syncAudioSource(audioSrc, audioDurationMs, selectedCue?.cue_id ?? '', selectedCue?.start_ms ?? null, selectedCue?.end_ms ?? null);
	});

	$effect(() => {
		selectedCue?.cue_id;
		if (selectedCue?.start_ms !== null && selectedCue?.start_ms !== undefined) {
			trimStart = Math.max(0, selectedCue.start_ms / 1000);
		}
		if (selectedCue?.end_ms !== null && selectedCue?.end_ms !== undefined) {
			trimEnd = Math.max(trimStart + 0.1, selectedCue.end_ms / 1000);
		}
	});

	function selectedRange() {
		const duration = durationSeconds || 0;
		const start = Math.min(trimStart, Math.max(0, trimEnd - 0.1));
		const fallbackEnd = duration ? Math.min(duration, start + 2) : start + 2;
		const end = Math.max(start + 0.1, Math.min(duration || fallbackEnd, trimEnd || fallbackEnd));
		return { start, end };
	}

	function setTrimStart(value: string | number, persist = true) {
		const max = trimEnd || durationSeconds;
		const next = Number(value);
		if (!Number.isFinite(next)) return;
		trimStart = Math.max(0, Math.min(next, Math.max(0, max - 0.1)));
		if (playhead < trimStart) playhead = trimStart;
		if (loopPreview && previewAudio) previewAudio.currentTime = trimStart;
		if (persist) onUpdateCueTime('start_ms', Math.round(trimStart * 1000));
	}

	function setTrimEnd(value: string | number, persist = true) {
		const next = Number(value);
		if (!Number.isFinite(next)) return;
		trimEnd = Math.max(trimStart + 0.1, Math.min(durationSeconds || next, next));
		if (playhead > trimEnd) playhead = trimEnd;
		if (persist) onUpdateCueTime('end_ms', Math.round(trimEnd * 1000));
	}

	function setTrimStartAtPlayhead() {
		setTrimStart(playhead);
	}

	function setTrimEndAtPlayhead() {
		setTrimEnd(playhead);
	}

	function setPlaybackPosition(value: string | number) {
		const next = Number(value);
		if (!Number.isFinite(next)) return;
		playhead = Math.max(0, Math.min(durationSeconds || next, next));
		if (loopPreview && previewAudio) previewAudio.currentTime = playhead;
	}

	function timelineTimeFromPointer(event: PointerEvent, timebar: HTMLElement) {
		const rect = timebar.getBoundingClientRect();
		const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
		return Math.round(ratio * durationSeconds * 10) / 10;
	}

	function handleTimebarPointer(event: PointerEvent) {
		if (!durationSeconds) return;
		if ((event.target as HTMLElement).closest('button,input')) return;
		trimPanelActive = true;
		setPlaybackPosition(timelineTimeFromPointer(event, event.currentTarget as HTMLElement));
	}

	function beginTrimDrag(event: PointerEvent, boundary: 'start' | 'end') {
		if (!durationSeconds) return;
		const timebar = (event.currentTarget as HTMLElement).closest('.cue-timeline-timebar') as HTMLElement | null;
		if (!timebar) return;
		trimPanelActive = true;
		event.preventDefault();
		event.stopPropagation();
		const apply = (cursorEvent: PointerEvent) => {
			const next = timelineTimeFromPointer(cursorEvent, timebar);
			if (boundary === 'start') setTrimStart(next);
			else setTrimEnd(next);
		};
		const cleanup = () => {
			window.removeEventListener('pointermove', apply);
			window.removeEventListener('pointerup', cleanup);
			window.removeEventListener('pointercancel', cleanup);
		};
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', cleanup, { once: true });
		window.addEventListener('pointercancel', cleanup, { once: true });
	}

	function beginPlayheadDrag(event: PointerEvent) {
		if (!durationSeconds) return;
		const timebar = (event.currentTarget as HTMLElement).closest('.cue-timeline-timebar') as HTMLElement | null;
		if (!timebar) return;
		trimPanelActive = true;
		event.preventDefault();
		event.stopPropagation();
		const apply = (cursorEvent: PointerEvent) => setPlaybackPosition(timelineTimeFromPointer(cursorEvent, timebar));
		const cleanup = () => {
			window.removeEventListener('pointermove', apply);
			window.removeEventListener('pointerup', cleanup);
			window.removeEventListener('pointercancel', cleanup);
		};
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', cleanup, { once: true });
		window.addEventListener('pointercancel', cleanup, { once: true });
	}

	function updateTimelineViewport(element: HTMLElement) {
		timelineScrollLeft = element.scrollLeft;
		timelineViewportWidth = element.clientWidth;
	}

	function handleTimelineWheel(event: WheelEvent) {
		if (!durationSeconds) return;
		event.preventDefault();
		const windowEl = event.currentTarget as HTMLElement;
		const rect = windowEl.getBoundingClientRect();
		const anchorOffset = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
		const anchorRatio = (windowEl.scrollLeft + anchorOffset) / Math.max(1, windowEl.scrollWidth);
		const factor = event.deltaY < 0 ? 1.16 : 1 / 1.16;
		zoomTimeline(timelineZoom * factor, anchorRatio, anchorOffset);
	}

	function zoomTimeline(nextZoom: number, anchorRatio = 0.5, anchorOffset?: number) {
		const clamped = Math.max(1, Math.min(18, nextZoom));
		if (Math.abs(clamped - timelineZoom) < 0.001) return;
		timelineZoom = clamped;
		requestAnimationFrame(() => {
			const windowEl = timelineWindow;
			if (!windowEl) return;
			const scrollWidth = Math.max(windowEl.clientWidth, windowEl.clientWidth * timelineZoom);
			const targetOffset = anchorOffset ?? windowEl.clientWidth / 2;
			windowEl.scrollLeft = Math.max(0, anchorRatio * scrollWidth - targetOffset);
			updateTimelineViewport(windowEl);
		});
	}

	function toggleSelectionPreview() {
		void runSelectionPreview();
	}

	async function runSelectionPreview() {
		if (!previewAudio || !audioSrc || !durationSeconds) return;
		if (loopPreview && !previewAudio.paused) {
			stopSelectionPreview();
			return;
		}
		const { start } = selectedRange();
		const absoluteUrl = new URL(audioSrc, window.location.href).href;
		if (previewAudio.src !== absoluteUrl) previewAudio.src = audioSrc;
		previewAudio.currentTime = start;
		playhead = start;
		loopPreview = true;
		try {
			await previewAudio.play();
		} catch {
			loopPreview = false;
		}
	}

	function stopSelectionPreview() {
		const { start } = selectedRange();
		if (previewAudio) {
			previewAudio.pause();
			previewAudio.currentTime = start;
		}
		playhead = start;
		loopPreview = false;
	}

	function handlePreviewMetadata() {
		if (!previewAudio || sourceDurationMs) return;
		const duration = Number.isFinite(previewAudio.duration) ? previewAudio.duration : 0;
		if (!duration) return;
		sourceDurationMs = Math.round(duration * 1000);
	}

	function handlePreviewTimeUpdate() {
		if (!previewAudio || !loopPreview) return;
		const { start, end } = selectedRange();
		playhead = Math.max(start, Math.min(end, previewAudio.currentTime));
		if (previewAudio.currentTime >= end || previewAudio.currentTime < start) {
			if (loopEnabled) {
				previewAudio.currentTime = start;
				playhead = start;
				void previewAudio.play().catch(() => {
					loopPreview = false;
				});
			} else {
				stopSelectionPreview();
			}
		}
	}

	function handleTrimFocusOut(event: FocusEvent) {
		const current = event.currentTarget as HTMLElement;
		trimFocusWithin = Boolean(event.relatedTarget && current.contains(event.relatedTarget as Node));
	}

	function handleGlobalPointerDown(event: PointerEvent) {
		trimPanelActive = Boolean(timelinePanel?.contains(event.target as Node));
	}

	function isTypingTarget(target: EventTarget | null) {
		const element = target instanceof HTMLElement ? target : null;
		if (!element) return false;
		const tag = element.tagName.toLowerCase();
		return tag === 'input' || tag === 'textarea' || tag === 'select' || element.isContentEditable;
	}

	function handleTrimKeydown(event: KeyboardEvent) {
		if (!trimHotkeysActive || !audioReady) return;
		if (isTypingTarget(event.target)) return;
		const key = event.key.toLowerCase();
		const isSpace = event.code === 'Space' || event.key === ' ';
		if (isSpace) {
			if ((event.target as HTMLElement | null)?.closest('button,a')) return;
			event.preventDefault();
			if (!event.repeat && selectionDurationMs >= 100) toggleSelectionPreview();
			return;
		}
		if (event.repeat) return;
		if (key === 'i') {
			event.preventDefault();
			setTrimStartAtPlayhead();
		} else if (key === 'o') {
			event.preventDefault();
			setTrimEndAtPlayhead();
		}
	}

	async function syncAudioSource(nextAudioSrc: string, durationMs: number | null, cueId: string, startMs: number | null, endMs: number | null) {
		stopSelectionPreview();
		waveformBars = [];
		waveformLoading = Boolean(nextAudioSrc);
		waveformProgress = 0;
		sourceDurationMs = durationMs;
		timelineZoom = 1;
		timelineScrollLeft = 0;
		timelineViewportWidth = 0;

		if (!nextAudioSrc) {
			trimStart = 0;
			trimEnd = 0;
			playhead = 0;
			return;
		}

		try {
			trimStart = startMs !== null && startMs !== undefined ? startMs / 1000 : 0;
			trimEnd = endMs !== null && endMs !== undefined ? endMs / 1000 : Math.max(trimStart + 0.1, 2);
			playhead = trimStart;
			const analysis = await analyzeWaveformFromUrl(nextAudioSrc, (nextBars, progress) => {
				waveformBars = nextBars;
				waveformProgress = progress;
			});
			const duration = durationMs ? durationMs / 1000 : analysis.durationSeconds;
			sourceDurationMs = Math.round(duration * 1000);
			trimStart = Math.max(0, Math.min(trimStart, Math.max(0, duration - 0.1)));
			trimEnd = Math.max(trimStart + 0.1, Math.min(duration, trimEnd || Math.max(trimStart + 0.1, 2)));
			playhead = Math.max(trimStart, Math.min(trimEnd, playhead));
			waveformBars = analysis.bars;
			waveformProgress = 1;
			if (selectedCue?.cue_id === cueId && timelineWindow) updateTimelineViewport(timelineWindow);
		} catch {
			waveformBars = [];
			waveformProgress = 0;
		} finally {
			waveformLoading = false;
		}
	}
</script>

<svelte:window onkeydown={handleTrimKeydown} onpointerdown={handleGlobalPointerDown} />

<section bind:this={timelinePanel} class="cue-timeline-panel">
	<div class="cue-timeline-head">
		<div>
			<h3>时间轴与波形</h3>
			<p>{audioLabel} 的整段时间轴。拖动 IN / OUT 可直接调整 cue 入点出点。</p>
		</div>
		<div class="cue-timeline-metrics">
			<div><span>当前</span><strong>{formatTimecode(playhead)}</strong></div>
			<div><span>入点</span><strong class="metric-in">{formatTimecode(trimStart)}</strong></div>
			<div><span>出点</span><strong class="metric-out">{formatTimecode(trimEnd)}</strong></div>
			<div><span>片段时长</span><strong>{formatDuration(selectionDurationMs)}</strong></div>
		</div>
	</div>

	{#if audioSrc}
		<audio bind:this={previewAudio} src={audioSrc} preload="metadata" onloadedmetadata={handlePreviewMetadata} ontimeupdate={handlePreviewTimeUpdate} onended={stopSelectionPreview}></audio>
		<div
			class="cue-timeline-shell"
			class:hotkeys-active={trimHotkeysActive}
			role="group"
			aria-label="cue 时间轴，空格播放选区，I 设置入点，O 设置出点"
			onfocusin={() => (trimFocusWithin = true)}
			onfocusout={handleTrimFocusOut}
		>
			<div class="cue-timeline-toolbar">
				<div class="toolbar-left">
					<button class="timeline-icon-btn play" type="button" aria-label={loopPreview ? '停止选区播放' : '播放选区'} onclick={toggleSelectionPreview} disabled={!audioReady || selectionDurationMs < 100}>
						{#if loopPreview}<Square size={16} />{:else}<Play size={16} />{/if}
					</button>
					<button class="timeline-loop-btn" class:active={loopEnabled} type="button" aria-label={loopEnabled ? '关闭循环播放' : '开启循环播放'} onclick={() => (loopEnabled = !loopEnabled)} disabled={!audioReady}>
						<Repeat size={14} /> 循环
					</button>
					<div class="timeline-zoom-buttons">
						<button class="timeline-tool-btn" type="button" aria-label="缩小时间轴" onclick={() => zoomTimeline(timelineZoom / 1.35)} disabled={!audioReady}>−</button>
						<span>{formatTimelineZoom(timelineZoom)}x</span>
						<button class="timeline-tool-btn" type="button" aria-label="放大时间轴" onclick={() => zoomTimeline(timelineZoom * 1.35)} disabled={!audioReady}>+</button>
					</div>
				</div>
				<div class="toolbar-right">
					<button class="timeline-marker-btn" type="button" onclick={setTrimStartAtPlayhead} disabled={!audioReady}><ChevronsLeft size={13} /> 入点</button>
					<button class="timeline-marker-btn" type="button" onclick={setTrimEndAtPlayhead} disabled={!audioReady}><ChevronsRight size={13} /> 出点</button>
				</div>
			</div>

			<div bind:this={timelineWindow} class="cue-timeline-window" role="region" aria-label="cue 时间轴滚动区域" onwheel={handleTimelineWheel} onscroll={(event) => updateTimelineViewport(event.currentTarget as HTMLElement)} onpointerenter={(event) => updateTimelineViewport(event.currentTarget as HTMLElement)}>
				<div class="cue-timeline-timebar" role="group" aria-label="cue 波形时间轴" style={`--trim-start:${trimStartPercent}%;--trim-end:${trimEndPercent}%;--playhead:${playheadPercent}%;width:${timelineZoom * 100}%`} onpointerdown={handleTimebarPointer}>
					<div class="cue-timeline-ruler" aria-hidden="true">
						{#each timelineTicks as tick}
							<span class:major={tick.major} style={`left:${tick.percent}%`}><i></i><b>{tick.label}</b></span>
						{/each}
					</div>
					<div class="cue-timeline-track" aria-hidden="true"></div>
					<div class="cue-timeline-waveform" class:loading={waveformLoading} style={`--waveform-progress:${Math.round(waveformProgress * 100)}%`} aria-hidden="true">
						{#if waveformBars.length}
							<svg class="cue-timeline-waveform-svg" viewBox={`0 0 ${waveformBars.length} 100`} preserveAspectRatio="none">
								<line class="waveform-midline" x1="0" y1="50" x2={waveformBars.length} y2="50" />
								{#each visibleWaveformBars as bar}
									<rect x={bar.x + 0.08} y={50 - bar.level * 46} width={bar.width} height={Math.max(8, bar.level * 92)} rx="0.18" />
								{/each}
							</svg>
						{:else}
							<span class="waveform-empty"></span>
						{/if}
					</div>
					<div class="cue-play-progress" aria-hidden="true"></div>
					<button type="button" class="timeline-playhead-handle" aria-label="拖动当前播放指针" onpointerdown={beginPlayheadDrag}><span>当前</span></button>
					<button type="button" class="timeline-handle-label timeline-in-label" aria-label="拖动裁切入点" onpointerdown={(event) => beginTrimDrag(event, 'start')}><span>IN</span></button>
					<button type="button" class="timeline-handle-label timeline-out-label" aria-label="拖动裁切出点" onpointerdown={(event) => beginTrimDrag(event, 'end')}><span>OUT</span></button>
					<input aria-label="裁切入点" class="timeline-range timeline-start" type="range" min="0" max={durationSeconds} step="0.1" value={trimStart} oninput={(event) => setTrimStart((event.currentTarget as HTMLInputElement).value)} disabled={!audioReady} />
					<input aria-label="裁切出点" class="timeline-range timeline-end" type="range" min="0.1" max={durationSeconds} step="0.1" value={trimEnd} oninput={(event) => setTrimEnd((event.currentTarget as HTMLInputElement).value)} disabled={!audioReady} />
				</div>
			</div>
		</div>
	{:else}
		<p class="muted">当前还没有可用的源音轨。先抽取源音或完成人声分离后，这里会显示可编辑的时间轴。</p>
	{/if}
</section>

<style>
	.cue-timeline-panel {
		display: grid;
		gap: 12px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
	}

	.cue-timeline-head {
		display: grid;
		gap: 10px;
	}

	.cue-timeline-head h3,
	.cue-timeline-head p {
		margin: 0;
	}

	.cue-timeline-head h3 {
		font-size: 14px;
	}

	.cue-timeline-head p {
		color: var(--muted);
		font-size: 12px;
	}

	.cue-timeline-metrics {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 8px;
	}

	.cue-timeline-metrics div {
		padding: 8px;
		border-radius: 7px;
		background: #0c1015;
		border: 1px solid var(--line);
	}

	.cue-timeline-metrics span,
	.cue-timeline-metrics strong {
		display: block;
	}

	.cue-timeline-metrics span {
		font-size: 11px;
		color: var(--muted);
	}

	.metric-in {
		color: #8cc4ff;
	}

	.metric-out {
		color: #58d5ab;
	}

	.cue-timeline-shell {
		display: grid;
		gap: 12px;
		--trim-start: 0%;
		--trim-end: 100%;
		--playhead: 0%;
	}

	.cue-timeline-shell.hotkeys-active,
	.cue-timeline-shell:focus-visible {
		outline: 1px solid rgba(79, 156, 249, 0.5);
		outline-offset: 1px;
	}

	.cue-timeline-toolbar {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		flex-wrap: wrap;
	}

	.toolbar-left,
	.toolbar-right {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.timeline-icon-btn,
	.timeline-loop-btn,
	.timeline-tool-btn,
	.timeline-marker-btn {
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #0d1218;
		color: var(--text);
	}

	.timeline-icon-btn,
	.timeline-tool-btn {
		width: 34px;
		height: 34px;
		display: inline-grid;
		place-items: center;
	}

	.timeline-icon-btn.play {
		background: #1c3f67;
		border-color: #3c74b4;
	}

	.timeline-loop-btn,
	.timeline-marker-btn {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 8px 10px;
		font-size: 12px;
	}

	.timeline-loop-btn.active {
		background: #173629;
		border-color: #2e7d5a;
		color: #a5e8cb;
	}

	.timeline-zoom-buttons {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 0 4px;
	}

	.timeline-zoom-buttons span {
		min-width: 38px;
		text-align: center;
		font-size: 12px;
		color: var(--muted);
	}

	.cue-timeline-window {
		overflow-x: auto;
		overflow-y: hidden;
		padding-bottom: 6px;
	}

	.cue-timeline-timebar {
		position: relative;
		min-width: 100%;
		height: 220px;
		border-radius: 8px;
		background:
			linear-gradient(
				90deg,
				rgba(2, 6, 12, 0.72) var(--trim-start),
				rgba(79, 156, 249, 0.28) var(--trim-start),
				rgba(79, 156, 249, 0.28) var(--trim-end),
				rgba(2, 6, 12, 0.72) var(--trim-end)
			),
			#0b1015;
		border: 1px solid var(--line);
	}

	.cue-timeline-ruler {
		position: absolute;
		inset: 10px 12px auto;
		height: 30px;
	}

	.cue-timeline-ruler span {
		position: absolute;
		top: 0;
		transform: translateX(-50%);
	}

	.cue-timeline-ruler i {
		display: block;
		width: 1px;
		height: 12px;
		background: rgba(255, 255, 255, 0.2);
	}

	.cue-timeline-ruler span.major i {
		height: 16px;
		background: rgba(255, 255, 255, 0.38);
	}

	.cue-timeline-ruler b {
		display: block;
		margin-top: 4px;
		font-size: 10px;
		color: var(--muted);
		font-weight: 500;
		white-space: nowrap;
	}

	.cue-timeline-track {
		position: absolute;
		left: 12px;
		right: 12px;
		top: 58px;
		bottom: 18px;
		border-radius: 8px;
		background: rgba(255, 255, 255, 0.02);
	}

	.cue-timeline-waveform {
		position: absolute;
		left: 12px;
		right: 12px;
		top: 70px;
		height: 110px;
		border-radius: 8px;
		overflow: hidden;
	}

	.cue-timeline-waveform.loading::after {
		content: '';
		position: absolute;
		inset: 0;
		background:
			linear-gradient(90deg, rgba(13, 18, 24, 0.44) var(--waveform-progress), rgba(13, 18, 24, 0.74) var(--waveform-progress)),
			linear-gradient(110deg, transparent, rgba(255, 255, 255, 0.08), transparent);
		background-size: 100% 100%, 24% 100%;
		background-position: 0 0, var(--waveform-progress) 0;
		background-repeat: no-repeat;
		animation: cue-waveform-scan 1s linear infinite;
	}

	@keyframes cue-waveform-scan {
		from { background-position: 0 0, -24% 0; }
		to { background-position: 0 0, 124% 0; }
	}

	.cue-timeline-waveform-svg {
		width: 100%;
		height: 100%;
		display: block;
	}

	.cue-timeline-waveform-svg rect {
		fill: rgba(93, 170, 255, 0.84);
	}

	.cue-timeline-waveform-svg .waveform-midline {
		stroke: rgba(255, 255, 255, 0.06);
		stroke-width: 1;
	}

	.waveform-empty {
		position: absolute;
		inset: 50% 12px auto;
		height: 1px;
		background: rgba(255, 255, 255, 0.08);
	}

	.cue-play-progress {
		position: absolute;
		top: 58px;
		bottom: 18px;
		left: var(--playhead);
		width: 2px;
		background: rgba(255, 255, 255, 0.92);
		box-shadow: 0 0 10px rgba(255, 255, 255, 0.22);
		transform: translateX(-1px);
	}

	.timeline-playhead-handle,
	.timeline-handle-label {
		position: absolute;
		top: 46px;
		transform: translateX(-50%);
		border: 0;
		background: transparent;
		color: #fff;
		padding: 0;
		cursor: ew-resize;
	}

	.timeline-playhead-handle {
		left: var(--playhead);
		cursor: grab;
	}

	.timeline-playhead-handle::after,
	.timeline-handle-label::after {
		content: '';
		position: absolute;
		top: 12px;
		left: 50%;
		width: 2px;
		height: 148px;
		transform: translateX(-50%);
	}

	.timeline-playhead-handle::after {
		background: rgba(255, 255, 255, 0.9);
	}

	.timeline-handle-label::after {
		background: rgba(93, 170, 255, 0.72);
	}

	.timeline-playhead-handle span,
	.timeline-handle-label span {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 42px;
		height: 24px;
		padding: 0 10px;
		border-radius: 999px;
		font-size: 11px;
		font-weight: 700;
		box-shadow: 0 6px 16px rgba(0, 0, 0, 0.28);
	}

	.timeline-playhead-handle span {
		background: #f2f6ff;
		color: #0c1117;
	}

	.timeline-handle-label span {
		background: #1b3d63;
		color: #fff;
	}

	.timeline-in-label {
		left: var(--trim-start);
	}

	.timeline-out-label {
		left: var(--trim-end);
	}

	.timeline-range {
		position: absolute;
		left: 12px;
		right: 12px;
		bottom: 22px;
		width: calc(100% - 24px);
		appearance: none;
		background: transparent;
		pointer-events: none;
	}

	.timeline-range::-webkit-slider-runnable-track,
	.timeline-range::-moz-range-track {
		height: 18px;
		background: transparent;
	}

	.timeline-range::-webkit-slider-thumb,
	.timeline-range::-moz-range-thumb {
		appearance: none;
		width: 18px;
		height: 18px;
		border-radius: 999px;
		border: 2px solid #fff;
		background: #4f9cf9;
		box-shadow: 0 0 0 4px rgba(79, 156, 249, 0.16);
		pointer-events: auto;
		cursor: ew-resize;
	}

	.timeline-end::-webkit-slider-thumb,
	.timeline-end::-moz-range-thumb {
		background: #42c49b;
		box-shadow: 0 0 0 4px rgba(66, 196, 155, 0.16);
	}

	.timeline-icon-btn:disabled,
	.timeline-loop-btn:disabled,
	.timeline-tool-btn:disabled,
	.timeline-marker-btn:disabled,
	.timeline-range:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	@media (max-width: 900px) {
		.cue-timeline-metrics {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		.cue-timeline-timebar {
			height: 238px;
		}
	}
</style>
