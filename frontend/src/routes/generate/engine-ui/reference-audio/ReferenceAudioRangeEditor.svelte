<script module lang="ts">
	let activeReferenceTimelineHotkeyOwner: symbol | null = null;
</script>

<script lang="ts">
	import {
		SelectionPlaybackController,
		type SelectionPlaybackCommand,
		type SelectionRange
	} from '$lib/audio/selection-playback-controller';
	import {
		cachedWaveform,
		requestWaveform,
		waveformUrlWithBins,
		type WaveformPeaksResponse
	} from '$lib/audio/waveform-peaks-client';
	import {
		waveformBinsForPixels,
		waveformPreviewBins
	} from '$lib/audio/waveform-lod';
	import { buildVisibleWaveformBars } from '$lib/audio/waveform';
	import { ChevronsLeft, ChevronsRight, CircleCheck, Pause, Play, Plus, Repeat, RotateCcw, X } from 'lucide-svelte';
	import { onMount } from 'svelte';
	import { buildReferenceTimelineTicks, maximumReferenceTimelineZoom } from './timeline-viewport';

	interface Props {
		sourceUrl: string;
		waveformUrl?: string;
		durationMs: number;
		startMs: number;
		endMs: number;
		busy?: boolean;
		dirty?: boolean;
		matched?: boolean;
		ariaLabel?: string;
		purposeLabel?: string;
		focusKey?: string;
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
		waveformUrl = '',
		durationMs,
		startMs,
		endMs,
		busy = false,
		dirty = false,
		matched = false,
		ariaLabel = '裁切选区。将鼠标移入或聚焦时间线后，空格播放或暂停；I 设置入点，O 设置出点',
		purposeLabel = '参考音频',
		focusKey = '',
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
	let loopEnabled = $state(false);
	let trimEditing = $state(false);
	let timelineHover = $state(false);
	let timelineFocusWithin = $state(false);
	let frame: number | null = null;
	let waveformLoadSequence = 0;
	let focusedSelectionKey = '';
	const playbackController = new SelectionPlaybackController();
	const hotkeyOwnerId = Symbol('reference-audio-timeline');

	const durationSeconds = $derived(Math.max(0.1, durationMs / 1000));
	const trimStart = $derived(Math.max(0, Math.min(durationSeconds, startMs / 1000)));
	const trimEnd = $derived(Math.max(trimStart + 0.1, Math.min(durationSeconds, endMs / 1000)));
	const selectedDurationMs = $derived(Math.max(0, Math.round((trimEnd - trimStart) * 1000)));
	const trimStartPercent = $derived((trimStart / durationSeconds) * 100);
	const trimEndPercent = $derived((trimEnd / durationSeconds) * 100);
	const playheadPercent = $derived((playbackPosition / durationSeconds) * 100);
	const timelineTicks = $derived.by(() => buildReferenceTimelineTicks(durationSeconds, timelineZoom, timelineViewportWidth || 900));
	const visibleWaveformBars = $derived.by(() => buildVisibleWaveformBars(waveformBars, timelineZoom, timelineScrollLeft, timelineViewportWidth));
	const requestedWaveformBins = $derived.by(() => {
		const viewportWidth = Math.max(900, timelineViewportWidth || 0);
		const pixelRatio = typeof window === 'undefined' ? 1 : window.devicePixelRatio;
		return waveformBinsForPixels(viewportWidth * Math.max(1, timelineZoom), pixelRatio);
	});
	const hotkeysActive = $derived(timelineHover || timelineFocusWithin);

	function selectionRange(): SelectionRange {
		return { start: trimStart, end: trimEnd };
	}
	function currentSourcePosition() {
		const position = audio?.currentTime;
		return Number.isFinite(position) ? Math.max(0, position) : playbackPosition;
	}
	function sourceIsPlaying() {
		return Boolean(audio && !audio.paused && !audio.ended);
	}
	function cancelPreviewTracking() {
		if (frame !== null) cancelAnimationFrame(frame);
		frame = null;
	}
	function schedulePreviewTracking() {
		if (frame === null) frame = requestAnimationFrame(trackPreview);
	}
	function applyPlaybackCommand(command: SelectionPlaybackCommand) {
		playbackPosition = Math.max(0, Math.min(durationSeconds, command.position));
		loopPreview = command.phase !== 'stopped';
		if (!audio) return;
		if (command.shouldPause && !audio.paused) audio.pause();
		if (command.seekTo !== null && Math.abs(audio.currentTime - command.seekTo) > 0.001) {
			audio.currentTime = command.seekTo;
		}
	}
	async function continuePlayback(command: SelectionPlaybackCommand) {
		applyPlaybackCommand(command);
		if (!audio || !command.shouldPlay) return;
		try {
			await audio.play();
			schedulePreviewTracking();
		} catch {
			applyPlaybackCommand(playbackController.pauseAt(currentSourcePosition()));
		}
	}

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
	function formatTimelineZoom(value: number) { return value < 10 ? value.toFixed(1) : value.toFixed(0); }
	function updateTimelineViewport(element = timebarWindow) {
		if (!element) return;
		timelineScrollLeft = element.scrollLeft;
		timelineViewportWidth = element.clientWidth;
	}
	function zoomTimeline(nextZoom: number, anchorRatio?: number, anchorOffset?: number) {
		const element = timebarWindow;
		const ratio = anchorRatio ?? ((trimStart + trimEnd) / 2) / durationSeconds;
		const offset = anchorOffset ?? (element?.clientWidth ?? 0) / 2;
		timelineZoom = Math.max(1, Math.min(maximumReferenceTimelineZoom(durationSeconds), Math.round(nextZoom * 10) / 10));
		requestAnimationFrame(() => {
			if (!element) return;
			element.scrollLeft = Math.max(0, ratio * element.scrollWidth - offset);
			updateTimelineViewport(element);
		});
	}
	function focusSelection() {
		const element = timebarWindow;
		if (!element || durationSeconds <= 0 || trimEnd <= trimStart) return;
		const selectionDuration = trimEnd - trimStart;
		const visibleDuration = Math.min(durationSeconds, Math.max(8, selectionDuration * 1.8));
		const nextZoom = Math.max(1, Math.min(maximumReferenceTimelineZoom(durationSeconds), durationSeconds / visibleDuration));
		const centerRatio = ((trimStart + trimEnd) / 2) / durationSeconds;
		timelineZoom = Math.round(nextZoom * 10) / 10;
		requestAnimationFrame(() => {
			element.scrollLeft = Math.max(0, centerRatio * element.scrollWidth - element.clientWidth / 2);
			updateTimelineViewport(element);
		});
	}
	function setRange(startSeconds: number, endSeconds: number) {
		const start = Math.max(0, Math.min(startSeconds, durationSeconds - 0.1));
		const end = Math.min(durationSeconds, Math.max(start + 0.1, endSeconds));
		onRangeChange(Math.round(start * 1000), Math.round(end * 1000));
		if (!trimEditing) {
			applyPlaybackCommand(
				playbackController.selectionChanged(
					currentSourcePosition(),
					{ start, end },
					loopEnabled,
					sourceIsPlaying()
				)
			);
		}
	}
	function setTrimStart(value: string | number) {
		const next = Number(value);
		if (Number.isFinite(next)) setRange(next, trimEnd);
	}
	function setTrimEnd(value: string | number) {
		const next = Number(value);
		if (Number.isFinite(next)) setRange(trimStart, next);
	}
	function resetRange() { setRange(0, durationSeconds); }
	function setStartAtPlayhead() { setRange(playbackPosition, trimEnd); }
	function setEndAtPlayhead() { setRange(trimStart, playbackPosition); }
	function focusTimeline() {
		timebarWindow?.focus({ preventScroll: true });
	}
	function timeFromPointer(event: PointerEvent, timebar: HTMLElement) {
		const rect = timebar.getBoundingClientRect();
		return Math.round(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * durationSeconds * 10) / 10;
	}
	function handleTimebarPointer(event: PointerEvent) {
		focusTimeline();
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
			if (!dragged) {
				dragged = true;
				beginSelectionEdit();
			}
			const current = timeFromPointer(moveEvent, timebar);
			setRange(Math.min(anchor, current), Math.max(anchor, current));
		};
		const finish = () => {
			window.removeEventListener('pointermove', apply);
			window.removeEventListener('pointerup', finish);
			window.removeEventListener('pointercancel', finish);
			if (dragged) finishSelectionEdit();
			else setPlayheadPosition(anchor);
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
		const element = event.currentTarget as HTMLElement;
		if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
			event.preventDefault();
			element.scrollLeft += Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
			updateTimelineViewport(element as HTMLDivElement);
			return;
		}
		event.preventDefault();
		const rect = element.getBoundingClientRect();
		const anchorOffset = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
		const anchorRatio = (element.scrollLeft + anchorOffset) / Math.max(1, element.scrollWidth);
		zoomTimeline(timelineZoom * (event.deltaY < 0 ? 1.16 : 1 / 1.16), anchorRatio, anchorOffset);
	}
	function beginSelectionEdit() {
		if (trimEditing) return;
		const command = playbackController.beginSelectionEdit(currentSourcePosition(), sourceIsPlaying());
		trimEditing = true;
		applyPlaybackCommand(command);
	}
	function finishSelectionEdit() {
		if (!trimEditing) return;
		trimEditing = false;
		const command = playbackController.finishSelectionEdit(
			currentSourcePosition(),
			selectionRange(),
			loopEnabled,
			sourceIsPlaying()
		);
		applyPlaybackCommand(command);
		if (command.phase === 'playing') schedulePreviewTracking();
	}
	function beginBoundaryDrag(event: PointerEvent, boundary: 'start' | 'end') {
		const timebar = (event.currentTarget as HTMLElement).closest('.custom-voice-timebar') as HTMLElement | null;
		if (!timebar) return;
		event.preventDefault();
		event.stopPropagation();
		focusTimeline();
		beginSelectionEdit();
		const apply = (moveEvent: PointerEvent) => boundary === 'start' ? setTrimStart(timeFromPointer(moveEvent, timebar)) : setTrimEnd(timeFromPointer(moveEvent, timebar));
		const finish = () => { finishSelectionEdit(); window.removeEventListener('pointermove', apply); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', finish); };
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
		focusTimeline();
		const apply = (moveEvent: PointerEvent) => setPlayheadPosition(timeFromPointer(moveEvent, timebar));
		const finish = () => { window.removeEventListener('pointermove', apply); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', finish); };
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', finish, { once: true });
		window.addEventListener('pointercancel', finish, { once: true });
	}
	function setPlayheadPosition(position: number) {
		const bounded = Math.max(0, Math.min(durationSeconds, position));
		playbackPosition = bounded;
		if (audio && sourceIsPlaying()) audio.currentTime = bounded;
		applyPlaybackCommand(playbackController.seekDuringPlayback(bounded, selectionRange(), loopEnabled));
	}
	function handlePlayheadKeydown(event: KeyboardEvent) {
		let nextPosition: number | null = null;
		const step = event.shiftKey ? 1 : 0.1;
		if (event.key === 'ArrowLeft') nextPosition = playbackPosition - step;
		else if (event.key === 'ArrowRight') nextPosition = playbackPosition + step;
		else if (event.key === 'Home') nextPosition = trimStart;
		else if (event.key === 'End') nextPosition = trimEnd;
		if (nextPosition === null) return;
		event.preventDefault();
		event.stopPropagation();
		setPlayheadPosition(nextPosition);
	}
	function stopPreview(reset = true) {
		cancelPreviewTracking();
		if (reset) applyPlaybackCommand(playbackController.stop(selectionRange()));
		else {
			if (audio && !audio.paused) audio.pause();
			applyPlaybackCommand(playbackController.pauseAt(currentSourcePosition()));
		}
	}
	function trackPreview() {
		frame = null;
		if (!audio) return;
		if (audio.paused) {
			if (!audio.ended) applyPlaybackCommand(playbackController.pauseAt(currentSourcePosition()));
			return;
		}
		applyPlaybackCommand(playbackController.tick(audio.currentTime, selectionRange(), loopEnabled));
		schedulePreviewTracking();
	}
	async function togglePreview() {
		if (!audio || !sourceUrl) return;
		if (loopPreview) { stopPreview(false); return; }
		await continuePlayback(
			playbackController.start(selectionRange(), loopEnabled, playbackPosition)
		);
	}
	function toggleLoop() {
		loopEnabled = !loopEnabled;
		applyPlaybackCommand(
			playbackController.setLoopEnabled(loopEnabled, currentSourcePosition(), selectionRange())
		);
	}
	function handleSourceEnded() {
		cancelPreviewTracking();
		const command = playbackController.sourceEnded(currentSourcePosition(), selectionRange(), loopEnabled);
		if (command.shouldPlay) void continuePlayback(command);
		else applyPlaybackCommand(command);
	}
	function handleKeydown(event: KeyboardEvent) {
		if (
			activeReferenceTimelineHotkeyOwner !== hotkeyOwnerId
			|| !hotkeysActive
			|| !sourceUrl
			|| event.repeat
			|| event.altKey
			|| event.ctrlKey
			|| event.metaKey
		) return;
		const target = event.target as HTMLElement | null;
		const targetTag = target?.tagName.toLowerCase();
		const targetInput = targetTag === 'input' ? target as HTMLInputElement : null;
		if (
			target
			&& (
				targetTag === 'textarea'
				|| targetTag === 'select'
				|| Boolean(targetInput && targetInput.type !== 'range')
				|| target.isContentEditable
				|| Boolean(target.closest('[role="dialog"]'))
				|| (!timelineHover && Boolean(target.closest('button,a,[role="button"]')))
			)
		) return;
		const key = event.key.toLowerCase();
		if (event.code === 'Space') { event.preventDefault(); void togglePreview(); }
		else if (key === 'i') { event.preventDefault(); setStartAtPlayhead(); }
		else if (key === 'o') { event.preventDefault(); setEndAtPlayhead(); }
		else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomTimeline(timelineZoom * 1.35); }
		else if (event.key === '-') { event.preventDefault(); zoomTimeline(timelineZoom / 1.35); }
	}
	function activateTimelineHotkeys() {
		activeReferenceTimelineHotkeyOwner = hotkeyOwnerId;
	}
	function releaseTimelineHotkeysIfInactive() {
		if (
			!timelineHover
			&& !timelineFocusWithin
			&& activeReferenceTimelineHotkeyOwner === hotkeyOwnerId
		) activeReferenceTimelineHotkeyOwner = null;
	}
	function handleTimelineFocusOut(event: FocusEvent) {
		const current = event.currentTarget as HTMLElement;
		timelineFocusWithin = Boolean(event.relatedTarget && current.contains(event.relatedTarget as Node));
		releaseTimelineHotkeysIfInactive();
	}
	function normalizedWaveformBars(payload: WaveformPeaksResponse) {
		let max = 0.01;
		for (const value of payload.peaks) {
			max = Math.max(max, Number(value) || 0);
		}
		return payload.peaks.map((value) =>
			Math.max(0, Math.min(1, Math.pow(Number(value) / max, 0.72)))
		);
	}
	function applyWaveformPayload(payload: WaveformPeaksResponse, sequence: number, signal: AbortSignal) {
		if (sequence !== waveformLoadSequence || signal.aborted) return false;
		waveformBars = normalizedWaveformBars(payload);
		waveformProgress = 1;
		return true;
	}
	async function loadManagedWaveform(baseUrl: string, detailBins: number, sequence: number, signal: AbortSignal) {
		const detailUrl = waveformUrlWithBins(baseUrl, detailBins);
		const previewBins = waveformPreviewBins(detailBins);
		const previewUrl = waveformUrlWithBins(baseUrl, previewBins);
		const cachedDetail = cachedWaveform(detailUrl);
		if (cachedDetail) {
			applyWaveformPayload(cachedDetail, sequence, signal);
			waveformLoading = false;
			return;
		}
		const cachedPreview = cachedWaveform(previewUrl);
		if (cachedPreview) applyWaveformPayload(cachedPreview, sequence, signal);
		waveformLoading = !cachedPreview && !waveformBars.length;
		waveformProgress = cachedPreview ? 0.7 : 0.08;
		try {
			if (!cachedPreview) {
				const preview = await requestWaveform(previewUrl, signal);
				if (!applyWaveformPayload(preview, sequence, signal)) return;
			}
			if (detailUrl === previewUrl) return;
			await new Promise<void>((resolve) => {
				let settled = false;
				const finish = () => {
					if (settled) return;
					settled = true;
					clearTimeout(timer);
					signal.removeEventListener('abort', finish);
					resolve();
				};
				const timer = setTimeout(finish, 180);
				signal.addEventListener('abort', finish, { once: true });
			});
			if (signal.aborted || sequence !== waveformLoadSequence) return;
			const detail = await requestWaveform(detailUrl, signal);
			applyWaveformPayload(detail, sequence, signal);
		} catch (error) {
			if (!signal.aborted && sequence === waveformLoadSequence) {
				console.warn('Reference waveform request failed', { url: baseUrl, error });
			}
		} finally {
			if (!signal.aborted && sequence === waveformLoadSequence) waveformLoading = false;
		}
	}
	async function loadWaveformFromAudio(url: string, sequence: number) {
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
			if (sourceUrl === url && sequence === waveformLoadSequence) { waveformBars = raw.map((value) => Math.max(0, Math.min(1, Math.pow(value / max, 0.72)))); waveformProgress = 1; }
		} catch { if (sourceUrl === url && sequence === waveformLoadSequence) waveformBars = []; }
		finally { if (sourceUrl === url && sequence === waveformLoadSequence) waveformLoading = false; }
	}

	$effect(() => {
		const url = sourceUrl;
		const peaksUrl = waveformUrl;
		const sequence = ++waveformLoadSequence;
		playbackController.reset(0);
		playbackPosition = 0;
		loopPreview = false;
		waveformBars = [];
		waveformProgress = 0;
		timelineZoom = 1;
		if (timebarWindow) timebarWindow.scrollLeft = 0;
		timelineScrollLeft = 0;
		if (!peaksUrl) void loadWaveformFromAudio(url, sequence);
		return () => stopPreview(false);
	});
	$effect(() => {
		const baseUrl = waveformUrl;
		const detailBins = requestedWaveformBins;
		if (!baseUrl) return;
		const sequence = ++waveformLoadSequence;
		const controller = new AbortController();
		void loadManagedWaveform(baseUrl, detailBins, sequence, controller.signal);
		return () => controller.abort();
	});
	$effect(() => {
		const nextFocusKey = focusKey || sourceUrl;
		if (!nextFocusKey || !durationMs || endMs <= startMs || nextFocusKey === focusedSelectionKey) return;
		focusedSelectionKey = nextFocusKey;
		requestAnimationFrame(focusSelection);
	});
	onMount(() => {
		updateTimelineViewport();
		const resizeObserver = new ResizeObserver(() => updateTimelineViewport());
		resizeObserver.observe(timebarWindow);
		window.addEventListener('keydown', handleKeydown);
		return () => {
			resizeObserver.disconnect();
			window.removeEventListener('keydown', handleKeydown);
			if (activeReferenceTimelineHotkeyOwner === hotkeyOwnerId) {
				activeReferenceTimelineHotkeyOwner = null;
			}
			stopPreview(false);
		};
	});
</script>

<audio bind:this={audio} src={sourceUrl} preload="metadata" onended={handleSourceEnded}></audio>
<div
	class="custom-voice-trimmer"
		class:hotkeys-active={hotkeysActive}
		role="group"
		aria-label={ariaLabel}
	>
		<div class="custom-voice-trimmer-head">
			<div class="custom-voice-trim-readout">
				<span class="readout-chip readout-selection"><b>选区</b>{formatDuration(selectedDurationMs)}</span>
				<span class="readout-chip readout-in"><b>IN</b>{formatTimecode(trimStart)}</span>
				<span class="readout-chip readout-out"><b>OUT</b>{formatTimecode(trimEnd)}</span>
				<span class="readout-chip readout-current"><b>当前</b>{formatTimecode(playbackPosition)}</span>
				<span class="readout-chip readout-status" class:ok={matched && !dirty} class:warn={dirty} aria-live="polite"><b>处理</b>{dirty ? statusDirtyLabel : (matched ? statusReadyLabel : statusIdleLabel)}</span>
			</div>
			<div class="trim-transport-buttons">
				<button class="trim-icon-btn play" type="button" aria-label={loopPreview ? '暂停选区播放' : '播放选区'} data-tooltip={loopPreview ? '暂停在当前位置，快捷键 Space' : '从当前指针播放到出点；指针在选区外时从入点开始，快捷键 Space'} onclick={togglePreview} disabled={!sourceUrl || selectedDurationMs < 100}>{#if loopPreview}<Pause size={16} />{:else}<Play size={16} />{/if}</button>
			<button class="trim-loop-btn trim-icon-only" class:active={loopEnabled} type="button" aria-label={loopEnabled ? '关闭循环播放' : '开启循环播放'} data-tooltip={loopEnabled ? '循环播放已开启，点击关闭' : '循环播放已关闭，点击开启'} onclick={toggleLoop} disabled={!sourceUrl}><Repeat size={14} /></button>
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
		<!-- svelte-ignore a11y_no_noninteractive_tabindex (the timeline is a composite editor with scoped keyboard commands) -->
			<div
				bind:this={timebarWindow}
				class="custom-voice-timebar-window"
				role="application"
				tabindex="0"
				aria-label="裁剪时间轴滚动窗口；空格播放或暂停，滚轮缩放，Shift 加滚轮横向移动，I 设置入点，O 设置出点"
				aria-keyshortcuts="Space I O = -"
				onwheel={handleTimelineWheel}
				onpointerdown={focusTimeline}
				onscroll={(event) => updateTimelineViewport(event.currentTarget as HTMLDivElement)}
				onpointerenter={(event) => { timelineHover = true; activateTimelineHotkeys(); updateTimelineViewport(event.currentTarget as HTMLDivElement); }}
				onpointerleave={() => { timelineHover = false; releaseTimelineHotkeysIfInactive(); }}
				onfocusin={() => { timelineFocusWithin = true; activateTimelineHotkeys(); }}
				onfocusout={handleTimelineFocusOut}
			>
			<div class="custom-voice-timebar" class:panning={timelinePanning} role="group" aria-label={`${purposeLabel}裁切时间轴`} style={`--trim-start:${trimStartPercent}%;--trim-end:${trimEndPercent}%;--playhead:${playheadPercent}%;width:${timelineZoom * 100}%`} onpointerdown={handleTimebarPointer}>
				<div class="custom-voice-timebar-ruler" aria-hidden="true">{#each timelineTicks as tick}<span class:major={tick.major} style={`left:${tick.percent}%`}><i></i><b>{tick.label}</b></span>{/each}</div>
				<div class="custom-voice-timebar-track" aria-hidden="true"></div>
				<div class="custom-voice-waveform" class:loading={waveformLoading} style={`--waveform-progress:${Math.round(waveformProgress * 100)}%`} aria-hidden="true">
					{#if waveformBars.length}<svg class="custom-voice-waveform-svg" viewBox={`0 0 ${waveformBars.length} 100`} preserveAspectRatio="none"><line class="waveform-midline" x1="0" y1="50" x2={waveformBars.length} y2="50" />{#each visibleWaveformBars as bar}<rect x={bar.x + 0.1} y={50 - bar.level * 38} width={bar.width} height={Math.max(2, bar.level * 76)} rx="0.12" />{/each}</svg>{:else}<span class="waveform-empty"></span>{/if}
				</div>
				<div class="custom-voice-play-progress" aria-hidden="true"></div>
				<button type="button" class="trim-playhead-handle" aria-label="当前播放指针；左右方向键调整，Shift 加左右方向键大步调整，Home 到入点，End 到出点" onkeydown={handlePlayheadKeydown} onpointerdown={beginPlayheadDrag}><span>当前</span></button>
				<button type="button" class="trim-handle-label trim-in-label" aria-label="拖动裁切入点" onpointerdown={(event) => beginBoundaryDrag(event, 'start')}><span>IN</span></button>
				<button type="button" class="trim-handle-label trim-out-label" aria-label="拖动裁切出点" onpointerdown={(event) => beginBoundaryDrag(event, 'end')}><span>OUT</span></button>
				<input aria-label="裁切入点" class="trim-range trim-start" type="range" min="0" max={durationSeconds} step="0.1" value={trimStart} onpointerdown={beginSelectionEdit} onpointerup={finishSelectionEdit} onpointercancel={finishSelectionEdit} onchange={finishSelectionEdit} oninput={(event) => setTrimStart((event.currentTarget as HTMLInputElement).value)} disabled={!durationMs} />
				<input aria-label="裁切出点" class="trim-range trim-end" type="range" min="0.1" max={durationSeconds} step="0.1" value={trimEnd} onpointerdown={beginSelectionEdit} onpointerup={finishSelectionEdit} onpointercancel={finishSelectionEdit} onchange={finishSelectionEdit} oninput={(event) => setTrimEnd((event.currentTarget as HTMLInputElement).value)} disabled={!durationMs} />
			</div>
		</div>
	</div>
</div>

<style>
	@import './ReferenceAudioRangeEditor.css';
	audio { display: none; }
</style>
