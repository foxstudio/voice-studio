<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		label,
		controlId,
		description,
		error,
		children,
		wide = false
	}: {
		label: string;
		controlId: string;
		description?: Snippet;
		error?: string;
		children: Snippet;
		wide?: boolean;
	} = $props();
</script>

<div class:wide class="settings-field">
	<label for={controlId}>{label}</label>
	<div class="field-control">
		{@render children()}
	</div>
	{#if error}
		<small class="field-error" role="alert">{error}</small>
	{:else if description}
		<small class="field-description">{@render description()}</small>
	{/if}
</div>

<style>
	.settings-field {
		display: grid;
		align-content: start;
		gap: 6px;
		min-width: 0;
	}

	.settings-field.wide {
		grid-column: 1 / -1;
	}

	label {
		color: #909ba9;
		font-size: 11px;
		line-height: 1.35;
	}

	.field-control {
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
	}

	.field-description,
	.field-error {
		color: #6f7c8b;
		font-size: 10px;
		line-height: 1.5;
	}

	.field-error {
		color: #f18e96;
	}

	:global(.settings-field input:not([type='checkbox']):not([type='radio'])),
	:global(.settings-field select),
	:global(.settings-field textarea) {
		width: 100%;
		height: var(--settings-control-height, 34px);
		min-height: var(--settings-control-height, 34px);
		min-width: 0;
		border-color: #2c3541;
		border-radius: var(--settings-control-radius, 7px);
		background: #0d1218;
		font-size: var(--settings-control-font-size, 12px);
	}

	:global(.settings-field textarea) {
		height: auto;
		min-height: calc(var(--settings-control-height, 34px) * 2);
		padding-block: 8px;
		resize: vertical;
	}

	:global(.settings-field input[readonly]),
	:global(.settings-field textarea[readonly]) {
		color: #8793a1;
		cursor: text;
	}

	:global(.settings-field input:disabled),
	:global(.settings-field select:disabled),
	:global(.settings-field textarea:disabled) {
		cursor: not-allowed;
		opacity: 0.55;
	}

	:global(.settings-field a) {
		color: #75ade8;
	}

	:global(.settings-field a:hover) {
		text-decoration: underline;
		text-underline-offset: 3px;
	}

	@media (max-width: 720px) {
		.settings-field.wide {
			grid-column: auto;
		}

		:global(.settings-field input:not([type='checkbox']):not([type='radio'])),
		:global(.settings-field select) {
			height: var(--settings-control-touch-height, 44px);
			min-height: var(--settings-control-touch-height, 44px);
		}
	}
</style>
