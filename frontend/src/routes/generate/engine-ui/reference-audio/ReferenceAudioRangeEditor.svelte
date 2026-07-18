<script lang="ts">
	import { CircleCheck, Pause, Play, RotateCcw } from 'lucide-svelte';
	import { onMount } from 'svelte';

	interface Props {
		sourceUrl: string;
		durationMs: number;
		startMs: number;
		endMs: number;
		busy?: boolean;
		dirty?: boolean;
		applyLabel?: string;
		ariaLabel?: string;
		purposeLabel?: string;
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
		applyLabel = '使用这个片段',
		ariaLabel = '参考音频片段时间线',
		purposeLabel = '参考片段',
		onRangeChange = () => {},
		onApply = () => {}
	}: Props = $props();

	let audio: HTMLAudioElement;
	let timeline: HTMLDivElement;
	let peaks: number[] = $state([]);
	let loading = $state(false);
	let playing = $state(false);
	let playheadMs = $state(0);
	let frame: number | null = null;
	const safeDuration = $derived(Math.max(100, durationMs || 100));
	const selectedMs = $derived(Math.max(0, endMs - startMs));
	const startPercent = $derived((startMs / safeDuration) * 100);
	const endPercent = $derived((endMs / safeDuration) * 100);
	const playheadPercent = $derived((playheadMs / safeDuration) * 100);

	function format(ms: number) {
		const total = Math.max(0, ms) / 1000;
		const minutes = Math.floor(total / 60);
		const seconds = total - minutes * 60;
		return minutes ? `${minutes}:${seconds.toFixed(1).padStart(4, '0')}` : `${seconds.toFixed(1)} 秒`;
	}

	function setRange(nextStart: number, nextEnd: number) {
		const minimum = Math.min(100, safeDuration);
		const start = Math.max(0, Math.min(nextStart, safeDuration - minimum));
		const end = Math.min(safeDuration, Math.max(start + minimum, nextEnd));
		onRangeChange(Math.round(start), Math.round(end));
	}

	function reset() { setRange(0, safeDuration); }
	function pointerValue(event: PointerEvent) {
		const rect = timeline.getBoundingClientRect();
		const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0;
		return Math.round((Math.max(0, Math.min(1, ratio)) * safeDuration) / 100) * 100;
	}
	function setBoundary(boundary: 'start' | 'end', value: number) {
		if (boundary === 'start') setRange(value, endMs);
		else setRange(startMs, value);
	}
	function beginDrag(event: PointerEvent, boundary: 'start' | 'end') {
		event.preventDefault();
		stop();
		const apply = (moveEvent: PointerEvent) => setBoundary(boundary, pointerValue(moveEvent));
		const finish = () => {
			window.removeEventListener('pointermove', apply);
			window.removeEventListener('pointerup', finish);
			window.removeEventListener('pointercancel', finish);
		};
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', finish, { once: true });
		window.addEventListener('pointercancel', finish, { once: true });
	}
	function handleBoundaryKey(event: KeyboardEvent, boundary: 'start' | 'end') {
		const current = boundary === 'start' ? startMs : endMs;
		const step = event.shiftKey ? 1000 : 100;
		let next = current;
		if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next -= step;
		else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next += step;
		else if (event.key === 'Home') next = boundary === 'start' ? 0 : startMs + 100;
		else if (event.key === 'End') next = boundary === 'start' ? endMs - 100 : safeDuration;
		else return;
		event.preventDefault();
		setBoundary(boundary, next);
	}
	function stop() {
		if (frame !== null) cancelAnimationFrame(frame);
		frame = null;
		playing = false;
		if (audio) audio.pause();
	}
	function track() {
		if (!audio || audio.paused) { stop(); return; }
		playheadMs = audio.currentTime * 1000;
		if (playheadMs >= endMs) { stop(); playheadMs = startMs; audio.currentTime = startMs / 1000; return; }
		frame = requestAnimationFrame(track);
	}
	async function toggle() {
		if (!audio || !sourceUrl) return;
		if (playing) { stop(); playheadMs = startMs; audio.currentTime = startMs / 1000; return; }
		audio.currentTime = startMs / 1000;
		playheadMs = startMs;
		try { await audio.play(); playing = true; frame = requestAnimationFrame(track); } catch { playing = false; }
	}

	async function loadPeaks(url: string) {
		if (!url) { peaks = []; return; }
		loading = true;
		try {
			const response = await fetch(url);
			if (!response.ok) throw new Error(String(response.status));
			const context = new AudioContext();
			const buffer = await context.decodeAudioData(await response.arrayBuffer());
			const channel = buffer.getChannelData(0);
			const count = 120;
			const block = Math.max(1, Math.floor(channel.length / count));
			const next = Array.from({ length: count }, (_, index) => {
				let peak = 0;
				const end = Math.min(channel.length, (index + 1) * block);
				for (let i = index * block; i < end; i += Math.max(1, Math.floor(block / 48))) peak = Math.max(peak, Math.abs(channel[i]));
				return Math.max(0.04, Math.min(1, peak));
			});
			await context.close();
			if (sourceUrl === url) peaks = next;
		} catch { if (sourceUrl === url) peaks = []; }
		finally { if (sourceUrl === url) loading = false; }
	}

	$effect(() => { void loadPeaks(sourceUrl); return () => stop(); });
	onMount(() => () => stop());
</script>

<div class="range-editor" role="group" aria-label={ariaLabel}>
	<audio bind:this={audio} src={sourceUrl} preload="metadata" onended={() => { playing = false; playheadMs = startMs; }}></audio>
	<div class="editor-head" aria-live="polite">
		<span><b>选区</b>{format(selectedMs)}</span>
		<span><b>IN</b>{format(startMs)}</span>
		<span><b>OUT</b>{format(endMs)}</span>
		{#if dirty}<em>片段已调整，需重新应用</em>{/if}
	</div>
	<div bind:this={timeline} class="timeline" style={`--start:${startPercent}%;--end:${endPercent}%;--playhead:${playheadPercent}%`}>
		<div class="waveform" aria-hidden="true">
			{#if peaks.length}
				{#each peaks as peak}<i style={`height:${Math.round(peak * 82)}%`}></i>{/each}
			{:else}<span>{loading ? '正在读取波形…' : '波形暂不可用，仍可拖动范围'}</span>{/if}
		</div>
		<div class="shade before"></div><div class="shade after"></div><div class="selection"></div><div class="playhead"></div>
		<div class="range-handle start" role="slider" tabindex="0" aria-label={`${purposeLabel}入点`} aria-valuemin="0" aria-valuemax={Math.max(0, endMs - 100)} aria-valuenow={startMs} aria-valuetext={format(startMs)} onpointerdown={(event) => beginDrag(event, 'start')} onkeydown={(event) => handleBoundaryKey(event, 'start')}><span>IN</span></div>
		<div class="range-handle end" role="slider" tabindex="0" aria-label={`${purposeLabel}出点`} aria-valuemin={Math.min(safeDuration, startMs + 100)} aria-valuemax={safeDuration} aria-valuenow={endMs} aria-valuetext={format(endMs)} onpointerdown={(event) => beginDrag(event, 'end')} onkeydown={(event) => handleBoundaryKey(event, 'end')}><span>OUT</span></div>
	</div>
	<div class="editor-actions">
		<button type="button" onclick={toggle} disabled={!sourceUrl || selectedMs < 100}>{#if playing}<Pause size={14} />停止{:else}<Play size={14} />试听选区{/if}</button>
		<button type="button" onclick={reset} disabled={!durationMs}><RotateCcw size={14} />完整范围</button>
		<button class="primary" type="button" onclick={onApply} disabled={busy || !sourceUrl || selectedMs < 100}><CircleCheck size={14} />{busy ? '处理中…' : applyLabel}</button>
	</div>
</div>

<style>
	.range-editor { display: grid; gap: 8px; min-width: 0; }
	audio { display: none; }
	.editor-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; color: var(--muted); font-size: 10.5px; }
	.editor-head span { display: inline-flex; gap: 5px; border: 1px solid color-mix(in srgb, var(--line) 82%, transparent); border-radius: 5px; padding: 3px 6px; }
	.editor-head b { color: #9fc8ff; font-size: 9px; }
	.editor-head em { margin-left: auto; color: #e6c27a; font-style: normal; }
	.timeline { position: relative; min-width: 0; height: 76px; overflow: hidden; border: 1px solid #34475c; border-radius: 7px; background: #091019; }
	.waveform { position: absolute; inset: 10px 7px; display: flex; align-items: center; gap: 1px; color: var(--muted); }
	.waveform i { flex: 1 1 0; min-width: 1px; border-radius: 1px; background: #6688aa; opacity: .82; }
	.waveform span { margin: auto; font-size: 10px; }
	.shade, .selection, .playhead { position: absolute; top: 0; bottom: 0; pointer-events: none; }
	.shade { background: rgba(2, 5, 9, .64); }
	.shade.before { left: 0; width: var(--start); }
	.shade.after { left: var(--end); right: 0; }
	.selection { left: var(--start); width: calc(var(--end) - var(--start)); border: 1px solid #66a8f4; background: rgba(79, 156, 249, .1); }
	.playhead { left: var(--playhead); width: 1px; background: #f4d27f; }
	.range-handle { position: absolute; z-index: 4; top: 0; bottom: 0; width: 24px; border-inline: 2px solid #86bbf6; cursor: ew-resize; touch-action: none; transform: translateX(-50%); }
	.range-handle.start { left: clamp(12px, var(--start), calc(100% - 12px)); }
	.range-handle.end { left: clamp(12px, var(--end), calc(100% - 12px)); border-color: #8edbc0; }
	.range-handle span { position: absolute; top: 4px; left: 50%; transform: translateX(-50%); border-radius: 3px; background: #1b2d40; padding: 1px 3px; color: #dcecff; font-size: 8px; font-weight: 700; }
	.range-handle.end span { background: #17352f; }
	.range-handle:focus-visible { outline: 2px solid #f4d27f; outline-offset: -3px; }
	.timeline:focus-within { outline: 2px solid color-mix(in srgb, var(--accent) 50%, transparent); outline-offset: 2px; }
	.editor-actions { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
	button { min-height: 32px; display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel-2); padding: 5px 8px; color: var(--text); font: inherit; font-size: 11px; cursor: pointer; }
	button.primary { border-color: color-mix(in srgb, var(--accent) 66%, var(--line)); background: color-mix(in srgb, var(--accent) 18%, var(--panel-2)); }
	button:disabled { cursor: not-allowed; opacity: .45; }
	@media (max-width: 640px) { .timeline { height: 92px; } .editor-head em { width: 100%; margin-left: 0; } button { min-height: 44px; } .editor-actions { justify-content: stretch; } .editor-actions button { flex: 1 1 auto; justify-content: center; } }
	@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
