<script lang="ts">
	import { LoaderCircle } from 'lucide-svelte';

	let { size = 13 } = $props<{ size?: number }>();
</script>

<span class="command-spinner" aria-hidden="true" style={`--spinner-size: ${size}px`}><LoaderCircle {size} /></span>

<style>
	.command-spinner {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: var(--spinner-size);
		height: var(--spinner-size);
		flex: 0 0 var(--spinner-size);
		line-height: 0;
		transform-origin: 50% 50%;
		animation: command-spin 850ms linear infinite;
	}

	.command-spinner :global(svg) {
		display: block;
		width: 100%;
		height: 100%;
	}

	@keyframes command-spin {
		to { transform: rotate(360deg); }
	}

	@media (prefers-reduced-motion: reduce) {
		.command-spinner { animation: command-pulse 1.2s ease-in-out infinite alternate; }
		@keyframes command-pulse { to { opacity: 0.42; } }
	}
</style>
