<script lang="ts">
	import { CircleHelp } from 'lucide-svelte';
	import { hoverTooltip } from './hover-tooltip';

	type Props = {
		content?: string | null;
	};

	let { content = '' }: Props = $props();
</script>

<span class="tooltip">
	<button class="tooltip-icon" type="button" aria-label={content ? `说明：${content}` : '查看说明'} use:hoverTooltip={content}>
		<CircleHelp size={14} strokeWidth={1.8} />
	</button>
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
		display: grid;
		place-items: center;
		border: 0;
		border-radius: 4px;
		background: transparent;
		color: var(--muted);
		padding: 0;
	}

	.tooltip-icon:hover,
	.tooltip-icon:focus-visible {
		color: var(--accent);
	}

</style>
