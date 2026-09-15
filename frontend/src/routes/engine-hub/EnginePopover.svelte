<script lang="ts">
	import { ChevronDown } from 'lucide-svelte';
	import { onMount, type Snippet } from 'svelte';

	let {
		id,
		label,
		size = 'compact',
		children
	}: {
		id: string;
		label: string;
		size?: 'compact' | 'wide';
		children?: Snippet;
	} = $props();

	let root: HTMLDivElement | null = $state(null);
	let trigger: HTMLButtonElement | null = $state(null);
	let open = $state(false);
	let closeTimer: number | null = null;

	function cancelScheduledClose() {
		if (closeTimer === null) return;
		window.clearTimeout(closeTimer);
		closeTimer = null;
	}

	function close(restoreFocus = false) {
		cancelScheduledClose();
		open = false;
		if (restoreFocus) trigger?.focus({ preventScroll: true });
	}

	function toggle() {
		if (open) {
			close();
			return;
		}
		open = true;
		window.dispatchEvent(new CustomEvent('engine-popover-open', { detail: id }));
	}

	function schedulePointerClose() {
		cancelScheduledClose();
		closeTimer = window.setTimeout(() => close(), 180);
	}

	function closeOnOutsidePointer(event: PointerEvent) {
		if (!open || root?.contains(event.target as Node)) return;
		close();
	}

	function closeOnEscape(event: KeyboardEvent) {
		if (!open || event.key !== 'Escape') return;
		event.preventDefault();
		close(true);
	}

	onMount(() => {
		const closeOnViewportChange = () => close();
		const closeOnPeerOpen = (event: Event) => {
			if ((event as CustomEvent<string>).detail !== id) close();
		};
		window.addEventListener('resize', closeOnViewportChange);
		window.addEventListener('scroll', closeOnViewportChange, true);
		window.addEventListener('engine-popover-open', closeOnPeerOpen);
		return () => {
			cancelScheduledClose();
			window.removeEventListener('resize', closeOnViewportChange);
			window.removeEventListener('scroll', closeOnViewportChange, true);
			window.removeEventListener('engine-popover-open', closeOnPeerOpen);
		};
	});
</script>

<svelte:window onpointerdown={closeOnOutsidePointer} onkeydown={closeOnEscape} />

<div
	class="engine-popover"
	class:wide={size === 'wide'}
	bind:this={root}
	role="group"
	onpointerenter={cancelScheduledClose}
	onpointerleave={schedulePointerClose}
	onfocusout={(event) => {
		if (root?.contains(event.relatedTarget as Node)) return;
		close();
	}}
>
	<button
		bind:this={trigger}
		class="btn mini-btn popover-trigger"
		class:open
		type="button"
		aria-expanded={open}
		aria-controls={`${id}-panel`}
		aria-haspopup="dialog"
		onclick={toggle}
	><ChevronDown size={13} /> {label}</button>
	{#if open}
		<div
			id={`${id}-panel`}
			class="popover-panel"
			role="dialog"
			tabindex="-1"
			aria-label={`${label}面板`}
		>
			{@render children?.()}
		</div>
	{/if}
</div>

<style>
	.engine-popover { position: relative; }
	.popover-trigger { min-height: 26px; padding: 3px 7px; gap: 4px; border-radius: 6px; font-size: 10px; line-height: 1.1; }
	.popover-trigger :global(svg) { transition: transform 120ms ease; }
	.popover-trigger.open { border-color: #46505d; background: #252a31; color: #e5ebf2; }
	.popover-trigger.open :global(svg) { transform: rotate(180deg); }
	.popover-panel {
		position: absolute;
		z-index: 30;
		top: calc(100% + 6px);
		right: 0;
		display: grid;
		gap: 8px;
		width: min(290px, calc(100vw - 32px));
		padding: 10px;
		border: 1px solid #343a43;
		border-radius: 8px;
		background: #1a1d22;
		box-shadow: 0 14px 38px rgba(0, 0, 0, .46), 0 1px 0 rgba(255, 255, 255, .025) inset;
		animation: popover-in 100ms cubic-bezier(.2, .8, .2, 1);
	}
	.wide .popover-panel { width: min(520px, calc(100vw - 32px)); }
	@keyframes popover-in { from { opacity: 0; transform: translateY(-3px); } to { opacity: 1; transform: translateY(0); } }
	@media (max-width: 760px) {
		.popover-panel { right: auto; left: 0; width: min(320px, calc(100vw - 32px)); }
		.wide .popover-panel { width: min(520px, calc(100vw - 32px)); }
	}
	@media (prefers-reduced-motion: reduce) {
		.popover-trigger :global(svg) { transition: none; }
		.popover-panel { animation: none; }
	}
</style>
