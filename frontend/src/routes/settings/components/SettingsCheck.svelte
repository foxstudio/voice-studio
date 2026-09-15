<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		id,
		checked = $bindable(false),
		disabled = false,
		danger = false,
		children,
		onchange
	}: {
		id: string;
		checked?: boolean;
		disabled?: boolean;
		danger?: boolean;
		children: Snippet;
		onchange?: (event: Event) => void;
	} = $props();
</script>

<label class:danger class:disabled class="settings-check" for={id}>
	<input {id} type="checkbox" bind:checked {disabled} {onchange} />
	<span>{@render children()}</span>
</label>

<style>
	.settings-check {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		min-height: 30px;
		padding: 5px 8px;
		border: 1px solid rgba(148, 163, 184, 0.15);
		border-radius: var(--settings-control-radius, 7px);
		background: rgba(20, 26, 35, 0.58);
		color: #cbd3dc;
		font-size: 11px;
		line-height: 1.35;
		cursor: pointer;
	}

	.settings-check.danger {
		border-color: rgba(221, 91, 97, 0.2);
		background: rgba(96, 33, 38, 0.14);
		color: #f5a0a3;
	}

	.settings-check.disabled {
		cursor: not-allowed;
		opacity: 0.6;
	}

	input {
		position: relative;
		width: 16px;
		height: 16px;
		min-height: 16px;
		margin: 0;
		flex: 0 0 16px;
		appearance: none;
		border: 1px solid #536171;
		border-radius: 4px;
		background: #0c1117;
	}

	input::after {
		content: '';
		position: absolute;
		left: 4px;
		top: 1px;
		width: 5px;
		height: 9px;
		border: solid white;
		border-width: 0 2px 2px 0;
		opacity: 0;
		transform: rotate(45deg);
	}

	input:checked {
		border-color: #2f85ed;
		background: #2f85ed;
	}

	input:checked::after {
		opacity: 1;
	}

	input:focus-visible {
		outline: 2px solid rgba(94, 165, 246, 0.55);
		outline-offset: 2px;
	}

	@media (max-width: 720px) {
		.settings-check {
			min-height: 42px;
		}
	}
</style>
