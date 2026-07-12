<script lang="ts">
	import type { VideoLocalizationTimelineClip } from '$lib/api/types';
	import ClipWaveform from './ClipWaveform.svelte';

	let {
		clip,
		audioSrc,
		label,
		tone,
		left,
		width,
		dragging = false,
		locked = false,
		startMs,
		endMs,
		sourceStartMs = 0,
		sourceEndMs = null,
		timelineDurationMs,
		timelineZoom = 1,
		timelineScrollLeft = 0,
		timelineViewportWidth = 0,
		onMove,
		onTrimStart,
		onTrimEnd,
		onDelete,
		onAnalysis
	}: {
		clip: VideoLocalizationTimelineClip;
		audioSrc: string;
		label: string;
		tone: 'source' | 'vocals' | 'music' | 'dub';
		left: number;
		width: number;
		dragging?: boolean;
		locked?: boolean;
		startMs: number;
		endMs: number;
		sourceStartMs?: number;
		sourceEndMs?: number | null;
		timelineDurationMs: number;
		timelineZoom?: number;
		timelineScrollLeft?: number;
		timelineViewportWidth?: number;
		onMove: (event: PointerEvent) => void;
		onTrimStart: (event: PointerEvent) => void;
		onTrimEnd: (event: PointerEvent) => void;
		onDelete: () => void;
		onAnalysis: (bars: number[], durationSeconds: number) => void;
	} = $props();
</script>

<div
	class="audio-clip tone-{tone}"
	class:dragging
	class:locked
	role="button"
	tabindex="0"
	style={`left:${left}%;width:${width}%`}
	onpointerdown={(event) => {
		if (!(event.target as HTMLElement).closest('.clip-label')) return;
		if (locked) { event.preventDefault(); event.stopPropagation(); return; }
		onMove(event);
	}}
	aria-label={`移动${label}片段 ${clip.clip_id}`}
	aria-disabled={locked}
>
	<ClipWaveform
		{audioSrc}
		{sourceStartMs}
		{sourceEndMs}
		{tone}
		{timelineZoom}
		{timelineScrollLeft}
		{timelineViewportWidth}
		{timelineDurationMs}
		clipStartMs={startMs}
		clipEndMs={endMs}
		{onAnalysis}
	/>
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
	<strong class="clip-label" data-tooltip={`移动${label}片段｜按住标题左右拖动，调整片段在时间线上的位置。`}>{label}</strong>
	<button class="clip-delete" type="button" aria-label={`删除${label}片段 ${clip.clip_id}`} data-tooltip={`删除${label}片段｜从当前轨道移除此片段，可使用撤销恢复。`} disabled={locked} onclick={(event) => { event.stopPropagation(); if (!locked) onDelete(); }}>×</button>
	<span
		class="clip-handle end"
		role="slider"
		tabindex="-1"
		aria-label={`裁切${label}片段出点`}
		aria-valuemin="0"
		aria-valuemax={timelineDurationMs}
		aria-valuenow={endMs}
		onpointerdown={(event) => { if (!locked) onTrimEnd(event); }}
	></span>
</div>

<style>
	.audio-clip {
		position: absolute;
		top: 6px;
		bottom: 6px;
		min-width: 12px;
		border: 1px solid color-mix(in srgb, var(--clip-color) 74%, #fff 8%);
		border-radius: 5px;
		padding: 4px 22px 4px 12px;
		background: color-mix(in srgb, var(--clip-color) 34%, #12181d);
		color: #eef7f8;
		overflow: hidden;
		cursor: crosshair;
		user-select: none;
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

	.audio-clip.locked {
		cursor: default;
	}

	.audio-clip.locked .clip-handle {
		display: none;
	}

	.audio-clip strong,
	.clip-delete {
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
		cursor: grab;
	}

	.clip-handle {
		position: absolute;
		top: 0;
		bottom: 0;
		z-index: 4;
		width: 8px;
		background: transparent;
		cursor: ew-resize;
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

	.clip-handle.start { left: 0; }
	.clip-handle.end { right: 0; }
	.audio-clip:hover .clip-handle::after,
	.audio-clip.dragging .clip-handle::after { background: #f4d36b; }

	.clip-delete {
		position: absolute;
		right: 9px;
		top: 50%;
		width: 15px;
		height: 15px;
		border: 0;
		border-radius: 50%;
		padding: 0;
		background: rgba(7, 10, 13, 0.55);
		color: #c8d2d7;
		font-size: 11px;
		line-height: 15px;
		transform: translateY(-50%);
		cursor: pointer;
		opacity: 0;
	}

	.audio-clip:hover .clip-delete,
	.clip-delete:focus-visible { opacity: 1; }
	.clip-delete:disabled { display: none; }
</style>
