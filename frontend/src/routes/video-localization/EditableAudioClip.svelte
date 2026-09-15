<script lang="ts">
	import type { VideoLocalizationTimelineClip } from '$lib/api/types';
	import ClipWaveform from './ClipWaveform.svelte';
	import { resolveVirtualizedClipRenderWindow } from './timeline-render-lod';

	let {
		clip,
		waveformSrc,
		waveformPreviewSrc = '',
		label,
		tone,
		gain = 1,
		left,
		width,
		dragging = false,
		selected = false,
		locked = false,
		processing = false,
		progress = null,
		coverage = typeof clip.verification_coverage === 'number' ? clip.verification_coverage : null,
		startMs,
		endMs,
		sourceStartMs = 0,
		sourceEndMs = null,
		renderStartMs = null,
		renderEndMs = null,
		showStartHandle = true,
		showEndHandle = true,
		timelineDurationMs,
		timelineZoom = 1,
		timelineScrollLeft = 0,
		timelineViewportWidth = 0,
		onMove,
		onSelect,
		onTrimStart,
		onTrimEnd,
		onAnalysis
	}: {
		clip: VideoLocalizationTimelineClip;
		waveformSrc: string;
		waveformPreviewSrc?: string;
		label: string;
		tone: 'source' | 'vocals' | 'music' | 'dub';
		gain?: number;
		left: number;
		width: number;
		dragging?: boolean;
		selected?: boolean;
		locked?: boolean;
		processing?: boolean;
		progress?: number | null;
		coverage?: number | null;
		startMs: number;
		endMs: number;
		sourceStartMs?: number;
		sourceEndMs?: number | null;
		renderStartMs?: number | null;
		renderEndMs?: number | null;
		showStartHandle?: boolean;
		showEndHandle?: boolean;
		timelineDurationMs: number;
		timelineZoom?: number;
		timelineScrollLeft?: number;
		timelineViewportWidth?: number;
		onMove: (event: PointerEvent) => void;
		onSelect: (event: PointerEvent) => void;
		onTrimStart: (event: PointerEvent) => void;
		onTrimEnd: (event: PointerEvent) => void;
		onAnalysis: (bars: number[], durationSeconds: number) => void;
	} = $props();
	const targetBindingStale = $derived(
		tone === 'dub' && clip.tts_target_binding_status === 'stale'
	);
	const virtualRender = $derived.by(() => {
		if (timelineViewportWidth <= 0 || timelineDurationMs <= 0) {
			return { startMs, endMs, showStartEdge: true, showEndEdge: true };
		}
		const contentWidth = Math.max(1, timelineViewportWidth) * Math.max(1, timelineZoom);
		const viewportStartMs = (Math.max(0, timelineScrollLeft) / contentWidth) * timelineDurationMs;
		const viewportEndMs = ((Math.max(0, timelineScrollLeft) + timelineViewportWidth) / contentWidth) * timelineDurationMs;
		return resolveVirtualizedClipRenderWindow({ clipStartMs: startMs, clipEndMs: endMs, viewportStartMs, viewportEndMs });
	});
	const effectiveRenderStartMs = $derived(renderStartMs ?? virtualRender.startMs);
	const effectiveRenderEndMs = $derived(renderEndMs ?? virtualRender.endMs);
	const effectiveShowStartHandle = $derived(showStartHandle && virtualRender.showStartEdge);
	const effectiveShowEndHandle = $derived(showEndHandle && virtualRender.showEndEdge);
	const renderedLeft = $derived((effectiveRenderStartMs / Math.max(1, timelineDurationMs)) * 100);
	const renderedWidth = $derived(((effectiveRenderEndMs - effectiveRenderStartMs) / Math.max(1, timelineDurationMs)) * 100);
</script>

<div
	class="audio-clip tone-{tone}"
	class:dragging
	class:selected
	class:locked
	class:processing
	class:virtual-start={!effectiveShowStartHandle}
	class:virtual-end={!effectiveShowEndHandle}
	class:target-binding-stale={targetBindingStale}
	data-audio-clip-id={clip.clip_id}
	data-render-start-ms={effectiveRenderStartMs}
	data-render-end-ms={effectiveRenderEndMs}
	role="button"
	tabindex="0"
	style={`left:${renderedLeft}%;width:${renderedWidth}%`}
	onpointerdown={(event) => {
		const target = event.target as HTMLElement;
		if (target.closest('.clip-handle')) return;
		(event.currentTarget as HTMLElement).focus({ preventScroll: true });
		if (event.button === 0 && (event.currentTarget as HTMLElement).closest('.razor-tool')) {
			event.preventDefault();
			return;
		}
		if (event.button === 0 && (event.ctrlKey || event.metaKey)) {
			event.preventDefault();
			event.stopPropagation();
			onSelect(event);
			return;
		}
		if (event.button === 0 && !selected) onSelect(event);
		if (locked) { event.preventDefault(); event.stopPropagation(); return; }
		onMove(event);
	}}
	aria-label={`移动${label}片段 ${clip.clip_id}${targetBindingStale ? '，字幕已修改，旧音频仍保留' : ''}`}
	aria-disabled={locked}
>
	<ClipWaveform
		{waveformSrc}
		previewWaveformSrc={waveformPreviewSrc}
		{sourceStartMs}
		{sourceEndMs}
		{tone}
		{gain}
		{timelineZoom}
		{timelineScrollLeft}
		{timelineViewportWidth}
		{timelineDurationMs}
		clipStartMs={startMs}
		clipEndMs={endMs}
		renderStartMs={effectiveRenderStartMs}
		renderEndMs={effectiveRenderEndMs}
		{onAnalysis}
	/>
	{#if effectiveShowStartHandle}
	<span
		class="clip-handle start"
		role="slider"
		tabindex="-1"
		aria-label={`裁切${label}片段入点`}
		aria-valuemin="0"
		aria-valuemax={timelineDurationMs}
		aria-valuenow={startMs}
		onpointerdown={(event) => { if (!locked) onTrimStart(event); }}
	></span>
	{/if}
	<strong class="clip-label" data-tooltip={tone === 'dub' ? `移动${label}片段｜左右拖动调整时间，上下拖动切换合成配音轨。` : `移动${label}片段｜按住标题左右拖动，调整片段在时间线上的位置。`}>{label}</strong>
	{#if targetBindingStale}
		<span class="clip-stale-binding" data-tooltip="关联字幕后来被修改；这段已剪辑音频仍按原位置保留，可试听后决定重新生成或删除。">字幕已改</span>
	{/if}
	{#if tone === 'dub' && coverage !== null && !(processing && progress !== null)}
		<span class="clip-coverage" class:low={coverage < 90} aria-label={`台词覆盖率 ${Math.round(Math.max(0, Math.min(100, coverage)))}%`}>{Math.round(Math.max(0, Math.min(100, coverage)))}%</span>
	{/if}
	{#if processing && progress !== null}
		<span class="clip-progress" aria-label={`生成进度 ${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%`}>{Math.round(Math.max(0, Math.min(1, progress)) * 100)}%</span>
	{/if}
	{#if effectiveShowEndHandle}<span
		class="clip-handle end"
		role="slider"
		tabindex="-1"
		aria-label={`裁切${label}片段出点`}
		aria-valuemin="0"
		aria-valuemax={timelineDurationMs}
		aria-valuenow={endMs}
		onpointerdown={(event) => { if (!locked) onTrimEnd(event); }}
	></span>{/if}
</div>

<style>
	.audio-clip {
		position: absolute;
		box-sizing: border-box;
		top: 6px;
		bottom: 6px;
		min-width: 0;
		border: 1px solid color-mix(in srgb, var(--clip-color) 74%, #fff 8%);
		border-radius: 5px;
		padding: 4px 0;
		background: color-mix(in srgb, var(--clip-color) 34%, #12181d);
		color: #eef7f8;
		overflow: visible;
		cursor: grab;
		user-select: none;
		white-space: nowrap;
	}

	.tone-source { --clip-color: #58d1c8; }
	.tone-vocals { --clip-color: #7da4ff; }
	.tone-music { --clip-color: #d9b45f; }
	.tone-dub { --clip-color: #9b87f5; }

	.audio-clip.dragging {
		border-color: #f4d36b;
		box-shadow: 0 0 0 2px rgba(244, 211, 107, 0.18);
		cursor: grabbing;
	}

	.audio-clip.selected {
		z-index: 6;
		border-color: #f4d36b;
		box-shadow: 0 0 0 1px rgba(244, 211, 107, 0.48), 0 5px 14px rgba(0, 0, 0, 0.24);
	}

	.audio-clip.virtual-start {
		border-left-color: transparent;
		border-top-left-radius: 0;
		border-bottom-left-radius: 0;
	}

	.audio-clip.virtual-end {
		border-right-color: transparent;
		border-top-right-radius: 0;
		border-bottom-right-radius: 0;
	}

	.audio-clip.locked {
		cursor: default;
	}

	.audio-clip.processing::after {
		content: "";
		position: absolute;
		inset: 0;
		z-index: 3;
		pointer-events: none;
		background: repeating-linear-gradient(115deg, transparent 0 9px, rgba(129, 205, 224, 0.17) 9px 15px, transparent 15px 24px);
		background-size: 42px 100%;
		animation: clip-processing 1.15s linear infinite;
	}

	.audio-clip.locked .clip-handle {
		display: none;
	}

	.audio-clip strong {
		position: relative;
		z-index: 2;
	}

	.audio-clip strong {
		display: inline-block;
		max-width: min(42%, 180px);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 10px;
		line-height: 16px;
	}

	.audio-clip .clip-label {
		cursor: inherit;
		margin: 0 12px;
		color: rgba(218, 225, 230, 0.64);
		font-size: 9px;
		font-weight: 500;
	}

	.clip-progress {
		position: absolute;
		right: 10px;
		top: 50%;
		z-index: 4;
		padding: 1px 4px;
		border-radius: 3px;
		background: rgba(10, 15, 19, 0.72);
		color: #c8e9f2;
		font-size: 9px;
		font-variant-numeric: tabular-nums;
		transform: translateY(-50%);
	}

	.clip-coverage {
		position: relative;
		z-index: 2;
		margin-left: 4px;
		color: rgba(201, 211, 216, 0.46);
		font-size: 8px;
		line-height: 16px;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	.clip-coverage.low { color: rgba(218, 184, 126, 0.68); }

	.clip-stale-binding {
		position: relative;
		z-index: 3;
		margin-left: 4px;
		padding: 1px 4px;
		border: 1px solid rgba(226, 180, 92, 0.42);
		border-radius: 3px;
		background: rgba(76, 52, 18, 0.62);
		color: #e7c985;
		font-size: 8px;
		line-height: 13px;
		white-space: nowrap;
	}

	.clip-handle {
		position: absolute;
		top: 50%;
		bottom: auto;
		z-index: 5;
		height: min(100%, 46px);
		width: 8px;
		background: transparent;
		cursor: ew-resize;
		opacity: 0;
		pointer-events: none;
	}

	.clip-handle::after {
		content: "";
		position: absolute;
		top: 7px;
		bottom: 7px;
		left: 50%;
		width: 2px;
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.48);
		transform: translateX(-50%);
	}

	.clip-handle.start {
		left: 0;
		transform: translate(-100%, -50%);
	}

	.clip-handle.start::after { left: 100%; }

	.clip-handle.end {
		right: 0;
		transform: translate(100%, -50%);
	}

	.clip-handle.end::after { left: 0; }

	.audio-clip.selected .clip-handle,
	.audio-clip.dragging .clip-handle {
		opacity: 1;
		pointer-events: auto;
	}
	.audio-clip:hover .clip-handle::after,
	.audio-clip.dragging .clip-handle::after { background: #f4d36b; }

	@keyframes clip-processing { to { background-position: 42px 0; } }
	@media (prefers-reduced-motion: reduce) { .audio-clip.processing::after { animation: none; } }
</style>
