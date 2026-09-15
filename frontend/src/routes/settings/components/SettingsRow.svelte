<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		icon: Icon,
		title,
		description,
		children,
		id,
		controlId,
		stacked = false
	}: {
		icon: any;
		title: string;
		description: string;
		children: Snippet;
		id?: string;
		controlId?: string;
		stacked?: boolean;
	} = $props();
</script>

<div class:stacked class="setting-row" {id}>
		<span class="row-icon" aria-hidden="true"><Icon size={17} strokeWidth={1.8} /></span>
	<div class="row-copy">
		{#if controlId}<label for={controlId}>{title}</label>{:else}<strong>{title}</strong>{/if}
		<span>{description}</span>
	</div>
	<div class="row-control">
		{@render children()}
	</div>
</div>

<style>
	.setting-row {
		display: grid;
		grid-template-columns: 32px minmax(190px, 0.65fr) minmax(240px, 1fr);
		align-items: center;
		gap: 13px;
		min-height: 62px;
		padding: 10px 14px;
		border-bottom: 1px solid rgba(148, 163, 184, 0.13);
	}

	.setting-row:last-child {
		border-bottom: 0;
	}

	.setting-row:hover {
		background: rgba(255, 255, 255, 0.018);
	}

	.row-icon {
		display: grid;
		width: 30px;
		height: 30px;
		place-items: center;
		border: 1px solid rgba(148, 163, 184, 0.14);
		border-radius: 8px;
		background: rgba(13, 18, 26, 0.66);
		color: #b7c1cf;
	}

	.row-copy {
		display: grid;
		gap: 3px;
		min-width: 0;
	}

	.row-copy strong,
	.row-copy label {
		color: #f2f5f8;
		font-size: 13px;
		font-weight: 620;
		letter-spacing: -0.01em;
	}

	.row-copy span {
		color: #818b99;
		font-size: 11px;
		line-height: 1.4;
	}

	.row-control {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 7px;
		min-width: 0;
	}

	.stacked {
		align-items: start;
	}

	.stacked .row-control {
		align-items: stretch;
		flex-direction: column;
	}

	:global(.setting-row .row-control > input:not([type='checkbox']):not([type='radio'])),
	:global(.setting-row .row-control > select) {
		height: var(--settings-control-height, 34px);
		min-height: var(--settings-control-height, 34px);
		max-width: 380px;
		border-radius: var(--settings-control-radius, 7px);
		font-size: var(--settings-control-font-size, 12px);
	}

	@media (max-width: 760px) {
		.setting-row {
			grid-template-columns: 30px minmax(0, 1fr);
			gap: 9px 11px;
			min-height: auto;
			padding: 12px;
		}

		.row-icon {
			width: 30px;
			height: 30px;
		}

		.row-control {
			grid-column: 2;
			justify-content: stretch;
		}

		:global(.setting-row .row-control > input:not([type='checkbox']):not([type='radio'])),
		:global(.setting-row .row-control > select) {
			height: var(--settings-control-touch-height, 44px);
			min-height: var(--settings-control-touch-height, 44px);
			max-width: none;
		}
	}
</style>
