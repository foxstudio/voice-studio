<script lang="ts">
	import type { HistoryItem } from '$lib/api/types';
	import { ArrowRightLeft, ExternalLink, History, Plus, Repeat2, SlidersHorizontal, Trash2 } from 'lucide-svelte';
	import SubtitleAudioWaveform from './SubtitleAudioWaveform.svelte';

	let {
		items = [],
		selectedSegmentId = '',
		segmentLabels = {},
		canGenerate = false,
		busy = false,
		appliedResultId = '',
		canApplyToTimeline = false,
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
		canGenerate?: boolean;
		busy?: boolean;
		appliedResultId?: string;
		canApplyToTimeline?: boolean;
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

	let scope = $state<'current' | 'all'>('current');
	function historySegmentId(item: HistoryItem) {
		return item.localized_subtitle_id || item.segment_id || item.cue_id || '';
	}

	const currentItems = $derived(items.filter((item) => historySegmentId(item) === selectedSegmentId));
	const visibleItems = $derived((scope === 'current' ? currentItems : items).slice(0, scope === 'current' ? 8 : 40));

	function formatDate(value: string) {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat('zh-CN', {
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit'
		}).format(date);
	}

	function durationLabel(value: number | null) {
		if (!value) return '';
		return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} ms`;
	}

	function parameterSummary(item: HistoryItem) {
		const params = item.parameter_snapshot ?? {};
		const details = [item.engine_id];
		if (typeof params.speed === 'number') details.push(`语速 ${params.speed}`);
		if (typeof params.emotion_mode === 'string' && params.emotion_mode !== 'follow_reference') details.push(String(params.emotion_mode));
		return details.join(' · ');
	}

	function parameterJson(item: HistoryItem) {
		return JSON.stringify(item.parameter_snapshot ?? {}, null, 2);
	}

	function audioUrl(item: HistoryItem) {
		return `/api/history/${encodeURIComponent(item.result_id)}/audio`;
	}

	function waveformUrl(item: HistoryItem) {
		return `/api/history/${encodeURIComponent(item.result_id)}/waveform`;
	}
</script>

<section class="tts-history" aria-label="字幕配音记录">
	<div class="history-head">
		<div class="history-heading">
			<strong><History size={13} />配音记录</strong>
			<span>{selectionCount > 1 ? `已选 ${selectionCount} 条${selectionContiguous ? '连续' : ''}字幕` : currentItems.length ? `当前片段 ${currentItems.length} 次生成` : '当前片段尚未生成'}</span>
		</div>
		<div class="history-head-actions">
			{#if currentItems.length && onDeleteCurrent}
				<button class="delete-current" type="button" aria-label="删除当前字幕的全部配音记录" data-tooltip="删除当前字幕的全部配音记录：不会删除已经复制到时间线的音频片段。" onclick={onDeleteCurrent}><Trash2 size={13} />清空当前</button>
			{/if}
			{#if items.length && onDeleteAll}
				<button class="delete-current" type="button" aria-label="删除当前项目的全部配音记录" data-tooltip="清空全部：删除当前项目所有字幕的配音历史，不影响已经复制到时间线的音频片段。" onclick={onDeleteAll}><Trash2 size={13} />清空全部</button>
			{/if}
			<button class="open-generate" type="button" aria-label={currentItems.length ? '调整当前片段的配音参数' : '送到语音合成'} disabled={!canGenerate} data-tooltip={currentItems.length ? '调整配音参数：打开语音合成并带入当前片段的台词、参考音色和时间范围。' : '送到语音合成：带入当前片段的台词、参考音色和时间范围。'} onclick={onOpenGenerate}>
				<ExternalLink size={14} />{selectionCount > 1 && selectionContiguous ? '合并配音' : currentItems.length ? '调整配音' : '送到合成'}
			</button>
		</div>
	</div>

	<div class="history-scope" role="tablist" aria-label="配音记录范围">
		<button class:active={scope === 'current'} type="button" role="tab" aria-selected={scope === 'current'} onclick={() => (scope = 'current')}>当前片段 <span>{currentItems.length}</span></button>
		<button class:active={scope === 'all'} type="button" role="tab" aria-selected={scope === 'all'} onclick={() => (scope = 'all')}>全部片段 <span>{items.length}</span></button>
	</div>

	{#if visibleItems.length}
		<div class="history-list">
			{#each visibleItems as item (item.result_id)}
				<article class="history-row" class:current={item.segment_id === selectedSegmentId}>
					<div class="history-row-head">
						<div class="history-title">
							<strong data-tooltip={item.input_text || '无台词'}>{scope === 'current' ? '生成记录' : segmentLabels[historySegmentId(item)] || historySegmentId(item) || '未关联片段'}</strong>
							<span>{formatDate(item.created_at)}{item.duration_ms ? ` · ${durationLabel(item.duration_ms)}` : ''}</span>
						</div>
						<div class="row-actions">
							<details class="parameter-details">
								<summary aria-label="查看生成参数" data-tooltip="查看生成参数"><SlidersHorizontal size={13} /></summary>
								<pre>{parameterJson(item)}</pre>
							</details>
							<button class="reuse-button" type="button" disabled={busy || !canGenerate} aria-label="沿用这次参数生成" data-tooltip="沿用参数：使用这次记录的模型和参数，为当前选中的字幕重新生成配音。" onclick={() => onReuse(item)}><Repeat2 size={13} /></button>
							{#if scope === 'current' && onApply && item.output_path}
								<button class="apply-button" class:applied={item.result_id === appliedResultId} type="button" disabled={!canApplyToTimeline || item.result_id === appliedResultId || Boolean(applyingResultId)} aria-label={item.result_id === appliedResultId ? '当前已采用这条历史声音' : timelineClipPresent ? '替换当前配音片段' : '添加到合成配音轨'} data-tooltip={item.result_id === appliedResultId ? '当前版本：这条声音已在合成配音轨中使用。' : canApplyToTimeline ? timelineClipPresent ? '替换配音：采用这条同字幕的历史声音，并保持字幕入点对齐。' : '添加配音：把这条历史声音按字幕入点添加到合成配音轨；如与现有片段重叠，会自动显示在新的配音分轨。' : '请先选择一条字幕。'} onclick={() => onApply(item)}>{#if timelineClipPresent}<ArrowRightLeft size={13} />{:else}<Plus size={13} />{/if}</button>
							{/if}
							{#if onDelete}
								<button class="delete-record" type="button" aria-label="删除这条配音记录" data-tooltip="删除这条配音记录：不会删除已经复制到时间线的音频片段。" onclick={() => onDelete(item)}><Trash2 size={13} /></button>
							{/if}
						</div>
					</div>
					{#if scope === 'all'}<p class="script-line" data-tooltip={item.input_text || '无台词'}>{item.input_text || '无台词'}</p>{/if}
					<span class="parameter-summary" data-tooltip={`生成参数：${parameterSummary(item)}`}><SlidersHorizontal size={11} />{parameterSummary(item)}</span>
					{#if item.output_path}
						<SubtitleAudioWaveform label="配音" audioUrl={audioUrl(item)} waveformUrl={waveformUrl(item)} downloadUrl={audioUrl(item)} {frameRate} />
					{/if}
				</article>
			{/each}
		</div>
	{:else}
		<p class="history-empty">{scope === 'current' ? '当前字幕还没有配音记录。先送到语音合成页调好第一版，之后就能在这里直接复用。' : '当前项目还没有语音合成记录。'}</p>
	{/if}
</section>

<style>
	.tts-history {
		display: grid;
		grid-template-rows: auto auto minmax(0, 1fr);
		gap: 8px;
		min-height: 0;
		margin: 10px 0 0;
		padding: 10px 0 0;
		border-top: 1px solid rgba(255, 255, 255, 0.1);
	}

	.history-head,
	.history-row-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}

	.history-head-actions {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}

	.history-heading,
	.history-title {
		display: grid;
		gap: 2px;
		min-width: 0;
	}

	.history-heading strong {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		color: #e0e9ec;
		font-size: 11px;
	}

	.history-heading span,
	.history-title span {
		color: #7f8c94;
		font-size: 9px;
	}

	.open-generate,
	.reuse-button,
	.apply-button,
	.parameter-details summary {
		display: inline-grid;
		place-items: center;
		flex: 0 0 auto;
		border: 1px solid rgba(87, 208, 200, 0.45);
		border-radius: 5px;
		background: #173330;
		color: #d8fffb;
		cursor: pointer;
	}

	.open-generate {
		display: inline-flex;
		gap: 5px;
		min-height: 26px;
		padding: 0 7px;
		font-size: 10px;
		white-space: nowrap;
	}

	.open-generate:disabled,
	.reuse-button:disabled,
	.apply-button:disabled {
		cursor: not-allowed;
		opacity: 0.45;
	}

	.apply-button {
		width: 25px;
		height: 25px;
		border-color: rgba(155, 135, 245, 0.52);
		background: #27213f;
		color: #e9e2ff;
	}

	.delete-current,
	.delete-record {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 4px;
		min-height: 25px;
		box-sizing: border-box;
		border: 1px solid rgba(229, 117, 106, 0.34);
		border-radius: 5px;
		background: transparent;
		color: #df9e96;
		font-size: 10px;
		cursor: pointer;
	}

	.delete-current { padding: 0 6px; }
	.delete-record { width: 25px; }

	.delete-current:hover,
	.delete-record:hover {
		border-color: rgba(255, 125, 112, 0.78);
		color: #ffd0cb;
	}

	.apply-button.applied {
		border-color: rgba(87, 208, 200, 0.48);
		background: #173330;
		color: #d8fffb;
	}

	.history-scope {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 2px;
		padding: 2px;
		border-radius: 5px;
		background: #10151a;
	}

	.history-scope button {
		min-height: 25px;
		border: 0;
		border-radius: 4px;
		background: transparent;
		color: #7f8c94;
		font-size: 10px;
		cursor: pointer;
	}

	.history-scope button.active {
		background: #20282e;
		color: #e5eef1;
	}

	.history-scope span {
		margin-left: 3px;
		color: #69d8d0;
	}

	.history-list {
		display: grid;
		align-content: start;
		min-height: 0;
		max-height: none;
		overflow-x: hidden;
		overflow-y: auto;
		scrollbar-gutter: stable;
		overscroll-behavior: contain;
	}

	.history-row {
		display: grid;
		gap: 6px;
		padding: 8px 0;
		border-bottom: 1px solid rgba(255, 255, 255, 0.07);
	}

	.history-row.current {
		border-left: 2px solid rgba(87, 208, 200, 0.7);
		padding-left: 7px;
	}

	.history-title strong {
		max-width: 100%;
		overflow: hidden;
		color: #d9e2e5;
		font-size: 10px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.row-actions {
		display: inline-flex;
		align-items: center;
		gap: 4px;
	}

	.reuse-button,
	.parameter-details summary {
		width: 25px;
		height: 25px;
		box-sizing: border-box;
		border-color: #364149;
		background: #171d22;
		color: #bfcbd0;
	}

	.reuse-button:hover:not(:disabled),
	.parameter-details summary:hover,
	.parameter-details summary:focus-visible {
		border-color: rgba(87, 208, 200, 0.6);
		color: #8bf1e7;
		outline: none;
	}

	.parameter-details {
		position: relative;
	}

	.parameter-details summary {
		list-style: none;
	}

	.parameter-details summary::-webkit-details-marker {
		display: none;
	}

	.parameter-details pre {
		position: absolute;
		right: 0;
		z-index: 2;
		width: min(280px, 70vw);
		max-height: 180px;
		margin: 5px 0 0;
		overflow: auto;
		border: 1px solid #3a474e;
		border-radius: 5px;
		padding: 7px;
		background: #10151a;
		box-shadow: 0 10px 24px rgba(0, 0, 0, 0.32);
		color: #b9c8cd;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 9px;
		line-height: 1.45;
		white-space: pre-wrap;
		word-break: break-word;
	}

	.script-line {
		display: -webkit-box;
		margin: 0;
		overflow: hidden;
		color: #abb7bc;
		font-size: 10px;
		line-height: 1.4;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
	}

	.parameter-summary {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		overflow: hidden;
		color: #77858c;
		font-size: 9px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.history-empty {
		margin: 0;
		padding: 8px 1px;
		color: #77858c;
		font-size: 10px;
		line-height: 1.5;
	}
</style>
