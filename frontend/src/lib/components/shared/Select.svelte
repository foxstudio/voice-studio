<script lang="ts">
	import { tick } from 'svelte';

	type SelectOption = { label: string; value: string };
	type Props = {
		value?: string;
		options?: SelectOption[];
		searchable?: boolean;
		scrollBlock?: ScrollLogicalPosition;
		placeholder?: string;
		onChange?: (value: string) => void;
	};

	let {
		value = $bindable(''),
		options = [],
		searchable = false,
		scrollBlock = 'nearest',
		placeholder = '请选择',
		onChange = () => {}
	}: Props = $props();

	let open = $state(false);
	let query = $state('');
	let root: HTMLDivElement | undefined = $state();
	let searchInput: HTMLInputElement | undefined = $state();
	let selectedOptionEl: HTMLButtonElement | undefined = $state();

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
		await tick();
		searchInput?.focus({ preventScroll: true });
		selectedOptionEl = root?.querySelector<HTMLButtonElement>('.select-option.selected') ?? undefined;
		selectedOptionEl?.scrollIntoView({ block: scrollBlock });
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

<svelte:window onclick={closeOnOutsideClick} />

<div class="select" bind:this={root}>
	<button class="select-trigger" type="button" aria-haspopup="listbox" aria-expanded={open} onclick={toggle}>
		<span class:placeholder={!selected}>{selected?.label ?? placeholder}</span>
		<span class="chevron" aria-hidden="true">⌄</span>
	</button>

	{#if open}
		<div class="select-menu">
			{#if searchable}
				<input bind:this={searchInput} class="select-search" bind:value={query} placeholder="搜索选项" autocomplete="off" />
			{/if}

			<div class="select-options" role="listbox" aria-label={placeholder}>
				{#each filteredOptions as option}
					<button
						class="select-option"
						class:selected={option.value === value}
						data-selected={option.value === value}
						type="button"
						role="option"
						aria-selected={option.value === value}
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

	.placeholder {
		color: var(--muted);
	}

	.chevron {
		color: var(--muted);
		font-size: 14px;
	}

	.select-menu {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		right: 0;
		z-index: 30;
		border: 1px solid var(--line);
		border-radius: 9px;
		background: var(--panel);
		box-shadow: 0 18px 38px rgba(0, 0, 0, 0.34);
		padding: 6px;
	}

	.select-search {
		margin-bottom: 6px;
	}

	.select-options {
		max-height: 220px;
		overflow-y: auto;
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
