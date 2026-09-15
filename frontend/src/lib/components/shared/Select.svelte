<script lang="ts">
	import { ChevronDown } from 'lucide-svelte';
	import { tick } from 'svelte';
	import { selectMenuPlacement, type SelectMenuPlacement } from './select-menu';

	type SelectOption = { label: string; value: string };
	type Props = {
		value?: string;
		options?: SelectOption[];
		searchable?: boolean;
		scrollBlock?: ScrollLogicalPosition;
		menuWidth?: number;
		placeholder?: string;
		onChange?: (value: string) => void;
	};

	let {
		value = $bindable(''),
		options = [],
		searchable = false,
		scrollBlock = 'nearest',
		menuWidth = 300,
		placeholder = '请选择',
		onChange = () => {}
	}: Props = $props();

	let open = $state(false);
	let query = $state('');
	let root: HTMLDivElement | undefined = $state();
	let searchInput: HTMLInputElement | undefined = $state();
	let selectedOptionEl: HTMLButtonElement | undefined = $state();
	let menuPlacement: SelectMenuPlacement = $state('start');

	const selected = $derived(options.find((option) => option.value === value) ?? null);
	const filteredOptions = $derived(
		query.trim()
			? options.filter((option) => `${option.label} ${option.value}`.toLowerCase().includes(query.trim().toLowerCase()))
			: options
	);

	async function toggle() {
		open = !open;
		if (!open) return;
		query = '';
		updateMenuPlacement();
		await tick();
		searchInput?.focus({ preventScroll: true });
		selectedOptionEl = root?.querySelector<HTMLButtonElement>('.select-option.selected') ?? undefined;
		selectedOptionEl?.scrollIntoView({ block: scrollBlock });
	}

	function updateMenuPlacement() {
		if (!root) return;
		menuPlacement = selectMenuPlacement(root.getBoundingClientRect(), window.innerWidth, menuWidth);
	}

	function choose(nextValue: string) {
		value = nextValue;
		onChange(nextValue);
		open = false;
	}

	function closeOnOutsideClick(event: MouseEvent) {
		if (!open || !root || root.contains(event.target as Node)) return;
		open = false;
	}
</script>

<svelte:window onclick={closeOnOutsideClick} onresize={() => open && updateMenuPlacement()} />

<div class="select" bind:this={root}>
	<button class="select-trigger" type="button" aria-haspopup="listbox" aria-expanded={open} onclick={toggle}>
		<span class:placeholder={!selected} title={selected?.label ?? placeholder}>{selected?.label ?? placeholder}</span>
		<span class="chevron" aria-hidden="true"><ChevronDown size={14} /></span>
	</button>

	{#if open}
		<div
			class="select-menu"
			class:align-end={menuPlacement === 'end'}
			style={`--select-menu-width: ${menuWidth}px`}
		>
			{#if searchable}
				<input bind:this={searchInput} class="select-search" bind:value={query} placeholder="搜索选项" autocomplete="off" />
			{/if}

			<div class="select-options menu-scroll-region" role="listbox" aria-label={placeholder}>
				{#each filteredOptions as option}
					<button
						class="select-option"
						class:selected={option.value === value}
						data-selected={option.value === value}
						type="button"
						role="option"
						aria-selected={option.value === value}
						title={option.label}
						onclick={() => choose(option.value)}
					>
						<span>{option.label}</span>
					</button>
				{:else}
					<div class="select-empty">无匹配选项</div>
				{/each}
			</div>
		</div>
	{/if}
</div>

<style>
	.select {
		position: relative;
		width: 100%;
	}

	.select-trigger {
		width: 100%;
		min-height: 34px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
		color: var(--text);
		padding: 8px 10px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		text-align: left;
	}

	.select-trigger span:first-child {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}

	.placeholder {
		color: var(--muted);
	}

	.chevron {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 14px;
		height: 14px;
		flex: 0 0 14px;
		color: var(--muted);
		line-height: 1;
	}

	.select-menu {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		right: auto;
		z-index: 30;
		width: min(calc(100vw - 24px), max(100%, var(--select-menu-width)));
		max-width: calc(100vw - 24px);
		border: 1px solid var(--line);
		border-radius: 9px;
		background: var(--panel);
		box-shadow: 0 18px 38px rgba(0, 0, 0, 0.34);
		padding: 6px;
	}

	.select-menu.align-end {
		left: auto;
		right: 0;
	}

	.select-search {
		margin-bottom: 6px;
	}

	.select-options {
		max-height: 220px;
		overflow-y: auto;
		overflow-x: hidden;
		overscroll-behavior: contain;
		display: grid;
		gap: 2px;
	}

	.select-option {
		width: 100%;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--text);
		padding: 8px 9px;
		text-align: left;
	}

	.select-option span {
		display: block;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.select-option:hover,
	.select-option.selected {
		background: var(--panel-2);
	}

	.select-option.selected {
		color: #9cc9ff;
	}

	.select-empty {
		padding: 12px 9px;
		color: var(--muted);
		font-size: 12px;
		text-align: center;
	}
</style>
