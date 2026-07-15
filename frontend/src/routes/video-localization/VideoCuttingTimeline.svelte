<script lang="ts">
	import { Captions, ChevronsLeft, ChevronsRight, Eye, EyeOff, FileAudio, FileUp, GripVertical, LoaderCircle, Lock, Mic2, MousePointer2, Pause, Play, Redo2, RefreshCw, Save, Scissors, SkipBack, SkipForward, Trash2, Undo2, Unlock, Wand2, ZoomIn, ZoomOut } from 'lucide-svelte';
	import { tick } from 'svelte';
	import { buildTimelineTicks, formatTimecode, formatTimelineZoom } from '$lib/audio/waveform';
	import ContextMenu from '$lib/components/shared/ContextMenu.svelte';
	import type { VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationOperation, VideoLocalizationSubtitleCue, VideoLocalizationTimelineClip } from '$lib/api/types';
	import { durationLabel, timelineClipWaveformUrl } from './utils';
	import { MIN_SUBTITLE_DURATION_MS, reorderAudioTracks, subtitleCueDragBounds, timeRangeIntersectsViewport, timelineViewportRange, TRACK_LABELS, type SubtitlePreviewSource, type SubtitlePreviewState, type VideoLocalizationAudioTrackId, type VideoLocalizationAudioTrackOrder, type VideoLocalizationTrackId, type VideoLocalizationTrackState, type VideoLocalizationTrackStates } from './studio-state';
	import { buildSubtitleTrackCommands, buildTimelineContextMenuItems, type SubtitleTrackKind, type TimelineContextMenuTarget } from './timeline-context-menu';
	import ActivityNotice from './ActivityNotice.svelte';
	import { activityTaskAffectsTrack, type ActivityTask } from './activity-notice';
	import type { AsrOperationPreview } from './asr-operation-preview';
	import { timelinePointerIntent } from './timeline-interaction';
	import EditableAudioClip from './EditableAudioClip.svelte';

	let {
		projectId,
		draft,
		selectedCueId,
		currentTimeMs,
		isPlaying,
			latestOperation,
			extractingAudio,
			separatingStems,
			noticeKind,
			noticeSummary,
			noticeDetail,
			activityTasks,
			asrBusy,
		trackStates,
		audioTrackOrder,
		timelineZoom,
		subtitlePreview,
		onSelectCue,
		onClearCueSelection = undefined,
		onExtractAudio,
		onRestoreOriginalAudio,
		onSeparateStems,
		onImportLocalizedSrt,
		onTransportAction,
		onTrackStateChange,
		onAudioTrackOrderChange,
		onTimelineZoomChange,
		onToggleSubtitleSource,
		onSeekTimeline,
		onUpdateCueTime,
		onUpdateLocalizedSubtitleTime,
		onClearSubtitleTrack,
		onDeleteSubtitleItem,
		onFillSubtitleGaps,
		onGenerateAsr,
		onOpenTaskCenter = undefined,
		asrPreview = null,
		onSplitCue,
		onMergeCue,
		onDeleteCue,
		onSaveSelectionAsVoice,
		onGenerateToSelection,
		onUpdateTimelineClip,
		onDeleteTimelineClip,
		hoverScrubEnabled = true,
		onHoverScrubChange = undefined,
		onHoverScrub = undefined,
		onHoverScrubEnd = undefined,
		onUndoTimelineClip,
		onRedoTimelineClip,
		canUndoTimeline,
		canRedoTimeline
	}: {
		projectId: string;
		draft: VideoLocalizationDraft | null;
		selectedCueId: string;
		currentTimeMs: number;
		isPlaying: boolean;
			latestOperation: VideoLocalizationOperation | null;
			extractingAudio: boolean;
			separatingStems: boolean;
			noticeKind: 'idle' | 'success' | 'error';
			noticeSummary: string;
			noticeDetail: string;
			activityTasks: ActivityTask[];
			asrBusy: boolean;
		trackStates: VideoLocalizationTrackStates;
		audioTrackOrder: VideoLocalizationAudioTrackOrder;
		timelineZoom: number;
		subtitlePreview: SubtitlePreviewState;
		onSelectCue: (cueId: string) => void;
		onClearCueSelection?: () => void;
		onExtractAudio: () => void;
		onRestoreOriginalAudio: () => void | Promise<void>;
		onSeparateStems: () => void;
		onImportLocalizedSrt: () => void;
		onTransportAction: (action: 'start' | 'play-pause' | 'next') => void;
		onTrackStateChange: (trackId: VideoLocalizationTrackId, patch: Partial<VideoLocalizationTrackState>) => void;
		onAudioTrackOrderChange: (order: VideoLocalizationAudioTrackOrder) => void;
		onTimelineZoomChange: (zoom: number) => void;
		onToggleSubtitleSource: (source: SubtitlePreviewSource) => void;
		onSeekTimeline: (timeMs: number) => void;
		onUpdateCueTime: (cueId: string, startMs: number, endMs: number) => void;
		onUpdateLocalizedSubtitleTime: (subtitleId: string, startMs: number, endMs: number) => void;
		onClearSubtitleTrack: (track: SubtitleTrackKind) => void | Promise<void>;
		onDeleteSubtitleItem: (track: SubtitleTrackKind, itemId: string) => void | Promise<void>;
		onFillSubtitleGaps: (track: SubtitleTrackKind) => void | Promise<void>;
		onGenerateAsr: () => void | Promise<void>;
		onOpenTaskCenter?: () => void;
		asrPreview?: AsrOperationPreview | null;
		onSplitCue: () => void;
		onMergeCue: () => void;
		onDeleteCue: () => void;
		onSaveSelectionAsVoice: (startMs: number, endMs: number) => void;
		onGenerateToSelection: (startMs: number, endMs: number) => void;
		onUpdateTimelineClip: (clipId: string, startMs: number, endMs: number, sourceStartMs: number, sourceEndMs: number | null) => void;
		onDeleteTimelineClip: (clipId: string) => void;
		hoverScrubEnabled?: boolean;
		onHoverScrubChange?: (enabled: boolean) => void;
		onHoverScrub?: (timeMs: number) => void;
		onHoverScrubEnd?: () => void;
		onUndoTimelineClip: () => void;
		onRedoTimelineClip: () => void;
		canUndoTimeline: boolean;
		canRedoTimeline: boolean;
	} = $props();

	type DragMode = 'move' | 'trim-start' | 'trim-end';
	type SubtitleTimelineItem = VideoLocalizationCue | VideoLocalizationSubtitleCue;
	type CueDragState = {
		itemId: string;
		trackKind: SubtitleTrackKind;
		mode: DragMode;
		startX: number;
		startMs: number;
		endMs: number;
		durationMs: number;
		minStartMs: number;
		maxEndMs: number;
	};
	type ClipDragState = {
		clipId: string;
		mode: DragMode;
		startX: number;
		startMs: number;
		endMs: number;
		durationMs: number;
		sourceStartMs: number;
		sourceEndMs: number | null;
	};

	let timelineContentEl: HTMLDivElement | null = null;
	let trackCanvasEl: HTMLDivElement | null = null;
	let dragState = $state<CueDragState | null>(null);
	let clipDragState = $state<ClipDragState | null>(null);
	let liveCueTimes = $state<Record<string, { start_ms: number; end_ms: number }>>({});
	let liveClipTimes = $state<Record<string, { start_ms: number; end_ms: number; source_start_ms?: number; source_end_ms?: number | null }>>({});
	let timelineScrollLeft = $state(0);
	let timelineViewportWidth = $state(0);
	let timelineSeekDrag = $state(false);
	let timelinePanState = $state<{ startX: number; scrollLeft: number } | null>(null);
	let autoFollowSuspendedUntil = 0;
	let programmaticTimelineScroll = false;
	let wasPlaying = false;
	let pendingSeekMs = 0;
	let seekAnimationFrame = 0;
	let selectionDrag = $state<'start' | 'end' | null>(null);
	let rangeCreateState = $state<{ startX: number; startMs: number; moved: boolean } | null>(null);
	let preserveRangeOnCueSelection = false;
	let rangeStartMs = $state<number | null>(null);
	let rangeEndMs = $state<number | null>(null);
	const MIN_RANGE_DURATION_MS = MIN_SUBTITLE_DURATION_MS;
	let labelColumnWidth = $state(236);
	let editingTrackId = $state<VideoLocalizationTrackId | null>(null);
	let editingTrackValue = $state('');
	let openVolumeTrack = $state<VideoLocalizationTrackId | null>(null);
	let draggedAudioTrackId = $state<VideoLocalizationAudioTrackId | null>(null);
	let dragOverAudioTrackId = $state<VideoLocalizationAudioTrackId | null>(null);
	let dragOverAudioTrackPlacement = $state<'before' | 'after'>('before');
	let volumeClickTimer: ReturnType<typeof setTimeout> | null = null;
	let dubWaveforms = $state<Record<string, { bars: number[]; durationSeconds: number }>>({});
	let originalWaveformUnavailable = $state(false);
	let originalWaveformRevision = $state(0);
	let restoredSourceOperationMarker = $state('');
	let timelineContextMenu = $state<{ x: number; y: number; target: TimelineContextMenuTarget } | null>(null);
	let selectedTimelineItem = $state<{ kind: 'subtitle' | 'audio'; trackId: VideoLocalizationTrackId; itemId: string } | null>(null);
	let hoverTimeMs = $state<number | null>(null);
	let hoverScrubFrame = 0;
	const DEFAULT_TRACK_HEIGHTS: Record<VideoLocalizationTrackId, number> = {
		original: 58,
		vocals: 58,
		background: 58,
		subtitles: 34,
		localizedSubtitles: 34,
		dub: 58
	};
	let trackHeights = $state<Record<VideoLocalizationTrackId, number>>({ ...DEFAULT_TRACK_HEIGHTS });

	const hasVideo = $derived(Boolean(draft?.source_media.video_path || draft?.source_media.filename));
	const hasRecoverableVideo = $derived(Boolean(draft?.source_media.video_path));
	const hasSourceAudio = $derived(Boolean(draft?.source_media.audio_path || draft?.stems.original_audio_path));
	const canAttemptOriginalRecovery = $derived(hasSourceAudio || hasRecoverableVideo);
	const hasVocals = $derived(Boolean(draft?.stems.vocals_clean_path));
	const vocalsTrackReady = $derived(hasVocals && clipsForTrack('vocals').some((clip) => Boolean(clip.audio_path)));
	const stemsReady = $derived(Boolean(draft?.stems.vocals_clean_path && draft?.stems.background_path));
	const durationMs = $derived(Math.max(
		draft?.source_media.duration_ms ?? 0,
		...(draft?.cues ?? []).map((cue) => cue.end_ms ?? 0),
		...(draft?.localized_subtitles ?? []).map((cue) => cue.end_ms),
		...(draft?.timeline_clips ?? []).map((clip) => clip.end_ms ?? 0),
		0
	));
	const timelineDurationMs = $derived(durationMs ? Math.max(durationMs, 1000) : 60000);
	const subtitleTimelineLimitMs = $derived(Math.max(draft?.source_media.duration_ms ?? timelineDurationMs, MIN_SUBTITLE_DURATION_MS));
	const timelineTicks = $derived(buildTimelineTicks(timelineDurationMs / 1000, timelineZoom));
	const renderViewport = $derived(timelineViewportWidth > 0
		? timelineViewportRange(timelineDurationMs, timelineZoom, timelineScrollLeft, timelineViewportWidth)
		: { startMs: 0, endMs: timelineDurationMs });
	const visibleTimelineTicks = $derived(timelineTicks.filter((tick) => tick.time * 1000 >= renderViewport.startMs && tick.time * 1000 <= renderViewport.endMs));
	const visibleAsrCues = $derived((draft?.cues ?? []).filter((cue) => dragState?.itemId === cue.cue_id || timeRangeIntersectsViewport(cue.start_ms, cue.end_ms, renderViewport)));
	const visibleAsrPreviewCues = $derived((asrPreview?.cues ?? []).filter((cue) => timeRangeIntersectsViewport(cue.start_ms, cue.end_ms, renderViewport)));
	const visibleLocalizedSubtitles = $derived((draft?.localized_subtitles ?? []).filter((cue) => dragState?.itemId === cue.subtitle_id || timeRangeIntersectsViewport(cue.start_ms, cue.end_ms, renderViewport)));
	const playheadPercent = $derived(Math.max(0, Math.min(100, (currentTimeMs / timelineDurationMs) * 100)));
	const selectedCue = $derived(draft?.cues.find((cue) => cue.cue_id === selectedCueId) ?? null);
	const canEditSelectedCue = $derived(Boolean(selectedCue) && !trackInteractionLocked('subtitles'));
	const canSplitSelectedCue = $derived(Boolean(!trackInteractionLocked('subtitles') && selectedCue && selectedCue.start_ms !== null && selectedCue.end_ms !== null && selectedCue.end_ms - selectedCue.start_ms >= 700));
	const canMergeSelectedCue = $derived(Boolean(!trackInteractionLocked('subtitles') && selectedCue && nextCueAfter(selectedCue)));
	const hasRangeSelection = $derived(rangeStartMs !== null && rangeEndMs !== null && Math.abs(rangeEndMs - rangeStartMs) >= MIN_RANGE_DURATION_MS);
	const rangeStartValue = $derived(rangeStartMs ?? 0);
	const rangeEndValue = $derived(rangeEndMs ?? rangeStartValue);
	const rangeStartPercent = $derived(Math.max(0, Math.min(100, (rangeStartValue / timelineDurationMs) * 100)));
	const rangeEndPercent = $derived(Math.max(0, Math.min(100, (rangeEndValue / timelineDurationMs) * 100)));
	const rangeLeftPercent = $derived(hasRangeSelection ? Math.max(0, Math.min(100, (Math.min(rangeStartValue, rangeEndValue) / timelineDurationMs) * 100)) : 0);
	const rangeWidthPercent = $derived(hasRangeSelection ? Math.max(0, Math.min(100 - rangeLeftPercent, (Math.abs(rangeEndValue - rangeStartValue) / timelineDurationMs) * 100)) : 0);
	const masterLevel = $derived(isPlaying ? estimateMasterLevel() : 0);
	const asrSubtitleVisible = $derived(subtitlePreview.enabled && subtitlePreview.sources?.asr === true);
	const localizedSubtitleVisible = $derived(subtitlePreview.enabled && subtitlePreview.sources?.localized === true);
	const asrCommandContext = $derived({
		itemCount: draft?.cues.length ?? 0,
		locked: trackStates.subtitles.locked === true,
		canGenerateAsr: vocalsTrackReady,
		asrBusy,
		trackBusy: trackRuntimeBusy('subtitles'),
		asrUnavailableReason: '人声轨有可用音频后，才能听写生成 ASR 字幕',
		hasSelectionPoints: rangeStartMs !== null || rangeEndMs !== null,
		onGenerateAsr,
		onClearSubtitleTrack,
		onDeleteSubtitleItem: deleteSubtitleTimelineItem,
		onDeleteAudioClip: deleteAudioTimelineItem,
		onFillSubtitleGaps,
		onSetSelectionStart: (timeMs: number) => setSelectionPoint('start', timeMs),
		onSetSelectionEnd: (timeMs: number) => setSelectionPoint('end', timeMs),
		onClearSelection: clearSelection
	});
	const asrGenerateCommand = $derived(buildSubtitleTrackCommands('asr', asrCommandContext)[0]);
	const timelineContextMenuLabel = $derived(timelineContextMenu?.target.kind === 'track'
		? `${TRACK_LABELS[timelineContextMenu.target.trackId]}操作`
		: '时间线操作');
	const timelineContextMenuItems = $derived.by(() => {
		if (!timelineContextMenu) return [];
		const target = timelineContextMenu.target;
		const subtitleTrack = target.kind === 'track' || target.kind === 'subtitle-clip'
			? target.subtitleTrack
			: undefined;
		const targetTrackId = 'trackId' in target ? target.trackId : null;
		const itemCount = subtitleTrack === 'asr'
			? (draft?.cues.length ?? 0)
			: subtitleTrack === 'localized'
				? (draft?.localized_subtitles.length ?? 0)
				: 0;
		return buildTimelineContextMenuItems(target, {
			itemCount,
			locked: targetTrackId ? trackStates[targetTrackId].locked === true : false,
			canGenerateAsr: vocalsTrackReady,
			asrBusy,
			trackBusy: targetTrackId ? trackRuntimeBusy(targetTrackId) : false,
			asrUnavailableReason: '人声轨有可用音频后，才能听写生成 ASR 字幕',
			hasSelectionPoints: rangeStartMs !== null || rangeEndMs !== null,
			onGenerateAsr,
			onClearSubtitleTrack,
				onDeleteSubtitleItem: deleteSubtitleTimelineItem,
				onDeleteAudioClip: deleteAudioTimelineItem,
			onFillSubtitleGaps,
			onSetSelectionStart: (timeMs) => setSelectionPoint('start', timeMs),
			onSetSelectionEnd: (timeMs) => setSelectionPoint('end', timeMs),
			onClearSelection: clearSelection
		});
	});

	$effect(() => {
		projectId;
		originalWaveformUnavailable = false;
		originalWaveformRevision = 0;
		restoredSourceOperationMarker = '';
	});

	$effect(() => {
		const marker = latestOperation?.kind === 'source_audio' && latestOperation.status === 'success'
			? `${latestOperation.operation_id}:${latestOperation.completed_at ?? 'completed'}`
			: '';
		if (!marker || marker === restoredSourceOperationMarker) return;
		restoredSourceOperationMarker = marker;
		originalWaveformUnavailable = false;
		originalWaveformRevision = Date.now();
	});

	function isLocalizedSubtitle(item: SubtitleTimelineItem): item is VideoLocalizationSubtitleCue {
		return typeof (item as VideoLocalizationSubtitleCue).subtitle_id === 'string';
	}

	function subtitleItemId(item: SubtitleTimelineItem): string {
		return isLocalizedSubtitle(item) ? item.subtitle_id : item.cue_id;
	}

	function subtitleLiveKey(trackKind: SubtitleTrackKind, itemId: string) {
		return `${trackKind}:${itemId}`;
	}

	function cueLeft(cue: SubtitleTimelineItem, trackKind: SubtitleTrackKind) {
		const time = cueLiveTime(cue, trackKind);
		return Math.max(0, Math.min(100, (time.start_ms / timelineDurationMs) * 100));
	}

	function cueWidth(cue: SubtitleTimelineItem, trackKind: SubtitleTrackKind) {
		const { start_ms: start, end_ms: end } = cueLiveTime(cue, trackKind);
		const left = Math.max(0, Math.min(100, (start / timelineDurationMs) * 100));
		return Math.max(0, Math.min(100 - left, ((end - start) / timelineDurationMs) * 100));
	}

	function clipLeft(startMs: number | null | undefined) {
		return Math.max(0, Math.min(100, (((startMs ?? 0) / timelineDurationMs) * 100)));
	}

	function clipWidth(startMs: number | null | undefined, endMs: number | null | undefined) {
		const start = startMs ?? 0;
		const end = Math.max(start + 300, endMs ?? start + 1800);
		const left = clipLeft(start);
		return Math.max(0.02, Math.min(100 - left, ((end - start) / timelineDurationMs) * 100));
	}

	function timelineClipTime(clip: VideoLocalizationTimelineClip) {
		const live = liveClipTimes[clip.clip_id];
		const start = clip.start_ms ?? 0;
		const end = clip.end_ms ?? start + 1800;
		return live ?? { start_ms: start, end_ms: Math.max(start + 300, end), source_start_ms: clip.source_start_ms ?? 0, source_end_ms: clip.source_end_ms ?? null };
	}

	function cueLabel(cue: SubtitleTimelineItem) {
		if (isLocalizedSubtitle(cue)) return cue.text.trim() || '未命名本土化字幕';
		return cue.en_subtitle_text?.trim() || '未命名 ASR 字幕';
	}

	function nextCueAfter(cue: VideoLocalizationCue | null) {
		if (!cue || !draft?.cues.length) return null;
		const sorted = [...draft.cues].sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0));
		const index = sorted.findIndex((item) => item.cue_id === cue.cue_id);
		return index >= 0 ? (sorted[index + 1] ?? null) : null;
	}

	function toggleMuted(trackId: VideoLocalizationTrackId) {
		const muted = !trackStates[trackId].muted;
		onTrackStateChange(trackId, { muted, ...(muted ? { solo: false } : {}) });
	}

	function toggleSolo(trackId: VideoLocalizationTrackId) {
		const solo = !trackStates[trackId].solo;
		onTrackStateChange(trackId, { solo, ...(solo ? { muted: false } : {}) });
	}

	function toggleLocked(trackId: VideoLocalizationTrackId) {
		onTrackStateChange(trackId, { locked: !trackStates[trackId].locked });
	}

	function audioTrackOrderValue(trackId: VideoLocalizationAudioTrackId) {
		const index = audioTrackOrder.indexOf(trackId);
		return 3 + (index < 0 ? 99 : index);
	}

	function audioTrackStyle(trackId: VideoLocalizationAudioTrackId) {
		return `height:${trackHeights[trackId]}px;order:${audioTrackOrderValue(trackId)}`;
	}

	function subtitleTrackStyle(trackId: 'subtitles' | 'localizedSubtitles') {
		return `height:${trackHeights[trackId]}px;order:${trackId === 'subtitles' ? 1 : 2}`;
	}

	function beginAudioTrackReorder(event: DragEvent, trackId: VideoLocalizationAudioTrackId) {
		draggedAudioTrackId = trackId;
		dragOverAudioTrackId = null;
		if (event.dataTransfer) {
			event.dataTransfer.effectAllowed = 'move';
			event.dataTransfer.setData('text/plain', trackId);
		}
	}

	function markAudioTrackDropTarget(event: DragEvent, trackId: VideoLocalizationAudioTrackId) {
		if (!draggedAudioTrackId || draggedAudioTrackId === trackId) return;
		event.preventDefault();
		dragOverAudioTrackId = trackId;
		const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
		dragOverAudioTrackPlacement = event.clientY >= rect.top + rect.height / 2 ? 'after' : 'before';
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
	}

	function dropAudioTrack(event: DragEvent, targetId: VideoLocalizationAudioTrackId) {
		event.preventDefault();
		if (draggedAudioTrackId && draggedAudioTrackId !== targetId) {
			onAudioTrackOrderChange(reorderAudioTracks(audioTrackOrder, draggedAudioTrackId, targetId, dragOverAudioTrackPlacement));
		}
		endAudioTrackReorder();
	}

	function endAudioTrackReorder() {
		draggedAudioTrackId = null;
		dragOverAudioTrackId = null;
		dragOverAudioTrackPlacement = 'before';
	}

	async function requestOriginalAudioRecovery() {
		if (extractingAudio || !canAttemptOriginalRecovery) return;
		await onRestoreOriginalAudio();
		originalWaveformUnavailable = false;
		originalWaveformRevision += 1;
	}

	function clipWaveformSrc(clip: VideoLocalizationTimelineClip, trackId: VideoLocalizationTrackId) {
		const url = timelineClipWaveformUrl(projectId, clip);
		if (trackId !== 'original' || !url || !originalWaveformRevision) return url;
		return `${url}?recovery=${originalWaveformRevision}`;
	}

	function trackName(trackId: VideoLocalizationTrackId) {
		return trackStates[trackId]?.label?.trim() || TRACK_LABELS[trackId];
	}

	function renameTrack(trackId: VideoLocalizationTrackId, value: string) {
		const label = value.trim();
		onTrackStateChange(trackId, { label: label && label !== TRACK_LABELS[trackId] ? label : undefined });
	}

	function beginTrackRename(trackId: VideoLocalizationTrackId) {
		if (trackStates[trackId].locked) return;
		editingTrackId = trackId;
		editingTrackValue = trackName(trackId);
		requestAnimationFrame(() => {
			const input = document.querySelector<HTMLInputElement>(`[data-track-name="${trackId}"]`);
			input?.focus();
			input?.select();
		});
	}

	function finishTrackRename(trackId: VideoLocalizationTrackId, save = true) {
		if (save) renameTrack(trackId, editingTrackValue);
		editingTrackId = null;
	}

	function handleTrackNameKeydown(event: KeyboardEvent, trackId: VideoLocalizationTrackId) {
		if (event.key === 'Enter') {
			event.preventDefault();
			finishTrackRename(trackId);
		} else if (event.key === 'Escape') {
			event.preventDefault();
			finishTrackRename(trackId, false);
		}
	}

	function volumeToDb(volume: number | null | undefined, precision = 1) {
		const safeVolume = Math.max(0, Number.isFinite(volume) ? Number(volume) : 1);
		if (safeVolume <= 0.001) return -60;
		const factor = Math.pow(10, precision);
		return Math.round(20 * Math.log10(safeVolume) * factor) / factor;
	}

	function volumeDbLabel(trackId: VideoLocalizationTrackId) {
		return `${volumeToDb(trackStates[trackId].volume).toFixed(1)} dB`;
	}

	function dbToVolume(db: number) {
		if (!Number.isFinite(db) || db <= -60) return 0;
		return Math.max(0, Math.min(2, Math.pow(10, Math.min(6.02, db) / 20)));
	}

	function updateTrackDb(trackId: VideoLocalizationTrackId, db: number) {
		onTrackStateChange(trackId, { volume: dbToVolume(Math.max(-60, Math.min(6.02, db))) });
	}

	function beginVolumeEdit(trackId: VideoLocalizationTrackId) {
		openVolumeTrack = trackId;
		requestAnimationFrame(() => {
			const input = document.querySelector<HTMLInputElement>(`[data-volume-input="${trackId}"]`);
			input?.focus();
			input?.select();
		});
	}

	function finishVolumeEdit(trackId: VideoLocalizationTrackId) {
		if (openVolumeTrack === trackId) openVolumeTrack = null;
	}

	function handleVolumeInputKeydown(event: KeyboardEvent, trackId: VideoLocalizationTrackId) {
		if (event.key === 'Enter') {
			event.preventDefault();
			(event.currentTarget as HTMLInputElement).blur();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			finishVolumeEdit(trackId);
		}
	}

	function resetTrackDb(event: MouseEvent, trackId: VideoLocalizationTrackId) {
		event.preventDefault();
		event.stopPropagation();
		if (volumeClickTimer) clearTimeout(volumeClickTimer);
		volumeClickTimer = null;
		openVolumeTrack = null;
		updateTrackDb(trackId, 0);
	}

	function beginVolumeScrub(event: PointerEvent, trackId: VideoLocalizationTrackId) {
		event.preventDefault();
		event.stopPropagation();
		if (volumeClickTimer) clearTimeout(volumeClickTimer);
		volumeClickTimer = null;
		const startX = event.clientX;
		const startDb = volumeToDb(trackStates[trackId].volume, 2);
		let moved = false;
		const move = (moveEvent: PointerEvent) => {
			const delta = moveEvent.clientX - startX;
			if (Math.abs(delta) >= 2) moved = true;
			if (!moved) return;
			updateTrackDb(trackId, Math.round(Math.max(-60, Math.min(6, startDb + delta * 0.1)) * 10) / 10);
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
			if (!moved) volumeClickTimer = setTimeout(() => beginVolumeEdit(trackId), 220);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
	}

	function handleCueTextWheel(event: WheelEvent) {
		const target = event.currentTarget as HTMLElement;
		if (target.scrollWidth <= target.clientWidth) return;
		event.preventDefault();
		event.stopPropagation();
		target.scrollLeft += Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
	}

	function stepTimelineZoom(delta: number) {
		const factor = delta > 0 ? 1.35 : 1 / 1.35;
		onTimelineZoomChange(Math.max(1, Math.min(1200, Math.round(timelineZoom * factor * 10) / 10)));
	}

	function zoomTimelineAtPointer(delta: number, clientX?: number) {
		suspendAutoFollow();
		if (!trackCanvasEl) {
			stepTimelineZoom(delta);
			return;
		}
		const rect = trackCanvasEl.getBoundingClientRect();
		const pointerX = clientX === undefined ? rect.width / 2 : Math.max(0, Math.min(rect.width, clientX - rect.left));
		const anchorRatio = (trackCanvasEl.scrollLeft + pointerX) / Math.max(1, trackCanvasEl.scrollWidth);
		stepTimelineZoom(delta);
		void tick().then(() => requestAnimationFrame(() => {
			if (!trackCanvasEl) return;
			trackCanvasEl.scrollLeft = Math.max(0, anchorRatio * trackCanvasEl.scrollWidth - pointerX);
			updateTimelineViewport(trackCanvasEl);
		}));
	}

	function handleTimelineKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		if (target?.closest('input,textarea,select,[contenteditable="true"]')) return;
		if (event.code === 'Space') {
			event.preventDefault();
			onTransportAction('play-pause');
		} else if (event.key === '+' || event.key === '=') {
			event.preventDefault();
			zoomTimelineAtPointer(1);
		} else if (event.key === '-' || event.key === '_') {
			event.preventDefault();
			zoomTimelineAtPointer(-1);
		} else if (event.key.toLowerCase() === 'i') {
			event.preventDefault();
			setSelectionPoint('start');
		} else if (event.key.toLowerCase() === 'o') {
			event.preventDefault();
			setSelectionPoint('end');
		} else if (event.key === 'Escape') {
			openVolumeTrack = null;
			timelineContextMenu = null;
		}
	}

	function trackRuntimeBusy(trackId: VideoLocalizationTrackId, itemId?: string) {
		return activityTasks.some((task) => activityTaskAffectsTrack(task, trackId, itemId));
	}

	function trackInteractionLocked(trackId: VideoLocalizationTrackId, itemId?: string) {
		return trackStates[trackId].locked || trackRuntimeBusy(trackId, itemId);
	}

	function setSelectionPoint(edge: 'start' | 'end', requestedTimeMs = currentTimeMs) {
		const timeMs = Math.max(0, Math.min(timelineDurationMs, requestedTimeMs));
		if (edge === 'start') {
			rangeStartMs = Math.min(timeMs, (rangeEndMs ?? timelineDurationMs) - MIN_RANGE_DURATION_MS);
			if (rangeEndMs !== null && rangeEndMs <= rangeStartMs) rangeEndMs = null;
			return;
		}
		rangeEndMs = Math.max(timeMs, (rangeStartMs ?? 0) + MIN_RANGE_DURATION_MS);
	}

	function clearSelection() {
		rangeCreateState = null;
		selectionDrag = null;
		rangeStartMs = null;
		rangeEndMs = null;
	}

	function setRangeFromSelectedCue() {
		if (!selectedCue || selectedCue.start_ms === null || selectedCue.end_ms === null) return;
		rangeStartMs = selectedCue.start_ms;
		rangeEndMs = Math.max(selectedCue.start_ms + MIN_RANGE_DURATION_MS, selectedCue.end_ms);
	}

	function handleRangeAction(action: (startMs: number, endMs: number) => void) {
		if (!hasRangeSelection) return;
		action(Math.min(rangeStartValue, rangeEndValue), Math.max(rangeStartValue, rangeEndValue));
	}

	function estimateMasterLevel() {
		const level = Math.max(levelForTrack('original'), levelForTrack('vocals'), levelForTrack('background'), levelForTrack('dub'));
		if (level <= 0.0001) return 0;
		const db = 20 * Math.log10(level);
		return Math.max(0, Math.min(1.08, (db + 60) / 66));
	}

	function levelForTrack(trackId: VideoLocalizationTrackId) {
		if (!trackAudible(trackId)) return 0;
		if (clipsForTrack(trackId).length) return levelForClipTrack(trackId);
		return 0;
	}

	function levelForClipTrack(trackId: VideoLocalizationTrackId) {
		const clip = clipsForTrack(trackId).find((item) => currentTimeMs >= (item.start_ms ?? 0) && currentTimeMs <= (item.end_ms ?? 0));
		if (!clip) return 0;
		const analysis = dubWaveforms[clip.clip_id];
		if (!analysis?.bars.length) return 0;
		const live = timelineClipTime(clip);
		const sourceStart = live.source_start_ms ?? 0;
		const localMs = sourceStart + Math.max(0, currentTimeMs - live.start_ms);
		const durationMs = Math.max(1, analysis.durationSeconds * 1000);
		const index = Math.max(0, Math.min(analysis.bars.length - 1, Math.round((localMs / durationMs) * (analysis.bars.length - 1))));
		let peak = 0;
		for (let cursor = Math.max(0, index - 2); cursor <= Math.min(analysis.bars.length - 1, index + 2); cursor += 1) peak = Math.max(peak, analysis.bars[cursor] ?? 0);
		return peak * (trackStates[trackId].volume ?? 1);
	}

	function clipsForTrack(trackId: VideoLocalizationTrackId) {
		return draft?.timeline_clips.filter((clip) => clip.track_id === trackId && clip.audio_path) ?? [];
	}

	function visibleClipsForTrack(trackId: VideoLocalizationTrackId) {
		return clipsForTrack(trackId).filter((clip) => {
			const time = timelineClipTime(clip);
			return clipDragState?.clipId === clip.clip_id || timeRangeIntersectsViewport(time.start_ms, time.end_ms, renderViewport);
		});
	}

	function clipLabel(clip: VideoLocalizationTimelineClip, trackId: VideoLocalizationTrackId) {
		if (trackId === 'dub') return clip.cue_id || clip.clip_id;
		return trackName(trackId);
	}

	function clipTone(trackId: VideoLocalizationTrackId): 'source' | 'vocals' | 'music' | 'dub' {
		if (trackId === 'original') return 'source';
		if (trackId === 'vocals') return 'vocals';
		if (trackId === 'background') return 'music';
		return 'dub';
	}

	function trackMeterPercent(trackId: VideoLocalizationTrackId) {
		if (!isPlaying || trackStates[trackId]?.muted) return 0;
		const level = levelForTrack(trackId);
		if (level <= 0.0001) return 0;
		return Math.max(0, Math.min(100, ((20 * Math.log10(level) + 60) / 60) * 100));
	}

	function trackAudible(trackId: VideoLocalizationTrackId) {
		if (!trackHasMedia(trackId)) return false;
		if (trackStates[trackId]?.muted) return false;
		const soloTracks = (['original', 'vocals', 'background', 'dub'] as VideoLocalizationTrackId[]).filter((candidate) => trackStates[candidate]?.solo);
		return !soloTracks.length || soloTracks.includes(trackId);
	}

	function trackHasMedia(trackId: VideoLocalizationTrackId) {
		if (clipsForTrack(trackId).length) return true;
		if (trackId === 'original') return hasSourceAudio;
		if (trackId === 'vocals') return Boolean(draft?.stems.vocals_clean_path);
		if (trackId === 'background') return Boolean(draft?.stems.background_path);
		return false;
	}

	function updateDubWaveform(clipId: string, bars: number[], durationSeconds: number) {
		dubWaveforms = { ...dubWaveforms, [clipId]: { bars, durationSeconds } };
	}

	function seekFromPointer(event: PointerEvent | MouseEvent) {
		scheduleTimelineSeek(timeFromPointer(event));
	}

	function scheduleTimelineSeek(timeMs: number, flush = false) {
		pendingSeekMs = Math.max(0, Math.min(timelineDurationMs, Math.round(timeMs)));
		if (flush) {
			if (seekAnimationFrame) cancelAnimationFrame(seekAnimationFrame);
			seekAnimationFrame = 0;
			onSeekTimeline(pendingSeekMs);
			return;
		}
		if (seekAnimationFrame) return;
		seekAnimationFrame = requestAnimationFrame(() => {
			seekAnimationFrame = 0;
			onSeekTimeline(pendingSeekMs);
		});
	}

	function handleTimelinePointerDown(event: PointerEvent) {
		const target = event.target as HTMLElement;
		const overTrack = Boolean(target.closest('[data-track-row]'));
		const overTimeline = Boolean(target.closest('.timeline-ruler') || overTrack);
		const interactive = isTimelineInteractiveTarget(target);
		const intent = timelinePointerIntent({
			button: event.button,
			overTimeline,
			overTrack,
			interactive
		});
		if (intent === 'range-create') {
			event.preventDefault();
			suspendAutoFollow();
			timelineSeekDrag = false;
			const startMs = timeFromPointer(event);
			rangeCreateState = { startX: event.clientX, startMs, moved: false };
			(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
			return;
		}
		if (intent === 'pan') {
			suspendAutoFollow();
			event.preventDefault();
			timelinePanState = { startX: event.clientX, scrollLeft: trackCanvasEl?.scrollLeft ?? 0 };
			(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
			return;
		}
		if (intent !== 'seek') return;
		clearTimelineItemSelection();
		event.preventDefault();
		suspendAutoFollow();
		timelineSeekDrag = true;
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		seekFromPointer(event);
	}

	function handleTimelineDoubleClick(event: MouseEvent) {
		if (isTimelineInteractiveTarget(event.target as HTMLElement)) return;
		event.preventDefault();
		clearSelection();
	}

	function isTimelineInteractiveTarget(target: HTMLElement) {
		return Boolean(target.closest('button,input,.cue-chip,.clip-label,.clip-handle,.range-handle,.track-resize-handle,.track-label-width-handle'));
	}

	function handleTimelinePointerMove(event: PointerEvent) {
		if (timelinePanState && trackCanvasEl) {
			trackCanvasEl.scrollLeft = timelinePanState.scrollLeft - (event.clientX - timelinePanState.startX);
			updateTimelineViewport(trackCanvasEl);
			return;
		}
		moveCueDrag(event);
		moveClipDrag(event);
		if (rangeCreateState) moveRangeCreation(event);
		if (timelineSeekDrag) seekFromPointer(event);
		if (selectionDrag) moveSelectionHandle(event);
		if (!timelinePanState && !dragState && !clipDragState && !rangeCreateState && !timelineSeekDrag && !selectionDrag) {
			updateHoverScrub(event);
		}
	}

	function updateHoverScrub(event: PointerEvent) {
		if (!hoverScrubEnabled || isPlaying || !(event.target as HTMLElement).closest('[data-track-row],.timeline-ruler')) {
			endHoverScrub();
			return;
		}
		hoverTimeMs = timeFromPointer(event);
		if (hoverScrubFrame) cancelAnimationFrame(hoverScrubFrame);
		hoverScrubFrame = requestAnimationFrame(() => {
			hoverScrubFrame = 0;
			if (hoverTimeMs !== null) onHoverScrub?.(hoverTimeMs);
		});
	}

	function endHoverScrub() {
		if (hoverScrubFrame) cancelAnimationFrame(hoverScrubFrame);
		hoverScrubFrame = 0;
		if (hoverTimeMs === null) return;
		hoverTimeMs = null;
		onHoverScrubEnd?.();
	}

	function clearTimelineItemSelection() {
		if (!selectedTimelineItem && !selectedCueId) return;
		selectedTimelineItem = null;
		onClearCueSelection?.();
	}

	function selectSubtitleTimelineItem(trackKind: SubtitleTrackKind, itemId: string, preserveRange = false) {
		const trackId = trackKind === 'asr' ? 'subtitles' : 'localizedSubtitles';
		selectedTimelineItem = { kind: 'subtitle', trackId, itemId };
		if (trackKind === 'asr') {
			preserveRangeOnCueSelection = preserveRange && itemId !== selectedCueId;
			onSelectCue(itemId);
			if (preserveRangeOnCueSelection) queueMicrotask(() => (preserveRangeOnCueSelection = false));
		}
	}

	function selectAudioTimelineItem(trackId: VideoLocalizationTrackId, itemId: string) {
		selectedTimelineItem = { kind: 'audio', trackId, itemId };
	}

	async function deleteSubtitleTimelineItem(track: SubtitleTrackKind, itemId: string) {
		await onDeleteSubtitleItem(track, itemId);
		if (selectedTimelineItem?.kind === 'subtitle' && selectedTimelineItem.itemId === itemId) selectedTimelineItem = null;
	}

	async function deleteAudioTimelineItem(itemId: string) {
		await onDeleteTimelineClip(itemId);
		if (selectedTimelineItem?.kind === 'audio' && selectedTimelineItem.itemId === itemId) selectedTimelineItem = null;
	}

	function endTimelinePointerWork() {
		endCueDrag();
		endClipDrag();
		if (rangeCreateState && !rangeCreateState.moved) {
			clearTimelineItemSelection();
			scheduleTimelineSeek(rangeCreateState.startMs, true);
		}
		if (timelineSeekDrag) scheduleTimelineSeek(pendingSeekMs, true);
		rangeCreateState = null;
		timelineSeekDrag = false;
		timelinePanState = null;
		selectionDrag = null;
	}

	function timeFromPointer(event: PointerEvent | MouseEvent) {
		if (!timelineContentEl) return 0;
		const rect = timelineContentEl.getBoundingClientRect();
		const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
		return Math.round(ratio * timelineDurationMs);
	}

	function moveRangeCreation(event: PointerEvent) {
		if (!rangeCreateState) return;
		const currentMs = timeFromPointer(event);
		const moved = rangeCreateState.moved || Math.abs(event.clientX - rangeCreateState.startX) >= 4;
		if (!moved) return;
		rangeCreateState = { ...rangeCreateState, moved: true };
		if (currentMs >= rangeCreateState.startMs) {
			rangeStartMs = rangeCreateState.startMs;
			rangeEndMs = Math.min(timelineDurationMs, Math.max(rangeCreateState.startMs + MIN_RANGE_DURATION_MS, currentMs));
		} else {
			rangeStartMs = Math.max(0, Math.min(rangeCreateState.startMs - MIN_RANGE_DURATION_MS, currentMs));
			rangeEndMs = rangeCreateState.startMs;
		}
	}

	function beginSelectionDrag(event: PointerEvent, edge: 'start' | 'end') {
		event.preventDefault();
		event.stopPropagation();
		suspendAutoFollow();
		selectionDrag = edge;
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		moveSelectionHandle(event);
	}

	function moveSelectionHandle(event: PointerEvent) {
		if (!timelineContentEl || !selectionDrag) return;
		const rect = timelineContentEl.getBoundingClientRect();
		const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
		const timeMs = Math.round(ratio * timelineDurationMs);
		const currentStart = rangeStartMs ?? 0;
		const currentEnd = rangeEndMs ?? Math.min(timelineDurationMs, currentStart + 1800);
		if (selectionDrag === 'start') rangeStartMs = Math.min(timeMs, currentEnd - MIN_RANGE_DURATION_MS);
		else rangeEndMs = Math.max(timeMs, currentStart + MIN_RANGE_DURATION_MS);
	}

	function handleTrackWheel(event: WheelEvent) {
		if (!trackCanvasEl) return;
		event.preventDefault();
		suspendAutoFollow();
		if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
			trackCanvasEl.scrollLeft += Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
			updateTimelineViewport(trackCanvasEl);
			return;
		}
		if (Math.abs(event.deltaY) < 0.5) return;
		zoomTimelineAtPointer(event.deltaY < 0 ? 1 : -1, event.clientX);
		updateTimelineViewport(trackCanvasEl);
	}

	function closeFloatingControls(event: PointerEvent) {
		if (!(event.target as HTMLElement | null)?.closest('.volume-control')) openVolumeTrack = null;
		if (!(event.target as HTMLElement | null)?.closest('.context-menu')) timelineContextMenu = null;
	}

	function openTimelineContextMenu(event: MouseEvent) {
		const targetElement = event.target as HTMLElement;
		const row = targetElement.closest<HTMLElement>('[data-track-row][data-track-id]');
		if (!row) return;
		event.preventDefault();
		event.stopPropagation();
		const trackId = row.dataset.trackId as VideoLocalizationTrackId;
		const timeMs = timeFromPointer(event);
		let target: TimelineContextMenuTarget;
		if (trackId === 'subtitles' || trackId === 'localizedSubtitles') {
			const subtitleTrack: SubtitleTrackKind = trackId === 'subtitles' ? 'asr' : 'localized';
			const item = targetElement.closest<HTMLElement>('[data-subtitle-item-id]');
			target = item
				? { kind: 'subtitle-clip', trackId, subtitleTrack, itemId: item.dataset.subtitleItemId || '', timeMs }
				: { kind: 'track', hit: 'empty', trackId, subtitleTrack, timeMs };
			if (item?.dataset.subtitleItemId) selectSubtitleTimelineItem(subtitleTrack, item.dataset.subtitleItemId, true);
		} else {
			const clip = targetElement.closest<HTMLElement>('[data-audio-clip-id]');
			target = clip
				? { kind: 'audio-clip', trackId, itemId: clip.dataset.audioClipId || '', timeMs }
				: { kind: 'track', hit: 'empty', trackId, timeMs };
			if (clip?.dataset.audioClipId) selectAudioTimelineItem(trackId, clip.dataset.audioClipId);
		}
		timelineContextMenu = {
			x: event.clientX,
			y: event.clientY,
			target
		};
		openVolumeTrack = null;
	}

	function beginLabelColumnResize(event: PointerEvent) {
		event.preventDefault();
		const startX = event.clientX;
		const startWidth = labelColumnWidth;
		const move = (moveEvent: PointerEvent) => {
			labelColumnWidth = Math.max(160, Math.min(380, startWidth + moveEvent.clientX - startX));
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
	}

	function beginTrackHeightResize(event: PointerEvent, trackId: VideoLocalizationTrackId) {
		event.preventDefault();
		event.stopPropagation();
		const startY = event.clientY;
		const startHeight = trackHeights[trackId] ?? 58;
		const minHeight = trackId === 'subtitles' || trackId === 'localizedSubtitles' ? 34 : 44;
		const previousCursor = document.body.style.cursor;
		const previousUserSelect = document.body.style.userSelect;
		document.body.style.cursor = 'ns-resize';
		document.body.style.userSelect = 'none';
		const move = (moveEvent: PointerEvent) => {
			trackHeights = { ...trackHeights, [trackId]: Math.max(minHeight, Math.min(168, startHeight + moveEvent.clientY - startY)) };
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
			window.removeEventListener('pointercancel', stop);
			document.body.style.cursor = previousCursor;
			document.body.style.userSelect = previousUserSelect;
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
		window.addEventListener('pointercancel', stop, { once: true });
	}

	function resetTrackHeight(event: MouseEvent, trackId: VideoLocalizationTrackId) {
		event.preventDefault();
		event.stopPropagation();
		trackHeights = { ...trackHeights, [trackId]: DEFAULT_TRACK_HEIGHTS[trackId] };
	}

	function cueLiveTime(cue: SubtitleTimelineItem, trackKind: SubtitleTrackKind) {
		const itemId = subtitleItemId(cue);
		const live = liveCueTimes[subtitleLiveKey(trackKind, itemId)];
		const fallbackStart = cue.start_ms ?? 0;
		const fallbackEnd = cue.end_ms ?? fallbackStart + MIN_SUBTITLE_DURATION_MS;
		return live ?? { start_ms: fallbackStart, end_ms: Math.max(fallbackStart + MIN_SUBTITLE_DURATION_MS, fallbackEnd) };
	}

	function startCueDrag(event: PointerEvent, cue: SubtitleTimelineItem, mode: DragMode, trackKind: SubtitleTrackKind) {
		if (event.button !== 0) return;
		const trackId = trackKind === 'asr' ? 'subtitles' : 'localizedSubtitles';
		const itemId = subtitleItemId(cue);
		if (trackInteractionLocked(trackId, itemId)) return;
		const time = cueLiveTime(cue, trackKind);
		if (time.end_ms <= time.start_ms) return;
		const trackCues = trackKind === 'asr'
			? (draft?.cues ?? [])
			: (draft?.localized_subtitles ?? []).map((item) => ({ cue_id: item.subtitle_id, start_ms: item.start_ms, end_ms: item.end_ms }));
		const bounds = subtitleCueDragBounds(trackCues, itemId, subtitleTimelineLimitMs);
		event.preventDefault();
		event.stopPropagation();
		suspendAutoFollow();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		if (trackKind === 'asr' && !isLocalizedSubtitle(cue)) onSelectCue(cue.cue_id);
		dragState = {
			itemId,
			trackKind,
			mode,
			startX: event.clientX,
			startMs: time.start_ms,
			endMs: time.end_ms,
			durationMs: time.end_ms - time.start_ms,
			minStartMs: bounds.minStartMs,
			maxEndMs: bounds.maxEndMs
		};
	}

	function moveCueDrag(event: PointerEvent) {
		if (!dragState || !timelineContentEl) return;
		const rect = timelineContentEl.getBoundingClientRect();
		const deltaMs = ((event.clientX - dragState.startX) / Math.max(1, rect.width)) * timelineDurationMs;
		const minDurationMs = MIN_SUBTITLE_DURATION_MS;
		let nextStart = dragState.startMs;
		let nextEnd = dragState.endMs;
		if (dragState.mode === 'move') {
			const duration = Math.max(minDurationMs, dragState.durationMs);
			nextStart = clampMs(dragState.startMs + deltaMs, dragState.minStartMs, Math.max(dragState.minStartMs, dragState.maxEndMs - duration));
			nextEnd = nextStart + duration;
		} else if (dragState.mode === 'trim-start') {
			nextStart = clampMs(dragState.startMs + deltaMs, dragState.minStartMs, dragState.endMs - minDurationMs);
		} else {
			nextEnd = clampMs(dragState.endMs + deltaMs, dragState.startMs + minDurationMs, dragState.maxEndMs);
		}
		liveCueTimes = { ...liveCueTimes, [subtitleLiveKey(dragState.trackKind, dragState.itemId)]: { start_ms: Math.round(nextStart), end_ms: Math.round(nextEnd) } };
	}

	function endCueDrag() {
		if (!dragState) return;
		const live = liveCueTimes[subtitleLiveKey(dragState.trackKind, dragState.itemId)];
		if (live) {
			if (dragState.trackKind === 'asr') onUpdateCueTime(dragState.itemId, live.start_ms, live.end_ms);
			else onUpdateLocalizedSubtitleTime(dragState.itemId, live.start_ms, live.end_ms);
		}
		dragState = null;
	}

	function startClipDrag(event: PointerEvent, clip: VideoLocalizationTimelineClip, mode: DragMode) {
		if (event.button !== 0) return;
		const trackId = clip.track_id as VideoLocalizationTrackId;
		if (trackInteractionLocked(trackId, clip.clip_id)) return;
		const time = timelineClipTime(clip);
		event.preventDefault();
		event.stopPropagation();
		suspendAutoFollow();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		clipDragState = {
			clipId: clip.clip_id,
			mode,
			startX: event.clientX,
			startMs: time.start_ms,
			endMs: time.end_ms,
			durationMs: time.end_ms - time.start_ms,
			sourceStartMs: time.source_start_ms ?? 0,
			sourceEndMs: time.source_end_ms ?? null
		};
	}

	function moveClipDrag(event: PointerEvent) {
		if (!clipDragState || !timelineContentEl) return;
		const rect = timelineContentEl.getBoundingClientRect();
		const deltaMs = ((event.clientX - clipDragState.startX) / Math.max(1, rect.width)) * timelineDurationMs;
		const minDurationMs = 300;
		let nextStart = clipDragState.startMs;
		let nextEnd = clipDragState.endMs;
		let nextSourceStart = clipDragState.sourceStartMs;
		let nextSourceEnd = clipDragState.sourceEndMs ?? clipDragState.sourceStartMs + Math.max(minDurationMs, clipDragState.durationMs);
		if (clipDragState.mode === 'move') {
			const duration = Math.max(minDurationMs, clipDragState.durationMs);
			nextStart = clampMs(clipDragState.startMs + deltaMs, 0, Math.max(0, timelineDurationMs - duration));
			nextEnd = nextStart + duration;
		} else if (clipDragState.mode === 'trim-start') {
			nextStart = clampMs(clipDragState.startMs + deltaMs, 0, clipDragState.endMs - minDurationMs);
			nextSourceStart = Math.max(0, clipDragState.sourceStartMs + (nextStart - clipDragState.startMs));
		} else {
			nextEnd = clampMs(clipDragState.endMs + deltaMs, clipDragState.startMs + minDurationMs, timelineDurationMs);
			nextSourceEnd = Math.max(nextSourceStart + minDurationMs, clipDragState.sourceStartMs + (nextEnd - clipDragState.startMs));
		}
		liveClipTimes = {
			...liveClipTimes,
			[clipDragState.clipId]: {
				start_ms: Math.round(nextStart),
				end_ms: Math.round(nextEnd),
				source_start_ms: Math.round(nextSourceStart),
				source_end_ms: Math.round(nextSourceEnd)
			}
		};
	}

	function endClipDrag() {
		if (!clipDragState) return;
		const live = liveClipTimes[clipDragState.clipId];
		if (live) onUpdateTimelineClip(clipDragState.clipId, live.start_ms, live.end_ms, live.source_start_ms ?? 0, live.source_end_ms ?? null);
		clipDragState = null;
	}

	function clampMs(value: number, min: number, max: number) {
		if (!Number.isFinite(value)) return min;
		return Math.max(min, Math.min(max, value));
	}

	function updateTimelineViewport(element = trackCanvasEl) {
		if (!element) return;
		timelineScrollLeft = element.scrollLeft;
		timelineViewportWidth = element.clientWidth;
	}

	function suspendAutoFollow() {
		autoFollowSuspendedUntil = Date.now() + 3000;
	}

	function handleTimelineScroll(element: HTMLDivElement) {
		updateTimelineViewport(element);
		timelineContextMenu = null;
		if (!programmaticTimelineScroll) suspendAutoFollow();
	}

	function scrollTimelineTo(left: number) {
		if (!trackCanvasEl) return;
		programmaticTimelineScroll = true;
		trackCanvasEl.scrollLeft = Math.max(0, Math.min(left, trackCanvasEl.scrollWidth - trackCanvasEl.clientWidth));
		updateTimelineViewport(trackCanvasEl);
		requestAnimationFrame(() => {
			programmaticTimelineScroll = false;
		});
	}

	function followPlaybackPage(_force = false) {
		if (!trackCanvasEl || !isPlaying || Date.now() < autoFollowSuspendedUntil) return;
		const viewportWidth = trackCanvasEl.clientWidth;
		if (viewportWidth <= 0 || trackCanvasEl.scrollWidth <= viewportWidth) return;
		const playheadPx = (Math.max(0, Math.min(timelineDurationMs, currentTimeMs)) / timelineDurationMs) * trackCanvasEl.scrollWidth;
		const visibleLeft = trackCanvasEl.scrollLeft;
		const visibleRight = visibleLeft + viewportWidth;
		const outside = playheadPx < visibleLeft || playheadPx >= visibleRight - 2;
		if (!outside) return;
		const pageLeft = Math.floor(playheadPx / viewportWidth) * viewportWidth;
		scrollTimelineTo(pageLeft);
	}

	$effect(() => {
		draft?.updated_at;
		draft?.cues.length;
		draft?.localized_subtitles.length;
		if (!dragState) liveCueTimes = {};
		draft?.timeline_clips.length;
		if (!clipDragState) liveClipTimes = {};
	});

	$effect(() => {
		selectedCueId;
		if (preserveRangeOnCueSelection) {
			preserveRangeOnCueSelection = false;
			return;
		}
		rangeStartMs = null;
		rangeEndMs = null;
	});

	$effect(() => {
		timelineZoom;
		requestAnimationFrame(() => updateTimelineViewport());
	});

	$effect(() => {
		currentTimeMs;
		const startedPlaying = isPlaying && !wasPlaying;
		wasPlaying = isPlaying;
		if (isPlaying) followPlaybackPage(startedPlaying);
	});

	$effect(() => {
		projectId;
		if (!hoverScrubEnabled || isPlaying) endHoverScrub();
	});
</script>

<svelte:window onkeydown={handleTimelineKeydown} onpointerdown={closeFloatingControls} />

<section class="cut-timeline" aria-label="视频本土化时间线">
	<div class="timeline-toolbar">
		<div class="transport">
			<button class="icon-btn" type="button" aria-label="跳到开始" data-tooltip="跳到开始：将播放指针移回时间线起点。" onclick={() => onTransportAction('start')}><SkipBack size={15} /></button>
			<button class="icon-btn" type="button" aria-label={isPlaying ? '暂停' : '播放'} data-tooltip={isPlaying ? '暂停｜停止视频和所有启用轨道的播放。快捷键：Space' : '播放｜从当前指针同步播放视频和启用的轨道。快捷键：Space'} onclick={() => onTransportAction('play-pause')}>
				{#if isPlaying}<Pause size={15} />{:else}<Play size={15} />{/if}
			</button>
			<button class="icon-btn" type="button" aria-label="跳到下一段" data-tooltip="下一段：跳到后一个字幕片段的入点。" onclick={() => onTransportAction('next')}><SkipForward size={15} /></button>
			<button class="icon-btn" type="button" aria-label="撤销配音片段编辑" data-tooltip="撤销：恢复上一次配音片段移动或裁切。" onclick={onUndoTimelineClip} disabled={!canUndoTimeline}><Undo2 size={15} /></button>
				<button class="icon-btn" type="button" aria-label="重做配音片段编辑" data-tooltip="重做：重新应用刚撤销的配音片段编辑。" onclick={onRedoTimelineClip} disabled={!canRedoTimeline}><Redo2 size={15} /></button>
			</div>
			<ActivityNotice kind={noticeSummary ? noticeKind : 'idle'} summary={noticeSummary} detail={noticeDetail} tasks={activityTasks} resetKey={projectId} {onOpenTaskCenter} />
			<div class="timeline-actions">
			<button
				class="tool-btn icon-tool hover-scrub-toggle"
				class:active={hoverScrubEnabled}
				type="button"
				aria-label={hoverScrubEnabled ? '关闭鼠标预听' : '启用鼠标预听'}
				aria-pressed={hoverScrubEnabled}
				data-tooltip={hoverScrubEnabled ? '鼠标预听已启用｜在时间线移动鼠标即可预览画面、声音和字幕；播放时不会抢占播放头。' : '启用鼠标预听｜无需点击，在时间线上移动鼠标即可快速试听。'}
				onclick={() => onHoverScrubChange?.(!hoverScrubEnabled)}
			><MousePointer2 size={13} /></button>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<div class="edit-tools" aria-label="字幕片段编辑">
				<button class="tool-btn icon-tool" type="button" onclick={onSplitCue} disabled={!canSplitSelectedCue} aria-label="拆分字幕片段" data-tooltip="拆分字幕片段：按当前指针或中点切开当前字幕。"><Scissors size={13} /></button>
				<button class="tool-btn icon-tool" type="button" onclick={onMergeCue} disabled={!canMergeSelectedCue} aria-label="合并下一字幕片段" data-tooltip="合并下一字幕片段：把当前字幕和后一段合并。">⇄</button>
				<button class="tool-btn icon-tool danger" type="button" onclick={onDeleteCue} disabled={!canEditSelectedCue} aria-label="删除当前字幕片段" data-tooltip="删除当前字幕片段：从时间线移除当前字幕。"><Trash2 size={13} /></button>
			</div>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<div class="edit-tools" aria-label="选区工作流">
				<button class="tool-btn icon-tool" type="button" onclick={setRangeFromSelectedCue} disabled={!canEditSelectedCue} aria-label="用当前字幕设置选区" data-tooltip="用当前字幕设置选区：把当前字幕的入点和出点作为样音范围。"><Captions size={13} /></button>
				<button class="tool-btn icon-tool" type="button" onclick={() => handleRangeAction(onSaveSelectionAsVoice)} disabled={!hasRangeSelection} aria-label="保存选区为音色" data-tooltip={hasRangeSelection ? '保存选区为音色：把当前时间范围保存为项目音色样音。' : '先设置一个时间范围，才能保存样音。'}><Save size={13} /></button>
				<button class="primary-tool icon-tool" type="button" onclick={() => handleRangeAction(onGenerateToSelection)} disabled={!hasRangeSelection} aria-label="生成到选区" data-tooltip={hasRangeSelection ? '生成到选区：把生成语音放入当前时间范围。' : '先设置一个时间范围，才能生成到选区。'}><Wand2 size={13} /></button>
			</div>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<div class="zoom-stepper" aria-label="时间线缩放">
				<button type="button" onclick={() => stepTimelineZoom(-1)} disabled={timelineZoom <= 1} aria-label="缩小时间线" data-tooltip="缩小时间线：显示更长时间范围。"><ZoomOut size={13} /></button>
				<span>{formatTimelineZoom(timelineZoom)}x</span>
				<button type="button" onclick={() => stepTimelineZoom(1)} disabled={timelineZoom >= 1200} aria-label="放大时间线" data-tooltip="放大时间线：更精细地查看波形和片段。"><ZoomIn size={13} /></button>
			</div>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<div class="range-marker-tools" aria-label="设置出入点">
				<button class:active={rangeStartMs !== null} type="button" aria-label="设置入点" data-tooltip="设置入点：把当前播放指针设为选区起点。快捷键 I" onclick={() => setSelectionPoint('start')}><ChevronsLeft size={14} /></button>
				<button class:active={rangeEndMs !== null} type="button" aria-label="设置出点" data-tooltip="设置出点：把当前播放指针设为选区终点。快捷键 O" onclick={() => setSelectionPoint('end')}><ChevronsRight size={14} /></button>
			</div>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<button class="tool-btn icon-tool" type="button" onclick={onExtractAudio} disabled={!hasRecoverableVideo || hasSourceAudio || extractingAudio} aria-label="抽取原音轨" data-tooltip="抽取原音轨：从视频中生成可编辑的原始音频轨。">
				<FileAudio size={13} />
			</button>
		</div>
	</div>

	<div class="tracks" style={`grid-template-columns:${labelColumnWidth}px minmax(0, 1fr);--label-column-width:${labelColumnWidth}px`}>
		<div class="track-labels">
			<div class="track-meter-head level-meter" class:active={isPlaying} aria-label="当前时间与音频电平">
				<div class="meter-track" aria-hidden="true">
					<i style={`width:${Math.min(100, masterLevel * 100)}%`}></i>
					<b style="left:18%">-48</b>
					<b style="left:55%">-24</b>
					<b style="left:73%">-12</b>
					<b style="left:82%">-6</b>
					<b style="left:91%">0</b>
				</div>
				<strong class="time-readout">{formatTimecode(currentTimeMs / 1000, 30)}</strong>
			</div>
				<div class="track-label subtitle track-subtitle track-asr-subtitle" class:track-locked={trackStates.subtitles.locked} class:track-processing={trackRuntimeBusy('subtitles')} style={subtitleTrackStyle('subtitles')}>
				<div>
					{#if editingTrackId === 'subtitles'}
						<input class="track-name-input" data-track-name="subtitles" aria-label="修改字幕轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('subtitles')} onkeydown={(event) => handleTrackNameKeydown(event, 'subtitles')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.subtitles.locked} data-tooltip="字幕轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('subtitles')}>{trackName('subtitles')}</button>
					{/if}
					<span>分离人声听写结果</span>
				</div>
					<div class="track-controls">
					<button class="track-toggle visibility-toggle" class:active={asrSubtitleVisible} type="button" aria-label={asrSubtitleVisible ? '隐藏 ASR 字幕' : '显示 ASR 字幕'} data-tooltip={asrSubtitleVisible ? '隐藏 ASR 字幕｜视频预览中不再显示这条字幕轨。' : '显示 ASR 字幕｜在视频预览中显示这条字幕轨。'} onclick={() => onToggleSubtitleSource('asr')}>{#if asrSubtitleVisible}<Eye size={13} />{:else}<EyeOff size={13} />{/if}</button>
					<button class="track-toggle lock-toggle" class:active={trackStates.subtitles.locked} type="button" aria-label={trackStates.subtitles.locked ? '解锁字幕轨' : '锁定字幕轨'} data-tooltip={trackStates.subtitles.locked ? '解锁字幕轨｜允许移动、裁切和删除字幕片段。' : '锁定字幕轨｜禁止修改字幕片段，但不影响显示和播放。'} onclick={() => toggleLocked('subtitles')}>{#if trackStates.subtitles.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
					</div>
					<button class="track-resize-handle" type="button" aria-label="调整 ASR 字幕轨高度" data-tooltip="调整轨道高度：上下拖动分界线，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'subtitles')} ondblclick={(event) => resetTrackHeight(event, 'subtitles')}></button>
				</div>
					<div class="track-label subtitle track-subtitle track-localized-subtitle" class:track-locked={trackStates.localizedSubtitles.locked} class:track-processing={trackRuntimeBusy('localizedSubtitles')} style={subtitleTrackStyle('localizedSubtitles')}>
				<div>
					{#if editingTrackId === 'localizedSubtitles'}
						<input class="track-name-input" data-track-name="localizedSubtitles" aria-label="修改本土化字幕轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('localizedSubtitles')} onkeydown={(event) => handleTrackNameKeydown(event, 'localizedSubtitles')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.localizedSubtitles.locked} data-tooltip="本土化字幕轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('localizedSubtitles')}>{trackName('localizedSubtitles')}</button>
					{/if}
					<span>外部 SRT 时间码</span>
				</div>
					<div class="track-controls">
					<button class="track-toggle visibility-toggle" class:active={localizedSubtitleVisible} type="button" aria-label={localizedSubtitleVisible ? '隐藏本土化字幕' : '显示本土化字幕'} data-tooltip={localizedSubtitleVisible ? '隐藏本土化字幕｜视频预览中不再显示这条字幕轨。' : '显示本土化字幕｜在视频预览中显示这条字幕轨。'} onclick={() => onToggleSubtitleSource('localized')}>{#if localizedSubtitleVisible}<Eye size={13} />{:else}<EyeOff size={13} />{/if}</button>
					<button class="track-toggle lock-toggle" class:active={trackStates.localizedSubtitles.locked} type="button" aria-label={trackStates.localizedSubtitles.locked ? '解锁本土化字幕轨' : '锁定本土化字幕轨'} data-tooltip={trackStates.localizedSubtitles.locked ? '解锁本土化字幕轨｜允许移动和裁切字幕片段。' : '锁定本土化字幕轨｜禁止修改字幕片段。'} onclick={() => toggleLocked('localizedSubtitles')}>{#if trackStates.localizedSubtitles.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
					</div>
					<button class="track-resize-handle" type="button" aria-label="调整本土化字幕轨高度" data-tooltip="调整轨道高度：上下拖动分界线，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'localizedSubtitles')} ondblclick={(event) => resetTrackHeight(event, 'localizedSubtitles')}></button>
				</div>
				<div class="track-label track-audio track-original" role="group" aria-label="原音轨" class:track-muted={trackStates.original.muted} class:track-locked={trackStates.original.locked} class:track-processing={trackRuntimeBusy('original')} class:drag-over={dragOverAudioTrackId === 'original' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'original' && dragOverAudioTrackPlacement === 'after'} style={audioTrackStyle('original')} ondragover={(event) => markAudioTrackDropTarget(event, 'original')} ondrop={(event) => dropAudioTrack(event, 'original')}>
				<i class="track-title-level" style={`width:${trackMeterPercent('original')}%`}></i>
				<div>
					{#if editingTrackId === 'original'}
						<input class="track-name-input" data-track-name="original" aria-label="修改原音轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('original')} onkeydown={(event) => handleTrackNameKeydown(event, 'original')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.original.locked} data-tooltip="原音轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('original')}>{trackName('original')}</button>
					{/if}
					<span>{originalWaveformUnavailable ? '源音频文件缺失' : hasSourceAudio ? '完整视频声音' : '导入后自动抽取'}</span>
				</div>
				<div class="track-controls">
					<button class="track-drag-handle" type="button" draggable="true" aria-label="拖动调整原音轨顺序" data-tooltip="调整轨道顺序｜拖动到其他音轨标题上方。" ondragstart={(event) => beginAudioTrackReorder(event, 'original')} ondragend={endAudioTrackReorder}><GripVertical size={13} /></button>
					<button class="track-toggle" class:active={trackStates.original.muted} type="button" data-tooltip="静音原音轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('original')}>M</button>
					<button class="track-toggle" class:active={trackStates.original.solo} type="button" data-tooltip="独奏原音轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('original')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'original'}
							<div class="volume-inline-editor" role="group" aria-label="原音轨音量" onpointerdown={(event) => event.stopPropagation()}>
								<input data-volume-input="original" aria-label="原音轨音量 dB" type="number" min="-60" max="6.02" step="0.01" value={volumeToDb(trackStates.original.volume, 2)} oninput={(event) => updateTrackDb('original', Number(event.currentTarget.value))} onblur={() => finishVolumeEdit('original')} onkeydown={(event) => handleVolumeInputKeydown(event, 'original')} />
								<span>dB</span>
							</div>
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整原音轨音量" data-tooltip="原音轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'original')} ondblclick={(event) => resetTrackDb(event, 'original')}>{volumeDbLabel('original')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.original.locked} type="button" aria-label={trackStates.original.locked ? '解锁原音轨' : '锁定原音轨'} data-tooltip={trackStates.original.locked ? '解锁原音轨｜允许移动、裁切和删除音频片段。' : '锁定原音轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('original')}>{#if trackStates.original.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整原音轨高度" data-tooltip="调整原音轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'original')} ondblclick={(event) => resetTrackHeight(event, 'original')}></button>
			</div>
				<div class="track-label track-audio track-vocals" role="group" aria-label="人声轨" class:track-muted={trackStates.vocals.muted} class:track-locked={trackStates.vocals.locked} class:track-processing={trackRuntimeBusy('vocals')} class:drag-over={dragOverAudioTrackId === 'vocals' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'vocals' && dragOverAudioTrackPlacement === 'after'} style={audioTrackStyle('vocals')} ondragover={(event) => markAudioTrackDropTarget(event, 'vocals')} ondrop={(event) => dropAudioTrack(event, 'vocals')}>
				<i class="track-title-level" style={`width:${trackMeterPercent('vocals')}%`}></i>
				<div>
					{#if editingTrackId === 'vocals'}
						<input class="track-name-input" data-track-name="vocals" aria-label="修改人声轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('vocals')} onkeydown={(event) => handleTrackNameKeydown(event, 'vocals')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.vocals.locked} data-tooltip="人声轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('vocals')}>{trackName('vocals')}</button>
					{/if}
					<span>{stemsReady ? '分离后人声' : '由原音轨生成'}</span>
				</div>
				<div class="track-controls">
					<button class="track-drag-handle" type="button" draggable="true" aria-label="拖动调整人声轨顺序" data-tooltip="调整轨道顺序｜拖动到其他音轨标题上方。" ondragstart={(event) => beginAudioTrackReorder(event, 'vocals')} ondragend={endAudioTrackReorder}><GripVertical size={13} /></button>
					<button class="track-toggle" class:active={trackStates.vocals.muted} type="button" data-tooltip="静音人声轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('vocals')}>M</button>
					<button class="track-toggle" class:active={trackStates.vocals.solo} type="button" data-tooltip="独奏人声轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('vocals')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'vocals'}
							<div class="volume-inline-editor" role="group" aria-label="人声轨音量" onpointerdown={(event) => event.stopPropagation()}>
								<input data-volume-input="vocals" aria-label="人声轨音量 dB" type="number" min="-60" max="6.02" step="0.01" value={volumeToDb(trackStates.vocals.volume, 2)} oninput={(event) => updateTrackDb('vocals', Number(event.currentTarget.value))} onblur={() => finishVolumeEdit('vocals')} onkeydown={(event) => handleVolumeInputKeydown(event, 'vocals')} />
								<span>dB</span>
							</div>
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整人声轨音量" data-tooltip="人声轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'vocals')} ondblclick={(event) => resetTrackDb(event, 'vocals')}>{volumeDbLabel('vocals')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.vocals.locked} type="button" aria-label={trackStates.vocals.locked ? '解锁人声轨' : '锁定人声轨'} data-tooltip={trackStates.vocals.locked ? '解锁人声轨｜允许移动、裁切和删除音频片段。' : '锁定人声轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('vocals')}>{#if trackStates.vocals.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整人声轨高度" data-tooltip="调整人声轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'vocals')} ondblclick={(event) => resetTrackHeight(event, 'vocals')}></button>
			</div>
				<div class="track-label track-audio track-background" role="group" aria-label="背景音乐轨" class:track-muted={trackStates.background.muted} class:track-locked={trackStates.background.locked} class:track-processing={trackRuntimeBusy('background')} class:drag-over={dragOverAudioTrackId === 'background' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'background' && dragOverAudioTrackPlacement === 'after'} style={audioTrackStyle('background')} ondragover={(event) => markAudioTrackDropTarget(event, 'background')} ondrop={(event) => dropAudioTrack(event, 'background')}>
				<i class="track-title-level" style={`width:${trackMeterPercent('background')}%`}></i>
				<div>
					{#if editingTrackId === 'background'}
						<input class="track-name-input" data-track-name="background" aria-label="修改背景音乐轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('background')} onkeydown={(event) => handleTrackNameKeydown(event, 'background')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.background.locked} data-tooltip="背景音乐轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('background')}>{trackName('background')}</button>
					{/if}
					<span>{stemsReady ? '伴奏/环境声' : '与人声轨同时生成'}</span>
				</div>
				<div class="track-controls">
					<button class="track-drag-handle" type="button" draggable="true" aria-label="拖动调整背景音乐轨顺序" data-tooltip="调整轨道顺序｜拖动到其他音轨标题上方。" ondragstart={(event) => beginAudioTrackReorder(event, 'background')} ondragend={endAudioTrackReorder}><GripVertical size={13} /></button>
					<button class="track-toggle" class:active={trackStates.background.muted} type="button" data-tooltip="静音背景音乐轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('background')}>M</button>
					<button class="track-toggle" class:active={trackStates.background.solo} type="button" data-tooltip="独奏背景音乐轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('background')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'background'}
							<div class="volume-inline-editor" role="group" aria-label="背景音乐轨音量" onpointerdown={(event) => event.stopPropagation()}>
								<input data-volume-input="background" aria-label="背景音乐轨音量 dB" type="number" min="-60" max="6.02" step="0.01" value={volumeToDb(trackStates.background.volume, 2)} oninput={(event) => updateTrackDb('background', Number(event.currentTarget.value))} onblur={() => finishVolumeEdit('background')} onkeydown={(event) => handleVolumeInputKeydown(event, 'background')} />
								<span>dB</span>
							</div>
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整背景音乐轨音量" data-tooltip="背景音乐轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'background')} ondblclick={(event) => resetTrackDb(event, 'background')}>{volumeDbLabel('background')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.background.locked} type="button" aria-label={trackStates.background.locked ? '解锁背景音乐轨' : '锁定背景音乐轨'} data-tooltip={trackStates.background.locked ? '解锁背景音乐轨｜允许移动、裁切和删除音频片段。' : '锁定背景音乐轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('background')}>{#if trackStates.background.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整背景音乐轨高度" data-tooltip="调整背景音乐轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'background')} ondblclick={(event) => resetTrackHeight(event, 'background')}></button>
			</div>
				<div class="track-label track-audio track-dub" role="group" aria-label="合成配音轨" class:track-muted={trackStates.dub.muted} class:track-locked={trackStates.dub.locked} class:track-processing={trackRuntimeBusy('dub')} class:drag-over={dragOverAudioTrackId === 'dub' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'dub' && dragOverAudioTrackPlacement === 'after'} style={audioTrackStyle('dub')} ondragover={(event) => markAudioTrackDropTarget(event, 'dub')} ondrop={(event) => dropAudioTrack(event, 'dub')}>
				<i class="track-title-level" style={`width:${trackMeterPercent('dub')}%`}></i>
				<div>
					{#if editingTrackId === 'dub'}
						<input class="track-name-input" data-track-name="dub" aria-label="修改合成配音轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('dub')} onkeydown={(event) => handleTrackNameKeydown(event, 'dub')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.dub.locked} data-tooltip="合成配音轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('dub')}>{trackName('dub')}</button>
					{/if}
					<span>TTS / 声音克隆生成音频</span>
				</div>
				<div class="track-controls">
					<button class="track-drag-handle" type="button" draggable="true" aria-label="拖动调整合成配音轨顺序" data-tooltip="调整轨道顺序｜拖动到其他音轨标题上方。" ondragstart={(event) => beginAudioTrackReorder(event, 'dub')} ondragend={endAudioTrackReorder}><GripVertical size={13} /></button>
					<button class="track-toggle" class:active={trackStates.dub.muted} type="button" data-tooltip="静音合成配音轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('dub')}>M</button>
					<button class="track-toggle" class:active={trackStates.dub.solo} type="button" data-tooltip="独奏合成配音轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('dub')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'dub'}
							<div class="volume-inline-editor" role="group" aria-label="合成配音轨音量" onpointerdown={(event) => event.stopPropagation()}>
								<input data-volume-input="dub" aria-label="合成配音轨音量 dB" type="number" min="-60" max="6.02" step="0.01" value={volumeToDb(trackStates.dub.volume, 2)} oninput={(event) => updateTrackDb('dub', Number(event.currentTarget.value))} onblur={() => finishVolumeEdit('dub')} onkeydown={(event) => handleVolumeInputKeydown(event, 'dub')} />
								<span>dB</span>
							</div>
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整合成配音轨音量" data-tooltip="合成配音轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'dub')} ondblclick={(event) => resetTrackDb(event, 'dub')}>{volumeDbLabel('dub')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.dub.locked} type="button" aria-label={trackStates.dub.locked ? '解锁合成配音轨' : '锁定合成配音轨'} data-tooltip={trackStates.dub.locked ? '解锁合成配音轨｜允许移动、裁切和删除音频片段。' : '锁定合成配音轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('dub')}>{#if trackStates.dub.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整合成配音轨高度" data-tooltip="调整合成配音轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'dub')} ondblclick={(event) => resetTrackHeight(event, 'dub')}></button>
			</div>
			<button class="track-label-width-handle" type="button" aria-label="调整轨道标题宽度" data-tooltip="调整标题宽度：左右拖动改变所有轨道标题栏宽度。" onpointerdown={beginLabelColumnResize}></button>
		</div>

		<div
			class="track-canvas"
			bind:this={trackCanvasEl}
			role="region"
			aria-label="音频与字幕轨道滚动区域"
			onscroll={(event) => handleTimelineScroll(event.currentTarget as HTMLDivElement)}
			onwheel={handleTrackWheel}
			onpointerenter={(event) => updateTimelineViewport(event.currentTarget as HTMLDivElement)}
			onpointerleave={endHoverScrub}
		>
			<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
			<div
				class="timeline-content"
				class:dragging={Boolean(dragState)}
				class:panning={Boolean(timelinePanState)}
				style={`width:${timelineZoom * 100}%;--processing-left:${timelineScrollLeft}px;--processing-width:${Math.max(1, timelineViewportWidth)}px`}
				bind:this={timelineContentEl}
				role="application"
				aria-label="视频配音时间线"
				onpointerdown={handleTimelinePointerDown}
				ondblclick={handleTimelineDoubleClick}
				oncontextmenu={openTimelineContextMenu}
				onpointermove={handleTimelinePointerMove}
				onpointerup={endTimelinePointerWork}
				onpointercancel={endTimelinePointerWork}
				onlostpointercapture={endTimelinePointerWork}
				onpointerleave={endHoverScrub}
			>
				<div class="timeline-ruler">
					{#each visibleTimelineTicks as tick}
						<span class:major={tick.major} class:medium={tick.level === 1} style={`left:${tick.percent}%`}>
							<i></i>
							{#if tick.label}<b>{tick.label}</b>{/if}
						</span>
					{/each}
				</div>
				<div class="playhead" style={`left:${playheadPercent}%`}></div>
				{#if hoverTimeMs !== null && hoverScrubEnabled && !isPlaying}
					<div class="hover-playhead" style={`left:${(hoverTimeMs / timelineDurationMs) * 100}%`} aria-hidden="true"></div>
				{/if}
				{#if hasRangeSelection}
					<div class="range-selection" style={`left:${rangeLeftPercent}%;width:${rangeWidthPercent}%`}></div>
				{/if}
				{#if rangeStartMs !== null}<button class="range-handle in" type="button" style={`left:${rangeStartPercent}%`} aria-label={`拖动选区入点，当前 ${rangeStartMs} 毫秒`} data-tooltip="选区入点｜拖动修改范围开始位置。快捷键：I" onpointerdown={(event) => beginSelectionDrag(event, 'start')}>I</button>{/if}
				{#if rangeEndMs !== null}<button class="range-handle out" type="button" style={`left:${rangeEndPercent}%`} aria-label={`拖动选区出点，当前 ${rangeEndMs} 毫秒`} data-tooltip="选区出点｜拖动修改范围结束位置。快捷键：O" onpointerdown={(event) => beginSelectionDrag(event, 'end')}>O</button>{/if}
				<div class="track-row subtitle row-subtitle row-asr-subtitle" data-track-row data-track-id="subtitles" role="group" aria-label="ASR 字幕轨时间线" aria-busy={trackRuntimeBusy('subtitles')} class:locked={trackInteractionLocked('subtitles')} class:processing={trackRuntimeBusy('subtitles')} style={subtitleTrackStyle('subtitles')}>
					{#if asrPreview?.cues.length}
						{#each visibleAsrPreviewCues as cue (cue.cue_id)}
							<div class="cue-chip cue-asr preview-cue" class:phase-timing={asrPreview.phase === 'timing_segmentation'} data-subtitle-item-id={cue.cue_id} style={`left:${clipLeft(cue.start_ms)}%;width:${clipWidth(cue.start_ms, cue.end_ms)}%`} aria-label={`ASR 处理中预览 ${cue.cue_id}`}>
								<strong>{cue.cue_id.replace(/^(preview_|cue_)/, '#')}</strong>
								<span class="cue-text">{cue.text}</span>
								<em>{durationLabel(cue.end_ms - cue.start_ms)}</em>
							</div>
						{/each}
						<div class="asr-preview-phase"><LoaderCircle size={11} /> {asrPreview.phaseLabel}</div>
					{:else if draft?.cues.length}
						{#each visibleAsrCues as cue (cue.cue_id)}
							<button
								class="cue-chip cue-asr"
								data-subtitle-item-id={cue.cue_id}
							class:active={cue.cue_id === selectedCueId}
							class:selected={selectedTimelineItem?.kind === 'subtitle' && selectedTimelineItem.itemId === cue.cue_id}
								class:dragging={dragState?.itemId === cue.cue_id && dragState.trackKind === 'asr'}
								class:timing-review={cue.timing_confidence === 'low' || cue.quality_flags?.includes('timing_review_required')}
								type="button"
								disabled={trackRuntimeBusy('subtitles')}
								style={`left:${cueLeft(cue, 'asr')}%;width:${cueWidth(cue, 'asr')}%`}
							onclick={() => !dragState && selectSubtitleTimelineItem('asr', cue.cue_id)}
								onpointerdown={(event) => startCueDrag(event, cue, 'move', 'asr')}
								aria-label={`选择字幕 ${cue.cue_id}`}
							>
								<span
									class="cue-handle"
									role="slider"
									tabindex="-1"
									aria-label="调整字幕入点"
									aria-valuemin="0"
									aria-valuemax={timelineDurationMs}
									aria-valuenow={cueLiveTime(cue, 'asr').start_ms}
									onpointerdown={(event) => startCueDrag(event, cue, 'trim-start', 'asr')}
								></span>
								<strong>{cue.cue_id.replace('cue_', '#')}</strong>
								<span class="cue-text" onwheel={handleCueTextWheel}>{cueLabel(cue)}</span>
								<em>{durationLabel(cueLiveTime(cue, 'asr').end_ms - cueLiveTime(cue, 'asr').start_ms)}</em>
								<span
									class="cue-handle"
									role="slider"
									tabindex="-1"
									aria-label="调整字幕出点"
									aria-valuemin="0"
									aria-valuemax={timelineDurationMs}
									aria-valuenow={cueLiveTime(cue, 'asr').end_ms}
									onpointerdown={(event) => startCueDrag(event, cue, 'trim-end', 'asr')}
								></span>
							</button>
						{/each}
					{:else if vocalsTrackReady}
						<div class="pending-actions single" aria-label="ASR 字幕轨可用操作">
							<button class="track-inline-action" type="button" disabled={asrGenerateCommand.disabled} data-tooltip={asrGenerateCommand.description} onpointerdown={(event) => event.stopPropagation()} onclick={() => void asrGenerateCommand.onSelect()}>
								<Captions size={11} /> {asrBusy ? '正在听写' : asrGenerateCommand.label}
							</button>
						</div>
					{:else}
						<div class="pending-block">人声轨有可用音频后，可听写生成 ASR 字幕</div>
					{/if}
				</div>
				<div class="track-row subtitle row-subtitle row-localized-subtitle" data-track-row data-track-id="localizedSubtitles" role="group" aria-label="本土化字幕轨时间线" aria-busy={trackRuntimeBusy('localizedSubtitles')} class:locked={trackInteractionLocked('localizedSubtitles')} class:processing={trackRuntimeBusy('localizedSubtitles')} style={subtitleTrackStyle('localizedSubtitles')}>
					{#each visibleLocalizedSubtitles as cue (cue.subtitle_id)}
						<button
							class="cue-chip cue-localized"
							data-subtitle-item-id={cue.subtitle_id}
							class:selected={selectedTimelineItem?.kind === 'subtitle' && selectedTimelineItem.itemId === cue.subtitle_id}
							class:dragging={dragState?.itemId === cue.subtitle_id && dragState.trackKind === 'localized'}
							type="button"
							style={`left:${cueLeft(cue, 'localized')}%;width:${cueWidth(cue, 'localized')}%`}
							onclick={() => !dragState && selectSubtitleTimelineItem('localized', cue.subtitle_id)}
							onpointerdown={(event) => startCueDrag(event, cue, 'move', 'localized')}
							aria-label={`本土化字幕 ${cue.subtitle_id}`}
						>
							<span class="cue-handle" role="slider" tabindex="-1" aria-label="调整本土化字幕入点" aria-valuemin="0" aria-valuemax={subtitleTimelineLimitMs} aria-valuenow={cueLiveTime(cue, 'localized').start_ms} onpointerdown={(event) => startCueDrag(event, cue, 'trim-start', 'localized')}></span>
							<strong>{cue.subtitle_id.replace('localized_', '#')}</strong>
							<span class="cue-text" onwheel={handleCueTextWheel}>{cue.text}</span>
							<em>{durationLabel(cueLiveTime(cue, 'localized').end_ms - cueLiveTime(cue, 'localized').start_ms)}</em>
							<span class="cue-handle" role="slider" tabindex="-1" aria-label="调整本土化字幕出点" aria-valuemin="0" aria-valuemax={subtitleTimelineLimitMs} aria-valuenow={cueLiveTime(cue, 'localized').end_ms} onpointerdown={(event) => startCueDrag(event, cue, 'trim-end', 'localized')}></span>
						</button>
					{/each}
					{#if !draft?.localized_subtitles.length}
						<div class="pending-actions single" aria-label="本土化字幕轨可用操作">
							<button class="track-inline-action" type="button" onclick={onImportLocalizedSrt} disabled={trackStates.localizedSubtitles.locked} data-tooltip="导入本土化 SRT｜创建独立字幕轨，不改动 ASR 字幕时间。" onpointerdown={(event) => event.stopPropagation()}>
								<FileUp size={11} /> 导入本土化 SRT
							</button>
						</div>
					{/if}
				</div>
				<div class="track-row row-original" data-track-row data-track-id="original" data-audio-selection-track="original" aria-busy={trackRuntimeBusy('original')} class:muted={trackStates.original.muted} class:locked={trackInteractionLocked('original')} class:processing={trackRuntimeBusy('original')} style={audioTrackStyle('original')}>
					{#if clipsForTrack('original').length && !originalWaveformUnavailable}
						{#each visibleClipsForTrack('original') as clip (clip.clip_id)}
							<EditableAudioClip {clip} waveformSrc={clipWaveformSrc(clip, 'original')} label={clipLabel(clip, 'original')} tone={clipTone('original')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} selected={selectedTimelineItem?.kind === 'audio' && selectedTimelineItem.itemId === clip.clip_id} locked={trackInteractionLocked('original', clip.clip_id)} processing={trackRuntimeBusy('original', clip.clip_id)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={() => selectAudioTimelineItem('original', clip.clip_id)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} onWaveformError={() => (originalWaveformUnavailable = true)} onWaveformReady={() => (originalWaveformUnavailable = false)} />
						{/each}
					{:else if canAttemptOriginalRecovery}
						<div class="pending-actions single" aria-label="原音轨恢复操作">
							<button class="track-inline-action" type="button" onclick={requestOriginalAudioRecovery} disabled={extractingAudio || trackStates.original.locked} data-tooltip="恢复原音轨｜源音频仍在时重新挂载到时间线；文件已经丢失时从原视频重新抽取。" onpointerdown={(event) => event.stopPropagation()}>
								<RefreshCw size={11} /> {extractingAudio ? '正在恢复' : originalWaveformUnavailable || hasSourceAudio ? '恢复原音轨' : '重新生成原音轨'}
							</button>
						</div>
					{:else if hasVideo}
						<div class="pending-block">原视频文件不可用，无法恢复原音轨</div>
					{:else}
						<div class="pending-block">导入视频后，将自动抽取并创建原音轨</div>
					{/if}
				</div>
				<div class="track-row row-vocals" data-track-row data-track-id="vocals" data-audio-selection-track="vocals" aria-busy={trackRuntimeBusy('vocals')} class:muted={trackStates.vocals.muted} class:locked={trackInteractionLocked('vocals')} class:processing={trackRuntimeBusy('vocals')} style={audioTrackStyle('vocals')}>
					{#if clipsForTrack('vocals').length}
						{#each visibleClipsForTrack('vocals') as clip (clip.clip_id)}
							<EditableAudioClip {clip} waveformSrc={timelineClipWaveformUrl(projectId, clip)} label={clipLabel(clip, 'vocals')} tone={clipTone('vocals')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} selected={selectedTimelineItem?.kind === 'audio' && selectedTimelineItem.itemId === clip.clip_id} locked={trackInteractionLocked('vocals', clip.clip_id)} processing={trackRuntimeBusy('vocals', clip.clip_id)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={() => selectAudioTimelineItem('vocals', clip.clip_id)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
					{:else if hasSourceAudio}
						<div class="pending-actions single">
							<button class="track-inline-action" type="button" onclick={onSeparateStems} disabled={stemsReady || separatingStems || trackStates.vocals.locked} data-tooltip="分离人声与背景｜一次处理会同时创建人声轨和背景音乐轨。" onpointerdown={(event) => event.stopPropagation()}>
								<Mic2 size={11} /> {separatingStems ? '正在分离' : '分离人声与背景'}
							</button>
						</div>
					{:else}
						<div class="pending-block">原音轨就绪后，可分离出人声音频</div>
					{/if}
				</div>
				<div class="track-row row-background" data-track-row data-track-id="background" data-audio-selection-track="background" aria-busy={trackRuntimeBusy('background')} class:muted={trackStates.background.muted} class:locked={trackInteractionLocked('background')} class:processing={trackRuntimeBusy('background')} style={audioTrackStyle('background')}>
					{#if clipsForTrack('background').length}
						{#each visibleClipsForTrack('background') as clip (clip.clip_id)}
							<EditableAudioClip {clip} waveformSrc={timelineClipWaveformUrl(projectId, clip)} label={clipLabel(clip, 'background')} tone={clipTone('background')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} selected={selectedTimelineItem?.kind === 'audio' && selectedTimelineItem.itemId === clip.clip_id} locked={trackInteractionLocked('background', clip.clip_id)} processing={trackRuntimeBusy('background', clip.clip_id)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={() => selectAudioTimelineItem('background', clip.clip_id)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
					{:else if hasSourceAudio}
						<div class="pending-actions single">
							<button class="track-inline-action" type="button" onclick={onSeparateStems} disabled={stemsReady || separatingStems || trackStates.background.locked} data-tooltip="分离人声与背景｜一次处理会同时创建人声轨和背景音乐轨。" onpointerdown={(event) => event.stopPropagation()}>
								<Mic2 size={11} /> {separatingStems ? '正在分离' : '分离人声与背景'}
							</button>
						</div>
					{:else}
						<div class="pending-block">原音轨就绪后，可分离出背景音乐</div>
					{/if}
				</div>
				<div class="track-row row-dub" data-track-row data-track-id="dub" data-audio-selection-track="dub" aria-busy={trackRuntimeBusy('dub')} class:muted={trackStates.dub.muted} class:locked={trackInteractionLocked('dub')} class:processing={trackRuntimeBusy('dub')} style={audioTrackStyle('dub')}>
					{#if clipsForTrack('dub').length}
						{#each visibleClipsForTrack('dub') as clip (clip.clip_id)}
							<EditableAudioClip {clip} waveformSrc={timelineClipWaveformUrl(projectId, clip)} label={clipLabel(clip, 'dub')} tone={clipTone('dub')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} selected={selectedTimelineItem?.kind === 'audio' && selectedTimelineItem.itemId === clip.clip_id} locked={trackInteractionLocked('dub', clip.clip_id)} processing={trackRuntimeBusy('dub', clip.clip_id)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={() => selectAudioTimelineItem('dub', clip.clip_id)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
					{:else}
						<div class="pending-block">准备字幕与音色后，可生成合成配音片段</div>
					{/if}
				</div>
			</div>
		</div>
		<div class="timeline-edge-shadow left" aria-hidden="true"></div>
		<div class="timeline-edge-shadow right" aria-hidden="true"></div>
	</div>
</section>

<ContextMenu
	open={Boolean(timelineContextMenu && timelineContextMenuItems.length)}
	x={timelineContextMenu?.x ?? 0}
	y={timelineContextMenu?.y ?? 0}
	label={timelineContextMenuLabel}
	items={timelineContextMenuItems}
	onClose={() => (timelineContextMenu = null)}
/>

<style>
	.cut-timeline {
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #0f1216;
		overflow: visible;
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
	}

	.timeline-toolbar {
		min-height: 42px;
		display: grid;
		grid-template-columns: auto minmax(120px, 1fr) auto;
		align-items: center;
		gap: 8px;
		padding: 6px 8px;
		border-bottom: 1px solid var(--line);
		background: #171c21;
	}

	.transport,
	.timeline-actions {
		display: flex;
		align-items: center;
		gap: 5px;
		min-width: 0;
	}

	.timeline-actions {
		justify-content: flex-end;
		flex-wrap: wrap;
		row-gap: 4px;
	}

	.track-meter-head {
		position: relative;
		height: 28px;
		overflow: hidden;
		border-bottom: 1px solid var(--line);
		background:
			linear-gradient(90deg, rgba(75, 201, 191, 0.055) 0 72%, rgba(216, 180, 95, 0.06) 72% 88%, rgba(255, 113, 104, 0.075) 88%),
			repeating-linear-gradient(90deg, transparent 0 9px, rgba(255, 255, 255, 0.028) 9px 10px),
			#11161b;
		order: 0;
	}

	.track-meter-head .meter-track {
		position: absolute;
		inset: 0;
		overflow: visible;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.018), rgba(0, 0, 0, 0.14));
	}

	.track-meter-head .meter-track i {
		position: absolute;
		left: 0;
		top: 1px;
		bottom: 1px;
		background: linear-gradient(90deg, #4bc9bf 0 72%, #d8b45f 72% 88%, #ff7168 88%);
		mask-image: repeating-linear-gradient(90deg, #000 0 3px, rgba(0, 0, 0, 0.18) 3px 5px);
		mix-blend-mode: screen;
		opacity: 0.34;
		filter: drop-shadow(0 0 4px rgba(75, 201, 191, 0.18));
		transition: width 16ms linear;
	}

	.track-meter-head:not(.active) .meter-track i {
		transition: none;
	}

	.track-meter-head .meter-track b {
		position: absolute;
		bottom: 2px;
		z-index: 2;
		font-size: 6px;
		line-height: 1;
		color: rgba(190, 201, 208, 0.56);
		font-weight: 600;
		transform: translateX(-50%);
		pointer-events: none;
	}

	.track-meter-head .meter-track b::before {
		content: "";
		position: absolute;
		left: 50%;
		bottom: 7px;
		width: 1px;
		height: 12px;
		background: rgba(225, 232, 236, 0.12);
	}

	.track-meter-head .time-readout {
		position: absolute;
		left: 7px;
		top: 3px;
		z-index: 3;
		min-width: 82px;
		padding: 1px 0;
		border: 0;
		border-radius: 3px;
		background: transparent;
		color: #f6e8a8;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 9px;
		line-height: 13px;
		font-variant-numeric: tabular-nums;
		letter-spacing: 0;
		text-align: center;
		white-space: nowrap;
	}

	.edit-tools {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		padding: 0;
		border: 0;
		background: transparent;
	}

	.toolbar-divider {
		width: 1px;
		height: 18px;
		flex: 0 0 1px;
		margin: 0 1px;
		background: #364149;
	}

	.zoom-stepper {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		border: 0;
		padding: 0;
		background: transparent;
		color: var(--muted);
		font-size: 11px;
		white-space: nowrap;
	}

	.zoom-stepper button {
		width: 26px;
		height: 26px;
		padding: 0;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #20262c;
		color: var(--text);
		cursor: pointer;
		display: inline-grid;
		place-items: center;
		position: relative;
	}

	.zoom-stepper button:hover:not(:disabled) {
		background: #253039;
	}

	.zoom-stepper button:disabled {
		opacity: 0.38;
		cursor: not-allowed;
	}

	.zoom-stepper span {
		min-width: 42px;
		text-align: center;
		font-variant-numeric: tabular-nums;
	}

	.range-marker-tools {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		padding: 0;
		border: 0;
		background: transparent;
	}

	.range-marker-tools button {
		width: 26px;
		height: 26px;
		padding: 0;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #20262c;
		color: #aebbc3;
		display: inline-grid;
		place-items: center;
		cursor: pointer;
	}

	.range-marker-tools button.active {
		border-color: rgba(87, 208, 200, 0.72);
		background: #173a37;
		color: #d5fffb;
	}

	.icon-btn,
	.tool-btn,
	.primary-tool,
	.track-toggle {
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #20262c;
		color: var(--text);
		cursor: pointer;
	}

	.hover-scrub-toggle.active {
		border-color: rgba(87, 208, 200, 0.76);
		background: #173a37;
		color: #d7fffb;
	}

	.icon-tool {
		width: 26px;
		height: 26px;
		display: inline-grid;
		place-items: center;
		padding: 0;
		position: relative;
	}

	.icon-btn {
		width: 26px;
		height: 26px;
		display: inline-grid;
		place-items: center;
		padding: 0;
		line-height: 0;
		position: relative;
	}

	.icon-btn:hover:not(:disabled),
	.icon-btn:focus-visible,
	.tool-btn:hover:not(:disabled),
	.tool-btn:focus-visible,
	.primary-tool:hover:not(:disabled),
	.primary-tool:focus-visible,
	.track-toggle:hover:not(:disabled),
	.track-toggle:focus-visible,
	.range-marker-tools button:hover:not(:disabled),
	.range-marker-tools button:focus-visible {
		border-color: rgba(113, 224, 215, 0.72);
		background: #26343a;
		color: #efffff;
		box-shadow: 0 0 0 1px rgba(87, 208, 200, 0.12);
		outline: none;
	}

	.tool-btn,
	.primary-tool {
		min-height: 24px;
		padding: 3px 7px;
		font-size: 11px;
		white-space: nowrap;
	}

	.tool-btn.icon-tool,
	.primary-tool.icon-tool {
		padding: 0;
		line-height: 0;
	}

	.primary-tool {
		border-color: rgba(87, 208, 200, 0.8);
		background: #143b39;
		color: #d8fffb;
		font-weight: 800;
	}

	.tool-btn.danger {
		color: #ffb5b5;
		border-color: #5e3135;
		background: #251719;
	}

	.icon-btn:disabled,
	.tool-btn:disabled,
	.primary-tool:disabled {
		opacity: 0.52;
		cursor: not-allowed;
	}

	.tracks {
		display: grid;
		position: relative;
		align-items: start;
	}

	.track-labels {
		display: flex;
		flex-direction: column;
		border-right: 1px solid var(--line);
		background: #171c21;
		position: relative;
	}

	.track-label {
		position: relative;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 7px;
		align-items: center;
		padding: 7px 10px 7px 12px;
		border-bottom: 1px solid #2e363d;
		--track-color: #58d1c8;
		background:
			linear-gradient(90deg, color-mix(in srgb, var(--track-color) 16%, transparent), transparent 72%),
			#171c21;
	}

	.track-asr-subtitle { order: 1; --track-color: #7da4ff; }
	.track-localized-subtitle { order: 2; --track-color: #ad8cff; }

	.track-label.drag-over {
		box-shadow: inset 0 2px 0 var(--track-color);
	}

	.track-label.drag-over-after {
		box-shadow: inset 0 -2px 0 var(--track-color);
	}

	.track-label::before {
		content: "";
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 3px;
		background: var(--track-color);
		opacity: 0.92;
	}

	.track-label > :not(.track-title-level):not(.track-resize-handle) {
		position: relative;
		z-index: 2;
	}

	.track-title-level {
		position: absolute;
		inset: 0 auto 0 0;
		z-index: 0;
		background: linear-gradient(90deg, color-mix(in srgb, var(--track-color) 32%, transparent), color-mix(in srgb, var(--track-color) 8%, transparent));
		mix-blend-mode: screen;
		opacity: 0.62;
		transition: width 16ms linear;
		pointer-events: none;
	}

	.track-label.track-locked::after,
	.track-row.locked::after {
		content: "";
		position: absolute;
		inset: 0;
		z-index: 6;
		background: repeating-linear-gradient(135deg, rgba(8, 11, 14, 0.12) 0 7px, rgba(190, 204, 212, 0.075) 7px 9px);
		pointer-events: none;
	}

	.track-label.track-locked::after {
		z-index: 1;
	}

	.track-label.track-processing::after,
	.track-row.processing::after {
		content: "";
		position: absolute;
		z-index: 8;
		top: 0;
		bottom: 0;
		background:
			linear-gradient(90deg, transparent, color-mix(in srgb, var(--track-color, #72b9ce) 12%, transparent), transparent) 0 0 / 180px 100% repeat-x,
			repeating-linear-gradient(115deg, transparent 0 12px, color-mix(in srgb, var(--track-color, #72b9ce) 20%, transparent) 12px 20px, transparent 20px 34px);
		background-size: 180px 100%, 48px 100%;
		animation: track-processing 900ms linear infinite;
		pointer-events: none;
	}

	.track-label.track-processing::after { inset: 0; z-index: 1; }
	.track-row.processing::after {
		left: var(--processing-left, 0);
		width: var(--processing-width, 100%);
	}
	.track-label.track-processing .track-controls { opacity: 0.48; pointer-events: none; }
	.track-row.processing { cursor: progress; }
	.track-row.processing > :not(.preview-cue):not(.asr-preview-phase) { pointer-events: none; }

	.track-label.track-muted .track-title-level {
		display: none;
	}

	.track-label.track-muted {
		filter: grayscale(0.82) saturate(0.28) brightness(0.78);
	}

	.track-original { --track-color: #58d1c8; }
	.track-vocals { --track-color: #7da4ff; }
	.track-background { --track-color: #d9b45f; }
	.track-subtitle { --track-color: #ad8cff; }
	.track-dub { --track-color: #65d28f; }

	.track-name-input,
	.track-name-button,
	.track-label span {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.track-subtitle {
		padding-block: 4px;
	}

	.track-subtitle > div:first-of-type > span {
		display: none;
	}

	.track-name-input {
		width: min(132px, 100%);
		height: 20px;
		box-sizing: border-box;
		border: 0;
		border-radius: 4px;
		padding: 1px 4px 1px 0;
		background: transparent;
		color: var(--text);
		font-size: 12px;
		font-weight: 800;
		outline: none;
	}

	.track-name-button {
		width: 100%;
		border: 0;
		border-radius: 3px;
		padding: 1px 4px 1px 0;
		background: transparent;
		color: var(--text);
		font-size: 12px;
		font-weight: 800;
		line-height: 18px;
		text-align: left;
		cursor: text;
	}

	.track-name-button:hover {
		color: #ffffff;
		background: rgba(255, 255, 255, 0.04);
	}

	.track-name-input:hover {
		background: rgba(255, 255, 255, 0.035);
	}

	.track-name-input:focus {
		padding-inline: 5px;
		background: #0d1216;
		box-shadow: inset 0 0 0 1px rgba(87, 208, 200, 0.45);
	}

	.track-label span {
		margin-top: 2px;
		color: var(--muted);
		font-size: 11px;
	}

	.track-toggle {
		width: 23px;
		height: 23px;
		display: inline-grid;
		place-items: center;
		flex: 0 0 23px;
		padding: 0;
		line-height: 1;
		font-size: 10px;
	}

	.track-toggle.active {
		border-color: #78ddd5;
		background: #173a37;
		color: #d4fffb;
	}

	.track-controls {
		display: flex;
		align-items: center;
		gap: 4px;
		flex-wrap: wrap;
		justify-content: flex-end;
	}

	.track-drag-handle {
		width: 18px;
		height: 23px;
		border: 0;
		border-radius: 4px;
		padding: 0;
		background: transparent;
		color: #6f7c84;
		display: grid;
		place-items: center;
		cursor: grab;
	}

	.track-drag-handle:hover,
	.track-drag-handle:focus-visible {
		background: rgba(255, 255, 255, 0.055);
		color: #d8e3e7;
		outline: none;
	}

	.track-drag-handle:active {
		cursor: grabbing;
	}

	.volume-control {
		position: relative;
		display: inline-flex;
	}

	.volume-db-button {
		min-width: 44px;
		height: 23px;
		border: 1px solid transparent;
		border-radius: 5px;
		padding: 0 5px;
		background: transparent;
		color: #b9c5cd;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 8px;
		font-variant-numeric: tabular-nums;
		cursor: ew-resize;
		white-space: nowrap;
	}

	.volume-db-button:hover {
		background: rgba(255, 255, 255, 0.035);
		color: #d6e1e6;
	}

	.volume-inline-editor {
		width: 54px;
		height: 23px;
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 3px;
		box-sizing: border-box;
		border: 1px solid rgba(87, 208, 200, 0.45);
		border-radius: 4px;
		background: #0d1216;
	}

	.volume-inline-editor input {
		width: 36px;
		height: 19px;
		border: 0;
		background: transparent;
		color: #e2edf2;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 9px;
		font-variant-numeric: tabular-nums;
		outline: none;
	}

	.volume-inline-editor input::-webkit-inner-spin-button,
	.volume-inline-editor input::-webkit-outer-spin-button {
		appearance: none;
		margin: 0;
	}

	.volume-inline-editor span {
		color: #d9e4ea;
		font-size: 8px;
		font-weight: 750;
	}

	.track-label > .track-resize-handle {
		position: absolute;
		left: 0;
		right: 0;
		bottom: -4px;
		z-index: 8;
		height: 9px;
		min-width: 0;
		margin: 0;
		padding: 0;
		border: 0;
		border-radius: 0;
		background: transparent;
		box-sizing: border-box;
		cursor: ns-resize;
		touch-action: none;
	}

	.track-label > .track-resize-handle::after {
		content: "";
		position: absolute;
		left: 0;
		right: 0;
		top: 50%;
		height: 1px;
		background: transparent;
		transform: translateY(-50%);
		transition: height 120ms ease, background 120ms ease, box-shadow 120ms ease;
	}

	.track-label > .track-resize-handle:hover::after,
	.track-label > .track-resize-handle:focus-visible::after {
		height: 2px;
		background: rgba(87, 208, 200, 0.82);
		box-shadow: 0 0 0 1px rgba(87, 208, 200, 0.08);
	}

	.track-label-width-handle {
		position: absolute;
		top: 0;
		right: -4px;
		bottom: 0;
		z-index: 20;
		width: 8px;
		border: 0;
		background: transparent;
		cursor: ew-resize;
	}

	.track-label-width-handle:hover {
		background: rgba(87, 208, 200, 0.2);
	}

	.track-canvas {
		min-width: 0;
		overflow-x: auto;
		overflow-y: hidden;
		position: relative;
	}

	.timeline-content {
		position: relative;
		min-width: 100%;
		display: flex;
		flex-direction: column;
	}

	.timeline-content.dragging {
		cursor: grabbing;
		user-select: none;
	}

	.timeline-content.panning,
	.timeline-content.panning .timeline-ruler {
		cursor: grabbing;
		user-select: none;
	}

	.timeline-content {
		cursor: crosshair;
	}

	.playhead {
		position: absolute;
		top: 34px;
		bottom: 0;
		width: 2px;
		background: #f4d36b;
		z-index: 5;
		box-shadow: 0 0 0 1px rgba(244, 211, 107, 0.18);
		pointer-events: none;
	}

	.hover-playhead {
		position: absolute;
		top: 28px;
		bottom: 0;
		z-index: 4;
		width: 1px;
		border-left: 1px dashed rgba(133, 229, 222, 0.78);
		filter: drop-shadow(0 0 3px rgba(87, 208, 200, 0.32));
		pointer-events: none;
	}

	.range-selection {
		position: absolute;
		top: 34px;
		bottom: 0;
		z-index: 3;
		border-left: 1px solid rgba(244, 211, 107, 0.72);
		border-right: 1px solid rgba(244, 211, 107, 0.72);
		background: rgba(244, 211, 107, 0.08);
		pointer-events: none;
	}

	.range-handle {
		position: absolute;
		top: 35px;
		z-index: 7;
		width: 18px;
		height: 18px;
		border: 1px solid rgba(244, 211, 107, 0.8);
		border-radius: 4px;
		background: #2c2412;
		color: #fff1bd;
		font-size: 9px;
		font-weight: 900;
		transform: translateX(-50%);
		cursor: ew-resize;
		touch-action: none;
	}

	.playhead::before {
		content: "";
		position: absolute;
		left: -6px;
		top: 0;
		width: 14px;
		height: 11px;
		background: #f4d36b;
		clip-path: polygon(0 0, 100% 0, 50% 100%);
	}

	.timeline-ruler {
		position: relative;
		height: 28px;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.02), transparent),
			#191e22;
		border-bottom: 1px solid var(--line);
		cursor: grab;
		order: 0;
	}

	.track-row.row-asr-subtitle { order: 1; --row-tint: rgba(125, 164, 255, 0.065); --track-color: #7da4ff; }
	.track-row.row-localized-subtitle { order: 2; --row-tint: rgba(173, 140, 255, 0.065); --track-color: #ad8cff; }

	.timeline-ruler span {
		position: absolute;
		top: 8px;
		height: 4px;
		width: 1px;
		background: rgba(220, 228, 233, 0.26);
		transform: translateX(-0.5px);
	}

	.timeline-ruler span.medium {
		height: 7px;
		background: rgba(220, 228, 233, 0.34);
	}

	.timeline-ruler span.major {
		height: 12px;
		background: rgba(232, 238, 242, 0.5);
	}

	.timeline-ruler span b {
		position: absolute;
		top: -4px;
		left: 7px;
		color: #8f989f;
		font-size: 8px;
		font-variant-numeric: tabular-nums;
		font-weight: 550;
		white-space: nowrap;
	}

	.track-row {
		position: relative;
		contain: layout paint style;
		--row-tint: rgba(88, 209, 200, 0.06);
		border-bottom: 1px solid #2e363d;
		background:
			linear-gradient(180deg, var(--row-tint), transparent),
			#11161b;
		overflow: hidden;
	}

	.row-original { --row-tint: rgba(88, 209, 200, 0.07); --track-color: #58d1c8; }
	.row-vocals { --row-tint: rgba(125, 164, 255, 0.07); --track-color: #7da4ff; }
	.row-background { --row-tint: rgba(217, 180, 95, 0.065); --track-color: #d9b45f; }
	.row-subtitle { --row-tint: rgba(173, 140, 255, 0.06); --track-color: #ad8cff; }
	.row-dub { --row-tint: rgba(101, 210, 143, 0.06); --track-color: #65d28f; }

	.track-row.muted {
		filter: grayscale(0.9) saturate(0.18) brightness(0.7);
	}

	.timeline-edge-shadow {
		position: absolute;
		top: 28px;
		bottom: 0;
		z-index: 12;
		width: 18px;
		pointer-events: none;
	}

	.timeline-edge-shadow.left {
		left: var(--label-column-width);
		background: linear-gradient(90deg, rgba(4, 7, 9, 0.52), transparent);
	}

	.timeline-edge-shadow.right {
		right: 0;
		background: linear-gradient(270deg, rgba(4, 7, 9, 0.48), transparent);
	}

	.pending-block {
		position: absolute;
		left: 12px;
		top: 50%;
		transform: translateY(-50%);
		max-width: min(360px, calc(100% - 24px));
		max-height: calc(100% - 10px);
		box-sizing: border-box;
		border: 1px dashed color-mix(in srgb, var(--track-color, #6f7d85) 38%, #465058);
		border-radius: 4px;
		padding: 3px 8px;
		color: #77838b;
		background:
			linear-gradient(180deg, color-mix(in srgb, var(--track-color, #6f7d85) 8%, transparent), transparent),
			rgba(17, 22, 27, 0.38);
		font-size: 9.5px;
		font-weight: 500;
		line-height: 13px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.pending-actions {
		position: absolute;
		left: 12px;
		top: 50%;
		display: flex;
		align-items: center;
		gap: 4px;
		max-width: calc(100% - 24px);
		transform: translateY(-50%);
	}

	.pending-actions.single {
		width: min(168px, 46%);
	}

	.track-inline-action {
		height: 23px;
		min-width: 0;
		border: 1px solid color-mix(in srgb, var(--track-color, #789097) 56%, #3f494f);
		border-radius: 4px;
		padding: 0 7px;
		background:
			linear-gradient(180deg, color-mix(in srgb, var(--track-color, #789097) 16%, transparent), transparent),
			rgba(23, 28, 32, 0.56);
		color: #cbd5d9;
		font: inherit;
		font-size: 10px;
		font-weight: 600;
		line-height: 1;
		letter-spacing: 0;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 4px;
		white-space: nowrap;
		cursor: pointer;
		pointer-events: auto;
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
		transition: border-color 120ms ease, background-color 120ms ease, color 120ms ease, box-shadow 120ms ease, transform 120ms ease;
	}

	.pending-actions.single .track-inline-action {
		width: 100%;
	}

	.track-inline-action:hover:not(:disabled),
	.track-inline-action:focus-visible {
		border-color: color-mix(in srgb, var(--track-color, #8faab0) 78%, #dfeaec);
		background:
			linear-gradient(180deg, color-mix(in srgb, var(--track-color, #8faab0) 22%, transparent), transparent),
			rgba(25, 33, 38, 0.68);
		color: #f0f6f7;
		box-shadow: 0 5px 14px rgba(0, 0, 0, 0.32), 0 0 0 1px color-mix(in srgb, var(--track-color, #8faab0) 18%, transparent);
		transform: translateY(-1px);
		outline: none;
	}

	.track-inline-action:disabled {
		opacity: 0.48;
		cursor: wait;
	}

	.cue-chip {
		position: absolute;
		top: 10px;
		height: 50px;
		border: 1px solid #65727b;
		border-radius: 7px;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.04), transparent),
			#242b31;
		color: var(--text);
		text-align: left;
		padding: 7px 16px;
		font-size: 11px;
		overflow: hidden;
		cursor: pointer;
		touch-action: none;
	}

	.cue-chip { min-width: 0; }

	.cue-chip.cue-asr { border-color: #566f9a; }
	.cue-chip.preview-cue {
		z-index: 9;
		border-color: rgba(117, 190, 215, 0.78);
		background:
			repeating-linear-gradient(115deg, rgba(86, 154, 178, 0.13) 0 9px, transparent 9px 18px),
			#1a2b32;
		box-shadow: 0 0 0 1px rgba(102, 190, 217, 0.13), 0 3px 12px rgba(0, 0, 0, 0.28);
		color: #e4f6fb;
		cursor: progress;
		pointer-events: none;
	}

	.cue-chip.preview-cue.phase-timing {
		border-color: #e1bf69;
		box-shadow: 0 0 0 1px rgba(225, 191, 105, 0.22), 0 0 14px rgba(225, 191, 105, 0.12);
	}

	.asr-preview-phase {
		position: sticky;
		left: 8px;
		top: 5px;
		z-index: 10;
		width: max-content;
		height: 22px;
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 0 7px;
		border: 1px solid rgba(104, 184, 208, 0.38);
		border-radius: 4px;
		background: rgba(14, 27, 33, 0.72);
		color: #bfe6f1;
		font-size: 9.5px;
		font-weight: 650;
		pointer-events: none;
		backdrop-filter: blur(5px);
	}

	.asr-preview-phase :global(svg) { animation: task-spin 900ms linear infinite; }
	.cue-chip.cue-asr.timing-review {
		border-color: #a88945;
		background:
			repeating-linear-gradient(135deg, rgba(220, 183, 91, 0.08) 0 5px, transparent 5px 10px),
			#282a2c;
	}
	.cue-chip.cue-localized {
		border-color: #725e91;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.04), transparent), #292532;
	}

	.cue-chip.active {
		border-color: #70ddd5;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.04), transparent),
			#173a37;
		box-shadow: inset 0 0 0 1px rgba(112, 221, 213, 0.18);
	}

	.cue-chip.selected {
		border-color: #f4d36b;
		box-shadow: 0 0 0 1px rgba(244, 211, 107, 0.48), 0 5px 14px rgba(0, 0, 0, 0.25);
	}

	.cue-chip.dragging {
		border-color: #f4d36b;
		background: #2f3320;
		box-shadow: 0 0 0 2px rgba(244, 211, 107, 0.18);
		cursor: grabbing;
	}

	.cue-handle {
		position: absolute;
		top: 4px;
		bottom: 4px;
		width: 8px;
		background: transparent;
		cursor: ew-resize;
	}

	.cue-handle::after {
		content: "";
		position: absolute;
		top: 3px;
		bottom: 3px;
		left: 50%;
		width: 2px;
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.46);
		transform: translateX(-50%);
	}

	.cue-handle:first-child {
		left: 0;
	}

	.cue-handle:last-child {
		right: 0;
	}

	.cue-chip:hover .cue-handle::after,
	.cue-chip.active .cue-handle::after {
		background: #f4d36b;
	}

	.cue-chip strong,
	.cue-chip span,
	.cue-chip em {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-style: normal;
	}

	.cue-chip .cue-text {
		scrollbar-width: none;
	}

	.cue-chip .cue-text::-webkit-scrollbar {
		display: none;
	}

	.row-subtitle .cue-chip {
		top: 4px;
		bottom: 4px;
		height: auto;
		min-height: 26px;
		padding: 4px 9px;
		border-radius: 5px;
	}

	.row-subtitle .cue-chip strong,
	.row-subtitle .cue-chip em {
		display: none;
	}

	.row-subtitle .cue-chip .cue-text {
		margin: 0;
		line-height: 16px;
		text-overflow: ellipsis;
		overflow: hidden;
	}

	.cue-chip span,
	.cue-chip em {
		color: var(--muted);
		margin-top: 2px;
	}

	@keyframes track-processing { to { background-position: 180px 0, 48px 0; } }
	@keyframes task-spin { to { transform: rotate(360deg); } }

	@media (prefers-reduced-motion: reduce) {
		.track-label.track-processing::after,
		.track-row.processing::after,
		.asr-preview-phase :global(svg) { animation: none; }
	}

	@media (max-width: 1180px) {
		.timeline-toolbar {
			grid-template-columns: 1fr;
			align-items: start;
		}

		.tracks {
			grid-template-columns: 142px minmax(0, 1fr);
		}
	}
</style>
