<script lang="ts">
	type Props = {
		content?: string | null;
	};

	let { content = '' }: Props = $props();
	let open = $state(false);
	let pinned = $state(false);
	let root: HTMLSpanElement | undefined = $state();

	function show() {
		if (!content) return;
		open = true;
	}

	function hide() {
		if (pinned) return;
		open = false;
	}

	function togglePinned() {
		if (!content) return;
		pinned = !pinned;
		open = pinned || open;
	}

	function closeOnOutsideClick(event: MouseEvent) {
		if (!open || !root || root.contains(event.target as Node)) return;
		pinned = false;
		open = false;
	}
</script>

<svelte:window onclick={closeOnOutsideClick} />

<span class="tooltip" bind:this={root}>
	<button class="tooltip-icon" type="button" aria-label="查看说明" aria-expanded={open} onmouseenter={show} onmouseleave={hide} onfocus={show} onblur={hide} onclick={togglePinned}>ⓘ</button>
	{#if open && content}
		<span class="tooltip-pop" role="tooltip">{content}</span>
	{/if}
</span>

<style>
	.tooltip {
		position: relative;
		display: inline-flex;
		align-items: center;
	}

	.tooltip-icon {
		width: 18px;
		height: 18px;
		border: 0;
		border-radius: 50%;
		background: transparent;
		color: var(--muted);
		padding: 0;
		line-height: 18px;
		font-size: 13px;
	}

	.tooltip-icon:hover,
	.tooltip-icon:focus-visible {
		color: var(--accent);
	}

	.tooltip-pop {
		position: absolute;
		left: 50%;
		bottom: calc(100% + 8px);
		z-index: 40;
		width: max-content;
		max-width: min(300px, 72vw);
		transform: translateX(-50%);
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 10px;
		background: rgba(12, 15, 20, 0.94);
		color: #eef3fb;
		padding: 9px 10px;
		font-size: 11.5px;
		line-height: 1.55;
		white-space: pre-line;
		word-break: break-word;
		box-shadow: 0 16px 34px rgba(0, 0, 0, 0.34);
	}

	.tooltip-pop::after {
		content: '';
		position: absolute;
		left: 50%;
		top: 100%;
		width: 8px;
		height: 8px;
		background: rgba(12, 15, 20, 0.94);
		border-right: 1px solid rgba(255, 255, 255, 0.08);
		border-bottom: 1px solid rgba(255, 255, 255, 0.08);
		transform: translate(-50%, -4px) rotate(45deg);
	}
</style>
