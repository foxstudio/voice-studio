<script lang="ts">
	import { AlertTriangle, Check, ChevronDown, ChevronRight, Circle, CircleOff, CircleStop, Clock3, LoaderCircle } from 'lucide-svelte';
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

	let {
		tasks = [],
		onCancelTask = undefined,
		pulseKey = 0,
		full = false
	}: {
		tasks?: ActivityTask[];
		onCancelTask?: (task: ActivityTask) => void | Promise<void>;
		pulseKey?: number;
		full?: boolean;
	} = $props();

	let pulsing = $state(false);
	let highlightedTaskId = $state('');
	let expandedHistoryIds = $state<string[]>([]);
	let showAllHistory = $state(false);
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
									{#if meta.length}<div class="task-meta">{meta.join(' · ')}</div>{/if}
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
													<span class="task-step-state" class:spinning={step.status === 'running'} aria-hidden="true">
														{#if step.status === 'success'}<Check size={10} />
														{:else if step.status === 'running'}<LoaderCircle size={10} />
														{:else if step.status === 'failed'}<AlertTriangle size={10} />
														{:else if step.status === 'cancelled'}<CircleOff size={10} />
														{:else}<Circle size={8} />{/if}
													</span>
													<span>{step.label}</span>
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
								<button class="history-summary" type="button" aria-expanded={expanded} onclick={() => toggleHistoryTask(task.id)}>
									<span class="task-state" aria-hidden="true">
										{#if task.status === 'success'}<Check size={13} />
										{:else if task.status === 'failed'}<AlertTriangle size={13} />
										{:else}<CircleOff size={13} />{/if}
									</span>
									<strong>{displayLabel(task)}</strong>
									<span class="history-time"><b>{duration(task)}</b><time>{historyTime(task)}</time></span>
									<span class="history-chevron" aria-hidden="true"><ChevronRight size={13} /></span>
								</button>
								{#if expanded}
									<div class="task-details history-details">
										{#if meta.length}<div class="task-meta">{meta.join(' · ')}</div>{/if}
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
														<span>{step.label}</span>
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

	.task-center-head strong { font-size: 11px; }
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
	.task-group-head strong { color: #aebbc1; font-size: 9.5px; font-weight: 750; }
	.task-group-head span { font-size: 8.5px; }

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
		min-height: 34px;
		padding: 5px 8px 3px;
	}
	.task-state { display: grid; place-items: center; color: #6f8088; }
	.running .task-state { color: #72b9ce; }
	.failed .task-state { color: #dc8587; }
	.spinning { animation: task-spin 900ms linear infinite; }

	.task-title { min-width: 0; display: flex; align-items: center; gap: 6px; }
	.task-title strong,
	.history-summary > strong {
		min-width: 0;
		overflow: hidden;
		color: #d5dfe3;
		font-size: 10.5px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.task-title span { flex: 0 0 auto; color: #78909a; font-size: 8.5px; }

	.task-details {
		min-width: 0;
		display: grid;
		gap: 5px;
		padding: 0 8px 9px 32px;
	}
	.task-meta { overflow: hidden; color: #74848c; font-size: 8.5px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
	.task-stage { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #93a2a9; font-size: 9.5px; line-height: 1.35; }
	.task-stage span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.task-stage strong { flex: 0 0 auto; color: #a9ccd6; font-size: 9px; }
	.task-details small { color: #c28d8f; font-size: 9px; line-height: 1.45; }

	.task-meter { height: 3px; border-radius: 2px; background: #263238; overflow: hidden; }
	.task-meter i { display: block; height: 100%; background: #65afc5; transition: width 180ms ease; }

	.task-steps { display: grid; gap: 1px; margin: 1px 0 0; padding: 5px 0 0; border-top: 1px solid rgba(255, 255, 255, 0.05); list-style: none; }
	.task-steps li { min-width: 0; display: grid; grid-template-columns: 13px minmax(0, 1fr) auto; align-items: center; gap: 4px; min-height: 17px; color: #728087; font-size: 8.5px; }
	.task-steps li.current { color: #a9ccd6; }
	.task-steps li.step-failed { color: #d89496; }
	.task-step-summary { min-width: 0; display: flex; align-items: baseline; justify-content: flex-end; gap: 5px; white-space: nowrap; }
	.task-step-summary b { color: #8fa1a9; font-size: 8px; font-weight: 650; }
	.task-step-summary em { color: #65747b; font-size: 8px; font-style: normal; }
	.task-steps li.current .task-step-summary b { color: #a9ccd6; }
	.task-step-state { display: grid; place-items: center; color: #64747b; }
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
	.history-summary:hover { background: rgba(255, 255, 255, 0.025); }
	.history-time { display: grid; justify-items: end; gap: 2px; white-space: nowrap; }
	.history-time b { color: #8b9aa1; font-size: 8px; font-weight: 650; }
	.history-time time { color: #697880; font-size: 8px; }
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
