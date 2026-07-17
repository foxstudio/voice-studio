<script lang="ts">
	import { AlertTriangle, Check, ChevronDown, ChevronRight, Circle, CircleOff, CircleStop, Clock3, Info, LoaderCircle, RotateCcw } from 'lucide-svelte';
	import { untrack } from 'svelte';
	import { hoverTooltip } from '$lib/components/shared/hover-tooltip';
	import {
		activityTaskProgress,
		activityTaskDisplayName,
		activityTaskElapsedMs,
		activityTaskResultLabel,
		activityTaskSourceLabel,
		activityTaskStepTimingLabel,
		activityTaskStatusLabel,
		formatActivityTaskTime,
		formatActivityTaskDuration,
		isActiveActivityTask,
		type ActivityTask,
		type ActivityTaskStep
	} from './activity-notice';
	import TaskStepResultDialog from './TaskStepResultDialog.svelte';

	let {
		tasks = [],
		onCancelTask = undefined,
		onRetryTask = undefined,
		pulseKey = 0,
		full = false
	}: {
		tasks?: ActivityTask[];
		onCancelTask?: (task: ActivityTask) => void | Promise<void>;
		onRetryTask?: (task: ActivityTask) => void | Promise<void>;
		pulseKey?: number;
		full?: boolean;
	} = $props();

	let pulsing = $state(false);
	let highlightedTaskId = $state('');
	let expandedHistoryIds = $state<string[]>([]);
	let showAllHistory = $state(false);
	let selectedResult = $state<{ taskId: string; stepId: string } | null>(null);
	let handledPulseKey = 0;
	let nowMs = $state(Date.now());

	const uniqueTasks = $derived.by(() => {
		const seen = new Set<string>();
		return tasks.filter((task) => {
			if (seen.has(task.id)) return false;
			seen.add(task.id);
			return true;
		});
	});
	const activeTasks = $derived(uniqueTasks.filter(isActiveActivityTask));
	const historyTasks = $derived(uniqueTasks.filter((task) => !isActiveActivityTask(task)));
	const visibleActiveTasks = $derived(full ? activeTasks : activeTasks.slice(0, 5));
	const availableHistorySlots = $derived(full ? 5 : Math.max(0, 5 - visibleActiveTasks.length));
	const visibleHistoryTasks = $derived(showAllHistory ? historyTasks : historyTasks.slice(0, availableHistorySlots));
	const visibleTasks = $derived([...visibleActiveTasks, ...visibleHistoryTasks]);
	const hiddenHistoryCount = $derived(Math.max(0, historyTasks.length - visibleHistoryTasks.length));
	const selectedResultContext = $derived.by(() => {
		if (!selectedResult) return null;
		const task = uniqueTasks.find((item) => item.id === selectedResult?.taskId);
		const step = task?.steps?.find((item) => item.id === selectedResult?.stepId);
		return task && step?.result ? { task, step } : null;
	});

	$effect(() => {
		if (!activeTasks.length) return;
		nowMs = Date.now();
		const timer = window.setInterval(() => (nowMs = Date.now()), 1000);
		return () => window.clearInterval(timer);
	});

	$effect(() => {
		const nextPulseKey = pulseKey;
		if (!nextPulseKey || nextPulseKey === handledPulseKey) return;
		handledPulseKey = nextPulseKey;
		highlightedTaskId = untrack(() => activeTasks[0]?.id ?? visibleTasks[0]?.id ?? '');
		pulsing = false;
		const frame = requestAnimationFrame(() => (pulsing = true));
		const timer = setTimeout(() => {
			pulsing = false;
			highlightedTaskId = '';
		}, 1100);
		return () => {
			cancelAnimationFrame(frame);
			clearTimeout(timer);
		};
	});

	function cancelTask(task: ActivityTask) {
		if (!task.cancellable || task.cancelPending || !onCancelTask) return;
		void onCancelTask(task);
	}

	function retryTask(task: ActivityTask) {
		if (!task.operationId || !onRetryTask || (task.status !== 'failed' && task.status !== 'cancelled')) return;
		void onRetryTask(task);
	}

	function toggleHistoryTask(taskId: string) {
		expandedHistoryIds = expandedHistoryIds.includes(taskId)
			? expandedHistoryIds.filter((id) => id !== taskId)
			: [...expandedHistoryIds, taskId];
	}

	function displayLabel(task: ActivityTask) {
		return activityTaskDisplayName(task);
	}

	function duration(task: ActivityTask) {
		return formatActivityTaskDuration(activityTaskElapsedMs(task, nowMs));
	}

	function metadata(task: ActivityTask) {
		const eventTime = isActiveActivityTask(task)
			? formatActivityTaskTime(task.startedAt || task.createdAt)
			: formatActivityTaskTime(task.completedAt || task.startedAt || task.createdAt);
		const source = activityTaskSourceLabel(task.sourceTrackId);
		const result = activityTaskResultLabel(task);
		const elapsed = duration(task);
		return [
			eventTime ? `${isActiveActivityTask(task) ? '开始' : '结束'} ${eventTime}` : '',
			elapsed ? `耗时 ${elapsed}` : '',
			source ? `来源 ${source}` : '',
			task.engineId ? `引擎 ${task.engineId}` : '',
			task.semanticModelId ? `语义模型 ${task.semanticModelId}` : '',
			result ? `结果 ${result}` : ''
		].filter(Boolean);
	}

	function historyTime(task: ActivityTask) {
		return formatActivityTaskTime(task.completedAt || task.startedAt || task.createdAt);
	}

	function stepStateLabel(step: ActivityTaskStep) {
		return {
			todo: '待处理',
			running: '处理中',
			success: '已完成',
			failed: '失败',
			cancelled: '已取消'
		}[step.status];
	}

	function stepTiming(step: ActivityTaskStep, task: ActivityTask) {
		return activityTaskStepTimingLabel(step, task, nowMs);
	}

	function showStepResult(task: ActivityTask, step: ActivityTaskStep) {
		if (!step.result) return;
		selectedResult = { taskId: task.id, stepId: step.id };
	}
</script>

<section class="task-center" class:active={activeTasks.length > 0} class:pulsing class:full aria-label="后台任务进度">
	<header class="task-center-head">
		<span class="task-center-mark">
			{#if activeTasks.length}<LoaderCircle size={13} />{:else}<Clock3 size={13} />{/if}
		</span>
		<strong>{activeTasks.length ? '任务处理中' : '当前无运行任务'}</strong>
		{#if activeTasks.length}<span class="summary-count active-count">{activeTasks.length} 项</span>{/if}
	</header>

	{#if uniqueTasks.length}
		<div class="task-scroll">
			{#if visibleActiveTasks.length}
				<section class="task-group" aria-labelledby="active-task-heading">
					<div class="task-group-head">
						<strong id="active-task-heading">进行中</strong>
						<span>{visibleActiveTasks.length} 项</span>
					</div>
					<div class="task-list active-task-list">
						{#each visibleActiveTasks as task (task.id)}
							{@const progress = activityTaskProgress(task)}
							{@const meta = metadata(task)}
							<article class="task-row running" class:highlighted={highlightedTaskId === task.id}>
								<div class="task-primary">
									<span class="task-state spinning" aria-hidden="true"><LoaderCircle size={13} /></span>
									<div class="task-title">
										<strong>{displayLabel(task)}</strong>
										<span>{activityTaskStatusLabel(task.status)}</span>
									</div>
									{#if task.cancellable}
										<button
											class="task-stop"
											type="button"
											aria-label={`终止${displayLabel(task)}`}
											use:hoverTooltip={'终止任务｜当前安全步骤结束后停止处理。'}
											disabled={task.cancelPending}
											onclick={() => cancelTask(task)}
										><CircleStop size={14} strokeWidth={1.8} /></button>
									{/if}
								</div>
								<div class="task-details">
									{#if meta.length}
										<div class="task-meta" aria-label={meta.join('，')}>
											{#each meta as item}<span>{item}</span>{/each}
										</div>
									{/if}
									<div class="task-stage">
										<span>{task.stage || activityTaskStatusLabel(task.status)}</span>
										{#if progress !== null}<strong>{progress}%</strong>{/if}
									</div>
									{#if progress !== null}
										<div class="task-meter" aria-label={`进度 ${progress}%`}><i style={`width:${progress}%`}></i></div>
									{/if}
									{#if task.steps?.length}
										<ul class="task-steps" aria-label={`${displayLabel(task)}处理步骤`}>
											{#each task.steps as step (step.id)}
												{@const timing = stepTiming(step, task)}
												<li class:current={step.status === 'running'} class:step-failed={step.status === 'failed'}>
													<span class="task-step-state" class:step-spinning={step.status === 'running'} aria-hidden="true">
														{#if step.status === 'success'}<Check size={10} />
														{:else if step.status === 'running'}<LoaderCircle size={10} />
														{:else if step.status === 'failed'}<AlertTriangle size={10} />
														{:else if step.status === 'cancelled'}<CircleOff size={10} />
														{:else}<Circle size={8} />{/if}
													</span>
													<span class="task-step-name">
														<span class="task-step-label">{step.label}</span>
														{#if step.result}
															<button
																class="step-result-trigger"
																type="button"
																aria-label={`查看“${step.label}”的结果`}
																aria-haspopup="dialog"
																use:hoverTooltip={`查看结果｜核对“${step.label}”实际产出的内容和质量状态。`}
																onclick={() => showStepResult(task, step)}
															><Info size={11} /></button>
														{/if}
													</span>
													<span class="task-step-summary">
														{#if timing}<b>{timing}</b>{/if}
														<em>{stepStateLabel(step)}</em>
													</span>
												</li>
											{/each}
										</ul>
									{/if}
								</div>
							</article>
						{/each}
					</div>
				</section>
			{/if}

			{#if visibleHistoryTasks.length}
				<section class="task-group history-group" aria-labelledby="history-task-heading">
					<div class="task-group-head">
						<strong id="history-task-heading">历史记录</strong>
						<span>{historyTasks.length} 项</span>
					</div>
					<div class="task-list history-task-list">
						{#each visibleHistoryTasks as task (task.id)}
							{@const expanded = expandedHistoryIds.includes(task.id)}
							{@const meta = metadata(task)}
							<article
								class="task-row history-row"
								class:failed={task.status === 'failed'}
								class:highlighted={highlightedTaskId === task.id}
								class:expanded
							>
								<div class="history-row-head">
									<button class="history-summary" type="button" aria-expanded={expanded} onclick={() => toggleHistoryTask(task.id)}>
										<span class="task-state" aria-hidden="true">
											{#if task.status === 'success'}<Check size={13} />
											{:else if task.status === 'failed'}<AlertTriangle size={13} />
											{:else}<CircleOff size={13} />{/if}
										</span>
										<strong>{displayLabel(task)}{task.status === 'failed' ? ' · 失败' : task.status === 'cancelled' ? ' · 已取消' : ''}</strong>
										<span class="history-time"><b>{duration(task)}</b><time>{historyTime(task)}</time></span>
										<span class="history-chevron" aria-hidden="true"><ChevronRight size={13} /></span>
									</button>
									{#if (task.status === 'failed' || task.status === 'cancelled') && task.operationId && onRetryTask}
										<button
											class="task-retry"
											type="button"
											aria-label={`重试${displayLabel(task)}`}
											use:hoverTooltip={'重试任务｜使用原参数重新提交这项任务。'}
											onclick={() => retryTask(task)}
										><RotateCcw size={12} strokeWidth={1.9} /></button>
									{/if}
								</div>
								{#if expanded}
									<div class="task-details history-details">
										{#if meta.length}
											<div class="task-meta" aria-label={meta.join('，')}>
												{#each meta as item}<span>{item}</span>{/each}
											</div>
										{/if}
										<div class="task-stage"><span>{task.stage || activityTaskStatusLabel(task.status)}</span></div>
										{#if task.steps?.length}
											<ul class="task-steps" aria-label={`${displayLabel(task)}处理步骤`}>
												{#each task.steps as step (step.id)}
													{@const timing = stepTiming(step, task)}
													<li class:step-failed={step.status === 'failed'}>
														<span class="task-step-state" aria-hidden="true">
															{#if step.status === 'success'}<Check size={10} />
															{:else if step.status === 'failed'}<AlertTriangle size={10} />
															{:else if step.status === 'cancelled'}<CircleOff size={10} />
															{:else}<Circle size={8} />{/if}
														</span>
														<span class="task-step-name">
															<span class="task-step-label">{step.label}</span>
															{#if step.result}
																<button
																	class="step-result-trigger"
																	type="button"
																	aria-label={`查看“${step.label}”的结果`}
																	aria-haspopup="dialog"
																	use:hoverTooltip={`查看结果｜核对“${step.label}”实际产出的内容和质量状态。`}
																	onclick={() => showStepResult(task, step)}
																><Info size={11} /></button>
															{/if}
														</span>
														<span class="task-step-summary">
															{#if timing}<b>{timing}</b>{/if}
															<em>{stepStateLabel(step)}</em>
														</span>
													</li>
												{/each}
											</ul>
										{/if}
										{#if task.status === 'failed' && task.detail}<small>{task.detail}</small>{/if}
									</div>
								{/if}
							</article>
						{/each}
					</div>
				</section>
			{/if}
			{#if showAllHistory || hiddenHistoryCount > 0}
				<button class="task-history-toggle" type="button" aria-expanded={showAllHistory} onclick={() => (showAllHistory = !showAllHistory)}>
					<span>{showAllHistory ? '收起历史记录' : `查看更早的 ${hiddenHistoryCount} 条记录`}</span>
					<span class="history-toggle-chevron" aria-hidden="true"><ChevronDown size={12} /></span>
				</button>
			{/if}
		</div>
	{:else}
		<div class="task-center-empty">导入、分离、ASR 字幕和配音生成任务会按时间显示在这里。</div>
	{/if}
</section>

{#if selectedResultContext?.step.result}
	<TaskStepResultDialog
		stepLabel={selectedResultContext.step.label}
		result={selectedResultContext.step.result}
		durationLabel={stepTiming(selectedResultContext.step, selectedResultContext.task)}
		onClose={() => (selectedResult = null)}
	/>
{/if}

<style>
	.task-center {
		min-width: 0;
		border: 0;
		border-radius: 0;
		background: transparent;
		overflow: hidden;
	}

	.task-center.full {
		height: 100%;
		min-height: 0;
		display: grid;
		grid-template-rows: 36px minmax(0, 1fr);
	}

	.task-center.pulsing { animation: task-center-pulse 900ms ease-out; }

	.task-center-head {
		min-width: 0;
		height: 36px;
		display: flex;
		align-items: center;
		gap: 7px;
		padding: 0 2px 0 4px;
		border-bottom: 1px solid #29333a;
		color: #d9e3e7;
		white-space: nowrap;
	}

	.task-center-head strong { font-size: 11.5px; }
	.task-center-mark { display: grid; place-items: center; color: #71b8cd; }
	.active .task-center-mark { animation: task-spin 900ms linear infinite; }
	.summary-count { color: #7f9098; font-size: 9.5px; }
	.active-count { color: #8bc7d7; }

	.task-scroll {
		min-height: 0;
		overflow-y: auto;
		overflow-x: hidden;
		scrollbar-gutter: stable;
	}

	.task-group + .task-group { border-top: 1px solid #303940; }
	.task-group-head {
		height: 29px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0 4px;
		background: transparent;
		color: #8b9aa1;
	}
	.task-group-head strong { color: #aebbc1; font-size: 10px; font-weight: 700; }
	.task-group-head span { font-size: 9px; }

	.task-list { display: grid; }
	.task-row {
		min-width: 0;
		border-bottom: 1px solid rgba(255, 255, 255, 0.055);
		background: transparent;
		transition: background 140ms ease, box-shadow 140ms ease;
	}
	.task-row:last-child { border-bottom: 0; }
	.task-row.running { background: rgba(61, 119, 137, 0.065); }
	.task-row.highlighted { background: rgba(61, 139, 163, 0.16); box-shadow: inset 2px 0 #68b5ca; }

	.task-primary {
		display: grid;
		grid-template-columns: 18px minmax(0, 1fr) auto;
		align-items: center;
		gap: 6px;
		min-height: 38px;
		padding: 6px 8px 4px;
	}
	.task-state { display: grid; place-items: center; color: #6f8088; }
	.running .task-state { color: #72b9ce; }
	.failed .task-state { color: #dc8587; }
	.failed .history-summary > strong { color: #d89496; }
	.spinning { animation: task-spin 900ms linear infinite; }

	.task-title { min-width: 0; display: flex; align-items: center; gap: 6px; }
	.task-title strong,
	.history-summary > strong {
		min-width: 0;
		overflow: hidden;
		color: #d5dfe3;
		font-size: 11.5px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.task-title span { flex: 0 0 auto; color: #78909a; font-size: 9.5px; }

	.task-details {
		min-width: 0;
		display: grid;
		gap: 6px;
		padding: 0 8px 10px 32px;
	}
	.task-meta { min-width: 0; display: flex; flex-wrap: wrap; gap: 2px 0; color: #74848c; font-size: 9.5px; line-height: 1.45; }
	.task-meta span { display: inline-flex; align-items: baseline; white-space: nowrap; }
	.task-meta span:not(:last-child)::after { content: '·'; margin: 0 6px; color: #4f5e65; }
	.task-stage { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #9baab1; font-size: 10px; line-height: 1.4; }
	.task-stage span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.task-stage strong { flex: 0 0 auto; color: #b4d8e2; font-size: 9.5px; }
	.task-details small { color: #c28d8f; font-size: 9px; line-height: 1.45; }

	.task-meter { height: 3px; border-radius: 2px; background: #263238; overflow: hidden; }
	.task-meter i { display: block; height: 100%; background: #65afc5; transition: width 180ms ease; }

	.task-steps { display: grid; gap: 0; margin: 1px 0 0; padding: 7px 0 0; border-top: 1px solid rgba(255, 255, 255, 0.055); list-style: none; }
	.task-steps li { position: relative; min-width: 0; display: grid; grid-template-columns: 15px minmax(0, 1fr) auto; align-items: center; gap: 6px; min-height: 22px; color: #78878e; font-size: 9.5px; }
	.task-steps li.current { color: #a9ccd6; }
	.task-steps li.step-failed { color: #d89496; }
	.task-step-name { min-width: 0; display: flex; align-items: center; gap: 3px; }
	.task-step-label { min-width: 0; overflow: hidden; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
	.task-step-summary { min-width: 0; display: flex; align-items: baseline; justify-content: flex-end; gap: 6px; white-space: nowrap; }
	.task-step-summary b { color: #91a2aa; font-size: 9px; font-weight: 600; }
	.task-step-summary em { min-width: 28px; color: #697980; font-size: 9px; font-style: normal; text-align: right; }
	.step-result-trigger {
		width: 18px;
		height: 18px;
		display: grid;
		place-items: center;
		flex: 0 0 auto;
		padding: 0;
		border: 0;
		border-radius: 4px;
		background: transparent;
		color: #718188;
		line-height: 0;
		cursor: pointer;
	}
	.step-result-trigger:hover { background: #273238; color: #9bc8d5; }
	.step-result-trigger:focus-visible { outline: 1px solid #69abc0; outline-offset: 1px; }
	.task-steps li.current .task-step-summary b { color: #a9ccd6; }
	.task-step-state { position: relative; z-index: 1; display: grid; place-items: center; color: #64747b; }
	.task-step-state.step-spinning :global(svg) { animation: task-spin 900ms linear infinite; }
	.task-steps li:not(:last-child) .task-step-state::after { content: ''; position: absolute; top: 13px; left: 7px; width: 1px; height: 10px; background: #354147; }
	.task-steps li.current .task-step-state { color: #72b9ce; }

	.task-stop {
		width: 24px;
		height: 24px;
		display: grid;
		place-items: center;
		padding: 0;
		border: 1px solid rgba(212, 108, 105, 0.34);
		border-radius: 5px;
		background: rgba(112, 45, 44, 0.12);
		color: #e3a19e;
		line-height: 0;
		cursor: pointer;
	}
	.task-stop:disabled { opacity: 0.42; cursor: not-allowed; }
	.task-stop:hover:not(:disabled) { border-color: rgba(230, 128, 124, 0.58); background: rgba(136, 54, 52, 0.22); color: #ffd0cd; }

	.history-summary {
		width: 100%;
		height: 34px;
		display: grid;
		grid-template-columns: 18px minmax(0, 1fr) auto 14px;
		align-items: center;
		gap: 6px;
		padding: 0 8px;
		border: 0;
		background: transparent;
		color: #d5dfe3;
		text-align: left;
		cursor: pointer;
	}
	.history-row-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; }
	.history-summary:hover { background: rgba(255, 255, 255, 0.025); }
	.task-retry {
		width: 24px;
		height: 24px;
		display: grid;
		place-items: center;
		margin-right: 6px;
		padding: 0;
		border: 0;
		border-radius: 4px;
		background: transparent;
		color: #84969e;
		line-height: 0;
		cursor: pointer;
	}
	.task-retry:hover { background: #273238; color: #b6d7df; }
	.task-retry:focus-visible { outline: 1px solid #69abc0; outline-offset: 1px; }
	.history-time { display: grid; justify-items: end; gap: 2px; white-space: nowrap; }
	.history-time b { color: #8b9aa1; font-size: 9px; font-weight: 650; }
	.history-time time { color: #697880; font-size: 9px; }
	.history-chevron { display: grid; place-items: center; color: #61727a; transition: transform 140ms ease; }
	.history-row.expanded .history-chevron { transform: rotate(90deg); }
	.history-details { padding-top: 1px; }

	.task-center-empty {
		display: grid;
		place-items: center;
		min-height: 120px;
		padding: 20px 12px;
		color: #718087;
		font-size: 10px;
		line-height: 1.6;
		text-align: center;
	}
	.task-history-toggle {
		width: 100%;
		height: 30px;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
		border: 0;
		border-top: 1px solid #29333a;
		background: transparent;
		color: #7f9098;
		font-size: 9px;
		cursor: pointer;
	}
	.task-history-toggle:hover { background: rgba(255, 255, 255, 0.025); color: #aebbc1; }
	.history-toggle-chevron { display: grid; place-items: center; transition: transform 140ms ease; }
	.task-history-toggle[aria-expanded='true'] .history-toggle-chevron { transform: rotate(180deg); }

	@keyframes task-spin { to { transform: rotate(360deg); } }
	@keyframes task-center-pulse {
		0% { box-shadow: 0 0 0 0 rgba(89, 183, 210, 0.42); }
		55% { box-shadow: 0 0 0 4px rgba(89, 183, 210, 0.14); }
		100% { box-shadow: 0 0 0 0 rgba(89, 183, 210, 0); }
	}
	@media (prefers-reduced-motion: reduce) {
		.active .task-center-mark, .spinning, .task-center.pulsing, .history-chevron { animation: none; transition: none; }
	}
</style>
