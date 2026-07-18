<script lang="ts">
	import { buildVisibleWaveformBars } from '$lib/audio/waveform';
	import { ChevronsLeft, ChevronsRight, CircleCheck, Play, Plus, Repeat, RotateCcw, Square, X } from 'lucide-svelte';
	import { onMount } from 'svelte';

	interface Props {
		sourceUrl: string;
		durationMs: number;
		startMs: number;
		endMs: number;
		busy?: boolean;
		dirty?: boolean;
		matched?: boolean;
		ariaLabel?: string;
		purposeLabel?: string;
		statusDirtyLabel?: string;
		statusReadyLabel?: string;
		statusIdleLabel?: string;
		applyAriaLabel?: string;
		applyTooltip?: string;
		showRegister?: boolean;
		registerDisabled?: boolean;
		onRegister?: () => void;
		clearLabel?: string;
		clearTooltip?: string;
		clearDisabled?: boolean;
		onClear?: () => void;
		onRangeChange?: (startMs: number, endMs: number) => void;
		onApply?: () => void;
	}

	let {
		sourceUrl,
		durationMs,
		startMs,
		endMs,
		busy = false,
		dirty = false,
		matched = false,
		ariaLabel = '裁切选区，空格播放选区，I 设置入点，O 设置出点',
		purposeLabel = '参考音频',
		statusDirtyLabel = '待重新应用',
		statusReadyLabel = '已生效',
		statusIdleLabel = '待应用',
		applyAriaLabel = '使用当前选区',
		applyTooltip = '使用当前选区作为参考片段',
		showRegister = false,
		registerDisabled = true,
		onRegister = () => {},
		clearLabel = '',
		clearTooltip = '',
		clearDisabled = false,
		onClear = () => {},
		onRangeChange = () => {},
		onApply = () => {}
	}: Props = $props();

	let audio: HTMLAudioElement;
	let timebarWindow: HTMLDivElement;
	let waveformBars: number[] = $state([]);
	let waveformLoading = $state(false);
	let waveformProgress = $state(0);
	let playbackPosition = $state(0);
	let timelineZoom = $state(1);
	let timelineScrollLeft = $state(0);
	let timelineViewportWidth = $state(0);
	let timelinePanning = $state(false);
	let loopPreview = $state(false);
	let loopEnabled = $state(true);
	let trimEditing = $state(false);
	let trimHover = $state(false);
	let trimFocusWithin = $state(false);
	let frame: number | null = null;

	const durationSeconds = $derived(Math.max(0.1, durationMs / 1000));
	const trimStart = $derived(Math.max(0, Math.min(durationSeconds, startMs / 1000)));
	const trimEnd = $derived(Math.max(trimStart + 0.1, Math.min(durationSeconds, endMs / 1000)));
	const selectedDurationMs = $derived(Math.max(0, Math.round((trimEnd - trimStart) * 1000)));
	const trimStartPercent = $derived((trimStart / durationSeconds) * 100);
	const trimEndPercent = $derived((trimEnd / durationSeconds) * 100);
	const playheadPercent = $derived((playbackPosition / durationSeconds) * 100);
	const timelineTicks = $derived.by(() => buildTimelineTicks(durationSeconds, timelineZoom));
	const visibleWaveformBars = $derived.by(() => buildVisibleWaveformBars(waveformBars, timelineZoom, timelineScrollLeft, timelineViewportWidth));
	const hotkeysActive = $derived(trimHover || trimFocusWithin);

	function formatDuration(ms: number) {
		const seconds = Math.max(0, ms) / 1000;
		return `${seconds.toFixed(seconds >= 10 ? 1 : 2).replace(/\.0+$/, '')} 秒`;
	}
	function formatTimecode(seconds: number) {
		const safe = Math.max(0, seconds);
		const minutes = Math.floor(safe / 60);
		const rest = safe - minutes * 60;
		return minutes ? `${minutes}:${rest.toFixed(1).padStart(4, '0')}` : `${rest.toFixed(1)} 秒`;
	}
	function formatTimelineTick(valueSeconds: number, fps = 30) {
		if (valueSeconds < 1) return `${Math.round(valueSeconds * fps)}f`;
		const safe = Math.max(0, Math.round(valueSeconds * 10) / 10);
		const hours = Math.floor(safe / 3600);
		const minutes = Math.floor((safe % 3600) / 60);
		const seconds = safe % 60;
		if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${String(Math.floor(seconds)).padStart(2, '0')}`;
		if (safe < 10 && !Number.isInteger(safe)) return `${safe.toFixed(1)}s`;
		return `${minutes}:${String(Math.floor(seconds)).padStart(2, '0')}`;
	}
	function formatTimelineZoom(value: number) { return value < 10 ? value.toFixed(1) : value.toFixed(0); }
	function buildTimelineTicks(duration: number, zoom: number) {
		if (!duration) return [];
		const target = Math.max(8, Math.min(36000, Math.round(28 * Math.max(1, zoom))));
		const rawStep = duration / target;
		const frameStep = 1 / 30;
		const steps = [frameStep, frameStep * 2, frameStep * 5, frameStep * 10, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
		const step = steps.find((value) => value >= rawStep) ?? steps[steps.length - 1];
		const labelEvery = step < 1 ? Math.max(1, Math.round(1 / step)) : step < 5 ? 2 : 1;
		const ticks: Array<{ time: number; percent: number; label: string; major: boolean }> = [];
		for (let time = 0; time <= duration + 0.001; time += step) {
			const index = Math.round(time / step);
			ticks.push({ time, percent: (time / duration) * 100, label: index % labelEvery === 0 ? formatTimelineTick(time) : '', major: index % labelEvery === 0 });
		}
		if (ticks[ticks.length - 1]?.time !== duration) ticks.push({ time: duration, percent: 100, label: formatTimelineTick(duration), major: true });
		return ticks;
	}
	function updateTimelineViewport(element = timebarWindow) {
		if (!element) return;
		timelineScrollLeft = element.scrollLeft;
		timelineViewportWidth = element.clientWidth;
	}
	function zoomTimeline(nextZoom: number, anchorRatio?: number, anchorOffset?: number) {
		const element = timebarWindow;
		const ratio = anchorRatio ?? ((trimStart + trimEnd) / 2) / durationSeconds;
		const offset = anchorOffset ?? (element?.clientWidth ?? 0) / 2;
		timelineZoom = Math.max(1, Math.min(1200, Math.round(nextZoom * 10) / 10));
		requestAnimationFrame(() => {
			if (!element) return;
			element.scrollLeft = Math.max(0, ratio * element.scrollWidth - offset);
			updateTimelineViewport(element);
		});
	}
	function setRange(startSeconds: number, endSeconds: number) {
		const start = Math.max(0, Math.min(startSeconds, durationSeconds - 0.1));
		const end = Math.min(durationSeconds, Math.max(start + 0.1, endSeconds));
		onRangeChange(Math.round(start * 1000), Math.round(end * 1000));
	}
	function setTrimStart(value: string | number) {
		const next = Number(value);
		if (Number.isFinite(next)) setRange(next, trimEnd);
	}
	function setTrimEnd(value: string | number) {
		const next = Number(value);
		if (Number.isFinite(next)) setRange(trimStart, next);
	}
	function resetRange() { playbackPosition = 0; setRange(0, durationSeconds); }
	function setStartAtPlayhead() { setRange(playbackPosition, trimEnd); }
	function setEndAtPlayhead() { setRange(trimStart, playbackPosition); }
	function timeFromPointer(event: PointerEvent, timebar: HTMLElement) {
		const rect = timebar.getBoundingClientRect();
		return Math.round(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * durationSeconds * 10) / 10;
	}
	function handleTimebarPointer(event: PointerEvent) {
		if ((event.target as HTMLElement).closest('button,input')) return;
		if (event.button === 1) { beginTimelinePan(event); return; }
		if (event.button !== 0) return;
		event.preventDefault();
		const timebar = event.currentTarget as HTMLElement;
		const anchor = timeFromPointer(event, timebar);
		const originX = event.clientX;
		const originY = event.clientY;
		let dragged = false;
		const apply = (moveEvent: PointerEvent) => {
			if (!dragged && Math.max(Math.abs(moveEvent.clientX - originX), Math.abs(moveEvent.clientY - originY)) < 3) return;
			dragged = true;
			const current = timeFromPointer(moveEvent, timebar);
			setRange(Math.min(anchor, current), Math.max(anchor, current));
		};
		const finish = () => {
			window.removeEventListener('pointermove', apply);
			window.removeEventListener('pointerup', finish);
			window.removeEventListener('pointercancel', finish);
			if (!dragged) playbackPosition = anchor;
		};
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', finish, { once: true });
		window.addEventListener('pointercancel', finish, { once: true });
	}
	function beginTimelinePan(event: PointerEvent) {
		const element = timebarWindow;
		if (!element) return;
		event.preventDefault();
		const startX = event.clientX;
		const startScrollLeft = element.scrollLeft;
		timelinePanning = true;
		const apply = (moveEvent: PointerEvent) => { element.scrollLeft = startScrollLeft - (moveEvent.clientX - startX); updateTimelineViewport(element); };
		const finish = () => { timelinePanning = false; window.removeEventListener('pointermove', apply); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', finish); };
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', finish, { once: true });
		window.addEventListener('pointercancel', finish, { once: true });
	}
	function handleTimelineWheel(event: WheelEvent) {
		event.preventDefault();
		const element = event.currentTarget as HTMLElement;
		if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
			element.scrollLeft += Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
			updateTimelineViewport(element as HTMLDivElement);
			return;
		}
		const rect = element.getBoundingClientRect();
		const anchorOffset = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
		const anchorRatio = (element.scrollLeft + anchorOffset) / Math.max(1, element.scrollWidth);
		zoomTimeline(timelineZoom * (event.deltaY < 0 ? 1.16 : 1 / 1.16), anchorRatio, anchorOffset);
	}
	function beginBoundaryDrag(event: PointerEvent, boundary: 'start' | 'end') {
		const timebar = (event.currentTarget as HTMLElement).closest('.custom-voice-timebar') as HTMLElement | null;
		if (!timebar) return;
		event.preventDefault();
		event.stopPropagation();
		trimEditing = true;
		const apply = (moveEvent: PointerEvent) => boundary === 'start' ? setTrimStart(timeFromPointer(moveEvent, timebar)) : setTrimEnd(timeFromPointer(moveEvent, timebar));
		const finish = () => { trimEditing = false; window.removeEventListener('pointermove', apply); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', finish); };
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', finish, { once: true });
		window.addEventListener('pointercancel', finish, { once: true });
	}
	function beginPlayheadDrag(event: PointerEvent) {
		const timebar = (event.currentTarget as HTMLElement).closest('.custom-voice-timebar') as HTMLElement | null;
		if (!timebar) return;
		event.preventDefault();
		event.stopPropagation();
		const apply = (moveEvent: PointerEvent) => { playbackPosition = timeFromPointer(moveEvent, timebar); if (audio && loopPreview) audio.currentTime = playbackPosition; };
		const finish = () => { window.removeEventListener('pointermove', apply); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', finish); };
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', finish, { once: true });
		window.addEventListener('pointercancel', finish, { once: true });
	}
	function stopPreview(reset = true) {
		if (frame !== null) cancelAnimationFrame(frame);
		frame = null;
		loopPreview = false;
		if (audio) { audio.pause(); if (reset) audio.currentTime = trimStart; }
		if (reset) playbackPosition = trimStart;
	}
	function trackPreview() {
		if (!audio || audio.paused) { stopPreview(false); return; }
		playbackPosition = Math.max(trimStart, Math.min(trimEnd, audio.currentTime));
		if (!trimEditing && audio.currentTime >= trimEnd - 0.01) {
			if (loopEnabled) { audio.currentTime = trimStart; playbackPosition = trimStart; void audio.play(); }
			else { stopPreview(); return; }
		}
		frame = requestAnimationFrame(trackPreview);
	}
	async function togglePreview() {
		if (!audio || !sourceUrl) return;
		if (loopPreview) { stopPreview(); return; }
		audio.currentTime = trimStart;
		playbackPosition = trimStart;
		loopPreview = true;
		try { await audio.play(); frame = requestAnimationFrame(trackPreview); } catch { loopPreview = false; }
	}
	function handleKeydown(event: KeyboardEvent) {
		if (!hotkeysActive || !sourceUrl) return;
		const target = event.target as HTMLElement | null;
		if (target && ['input','textarea','select'].includes(target.tagName.toLowerCase())) return;
		const key = event.key.toLowerCase();
		if (event.code === 'Space') { if (target?.closest('button,a')) return; event.preventDefault(); if (!event.repeat) void togglePreview(); }
		else if (key === 'i' && !event.repeat) { event.preventDefault(); setStartAtPlayhead(); }
		else if (key === 'o' && !event.repeat) { event.preventDefault(); setEndAtPlayhead(); }
		else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomTimeline(timelineZoom * 1.35); }
		else if (event.key === '-') { event.preventDefault(); zoomTimeline(timelineZoom / 1.35); }
	}
	function handleTrimFocusOut(event: FocusEvent) {
		const current = event.currentTarget as HTMLElement;
		trimFocusWithin = Boolean(event.relatedTarget && current.contains(event.relatedTarget as Node));
	}
	async function loadWaveform(url: string) {
		if (!url) { waveformBars = []; return; }
		waveformLoading = true;
		waveformProgress = 0;
		try {
			const response = await fetch(url);
			if (!response.ok) throw new Error(String(response.status));
			const context = new AudioContext();
			const buffer = await context.decodeAudioData(await response.arrayBuffer());
			const channel = buffer.getChannelData(0);
			const count = Math.max(2400, Math.min(180000, Math.ceil(buffer.duration * 60), Math.round(buffer.length / 2048)));
			const block = Math.max(1, Math.floor(channel.length / count));
			const raw = new Array<number>(count).fill(0);
			let max = 0.01;
			for (let index = 0; index < count; index += 1) {
				const end = Math.min(channel.length, (index + 1) * block);
				let peak = 0;
				for (let sample = index * block; sample < end; sample += 1) peak = Math.max(peak, Math.abs(channel[sample] ?? 0));
				raw[index] = peak;
				max = Math.max(max, peak);
			}
			await context.close();
			if (sourceUrl === url) { waveformBars = raw.map((value) => Math.max(0, Math.min(1, Math.pow(value / max, 0.72)))); waveformProgress = 1; }
		} catch { if (sourceUrl === url) waveformBars = []; }
		finally { if (sourceUrl === url) waveformLoading = false; }
	}

	$effect(() => { playbackPosition = Math.max(trimStart, Math.min(trimEnd, playbackPosition)); });
	$effect(() => { void loadWaveform(sourceUrl); return () => stopPreview(false); });
	onMount(() => {
		updateTimelineViewport();
		window.addEventListener('keydown', handleKeydown);
		return () => {
			window.removeEventListener('keydown', handleKeydown);
			stopPreview(false);
		};
	});
</script>

<audio bind:this={audio} src={sourceUrl} preload="metadata" onended={() => stopPreview()}></audio>
<div
	class="custom-voice-trimmer"
	class:hotkeys-active={hotkeysActive}
	role="group"
	aria-label={ariaLabel}
	onmouseenter={() => (trimHover = true)}
	onmouseleave={() => (trimHover = false)}
	onfocusin={() => (trimFocusWithin = true)}
	onfocusout={handleTrimFocusOut}
>
	<div class="custom-voice-trimmer-head">
		<div class="custom-voice-trim-readout" aria-live="polite">
			<span class="readout-chip readout-selection"><b>选区</b>{formatDuration(selectedDurationMs)}</span>
			<span class="readout-chip readout-in"><b>IN</b>{formatTimecode(trimStart)}</span>
			<span class="readout-chip readout-out"><b>OUT</b>{formatTimecode(trimEnd)}</span>
			<span class="readout-chip readout-current"><b>当前</b>{formatTimecode(playbackPosition)}</span>
			<span class="readout-chip readout-status" class:ok={matched && !dirty} class:warn={dirty}><b>处理</b>{dirty ? statusDirtyLabel : (matched ? statusReadyLabel : statusIdleLabel)}</span>
		</div>
		<div class="trim-transport-buttons">
			<button class="trim-icon-btn play" type="button" aria-label={loopPreview ? '停止选区播放' : '播放选区'} data-tooltip={loopPreview ? '停止并回到入点，快捷键 Space' : '从入点播放到出点，快捷键 Space'} onclick={togglePreview} disabled={!sourceUrl || selectedDurationMs < 100}>{#if loopPreview}<Square size={16} />{:else}<Play size={16} />{/if}</button>
			<button class="trim-loop-btn trim-icon-only" class:active={loopEnabled} type="button" aria-label={loopEnabled ? '关闭循环播放' : '开启循环播放'} data-tooltip={loopEnabled ? '循环播放已开启，点击关闭' : '循环播放已关闭，点击开启'} onclick={() => (loopEnabled = !loopEnabled)} disabled={!sourceUrl}><Repeat size={14} /></button>
			<div class="trim-zoom-buttons" aria-label="时间轴缩放">
				<button class="trim-tool-btn" type="button" aria-label="缩小时间轴" data-tooltip="缩小时间轴，快捷键 -" onclick={() => zoomTimeline(timelineZoom / 1.35)} disabled={!durationMs}>−</button>
				<span>{formatTimelineZoom(timelineZoom)}x</span>
				<button class="trim-tool-btn" type="button" aria-label="放大时间轴" data-tooltip="放大时间轴，快捷键 + / =" onclick={() => zoomTimeline(timelineZoom * 1.35)} disabled={!durationMs}>+</button>
			</div>
			<button class="trim-marker-btn trim-marker-in-btn trim-icon-only" type="button" aria-label="将当前指针设为入点" data-tooltip="将当前指针设为入点，快捷键 I" onclick={setStartAtPlayhead} disabled={!durationMs}><ChevronsLeft size={13} /></button>
			<button class="trim-marker-btn trim-marker-out-btn trim-icon-only" type="button" aria-label="将当前指针设为出点" data-tooltip="将当前指针设为出点，快捷键 O" onclick={setEndAtPlayhead} disabled={!durationMs}><ChevronsRight size={13} /></button>
			<button class="trim-marker-btn trim-icon-only" type="button" aria-label="重置为完整选区" data-tooltip="重置为完整选区" onclick={resetRange} disabled={!durationMs}><RotateCcw size={13} /></button>
			<button class="btn compact primary trim-apply-btn trim-icon-only" type="button" aria-label={applyAriaLabel} data-tooltip={applyTooltip} onclick={onApply} disabled={busy || !durationMs || selectedDurationMs < 100}><CircleCheck size={13} /></button>
			{#if showRegister}<button class="btn compact trim-inline-action trim-icon-only" type="button" aria-label="注册为音色" data-tooltip="把当前选区和台词保存到音色库" onclick={onRegister} disabled={busy || registerDisabled}><Plus size={13} /></button>{/if}
			{#if clearLabel}<button class="btn compact trim-inline-action trim-icon-only" type="button" aria-label={clearLabel} data-tooltip={clearTooltip || clearLabel} onclick={onClear} disabled={clearDisabled}><X size={13} /></button>{/if}
		</div>
	</div>
	<div class="custom-voice-editor-strip">
		<div bind:this={timebarWindow} class="custom-voice-timebar-window" role="region" aria-label="裁剪时间轴滚动窗口" onwheel={handleTimelineWheel} onscroll={(event) => updateTimelineViewport(event.currentTarget as HTMLDivElement)} onpointerenter={(event) => updateTimelineViewport(event.currentTarget as HTMLDivElement)}>
			<div class="custom-voice-timebar" class:panning={timelinePanning} role="group" aria-label={`${purposeLabel}裁切时间轴`} style={`--trim-start:${trimStartPercent}%;--trim-end:${trimEndPercent}%;--playhead:${playheadPercent}%;width:${timelineZoom * 100}%`} onpointerdown={handleTimebarPointer}>
				<div class="custom-voice-timebar-ruler" aria-hidden="true">{#each timelineTicks as tick}<span class:major={tick.major} style={`left:${tick.percent}%`}><i></i><b>{tick.label}</b></span>{/each}</div>
				<div class="custom-voice-timebar-track" aria-hidden="true"></div>
				<div class="custom-voice-waveform" class:loading={waveformLoading} style={`--waveform-progress:${Math.round(waveformProgress * 100)}%`} aria-hidden="true">
					{#if waveformBars.length}<svg class="custom-voice-waveform-svg" viewBox={`0 0 ${waveformBars.length} 100`} preserveAspectRatio="none"><line class="waveform-midline" x1="0" y1="50" x2={waveformBars.length} y2="50" />{#each visibleWaveformBars as bar}<rect x={bar.x + 0.1} y={50 - bar.level * 38} width={bar.width} height={Math.max(2, bar.level * 76)} rx="0.12" />{/each}</svg>{:else}<span class="waveform-empty"></span>{/if}
				</div>
				<div class="custom-voice-play-progress" aria-hidden="true"></div>
				<button type="button" class="trim-playhead-handle" aria-label="拖动当前播放指针" onpointerdown={beginPlayheadDrag}><span>当前</span></button>
				<button type="button" class="trim-handle-label trim-in-label" aria-label="拖动裁切入点" onpointerdown={(event) => beginBoundaryDrag(event, 'start')}><span>IN</span></button>
				<button type="button" class="trim-handle-label trim-out-label" aria-label="拖动裁切出点" onpointerdown={(event) => beginBoundaryDrag(event, 'end')}><span>OUT</span></button>
				<input aria-label="裁切入点" class="trim-range trim-start" type="range" min="0" max={durationSeconds} step="0.1" value={trimStart} onpointerdown={() => (trimEditing = true)} onpointerup={() => (trimEditing = false)} onpointercancel={() => (trimEditing = false)} oninput={(event) => setTrimStart((event.currentTarget as HTMLInputElement).value)} disabled={!durationMs} />
				<input aria-label="裁切出点" class="trim-range trim-end" type="range" min="0.1" max={durationSeconds} step="0.1" value={trimEnd} onpointerdown={() => (trimEditing = true)} onpointerup={() => (trimEditing = false)} onpointercancel={() => (trimEditing = false)} oninput={(event) => setTrimEnd((event.currentTarget as HTMLInputElement).value)} disabled={!durationMs} />
			</div>
		</div>
	</div>
</div>

<style>
	@import './ReferenceAudioRangeEditor.css';
	audio { display: none; }
</style>
