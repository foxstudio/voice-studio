<script module lang="ts">
	import type { HistoryItem } from '$lib/api/types';
	import { historyAudioIdentityLabel } from './audio-identity-label';
	import { historyBelongsToSegment } from './tts-history-controller';

	const PARAMETER_LABELS: Record<string, string> = {
		engine_id: '模型引擎 / engine_id',
		voice_id: '音色 / voice_id',
		voice_source: '音色来源 / voice_source',
		language: '语言 / language',
		speed: '语速 / speed',
		pitch_rate: '音高 / pitch_rate',
		loudness_rate: '响度 / loudness_rate',
		sample_rate: '采样率 / sample_rate',
		bit_rate: '码率 / bit_rate',
		reference_audio_path: '参考音频 / reference_audio_path',
		ref_text: '参考文本 / ref_text',
		custom_reference_source_audio_path: '参考音频源 / custom_reference_source_audio_path',
		custom_reference_source_duration_ms: '参考音频时长 / custom_reference_source_duration_ms',
		custom_reference_trim_start_ms: '参考音频入点 / custom_reference_trim_start_ms',
		custom_reference_trim_end_ms: '参考音频出点 / custom_reference_trim_end_ms',
		emotion_mode: '情感模式 / emotion_mode',
		emotion: '情感 / emotion',
		emotion_values: '情感向量 / emotion_values',
		emotion_text: '情感文本 / emotion_text',
		emotion_reference_voice_id: '情感参考音色 / emotion_reference_voice_id',
		emotion_reference_audio_path: '情感参考音频 / emotion_reference_audio_path',
		style_instruction: '风格指令 / style_instruction',
		voice_design_prompt: '音色设计提示 / voice_design_prompt',
		temperature: '随机度 / temperature',
		top_p: '采样概率 / top_p',
		top_k: '候选数量 / top_k',
		repetition_penalty: '重复惩罚 / repetition_penalty',
		seed: '随机种子 / seed',
		remove_silence: '移除静音 / remove_silence',
		fix_duration: '固定时长 / fix_duration',
		duration: '目标时长 / duration',
		output_format: '输出格式 / output_format',
		input_mode: '输入模式 / input_mode',
		input_assets: '输入素材 / input_assets',
		engine_parameters: '引擎参数 / engine_parameters'
	};

	export function formatHistoryDate(value: string) {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat('zh-CN', {
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit'
		}).format(date);
	}

	export function historyDurationLabel(value: number | null) {
		if (!value) return '时长 --';
		return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} ms`;
	}

	export function historyParameterLabel(key: string) {
		return PARAMETER_LABELS[key] ?? `参数 / ${key}`;
	}

	export function historyParameterValue(value: unknown) {
		if (typeof value === 'string') return value;
		if (value === undefined) return 'undefined';
		if (value === null) return 'null';
		if (typeof value === 'object') return JSON.stringify(value, null, 2);
		return String(value);
	}

	export function historyParameterRows(snapshot: Record<string, unknown> | null | undefined) {
		return Object.entries(snapshot ?? {}).map(([key, value]) => ({
			key,
			label: historyParameterLabel(key),
			value: historyParameterValue(value)
		}));
	}

	export function historyMetadataParts(item: Pick<HistoryItem, 'created_at' | 'duration_ms' | 'engine_id' | 'parameter_snapshot'>) {
		const speed = item.parameter_snapshot?.speed;
		return [
			formatHistoryDate(item.created_at),
			historyDurationLabel(item.duration_ms),
			item.engine_id || '未知引擎',
			`语速 ${typeof speed === 'number' ? `${speed}x` : '--'}`
		];
	}

	export function historySegmentId(item: HistoryItem) {
		return item.segment_id || item.localized_subtitle_id || item.cue_id || '';
	}

	export function historyItemIsTimelineUsed(
		item: HistoryItem,
		appliedResultId = '',
		usedResultIds: string[] = []
	) {
		return item.result_id === appliedResultId || [item.result_id, item.task_id, item.generation_id]
			.filter(Boolean)
			.some((value) => usedResultIds.includes(String(value)));
	}

	export function unusedHistoryCount(
		items: HistoryItem[],
		scope: 'current' | 'all',
		selectedSegmentId = '',
		appliedResultId = '',
		usedResultIds: string[] = []
	) {
		return items.filter((item) =>
			(scope === 'all' || historyBelongsToSegment(item, selectedSegmentId))
			&& !historyItemIsTimelineUsed(item, appliedResultId, usedResultIds)
		).length;
	}

	export function historyActionIsDestructive(key: string) {
		return key === 'cleanup-unused'
			|| key === 'delete-current'
			|| key === 'delete-all'
			|| key.startsWith('delete:');
	}
</script>

<script lang="ts">
	import { ArrowRightLeft, Eraser, ExternalLink, History, Plus, Repeat2, SlidersHorizontal, Trash2 } from 'lucide-svelte';
	import { hoverPopover } from '$lib/components/shared/hover-tooltip';
	import CommandSpinner from './CommandSpinner.svelte';
	import SubtitleAudioWaveform from './SubtitleAudioWaveform.svelte';

	let {
		items = [],
		total = items.length,
		loaded = items.length,
		hasMore = false,
		loadingMore = false,
		onLoadMore = undefined,
		selectedSegmentId = '',
		segmentLabels = {},
		canGenerate = false,
		sourceSelectionAdvisory = null,
		busy = false,
		appliedResultId = '',
		selectedResultId = '',
		usedResultIds = [],
		canApplyToTimeline = false,
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
		scope = $bindable('current')
	}: {
		items?: HistoryItem[];
		total?: number;
		loaded?: number;
		hasMore?: boolean;
		loadingMore?: boolean;
		onLoadMore?: () => void | Promise<unknown>;
		selectedSegmentId?: string;
		segmentLabels?: Record<string, string>;
		canGenerate?: boolean;
		sourceSelectionAdvisory?: string | null;
		busy?: boolean;
		appliedResultId?: string;
		selectedResultId?: string;
		usedResultIds?: string[];
		canApplyToTimeline?: boolean;
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
		scope?: 'current' | 'all';
	} = $props();

	let pendingActionKeys = $state<Set<string>>(new Set());

	async function runAction(key: string, action: () => void | Promise<void>) {
		if (pendingActionKeys.has(key)) return;
		pendingActionKeys = new Set([...pendingActionKeys, key]);
		try {
			await action();
		} finally {
			const next = new Set(pendingActionKeys);
			next.delete(key);
			pendingActionKeys = next;
		}
	}
	const destructiveActionPending = $derived(
		[...pendingActionKeys].some(historyActionIsDestructive)
	);
	const actionPending = (key: string) => pendingActionKeys.has(key);
	const reuseActionKey = (item: HistoryItem) => `reuse:${selectedSegmentId}:${item.result_id}`;
	const currentItems = $derived(items.filter((item) => historyBelongsToSegment(item, selectedSegmentId)));
	const visibleItems = $derived(scope === 'current' ? currentItems.slice(0, 8) : items);
	const unusedProjectCount = $derived(unusedHistoryCount(items, 'all', selectedSegmentId, appliedResultId, usedResultIds));
	const unusedCurrentCount = $derived(unusedHistoryCount(items, 'current', selectedSegmentId, appliedResultId, usedResultIds));
	const unusedVisibleScopeCount = $derived(scope === 'current' ? unusedCurrentCount : unusedProjectCount);

	function audioUrl(item: HistoryItem) {
		return `/api/history/${encodeURIComponent(item.result_id)}/audio`;
	}

	function waveformUrl(item: HistoryItem) {
		return `/api/history/${encodeURIComponent(item.result_id)}/waveform`;
	}

	function beginHistoryDrag(event: PointerEvent, item: HistoryItem) {
		if (!item.output_path || busy || applyingResultId) {
			event.preventDefault();
			return;
		}
		onHistoryDragStart?.(item);
	}

	function isTimelineUsed(item: HistoryItem) {
		return historyItemIsTimelineUsed(item, appliedResultId, usedResultIds);
	}
</script>

<section class="tts-history" aria-label="字幕配音记录">
	<div class="history-head">
		<div class="history-heading">
			<strong><History size={13} />配音记录</strong>
			<span>{selectionCount > 1 ? `已选 ${selectionCount} 条${selectionContiguous ? '连续' : ''}字幕` : currentItems.length ? `当前片段 ${currentItems.length} 次生成` : '当前片段尚未生成'}</span>
		</div>
		<div class="history-head-actions">
			{#if onCleanupUnused}
				<button class="cleanup-unused" type="button" aria-label={scope === 'current' ? '清理当前片段未使用的配音素材' : '清理全部片段未使用的配音素材'} aria-busy={actionPending('cleanup-unused')} disabled={destructiveActionPending || (scope === 'current' ? !currentItems.length : !total)} data-tooltip={scope === 'current' ? '清理当前片段：只删除当前字幕没有放到时间线的配音记录、原始生成文件和波形缓存。' : '清理全部片段：删除整个项目中没有放到时间线的配音记录、原始生成文件和波形缓存。'} onclick={() => runAction('cleanup-unused', () => onCleanupUnused(scope))}>{#if actionPending('cleanup-unused')}<CommandSpinner size={13} />{:else}<Eraser size={13} />{/if}清理未用{scope === 'current' && unusedVisibleScopeCount ? ` ${unusedVisibleScopeCount}` : ''}</button>
			{/if}
			{#if currentItems.length && onDeleteCurrent}
				<button class="delete-current" type="button" aria-label="删除当前字幕的全部配音记录" aria-busy={actionPending('delete-current')} disabled={destructiveActionPending} data-tooltip="删除当前字幕的全部配音记录：不会删除已经复制到时间线的音频片段。" onclick={() => runAction('delete-current', onDeleteCurrent)}>{#if actionPending('delete-current')}<CommandSpinner size={13} />{:else}<Trash2 size={13} />{/if}清空当前</button>
			{/if}
			{#if total && onDeleteAll}
				<button class="delete-current" type="button" aria-label="删除当前项目的全部配音记录" aria-busy={actionPending('delete-all')} disabled={destructiveActionPending} data-tooltip="清空全部：删除当前项目所有字幕的配音历史，不影响已经复制到时间线的音频片段。" onclick={() => runAction('delete-all', onDeleteAll)}>{#if actionPending('delete-all')}<CommandSpinner size={13} />{:else}<Trash2 size={13} />{/if}清空全部</button>
			{/if}
			<button class="open-generate" type="button" aria-label={currentItems.length ? '调整当前片段的配音参数' : '送到语音合成'} aria-busy={busy} disabled={!canGenerate || busy} data-tooltip={sourceSelectionAdvisory ?? (currentItems.length ? '调整配音参数：打开语音合成并带入当前片段的台词、参考音色和时间范围。' : '送到语音合成：带入当前片段的台词、参考音色和时间范围。')} onclick={onOpenGenerate}>
				{#if busy}<CommandSpinner size={14} />生成中{:else}<ExternalLink size={14} />{selectionCount > 1 && selectionContiguous ? '合并配音' : currentItems.length ? '调整配音' : '送到合成'}{/if}
			</button>
		</div>
	</div>

	<div class="history-scope" role="tablist" aria-label="配音记录范围">
		<button class:active={scope === 'current'} type="button" role="tab" aria-selected={scope === 'current'} onclick={() => (scope = 'current')}>当前片段 <span>{currentItems.length}</span></button>
		<button class:active={scope === 'all'} type="button" role="tab" aria-selected={scope === 'all'} onclick={() => (scope = 'all')}>全部片段 <span>{total}</span></button>
	</div>

	{#if visibleItems.length}
		<div class="history-list">
			{#each visibleItems as item (item.result_id)}
				{@const detailRows = historyParameterRows(item.parameter_snapshot)}
				{@const timelineUsed = isTimelineUsed(item)}
				{@const recordSelected = item.result_id === selectedResultId}
				<article class="history-row" class:current={historyBelongsToSegment(item, selectedSegmentId)} class:timeline-used={timelineUsed} class:record-selected={recordSelected} data-result-id={item.result_id}>
					<div class="history-row-head">
						<div class="record-overview">
							<div class="record-context">
								{#if recordSelected}<span class="status-badge selected-badge">当前选中</span>{/if}
								{#if timelineUsed}<span class="status-badge used-badge">时间线在用</span>{/if}
								<span class="segment-label" data-tooltip={segmentLabels[historySegmentId(item)] || historyAudioIdentityLabel(item)}>{historyAudioIdentityLabel(item)}</span>
							</div>
							<span class="record-meta" aria-label="日期时间、持续时长、模型引擎和语速">{historyMetadataParts(item).join(' · ')}</span>
						</div>
						<div class="row-actions">
							<button class="parameter-trigger" type="button" aria-label="查看生成参数" use:hoverPopover={{ title: '生成参数', rows: detailRows, emptyText: '没有保存生成参数' }}><SlidersHorizontal size={13} /></button>
							<button class="reuse-button" type="button" aria-busy={actionPending(reuseActionKey(item))} disabled={!canGenerate || actionPending(reuseActionKey(item))} aria-label="沿用这次参数生成" data-tooltip={sourceSelectionAdvisory ? `${sourceSelectionAdvisory}；点击后会按正式生成流程校验。` : '沿用参数：使用这次记录的模型和参数，为当前选中的字幕重新生成配音；已有任务生成时，新任务会继续排队。'} onclick={() => runAction(reuseActionKey(item), () => onReuse(item))}>{#if actionPending(reuseActionKey(item))}<CommandSpinner size={13} />{:else}<Repeat2 size={13} />{/if}</button>
							{#if scope === 'current' && onApply && item.output_path}
								<button class="apply-button" class:applied={item.result_id === appliedResultId} type="button" aria-busy={applyingResultId === item.result_id} disabled={!canApplyToTimeline || item.result_id === appliedResultId || Boolean(applyingResultId)} aria-label={item.result_id === appliedResultId ? '当前已采用这条历史声音' : timelineClipPresent ? '替换当前配音片段' : '添加到合成配音轨'} data-tooltip={item.result_id === appliedResultId ? '当前版本：这条声音已在合成配音轨中使用。' : canApplyToTimeline ? timelineClipPresent ? '替换配音：采用这条同字幕的历史声音，并保持字幕入点对齐。' : '添加配音：把这条历史声音按字幕入点添加到合成配音轨；如与现有片段重叠，会自动显示在新的配音分轨。' : '请先选择一条字幕。'} onclick={() => onApply(item)}>{#if applyingResultId === item.result_id}<CommandSpinner size={13} />{:else if timelineClipPresent}<ArrowRightLeft size={13} />{:else}<Plus size={13} />{/if}</button>
							{/if}
							{#if onDelete}
								<button class="delete-record" type="button" aria-label="删除这条配音记录" aria-busy={actionPending(`delete:${item.result_id}`)} disabled={destructiveActionPending} data-tooltip="删除这条配音记录：不会删除已经复制到时间线的音频片段。" onclick={() => runAction(`delete:${item.result_id}`, () => onDelete(item))}>{#if actionPending(`delete:${item.result_id}`)}<CommandSpinner size={13} />{:else}<Trash2 size={13} />{/if}</button>
							{/if}
						</div>
					</div>
					{#if scope === 'all'}<p class="script-line" data-tooltip={item.input_text || '无台词'}>{item.input_text || '无台词'}</p>{/if}
					{#if item.output_path}
						<SubtitleAudioWaveform
							label="配音"
							audioUrl={audioUrl(item)}
							waveformUrl={waveformUrl(item)}
							downloadUrl={audioUrl(item)}
							draggable={!busy && !applyingResultId}
							onDragStart={(event) => beginHistoryDrag(event, item)}
						/>
					{/if}
				</article>
			{/each}
			{#if scope === 'all' && (hasMore || loadingMore)}
				<button class="history-load-more" type="button" disabled={loadingMore} onclick={() => onLoadMore?.()}>
					{#if loadingMore}<CommandSpinner size={13} />正在加载{:else}加载更多 · 已加载 {loaded} / {total}{/if}
				</button>
			{/if}
		</div>
	{:else}
		<p class="history-empty">{scope === 'current' ? '当前字幕还没有配音记录。先送到语音合成页调好第一版，之后就能在这里直接复用。' : '当前项目还没有语音合成记录。'}</p>
	{/if}
</section>

<style>
	.tts-history {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		grid-template-rows: auto auto minmax(0, 1fr);
		gap: 9px;
		min-width: 0;
		min-height: 0;
		margin: 14px 0 0;
		padding: 12px 0 0;
		border-top: 1px solid rgba(105, 216, 208, 0.2);
	}

	.history-head,
	.history-row-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}

	.history-head {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		align-items: stretch;
		gap: 7px;
	}

	.history-head-actions {
		display: flex;
		align-items: center;
		justify-content: flex-start;
		flex-wrap: wrap;
		gap: 5px;
		width: 100%;
	}

	.history-heading {
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
	}

	.history-heading strong {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		color: #e0e9ec;
		font-size: 11px;
		flex: 0 0 auto;
		white-space: nowrap;
	}

	.history-heading span {
		min-width: 0;
		overflow: hidden;
		color: #7f8c94;
		font-size: 9px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.open-generate,
	.reuse-button,
	.apply-button,
	.parameter-trigger {
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
		align-items: center;
		justify-content: center;
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
	.cleanup-unused,
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
	.cleanup-unused {
		padding: 0 6px;
		border-color: rgba(104, 154, 178, 0.38);
		color: #9fc2d2;
	}
	.delete-record { width: 25px; }

	.delete-current:hover,
	.delete-record:hover {
		border-color: rgba(255, 125, 112, 0.78);
		color: #ffd0cb;
	}

	.cleanup-unused:hover:not(:disabled) {
		border-color: rgba(104, 190, 214, 0.72);
		color: #d0eff8;
	}

	.cleanup-unused:disabled {
		cursor: not-allowed;
		opacity: 0.42;
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
		/* In stacked layouts the page owns vertical scrolling; let wheel input
		   over history rows reach that ancestor when this list has no overflow. */
		overscroll-behavior-y: auto;
	}

	.history-load-more {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		min-height: 32px;
		margin: 8px 6px 10px;
		border: 1px solid #364149;
		border-radius: 5px;
		background: #171d22;
		color: #aebbc0;
		font-size: 10px;
		cursor: pointer;
	}

	.history-load-more:disabled {
		cursor: wait;
		opacity: 0.65;
	}

	.history-row {
		display: grid;
		gap: 6px;
		padding: 9px 7px;
		border-bottom: 1px solid rgba(255, 255, 255, 0.07);
		border-radius: 4px;
		transition: background 140ms ease, box-shadow 140ms ease;
	}

	.history-row.timeline-used {
		background: rgba(43, 116, 109, 0.13);
	}

	.history-row.record-selected {
		box-shadow: inset 2px 0 0 rgba(166, 148, 241, 0.86);
		background: rgba(111, 91, 181, 0.12);
	}

	.history-row.timeline-used.record-selected {
		background: rgba(65, 105, 117, 0.18);
	}

	.history-row-head {
		align-items: flex-start;
	}

	.record-overview {
		display: grid;
		flex: 1 1 auto;
		gap: 4px;
		min-width: 0;
		padding-top: 1px;
	}

	.record-context {
		display: flex;
		align-items: center;
		gap: 4px;
		min-width: 0;
	}

	.status-badge {
		display: inline-flex;
		align-items: center;
		min-height: 16px;
		box-sizing: border-box;
		border: 1px solid transparent;
		border-radius: 3px;
		padding: 0 4px;
		font-size: 8px;
		font-weight: 600;
		white-space: nowrap;
	}

	.selected-badge {
		border-color: rgba(166, 148, 241, 0.42);
		background: rgba(116, 93, 190, 0.2);
		color: #d8d0ff;
	}

	.used-badge {
		border-color: rgba(87, 208, 200, 0.42);
		background: rgba(36, 117, 109, 0.22);
		color: #a7eee8;
	}

	.segment-label {
		min-width: 0;
		overflow: hidden;
		color: #8f9ca2;
		font-size: 9px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.record-meta {
		display: block;
		min-width: 0;
		overflow: hidden;
		color: #8b989e;
		font-size: 9px;
		font-variant-numeric: tabular-nums;
		line-height: 1.35;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.row-actions {
		display: inline-flex;
		align-items: center;
		flex: 0 0 auto;
		gap: 4px;
	}

	.reuse-button,
	.parameter-trigger {
		width: 25px;
		height: 25px;
		box-sizing: border-box;
		padding: 0;
		border-color: #364149;
		background: #171d22;
		color: #bfcbd0;
		line-height: 0;
	}

	.apply-button,
	.delete-record {
		height: 25px;
		padding: 0;
		line-height: 0;
	}

	.row-actions button,
	.row-actions .parameter-trigger {
		display: inline-grid;
		place-items: center;
		width: 25px;
		height: 25px;
		min-height: 25px;
		box-sizing: border-box;
		padding: 0;
		line-height: 0;
	}

	.row-actions button:active:not(:disabled),
	.parameter-trigger:active {
		transform: translateY(1px) scale(0.96);
	}

	.reuse-button:hover:not(:disabled),
	.parameter-trigger:hover,
	.parameter-trigger:focus-visible {
		border-color: rgba(87, 208, 200, 0.6);
		color: #8bf1e7;
		outline: none;
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

	.history-empty {
		margin: 0;
		padding: 8px 1px;
		color: #77858c;
		font-size: 10px;
		line-height: 1.5;
	}
</style>
