<script lang="ts">
	import { HelpCircle, X } from 'lucide-svelte';

	type HelpSection = { title: string; body: string };

	let { title, sections }: { title: string; sections: HelpSection[] } = $props();
	let open = $state(false);
</script>

<button class="btn" type="button" onclick={() => (open = true)}><HelpCircle size={15} /> 使用说明</button>

{#if open}
	<div class="help-backdrop" role="presentation" onclick={() => (open = false)}></div>
	<aside class="help-drawer" aria-label={`${title}使用说明`}>
		<div class="row" style="justify-content:space-between">
			<h2>{title}</h2>
			<button class="icon-btn" type="button" title="关闭" onclick={() => (open = false)}><X size={17} /></button>
		</div>
		{#each sections as section}
			<section>
				<h3>{section.title}</h3>
				<p>{section.body}</p>
			</section>
		{/each}
	</aside>
{/if}

<style>
	.help-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.36);
		z-index: 9;
	}

	.help-drawer {
		position: fixed;
		top: 0;
		right: 0;
		width: min(420px, 92vw);
		height: 100vh;
		overflow: auto;
		z-index: 10;
		background: var(--panel);
		border-left: 1px solid var(--line);
		padding: 18px;
		display: grid;
		align-content: start;
		gap: 16px;
		box-shadow: -18px 0 40px rgba(0, 0, 0, 0.28);
	}

	.help-drawer section {
		border-top: 1px solid var(--line);
		padding-top: 12px;
	}

	.help-drawer p {
		margin: 0;
		color: var(--muted);
		line-height: 1.65;
		font-size: 13px;
	}
</style>
