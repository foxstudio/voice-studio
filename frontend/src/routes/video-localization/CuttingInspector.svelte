<script lang="ts">
	import type {
		HistoryItem,
		ProjectMediaHealth,
		VideoLocalizationCue,
		VideoLocalizationDraft,
		VideoLocalizationDubSubtitleCue,
		VideoLocalizationSubtitleCue,
		VideoLocalizationTimelineClip
	} from '$lib/api/types';
	import { AudioLines, Captions, CheckCircle2, ExternalLink, ListTodo } from 'lucide-svelte';
	import { browser } from '$app/environment';
	import ScrubbableTimeField from './ScrubbableTimeField.svelte';
	import { workflowSegmentIdForSubtitleSelection } from './tts-workflow-selection';
	import { resolveHistoryTimelineClip, timelineDubSegmentId } from './timeline-history-command';
	import CommandSpinner from './CommandSpinner.svelte';
	import type { ActivityTask } from './activity-notice';
	import { formatTimecode } from './subtitle-workbench';
	import { mediaAssetAvailable } from './media-health';
	import {
		MIN_SUBTITLE_DURATION_MS,
		subtitleCueDragBounds
	} from './studio-state';
	import type { DubSubtitleReviewPatch } from './dub-subtitle-review';
	import {
		DUB_SUBTITLE_SOURCE_CHANGED_FLAG,
		dubSubtitleTimingBounds
	} from './dub-subtitle-state';
	import {
		SUBTITLE_STYLE_LABELS,
		type SubtitleDisplayModel,
		type SubtitleDisplaySettings,
		type SubtitleDisplayStyle,
		type SubtitleStylePreset
	} from './subtitle-display';

	type DubbingInspectorPanelComponent = (
		typeof import('./DubbingInspectorPanel.svelte')
	)['default'];
	type TaskProgressPanelComponent = (
		typeof import('./TaskProgressPanel.svelte')
	)['default'];

	const subtitleStylePresets = Object.entries(SUBTITLE_STYLE_LABELS).map(([id, label]) => ({
		id: id as SubtitleStylePreset,
		label
	}));

	function signedPixelLabel(value: number) {
		return `${value > 0 ? '+' : ''}${value}px`;
	}

	function timingConfirmationKind(cue: VideoLocalizationCue): 'automatic' | 'auditioned' | null {
		const current = cue.manual_timing_review_status === 'confirmed'
			&& cue.manual_timing_confirmed_revision === (cue.manual_timing_revision ?? 0)
			&& cue.manual_timing_confirmed_start_ms === cue.start_ms
			&& cue.manual_timing_confirmed_end_ms === cue.end_ms;
		if (!current) return null;
		const evidence = cue.manual_timing_confirmation_evidence;
		const confirmationIsCurrent = timingConfirmationCurrentByCueId.get(cue.cue_id) ?? false;
		if (cue.manual_timing_confirmation_method === 'asr_vad_verified'
			&& evidence?.transcription_revision_id === cue.transcription_revision_id
			&& JSON.stringify(evidence?.source_word_ids) === JSON.stringify(cue.source_word_ids)
			&& confirmationIsCurrent) return 'automatic';
		if (cue.manual_timing_confirmation_method === 'auditioned'
			&& !cue.manual_timing_confirmation_evidence
			&& confirmationIsCurrent) return 'auditioned';
		return null;
	}

	let {
		draft,
		mediaHealth,
		projectId,
		timingConfirmationCurrentByCueId = new Map(),
		selectedCue,
		selectedLocalizedSubtitle = null,
		ttsPrimaryLocalizedSubtitle = null,
		selectedLocalizedSubtitles = [],
		selectedLocalizedSubtitlesContiguous = false,
		ttsSourceSelectionAdvisoryMessage = null,
		selectedTimelineAudioClip = null,
		inspectorSection = 'tasks',
		dubbingHistoryScope = $bindable('current'),
		subtitleDisplay,
		onSectionChange,
		onUpdateCue,
		onPreviewLocalizedSubtitle = undefined,
		onUpdateLocalizedSubtitle = undefined,
		onUpdateDubSubtitle = undefined,
		dubSubtitleReviewBusy = false,
		onConfirmCueTiming,
		onUpdateSubtitleDisplaySettings,
		confirmingCueTiming,
		generatingVoice,
		taskHistory = [],
		operationHistoryTotal = 0,
		operationHistoryLoaded = 0,
		operationHistoryHasMore = false,
		operationHistoryLoading = false,
		onLoadMoreOperationHistory = undefined,
		ttsHistory = [],
		ttsHistoryTotal = 0,
		ttsHistoryLoaded = 0,
		ttsHistoryHasMore = false,
		ttsHistoryLoading = false,
		onLoadMoreTtsHistory = undefined,
		onOpenSubtitleGenerate = undefined,
		onReuseSubtitleHistory = undefined,
		onApplySubtitleHistory = undefined,
		onDeleteSubtitleHistory = undefined,
		onDeleteCurrentSubtitleHistory = undefined,
		onDeleteAllSubtitleHistory = undefined,
		onCleanupUnusedSubtitleHistory = undefined,
		onHistoryDragStart = undefined,
		historyApplyingResultId = '',
		onCancelTask = undefined,
		onDeleteTask = undefined,
		onRetryTask = undefined,
		onLoadTaskDetail = undefined,
		onSeekTimeline = undefined,
		onPlayTimelineRange = undefined,
		subtitleRuntimeBusy = false,
		localizationRuntimeBusy = false,
		taskCenterPulseKey = 0
	}: {
		draft: VideoLocalizationDraft | null;
		mediaHealth: ProjectMediaHealth | null;
		projectId: string;
		timingConfirmationCurrentByCueId?: ReadonlyMap<string, boolean>;
		selectedCue: VideoLocalizationCue | null;
		selectedLocalizedSubtitle?: VideoLocalizationSubtitleCue | null;
		ttsPrimaryLocalizedSubtitle?: VideoLocalizationSubtitleCue | null;
		selectedLocalizedSubtitles?: VideoLocalizationSubtitleCue[];
		selectedLocalizedSubtitlesContiguous?: boolean;
		ttsSourceSelectionAdvisoryMessage?: string | null;
		selectedTimelineAudioClip?: VideoLocalizationTimelineClip | null;
		inspectorSection?: 'tasks' | 'subtitle' | 'dubbing';
		dubbingHistoryScope?: 'current' | 'all';
		subtitleDisplay: SubtitleDisplayModel;
		onSectionChange: (section: 'tasks' | 'subtitle' | 'dubbing') => void;
		onUpdateCue: (patch: Partial<VideoLocalizationCue>) => void;
		onPreviewLocalizedSubtitle?: (patch: Partial<VideoLocalizationSubtitleCue>) => void;
		onUpdateLocalizedSubtitle?: (patch: Partial<VideoLocalizationSubtitleCue>) => void | Promise<void>;
		onUpdateDubSubtitle?: (subtitleId: string, patch: DubSubtitleReviewPatch) => void | Promise<void>;
		dubSubtitleReviewBusy?: boolean;
		onConfirmCueTiming: () => void;
		onUpdateSubtitleDisplaySettings: (settings: SubtitleDisplaySettings) => void;
		confirmingCueTiming: boolean;
		generatingVoice: boolean;
		taskHistory?: ActivityTask[];
		operationHistoryTotal?: number;
		operationHistoryLoaded?: number;
		operationHistoryHasMore?: boolean;
		operationHistoryLoading?: boolean;
		onLoadMoreOperationHistory?: () => void | Promise<unknown>;
		ttsHistory?: HistoryItem[];
		ttsHistoryTotal?: number;
		ttsHistoryLoaded?: number;
		ttsHistoryHasMore?: boolean;
		ttsHistoryLoading?: boolean;
		onLoadMoreTtsHistory?: () => void | Promise<unknown>;
		onOpenSubtitleGenerate?: () => void | Promise<void>;
		onReuseSubtitleHistory?: (item: HistoryItem) => void | Promise<void>;
		onApplySubtitleHistory?: (item: HistoryItem) => void | Promise<void>;
		onDeleteSubtitleHistory?: (item: HistoryItem) => void | Promise<void>;
		onDeleteCurrentSubtitleHistory?: () => void | Promise<void>;
		onDeleteAllSubtitleHistory?: () => void | Promise<void>;
		onCleanupUnusedSubtitleHistory?: (scope: 'current' | 'all') => void | Promise<void>;
		onHistoryDragStart?: (item: HistoryItem) => void;
		historyApplyingResultId?: string;
		onCancelTask?: (task: ActivityTask) => void | Promise<void>;
		onDeleteTask?: (task: ActivityTask) => void | Promise<void>;
		onRetryTask?: (task: ActivityTask) => void | Promise<void>;
		onLoadTaskDetail?: (projectId: string, operationId: string) => Promise<ActivityTask | null>;
		onSeekTimeline?: (timeMs: number) => void;
		onPlayTimelineRange?: (range: { startMs: number; endMs: number }) => void;
		subtitleRuntimeBusy?: boolean;
		localizationRuntimeBusy?: boolean;
		taskCenterPulseKey?: number;
	} = $props();

	const selectedSubtitleSource = $derived(subtitleDisplay.activeSource);
	const selectedSubtitleStyle = $derived(subtitleDisplay.settings.trackStyles[selectedSubtitleSource]);

	function updateSelectedSubtitleStyle(patch: Partial<SubtitleDisplayStyle>) {
		onUpdateSubtitleDisplaySettings(subtitleDisplay.updateStyle(selectedSubtitleSource, patch));
	}

	let activeSection = $state<'tasks' | 'subtitle' | 'dubbing'>('tasks');
	let dubSubtitleEditorKey = $state('');
	let dubSubtitleEditorText = $state('');
	let dubSubtitleEditorSourceText = $state('');
	let retainedDubbingTarget = $state<{ id: string; script: string; label: string; canGenerate: boolean } | null>(null);
	let dubbingInspectorPanelPromise: Promise<DubbingInspectorPanelComponent> | null = null;
	let dubbingInspectorLoadRevision = $state(0);
	let taskProgressPanelPromise: Promise<TaskProgressPanelComponent> | null = null;
	let taskProgressPanelLoadRevision = $state(0);

	$effect(() => {
		const selected = subtitleDisplay.selectedCue;
		const nextKey = selected?.reviewable ? selected.key : '';
		if (nextKey !== dubSubtitleEditorKey) {
			dubSubtitleEditorKey = nextKey;
			dubSubtitleEditorText = nextKey ? selected?.text ?? '' : '';
			dubSubtitleEditorSourceText = dubSubtitleEditorText;
		} else if (nextKey && selected && selected.text !== dubSubtitleEditorSourceText) {
			const editorWasDirty = dubSubtitleEditorText.trim() !== dubSubtitleEditorSourceText;
			dubSubtitleEditorSourceText = selected.text;
			if (!editorWasDirty || dubSubtitleEditorText.trim() === selected.text) {
				dubSubtitleEditorText = selected.text;
			}
		}
	});

	async function saveDubSubtitleText() {
		const selected = subtitleDisplay.selectedCue;
		if (!selected?.reviewable || !onUpdateDubSubtitle || dubSubtitleReviewBusy) return;
		await onUpdateDubSubtitle(selected.id, { text: dubSubtitleEditorText });
	}

	function loadTaskProgressPanel() {
		taskProgressPanelLoadRevision;
		taskProgressPanelPromise ??= import('./TaskProgressPanel.svelte')
			.then((module) => module.default)
			.catch((error) => {
				taskProgressPanelPromise = null;
				throw error;
			});
		return taskProgressPanelPromise;
	}

	function retryTaskProgressPanel() {
		taskProgressPanelPromise = null;
		taskProgressPanelLoadRevision += 1;
	}


	function loadDubbingInspectorPanel() {
		dubbingInspectorLoadRevision;
		dubbingInspectorPanelPromise ??= import('./DubbingInspectorPanel.svelte')
			.then((module) => module.default)
			.catch((error) => {
				dubbingInspectorPanelPromise = null;
				throw error;
			});
		return dubbingInspectorPanelPromise;
	}

	function retryDubbingInspectorPanel() {
		dubbingInspectorPanelPromise = null;
		dubbingInspectorLoadRevision += 1;
	}

	const dubbingLocalizedSubtitle = $derived(ttsPrimaryLocalizedSubtitle ?? selectedLocalizedSubtitle);
	const selectedWorkflowSegmentId = $derived(workflowSegmentIdForSubtitleSelection(
		draft?.tts_tasks ?? [],
		selectedLocalizedSubtitles.map((subtitle) => subtitle.subtitle_id)
	));
	const activeTtsSegmentId = $derived(
		(timelineDubSegmentId(selectedTimelineAudioClip) || null)
			?? (selectedWorkflowSegmentId || null)
			?? dubbingLocalizedSubtitle?.subtitle_id
			?? selectedCue?.cue_id
			?? ''
	);
	const activeTimelineDubClip = $derived(resolveHistoryTimelineClip(
		draft?.timeline_clips ?? [], selectedTimelineAudioClip, activeTtsSegmentId, !dubbingLocalizedSubtitle
	));
	const usedTtsIdentityIds = $derived.by(() => {
		const keys = ['result_id', 'task_id', 'generation_id', 'candidate_id', 'audio_id'] as const;
		const ids = new Set<string>();
		for (const clip of draft?.timeline_clips ?? []) {
			if (clip.track_id !== 'dub') continue;
			for (const key of keys) {
				const value = clip[key];
				if (value) ids.add(String(value));
			}
		}
		let changed = true;
		while (changed) {
			changed = false;
			for (const candidate of draft?.generated_candidates ?? []) {
				const candidateIds = keys.map((key) => candidate[key]).filter(Boolean).map(String);
				if (!candidateIds.some((value) => ids.has(value))) continue;
				for (const value of candidateIds) {
					if (!ids.has(value)) {
						ids.add(value);
						changed = true;
					}
				}
			}
		}
		return Array.from(ids);
	});
	const selectedTtsResultId = $derived(
		selectedTimelineAudioClip?.track_id === 'dub' ? String(selectedTimelineAudioClip.result_id ?? '') : ''
	);
	const appliedTtsResultId = $derived(String(
		activeTimelineDubClip?.result_id ?? ''
	));
	const canGenerateSubtitle = $derived(
		Boolean(
			mediaAssetAvailable(mediaHealth, 'vocals') &&
				(dubbingLocalizedSubtitle?.tts_text?.trim() || dubbingLocalizedSubtitle?.text?.trim() || selectedCue?.tts_recommended_text?.trim()) &&
				(dubbingLocalizedSubtitle ? dubbingLocalizedSubtitle.end_ms > dubbingLocalizedSubtitle.start_ms : selectedCue?.start_ms !== null && selectedCue?.end_ms !== null)
		)
	);

	function commitFocusedSubtitleEditor() {
		const activeElement = document.activeElement;
		if (activeElement instanceof HTMLTextAreaElement && activeElement.classList.contains('subtitle-textarea')) {
			activeElement.blur();
		}
	}
	const activeDubbingScript = $derived(
		selectedLocalizedSubtitles.length > 1
			? selectedLocalizedSubtitles.map((subtitle) => subtitle.tts_text?.trim() || subtitle.text.trim()).filter(Boolean).join(' ')
			: dubbingLocalizedSubtitle?.tts_text?.trim() || dubbingLocalizedSubtitle?.text?.trim() || selectedCue?.tts_recommended_text?.trim() || ''
	);
	const activeDubbingTargetLabel = $derived(
		selectedLocalizedSubtitles.length > 1
			? `${selectedLocalizedSubtitles.length} 条${selectedLocalizedSubtitlesContiguous ? '连续' : ''}字幕`
			: dubbingLocalizedSubtitle?.subtitle_id || selectedTimelineAudioClip?.subtitle_id || selectedCue?.cue_id || '未选择配音目标'
	);
	const resolvedDubbingTarget = $derived(activeTtsSegmentId
		? { id: activeTtsSegmentId, script: activeDubbingScript, label: activeDubbingTargetLabel, canGenerate: canGenerateSubtitle }
		: retainedDubbingTarget
	);
	const segmentLabels = $derived.by(() => {
		const labels: Record<string, string> = {};
		for (const subtitle of draft?.localized_subtitles ?? []) labels[subtitle.subtitle_id] = `${subtitle.subtitle_id.replace('localized_', '#')} · ${subtitle.text}`;
		for (const cue of draft?.cues ?? []) labels[cue.cue_id] = `${cue.cue_id.replace('cue_', '#')} · ${cue.tts_recommended_text || cue.zh_localized_subtitle_text || cue.en_subtitle_text || '无台词'}`;
		return labels;
	});
	const selectedReviewSegments = $derived(reviewSegmentsForCue(draft, selectedCue));

	function reviewSegmentsForCue(currentDraft: VideoLocalizationDraft | null, cue: VideoLocalizationCue | null) {
		if (!currentDraft?.transcription || !cue) return [];
		const wordIds = new Set(cue.source_word_ids ?? []);
		const segmentIds = new Set(
			currentDraft.transcription.words
				.filter((word) => wordIds.has(word.word_id))
				.map((word) => word.segment_id)
		);
		if (!segmentIds.size && cue.start_ms !== null && cue.end_ms !== null) {
			for (const segment of currentDraft.transcription.segments) {
				if (segment.end_ms > cue.start_ms && segment.start_ms < cue.end_ms) segmentIds.add(segment.segment_id);
			}
		}
		return currentDraft.transcription.segments.filter((segment) => segmentIds.has(segment.segment_id));
	}

	function reviewReasonLabel(reason: string | null | undefined) {
		const labels: Record<string, string> = {
			'llm_review_rejected:numbers_changed': '候选修改了数字或数值',
				'llm_review_rejected:negation_changed': '候选修改了否定关系',
				'llm_review_rejected:language_changed': '候选改变了原文语言',
				'llm_review_rejected:too_different': '候选改写幅度过大',
				'llm_review_rejected:empty_text': '候选为空，已保留原始识别文本'
		};
		return reason ? labels[reason] ?? reason : '';
	}

	function subtitleStatusLabel(status: VideoLocalizationCue['review_status']) {
		return {
			ready: '字幕已校对',
			needs_review: '待校对',
			blocked: '阻断',
			locked: '已锁定'
		}[status];
	}
	function timingBounds(track: 'asr' | 'localized') {
		const selected = track === 'localized' ? selectedLocalizedSubtitle : selectedCue;
		if (!selected) return { minStartMs: 0, maxEndMs: Math.max(0, draft?.source_media.duration_ms ?? 0) };
		const itemId = track === 'localized' ? selectedLocalizedSubtitle?.subtitle_id ?? '' : selectedCue?.cue_id ?? '';
		const items = track === 'localized'
			? (draft?.localized_subtitles ?? []).map((item) => ({ cue_id: item.subtitle_id, start_ms: item.start_ms, end_ms: item.end_ms }))
			: (draft?.cues ?? []);
		return subtitleCueDragBounds(items, itemId, Math.max(draft?.source_media.duration_ms ?? 0, selected.end_ms ?? 0));
	}

	$effect(() => {
		inspectorSection;
		activeSection = inspectorSection;
	});

	$effect(() => {
		if (!activeTtsSegmentId) return;
		retainedDubbingTarget = {
			id: activeTtsSegmentId,
			script: activeDubbingScript,
			label: activeDubbingTargetLabel,
			canGenerate: canGenerateSubtitle
		};
	});
</script>

<aside class="inspector" class:tasks-view={activeSection === 'tasks'} class:subtitle-view={activeSection === 'subtitle'} class:dubbing-view={activeSection === 'dubbing'}>
	<div class="inspector-mode-tabs" aria-label="右侧检查器">
		<button class:active={activeSection === 'tasks'} type="button" data-tooltip="任务：查看后台处理进度、每一步状态和历史结果。" onclick={() => onSectionChange('tasks')}><ListTodo size={14} /><span>任务</span></button>
		<button class:active={activeSection === 'subtitle'} type="button" data-tooltip="字幕：编辑文本、时间码、校对状态和上屏样式。" onclick={() => onSectionChange('subtitle')}><Captions size={14} /><span>字幕</span></button>
		<button class:active={activeSection === 'dubbing'} type="button" data-tooltip="配音：管理音色与参数，生成、试听并应用配音结果。" onclick={() => onSectionChange('dubbing')}><AudioLines size={14} /><span>配音</span></button>
	</div>

	{#if activeSection === 'tasks'}
		<div class="task-view-content">
			{#await loadTaskProgressPanel()}
				<section class="inspector-panel empty-dubbing-results" role="status">
					<p class="empty-text"><CommandSpinner size={12} />正在加载任务列表</p>
				</section>
			{:then TaskProgressPanel}
				<TaskProgressPanel
					{projectId}
					tasks={taskHistory}
					{operationHistoryTotal}
					{operationHistoryLoaded}
					{operationHistoryHasMore}
					{operationHistoryLoading}
					onLoadMoreHistory={onLoadMoreOperationHistory}
					{onCancelTask}
					{onDeleteTask}
					{onRetryTask}
					{onLoadTaskDetail}
					{onSeekTimeline}
					{onPlayTimelineRange}
					pulseKey={taskCenterPulseKey}
					full
				/>
			{:catch}
				<section class="inspector-panel empty-dubbing-results" role="alert">
					<p class="empty-text">任务列表加载失败。</p>
					<button type="button" onclick={retryTaskProgressPanel}>重新加载</button>
				</section>
			{/await}
		</div>
	{/if}

	{#if activeSection === 'dubbing' && draft}
		{#await import('./DubbingRecoveryPanel.svelte') then panel}
			<panel.default {draft} {projectId} />
		{/await}
	{/if}

	{#if activeSection === 'dubbing' && resolvedDubbingTarget && onOpenSubtitleGenerate && onReuseSubtitleHistory}
		{#await loadDubbingInspectorPanel()}
			<section class="inspector-panel empty-dubbing-results" role="status">
				<p class="empty-text"><CommandSpinner size={12} />正在加载配音结果</p>
			</section>
		{:then DubbingInspectorPanel}
			<DubbingInspectorPanel
				bind:historyScope={dubbingHistoryScope}
				items={ttsHistory}
				total={ttsHistoryTotal}
				loaded={ttsHistoryLoaded}
				hasMore={ttsHistoryHasMore}
				loadingMore={ttsHistoryLoading}
				onLoadMore={onLoadMoreTtsHistory}
				selectedSegmentId={resolvedDubbingTarget.id}
				{segmentLabels}
				script={resolvedDubbingTarget.script}
				targetLabel={resolvedDubbingTarget.label}
				canGenerate={resolvedDubbingTarget.canGenerate}
				sourceSelectionAdvisory={ttsSourceSelectionAdvisoryMessage}
				busy={generatingVoice}
				appliedResultId={appliedTtsResultId}
				selectedResultId={selectedTtsResultId}
				usedResultIds={usedTtsIdentityIds}
				timelineClipPresent={Boolean(activeTimelineDubClip)}
				applyingResultId={historyApplyingResultId}
				onOpenGenerate={onOpenSubtitleGenerate}
				onReuse={onReuseSubtitleHistory}
				onApply={onApplySubtitleHistory}
				onDelete={onDeleteSubtitleHistory}
				onDeleteCurrent={onDeleteCurrentSubtitleHistory}
				onDeleteAll={onDeleteAllSubtitleHistory}
				onCleanupUnused={onCleanupUnusedSubtitleHistory}
				onHistoryDragStart={onHistoryDragStart}
				selectionCount={selectedLocalizedSubtitles.length || 1}
				selectionContiguous={selectedLocalizedSubtitlesContiguous}
			/>
		{:catch}
			<section class="inspector-panel empty-dubbing-results" role="alert">
				<p class="empty-text">配音结果面板加载失败。</p>
				<button type="button" onclick={retryDubbingInspectorPanel}>重新加载</button>
			</section>
		{/await}
	{:else if activeSection === 'dubbing'}
		<section class="inspector-panel empty-dubbing-results">
			<p class="empty-text">选择一条字幕或合成配音片段后，这里会显示对应的生成记录和时间线版本。</p>
		</section>
	{/if}

	{#if activeSection === 'subtitle'}
		<section class="inspector-panel subtitle-panel" class:runtime-locked={selectedLocalizedSubtitle ? localizationRuntimeBusy : subtitleRuntimeBusy} aria-busy={selectedLocalizedSubtitle ? localizationRuntimeBusy : subtitleRuntimeBusy}>
		{#if subtitleDisplay.selectedCue?.reviewable}
			{@const dubRaw = subtitleDisplay.selectedCue.raw as VideoLocalizationDubSubtitleCue}
			{@const dubBounds = dubSubtitleTimingBounds(draft?.dub_subtitles ?? [], dubRaw.subtitle_id, draft?.source_media.duration_ms ?? 0)}
			<div class="panel-head">
				<h2>合成配音字幕：{subtitleDisplay.selectedCue.id}</h2>
				<span>可编辑复审稿</span>
			</div>
			<div class="status-strip">
				<span class="time-range">{formatTimecode(subtitleDisplay.selectedCue.start_ms, draft?.source_media.frame_rate ?? 24)} - {formatTimecode(subtitleDisplay.selectedCue.end_ms, draft?.source_media.frame_rate ?? 24)}</span>
				<span>修改后同步到播放器、时间线与导出字幕</span>
			</div>
			{#if dubRaw.quality_flags.includes(DUB_SUBTITLE_SOURCE_CHANGED_FLAG)}
				<div class="status-strip timing-warning">配音轨已经变化；旧字幕仍保留供对照，重新生成后才会替换。</div>
			{/if}
			<div class="editor-grid time-grid">
				<ScrubbableTimeField
					label="入点"
					value={dubRaw.start_ms}
					min={dubBounds.minStartMs}
					max={Math.max(dubBounds.minStartMs, dubRaw.end_ms - MIN_SUBTITLE_DURATION_MS)}
					frameRate={draft?.source_media.frame_rate ?? 24}
					disabled={dubSubtitleReviewBusy}
					onCommit={(value) => onUpdateDubSubtitle?.(dubRaw.subtitle_id, { start_ms: value })}
				/>
				<ScrubbableTimeField
					label="出点"
					value={dubRaw.end_ms}
					min={dubRaw.start_ms + MIN_SUBTITLE_DURATION_MS}
					max={dubBounds.maxEndMs}
					frameRate={draft?.source_media.frame_rate ?? 24}
					disabled={dubSubtitleReviewBusy}
					onCommit={(value) => onUpdateDubSubtitle?.(dubRaw.subtitle_id, { end_ms: value })}
				/>
			</div>
			<form class="dub-subtitle-review" onsubmit={(event) => { event.preventDefault(); void saveDubSubtitleText(); }}>
				<label class="field">
					<span>当前上屏文本</span>
					<textarea class="subtitle-textarea" rows="2" bind:value={dubSubtitleEditorText} disabled={dubSubtitleReviewBusy}></textarea>
				</label>
				<button class="subtitle-generate-action" type="submit" aria-busy={dubSubtitleReviewBusy} disabled={dubSubtitleReviewBusy || !dubSubtitleEditorText.trim() || dubSubtitleEditorText.trim() === subtitleDisplay.selectedCue.text}>
					{#if dubSubtitleReviewBusy}<CommandSpinner size={12} /> 正在保存{:else}<CheckCircle2 size={12} /> 保存并同步上屏{/if}
				</button>
			</form>
			<p class="empty-text">这里只修改实际配音对应的显示字幕，不会改写本土化台词或重新生成音频。</p>
		{:else if subtitleDisplay.selectedCue && !subtitleDisplay.selectedCue.editable}
			<div class="panel-head">
				<h2>{subtitleDisplay.selectedCue.provisional
					? (subtitleDisplay.selectedCue.source === 'asr' ? '阶段 ASR 字幕' : '阶段本土化字幕')
					: subtitleDisplay.selectedCue.phaseLabel ?? '只读字幕'}：{subtitleDisplay.selectedCue.id}</h2>
				<span>{subtitleDisplay.selectedCue.provisional ? '只读阶段结果' : '只读识别结果'}</span>
			</div>
			<div class="status-strip">
				<span class="time-range">{formatTimecode(subtitleDisplay.selectedCue.start_ms, draft?.source_media.frame_rate ?? 24)} - {formatTimecode(subtitleDisplay.selectedCue.end_ms, draft?.source_media.frame_rate ?? 24)}</span>
				<span>{subtitleDisplay.selectedCue.phaseLabel ?? '处理中'}</span>
			</div>
			<label class="field">
				<span>当前上屏文本</span>
				<textarea class="subtitle-textarea" rows="2" value={subtitleDisplay.selectedCue.text} readonly></textarea>
			</label>
			<p class="empty-text">{subtitleDisplay.selectedCue.provisional
				? '这是当前子任务的阶段输出，后续步骤可能替换内容与时间码；正式稿生成后可编辑。'
				: '这是当前阶段的只读字幕结果；正式稿生成后可继续编辑。'}</p>
		{:else}
		{#if selectedLocalizedSubtitles.length > 1}
			<div class="multi-selection-summary">
				<div><strong>已选 {selectedLocalizedSubtitles.length} 条本土化字幕</strong><span>{selectedLocalizedSubtitlesContiguous ? '连续片段，将合并为一条配音' : '非连续片段，将按时间顺序合并台词并覆盖所选范围'}</span></div>
				<span>{formatTimecode(selectedLocalizedSubtitles[0].start_ms, draft?.source_media.frame_rate ?? 24)} - {formatTimecode(selectedLocalizedSubtitles.at(-1)?.end_ms ?? 0, draft?.source_media.frame_rate ?? 24)}</span>
			</div>
		{/if}
		<div class="panel-head">
			<h2>{selectedLocalizedSubtitle ? `本土化字幕：${selectedLocalizedSubtitle.subtitle_id}` : `ASR 字幕${selectedCue ? `：${selectedCue.cue_id}` : ''}`}</h2>
			<span>{selectedLocalizedSubtitle ? '初稿' : selectedCue ? subtitleStatusLabel(selectedCue.review_status) : '未选择'}</span>
		</div>
		{#if selectedLocalizedSubtitle}
			{@const localizedBounds = timingBounds('localized')}
			<div class="cue-meta">
				<span class="time-range">时长 {formatTimecode(selectedLocalizedSubtitle.end_ms - selectedLocalizedSubtitle.start_ms, draft?.source_media.frame_rate ?? 24)}</span>
				<span>来源 {selectedLocalizedSubtitle.source_cue_ids?.length || (selectedLocalizedSubtitle.linked_cue_id ? 1 : 0)} 条原文</span>
			</div>
			<div class="editor-grid time-grid">
				<ScrubbableTimeField
					label="入点"
						value={selectedLocalizedSubtitle.start_ms}
						min={localizedBounds.minStartMs}
						max={Math.max(localizedBounds.minStartMs, selectedLocalizedSubtitle.end_ms - MIN_SUBTITLE_DURATION_MS)}
						frameRate={draft?.source_media.frame_rate ?? 24}
					disabled={localizationRuntimeBusy}
					onPreview={(value) => onPreviewLocalizedSubtitle?.({ start_ms: value })}
					onCommit={(value) => onUpdateLocalizedSubtitle?.({ start_ms: value, end_ms: selectedLocalizedSubtitle.end_ms })}
				/>
				<ScrubbableTimeField
					label="出点"
						value={selectedLocalizedSubtitle.end_ms}
						min={selectedLocalizedSubtitle.start_ms + MIN_SUBTITLE_DURATION_MS}
						max={localizedBounds.maxEndMs}
						frameRate={draft?.source_media.frame_rate ?? 24}
					disabled={localizationRuntimeBusy}
					onPreview={(value) => onPreviewLocalizedSubtitle?.({ end_ms: value })}
					onCommit={(value) => onUpdateLocalizedSubtitle?.({ start_ms: selectedLocalizedSubtitle.start_ms, end_ms: value })}
				/>
			</div>
			<label class="field">
				<span>上屏字幕</span>
				<textarea class="subtitle-textarea" rows="1" value={selectedLocalizedSubtitle.text} disabled={localizationRuntimeBusy} oninput={(event) => onUpdateLocalizedSubtitle?.({ text: event.currentTarget.value })}></textarea>
			</label>
			<label class="field">
				<span>配音台词</span>
				<textarea class="subtitle-textarea" rows="1" value={selectedLocalizedSubtitle.tts_text ?? selectedLocalizedSubtitle.text} disabled={localizationRuntimeBusy} oninput={(event) => onUpdateLocalizedSubtitle?.({ tts_text: event.currentTarget.value })}></textarea>
			</label>
			{#if selectedLocalizedSubtitles.length <= 1 && onOpenSubtitleGenerate}
				<button class="subtitle-generate-action" type="button" aria-busy={generatingVoice} disabled={!canGenerateSubtitle || generatingVoice} data-tooltip="送到语音合成：带入当前字幕、参考音频和时间范围，使用语音合成页的默认模型与默认参数。" onpointerdown={commitFocusedSubtitleEditor} onclick={onOpenSubtitleGenerate}>
					{#if generatingVoice}<CommandSpinner size={13} /> 正在准备{:else}<ExternalLink size={13} /> 送到语音合成{/if}
				</button>
			{/if}
			{#if selectedLocalizedSubtitle.adaptation_note}
				<div class="audit-row accepted">
					<span>本土化处理</span>
					<p>{selectedLocalizedSubtitle.adaptation_note}</p>
				</div>
			{/if}
		{:else if selectedCue}
			{@const asrBounds = timingBounds('asr')}
			{@const timingConfirmation = timingConfirmationKind(selectedCue)}
			<div class="cue-meta">
					<span class="time-range">{formatTimecode(selectedCue.start_ms, draft?.source_media.frame_rate ?? 24)} - {formatTimecode(selectedCue.end_ms, draft?.source_media.frame_rate ?? 24)}</span>
				<div class="timing-meta">
					<span class:timing-low={selectedCue.timing_confidence === 'low'}>时间置信度：{selectedCue.timing_confidence ?? '未评估'}</span>
					{#if timingConfirmation === 'automatic'}
						<span class="timing-verified"><CheckCircle2 size={12} /> 已通过 ASR/VAD 校核</span>
					{:else if timingConfirmation === 'auditioned'}
						<span class="timing-verified"><CheckCircle2 size={12} /> 已试听确认</span>
					{:else if selectedCue.timing_confidence === 'low' || selectedCue.quality_flags.includes('timing_review_required')}
						<button class="confirm-timing" type="button" aria-busy={confirmingCueTiming} data-tooltip="确认时间码：试听当前片段并确认出入点准确后，解除低置信时间阻断。" onclick={onConfirmCueTiming} disabled={confirmingCueTiming || subtitleRuntimeBusy}>
							{#if confirmingCueTiming}<CommandSpinner size={12} />{:else}<CheckCircle2 size={12} />{/if} {confirmingCueTiming ? '确认中' : '确认时间码'}
						</button>
					{/if}
				</div>
			</div>
			{#if selectedCue.source_text_raw || selectedReviewSegments.length}
				<details class="asr-audit" open>
					<summary>识别与校对依据</summary>
					<div class="audit-row">
						<span>原始听写</span>
						<p>{selectedCue.source_text_raw || selectedReviewSegments.map((segment) => segment.raw_text).join(' ')}</p>
					</div>
					<div class="audit-row accepted">
						<span>当前采用</span>
						<p>{selectedCue.en_subtitle_text || '尚无文本'}</p>
					</div>
					{#each selectedReviewSegments.filter((segment) => segment.review_rejection_reason && segment.review_candidate_text) as segment}
						<div class="audit-row rejected">
							<span>已拒绝候选 · {reviewReasonLabel(segment.review_rejection_reason)}</span>
							<p>{segment.review_candidate_text}</p>
						</div>
					{/each}
				</details>
			{/if}
			<div class="editor-grid time-grid">
				<ScrubbableTimeField
					label="入点"
						value={selectedCue.start_ms ?? 0}
						min={asrBounds.minStartMs}
						max={Math.max(asrBounds.minStartMs, (selectedCue.end_ms ?? MIN_SUBTITLE_DURATION_MS) - MIN_SUBTITLE_DURATION_MS)}
						frameRate={draft?.source_media.frame_rate ?? 24}
					disabled={subtitleRuntimeBusy}
					onCommit={(value) => onUpdateCue({ start_ms: value })}
				/>
				<ScrubbableTimeField
					label="出点"
						value={selectedCue.end_ms ?? MIN_SUBTITLE_DURATION_MS}
						min={(selectedCue.start_ms ?? 0) + MIN_SUBTITLE_DURATION_MS}
						max={asrBounds.maxEndMs}
						frameRate={draft?.source_media.frame_rate ?? 24}
					disabled={subtitleRuntimeBusy}
					onCommit={(value) => onUpdateCue({ end_ms: value })}
				/>
			</div>
			<label class="field">
				<span>原文/ASR</span>
			<textarea class="subtitle-textarea" rows="1" value={selectedCue.en_subtitle_text ?? ''} disabled={subtitleRuntimeBusy} oninput={(event) => onUpdateCue({ en_subtitle_text: event.currentTarget.value })}></textarea>
			</label>
			<label class="field">
				<span>本土化字幕</span>
			<textarea class="subtitle-textarea" rows="1" value={selectedCue.zh_localized_subtitle_text ?? ''} disabled={subtitleRuntimeBusy} oninput={(event) => onUpdateCue({ zh_localized_subtitle_text: event.currentTarget.value })}></textarea>
			</label>
		{:else}
			<p class="empty-text">点击时间线上的字幕片段后，这里会同步显示原文/ASR 与本土化字幕。</p>
		{/if}
		{/if}
	</section>
	{/if}

	{#if activeSection === 'subtitle'}
	<section class="inspector-panel subtitle-style-panel">
		<div class="panel-head">
			<h2>字幕显示 · {selectedSubtitleSource === 'asr' ? 'ASR' : '本土化'}</h2>
		</div>
		<div class="subtitle-controls">
			<label class="subtitle-slider-row">
				<span>字号 <output>{selectedSubtitleStyle.fontSize}px</output></span>
				<input class="subtitle-range" type="range" min="12" max="32" step="1" value={selectedSubtitleStyle.fontSize} style={`--range-progress:${((selectedSubtitleStyle.fontSize - 12) / 20) * 100}%`} oninput={(event) => updateSelectedSubtitleStyle({ fontSize: Number(event.currentTarget.value) })} />
			</label>
			<label class="subtitle-slider-row">
				<span>背景透明度 <output>{(selectedSubtitleStyle.backgroundOpacity * 100).toFixed(0)}%</output></span>
				<input class="subtitle-range" type="range" min="0" max="0.8" step="0.05" value={selectedSubtitleStyle.backgroundOpacity} style={`--range-progress:${(selectedSubtitleStyle.backgroundOpacity / 0.8) * 100}%`} oninput={(event) => updateSelectedSubtitleStyle({ backgroundOpacity: Number(event.currentTarget.value) })} />
			</label>
			<label class="subtitle-slider-row">
				<span>X 轴偏移 <output>{signedPixelLabel(selectedSubtitleStyle.offsetX)}</output></span>
				<input class="subtitle-range" type="range" min="-240" max="240" step="1" value={selectedSubtitleStyle.offsetX} style={`--range-progress:${((selectedSubtitleStyle.offsetX + 240) / 480) * 100}%`} oninput={(event) => updateSelectedSubtitleStyle({ offsetX: Number(event.currentTarget.value) })} />
			</label>
			<label class="subtitle-slider-row">
				<span>Y 轴偏移 <output>{signedPixelLabel(selectedSubtitleStyle.offsetY)}</output></span>
				<input class="subtitle-range" type="range" min="-160" max="160" step="1" value={selectedSubtitleStyle.offsetY} style={`--range-progress:${((selectedSubtitleStyle.offsetY + 160) / 320) * 100}%`} oninput={(event) => updateSelectedSubtitleStyle({ offsetY: Number(event.currentTarget.value) })} />
			</label>
		</div>
		<div class="subtitle-style-grid" aria-label="字幕样式预设">
			{#each subtitleStylePresets as preset}
				<button
					class:active={selectedSubtitleStyle.stylePreset === preset.id}
					type="button"
					aria-label={`使用${preset.label}`}
					data-tooltip={`字幕样式：${preset.label}`}
					onclick={() => updateSelectedSubtitleStyle({ stylePreset: preset.id })}
				>
					<span class={`style-swatch ${preset.id}`}>字</span>
					<span>{preset.label}</span>
				</button>
			{/each}
		</div>
	</section>
	{/if}
</aside>

<style>
	.inspector {
		display: grid;
		align-content: start;
		gap: 10px;
		min-width: 0;
		padding: 12px;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.018), transparent 180px),
			#161a1f;
		max-height: calc(100dvh - 108px);
		overflow: auto;
	}

	.inspector.tasks-view {
		height: calc(100dvh - 108px);
		min-height: 0;
		gap: 6px;
		grid-template-rows: auto minmax(0, 1fr);
		align-content: stretch;
		overflow: hidden;
	}

	.inspector.subtitle-view {
		height: 100%;
		max-height: none;
		min-height: 0;
		box-sizing: border-box;
		grid-template-rows: auto;
		align-content: start;
		overflow: auto;
	}

	.inspector.dubbing-view {
		grid-template-columns: minmax(0, 1fr);
		height: 100%;
		max-height: none;
		min-height: 0;
		box-sizing: border-box;
		grid-template-rows: auto;
		align-content: start;
		overflow: auto;
	}

	.task-view-content {
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}

	.empty-dubbing-results {
		border: 0;
		background: transparent;
	}

	.inspector-mode-tabs {
		position: sticky;
		top: 0;
		z-index: 3;
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 4px;
		padding: 5px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #11161b;
		box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
	}

	.inspector-mode-tabs button {
		border: 1px solid transparent;
		border-radius: 6px;
		min-height: 26px;
		background: transparent;
		color: var(--muted);
		font-size: 11px;
		cursor: pointer;
	}

	.inspector-mode-tabs button {
		min-height: 25px;
		font-weight: 760;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
	}

	.inspector-mode-tabs button.active {
		background: #273038;
		border-color: var(--line);
		color: var(--text);
	}

	.inspector-panel {
		border: 1px solid var(--line);
		border-radius: 7px;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent),
			#1c2126;
		overflow: hidden;
	}

	.inspector-panel.runtime-locked {
		border-color: rgba(82, 149, 169, 0.42);
		box-shadow: inset 0 0 0 1px rgba(82, 149, 169, 0.08);
	}

	.subtitle-panel {
		display: flex;
		flex-direction: column;
		min-height: 0;
		height: auto;
		border: 0;
		border-radius: 0;
		background: transparent;
		overflow: visible;
	}

	.subtitle-style-panel {
		border: 0;
		border-top: 1px solid var(--line);
		border-radius: 0;
		background: transparent;
	}

	.dubbing-history-panel {
		display: flex;
		min-height: 320px;
		max-height: min(58vh, 620px);
		border: 0;
		border-top: 1px solid var(--line);
		border-radius: 0;
		background: transparent;
		overflow: hidden;
	}

	.dubbing-history-panel :global(.tts-history) {
		flex: 1 1 0;
		min-height: 0;
		overflow: hidden;
	}

	.subtitle-panel :global(.tts-history) {
		flex: 1 1 0;
		min-height: 0;
		overflow: hidden;
	}

	.subtitle-panel.runtime-locked {
		border: 0;
		box-shadow: none;
	}

	.subtitle-panel .panel-head {
		padding: 3px 0 10px;
	}

	.multi-selection-summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 7px 0;
		border-bottom: 1px solid rgba(87, 208, 200, 0.2);
		color: #dffbf8;
	}

	.multi-selection-summary div {
		display: grid;
		gap: 2px;
	}

	.multi-selection-summary strong { font-size: 11px; }
	.multi-selection-summary span { color: #8fa6aa; font-size: 9px; }

	.subtitle-panel .cue-meta {
		padding: 8px 0;
	}

	.subtitle-panel .time-grid {
		column-gap: 18px;
		padding: 8px 0 0;
	}

	.subtitle-panel > .field {
		padding: 8px 0 0;
	}

	.subtitle-panel .asr-audit {
		margin: 8px 0 0;
		border: 0;
		border-top: 1px solid rgba(255, 255, 255, 0.09);
		border-bottom: 1px solid rgba(255, 255, 255, 0.09);
		border-radius: 0;
		background: transparent;
	}

	.subtitle-panel .asr-audit summary {
		padding: 8px 0;
	}

	.subtitle-panel .audit-row {
		padding-left: 0;
		padding-right: 0;
	}

	@media (min-width: 1381px) {
		.inspector,
		.inspector.tasks-view,
		.inspector.subtitle-view {
			height: 100%;
			max-height: 100%;
			min-height: 0;
		}
	}

	.time-range {
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-variant-numeric: tabular-nums;
		color: #d5e4e8;
		font-size: 10px;
	}

	.panel-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 10px 11px;
		border-bottom: 1px solid #303941;
	}

	.panel-head h2 {
		margin: 0;
		font-size: 13px;
	}

	.panel-head span,
	.empty-text,
	.cue-meta,
	.field span {
		color: var(--muted);
		font-size: 11px;
	}

	.cue-meta,
	.field {
		padding: 9px 11px;
	}

	.cue-meta {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		border-bottom: 1px solid #303941;
	}

	.cue-meta .timing-low {
		color: #efa3a9;
	}

	.timing-meta,
	.timing-verified,
	.confirm-timing {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}

	.timing-meta {
		justify-content: flex-end;
		flex-wrap: wrap;
	}

	.timing-verified {
		color: #79d7bd;
	}

	.confirm-timing {
		min-height: 24px;
		padding: 2px 7px;
		border: 1px solid #45665e;
		border-radius: 6px;
		background: #17332f;
		color: #bceee4;
		font-size: 11px;
		cursor: pointer;
	}

	.confirm-timing:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.asr-audit {
		margin: 8px 11px 2px;
		border: 1px solid #303941;
		border-radius: 7px;
		background: #14191e;
		overflow: hidden;
	}

	.asr-audit summary {
		padding: 7px 8px;
		color: #b8c3c8;
		font-size: 11px;
		font-weight: 760;
		cursor: pointer;
	}

	.audit-row {
		display: grid;
		gap: 3px;
		padding: 7px 8px;
		border-top: 1px solid rgba(255, 255, 255, 0.06);
	}

	.audit-row span {
		color: #87949b;
		font-size: 10px;
	}

	.audit-row p {
		margin: 0;
		color: #d8e0e3;
		font-size: 11px;
		line-height: 1.45;
	}

	.audit-row.accepted {
		border-left: 2px solid rgba(73, 167, 132, 0.72);
	}

	.audit-row.rejected {
		border-left: 2px solid rgba(198, 82, 91, 0.72);
		background: rgba(101, 35, 42, 0.12);
	}

	.field {
		display: grid;
		gap: 5px;
	}

	.field textarea {
		width: 100%;
		box-sizing: border-box;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #12171c;
		color: var(--text);
		padding: 7px 8px;
		resize: vertical;
		font-size: 12px;
		line-height: 18px;
	}

	.field textarea.subtitle-textarea {
		min-height: 36px;
		height: auto;
		max-height: 160px;
		field-sizing: content;
		resize: vertical;
	}

	.editor-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 4px;
	}

	.time-grid {
		padding: 10px 11px 0;
	}

	@media (max-width: 1380px) {
		.inspector {
			max-height: none;
			grid-template-columns: minmax(0, 0.95fr) minmax(320px, 0.75fr);
			align-items: start;
		}

		.inspector.tasks-view {
			height: var(--stacked-inspector-height, min(720px, calc(100dvh - 64px)));
			max-height: none;
			grid-template-columns: 1fr;
			grid-template-rows: auto minmax(0, 1fr);
			align-items: stretch;
		}

		.inspector-mode-tabs {
			grid-column: 1 / -1;
		}
	}

	@media (max-width: 900px) {
		.inspector {
			grid-template-columns: 1fr;
		}

		.inspector.tasks-view {
			height: var(--stacked-inspector-height, min(640px, calc(100dvh - 32px)));
		}
	}

	.subtitle-controls {
		display: grid;
		gap: 2px;
		padding: 6px 0 2px;
	}

	.subtitle-style-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 5px;
		padding-top: 8px;
	}

	.subtitle-style-grid button {
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
		height: 30px;
		padding: 0 7px;
		border: 1px solid #303940;
		border-radius: 5px;
		background: #13171b;
		color: #aeb9be;
		font-size: 10px;
		line-height: 1;
		white-space: nowrap;
		cursor: pointer;
		transition: border-color 120ms ease, background 120ms ease, color 120ms ease, transform 80ms ease;
	}

	.subtitle-style-grid button:hover {
		border-color: rgba(87, 208, 200, 0.42);
		background: rgba(87, 208, 200, 0.06);
		color: #dce8e9;
	}

	.subtitle-style-grid button:active {
		transform: translateY(1px);
	}

	.subtitle-style-grid button.active {
		border-color: rgba(87, 208, 200, 0.72);
		background: rgba(87, 208, 200, 0.12);
		color: #e7fbf8;
		box-shadow: inset 0 0 0 1px rgba(87, 208, 200, 0.08);
	}

	.style-swatch {
		display: inline-grid;
		place-items: center;
		width: 20px;
		height: 18px;
		flex: 0 0 20px;
		border-radius: 3px;
		background: #252b30;
		color: #fff;
		font-size: 10px;
		font-weight: 800;
	}

	.style-swatch.yellow-outline { color: #fff1a8; text-shadow: 1px 1px #000, -1px -1px #000; }
	.style-swatch.boxed { background: rgba(0, 0, 0, 0.78); }
	.style-swatch.clean-shadow { text-shadow: 0 2px 3px #000; }
	.style-swatch.strong-outline { text-shadow: 1px 1px #000, -1px -1px #000, 1px -1px #000, -1px 1px #000; }
	.style-swatch.warm-outline { color: #fff4dc; text-shadow: 0 1px 2px #2c2118; }
	.style-swatch.cyan-outline { color: #ddfbff; text-shadow: 0 1px 2px #063f46; }
	.style-swatch.caption-bar { width: 22px; flex-basis: 22px; background: #050607; border-radius: 1px; }
	.style-swatch.soft-panel { background: rgba(25, 33, 38, 0.88); box-shadow: inset 0 0 5px rgba(255, 255, 255, 0.08); }

	@media (max-width: 1120px) {
		.subtitle-style-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
	}

	.subtitle-slider-row {
		display: grid;
		grid-template-columns: minmax(104px, auto) minmax(110px, 1fr);
		align-items: center;
		gap: 12px;
		min-height: 36px;
		padding: 0;
		color: #b5c1c5;
		font-size: 11px;
		cursor: pointer;
	}

	.subtitle-slider-row > span {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 6px;
	}

	.subtitle-slider-row output {
		color: #7f969a;
		font: inherit;
		font-variant-numeric: tabular-nums;
	}

	label.subtitle-slider-row > input.subtitle-range[type='range'] {
		--range-progress: 0%;
		width: 100%;
		height: 18px;
		min-height: 0;
		margin: 0;
		padding: 0;
		border: 0;
		border-radius: 0;
		appearance: none;
		-webkit-appearance: none;
		background: transparent;
		box-shadow: none;
		cursor: pointer;
	}

	label.subtitle-slider-row > input.subtitle-range[type='range']:focus-visible {
		outline: none;
	}

	.subtitle-range::-webkit-slider-runnable-track {
		height: 1px;
		border-radius: 999px;
		background: linear-gradient(to right, #668782 0 var(--range-progress), #313c40 var(--range-progress) 100%);
	}

	.subtitle-range::-webkit-slider-thumb {
		width: 10px;
		height: 10px;
		margin-top: -4.5px;
		border: 1px solid #8faeaa;
		border-radius: 50%;
		appearance: none;
		-webkit-appearance: none;
		background: #526d69;
		box-shadow: none;
	}

	.subtitle-range:focus-visible::-webkit-slider-thumb {
		box-shadow: 0 0 0 2px rgba(143, 174, 170, 0.18);
	}

	.subtitle-range::-moz-range-track {
		height: 1px;
		border-radius: 999px;
		background: #313c40;
	}

	.subtitle-range::-moz-range-progress {
		height: 1px;
		border-radius: 999px;
		background: #668782;
	}

	.subtitle-range::-moz-range-thumb {
		width: 10px;
		height: 10px;
		border: 1px solid #8faeaa;
		border-radius: 50%;
		background: #526d69;
		box-shadow: none;
	}

	.subtitle-range:focus-visible::-moz-range-thumb {
		box-shadow: 0 0 0 2px rgba(143, 174, 170, 0.18);
	}

	.subtitle-generate-action {
		justify-self: end;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-height: 28px;
		padding: 0 10px;
		border: 1px solid rgba(116, 159, 171, 0.28);
		background: rgba(74, 111, 122, 0.16);
		color: #c9dadd;
		font-size: 10px;
	}

	.dub-subtitle-review {
		display: grid;
		gap: 8px;
	}

	.dub-subtitle-review .subtitle-generate-action {
		margin-right: 0;
	}
</style>
