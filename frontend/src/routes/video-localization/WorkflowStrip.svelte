<script lang="ts">
	import type { WorkflowStep } from './utils';

	let { steps }: { steps: WorkflowStep[] } = $props();
</script>

<section class="workflow-strip" aria-label="视频本土化流程">
	{#each steps as step}
		<div class={`workflow-step ${step.status}`}>
			<span>{step.label}</span>
		</div>
	{/each}
</section>

<style>
	.workflow-strip {
		display: grid;
		grid-template-columns: repeat(7, minmax(92px, 1fr));
		gap: 8px;
		margin-bottom: 14px;
	}

	.workflow-step {
		border: 1px solid var(--line);
		border-radius: 7px;
		background: var(--panel);
		color: var(--muted);
		padding: 8px 10px;
		font-size: 12px;
		min-height: 34px;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.workflow-step.done {
		color: #9ee6c8;
		border-color: #23634f;
		background: #12261f;
	}

	.workflow-step.active {
		color: #9cc9ff;
		border-color: #27527e;
		background: #101d2d;
	}

	.workflow-step.blocked {
		color: #ff9a9a;
		border-color: #6d3030;
		background: #2b1515;
	}

	@media (max-width: 780px) {
		.workflow-strip {
			grid-template-columns: repeat(2, 1fr);
		}
	}
</style>
