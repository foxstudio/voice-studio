<script module lang="ts">
	import { ApiError } from '$lib/api/client';
	import {
		activityTaskStepEntries as finalActivityTaskStepEntries,
		activityTaskStatusLabel as finalActivityTaskStatusLabel
	} from './activity-notice';
	import type {
		ActivityTask as FinalActivityTask,
		ActivityTaskStep as FinalActivityTaskStep
	} from './activity-notice';

	export const DEFAULT_VISIBLE_HISTORY_TASKS = 10;

	export function operationDetailCacheKey(projectId: string, operationId: string) {
		return JSON.stringify([projectId, operationId]);
	}

	export type OperationDetailCacheEntry = {
		task: FinalActivityTask;
		sourceSignature: string;
	};

	export function taskDetailFreshnessSignature(task: FinalActivityTask) {
		const taskIsSettled = task.status !== 'queued' && task.status !== 'running';
		return JSON.stringify({
			status: task.status,
			completedAt: task.completedAt ?? null,
			steps: finalActivityTaskStepEntries(task).map(({ step }) => ({
				id: step.id,
				status: step.status,
				result: step.status === 'todo' || step.status === 'running'
					? null
					: step.result ?? null
			})),
			finalResult: taskIsSettled ? task.finalResult ?? null : null,
			failureResult: taskIsSettled ? task.failureResult ?? null : null
		});
	}

	export function operationDetailCacheIsFresh(
		entry: OperationDetailCacheEntry | undefined,
		summaryTask: FinalActivityTask
	): entry is OperationDetailCacheEntry {
		return entry?.sourceSignature === taskDetailFreshnessSignature(summaryTask);
	}

	export function operationDetailRequestIsCurrent(
		request: { projectId: string; epoch: number },
		current: { projectId: string; epoch: number }
	) {
		return request.projectId === current.projectId
			&& request.epoch === current.epoch;
	}

	export function operationDetailLoadErrorMessage(error: unknown) {
		if (
			error instanceof ApiError
			&& error.code === 'VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED'
		) return error.message;
		return '完整任务详情加载失败，请稍后重试。';
	}

	export function historyTaskShouldLoadDetail(
		task: Pick<FinalActivityTask, 'operationId' | 'detailAvailable'>,
		wasExpanded: boolean,
		hasCachedDetail: boolean
	) {
		return !wasExpanded
			&& task.detailAvailable === true
			&& Boolean(task.operationId)
			&& !hasCachedDetail;
	}

	export function defaultHistoryTaskLimit(full: boolean, visibleActiveTaskCount: number) {
		return full ? DEFAULT_VISIBLE_HISTORY_TASKS : Math.max(0, DEFAULT_VISIBLE_HISTORY_TASKS - visibleActiveTaskCount);
	}

	export function hiddenHistoryTaskCount(totalHistoryTaskCount: number, visibleHistoryTaskCount: number) {
		return Math.max(0, totalHistoryTaskCount - visibleHistoryTaskCount);
	}

	export function resolvedHistoryTaskCount(
		loadedHistoryTaskCount: number,
		loadedOperationHistoryCount: number,
		operationHistoryTotal: number
	) {
		return loadedHistoryTaskCount + Math.max(
			0,
			operationHistoryTotal - loadedOperationHistoryCount
		);
	}

	export type HistoryPaginationAction = 'expand' | 'load' | 'collapse' | null;

	export function historyPaginationAction(
		showAllHistory: boolean,
		hiddenLoadedHistoryCount: number,
		canLoadMore: boolean
	): HistoryPaginationAction {
		if (!showAllHistory && hiddenLoadedHistoryCount > 0) return 'expand';
		if (canLoadMore) return 'load';
		if (showAllHistory) return 'collapse';
		return null;
	}

	export function newlyCompletedTaskIds(
		previousActiveTaskIds: readonly string[],
		tasks: readonly Pick<FinalActivityTask, 'id' | 'status'>[]
	) {
		const historyIds = new Set(
			tasks
				.filter((task) => task.status !== 'queued' && task.status !== 'running')
				.map((task) => task.id)
		);
		return previousActiveTaskIds.filter((taskId) => historyIds.has(taskId));
	}

	export function synchronizeTerminalTaskDetail(
		summary: FinalActivityTask,
		detail: FinalActivityTask
	): FinalActivityTask {
		if (summary.status !== 'failed' && summary.status !== 'cancelled') return detail;
		const closeStep = (step: FinalActivityTaskStep): FinalActivityTaskStep => {
			if (summary.status === 'failed') {
				if (step.status === 'running') return { ...step, status: 'failed' };
				if (step.status === 'todo') return { ...step, status: 'cancelled' };
			}
			if (
				summary.status === 'cancelled'
				&& (step.status === 'running' || step.status === 'todo')
			) return { ...step, status: 'cancelled' };
			return step;
		};
		return {
			...detail,
			status: summary.status,
			stage: summary.stage,
			progress: summary.progress,
			completedAt: summary.completedAt ?? detail.completedAt,
			failureResult: summary.failureResult ?? detail.failureResult,
			cancellable: false,
			stages: detail.stages?.map((stage) => ({
				...stage,
				steps: stage.steps.map(closeStep)
			})),
			steps: detail.steps?.map(closeStep)
		};
	}

	export function taskCanDelete(task: Pick<FinalActivityTask, 'status' | 'deletable'>) {
		return task.deletable === true;
	}

	export function taskStageLabel(task: FinalActivityTask) {
		if (task.status === 'failed') {
			const failedStep = finalActivityTaskStepEntries(task)
				.find(({ step }) => step.status === 'failed')
				?.step;
			return failedStep ? `失败在：${failedStep.label}` : '任务失败';
		}
		if (task.status === 'cancelled') return '任务已取消';
		return task.stage || finalActivityTaskStatusLabel(task.status);
	}

	export function taskFailureSummary(task: FinalActivityTask) {
		return task.failureResult?.summary || task.detail || '';
	}

	export function sortActivityTasksByRecency<T extends {
		id: string;
		createdAt?: string | null;
		startedAt?: string | null;
		completedAt?: string | null;
	}>(tasks: readonly T[]): T[] {
		return tasks
			.map((task, index) => ({
				task,
				index,
				timestamp: Date.parse(task.completedAt || task.startedAt || task.createdAt || '')
			}))
			.sort((left, right) => {
				const leftTime = Number.isFinite(left.timestamp) ? left.timestamp : Number.NEGATIVE_INFINITY;
				const rightTime = Number.isFinite(right.timestamp) ? right.timestamp : Number.NEGATIVE_INFINITY;
				return rightTime - leftTime || left.index - right.index;
			})
			.map(({ task }) => task);
	}

	export function finalActivityTaskResult(task: FinalActivityTask): {
		result: NonNullable<FinalActivityTask['finalResult']>;
		step?: FinalActivityTaskStep;
	} | undefined {
		if (task.status !== 'success') return undefined;
		if (task.kind === 'source_audio' || task.kind === 'stems') return undefined;
		if (task.executionScope === 'partial') return undefined;
		if (task.finalResult) return { result: task.finalResult };
		const entries = task.stages?.length
			? task.stages.flatMap((stage) => stage.steps)
			: task.steps ?? [];
		const step = [...entries].reverse().find((item) => item.result);
		return step?.result ? { result: step.result, step } : undefined;
	}

</script>

<script lang="ts">
	import { AlertTriangle, Check, ChevronDown, ChevronRight, CircleOff, CircleStop, Clock3, Info, RotateCcw, Trash2 } from 'lucide-svelte';
	import { untrack } from 'svelte';
	import { hoverTooltip } from '$lib/components/shared/hover-tooltip';
	import {
		activityTaskProgress,
		activityTaskStepEntries,
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
	import TaskWorkflowStages from './TaskWorkflowStages.svelte';
	import CommandSpinner from './CommandSpinner.svelte';

	type TaskStepResultDialogComponent = (
		typeof import('./TaskStepResultDialog.svelte')
	)['default'];

	let {
		projectId = '',
		tasks = [],
		operationHistoryTotal = 0,
		operationHistoryLoaded = 0,
		operationHistoryHasMore = false,
		operationHistoryLoading = false,
		onLoadMoreHistory = undefined,
		onCancelTask = undefined,
		onDeleteTask = undefined,
		onRetryTask = undefined,
		onLoadTaskDetail = undefined,
		onSeekTimeline = undefined,
		onPlayTimelineRange = undefined,
		pulseKey = 0,
		full = false
	}: {
		projectId?: string;
		tasks?: ActivityTask[];
		operationHistoryTotal?: number;
		operationHistoryLoaded?: number;
		operationHistoryHasMore?: boolean;
		operationHistoryLoading?: boolean;
		onLoadMoreHistory?: () => void | Promise<unknown>;
		onCancelTask?: (task: ActivityTask) => void | Promise<void>;
		onDeleteTask?: (task: ActivityTask) => void | Promise<void>;
		onRetryTask?: (task: ActivityTask) => void | Promise<void>;
		onLoadTaskDetail?: (projectId: string, operationId: string) => Promise<ActivityTask | null>;
		onSeekTimeline?: (timeMs: number) => void;
		onPlayTimelineRange?: (range: { startMs: number; endMs: number }) => void;
		pulseKey?: number;
		full?: boolean;
	} = $props();

	let pulsing = $state(false);
	let highlightedTaskId = $state('');
	let expandedHistoryIds = $state<string[]>([]);
	let showAllHistory = $state(false);
	let selectedResult = $state<{ taskId: string; stepId: string | null; final?: boolean } | null>(null);
	let detailCacheByOperationId = $state<Record<string, OperationDetailCacheEntry>>({});
	let detailErrorsByOperationId = $state<Record<string, string>>({});
	let loadingDetailSignaturesByOperationId = $state<Record<string, string>>({});
	let detailRequestVersionsByOperationId = $state<Record<string, number>>({});
	let detailCacheProjectId = '';
	let detailCacheEpoch = 0;
	let handledPulseKey = 0;
	let previousActiveTaskIds: string[] = [];
	let nowMs = $state(Date.now());
	let taskStepResultDialogPromise: Promise<TaskStepResultDialogComponent> | null = null;

	function loadTaskStepResultDialog() {
		taskStepResultDialogPromise ??= import('./TaskStepResultDialog.svelte')
			.then((module) => module.default)
			.catch((error) => {
				taskStepResultDialogPromise = null;
				throw error;
			});
		return taskStepResultDialogPromise;
	}

	const uniqueTasks = $derived.by(() => {
		const seen = new Set<string>();
		const deduplicated = tasks.filter((task) => {
			if (seen.has(task.id)) return false;
			seen.add(task.id);
			return true;
		});
		return sortActivityTasksByRecency(deduplicated);
	});
	const activeTasks = $derived(uniqueTasks.filter(isActiveActivityTask));
	const historyTasks = $derived(uniqueTasks.filter((task) => !isActiveActivityTask(task)));
	const visibleActiveTasks = $derived(full ? activeTasks : activeTasks.slice(0, 5));
	const availableHistorySlots = $derived(defaultHistoryTaskLimit(full, visibleActiveTasks.length));
	const visibleHistoryTasks = $derived(showAllHistory ? historyTasks : historyTasks.slice(0, availableHistorySlots));
	const visibleTasks = $derived([...visibleActiveTasks, ...visibleHistoryTasks]);
	const hiddenLoadedHistoryCount = $derived(
		hiddenHistoryTaskCount(historyTasks.length, visibleHistoryTasks.length)
	);
	const totalHistoryTaskCount = $derived(resolvedHistoryTaskCount(
		historyTasks.length,
		operationHistoryLoaded,
		operationHistoryTotal
	));
	const unloadedHistoryCount = $derived(
		Math.max(0, totalHistoryTaskCount - historyTasks.length)
	);
	const paginationAction = $derived(historyPaginationAction(
		showAllHistory,
		hiddenLoadedHistoryCount,
		operationHistoryHasMore && Boolean(onLoadMoreHistory)
	));
	const selectedResultContext = $derived.by(() => {
		if (!selectedResult) return null;
		const summaryTask = uniqueTasks.find((item) => item.id === selectedResult?.taskId);
		const cacheEntry = summaryTask?.operationId
			? detailCacheByOperationId[
				operationDetailCacheKey(projectId, summaryTask.operationId)
			]
			: undefined;
		const task = summaryTask && operationDetailCacheIsFresh(cacheEntry, summaryTask)
			? synchronizeTerminalTaskDetail(summaryTask, cacheEntry.task)
			: summaryTask;
		if (task && selectedResult.final) {
			const finalResult = finalActivityTaskResult(task);
			if (!finalResult) return null;
			const entries = activityTaskStepEntries(task);
			const stepIndex = finalResult.step
				? entries.findIndex((item) => item.step.id === finalResult.step?.id)
				: -1;
			return {
				task,
				stepId: finalResult.step?.id ?? '',
				stepLabel: finalResult.step?.label ?? '最终结果',
				stepPositionLabel: stepIndex >= 0 ? `第 ${stepIndex + 1} / ${entries.length} 项` : '',
				result: finalResult.result,
				durationLabel: finalResult.step ? stepTiming(finalResult.step, task) : duration(task)
			};
		}
		if (task && selectedResult.stepId === null && task.failureResult) {
			return {
				task,
				stepId: '',
				stepLabel: '错误详情',
				stepPositionLabel: '',
				result: task.failureResult,
				durationLabel: duration(task)
			};
		}
		const entries = task ? activityTaskStepEntries(task) : [];
		const stepIndex = entries.findIndex((item) => item.step.id === selectedResult?.stepId);
		const entry = stepIndex >= 0 ? entries[stepIndex] : undefined;
		const step = entry?.step;
		return task && step?.result ? {
			task,
			stepId: step.id,
			stepLabel: step.label,
			stepPositionLabel: entry?.stage
				? `${entry.stage.label} · 第 ${entry.stage.steps.findIndex((item) => item.id === step.id) + 1} / ${entry.stage.steps.length} 项`
				: `第 ${stepIndex + 1} / ${entries.length} 项`,
			result: step.result,
			durationLabel: stepTiming(step, task)
		} : null;
	});

	$effect(() => {
		const nextProjectId = projectId;
		if (nextProjectId === detailCacheProjectId) return;
		detailCacheProjectId = nextProjectId;
		detailCacheEpoch += 1;
		detailCacheByOperationId = {};
		detailErrorsByOperationId = {};
		loadingDetailSignaturesByOperationId = {};
		detailRequestVersionsByOperationId = {};
		selectedResult = null;
	});

	$effect(() => {
		if (!activeTasks.length) return;
		nowMs = Date.now();
		const timer = window.setInterval(() => (nowMs = Date.now()), 1000);
		return () => window.clearInterval(timer);
	});

	$effect(() => {
		const currentActiveTaskIds = activeTasks.map((task) => task.id);
		const completedTaskIds = newlyCompletedTaskIds(previousActiveTaskIds, uniqueTasks);
		if (completedTaskIds.length) {
			const currentExpandedIds = untrack(() => expandedHistoryIds);
			expandedHistoryIds = Array.from(new Set([...currentExpandedIds, ...completedTaskIds]));
			for (const taskId of completedTaskIds) {
				const task = uniqueTasks.find((item) => item.id === taskId);
				if (task?.detailAvailable) void loadDetailedTask(task, true);
			}
		}
		previousActiveTaskIds = currentActiveTaskIds;
	});

	$effect(() => {
		if (!selectedResult) return;
		const summaryTask = uniqueTasks.find((task) => task.id === selectedResult?.taskId);
		if (!summaryTask?.operationId || summaryTask.detailAvailable !== true) return;
		const cacheKey = operationDetailCacheKey(projectId, summaryTask.operationId);
		untrack(() => {
			if (!operationDetailCacheIsFresh(detailCacheByOperationId[cacheKey], summaryTask)) {
				void loadDetailedTask(summaryTask);
			}
		});
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
		if (!task.cancellable || task.actionPending || !onCancelTask) return;
		void onCancelTask(task);
	}

	function deleteTask(task: ActivityTask) {
		if (!taskCanDelete(task) || task.actionPending || !onDeleteTask) return;
		void onDeleteTask(task);
	}

	function retryTask(task: ActivityTask) {
		if (!task.operationId || task.actionPending || !onRetryTask || (task.status !== 'failed' && task.status !== 'cancelled')) return;
		void onRetryTask(task);
	}

	async function handleHistoryPagination() {
		if (operationHistoryLoading) return;
		if (paginationAction === 'expand') {
			showAllHistory = true;
			return;
		}
		if (paginationAction === 'load') {
			await onLoadMoreHistory?.();
			return;
		}
		if (paginationAction === 'collapse') showAllHistory = false;
	}

	function taskDetailCacheKey(task: ActivityTask) {
		return task.operationId
			? operationDetailCacheKey(projectId, task.operationId)
			: '';
	}

	function displayedHistoryTask(task: ActivityTask) {
		const cacheKey = taskDetailCacheKey(task);
		const cacheEntry = cacheKey
			? detailCacheByOperationId[cacheKey]
			: undefined;
		return operationDetailCacheIsFresh(cacheEntry, task)
			? synchronizeTerminalTaskDetail(task, cacheEntry.task)
			: task;
	}

	function historyTaskDetailIsLoading(task: ActivityTask) {
		const cacheKey = taskDetailCacheKey(task);
		return Boolean(cacheKey && loadingDetailSignaturesByOperationId[cacheKey]);
	}

	function historyTaskDetailError(task: ActivityTask) {
		const cacheKey = taskDetailCacheKey(task);
		return cacheKey ? detailErrorsByOperationId[cacheKey] ?? '' : '';
	}

	function toggleHistoryTask(task: ActivityTask) {
		const wasExpanded = expandedHistoryIds.includes(task.id);
		const cacheKey = taskDetailCacheKey(task);
		expandedHistoryIds = wasExpanded
			? expandedHistoryIds.filter((id) => id !== task.id)
			: [...expandedHistoryIds, task.id];
		if (historyTaskShouldLoadDetail(
			task,
			wasExpanded,
			Boolean(
				cacheKey
				&& operationDetailCacheIsFresh(detailCacheByOperationId[cacheKey], task)
			)
		)) {
			void loadDetailedTask(task);
		}
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

	function stepTiming(step: ActivityTaskStep, task: ActivityTask) {
		return activityTaskStepTimingLabel(step, task, nowMs);
	}

	async function loadDetailedTask(task: ActivityTask, force = false) {
		const expectedProjectId = projectId;
		const expectedEpoch = detailCacheEpoch;
		const operationId = task.operationId;
		const cacheKey = operationId
			? operationDetailCacheKey(expectedProjectId, operationId)
			: '';
		const sourceSignature = taskDetailFreshnessSignature(task);
		if (
			!expectedProjectId
			|| !operationId
			|| task.detailAvailable !== true
			|| !onLoadTaskDetail
			|| (!force && (
				operationDetailCacheIsFresh(detailCacheByOperationId[cacheKey], task)
				|| loadingDetailSignaturesByOperationId[cacheKey] === sourceSignature
			))
		) return;
		const requestVersion = (
			detailRequestVersionsByOperationId[cacheKey] ?? 0
		) + 1;
		detailRequestVersionsByOperationId = {
			...detailRequestVersionsByOperationId,
			[cacheKey]: requestVersion
		};
		loadingDetailSignaturesByOperationId = {
			...loadingDetailSignaturesByOperationId,
			[cacheKey]: sourceSignature
		};
		detailErrorsByOperationId = {
			...detailErrorsByOperationId,
			[cacheKey]: ''
		};
		try {
			const detailedTask = await onLoadTaskDetail(
				expectedProjectId,
				operationId
			);
			if (!detailedTask) return;
			if (!operationDetailRequestIsCurrent(
				{ projectId: expectedProjectId, epoch: expectedEpoch },
				{ projectId, epoch: detailCacheEpoch }
			)) return;
			if (
				detailRequestVersionsByOperationId[cacheKey]
				!== requestVersion
			) return;
			detailCacheByOperationId = {
				...detailCacheByOperationId,
				[cacheKey]: {
					task: detailedTask,
					sourceSignature
				}
			};
		} catch (error) {
			if (
				detailRequestVersionsByOperationId[cacheKey]
				!== requestVersion
			) return;
			detailErrorsByOperationId = {
				...detailErrorsByOperationId,
				[cacheKey]: operationDetailLoadErrorMessage(error)
			};
		} finally {
			if (
				detailRequestVersionsByOperationId[cacheKey]
				=== requestVersion
			) {
				const {
					[cacheKey]: _completedRequest,
					...remainingLoadingSignatures
				} = loadingDetailSignaturesByOperationId;
				loadingDetailSignaturesByOperationId = remainingLoadingSignatures;
			}
		}
	}

	function showStepResult(task: ActivityTask, step: ActivityTaskStep) {
		if (!step.result) return;
		selectedResult = { taskId: task.id, stepId: step.id };
	}

	function showFailureResult(task: ActivityTask) {
		if (!task.failureResult) return;
		selectedResult = { taskId: task.id, stepId: null };
	}

	function showFinalResult(task: ActivityTask) {
		if (!finalActivityTaskResult(task)) return;
		selectedResult = { taskId: task.id, stepId: null, final: true };
	}
</script>

<section class="task-center" class:active={activeTasks.length > 0} class:pulsing class:full aria-label="后台任务进度">
	<header class="task-center-head">
		<span class="task-center-mark">
			{#if activeTasks.length}<CommandSpinner size={13} />{:else}<Clock3 size={13} />{/if}
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
									<span class="task-state" aria-hidden="true"><CommandSpinner size={13} /></span>
									<div class="task-title">
										<strong>{displayLabel(task)}</strong>
										<span>{activityTaskStatusLabel(task.status)}</span>
									</div>
									{#if task.cancellable || taskCanDelete(task)}
										<div class="task-actions">
											{#if task.cancellable}
												<button
													class="task-stop"
													type="button"
													aria-label={`停止${displayLabel(task)}`}
													aria-busy={task.actionPending === 'cancel'}
													use:hoverTooltip={'停止任务｜停止后台生成；替换已有片段时会保留原片段。'}
													disabled={Boolean(task.actionPending)}
													onclick={() => cancelTask(task)}
												>{#if task.actionPending === 'cancel'}<CommandSpinner size={14} />{:else}<CircleStop size={14} strokeWidth={1.8} />{/if}</button>
											{/if}
											{#if taskCanDelete(task)}
												<button
													class="task-delete"
													type="button"
													aria-label={`停止并删除${displayLabel(task)}`}
													aria-busy={task.actionPending === 'delete'}
													use:hoverTooltip={'停止并删除片段｜先停止后台生成，再从合成配音轨移除该片段。'}
													disabled={Boolean(task.actionPending)}
													onclick={() => deleteTask(task)}
												>{#if task.actionPending === 'delete'}<CommandSpinner size={14} />{:else}<Trash2 size={14} strokeWidth={1.8} />{/if}</button>
											{/if}
										</div>
									{/if}
								</div>
								<div class="task-details">
									{#if meta.length}
										<div class="task-meta" aria-label={meta.join('，')}>
											{#each meta as item}<span>{item}</span>{/each}
										</div>
									{/if}
									<div class="task-stage">
										<span>{taskStageLabel(task)}</span>
										{#if progress !== null}<strong>{progress}%</strong>{/if}
									</div>
									{#if task.summaryFacts?.length}
										<dl class="task-summary-facts" aria-label={`${displayLabel(task)}基础信息`}>
											{#each task.summaryFacts as fact}<div><dt>{fact.label}</dt><dd>{fact.value}</dd></div>{/each}
										</dl>
									{/if}
									{#if progress !== null}
										<div class="task-meter" aria-label={`进度 ${progress}%`}><i style={`width:${progress}%`}></i></div>
									{/if}
									<TaskWorkflowStages {task} {nowMs} onShowResult={(step) => showStepResult(task, step)} />
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
						<span>{totalHistoryTaskCount} 项</span>
					</div>
					<div class="task-list history-task-list">
						{#each visibleHistoryTasks as task (task.id)}
							{@const expanded = expandedHistoryIds.includes(task.id)}
							{@const displayedTask = displayedHistoryTask(task)}
							{@const detailLoading = historyTaskDetailIsLoading(task)}
							{@const detailError = historyTaskDetailError(task)}
							{@const meta = metadata(displayedTask)}
							{@const finalResult = finalActivityTaskResult(displayedTask)}
							<article
								class="task-row history-row"
								class:failed={task.status === 'failed'}
								class:highlighted={highlightedTaskId === task.id}
								class:expanded
							>
								<div class="history-row-head">
									<button class="history-summary" type="button" aria-expanded={expanded} onclick={() => toggleHistoryTask(task)}>
										<span class="task-state" aria-hidden="true">
											{#if task.status === 'success'}<Check size={13} />
											{:else if task.status === 'failed'}<AlertTriangle size={13} />
											{:else}<CircleOff size={13} />{/if}
										</span>
										<strong>{displayLabel(displayedTask)}{displayedTask.status === 'failed' ? ' · 失败' : displayedTask.status === 'cancelled' ? ` · ${displayedTask.stage === '已采用其他版本' ? '已采用其他版本' : '已取消'}` : ''}</strong>
										<span class="history-time"><b>{duration(displayedTask)}</b><time>{historyTime(displayedTask)}</time></span>
										<span class="history-chevron" aria-hidden="true"><ChevronRight size={13} /></span>
									</button>
									{#if (task.status === 'failed' || task.status === 'cancelled') && task.operationId && onRetryTask}
										<button
											class="task-retry"
											type="button"
											aria-label={`重试${displayLabel(task)}`}
											aria-busy={task.actionPending === 'retry'}
											use:hoverTooltip={'重试任务｜使用原参数重新提交这项任务。'}
											disabled={Boolean(task.actionPending)}
											onclick={() => retryTask(task)}
										>{#if task.actionPending === 'retry'}<CommandSpinner size={12} />{:else}<RotateCcw size={12} strokeWidth={1.9} />{/if}</button>
									{/if}
									{#if taskCanDelete(displayedTask)}
										<button
											class="task-delete"
											type="button"
											aria-label={`删除${displayLabel(displayedTask)}`}
											aria-busy={displayedTask.actionPending === 'delete'}
											use:hoverTooltip={'删除记录｜移除这条失败或已取消的配音任务及其残留片段。'}
											disabled={Boolean(displayedTask.actionPending)}
											onclick={() => deleteTask(displayedTask)}
										>{#if displayedTask.actionPending === 'delete'}<CommandSpinner size={12} />{:else}<Trash2 size={12} strokeWidth={1.9} />{/if}</button>
									{/if}
								</div>
								{#if expanded}
									<div class="task-details history-details">
										{#if detailLoading}
											<div class="task-detail-loading" role="status"><CommandSpinner size={12} />正在加载完整任务详情</div>
										{/if}
										{#if detailError}
											<div class="task-detail-error" role="alert"><AlertTriangle size={12} />{detailError}</div>
										{/if}
										{#if meta.length}
											<div class="task-meta" aria-label={meta.join('，')}>
												{#each meta as item}<span>{item}</span>{/each}
											</div>
										{/if}
										<div class="task-stage"><span>{taskStageLabel(displayedTask)}</span></div>
										{#if displayedTask.summaryFacts?.length}
											<dl class="task-summary-facts" aria-label={`${displayLabel(displayedTask)}基础信息`}>
												{#each displayedTask.summaryFacts as fact}<div><dt>{fact.label}</dt><dd>{fact.value}</dd></div>{/each}
											</dl>
										{/if}
										{#if finalResult}
											<button class="task-final-result" type="button" onclick={() => showFinalResult(displayedTask)}>
												<span><Check size={11} />最终结果</span>
												<strong>{finalResult.result.summary}</strong>
												<ChevronRight size={13} aria-hidden="true" />
											</button>
										{/if}
										<TaskWorkflowStages task={displayedTask} {nowMs} onShowResult={(step) => showStepResult(displayedTask, step)} />
										{#if displayedTask.status === 'failed' && (displayedTask.detail || displayedTask.failureResult)}
											<div class="task-error-detail">
												<small>{taskFailureSummary(displayedTask)}</small>
												{#if displayedTask.failureResult}
													<button
														class="step-result-trigger"
														type="button"
														aria-label={`查看“${displayLabel(displayedTask)}”的错误详情`}
														aria-haspopup="dialog"
														use:hoverTooltip={'查看错误详情｜定位失败原因、限制条件和处理建议。'}
														onclick={() => showFailureResult(displayedTask)}
													><Info size={11} /></button>
												{/if}
											</div>
										{/if}
									</div>
								{/if}
							</article>
						{/each}
					</div>
				</section>
			{/if}
			{#if paginationAction}
				<button
					class="task-history-toggle"
					type="button"
					aria-expanded={showAllHistory}
					aria-busy={operationHistoryLoading}
					disabled={operationHistoryLoading}
					onclick={handleHistoryPagination}
				>
					{#if operationHistoryLoading}
						<CommandSpinner size={11} />
						<span>正在加载更早记录</span>
					{:else if paginationAction === 'expand'}
						<span>查看已加载的 {hiddenLoadedHistoryCount} 条记录</span>
						<span class="history-toggle-chevron" aria-hidden="true"><ChevronDown size={12} /></span>
					{:else if paginationAction === 'load'}
						<span>加载更早记录{unloadedHistoryCount ? `（还剩 ${unloadedHistoryCount} 项）` : ''}</span>
						<span class="history-toggle-chevron load-more" aria-hidden="true"><ChevronDown size={12} /></span>
					{:else}
						<span>收起历史记录</span>
						<span class="history-toggle-chevron" aria-hidden="true"><ChevronDown size={12} /></span>
					{/if}
				</button>
			{/if}
		</div>
	{:else}
		<div class="task-center-empty">导入、分离、ASR 字幕和配音生成任务会按时间显示在这里。</div>
	{/if}
</section>

	{#if selectedResultContext}
		{#await loadTaskStepResultDialog()}
			<div class="task-detail-loading" role="status">
				<CommandSpinner size={12} />正在加载任务结果
			</div>
		{:then TaskStepResultDialog}
			<TaskStepResultDialog
					taskLabel={displayLabel(selectedResultContext.task)}
					stepId={selectedResultContext.stepId}
					stepLabel={selectedResultContext.stepLabel}
					stepPositionLabel={selectedResultContext.stepPositionLabel}
					result={selectedResultContext.result}
					durationLabel={selectedResultContext.durationLabel}
					onJumpToTime={onSeekTimeline}
					onPlayRange={onPlayTimelineRange}
					onClose={() => (selectedResult = null)}
				/>
		{:catch}
			<div class="task-detail-loading" role="alert">
				任务结果窗口加载失败，请关闭后重试
				<button type="button" onclick={() => (selectedResult = null)}>关闭</button>
			</div>
		{/await}
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
	.task-summary-facts { display: flex; flex-wrap: wrap; gap: 4px; margin: 0; }
	.task-summary-facts div {
		box-sizing: border-box;
		min-width: 54px;
		max-width: 60px;
		flex: 0 1 60px;
		padding: 4px 6px;
		border: 1px solid #2b353a;
		border-radius: 4px;
		background: #181e22;
	}
	.task-summary-facts dt { margin-bottom: 1px; color: #6f7f86; font-size: 7.5px; }
	.task-summary-facts dd { margin: 0; overflow: hidden; color: #aebbc0; font-size: 8.5px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
	.task-final-result {
		min-width: 0;
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: center;
		gap: 8px;
		padding: 7px 8px;
		border: 1px solid rgba(101, 175, 197, 0.22);
		border-radius: 5px;
		background: rgba(61, 119, 137, 0.08);
		color: #9fb0b7;
		text-align: left;
		cursor: pointer;
	}
	.task-final-result span { display: inline-flex; align-items: center; gap: 4px; color: #83bd9e; font-size: 9px; white-space: nowrap; }
	.task-final-result strong { min-width: 0; overflow: hidden; color: #c9d5d9; font-size: 9.5px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
	.task-final-result:hover { border-color: rgba(101, 175, 197, 0.4); background: rgba(61, 119, 137, 0.14); }
	.task-final-result:focus-visible { outline: 1px solid #69abc0; outline-offset: 1px; }
	.task-details small { color: #c28d8f; font-size: 9px; line-height: 1.45; }
	.task-error-detail { min-width: 0; display: flex; align-items: flex-start; gap: 4px; }
	.task-error-detail small { min-width: 0; flex: 1 1 auto; overflow-wrap: anywhere; }
	.task-error-detail .step-result-trigger { margin-top: -2px; color: #b98586; }
	.task-detail-error {
		display: flex;
		align-items: flex-start;
		gap: 6px;
		padding: 7px 8px;
		border: 1px solid rgba(226, 140, 89, 0.35);
		border-radius: 5px;
		background: rgba(113, 52, 24, 0.2);
		color: #e7b090;
		font-size: 10px;
		line-height: 1.45;
	}

	.task-meter { height: 3px; border-radius: 2px; background: #263238; overflow: hidden; }
	.task-meter i { display: block; height: 100%; background: #65afc5; transition: width 180ms ease; }

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

	.task-actions { display: flex; align-items: center; gap: 4px; }
	.task-stop,
	.task-delete {
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
	.task-delete { border-color: rgba(212, 108, 105, 0.46); background: rgba(136, 54, 52, 0.2); color: #efb0ad; }
	.task-stop:disabled,
	.task-delete:disabled { opacity: 0.42; cursor: not-allowed; }
	.task-stop:hover:not(:disabled),
	.task-delete:hover:not(:disabled) { border-color: rgba(230, 128, 124, 0.58); background: rgba(136, 54, 52, 0.22); color: #ffd0cd; }

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
	.history-summary:focus-visible {
		outline: none;
		background: rgba(72, 137, 157, 0.08);
		box-shadow: inset 2px 0 rgba(105, 171, 192, 0.58);
	}
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
	.task-history-toggle:disabled { cursor: wait; opacity: 0.75; }
	.task-history-toggle:focus-visible {
		outline: none;
		background: rgba(72, 137, 157, 0.08);
		box-shadow: inset 0 0 0 1px rgba(105, 171, 192, 0.22);
	}
	.history-toggle-chevron { display: grid; place-items: center; transition: transform 140ms ease; }
	.task-history-toggle[aria-expanded='true'] .history-toggle-chevron { transform: rotate(180deg); }
	.task-history-toggle[aria-expanded='true'] .history-toggle-chevron.load-more { transform: none; }

	@keyframes task-center-pulse {
		0% { box-shadow: 0 0 0 0 rgba(89, 183, 210, 0.42); }
		55% { box-shadow: 0 0 0 4px rgba(89, 183, 210, 0.14); }
		100% { box-shadow: 0 0 0 0 rgba(89, 183, 210, 0); }
	}
	@media (prefers-reduced-motion: reduce) {
		.task-center.pulsing, .history-chevron { animation: none; transition: none; }
	}
</style>
