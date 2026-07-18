<script lang="ts">
	import type { HistoryItem } from '$lib/api/types';
	import { AudioLines } from 'lucide-svelte';
	import SubtitleTtsHistory from './SubtitleTtsHistory.svelte';

	let {
		items = [],
		selectedSegmentId = '',
		segmentLabels = {},
		script = '',
		targetLabel = '未选择配音目标',
		canGenerate = false,
		busy = false,
		appliedResultId = '',
		timelineClipPresent = false,
		applyingResultId = '',
		onOpenGenerate,
		onReuse,
		onApply = undefined,
		onDelete = undefined,
		onDeleteCurrent = undefined,
		onDeleteAll = undefined,
		selectionCount = 1,
		selectionContiguous = false,
		frameRate = 24
	}: {
		items?: HistoryItem[];
		selectedSegmentId?: string;
		segmentLabels?: Record<string, string>;
		script?: string;
		targetLabel?: string;
		canGenerate?: boolean;
		busy?: boolean;
		appliedResultId?: string;
		timelineClipPresent?: boolean;
		applyingResultId?: string;
		onOpenGenerate: () => void | Promise<void>;
		onReuse: (item: HistoryItem) => void | Promise<void>;
		onApply?: (item: HistoryItem) => void | Promise<void>;
		onDelete?: (item: HistoryItem) => void | Promise<void>;
		onDeleteCurrent?: () => void | Promise<void>;
		onDeleteAll?: () => void | Promise<void>;
		selectionCount?: number;
		selectionContiguous?: boolean;
		frameRate?: number;
	} = $props();
</script>

<section class="dubbing-workspace" aria-label="配音工作区">
	<header>
		<div class="title"><AudioLines size={15} /><strong>配音</strong></div>
		<span>{targetLabel}</span>
	</header>
	{#if selectedSegmentId}
		<div class="target-summary">
			<span>配音台词</span>
			<p>{script || '当前片段还没有配音台词，请先在字幕页补充。'}</p>
		</div>
	{:else}
		<p class="empty-target">选择本土化字幕或合成配音片段后，可生成、试听和替换对应声音。</p>
	{/if}
	<SubtitleTtsHistory
		{items}
		{selectedSegmentId}
		{segmentLabels}
		{canGenerate}
		{busy}
		{appliedResultId}
		canApplyToTimeline={Boolean(selectedSegmentId)}
		{timelineClipPresent}
		{applyingResultId}
		{onOpenGenerate}
		{onReuse}
		{onApply}
		{onDelete}
		{onDeleteCurrent}
		{onDeleteAll}
		{selectionCount}
		{selectionContiguous}
		{frameRate}
	/>
</section>

<style>
	.dubbing-workspace {
		display: grid;
		grid-template-rows: auto auto minmax(0, 1fr);
		gap: 10px;
		min-height: 0;
		height: 100%;
		padding: 12px 14px 0;
		box-sizing: border-box;
		overflow: hidden;
	}

	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		min-width: 0;
	}

	.title {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: #e5eef0;
	}

	header strong { font-size: 13px; }
	header span {
		overflow: hidden;
		color: #87949b;
		font-size: 10px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.target-summary {
		display: grid;
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

	.empty-target { color: #87949b; }

	.dubbing-workspace :global(.tts-history) {
		margin-top: 0;
		padding-top: 0;
		border-top: 0;
		overflow: hidden;
	}
</style>
