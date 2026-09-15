<script lang="ts">
	import type { Snippet } from 'svelte';
	import Tooltip from './Tooltip.svelte';

	type Props = {
		label?: string;
		tooltip?: string | null;
		error?: string | null;
		children?: Snippet;
	};

	let { label = '', tooltip = null, error = null, children }: Props = $props();
</script>

<div class="field shared-field" class:has-error={!!error}>
	{#if label || tooltip}
		<div class="field-label">
			{#if label}<span class="label-text">{label}</span>{/if}
			{#if tooltip}<Tooltip content={tooltip} />{/if}
		</div>
	{/if}

	{@render children?.()}

	{#if error}
		<p class="field-error">{error}</p>
	{/if}
</div>

<style>
	.shared-field {
		min-width: 0;
	}

	.field-label {
		display: flex;
		align-items: center;
		gap: 5px;
		min-width: 0;
	}

	.label-text {
		font-size: 12px;
		color: var(--muted);
		line-height: 1.3;
	}

	.field-error {
		margin: 0;
		color: var(--danger);
		font-size: 12px;
		line-height: 1.45;
	}

	.has-error :global(input),
	.has-error :global(select),
	.has-error :global(textarea) {
		border-color: var(--danger);
	}
</style>
