<script lang="ts">
	import { onMount } from 'svelte';

	let {
		text,
		gap = 24
	}: {
		text: string;
		gap?: number;
	} = $props();

	let viewport: HTMLElement | undefined;
	let primaryCopy: HTMLElement | undefined;
	let resizeObserver: ResizeObserver | undefined;
	let measureFrame = 0;
	let isOverflowing = $state(false);
	let scrollDistance = $state(0);
	let scrollDuration = $state(0);

	function measure() {
		if (!viewport || !primaryCopy) return;
		const textWidth = primaryCopy.scrollWidth;
		const nextOverflowing = textWidth > viewport.clientWidth + 1;
		isOverflowing = nextOverflowing;
		scrollDistance = textWidth + gap;
		scrollDuration = Math.max(3600, scrollDistance * 32);
	}

	function scheduleMeasure() {
		if (measureFrame) cancelAnimationFrame(measureFrame);
		measureFrame = requestAnimationFrame(() => {
			measureFrame = 0;
			measure();
		});
	}

	$effect(() => {
		text;
		scheduleMeasure();
	});

	onMount(() => {
		resizeObserver = new ResizeObserver(scheduleMeasure);
		if (viewport) resizeObserver.observe(viewport);
		if (primaryCopy) resizeObserver.observe(primaryCopy);
		scheduleMeasure();

		return () => {
			if (measureFrame) cancelAnimationFrame(measureFrame);
			resizeObserver?.disconnect();
		};
	});
</script>

<span
	class="hover-marquee"
	class:is-overflowing={isOverflowing}
	bind:this={viewport}
	style={`--hover-marquee-gap:${gap}px;--hover-marquee-distance:${scrollDistance}px;--hover-marquee-duration:${scrollDuration}ms`}
>
	<span class="hover-marquee-track">
		<span class="hover-marquee-copy" bind:this={primaryCopy}>{text}</span>
		{#if isOverflowing}
			<span class="hover-marquee-copy" aria-hidden="true">{text}</span>
		{/if}
	</span>
</span>

<style>
	.hover-marquee {
		display: block;
		width: 100%;
		min-width: 0;
		overflow: hidden;
		white-space: nowrap;
	}

	.hover-marquee.is-overflowing:not(:hover) {
		-webkit-mask-image: linear-gradient(90deg, #000 0, #000 calc(100% - 12px), transparent 100%);
		mask-image: linear-gradient(90deg, #000 0, #000 calc(100% - 12px), transparent 100%);
	}

	.hover-marquee-track {
		width: max-content;
		display: flex;
		align-items: center;
		gap: var(--hover-marquee-gap);
		transform: translateX(0);
		will-change: transform;
	}

	.hover-marquee.is-overflowing:hover .hover-marquee-track {
		animation: hover-marquee-loop var(--hover-marquee-duration) linear infinite;
		animation-delay: 300ms;
	}

	.hover-marquee-copy {
		flex: 0 0 auto;
	}

	@keyframes hover-marquee-loop {
		from { transform: translateX(0); }
		to { transform: translateX(calc(-1 * var(--hover-marquee-distance))); }
	}

	@media (prefers-reduced-motion: reduce) {
		.hover-marquee.is-overflowing:hover .hover-marquee-track {
			animation: none;
		}
	}
</style>
