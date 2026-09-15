<script lang="ts">
	import { onDestroy } from 'svelte';
	import { formatTimecode, snapToFrame } from './subtitle-workbench';

	let {
		label,
		value,
		min = 0,
		max = Number.MAX_SAFE_INTEGER,
		frameRate = 24,
		disabled = false,
		onPreview = undefined,
		onCommit
	}: {
		label: string;
		value: number;
		min?: number;
		max?: number;
		frameRate?: number;
		disabled?: boolean;
		onPreview?: (value: number) => void;
		onCommit: (value: number) => void | Promise<void>;
	} = $props();

	let stopActiveScrub: (() => void) | null = null;
	let scrubbing = $state(false);
	let previewValue = $state(0);
	const displayValue = $derived(scrubbing ? previewValue : value);

	$effect(() => {
		if (!scrubbing) previewValue = value;
	});

	function clamp(next: number) {
		const snapped = snapToFrame(next, frameRate);
		const lower = snapToFrame(min, frameRate);
		const upper = Math.max(lower, snapToFrame(max, frameRate));
		return Math.max(lower, Math.min(upper, snapped));
	}

	function beginScrub(event: PointerEvent) {
		if (disabled) return;
		event.preventDefault();
		event.stopPropagation();
		const startX = event.clientX;
		const startValue = value;
		let lastValue = clamp(value);
		let moved = false;
		scrubbing = true;
		previewValue = lastValue;

		const cleanup = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
			window.removeEventListener('pointercancel', stop);
			stopActiveScrub = null;
			scrubbing = false;
		};
		const move = (moveEvent: PointerEvent) => {
			const delta = moveEvent.clientX - startX;
			if (Math.abs(delta) >= 2) moved = true;
			if (!moved) return;
			const millisecondsPerPixel = moveEvent.shiftKey ? 2 : moveEvent.altKey ? 24 : 12;
			lastValue = clamp(startValue + delta * millisecondsPerPixel);
			previewValue = lastValue;
			onPreview?.(lastValue);
		};
		const stop = () => {
			cleanup();
			if (moved) void onCommit(lastValue);
		};

		stopActiveScrub?.();
		stopActiveScrub = cleanup;
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
		window.addEventListener('pointercancel', stop, { once: true });
	}

	function adjustWithKeyboard(event: KeyboardEvent) {
		if (disabled || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
		event.preventDefault();
		event.stopPropagation();
		const frameDurationMs = 1000 / Math.max(1, frameRate);
		const direction = event.key === 'ArrowRight' ? 1 : -1;
		const frameCount = event.shiftKey ? 10 : 1;
		void onCommit(clamp(value + direction * frameCount * frameDurationMs));
	}

	onDestroy(() => stopActiveScrub?.());
</script>

<div class="time-field">
	<span>{label}</span>
	<button
		class="time-scrub-button"
		type="button"
		{disabled}
		aria-label={`${label} ${formatTimecode(displayValue, frameRate)}`}
		data-tooltip={`${label}｜左右拖动调整；方向键每次一帧，Shift + 方向键每次十帧。`}
		onpointerdown={beginScrub}
		onkeydown={adjustWithKeyboard}
	>
		<strong>{formatTimecode(displayValue, frameRate)}</strong>
		<small>帧</small>
	</button>
</div>

<style>
	.time-field {
		display: grid;
		gap: 4px;
		min-width: 0;
	}

	.time-field > span {
		color: var(--muted);
		font-size: 10px;
	}

	.time-scrub-button {
		display: flex;
		align-items: center;
		justify-content: flex-start;
		gap: 3px;
		width: 100%;
		min-width: 0;
		height: 32px;
		box-sizing: border-box;
		border: 0;
		border-radius: 0;
		padding: 0 1px;
		background: transparent;
		color: var(--text);
		font-variant-numeric: tabular-nums;
		cursor: ew-resize;
	}

	.time-scrub-button:hover:not(:disabled),
	.time-scrub-button:focus-visible {
		color: #8bf1e7;
		outline: none;
	}

	.time-scrub-button:disabled {
		cursor: not-allowed;
		opacity: 0.54;
	}

	.time-scrub-button strong {
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 11px;
		font-weight: 720;
		letter-spacing: 0;
	}

	.time-scrub-button small {
		color: #7f8d95;
		font-size: 9px;
		line-height: 1;
	}
</style>
