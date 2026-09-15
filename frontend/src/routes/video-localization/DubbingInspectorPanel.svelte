<script lang="ts">
	import type { HistoryItem } from '$lib/api/types';
	import SubtitleTtsHistory from './SubtitleTtsHistory.svelte';

	let {
		items = [],
		total = 0,
		loaded = 0,
		hasMore = false,
		loadingMore = false,
		onLoadMore = undefined,
		selectedSegmentId = '',
		segmentLabels = {},
		script = '',
		targetLabel = '未选择配音目标',
		canGenerate = false,
		sourceSelectionAdvisory = null,
		busy = false,
		appliedResultId = '',
		selectedResultId = '',
		usedResultIds = [],
		timelineClipPresent = false,
		applyingResultId = '',
		onOpenGenerate,
		onReuse,
		onApply = undefined,
		onDelete = undefined,
		onDeleteCurrent = undefined,
		onDeleteAll = undefined,
		onCleanupUnused = undefined,
		onHistoryDragStart = undefined,
		selectionCount = 1,
		selectionContiguous = false,
		historyScope = $bindable('current')
	}: {
		items?: HistoryItem[];
		total?: number;
		loaded?: number;
		hasMore?: boolean;
		loadingMore?: boolean;
		onLoadMore?: () => void | Promise<unknown>;
		selectedSegmentId?: string;
		segmentLabels?: Record<string, string>;
		script?: string;
		targetLabel?: string;
		canGenerate?: boolean;
		sourceSelectionAdvisory?: string | null;
		busy?: boolean;
		appliedResultId?: string;
		selectedResultId?: string;
		usedResultIds?: string[];
		timelineClipPresent?: boolean;
		applyingResultId?: string;
		onOpenGenerate: () => void | Promise<void>;
		onReuse: (item: HistoryItem) => void | Promise<void>;
		onApply?: (item: HistoryItem) => void | Promise<void>;
		onDelete?: (item: HistoryItem) => void | Promise<void>;
		onDeleteCurrent?: () => void | Promise<void>;
		onDeleteAll?: () => void | Promise<void>;
		onCleanupUnused?: (scope: 'current' | 'all') => void | Promise<void>;
		onHistoryDragStart?: (item: HistoryItem) => void;
		selectionCount?: number;
		selectionContiguous?: boolean;
		historyScope?: 'current' | 'all';
	} = $props();
</script>

<section class="dubbing-workspace" aria-label="配音工作区">
	{#if selectedSegmentId}
		<div class="target-summary">
			<span>配音台词 <small>· {targetLabel}</small></span>
			<p>{script || '当前片段还没有配音台词，请先在字幕页补充。'}</p>
			{#if sourceSelectionAdvisory}
				<p class="selection-warning" role="status">{sourceSelectionAdvisory}</p>
			{:else if selectionCount > 1 && !selectionContiguous}
				<p class="selection-warning">所选字幕不连续，将按时间顺序合并台词并覆盖所选范围。</p>
			{/if}
		</div>
	{:else}
		<p class="empty-target">选择本土化字幕或合成配音片段后，可生成、试听和替换对应声音。</p>
	{/if}
	<SubtitleTtsHistory
		bind:scope={historyScope}
		{items}
		{total}
		{loaded}
		{hasMore}
		{loadingMore}
		{onLoadMore}
		{selectedSegmentId}
		{segmentLabels}
		{canGenerate}
		{sourceSelectionAdvisory}
		{busy}
		{appliedResultId}
		{selectedResultId}
		{usedResultIds}
		canApplyToTimeline={Boolean(selectedSegmentId)}
		{timelineClipPresent}
		{applyingResultId}
		{onOpenGenerate}
		{onReuse}
		{onApply}
		{onDelete}
		{onDeleteCurrent}
		{onDeleteAll}
		{onCleanupUnused}
		{onHistoryDragStart}
		{selectionCount}
		{selectionContiguous}
	/>
</section>

<style>
	.dubbing-workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		grid-template-rows: auto minmax(0, 1fr);
		gap: 10px;
		min-width: 0;
		min-height: 0;
		height: 100%;
		padding: 12px 14px 0;
		box-sizing: border-box;
		overflow: hidden;
	}

	.target-summary small { color: #718087; font-size: 9px; font-weight: 500; }
	.target-summary {
		display: grid;
		min-width: 0;
		overflow-wrap: anywhere;
		gap: 5px;
		padding: 9px 0;
		border-block: 1px solid rgba(255, 255, 255, 0.08);
	}

	.target-summary span {
		color: #859299;
		font-size: 9px;
	}

	.target-summary p,
	.empty-target {
		margin: 0;
		color: #d9e2e5;
		font-size: 11px;
		line-height: 1.55;
	}

	.target-summary .selection-warning {
		color: #f0b56d;
		font-size: 10px;
	}

	.empty-target { color: #87949b; }

	.dubbing-workspace :global(.tts-history) {
		margin-top: 0;
		padding-top: 0;
		border-top: 0;
		overflow: hidden;
	}
</style>
