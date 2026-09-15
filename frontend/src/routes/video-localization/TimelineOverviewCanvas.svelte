<script lang="ts">
	import ClipWaveform from './ClipWaveform.svelte';

	type TimelineOverviewItem = {
		id?: string;
		clip_id?: string;
		start_ms?: number | null;
		end_ms?: number | null;
		source_start_ms?: number | null;
		source_end_ms?: number | null;
		status?: string | null;
	};

	type OverviewTone = 'asr' | 'localized' | 'source' | 'vocals' | 'music' | 'dub';

	let {
		items,
		durationMs,
		tone,
		muted = false,
		gain = 1,
		waveformSrc = '',
		waveformItem = null,
		ariaLabel = '时间线概览，可点击选择片段',
		selectedItemIds = [],
		onSelect
	}: {
		items: TimelineOverviewItem[];
		durationMs: number;
		tone: OverviewTone;
		muted?: boolean;
		gain?: number;
		waveformSrc?: string;
		waveformItem?: TimelineOverviewItem | null;
		ariaLabel?: string;
		selectedItemIds?: string[];
		onSelect?: (item: TimelineOverviewItem, event: MouseEvent | KeyboardEvent) => void;
	} = $props();

	let hostEl: HTMLDivElement;
	let canvasEl: HTMLCanvasElement;
	let hostWidth = $state(0);
	let hostHeight = $state(0);
	let drawFrame = 0;
	const selectedIds = $derived(new Set(selectedItemIds));

	const colors: Record<OverviewTone, { fill: string; edge: string }> = {
		asr: { fill: 'rgba(125, 164, 255, 0.58)', edge: 'rgba(182, 204, 255, 0.76)' },
		localized: { fill: 'rgba(173, 140, 255, 0.58)', edge: 'rgba(214, 197, 255, 0.76)' },
		source: { fill: 'rgba(88, 209, 200, 0.58)', edge: 'rgba(164, 239, 233, 0.76)' },
		vocals: { fill: 'rgba(125, 164, 255, 0.58)', edge: 'rgba(182, 204, 255, 0.76)' },
		music: { fill: 'rgba(217, 180, 95, 0.58)', edge: 'rgba(244, 216, 151, 0.76)' },
		dub: { fill: 'rgba(155, 135, 245, 0.62)', edge: 'rgba(207, 195, 255, 0.8)' }
	};

	$effect(() => {
		if (!hostEl) return;
		const updateSize = () => {
			hostWidth = hostEl.clientWidth;
			hostHeight = hostEl.clientHeight;
		};
		updateSize();
		const observer = new ResizeObserver(updateSize);
		observer.observe(hostEl);
		return () => observer.disconnect();
	});

	$effect(() => {
		items;
		durationMs;
		tone;
		muted;
		hostWidth;
		hostHeight;
		selectedItemIds;
		cancelAnimationFrame(drawFrame);
		drawFrame = requestAnimationFrame(draw);
		return () => cancelAnimationFrame(drawFrame);
	});

	function draw() {
		if (!canvasEl || hostWidth <= 0 || hostHeight <= 0 || durationMs <= 0) return;
		const pixelRatio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
		const bitmapWidth = Math.max(1, Math.ceil(hostWidth * pixelRatio));
		const bitmapHeight = Math.max(1, Math.ceil(hostHeight * pixelRatio));
		if (canvasEl.width !== bitmapWidth) canvasEl.width = bitmapWidth;
		if (canvasEl.height !== bitmapHeight) canvasEl.height = bitmapHeight;

		const context = canvasEl.getContext('2d');
		if (!context) return;
		context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
		context.clearRect(0, 0, hostWidth, hostHeight);
		context.globalAlpha = muted ? 0.32 : 1;

		const palette = colors[tone];
		const top = Math.max(4, Math.min(7, hostHeight * 0.16));
		const height = Math.max(4, hostHeight - top * 2);
		for (const item of items) {
			const startMs = finiteTime(item.start_ms);
			const endMs = finiteTime(item.end_ms);
			if (startMs === null || endMs === null || endMs <= startMs) continue;
			const left = Math.max(0, Math.min(hostWidth, (startMs / durationMs) * hostWidth));
			const right = Math.max(left, Math.min(hostWidth, (endMs / durationMs) * hostWidth));
			const width = Math.max(1, right - left);
			const selected = selectedIds.has(item.clip_id ?? item.id ?? '');
			context.fillStyle = selected ? palette.edge : palette.fill;
			context.fillRect(left, top, width, height);
			context.fillStyle = palette.edge;
			context.fillRect(left, top, Math.min(selected ? 2 : 1, width), height);
		}
		context.globalAlpha = 1;
	}

	function finiteTime(value: number | null | undefined) {
		const numeric = Number(value);
		return Number.isFinite(numeric) ? numeric : null;
	}

	function waveformTone(value: OverviewTone) {
		return value === 'source' || value === 'vocals' || value === 'music' || value === 'dub'
			? value
			: null;
	}

	function waveformStyle(item: TimelineOverviewItem) {
		const startMs = Math.max(0, finiteTime(item.start_ms) ?? 0);
		const endMs = Math.max(startMs, finiteTime(item.end_ms) ?? durationMs);
		const left = durationMs > 0 ? (startMs / durationMs) * 100 : 0;
		const width = durationMs > 0 ? ((endMs - startMs) / durationMs) * 100 : 0;
		return `left:${left}%;width:${width}%`;
	}

	function itemStyle(item: TimelineOverviewItem) {
		const startMs = Math.max(0, finiteTime(item.start_ms) ?? 0);
		const endMs = Math.max(startMs, finiteTime(item.end_ms) ?? startMs);
		const left = durationMs > 0 ? (startMs / durationMs) * 100 : 0;
		const width = durationMs > 0 ? ((endMs - startMs) / durationMs) * 100 : 0;
		return `left:${left}%;width:max(4px,${width}%)`;
	}

	function itemAriaLabel(item: TimelineOverviewItem) {
		const id = item.clip_id ?? item.id ?? '片段';
		return `选择${id}`;
	}
</script>

<div
	class="timeline-overview"
	class:selectable={Boolean(onSelect)}
	data-timeline-overview-canvas={tone}
	bind:this={hostEl}
	role={onSelect ? 'group' : undefined}
	aria-label={onSelect ? ariaLabel : undefined}
	aria-hidden={onSelect ? undefined : 'true'}
>
	<canvas bind:this={canvasEl}></canvas>
	{#if waveformSrc && waveformItem && waveformTone(tone)}
		<div
			class="overview-waveform"
			class:muted
			style={waveformStyle(waveformItem)}
			data-overview-waveform={tone}
		>
			<ClipWaveform
				{waveformSrc}
				sourceStartMs={finiteTime(waveformItem.source_start_ms) ?? 0}
				sourceEndMs={finiteTime(waveformItem.source_end_ms)}
				tone={waveformTone(tone) ?? 'source'}
				{gain}
				clipStartMs={0}
				clipEndMs={Math.max(
					1,
					(finiteTime(waveformItem.end_ms) ?? durationMs)
						- (finiteTime(waveformItem.start_ms) ?? 0)
				)}
			/>
		</div>
	{/if}
	{#if onSelect}
		{#each items as item, index (`${item.clip_id ?? item.id ?? 'item'}-${index}`)}
			<button
				class="overview-item-hit"
				class:selected={selectedIds.has(item.clip_id ?? item.id ?? '')}
				type="button"
				style={itemStyle(item)}
				data-audio-clip-id={item.clip_id}
				data-subtitle-item-id={item.id}
				aria-label={itemAriaLabel(item)}
				aria-pressed={selectedIds.has(item.clip_id ?? item.id ?? '')}
				onpointerdown={(event) => event.stopPropagation()}
				onclick={(event) => onSelect?.(item, event)}
			></button>
		{/each}
	{/if}
</div>

<style>
	.timeline-overview {
		position: absolute;
		inset: 0;
		overflow: hidden;
		pointer-events: none;
		contain: strict;
	}

	.timeline-overview.selectable {
		pointer-events: none;
	}

	.overview-item-hit {
		position: absolute;
		top: 0;
		bottom: 0;
		z-index: 2;
		border: 0;
		padding: 0;
		background: transparent;
		pointer-events: auto;
		cursor: pointer;
	}

	.overview-item-hit:focus-visible {
		outline: 2px solid rgba(182, 204, 255, 0.92);
		outline-offset: -2px;
	}

	.overview-item-hit.selected {
		box-shadow: inset 0 0 0 2px rgba(238, 244, 255, 0.96);
		background: rgba(238, 244, 255, 0.14);
	}

	canvas {
		display: block;
		width: 100%;
		height: 100%;
	}

	.overview-waveform {
		position: absolute;
		top: 0;
		bottom: 0;
		min-width: 1px;
	}

	.overview-waveform.muted {
		opacity: 0.32;
	}
</style>
