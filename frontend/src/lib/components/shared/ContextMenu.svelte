<script lang="ts">
	import { tick } from 'svelte';
	import type { ContextMenuItem } from './context-menu';
	import { hoverTooltip } from './hover-tooltip';

	let {
		open,
		x,
		y,
		label,
		items,
		onClose
	}: {
		open: boolean;
		x: number;
		y: number;
		label: string;
		items: ContextMenuItem[];
		onClose: () => void;
	} = $props();

	let root: HTMLDivElement | null = $state(null);
	let position = $state({ x: 0, y: 0 });

	$effect(() => {
		if (!open) return;
		x;
		y;
		items;
		void positionAndFocus();
	});

	$effect(() => {
		if (!open) return;
		const close = () => onClose();
		window.addEventListener('resize', close);
		window.addEventListener('scroll', close, true);
		return () => {
			window.removeEventListener('resize', close);
			window.removeEventListener('scroll', close, true);
		};
	});

	async function positionAndFocus() {
		position = { x, y };
		await tick();
		if (!root) return;
		const rect = root.getBoundingClientRect();
		const pad = 8;
		position = {
			x: Math.max(pad, Math.min(x, window.innerWidth - rect.width - pad)),
			y: Math.max(pad, Math.min(y, window.innerHeight - rect.height - pad))
		};
		root.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true });
	}

	function closeOnOutsidePointer(event: PointerEvent) {
		if (!open || root?.contains(event.target as Node)) return;
		onClose();
	}

	function closeOnWindowKey(event: KeyboardEvent) {
		if (!open || event.key !== 'Escape') return;
		event.preventDefault();
		onClose();
	}

	function handleMenuKeydown(event: KeyboardEvent) {
		if (!root) return;
		const buttons = [...root.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
		if (!buttons.length) return;
		const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
		let next = current;
		if (event.key === 'ArrowDown') next = (current + 1 + buttons.length) % buttons.length;
		else if (event.key === 'ArrowUp') next = (current - 1 + buttons.length) % buttons.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = buttons.length - 1;
		else if (event.key === 'Tab') {
			onClose();
			return;
		} else return;
		event.preventDefault();
		buttons[next]?.focus({ preventScroll: true });
	}

	function selectItem(item: ContextMenuItem) {
		if (item.disabled) return;
		onClose();
		void item.onSelect();
	}
</script>

<svelte:window onpointerdown={closeOnOutsidePointer} onkeydown={closeOnWindowKey} />

{#if open}
	<div
		bind:this={root}
		class="context-menu"
		style={`left:${Math.round(position.x)}px;top:${Math.round(position.y)}px`}
		role="menu"
		aria-label={label}
		tabindex="-1"
		onkeydown={handleMenuKeydown}
		oncontextmenu={(event) => event.preventDefault()}
	>
		<div class="context-menu-items">
			{#each items as item (item.id)}
				{#if item.separatorBefore}<div class="context-menu-separator" role="separator"></div>{/if}
				<button
					class:danger={item.tone === 'danger'}
					type="button"
					role="menuitem"
						disabled={item.disabled}
						aria-label={item.description ? `${item.label}，${item.description}` : item.label}
						use:hoverTooltip={item.description}
						onclick={() => selectItem(item)}
				>
					{#if item.icon}
						{@const Icon = item.icon}
						<span class="context-menu-icon" aria-hidden="true"><Icon size={14} /></span>
					{/if}
					<span class="context-menu-copy">{item.label}</span>
				</button>
			{/each}
		</div>
	</div>
{/if}

<style>
	.context-menu {
		position: fixed;
		z-index: 180;
		width: min(196px, calc(100vw - 16px));
		border: 1px solid #3a454d;
		border-radius: 5px;
		background: rgba(18, 23, 27, 0.98);
		box-shadow: 0 12px 28px rgba(0, 0, 0, 0.46);
		overflow: hidden;
		transform-origin: top left;
		animation: context-menu-in 100ms cubic-bezier(0.2, 0.8, 0.2, 1);
	}

	.context-menu-items {
		display: grid;
		gap: 1px;
		padding: 3px;
	}

	.context-menu-items button {
		width: 100%;
		min-height: 30px;
		display: grid;
		grid-template-columns: 19px minmax(0, 1fr);
		align-items: center;
		gap: 5px;
		border: 0;
		border-radius: 5px;
		padding: 4px 7px;
		background: transparent;
		color: #dce5ea;
		text-align: left;
	}

	.context-menu-items button:hover:not(:disabled),
	.context-menu-items button:focus-visible {
		background: #1d282d;
		outline: none;
	}

	.context-menu-items button.danger {
		color: #ffb0ad;
	}

	.context-menu-items button.danger:hover:not(:disabled),
	.context-menu-items button.danger:focus-visible {
		background: rgba(126, 42, 43, 0.24);
	}

	.context-menu-items button:disabled {
		opacity: 0.42;
		cursor: not-allowed;
	}

	.context-menu-icon {
		display: grid;
		place-items: center;
		width: 19px;
		height: 19px;
	}

	.context-menu-copy {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 11px;
		font-weight: 560;
	}

	.context-menu-separator {
		height: 1px;
		margin: 3px 5px;
		background: #2d363d;
	}

	@keyframes context-menu-in {
		from { opacity: 0; transform: translateY(-4px) scale(0.98); }
		to { opacity: 1; transform: translateY(0) scale(1); }
	}
</style>
