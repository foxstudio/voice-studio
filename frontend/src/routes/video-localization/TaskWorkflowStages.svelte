<script lang="ts">
	import { AlertTriangle, Check, Circle, CircleOff } from 'lucide-svelte';
	import {
		activityTaskStageTimingLabel,
		activityTaskStepAttentionKind,
		activityTaskStepTimingLabel,
		type ActivityTask,
		type ActivityTaskStage,
		type ActivityTaskStep
	} from './activity-notice';
	import CommandSpinner from './CommandSpinner.svelte';

	let {
		task,
		nowMs,
		onShowResult
	}: {
		task: ActivityTask;
		nowMs: number;
		onShowResult: (step: ActivityTaskStep) => void;
	} = $props();

	const stepCount = $derived(task.stages?.reduce((total, stage) => total + stage.steps.length, 0) ?? task.steps?.length ?? 0);

	function stepNeedsAttention(step: ActivityTaskStep) {
		return step.status === 'success' && (step.result?.status === 'warning' || step.result?.status === 'failed');
	}

	function stepStateLabel(step: ActivityTaskStep) {
		const attentionKind = activityTaskStepAttentionKind(
			step.result,
			step.id
		);
		if (attentionKind === 'manual_review' || attentionKind === 'advisory') {
			return '已完成，有建议';
		}
		if (step.status === 'success' && step.result?.status === 'failed') return '结果异常';
		if (step.status === 'success' && step.result?.status === 'not_needed') return '无需执行';
		if (step.status === 'success' && step.result?.status === 'skipped') return '已跳过';
		if (step.status === 'cancelled' && task.status === 'failed') return '未执行';
		return {
			todo: '待处理',
			running: '处理中',
			success: '已完成',
			failed: '失败',
			cancelled: '已取消'
		}[step.status];
	}

	function stageStateLabel(stage: ActivityTaskStage) {
		if (
			stage.steps.length
			&& stage.steps.every((step) => step.result?.status === 'not_needed')
		) return '无需执行';
		if (stage.status === 'cancelled' && task.status === 'failed') return '未执行';
		return {
			todo: '待处理',
			running: '处理中',
			success: '已完成',
			failed: '失败',
			cancelled: '已取消'
		}[stage.status];
	}

	function stepTiming(step: ActivityTaskStep) {
		return activityTaskStepTimingLabel(step, task, nowMs);
	}

	function stageTiming(stage: ActivityTaskStage) {
		return activityTaskStageTimingLabel(stage, task, nowMs);
	}

	function stageHasParallel(stage: ActivityTaskStage) {
		return stage.steps.some((step) => step.execution === 'parallel');
	}
</script>

{#snippet stepCardContents(step: ActivityTaskStep, timing: string)}
	<span class="task-step-state" aria-hidden="true">
		{#if stepNeedsAttention(step)}<AlertTriangle size={10} />
		{:else if step.status === 'success'}<Check size={10} />
		{:else if step.status === 'running'}<CommandSpinner size={10} />
		{:else if step.status === 'failed'}<AlertTriangle size={10} />
		{:else if step.status === 'cancelled'}<CircleOff size={10} />
		{:else}<Circle size={8} />{/if}
	</span>
	<span class="task-step-label">{step.label}</span>
	<span class="task-step-summary">
		{#if timing}<b aria-label={`子任务耗时 ${timing}`}>{timing}</b>{/if}
		<em>{stepStateLabel(step)}</em>
	</span>
{/snippet}

{#snippet stepCard(step: ActivityTaskStep, timing: string)}
	<button
		class="task-step-card"
		type="button"
		aria-label={`查看“${step.label}”的结果`}
		aria-haspopup="dialog"
		onclick={() => onShowResult(step)}
	>
		{@render stepCardContents(step, timing)}
	</button>
{/snippet}

{#if stepCount}
	<div class="task-flow-head"><span>处理流程</span><b>共 {stepCount} 项</b></div>
	{#if task.stages?.length}
		<div class="task-stages" aria-label={`${task.label}处理阶段`}>
			{#each task.stages as stage (stage.id)}
				<section class="task-stage-group" class:parallel-join={stage.layout === 'parallel-join'}>
					<header class="stage-head">
						<div>
							<strong>{stage.label}</strong>
							{#if stage.description}<p>{stage.description}</p>{/if}
						</div>
						<span>
							{#if stageTiming(stage)}
								<b class="stage-time" aria-label={`父级总耗时 ${stageTiming(stage)}`}>总 {stageTiming(stage)}</b>
							{/if}
							<em>{stageStateLabel(stage)}</em>
						</span>
					</header>
					<ul class="stage-steps" class:parallel-layout={stageHasParallel(stage)}>
						{#each stage.steps as step (step.id)}
							{@const timing = stepTiming(step)}
							<li
								class:full-row={step.execution !== 'parallel'}
								class:parallel-step={step.execution === 'parallel'}
								class:join-step={step.execution === 'join'}
								class:current={step.status === 'running'}
								class:step-failed={step.status === 'failed'}
								class:step-warning={stepNeedsAttention(step)}
							>
								{@render stepCard(step, timing)}
							</li>
						{/each}
					</ul>
				</section>
			{/each}
		</div>
	{:else if task.steps?.length}
		<ul class="flat-steps" aria-label={`${task.label}处理步骤`}>
			{#each task.steps as step (step.id)}
				{@const timing = stepTiming(step)}
				<li class:current={step.status === 'running'} class:step-failed={step.status === 'failed'} class:step-warning={stepNeedsAttention(step)}>
					{@render stepCard(step, timing)}
				</li>
			{/each}
		</ul>
	{/if}
{/if}

<style>
	.task-flow-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin: 8px 0 4px;
		color: #65747b;
		font-size: 8.5px;
	}
	.task-flow-head b { color: #84939a; font-weight: 600; }
	.task-stages { display: grid; gap: 7px; container-type: inline-size; }
	.task-stage-group {
		min-width: 0;
		overflow: hidden;
		border: 1px solid rgba(255, 255, 255, 0.065);
		border-radius: 6px;
		background: rgba(20, 28, 33, 0.72);
	}
	.stage-head {
		min-width: 0;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: start;
		gap: 8px;
		padding: 7px 8px 6px;
		border-bottom: 1px solid rgba(255, 255, 255, 0.055);
		background: rgba(255, 255, 255, 0.018);
	}
	.stage-head > div { min-width: 0; display: grid; gap: 2px; }
	.stage-head strong { color: #b8c5ca; font-size: 9.5px; font-weight: 680; }
	.stage-head p {
		margin: 0;
		color: #718188;
		font-size: 8.5px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}
	.stage-head > span {
		display: flex;
		align-items: baseline;
		gap: 5px;
		color: #708087;
		font-size: 8.5px;
		white-space: nowrap;
	}
	.stage-head b {
		color: #84959c;
		font-size: 7.5px;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	.stage-head .stage-time { opacity: 0.9; }
	.stage-head em { font-style: normal; }
	.stage-steps,
	.flat-steps {
		display: grid;
		gap: 4px;
		margin: 0;
		padding: 6px;
		list-style: none;
	}
	.stage-steps { grid-template-columns: minmax(0, 1fr); }
	.stage-steps.parallel-layout { grid-template-columns: repeat(2, minmax(0, 1fr)); }
	.stage-steps li,
	.flat-steps li {
		position: relative;
		min-width: 0;
		border: 1px solid #27343a;
		border-radius: 5px;
		background: #171f24;
		color: #7c8c93;
		font-size: 9px;
	}
	.stage-steps li.full-row { grid-column: 1 / -1; }
	.stage-steps li.join-step { border-color: rgba(87, 143, 160, 0.25); }
	.parallel-join .stage-steps li.join-step::before {
		content: '';
		position: absolute;
		top: -5px;
		left: 50%;
		width: 1px;
		height: 5px;
		background: #3d515a;
	}
	.task-step-card {
		width: 100%;
		min-width: 0;
		min-height: 26px;
		display: grid;
		grid-template-columns: 13px minmax(0, 1fr) auto;
		align-items: center;
		gap: 5px;
		padding: 3px 5px;
		border: 0;
		border-radius: inherit;
		background: transparent;
		color: inherit;
		font: inherit;
		text-align: left;
	}
	button.task-step-card { cursor: pointer; }
	button.task-step-card:hover { background: rgba(255, 255, 255, 0.025); color: #a9c3cc; }
	.task-step-card:focus-visible { outline: 1px solid #69abc0; outline-offset: 1px; }
	.task-step-label { min-width: 0; overflow: hidden; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
	.task-step-summary { min-width: 0; display: flex; align-items: baseline; justify-content: flex-end; gap: 4px; white-space: nowrap; }
	.task-step-summary b {
		color: #8799a0;
		font-size: 7.5px;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	.task-step-summary em { color: #697980; font-size: 8px; font-style: normal; }
	.task-step-state { display: grid; place-items: center; color: #64747b; }
	.current { border-color: rgba(105, 171, 192, 0.36) !important; color: #a9ccd6 !important; }
	.current .task-step-state { color: #72b9ce; }
	.step-failed { color: #d89496 !important; }
	.step-warning { color: #c7a66f !important; }
	.step-warning .task-step-state,
	.step-warning .task-step-summary em { color: #b99761; }
	.flat-steps {
		gap: 4px;
		padding: 7px 0 0;
		border-top: 1px solid rgba(255, 255, 255, 0.055);
	}
	.flat-steps .task-step-card { min-height: 22px; }
	@container (max-width: 300px) {
		.stage-steps.parallel-layout { grid-template-columns: 1fr; }
		.stage-steps li.full-row { grid-column: 1; }
		.parallel-join .stage-steps li.join-step::before { left: 12px; }
	}
	@media (prefers-reduced-motion: reduce) {
		.task-step-card { transition: none; }
	}
</style>
