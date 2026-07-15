<script lang="ts">
	import { AlertTriangle, Check, Clipboard, Info, LoaderCircle, PanelRightOpen, X } from 'lucide-svelte';
	import { hoverTooltip } from '$lib/components/shared/hover-tooltip';
	import { activityTaskSummary, type ActivityTask } from './activity-notice';

	let {
		kind,
		summary,
		detail = '',
		tasks = [],
		resetKey = '',
		onOpenTaskCenter = undefined
	}: {
		kind: 'idle' | 'success' | 'error';
		summary: string;
		detail?: string;
		tasks?: ActivityTask[];
		resetKey?: string;
		onOpenTaskCenter?: () => void;
	} = $props();

	let detailOpen = $state(false);
	let copied = $state(false);
	let retainedKind = $state<'success' | 'error'>('success');
	let retainedSummary = $state('');
	let retainedDetail = $state('');
	let currentResetKey = $state('');

	const taskSummary = $derived(activityTaskSummary(tasks));
	const hasRetainedMessage = $derived(Boolean(retainedSummary.trim()));
	const errorHasPriority = $derived(retainedKind === 'error' && hasRetainedMessage);
	const displayedKind = $derived(errorHasPriority ? 'error' : tasks.length ? 'running' : retainedKind);
	const displayedSummary = $derived(errorHasPriority || !tasks.length ? retainedSummary : taskSummary.text);
	const hasMessage = $derived(Boolean(displayedSummary.trim()));
	const showDetails = $derived(
		retainedKind === 'error' &&
		Boolean(retainedDetail.trim()) &&
		(retainedDetail.trim() !== retainedSummary.trim() || retainedDetail.length > 96 || retainedDetail.includes('\n'))
	);

	$effect(() => {
		if (resetKey === currentResetKey) return;
		currentResetKey = resetKey;
		retainedSummary = '';
		retainedDetail = '';
		detailOpen = false;
	});

	$effect(() => {
		const nextSummary = summary.trim();
		if (!nextSummary || kind === 'idle') return;
		retainedKind = kind;
		retainedSummary = nextSummary;
		retainedDetail = detail;
	});

	function handleWindowKeydown(event: KeyboardEvent) {
		if (!detailOpen || event.key !== 'Escape') return;
		event.preventDefault();
		detailOpen = false;
	}

	function dismissNotice() {
		retainedSummary = '';
		retainedDetail = '';
		detailOpen = false;
	}

	async function copyDetail() {
		try {
			await navigator.clipboard.writeText(retainedDetail);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			copied = false;
		}
	}

</script>

<svelte:window onkeydown={handleWindowKeydown} />

<div class="activity-slot" class:active={hasMessage || tasks.length > 0}>
	{#if hasMessage}
		<div class:error={displayedKind === 'error'} class:success={displayedKind === 'success'} class:running={displayedKind === 'running'} class="activity-message" role={displayedKind === 'error' ? 'alert' : 'status'} aria-live="polite">
			<span class="activity-icon" aria-hidden="true">
				{#if displayedKind === 'error'}
					<AlertTriangle size={13} />
				{:else if displayedKind === 'running'}
					<span class="task-spinner"><LoaderCircle size={13} /></span>
				{:else if displayedKind === 'success'}
					<Check size={13} />
				{:else}
					<Info size={13} />
				{/if}
			</span>
			<span class="activity-summary">{displayedSummary}</span>
				{#if showDetails}
					<button class="activity-more" type="button" use:hoverTooltip={'查看完整错误信息'} onclick={() => (detailOpen = true)}>更多</button>
				{/if}
				{#if tasks.length}
					<button class="task-count" class:single={tasks.length === 1} type="button" use:hoverTooltip={'在右侧任务中心查看完整进度和结果'} onclick={() => onOpenTaskCenter?.()}>
						{tasks.length > 1 ? taskSummary.countLabel : '详情'} <PanelRightOpen size={11} />
					</button>
				{/if}
			{#if hasRetainedMessage}
					<button class="activity-close" type="button" aria-label="关闭状态提醒" use:hoverTooltip={'关闭这条提醒'} onclick={dismissNotice}><X size={12} /></button>
				{/if}
		</div>
	{:else if tasks.length}
		<div class="activity-message running" role="status" aria-live="polite">
			<span class="activity-icon" aria-hidden="true"><span class="task-spinner"><LoaderCircle size={13} /></span></span>
			<span class="activity-summary">{taskSummary.text}</span>
				<button class="task-count" class:single={tasks.length === 1} type="button" use:hoverTooltip={'在右侧任务中心查看完整进度和结果'} onclick={() => onOpenTaskCenter?.()}>{tasks.length > 1 ? taskSummary.countLabel : '详情'} <PanelRightOpen size={11} /></button>
		</div>
	{/if}

</div>

{#if detailOpen}
	<div
		class="notice-detail-backdrop"
		role="presentation"
		onclick={(event) => {
			if (event.currentTarget === event.target) detailOpen = false;
		}}
	>
		<div class="notice-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="notice-detail-title">
			<header>
				<div>
					<strong id="notice-detail-title">处理详情</strong>
					<span>可复制给 Agent 继续排查</span>
				</div>
					<button type="button" aria-label="关闭详情" use:hoverTooltip={'关闭处理详情'} onclick={() => (detailOpen = false)}><X size={15} /></button>
			</header>
			<div class="notice-detail-summary"><AlertTriangle size={14} />{retainedSummary}</div>
			<pre>{retainedDetail}</pre>
			<footer>
				<button type="button" class="copy-detail" onclick={copyDetail}>
					{#if copied}<Check size={14} /> 已复制{:else}<Clipboard size={14} /> 复制详情{/if}
				</button>
				<button type="button" onclick={() => (detailOpen = false)}>关闭</button>
			</footer>
		</div>
	</div>
{/if}

<style>
	.activity-slot {
		width: 100%;
		height: 28px;
		min-height: 0;
		display: grid;
		place-items: center;
		box-sizing: border-box;
		position: relative;
		z-index: 30;
	}

	.activity-message {
		width: min(100%, 640px);
		height: 24px;
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 0 7px;
		box-sizing: border-box;
		border: 1px solid #344047;
		border-radius: 4px;
		background: rgba(18, 23, 27, 0.82);
		color: #b9c4c9;
		font-size: 10.5px;
		line-height: 1;
	}

	.activity-message.error {
		border-color: rgba(197, 80, 87, 0.42);
		color: #e5b7ba;
	}

	.activity-message.success {
		border-color: rgba(68, 151, 116, 0.42);
		color: #a9d5c1;
	}

	.activity-message.running {
		border-color: rgba(78, 145, 169, 0.46);
		color: #b9d6df;
	}

	.activity-icon {
		width: 16px;
		height: 16px;
		flex: 0 0 16px;
		display: grid;
		place-items: center;
		color: #8c9ba2;
	}

	.error .activity-icon { color: #e98288; }
	.success .activity-icon { color: #64bd99; }
	.running .activity-icon { color: #72b9ce; }

	.activity-summary {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.activity-more,
	.task-count,
	.activity-close {
		flex: 0 0 auto;
		border: 0;
		border-left: 1px solid rgba(255, 255, 255, 0.1);
		padding: 1px 3px 1px 7px;
		background: transparent;
		color: #cbd5da;
		font-size: 10px;
	}

	.activity-more:hover,
	.task-count:hover,
	.activity-close:hover { color: #fff; }

	.task-count {
		display: inline-flex;
		align-items: center;
		gap: 2px;
		white-space: nowrap;
	}

	.task-count.single { color: #91c7d6; }
	.activity-close { width: 21px; padding: 0 0 0 5px; }

	.task-spinner { display: inline-grid; place-items: center; animation: task-spin 900ms linear infinite; }

	.notice-detail-backdrop {
		position: fixed;
		inset: 0;
		z-index: 240;
		display: grid;
		place-items: center;
		padding: 18px;
		background: rgba(4, 7, 9, 0.66);
		backdrop-filter: blur(3px);
	}

	.notice-detail-dialog {
		width: min(640px, calc(100vw - 36px));
		max-height: min(620px, calc(100vh - 36px));
		display: grid;
		grid-template-rows: auto auto minmax(120px, 1fr) auto;
		border: 1px solid #3a464d;
		border-radius: 7px;
		background: #12171b;
		box-shadow: 0 24px 70px rgba(0, 0, 0, 0.56);
		overflow: hidden;
	}

	.notice-detail-dialog header,
	.notice-detail-dialog footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 10px 12px;
	}

	.notice-detail-dialog header { border-bottom: 1px solid #2b343a; }
	.notice-detail-dialog header div { display: grid; gap: 2px; }
	.notice-detail-dialog header strong { font-size: 12px; }
	.notice-detail-dialog header span { color: #7f8d95; font-size: 9.5px; }

	.notice-detail-dialog button {
		min-height: 28px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		border: 1px solid #38444b;
		border-radius: 4px;
		padding: 0 9px;
		background: #1a2126;
		color: #d5dde1;
		font-size: 10px;
	}

	.notice-detail-dialog header button { width: 28px; padding: 0; }

	.notice-detail-summary {
		display: flex;
		align-items: center;
		gap: 7px;
		padding: 9px 12px;
		color: #e3b4b7;
		font-size: 11px;
		background: rgba(126, 42, 43, 0.12);
	}

	.notice-detail-dialog pre {
		min-height: 0;
		margin: 0;
		padding: 12px;
		overflow: auto;
		white-space: pre-wrap;
		word-break: break-word;
		font: 10.5px/1.55 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		color: #b8c4ca;
		background: #0d1114;
		user-select: text;
	}

	.notice-detail-dialog footer {
		justify-content: flex-end;
		border-top: 1px solid #2b343a;
	}

	.notice-detail-dialog .copy-detail {
		color: #a9d8c6;
		border-color: #31574b;
	}

	@media (prefers-reduced-motion: reduce) {
		.notice-detail-backdrop { backdrop-filter: none; }
		.task-spinner { animation: none; }
	}

	@keyframes task-spin { to { transform: rotate(360deg); } }
</style>
