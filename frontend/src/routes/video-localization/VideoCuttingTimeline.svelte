<script lang="ts">
	import { Captions, Check, ChevronsLeft, ChevronsRight, Eye, EyeOff, FileAudio, FileUp, GripVertical, Icon, Layers3, Lock, Mic2, MousePointer2, Pause, Play, Redo2, RefreshCw, SkipBack, SkipForward, Trash2, Undo2, Unlock, Wand2, ZoomIn, ZoomOut } from 'lucide-svelte';
	import type { IconNode } from 'lucide-svelte';
	import { onDestroy, tick } from 'svelte';
	import { buildTimelineTicks, formatTimelineZoom } from '$lib/audio/waveform';
	import ContextMenu from '$lib/components/shared/ContextMenu.svelte';
	import type { ContextMenuItem } from '$lib/components/shared/context-menu';
	import type { HistoryItem, ProjectMediaHealth, VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationDubSubtitleCue, VideoLocalizationOperation, VideoLocalizationSubtitleCue, VideoLocalizationTimelineClip, VideoPreviewCacheStatus } from '$lib/api/types';
	import { durationLabel, timelineClipPreviewWaveformUrl, timelineClipVerificationCoverage, timelineClipWaveformUrl } from './utils';
	import { MIN_SUBTITLE_DURATION_MS, reorderAudioTracks, resolveAudibleMix, subtitleCueDragBounds, timeRangeIntersectsViewport, timelineViewportRange, TRACK_LABELS, type VideoLocalizationAudioTrackId, type VideoLocalizationAudioTrackOrder, type VideoLocalizationDubLaneStates, type VideoLocalizationTrackId, type VideoLocalizationTrackState, type VideoLocalizationTrackStates } from './studio-state';
	import { buildSubtitleTrackCommands, buildTimelineContextMenuItems, type SubtitleTrackKind, type TimelineContextMenuTarget, type TimelineSelectionItem } from './timeline-context-menu';
	import ActivityNotice from './ActivityNotice.svelte';
	import AudioGainEditor from './AudioGainEditor.svelte';
	import CommandSpinner from './CommandSpinner.svelte';
	import HoverMarqueeText from './HoverMarqueeText.svelte';
	import { activityTaskAffectsTrack, type ActivityTask } from './activity-notice';
	import type { SubtitleDisplayCue, SubtitleDisplayModel, SubtitleDisplaySource } from './subtitle-display';
	import { isRepeatedPrimaryPress, resolveTimelineSubtitleHit, resolveTimelineSubtitleMerge, timelinePointerIntent, type TimelineGroupMoveCommitItem, type TimelineSubtitleMergeRequest } from './timeline-interaction';
	import { resolveTimelineGroupMove, type TimelineGroupMoveItem } from './timeline-group-move';
	import {
		TimelineGestureSession,
		type TimelineClipDragState as ClipDragState,
		type TimelineCueDragState as CueDragState,
		type TimelineDragMode as DragMode,
		type TimelineMarqueeState as MarqueeState
	} from './timeline-gesture-session';
	import {
		TimelineSelectionSession,
		timelineSelectionItemEqual as selectionItemEqual,
		uniqueTimelineSelection
	} from './timeline-selection-session';
	import { timelineItemDeletionBlocked } from './timeline-deletion-policy';
	import { isTimelineRuntimeClip } from './timeline-runtime-projection';
	import { timelineDeleteShortcutAllowed } from './timeline-keyboard';
	import { waveformMeterLevelAt } from './timeline-meter';
	import { timelineWheelIntent, trackpadPinchZoomScale } from './timeline-wheel';
	import { resizedTimelineScrollLeft, timelineContentPixelWidth } from './timeline-viewport';
	import { buildDubTrackLaneLayout, placeDubClipGroupAtPrimaryLane, resolveDubClipGroupLanePlacement, resolveDubHistoryDropLane, visibleDubTrackLanes } from './dub-track-lanes';
	import type { PlaybackReadinessStatus } from './playback-readiness';
	import { pendingPlaybackRanges, playbackRangesForViewport, playbackReadinessAt } from './playback-readiness';
	import { semanticTtsGroupAtOrdinal } from './semantic-tts-group-selection';
	import { dubSubtitleTimingBounds } from './dub-subtitle-state';
	import { previewCacheActivityLabel } from './preview-cache';
	import { timelineClipHasAudioSource } from './preview-playback';
	import { mediaAssetAvailable, sourceVideoConfigured } from './media-health';
	import { timelineDubClipLabel } from './audio-identity-label';
	import { ttsSelectionAnchorIsPassive, ttsSourceSelectionRange, type TtsSelectionAnchor, type TtsSelectionSession } from './tts-selection-session';
	import EditableAudioClip from './EditableAudioClip.svelte';
	import TimelineOverviewCanvas from './TimelineOverviewCanvas.svelte';
	import { dbToGain, gainToDb } from './audio-gain';
	import {
		OVERVIEW_WAVEFORM_BINS,
		resolveVisibleWaveformRequest,
		resolveTimelineRenderLevel
	} from './timeline-render-lod';
	import {
		buildVisibleFrameTicks,
		buildVisibleSecondTicks,
		editAudioFrameInterval,
		editFrameInterval,
		formatFrameTimecode,
		frameCoverage,
		frameIndexAtTime,
		framePrecisionVisible,
		lastFrameStartMs,
		minimumFrameDurationMs,
		normalizeAudioFrameInterval,
		normalizeFrameRate,
		snapTimeToFrame,
		stepFrameTime,
		timelineRangeWidthPercent
	} from './frame-timeline';

	let {
		projectId,
		draft,
		confirmedTimingCueIds = new Set<string>(),
		mediaHealth,
		selectedCueId,
		currentTimeMs,
		isPlaying,
		playbackPreparing = false,
			latestOperation,
			extractingAudio,
			separatingStems,
			noticeKind,
			noticeSummary,
			noticeDetail,
			activityTasks,
			asrBusy,
			localizationBusy,
			importingLocalizedSrt = false,
		trackStates,
		dubLaneStates,
		audioTrackOrder,
		timelineZoom,
		timelineViewportStartMs = 0,
		timelineViewportRestoreReady = true,
		subtitleDisplay,
		dubSubtitleDisplayEnabled = false,
		dubSubtitleDisplayAvailable = false,
		onToggleDubSubtitleDisplay = undefined,
		onSelectCue,
		onSelectAudioClip = undefined,
		onClearCueSelection = undefined,
		onExtractAudio,
		onRestoreOriginalAudio,
		onSeparateStems,
		onImportLocalizedSrt,
		onGenerateLocalization,
		onGenerateDubSubtitles = undefined,
		canGenerateDubSubtitles = false,
		dubSubtitleUnavailableReason = '',
		dubSubtitleGenerationBusy = false,
		semanticGroupingBusy = false,
		semanticTtsGroups = [],
		onGenerateSemanticTtsGroups = undefined,
		onSelectSemanticTtsGroup = undefined,
		onTransportAction,
		onTrackStateChange,
		onDubLaneStateChange,
		onDubLaneOrderChange,
		onAudioTrackOrderChange,
		onTimelineZoomChange,
		onTimelineViewportChange = undefined,
		onToggleSubtitleSource,
		onSeekTimeline,
		onSelectionRangeChange = undefined,
		onSelectionRangeCommit = undefined,
		onUpdateCueTime,
		onUpdateLocalizedSubtitleTime,
		onUpdateDubSubtitleTime = undefined,
		onClearSubtitleTrack,
		onDeleteSubtitleItem,
		onFillSubtitleGaps,
		onGenerateAsr,
		onSelectLocalizedSubtitle = undefined,
		onOpenTaskCenter = undefined,
		onSelectSubtitleDisplayCue,
		onSplitCue,
		onSplitLocalizedSubtitle = undefined,
		onSplitTimelineClip = undefined,
		onMergeTimelineSubtitles = undefined,
		onGenerateToSelection,
		onUpdateTimelineClip,
		onMoveTimelineItems = undefined,
		onDeleteTimelineClip,
		onDeleteTimelineItems,
		hoverScrubEnabled = true,
		previewCache = null,
		playbackReadiness = null,
		previewCacheRefreshing = false,
		onRefreshPreviewCache = undefined,
		onHoverScrubChange = undefined,
		onHoverScrub = undefined,
		onHoverScrubEnd = undefined,
		onUndoTimelineClip,
		onRedoTimelineClip,
		canUndoTimeline,
		canRedoTimeline,
		undoTimelineCount = 0,
		redoTimelineCount = 0,
		timelineSelectionItems = [],
		ttsSelectionSession = {
			anchor: null,
			sourceCueIds: [],
			localizedSubtitleIds: [],
			mappedSourceCueIds: [],
			mappedLocalizedSubtitleIds: [],
			explicitSourceCueIds: [],
			explicitLocalizedSubtitleIds: [],
			excludedSourceCueIds: [],
			excludedLocalizedSubtitleIds: []
		},
		ttsCoverageByIdentity = new Map<string, number>(),
		onTimelineSelectionChange = undefined,
		onTtsSelectionAnchorChange = undefined,
		draggingTtsHistory = null,
		onDropTtsHistory = undefined,
		onEndTtsHistoryDrag = undefined
	}: {
		projectId: string;
		draft: VideoLocalizationDraft | null;
		confirmedTimingCueIds?: ReadonlySet<string>;
		mediaHealth: ProjectMediaHealth | null;
		selectedCueId: string;
		currentTimeMs: number;
		isPlaying: boolean;
		playbackPreparing?: boolean;
			latestOperation: VideoLocalizationOperation | null;
			extractingAudio: boolean;
			separatingStems: boolean;
			noticeKind: 'idle' | 'success' | 'error';
			noticeSummary: string;
			noticeDetail: string;
			activityTasks: ActivityTask[];
			asrBusy: boolean;
			localizationBusy: boolean;
			importingLocalizedSrt?: boolean;
		trackStates: VideoLocalizationTrackStates;
		dubLaneStates: VideoLocalizationDubLaneStates;
		audioTrackOrder: VideoLocalizationAudioTrackOrder;
			timelineZoom: number;
		timelineViewportStartMs?: number;
		timelineViewportRestoreReady?: boolean;
		subtitleDisplay: SubtitleDisplayModel;
		dubSubtitleDisplayEnabled?: boolean;
		dubSubtitleDisplayAvailable?: boolean;
		onToggleDubSubtitleDisplay?: () => void;
		onSelectCue: (cueId: string) => void;
		onSelectAudioClip?: (clipId: string | null) => void;
		onClearCueSelection?: () => void;
		onExtractAudio: () => void;
		onRestoreOriginalAudio: () => void | Promise<void>;
		onSeparateStems: () => void;
		onImportLocalizedSrt: () => void;
			onGenerateLocalization: () => void | Promise<void>;
			onGenerateDubSubtitles?: (mode?: 'auto' | 'full') => void | Promise<void>;
			canGenerateDubSubtitles?: boolean;
			dubSubtitleUnavailableReason?: string;
			dubSubtitleGenerationBusy?: boolean;
			semanticGroupingBusy?: boolean;
			semanticTtsGroups?: Array<{ group_id: string; subtitle_ids: string[]; text_preview: string; char_count: number }>;
			onGenerateSemanticTtsGroups?: () => void | Promise<void>;
			onSelectSemanticTtsGroup?: (groupId: string) => void;
		onTransportAction: (action: 'start' | 'end' | 'previous-boundary' | 'next-boundary' | 'play-pause') => void;
		onTrackStateChange: (trackId: VideoLocalizationTrackId, patch: Partial<VideoLocalizationTrackState>) => void;
		onDubLaneStateChange: (lane: number, patch: Partial<VideoLocalizationTrackState>) => void;
		onDubLaneOrderChange: (fromLane: number, toLane: number) => void;
		onAudioTrackOrderChange: (order: VideoLocalizationAudioTrackOrder) => void;
		onTimelineZoomChange: (zoom: number) => void;
		onTimelineViewportChange?: (startMs: number) => void;
		onToggleSubtitleSource: (source: SubtitleDisplaySource) => void;
		onSeekTimeline: (timeMs: number, transient?: boolean) => void;
		onSelectionRangeChange?: (range: { startMs: number; endMs: number } | null) => void;
		onSelectionRangeCommit?: (range: { startMs: number; endMs: number }) => void;
		onUpdateCueTime: (cueId: string, startMs: number, endMs: number) => void;
		onUpdateLocalizedSubtitleTime: (subtitleId: string, startMs: number, endMs: number) => void;
		onUpdateDubSubtitleTime?: (subtitleId: string, startMs: number, endMs: number) => void | Promise<void>;
		onClearSubtitleTrack: (track: SubtitleTrackKind) => void | Promise<void>;
		onDeleteSubtitleItem: (track: SubtitleTrackKind, itemId: string) => void | Promise<void>;
		onFillSubtitleGaps: (track: SubtitleTrackKind) => void | Promise<void>;
		onGenerateAsr: () => void | Promise<void>;
		onSelectLocalizedSubtitle?: (subtitleId: string) => void;
		onOpenTaskCenter?: () => void;
		onSelectSubtitleDisplayCue: (cue: SubtitleDisplayCue) => void;
		onSplitCue: (cueId?: string, splitTimeMs?: number) => void;
		onSplitLocalizedSubtitle?: (subtitleId: string, splitTimeMs: number) => void;
		onSplitTimelineClip?: (clipId: string, splitTimeMs: number) => void;
		onMergeTimelineSubtitles?: (request: TimelineSubtitleMergeRequest) => void | Promise<void>;
		onGenerateToSelection: (startMs: number, endMs: number) => void;
		onUpdateTimelineClip: (clipId: string, startMs: number, endMs: number, sourceStartMs: number, sourceEndMs: number | null, dubLane?: number) => void;
		onMoveTimelineItems?: (items: TimelineGroupMoveCommitItem[]) => void;
		onDeleteTimelineClip: (clipId: string) => boolean | Promise<boolean>;
		onDeleteTimelineItems: (items: TimelineSelectionItem[]) => boolean | Promise<boolean>;
		hoverScrubEnabled?: boolean;
		previewCache?: VideoPreviewCacheStatus | null;
		playbackReadiness?: PlaybackReadinessStatus | null;
		previewCacheRefreshing?: boolean;
		onRefreshPreviewCache?: () => void | Promise<void>;
		onHoverScrubChange?: (enabled: boolean) => void;
		onHoverScrub?: (timeMs: number) => void;
		onHoverScrubEnd?: () => void;
		onUndoTimelineClip: () => void;
		onRedoTimelineClip: () => void;
		canUndoTimeline: boolean;
		canRedoTimeline: boolean;
		undoTimelineCount?: number;
		redoTimelineCount?: number;
		timelineSelectionItems?: TimelineSelectionItem[];
		ttsSelectionSession?: TtsSelectionSession;
		ttsCoverageByIdentity?: ReadonlyMap<string, number>;
		onTimelineSelectionChange?: (items: TimelineSelectionItem[]) => void;
		onTtsSelectionAnchorChange?: (anchor: TtsSelectionAnchor, items: TimelineSelectionItem[], additive: boolean) => void;
		draggingTtsHistory?: HistoryItem | null;
		onDropTtsHistory?: (item: HistoryItem, startMs: number, dubLane: number) => void | Promise<void>;
		onEndTtsHistoryDrag?: () => void;
	} = $props();

	type TimelineTool = 'select' | 'razor';
	// Double-edge blade geometry follows Lucide Lab's ISC-licensed razor-blade icon.
	const razorBladeIconNode: IconNode = [
		['path', { d: 'M22 8h-2V6H4v2H2v8h2v2h16v-2h2Z' }],
		['path', { d: 'M6 11v2' }],
		['path', { d: 'M10 12H6' }],
		['circle', { cx: '12', cy: '12', r: '2' }],
		['path', { d: 'M18 12h-4' }],
		['path', { d: 'M18 11v2' }]
	];
	type SubtitleTimelineItem = VideoLocalizationCue | VideoLocalizationSubtitleCue | VideoLocalizationDubSubtitleCue;
	type HistoryDropPreview = {
		startMs: number;
		endMs: number;
		lane: number;
	};

	let timelineContentEl: HTMLDivElement | null = null;
	let trackCanvasEl: HTMLDivElement | null = null;
	let gestureSession = $state(new TimelineGestureSession());
	const dragState = $derived(gestureSession.is('cue-drag') ? gestureSession.state : null);
	const clipDragState = $derived(gestureSession.is('clip-drag') ? gestureSession.state : null);
	const timelineSeekDrag = $derived(gestureSession.is('seek'));
	const timelinePanState = $derived(gestureSession.is('pan') ? gestureSession.state : null);
	const selectionDrag = $derived(gestureSession.is('selection-handle') ? gestureSession.state.edge : null);
	const rangeCreateState = $derived(gestureSession.is('range-create') ? gestureSession.state : null);
	const marqueeState = $derived(gestureSession.is('marquee') ? gestureSession.state : null);
	let liveCueTimes = $state<Record<string, { start_ms: number; end_ms: number }>>({});
	let liveClipTimes = $state<Record<string, { start_ms: number; end_ms: number; source_start_ms?: number; source_end_ms?: number | null }>>({});
	let timelineScrollLeft = $state(0);
	let timelineViewportWidth = $state(0);
	let restoredViewportProjectId = '';
	let timelineViewportRestored = false;
	let viewportChangeTimer: ReturnType<typeof setTimeout> | null = null;
	let pendingViewportStartMs = 0;
	let autoFollowSuspendedUntil = 0;
	let programmaticTimelineScroll = false;
	let wasPlaying = false;
	let pendingSeekMs = 0;
	let seekAnimationFrame = 0;
	let lastTrackPrimaryPress: { at: number; x: number; y: number } | null = null;
	let suppressNextTimelineDoubleClick = false;
	let preserveRangeOnCueSelection = false;
	let rangeStartMs = $state<number | null>(null);
	let rangeEndMs = $state<number | null>(null);
	let lastSelectionRangeKey = '';
	const MIN_RANGE_DURATION_MS = MIN_SUBTITLE_DURATION_MS;
	let labelColumnWidth = $state(236);
	let editingTrackId = $state<VideoLocalizationTrackId | null>(null);
	let editingTrackValue = $state('');
	let editingDubLane = $state<number | null>(null);
	let editingDubLaneValue = $state('');
	let openVolumeTrack = $state<VideoLocalizationTrackId | null>(null);
	let openDubLaneVolume = $state<number | null>(null);
	let draggedAudioTrackId = $state<VideoLocalizationAudioTrackId | null>(null);
	let dragOverAudioTrackId = $state<VideoLocalizationAudioTrackId | null>(null);
	let dragOverAudioTrackPlacement = $state<'before' | 'after'>('before');
	let draggedDubLane = $state<number | null>(null);
	let dragOverDubLane = $state<number | null>(null);
	let volumeClickTimer: ReturnType<typeof setTimeout> | null = null;
	let dubWaveforms = $state<Record<string, { bars: number[]; durationSeconds: number }>>({});
	let originalWaveformRevision = $state(0);
	let restoredSourceOperationMarker = $state('');
	let timelineContextMenu = $state<{ x: number; y: number; target: TimelineContextMenuTarget } | null>(null);
	let trackHeightContextMenu = $state<{ x: number; y: number; trackId: VideoLocalizationTrackId } | null>(null);
	const selectionSession = $derived(new TimelineSelectionSession(timelineSelectionItems));
	const selectedTimelineItem = $derived(selectionSession.primary);
	const selectedTimelineItems = $derived(selectionSession.items);
	let hoverTimeMs = $state<number | null>(null);
	let activeTool = $state<TimelineTool>('select');
	let semanticGroupNumber = $state(1);
	let hoverScrubFrame = 0;
	let snapGuideMs = $state<number | null>(null);
	let historyDropPreview = $state<HistoryDropPreview | null>(null);
	let historyDropTargetActive = false;
	let historyDropCommitting = false;
	let bufferingStartedAt = 0;
	let bufferingElapsedMs = $state(0);
	let bufferingElapsedTimer: ReturnType<typeof setInterval> | null = null;
	$effect(() => {
		if (playbackPreparing) {
			if (!bufferingStartedAt) bufferingStartedAt = Date.now();
			if (!bufferingElapsedTimer) {
				bufferingElapsedTimer = setInterval(() => {
					bufferingElapsedMs = Math.max(0, Date.now() - bufferingStartedAt);
				}, 200);
			}
		} else {
			bufferingStartedAt = 0;
			bufferingElapsedMs = 0;
			if (bufferingElapsedTimer) clearInterval(bufferingElapsedTimer);
			bufferingElapsedTimer = null;
		}
	});
	onDestroy(() => {
		if (bufferingElapsedTimer) clearInterval(bufferingElapsedTimer);
		if (viewportChangeTimer) {
			clearTimeout(viewportChangeTimer);
			onTimelineViewportChange?.(pendingViewportStartMs);
		}
	});
	$effect(() => {
		if (!draggingTtsHistory) {
			historyDropPreview = null;
			historyDropTargetActive = false;
		}
	});
	const DEFAULT_TRACK_HEIGHTS: Record<VideoLocalizationTrackId, number> = {
		original: 58,
		vocals: 58,
		background: 58,
		subtitles: 34,
		localizedSubtitles: 34,
		dub: 58
	};
	let trackHeights = $state<Record<VideoLocalizationTrackId, number>>({ ...DEFAULT_TRACK_HEIGHTS });

	const hasVideo = $derived(sourceVideoConfigured(mediaHealth));
	const hasRecoverableVideo = $derived(mediaAssetAvailable(mediaHealth, 'source_video'));
	const hasSourceAudio = $derived(mediaAssetAvailable(mediaHealth, 'source_audio'));
	const canAttemptOriginalRecovery = $derived(hasSourceAudio || hasRecoverableVideo);
	const hasVocals = $derived(mediaAssetAvailable(mediaHealth, 'vocals'));
	const clipsByTrack = $derived.by(() => {
		const grouped: Record<VideoLocalizationAudioTrackId, VideoLocalizationTimelineClip[]> = {
			original: [],
			vocals: [],
			background: [],
			dub: []
		};
		for (const clip of draft?.timeline_clips ?? []) {
			if (clip.track_id in grouped) grouped[clip.track_id as VideoLocalizationAudioTrackId].push(clip);
		}
		if (!mediaAssetAvailable(mediaHealth, 'source_audio')) grouped.original = [];
		if (!mediaAssetAvailable(mediaHealth, 'vocals')) grouped.vocals = [];
		if (!mediaAssetAvailable(mediaHealth, 'background')) grouped.background = [];
		return grouped;
	});
	const vocalsTrackReady = $derived(hasVocals && clipsForTrack('vocals').some(timelineClipHasAudioSource));
	const stemsReady = $derived(mediaAssetAvailable(mediaHealth, 'vocals') && mediaAssetAvailable(mediaHealth, 'background'));
	const durationMs = $derived(Math.max(
		draft?.source_media.duration_ms ?? 0,
		...subtitleDisplay.tracks.asr.cues.map((cue) => cue.end_ms),
		...subtitleDisplay.tracks.localized.cues.map((cue) => cue.end_ms),
		...(draft?.timeline_clips ?? []).map((clip) => clip.end_ms ?? 0),
		0
	));
	const timelineDurationMs = $derived(durationMs ? Math.max(durationMs, 1000) : 60000);
	const playbackCacheRanges = $derived(playbackReadiness?.ranges ?? pendingPlaybackRanges(previewCache?.ranges ?? []));
	const playbackCacheProgress = $derived(playbackReadiness?.progress ?? 0);
	const playbackCacheReadyChunks = $derived(playbackReadiness?.ready_chunks ?? 0);
	const playbackCacheTotalChunks = $derived(playbackReadiness?.total_chunks ?? previewCache?.total_chunks ?? 0);
	const currentPlaybackRange = $derived(playbackReadinessAt(playbackReadiness, currentTimeMs));
	const currentPlaybackBlockers = $derived(currentPlaybackRange?.blockers ?? []);
	const playbackHealthKind = $derived(playbackPreparing || currentPlaybackRange?.status === 'loading'
		? 'loading'
		: currentPlaybackRange?.status === 'ready'
			? 'ready'
			: currentPlaybackRange?.status === 'failed'
				? 'failed'
				: 'empty');
	const playbackHealthLabel = $derived.by(() => {
		const totalProgress = `${Math.round(playbackCacheProgress * 100)}%`;
		const blockers = currentPlaybackBlockers.join(' + ');
		if (playbackPreparing) {
			const elapsed = (bufferingElapsedMs / 1000).toFixed(1);
			return `缓冲中 · ${blockers || '正在检测媒体'} · ${elapsed}s`;
		}
		if (previewCache?.state === 'failed') return previewCacheActivityLabel(previewCache);
		if (previewCache?.state === 'building' && previewCache.phase !== 'idle') {
			return `${previewCacheActivityLabel(previewCache)} · 可即时播放 ${totalProgress}`;
		}
		if (!playbackReadiness) return previewCacheActivityLabel(previewCache ?? null);
		if (currentPlaybackRange?.status === 'ready') return `当前位置可即时播放 · 全线 ${totalProgress}`;
		if (currentPlaybackRange?.status === 'failed') return `播放失败 · ${blockers || '媒体不可用'}`;
		if (currentPlaybackRange?.status === 'loading') return `缓存中 · ${blockers || '正在加载媒体'} · 全线 ${totalProgress}`;
		return `当前位置尚未缓存 · ${blockers || '等待媒体加载'} · 全线 ${totalProgress}`;
	});
	const contentEndMs = $derived(Math.max(
		draft?.source_media.duration_ms ?? 0,
		...subtitleDisplay.tracks.asr.cues.map((cue) => cue.end_ms),
		...subtitleDisplay.tracks.localized.cues.map((cue) => cue.end_ms),
		...(draft?.timeline_clips ?? []).map((clip) => clip.end_ms ?? 0),
		0
	));
	const timelineFrameRate = $derived(normalizeFrameRate(draft?.source_media.frame_rate));
	const audioClipMinimumDurationMs = $derived(minimumFrameDurationMs(timelineFrameRate));
	const lastPlayableFrameMs = $derived(lastFrameStartMs(timelineDurationMs, timelineFrameRate));
	const subtitleTimelineLimitMs = $derived(Math.max(draft?.source_media.duration_ms ?? timelineDurationMs, MIN_SUBTITLE_DURATION_MS));
	const timelineTicks = $derived(buildTimelineTicks(timelineDurationMs / 1000, timelineZoom));
	const renderViewport = $derived(timelineViewportWidth > 0
		? timelineViewportRange(timelineDurationMs, timelineZoom, timelineScrollLeft, timelineViewportWidth)
		: { startMs: 0, endMs: timelineDurationMs });
	const visiblePlaybackCacheRanges = $derived(playbackRangesForViewport(
		playbackCacheRanges,
		renderViewport,
		Math.max(24, Math.min(96, Math.floor((timelineViewportWidth || 768) / 12)))
	));
	const visibleTimelineTicks = $derived(timelineTicks.filter((tick) => tick.time * 1000 >= renderViewport.startMs && tick.time * 1000 <= renderViewport.endMs));
	const showFramePrecision = $derived(framePrecisionVisible(timelineDurationMs, timelineFrameRate, timelineZoom, timelineViewportWidth));
	const renderableTimelineItemCount = $derived(
		subtitleDisplay.tracks.asr.cues.length
		+ subtitleDisplay.tracks.localized.cues.length
		+ (draft?.timeline_clips.length ?? 0)
	);
	const timelineRenderLevel = $derived(resolveTimelineRenderLevel({
		durationMs: timelineDurationMs,
		zoom: timelineZoom,
		viewportWidth: timelineViewportWidth,
		itemCount: renderableTimelineItemCount
	}));
	const visibleFrameTicks = $derived(showFramePrecision ? buildVisibleFrameTicks({
		durationMs: timelineDurationMs,
		frameRate: timelineFrameRate,
		startMs: renderViewport.startMs,
		endMs: renderViewport.endMs,
		zoom: timelineZoom,
		viewportWidth: timelineViewportWidth
	}) : []);
	const visibleFrameSubTicks = $derived(visibleFrameTicks.filter((tick) => tick.frame % Math.max(1, Math.round(timelineFrameRate)) !== 0));
	const visibleSecondTicks = $derived(showFramePrecision ? buildVisibleSecondTicks({
		durationMs: timelineDurationMs,
		startMs: renderViewport.startMs,
		endMs: renderViewport.endMs
	}) : []);
	const visibleAsrDisplayCues = $derived(subtitleDisplay.tracks.asr.cues.filter((cue) =>
		dragState?.itemId === cue.id || timeRangeIntersectsViewport(cue.start_ms, cue.end_ms, renderViewport)
	));
	const visibleLocalizedDisplayCues = $derived(subtitleDisplay.tracks.localized.cues.filter((cue) =>
		dragState?.itemId === cue.id || timeRangeIntersectsViewport(cue.start_ms, cue.end_ms, renderViewport)
	));
	function dubSubtitleCue(displayCue: SubtitleDisplayCue) {
		const raw = displayCue.raw as Partial<VideoLocalizationDubSubtitleCue>;
		return Array.isArray(raw.dub_lanes)
			? raw as VideoLocalizationDubSubtitleCue
			: null;
	}
	const dubTrackLayout = $derived.by(() => {
		// Lane ownership stays fixed while a clip is being dragged. The live time is
		// rendered separately so one moving clip cannot visually repack its neighbors.
		return buildDubTrackLaneLayout(clipsForTrack('dub'));
	});
	const dubTrackLanes = $derived(visibleDubTrackLanes(dubTrackLayout, Object.keys(dubLaneStates)));
	const audibleMix = $derived(resolveAudibleMix({
		trackStates,
		trackMedia: {
			original: trackHasMedia('original'),
			vocals: trackHasMedia('vocals'),
			background: trackHasMedia('background')
		},
		dubLanes: dubTrackLanes.map((clips, lane) => ({
			lane,
			hasMedia: clips.length > 0,
			state: dubLaneState(lane)
		}))
	}));
	const clipLaneDropPreviews = $derived.by(() => {
		if (!clipDragState || clipDragState.trackId !== 'dub' || clipDragState.mode !== 'move' || clipDragState.hoverLane === null || clipDragState.hoverLane === clipDragState.startLane) return null;
		const ranges = dubClipDragLaneRanges(clipDragState);
		if (!ranges.length) return [];
		const lockedLanes = dubTrackLanes.map((_, lane) => lane).filter((lane) => dubLaneInteractionLocked(lane));
		const placement = placeDubClipGroupAtPrimaryLane(
			clipsForTrack('dub'),
			ranges,
			clipDragState.clipId,
			clipDragState.hoverLane,
			lockedLanes
		);
		if (!placement) return [{ lane: clipDragState.hoverLane, items: ranges, allowed: false }];
		return placement.targetLanes.map((lane) => ({
			lane,
			items: ranges.filter((range) => placement.laneByClipId[range.clipId] === lane),
			allowed: true
		}));
	});
	const clipNewLaneIndexes = $derived.by(() => {
		if (!clipDragState || clipDragState.trackId !== 'dub' || clipDragState.mode !== 'move') return [];
		const ranges = dubClipDragLaneRanges(clipDragState);
		if (!ranges.length) return [];
		const lockedLanes = dubTrackLanes.map((_, lane) => lane).filter((lane) => dubLaneInteractionLocked(lane));
		const placement = resolveDubClipGroupLanePlacement(
			clipsForTrack('dub'),
			ranges,
			clipDragState.clipId,
			dubTrackLanes.length,
			lockedLanes
		);
		return placement?.targetLanes.filter((lane) => lane >= dubTrackLanes.length) ?? [];
	});
	const playheadFrame = $derived(frameCoverage(Math.min(currentTimeMs, lastPlayableFrameMs), timelineFrameRate, timelineDurationMs));
	const playheadPercent = $derived(Math.max(0, Math.min(100, (playheadFrame.startMs / timelineDurationMs) * 100)));
	const playheadFrameWidthPercent = $derived(Math.max(0, ((playheadFrame.endMs - playheadFrame.startMs) / timelineDurationMs) * 100));
	const hoverFrame = $derived(hoverTimeMs === null ? null : frameCoverage(hoverTimeMs, timelineFrameRate, timelineDurationMs));
	const selectedCue = $derived(draft?.cues.find((cue) => cue.cue_id === selectedCueId) ?? null);
	const asrCueSelectionActive = $derived(!selectedTimelineItem || (selectedTimelineItem.kind === 'subtitle' && selectedTimelineItem.trackId === 'subtitles' && selectedTimelineItem.itemId === selectedCueId));
	const canEditSelectedCue = $derived(Boolean(asrCueSelectionActive && selectedCue) && !trackInteractionLocked('subtitles'));
	const selectedSubtitleMerge = $derived(resolveTimelineSubtitleMerge(selectedTimelineItems, draft?.cues ?? [], draft?.localized_subtitles ?? []));
	const canMergeSelectedSubtitles = $derived(Boolean(
		selectedSubtitleMerge
		&& onMergeTimelineSubtitles
		&& !selectedTimelineItems.some((item) => trackInteractionLocked(item.trackId, item.itemId))
	));
	const canDeleteSelectedItems = $derived(selectedTimelineItems.length > 0 && !selectionContainsDeletionBlockedItem(selectedTimelineItems));
	const hasRangeSelection = $derived(rangeStartMs !== null && rangeEndMs !== null && Math.abs(rangeEndMs - rangeStartMs) >= MIN_RANGE_DURATION_MS);
	const rangeStartValue = $derived(rangeStartMs ?? 0);
	const rangeEndValue = $derived(rangeEndMs ?? rangeStartValue);
	const rangeStartPercent = $derived(Math.max(0, Math.min(100, (rangeStartValue / timelineDurationMs) * 100)));
	const rangeEndPercent = $derived(Math.max(0, Math.min(100, (rangeEndValue / timelineDurationMs) * 100)));
	const rangeLeftPercent = $derived(hasRangeSelection ? Math.max(0, Math.min(100, (Math.min(rangeStartValue, rangeEndValue) / timelineDurationMs) * 100)) : 0);
	const rangeWidthPercent = $derived(hasRangeSelection ? Math.max(0, Math.min(100 - rangeLeftPercent, (Math.abs(rangeEndValue - rangeStartValue) / timelineDurationMs) * 100)) : 0);
	const marqueeRect = $derived(marqueeState ? {
		left: Math.min(marqueeState.startX, marqueeState.currentX),
		top: Math.min(marqueeState.startY, marqueeState.currentY),
		width: Math.abs(marqueeState.currentX - marqueeState.startX),
		height: Math.abs(marqueeState.currentY - marqueeState.startY)
	} : null);
	const masterLevel = $derived(isPlaying ? estimateMasterLevel() : 0);
	const asrSubtitleVisible = $derived(subtitleDisplay.tracks.asr.visible);
	const localizedSubtitleVisible = $derived(subtitleDisplay.tracks.localized.visible);
	const asrCommandContext = $derived({
		itemCount: draft?.cues.length ?? 0,
		locked: trackStates.subtitles.locked === true,
		canGenerateAsr: vocalsTrackReady,
		asrBusy,
		canGenerateLocalization: Boolean(draft?.cues.length),
		localizationBusy,
		canGenerateDubSubtitles,
		dubSubtitleGenerationBusy,
		trackBusy: trackRuntimeBusy('subtitles'),
		asrUnavailableReason: '人声轨有可用音频后，才能听写生成 ASR 字幕',
		dubSubtitleUnavailableReason,
		hasSelectionPoints: rangeStartMs !== null || rangeEndMs !== null,
		onGenerateAsr,
		onGenerateLocalization,
		onGenerateDubSubtitles,
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
		const commands = buildTimelineContextMenuItems(target, {
			itemCount,
			trackItemCount: targetTrackId
				? targetTrackId === 'subtitles'
					? draft?.cues.length ?? 0
					: targetTrackId === 'localizedSubtitles'
						? draft?.localized_subtitles.length ?? 0
						: clipsForTrack(targetTrackId).length
				: 0,
			locked: targetTrackId ? trackStates[targetTrackId].locked === true : false,
			selectionLocked: selectionContainsDeletionBlockedItem(contextMenuDeletionItems(target)),
			selectedItems: selectedTimelineItems,
			canGenerateAsr: vocalsTrackReady,
			asrBusy,
			canGenerateLocalization: Boolean(draft?.cues.length),
			localizationBusy,
			canGenerateDubSubtitles,
			dubSubtitleGenerationBusy,
			trackBusy: targetTrackId ? trackRuntimeBusy(targetTrackId) : false,
			asrUnavailableReason: '人声轨有可用音频后，才能听写生成 ASR 字幕',
			dubSubtitleUnavailableReason,
			hasSelectionPoints: rangeStartMs !== null || rangeEndMs !== null,
			onGenerateAsr,
			onGenerateLocalization,
			onGenerateDubSubtitles,
			onClearSubtitleTrack,
			onDeleteSubtitleItem: deleteSubtitleTimelineItem,
			onDeleteAudioClip: deleteAudioTimelineItem,
			onDeleteSelectedItems: deleteSelectedTimelineItems,
			onDeleteTrack: deleteTimelineTrack,
			onFillSubtitleGaps,
			onSetSelectionStart: (timeMs) => setSelectionPoint('start', timeMs),
			onSetSelectionEnd: (timeMs) => setSelectionPoint('end', timeMs),
			onClearSelection: clearSelection
		});
		if (target.kind === 'audio-clip' && isVideoDerivedAudioTrack(target.trackId)) {
			commands.push({
				id: `restore-video-alignment-${target.itemId}`,
				label: '恢复与视频对齐',
				description: '把当前片段恢复到从视频派生时的原始时间位置',
				icon: RefreshCw,
				separatorBefore: true,
				disabled: trackInteractionLocked(target.trackId, target.itemId),
				onSelect: () => restoreVideoDerivedClipAlignment(target.itemId)
			});
		}
		if (target.kind === 'track' && isVideoDerivedAudioTrack(target.trackId)) {
			commands.push({
				id: `restore-video-alignment-track-${target.trackId}`,
				label: '恢复整条轨与视频对齐',
				description: '把这条轨上从视频派生的所有片段恢复到原始时间位置',
				icon: RefreshCw,
				separatorBefore: true,
				disabled: trackInteractionLocked(target.trackId) || !clipsForTrack(target.trackId).length,
				onSelect: () => restoreVideoDerivedTrackAlignment(target.trackId)
			});
		}
		return commands;
	});
	const trackHeightContextMenuItems = $derived.by<ContextMenuItem[]>(() => {
		if (!trackHeightContextMenu) return [];
		const { trackId } = trackHeightContextMenu;
		return [
			{
				id: `reset-track-height-${trackId}`,
				label: '恢复当前轨道默认高度',
				description: `${TRACK_LABELS[trackId]}恢复为默认高度`,
				icon: RefreshCw,
				onSelect: () => resetTrackHeightValue(trackId)
			},
			{
				id: 'reset-all-track-heights',
				label: '恢复全部轨道默认高度',
				description: '每条轨道恢复各自的默认高度',
				icon: RefreshCw,
				separatorBefore: true,
				onSelect: resetAllTrackHeights
			}
		];
	});

	$effect(() => {
		projectId;
		originalWaveformRevision = 0;
		restoredSourceOperationMarker = '';
	});

	$effect(() => {
		const marker = latestOperation?.kind === 'source_audio' && latestOperation.status === 'success'
			? `${latestOperation.operation_id}:${latestOperation.completed_at ?? 'completed'}`
			: '';
		if (!marker || marker === restoredSourceOperationMarker) return;
		restoredSourceOperationMarker = marker;
		originalWaveformRevision = Date.now();
	});

	function isLocalizedSubtitle(item: SubtitleTimelineItem): item is VideoLocalizationSubtitleCue | VideoLocalizationDubSubtitleCue {
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

	function cueAllowsPointerTrim(cue: SubtitleTimelineItem, trackKind: SubtitleTrackKind) {
		const time = cueLiveTime(cue, trackKind);
		const contentWidth = Math.max(1, timelineViewportWidth) * Math.max(1, timelineZoom);
		return ((time.end_ms - time.start_ms) / Math.max(1, timelineDurationMs)) * contentWidth >= 18;
	}

	function clipLeft(startMs: number | null | undefined) {
		return Math.max(0, Math.min(100, (((startMs ?? 0) / timelineDurationMs) * 100)));
	}

	function clipWidth(startMs: number | null | undefined, endMs: number | null | undefined) {
		const start = startMs ?? 0;
		const end = Math.max(start, endMs ?? start);
		return timelineRangeWidthPercent(start, end, timelineDurationMs);
	}

	function timelineClipTime(clip: VideoLocalizationTimelineClip) {
		const live = liveClipTimes[clip.clip_id];
		const start = clip.start_ms ?? 0;
		const end = clip.end_ms ?? start + 1800;
		if (live) return live;
		const sourceStart = clip.source_start_ms ?? 0;
		const normalized = normalizeAudioFrameInterval({
			startMs: start,
			endMs: end,
			sourceStartMs: sourceStart,
			sourceEndMs: clip.source_end_ms ?? sourceStart + Math.max(0, end - start),
			frameRate: timelineFrameRate,
			timelineDurationMs
		});
		return {
			start_ms: normalized.startMs,
			end_ms: normalized.endMs,
			source_start_ms: normalized.sourceStartMs,
			source_end_ms: normalized.sourceEndMs
		};
	}

	function cueLabel(cue: SubtitleTimelineItem) {
		if (isLocalizedSubtitle(cue)) return cue.text.trim() || '未命名本土化字幕';
		return cue.en_subtitle_text?.trim() || '未命名 ASR 字幕';
	}

	function cueTextMarquee(node: HTMLElement) {
		let animationFrame = 0;
		let hovering = false;
		let cycleStartedAt = 0;
		let maxScroll = 0;
		const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
		const updateEdges = () => {
			maxScroll = Math.max(0, node.scrollWidth - node.clientWidth);
			node.dataset.overflowLeft = String(node.scrollLeft > 1);
			node.dataset.overflowRight = String(node.scrollLeft < maxScroll - 1);
			node.dataset.overflowing = String(maxScroll > 1);
		};
		const stop = (reset = true) => {
			if (animationFrame) cancelAnimationFrame(animationFrame);
			animationFrame = 0;
			cycleStartedAt = 0;
			if (reset) node.scrollLeft = 0;
			updateEdges();
		};
		const animate = (timestamp: number) => {
			if (!hovering || maxScroll <= 1 || reduceMotion) return stop(false);
			if (!cycleStartedAt) cycleStartedAt = timestamp;
			const leadPauseMs = 420;
			const tailPauseMs = 720;
			const travelMs = Math.max(900, (maxScroll / 30) * 1000);
			const cycleMs = leadPauseMs + travelMs + tailPauseMs;
			const elapsed = (timestamp - cycleStartedAt) % cycleMs;
			if (elapsed < leadPauseMs) node.scrollLeft = 0;
			else if (elapsed < leadPauseMs + travelMs) node.scrollLeft = maxScroll * ((elapsed - leadPauseMs) / travelMs);
			else node.scrollLeft = maxScroll;
			updateEdges();
			animationFrame = requestAnimationFrame(animate);
		};
		const start = () => {
			hovering = true;
			updateEdges();
			if (maxScroll > 1 && !reduceMotion && !animationFrame) animationFrame = requestAnimationFrame(animate);
		};
		const leave = () => {
			hovering = false;
			stop();
		};
		const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
			stop(false);
			updateEdges();
			if (hovering) start();
		});
		const mutationObserver = new MutationObserver(updateEdges);
		node.addEventListener('pointerenter', start);
		node.addEventListener('pointerleave', leave);
		resizeObserver?.observe(node);
		mutationObserver.observe(node, { childList: true, characterData: true, subtree: true });
		requestAnimationFrame(updateEdges);
		return {
			destroy() {
				stop(false);
				node.removeEventListener('pointerenter', start);
				node.removeEventListener('pointerleave', leave);
				resizeObserver?.disconnect();
				mutationObserver.disconnect();
			}
		};
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

	function dubLaneState(lane: number) {
		return dubLaneStates[String(lane)] ?? (lane === 0
			? dubLaneStates['0'] ?? trackStates.dub
			: { muted: false, solo: false, volume: 1, locked: false });
	}

	function toggleDubLaneMuted(lane: number) {
		const muted = !dubLaneState(lane).muted;
		onDubLaneStateChange(lane, { muted, ...(muted ? { solo: false } : {}) });
	}

	function toggleDubLaneSolo(lane: number) {
		const solo = !dubLaneState(lane).solo;
		onDubLaneStateChange(lane, { solo, ...(solo ? { muted: false } : {}) });
	}

	function toggleDubLaneLocked(lane: number) {
		onDubLaneStateChange(lane, { locked: !dubLaneState(lane).locked });
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

	function beginDubLaneReorder(event: DragEvent, lane: number) {
		event.stopPropagation();
		if (dubLaneStates[String(lane)]?.locked) return;
		draggedDubLane = lane;
		dragOverDubLane = null;
		if (event.dataTransfer) {
			event.dataTransfer.effectAllowed = 'move';
			event.dataTransfer.setData('text/plain', `dub-lane:${lane}`);
		}
	}

	function markDubLaneDropTarget(event: DragEvent, lane: number) {
		if (draggedDubLane === null || draggedDubLane === lane) return;
		if (dubLaneStates[String(draggedDubLane)]?.locked || dubLaneStates[String(lane)]?.locked) return;
		event.preventDefault();
		event.stopPropagation();
		dragOverDubLane = lane;
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
	}

	function dropDubLane(event: DragEvent, lane: number) {
		if (draggedDubLane === null) return;
		event.preventDefault();
		event.stopPropagation();
		if (
			draggedDubLane !== lane
			&& !dubLaneStates[String(draggedDubLane)]?.locked
			&& !dubLaneStates[String(lane)]?.locked
		) onDubLaneOrderChange(draggedDubLane, lane);
		endDubLaneReorder();
	}

	function endDubLaneReorder() {
		draggedDubLane = null;
		dragOverDubLane = null;
	}

	async function requestOriginalAudioRecovery() {
		if (extractingAudio || !canAttemptOriginalRecovery) return;
		await onRestoreOriginalAudio();
		originalWaveformRevision += 1;
	}

	function clipWaveformSrc(clip: VideoLocalizationTimelineClip, trackId: VideoLocalizationTrackId) {
		const request = clipWaveformRequest(clip);
		return request
			? clipWaveformSrcForBins(clip, trackId, request.bins, request)
			: '';
	}

	function clipWaveformPreviewSrc(
		clip: VideoLocalizationTimelineClip,
		trackId: VideoLocalizationTrackId
	) {
		const url = timelineClipPreviewWaveformUrl(projectId, clip);
		if (trackId !== 'original' || !url || !originalWaveformRevision) return url;
		return `${url}&recovery=${originalWaveformRevision}`;
	}

	function overviewWaveformClip(trackId: VideoLocalizationTrackId) {
		const clips = clipsForTrack(trackId);
		return clips.length === 1 ? clips[0] : null;
	}

	function overviewWaveformSrc(trackId: VideoLocalizationTrackId) {
		const clip = overviewWaveformClip(trackId);
		return clip
			? clipWaveformSrcForBins(clip, trackId, OVERVIEW_WAVEFORM_BINS)
			: '';
	}

	function clipWaveformSrcForBins(
		clip: VideoLocalizationTimelineClip,
		trackId: VideoLocalizationTrackId,
		bins: number,
		window: { startMs: number; endMs: number } | null = null
	) {
		const url = timelineClipWaveformUrl(projectId, clip, bins, window);
		if (trackId !== 'original' || !url || !originalWaveformRevision) return url;
		return `${url}&recovery=${originalWaveformRevision}`;
	}

	function clipWaveformRequest(clip: VideoLocalizationTimelineClip) {
		const time = timelineClipTime(clip);
		const pixelRatio = typeof window === 'undefined' ? 1 : window.devicePixelRatio;
		return resolveVisibleWaveformRequest({
			clipStartMs: time.start_ms,
			clipEndMs: time.end_ms,
			sourceStartMs: time.source_start_ms ?? 0,
			sourceEndMs: time.source_end_ms ?? null,
			timelineDurationMs,
			zoom: timelineZoom,
			scrollLeft: timelineScrollLeft,
			viewportWidth: timelineViewportWidth,
			devicePixelRatio: pixelRatio
		});
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

	function dubLaneName(lane: number) {
		return dubLaneState(lane).label?.trim() || `合成配音 ${lane + 1}`;
	}

	function beginDubLaneRename(lane: number) {
		if (dubLaneState(lane).locked) return;
		editingDubLane = lane;
		editingDubLaneValue = dubLaneName(lane);
		requestAnimationFrame(() => {
			const input = document.querySelector<HTMLInputElement>(`[data-dub-lane-name="${lane}"]`);
			input?.focus();
			input?.select();
		});
	}

	function finishDubLaneRename(lane: number, save = true) {
		if (save) {
			const label = editingDubLaneValue.trim();
			onDubLaneStateChange(lane, { label: label && label !== `合成配音 ${lane + 1}` ? label : undefined });
		}
		editingDubLane = null;
	}

	function handleDubLaneNameKeydown(event: KeyboardEvent, lane: number) {
		if (event.key === 'Enter') {
			event.preventDefault();
			finishDubLaneRename(lane);
		} else if (event.key === 'Escape') {
			event.preventDefault();
			finishDubLaneRename(lane, false);
		}
	}

	function volumeDbLabel(trackId: VideoLocalizationTrackId) {
		return `${gainToDb(trackStates[trackId].volume).toFixed(1)} dB`;
	}

	function updateTrackDb(trackId: VideoLocalizationTrackId, db: number) {
		onTrackStateChange(trackId, { volume: dbToGain(db) });
	}

	function updateDubLaneDb(lane: number, db: number) {
		onDubLaneStateChange(lane, { volume: dbToGain(db) });
	}

	function beginDubLaneVolumeEdit(lane: number) {
		openDubLaneVolume = lane;
	}

	function beginDubLaneVolumeScrub(event: PointerEvent, lane: number) {
		event.preventDefault();
		event.stopPropagation();
		const startX = event.clientX;
		const startDb = gainToDb(dubLaneState(lane).volume, 2);
		let moved = false;
		const move = (moveEvent: PointerEvent) => {
			const delta = moveEvent.clientX - startX;
			if (Math.abs(delta) >= 2) moved = true;
			if (moved) updateDubLaneDb(lane, Math.round(Math.max(-60, Math.min(12, startDb + delta * 0.1)) * 10) / 10);
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
			if (!moved) beginDubLaneVolumeEdit(lane);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
	}

	function beginVolumeEdit(trackId: VideoLocalizationTrackId) {
		openVolumeTrack = trackId;
	}

	function finishVolumeEdit(trackId: VideoLocalizationTrackId) {
		if (openVolumeTrack === trackId) openVolumeTrack = null;
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
		const startDb = gainToDb(trackStates[trackId].volume, 2);
		let moved = false;
		const move = (moveEvent: PointerEvent) => {
			const delta = moveEvent.clientX - startX;
			if (Math.abs(delta) >= 2) moved = true;
			if (!moved) return;
			updateTrackDb(trackId, Math.round(Math.max(-60, Math.min(12, startDb + delta * 0.1)) * 10) / 10);
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
			if (!moved) volumeClickTimer = setTimeout(() => beginVolumeEdit(trackId), 220);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
	}

	function scaleTimelineAtPointer(scale: number, clientX: number | undefined, rounding: number) {
		suspendAutoFollow();
		if (!trackCanvasEl) {
			onTimelineZoomChange(Math.max(1, Math.min(1200, Math.round(timelineZoom * scale * rounding) / rounding)));
			return;
		}
		updateTimelineViewport(trackCanvasEl);
		const rect = trackCanvasEl.getBoundingClientRect();
		const pointerX = clientX === undefined ? rect.width / 2 : Math.max(0, Math.min(rect.width, clientX - rect.left));
		const anchorRatio = (trackCanvasEl.scrollLeft + pointerX) / Math.max(1, timelineContentPixelWidth(trackCanvasEl.clientWidth, timelineZoom));
		onTimelineZoomChange(Math.max(1, Math.min(1200, Math.round(timelineZoom * scale * rounding) / rounding)));
		void tick().then(() => requestAnimationFrame(() => {
			if (!trackCanvasEl) return;
			updateTimelineViewport(trackCanvasEl);
			trackCanvasEl.scrollLeft = Math.max(0, anchorRatio * timelineContentPixelWidth(trackCanvasEl.clientWidth, timelineZoom) - pointerX);
			updateTimelineViewport(trackCanvasEl);
			scheduleTimelineViewportChange(trackCanvasEl);
		}));
	}

	function zoomTimelineAtPointer(delta: number, clientX?: number) {
		const scale = delta > 0 ? 1.35 : 1 / 1.35;
		scaleTimelineAtPointer(scale, clientX, 10);
	}

	function pinchTimelineAtPointer(deltaY: number, clientX: number) {
		scaleTimelineAtPointer(trackpadPinchZoomScale(deltaY), clientX, 1000);
	}

	function handleTimelineKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		if (target?.closest('input,textarea,select,[contenteditable="true"]')) return;
		const timelineOwnsKeyboard = Boolean(
			target?.closest('[data-timeline-keyboard-scope]')
		);
		if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
			event.preventDefault();
			if (event.shiftKey) onRedoTimelineClip();
			else onUndoTimelineClip();
		} else if (event.key === 'Home') {
			event.preventDefault();
			onTransportAction('start');
		} else if (event.key === 'End') {
			event.preventDefault();
			onTransportAction('end');
		} else if (event.shiftKey && event.key === 'ArrowLeft') {
			event.preventDefault();
			onTransportAction('previous-boundary');
		} else if (event.shiftKey && event.key === 'ArrowRight') {
			event.preventDefault();
			onTransportAction('next-boundary');
		} else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
			event.preventDefault();
			endHoverScrub();
			onSeekTimeline(stepFrameTime(currentTimeMs, event.key === 'ArrowLeft' ? -1 : 1, timelineFrameRate, timelineDurationMs));
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
		} else if (event.key.toLowerCase() === 'h') {
			event.preventDefault();
			onHoverScrubChange?.(!hoverScrubEnabled);
		} else if (event.key.toLowerCase() === 'c') {
			event.preventDefault();
			activateTimelineTool(activeTool === 'razor' ? 'select' : 'razor');
		} else if (event.shiftKey && event.key.toLowerCase() === 'm') {
			event.preventDefault();
			void mergeSelectedTimelineSubtitles();
		} else if (timelineDeleteShortcutAllowed(event.key, timelineOwnsKeyboard, canDeleteSelectedItems)) {
			event.preventDefault();
			void deleteSelectedTimelineItems();
		} else if (event.key.toLowerCase() === 'r') {
			event.preventDefault();
			if (canEditSelectedCue) setRangeFromSelectedCue();
		} else if (event.key.toLowerCase() === 'v') {
			event.preventDefault();
			activateTimelineTool('select');
		} else if (event.key.toLowerCase() === 'g') {
			event.preventDefault();
			handleRangeAction(onGenerateToSelection);
		} else if (event.key.toLowerCase() === 'e') {
			event.preventDefault();
			if (!timelineOwnsKeyboard && hasRecoverableVideo && !hasSourceAudio && !extractingAudio) onExtractAudio();
		} else if (event.key === 'Escape') {
			cancelTtsHistoryPointer();
			cancelTimelinePointerWork();
			activeTool = 'select';
			openVolumeTrack = null;
			timelineContextMenu = null;
		}
	}

	function trackRuntimeBusy(trackId: VideoLocalizationTrackId, itemId?: string) {
		return activityTasks.some((task) => activityTaskAffectsTrack(task, trackId, itemId));
	}

	function trackInteractionLocked(trackId: VideoLocalizationTrackId, itemId?: string) {
		if (itemId) {
			const clip = draft?.timeline_clips.find((candidate) => candidate.clip_id === itemId);
			if (clip && isTimelineRuntimeClip(clip)) return true;
		}
		if (trackId === 'dub') {
			const lane = itemId ? (dubTrackLayout.laneByClipId.get(itemId) ?? 0) : 0;
			return dubLaneInteractionLocked(lane, itemId);
		}
		return trackStates[trackId].locked || trackRuntimeBusy(trackId, itemId);
	}

	function dubLaneInteractionLocked(lane: number, itemId?: string) {
		return dubLaneState(lane).locked || trackRuntimeBusy('dub', itemId);
	}

	function setSelectionPoint(edge: 'start' | 'end', requestedTimeMs = currentTimeMs) {
		const timeMs = snapTimeToFrame(requestedTimeMs, timelineFrameRate, 'nearest', 0, timelineDurationMs);
		if (edge === 'start') {
			rangeStartMs = Math.min(timeMs, (rangeEndMs ?? timelineDurationMs) - MIN_RANGE_DURATION_MS);
			if (rangeEndMs !== null && rangeEndMs <= rangeStartMs) rangeEndMs = null;
			return;
		}
		rangeEndMs = Math.max(timeMs, (rangeStartMs ?? 0) + MIN_RANGE_DURATION_MS);
	}

	function clearSelection() {
		if (gestureSession.is('range-create') || gestureSession.is('selection-handle')) {
			gestureSession = gestureSession.cancel();
		}
		rangeStartMs = null;
		rangeEndMs = null;
	}

	function currentSelectionRange() {
		if (!hasRangeSelection) return null;
		return {
			startMs: Math.min(rangeStartValue, rangeEndValue),
			endMs: Math.max(rangeStartValue, rangeEndValue)
		};
	}

	function setRangeFromSelectedCue() {
		const range = ttsSourceSelectionRange(ttsSelectionSession, draft?.cues ?? [])
			?? (selectedCue && selectedCue.start_ms !== null && selectedCue.end_ms !== null
				? { startMs: selectedCue.start_ms, endMs: selectedCue.end_ms }
				: null);
		if (!range) return;
		rangeStartMs = range.startMs;
		rangeEndMs = Math.max(range.startMs + MIN_RANGE_DURATION_MS, range.endMs);
	}

	function handleRangeAction(action: (startMs: number, endMs: number) => void) {
		if (!hasRangeSelection) return;
		action(Math.min(rangeStartValue, rangeEndValue), Math.max(rangeStartValue, rangeEndValue));
	}

	function selectSemanticGroupByNumber() {
		const bounded = Math.max(1, Math.min(
			semanticTtsGroups.length,
			Math.floor(Number(semanticGroupNumber) || 1)
		));
		semanticGroupNumber = bounded;
		const group = semanticTtsGroupAtOrdinal(semanticTtsGroups, bounded);
		if (group) onSelectSemanticTtsGroup?.(group.group_id);
	}

	function estimateMasterLevel() {
		const dubLevels = dubTrackLanes.map((_, lane) => levelForTrack('dub', lane));
		const level = Math.max(levelForTrack('original'), levelForTrack('vocals'), levelForTrack('background'), ...dubLevels, 0);
		if (level <= 0.0001) return 0;
		const db = 20 * Math.log10(level);
		return Math.max(0, Math.min(1.08, (db + 60) / 66));
	}

	function levelForTrack(trackId: VideoLocalizationTrackId, dubLane = 0) {
		if (!trackAudible(trackId, dubLane)) return 0;
		if (clipsForTrack(trackId).length) return levelForClipTrack(trackId, dubLane);
		return 0;
	}

	function levelForClipTrack(trackId: VideoLocalizationTrackId, dubLane = 0) {
		const clip = clipsForTrack(trackId).find((item) => {
			if (trackId === 'dub' && (dubTrackLayout.laneByClipId.get(item.clip_id) ?? 0) !== dubLane) return false;
			const time = timelineClipTime(item);
			return currentTimeMs >= time.start_ms && currentTimeMs < time.end_ms;
		});
		if (!clip) return 0;
		const analysis = dubWaveforms[clip.clip_id];
		if (!analysis?.bars.length) return 0;
		const live = timelineClipTime(clip);
		const sourceStart = live.source_start_ms ?? 0;
		const localMs = sourceStart + Math.max(0, currentTimeMs - live.start_ms);
		const durationMs = Math.max(1, analysis.durationSeconds * 1000);
		const peak = waveformMeterLevelAt(analysis.bars, localMs, durationMs);
		const volume = trackId === 'dub' ? dubLaneState(dubLane).volume : trackStates[trackId].volume;
		return peak * (volume ?? 1);
	}

	function clipsForTrack(trackId: VideoLocalizationTrackId) {
		if (trackId === 'subtitles' || trackId === 'localizedSubtitles') return [];
		return clipsByTrack[trackId];
	}

	function visibleClipsForTrack(trackId: VideoLocalizationTrackId) {
		return clipsForTrack(trackId).filter((clip) => {
			const time = timelineClipTime(clip);
			return clipDragState?.clipId === clip.clip_id || timeRangeIntersectsViewport(time.start_ms, time.end_ms, renderViewport);
		});
	}

	function visibleDubLaneClips(laneIndex: number) {
		return (dubTrackLanes[laneIndex] ?? []).filter((clip) => {
			const time = timelineClipTime(clip);
			return clipDragState?.clipId === clip.clip_id || timeRangeIntersectsViewport(time.start_ms, time.end_ms, renderViewport);
		});
	}

	const visibleAudioClipIds = $derived.by(() => new Set([
		...visibleClipsForTrack('original'),
		...visibleClipsForTrack('vocals'),
		...visibleClipsForTrack('background'),
		...dubTrackLanes.flatMap((_, laneIndex) => visibleDubLaneClips(laneIndex))
	].map((clip) => clip.clip_id)));

	$effect(() => {
		const retained: Record<string, { bars: number[]; durationSeconds: number }> = {};
		let changed = false;
		for (const [clipId, analysis] of Object.entries(dubWaveforms)) {
			if (visibleAudioClipIds.has(clipId)) retained[clipId] = analysis;
			else changed = true;
		}
		if (changed) dubWaveforms = retained;
	});

	function historyDropDurationMs(item: HistoryItem) {
		const duration = Number(item.duration_ms);
		return Number.isFinite(duration) && duration > 0
			? Math.max(audioClipMinimumDurationMs, Math.round(duration))
			: 1800;
	}

	function historyDropLaneFromPointer(event: PointerEvent | MouseEvent) {
		if (!timelineContentEl) return null;
		const hit = document.elementFromPoint(event.clientX, event.clientY);
		const row = hit?.closest<HTMLElement>('[data-track-row][data-track-id="dub"][data-dub-lane]');
		if (!row || !timelineContentEl.contains(row)) return null;
		const lane = Number(row.dataset.dubLane);
		return Number.isFinite(lane) ? Math.max(0, Math.floor(lane)) : null;
	}

	function updateHistoryDropPreview(event: PointerEvent | MouseEvent, requestedLane: number) {
		if (!draggingTtsHistory || dubLaneInteractionLocked(requestedLane)) {
			historyDropPreview = null;
			historyDropTargetActive = false;
			return;
		}
		const duration = historyDropDurationMs(draggingTtsHistory);
		const maxStart = Math.max(0, timelineDurationMs - Math.min(duration, timelineDurationMs));
		const startMs = snapTimeToFrame(rawTimeFromPointer(event), timelineFrameRate, 'nearest', 0, maxStart);
		const endMs = startMs + duration;
		const clips = clipsForTrack('dub').map((clip) => ({ ...clip, ...timelineClipTime(clip) }));
		const lockedLanes = dubTrackLanes
			.map((_, lane) => lane)
			.filter((lane) => dubLaneState(lane).locked);
		const resolvedLane = resolveDubHistoryDropLane(clips, startMs, endMs, requestedLane, lockedLanes);
		if (dubLaneInteractionLocked(resolvedLane)) {
			historyDropPreview = null;
			historyDropTargetActive = false;
			return;
		}
		historyDropPreview = {
			startMs,
			endMs,
			lane: resolvedLane
		};
		historyDropTargetActive = true;
	}

	function moveTtsHistoryPointer(event: PointerEvent) {
		if (!draggingTtsHistory) return;
		const requestedLane = historyDropLaneFromPointer(event);
		if (requestedLane === null || dubLaneInteractionLocked(requestedLane)) {
			historyDropPreview = null;
			historyDropTargetActive = false;
			return;
		}
		updateHistoryDropPreview(event, requestedLane);
	}

	async function endTtsHistoryPointer(event: PointerEvent | MouseEvent) {
		if (!draggingTtsHistory || historyDropCommitting) return;
		const requestedLane = historyDropLaneFromPointer(event);
		if (requestedLane !== null && !dubLaneInteractionLocked(requestedLane)) updateHistoryDropPreview(event, requestedLane);
		const preview = historyDropTargetActive && historyDropPreview && !dubLaneInteractionLocked(historyDropPreview.lane)
			? historyDropPreview
			: null;
		const item = draggingTtsHistory;
		historyDropPreview = null;
		historyDropTargetActive = false;
		if (!preview) {
			onEndTtsHistoryDrag?.();
			return;
		}
		historyDropCommitting = true;
		try {
			await onDropTtsHistory?.(item, preview.startMs, preview.lane);
		} finally {
			historyDropCommitting = false;
		}
	}

	function cancelTtsHistoryPointer() {
		if (!draggingTtsHistory) return;
		historyDropPreview = null;
		historyDropTargetActive = false;
		onEndTtsHistoryDrag?.();
	}

	function clipLabel(clip: VideoLocalizationTimelineClip, trackId: VideoLocalizationTrackId) {
		if (trackId === 'dub') return timelineDubClipLabel(clip, draft?.timeline_clips ?? [clip]);
		return trackName(trackId);
	}

	function clipTone(trackId: VideoLocalizationTrackId): 'source' | 'vocals' | 'music' | 'dub' {
		if (trackId === 'original') return 'source';
		if (trackId === 'vocals') return 'vocals';
		if (trackId === 'background') return 'music';
		return 'dub';
	}

	function clipSourceDurationMs(clip: VideoLocalizationTimelineClip, time = timelineClipTime(clip)) {
		const analyzedDuration = (dubWaveforms[clip.clip_id]?.durationSeconds ?? 0) * 1000;
		const declaredCandidates = [clip.source_duration_ms, clip.audio_duration_ms, clip.duration_ms]
			.map((value) => Number(value))
			.filter((value) => Number.isFinite(value) && value > 0);
		const currentSourceEnd = time.source_end_ms ?? (time.source_start_ms ?? 0) + Math.max(0, time.end_ms - time.start_ms);
		return Math.max(currentSourceEnd, analyzedDuration, ...declaredCandidates, 0);
	}

	function activateTimelineTool(tool: TimelineTool) {
		activeTool = tool;
		if (tool === 'razor') {
			clearSelection();
			endCueDrag();
			cancelClipDrag();
		}
	}

	function razorSplitMs(event: PointerEvent) {
		const rawTimeMs = rawTimeFromPointer(event);
		return frameCoverage(rawTimeMs, timelineFrameRate, timelineDurationMs).startMs;
	}

	function cutAudioClipAtPointer(event: PointerEvent, clip: VideoLocalizationTimelineClip) {
		if (activeTool !== 'razor' || trackInteractionLocked(clip.track_id as VideoLocalizationTrackId, clip.clip_id)) return;
		const splitMs = razorSplitMs(event);
		const time = timelineClipTime(clip);
		if (
			splitMs < time.start_ms + audioClipMinimumDurationMs
			|| splitMs > time.end_ms - audioClipMinimumDurationMs
		) return;
		onSplitTimelineClip?.(clip.clip_id, splitMs);
	}

	function cutSubtitleAtPointer(event: PointerEvent, track: SubtitleTrackKind, itemId: string) {
		if (activeTool !== 'razor') return false;
		event.preventDefault();
		event.stopPropagation();
		const splitMs = razorSplitMs(event);
		if (track === 'localized') {
			const subtitle = draft?.localized_subtitles.find((item) => item.subtitle_id === itemId);
			if (!subtitle || trackInteractionLocked('localizedSubtitles', itemId)) return true;
			if (splitMs < subtitle.start_ms + MIN_SUBTITLE_DURATION_MS || splitMs > subtitle.end_ms - MIN_SUBTITLE_DURATION_MS) return true;
			onSplitLocalizedSubtitle?.(itemId, splitMs);
			return true;
		}
		const cue = draft?.cues.find((item) => item.cue_id === itemId);
		if (!cue || cue.start_ms === null || cue.end_ms === null || trackInteractionLocked('subtitles', itemId)) return true;
		if (splitMs < cue.start_ms + MIN_SUBTITLE_DURATION_MS || splitMs > cue.end_ms - MIN_SUBTITLE_DURATION_MS) return true;
		onSplitCue(itemId, splitMs);
		return true;
	}

	function trackMeterPercent(trackId: VideoLocalizationTrackId, dubLane = 0) {
		const state = trackId === 'dub' ? dubLaneState(dubLane) : trackStates[trackId];
		if (!isPlaying || state?.muted) return 0;
		const level = levelForTrack(trackId, dubLane);
		if (level <= 0.0001) return 0;
		return Math.max(0, Math.min(100, ((20 * Math.log10(level) + 60) / 60) * 100));
	}

	function trackAudible(trackId: VideoLocalizationTrackId, dubLane = 0) {
		if (!trackHasMedia(trackId)) return false;
		if (trackId === 'dub') return audibleMix.audibleDubLaneIds.includes(dubLane);
		if (trackId === 'subtitles' || trackId === 'localizedSubtitles') return false;
		return audibleMix.audibleTrackIds.includes(trackId);
	}

	function trackHasMedia(trackId: VideoLocalizationTrackId) {
		if (trackId === 'subtitles' || trackId === 'localizedSubtitles') return false;
		return clipsForTrack(trackId).length > 0;
	}

	function updateDubWaveform(clipId: string, bars: number[], durationSeconds: number) {
		dubWaveforms[clipId] = { bars, durationSeconds };
	}

	function seekFromPointer(event: PointerEvent | MouseEvent) {
		scheduleTimelineSeek(timeFromPointer(event));
	}

	function scheduleTimelineSeek(timeMs: number, flush = false) {
		pendingSeekMs = snapTimeToFrame(timeMs, timelineFrameRate, 'nearest', 0, lastPlayableFrameMs);
		if (flush) {
			if (seekAnimationFrame) cancelAnimationFrame(seekAnimationFrame);
			seekAnimationFrame = 0;
			onSeekTimeline(pendingSeekMs, false);
			return;
		}
		if (seekAnimationFrame) return;
		seekAnimationFrame = requestAnimationFrame(() => {
			seekAnimationFrame = 0;
			onSeekTimeline(pendingSeekMs, true);
		});
	}

	function handleTimelinePointerDown(event: PointerEvent) {
		(event.currentTarget as HTMLElement).focus({ preventScroll: true });
		const target = event.target as HTMLElement;
		const audioClipElement = target.closest<HTMLElement>('[data-audio-clip-id]');
		if (activeTool === 'razor' && event.button === 0 && audioClipElement && !target.closest('.clip-handle')) {
			event.preventDefault();
			event.stopPropagation();
			const clip = draft?.timeline_clips.find((item) => item.clip_id === audioClipElement.dataset.audioClipId);
			if (clip) cutAudioClipAtPointer(event, clip);
			return;
		}
		if (activeTool === 'razor' && event.button === 0 && target.closest('[data-track-row]')) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.button === 0 && audioClipElement && (event.ctrlKey || event.metaKey)) {
			event.preventDefault();
			event.stopPropagation();
			const clip = draft?.timeline_clips.find((item) => item.clip_id === audioClipElement.dataset.audioClipId);
			if (clip) toggleTimelineItemSelection({ kind: 'audio', trackId: clip.track_id as VideoLocalizationTrackId, itemId: clip.clip_id });
			return;
		}
		if (event.button === 0 && audioClipElement && !target.closest('.clip-handle,.clip-label')) {
			const clip = draft?.timeline_clips.find((item) => item.clip_id === audioClipElement.dataset.audioClipId);
			if (clip) {
				selectAudioTimelineItem(clip.track_id as VideoLocalizationTrackId, clip.clip_id);
				startClipDrag(event, clip, 'move');
				return;
			}
		}
		const overTrack = Boolean(target.closest('[data-track-row]'));
		const overTimeline = Boolean(target.closest('.timeline-ruler') || overTrack);
		const interactive = isTimelineInteractiveTarget(target);
		let pressDetail = event.detail;
		if (event.button === 0 && overTrack && !interactive) {
			const now = performance.now();
			const previous = lastTrackPrimaryPress;
			const repeated = isRepeatedPrimaryPress({
				detail: event.detail,
				elapsedMs: previous ? now - previous.at : null,
				distancePx: previous ? Math.hypot(event.clientX - previous.x, event.clientY - previous.y) : null
			});
			pressDetail = repeated ? 2 : 1;
			lastTrackPrimaryPress = repeated ? null : { at: now, x: event.clientX, y: event.clientY };
		}
		const intent = timelinePointerIntent({
			button: event.button,
			detail: pressDetail,
			overTimeline,
			overTrack,
			interactive
		});
		if (intent === 'marquee-select') {
			event.preventDefault();
			suspendAutoFollow();
			const point = timelineLocalPoint(event);
			gestureSession = gestureSession.begin({
				kind: 'marquee',
				startX: point.x,
				startY: point.y,
				currentX: point.x,
				currentY: point.y,
				moved: false,
				additive: event.ctrlKey || event.metaKey,
				baseItems: event.ctrlKey || event.metaKey ? [...selectedTimelineItems] : []
			});
			(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
			return;
		}
		if (intent === 'range-create') {
			event.preventDefault();
			suspendAutoFollow();
			const startMs = timeFromPointer(event);
			gestureSession = gestureSession.begin({
				kind: 'range-create',
				startX: event.clientX,
				startMs,
				moved: false
			});
			(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
			return;
		}
		if (intent === 'pan') {
			suspendAutoFollow();
			event.preventDefault();
			gestureSession = gestureSession.begin({
				kind: 'pan',
				startX: event.clientX,
				scrollLeft: trackCanvasEl?.scrollLeft ?? 0
			});
			(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
			return;
		}
		if (intent !== 'seek') return;
		clearTimelineItemSelection();
		event.preventDefault();
		suspendAutoFollow();
		gestureSession = gestureSession.begin({ kind: 'seek' });
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		seekFromPointer(event);
	}

	function handleTimelineDoubleClick(event: MouseEvent) {
		if (isTimelineInteractiveTarget(event.target as HTMLElement)) return;
		event.preventDefault();
		if (suppressNextTimelineDoubleClick) {
			suppressNextTimelineDoubleClick = false;
			return;
		}
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
		if (marqueeState) moveMarqueeSelection(event);
		if (timelineSeekDrag) seekFromPointer(event);
		if (selectionDrag) moveSelectionHandle(event);
		if (!timelinePanState && !dragState && !clipDragState && !rangeCreateState && !marqueeState && !timelineSeekDrag && !selectionDrag) {
			updateHoverScrub(event);
		}
	}

	function updateHoverScrub(event: PointerEvent) {
		if (isPlaying || !(event.target as HTMLElement).closest('[data-track-row],.timeline-ruler') || (!hoverScrubEnabled && activeTool !== 'razor')) {
			endHoverScrub();
			return;
		}
		hoverTimeMs = rawTimeFromPointer(event);
		if (!hoverScrubEnabled) return;
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
		if (!selectedTimelineItem && !selectedTimelineItems.length && !selectedCueId) return;
		onSelectAudioClip?.(null);
		onClearCueSelection?.();
		onTimelineSelectionChange?.([]);
	}

	function timelineItemSelected(item: TimelineSelectionItem) {
		return selectionSession.contains(item);
	}

	function setTimelineSelection(
		items: TimelineSelectionItem[],
		ttsAdditive = false,
		ttsClickedItem: TimelineSelectionItem | null = null
	) {
		const nextSelection = selectionSession.set(items);
		const nextItems = nextSelection.items;
		const nextPrimary = nextSelection.primary;
		onTimelineSelectionChange?.(nextItems);
		const ttsAnchorItem = ttsClickedItem ?? nextPrimary;
		if (ttsAnchorItem && nextItems.length) {
			syncTtsSelectionAnchor(ttsAnchorItem, nextItems, ttsAdditive);
		}
		if (nextPrimary) {
			syncPrimaryTimelineSelection(nextPrimary);
		}
		else {
			onSelectAudioClip?.(null);
			onClearCueSelection?.();
		}
	}

	function syncTtsSelectionAnchor(item: TimelineSelectionItem, items: TimelineSelectionItem[], additive = false) {
		if (item.kind !== 'subtitle') return;
		onTtsSelectionAnchorChange?.({
			kind: item.trackId === 'subtitles' ? 'source' : 'target',
			itemId: item.itemId
		}, items, additive);
	}

	function toggleTimelineItemSelection(item: TimelineSelectionItem) {
		const clickedAnchor = item.kind === 'subtitle'
			? {
					kind: item.trackId === 'subtitles' ? 'source' : 'target',
					itemId: item.itemId
				} satisfies TtsSelectionAnchor
			: null;
		const keepPassiveItemOutOfEditSelection = clickedAnchor
			&& !timelineItemSelected(item)
			&& ttsSelectionAnchorIsPassive(ttsSelectionSession, clickedAnchor);
		const nextSelection = selectionSession.toggle(item, {
			includeWhenMissing: !keepPassiveItemOutOfEditSelection
		});
		if (hasRangeSelection) preserveRangeThroughCueSync();
		setTimelineSelection(nextSelection.items, true, item);
	}

	function preserveRangeThroughCueSync() {
		preserveRangeOnCueSelection = true;
		void tick().then(() => {
			preserveRangeOnCueSelection = false;
		});
	}

	function syncPrimaryTimelineSelection(item: TimelineSelectionItem) {
		if (item.kind === 'audio') {
			onClearCueSelection?.();
			onSelectAudioClip?.(item.itemId);
			return;
		}
		onSelectAudioClip?.(null);
		if (item.trackId === 'subtitles') onSelectCue(item.itemId);
		else {
			onClearCueSelection?.();
			onSelectLocalizedSubtitle?.(item.itemId);
		}
	}

	function selectSubtitleTimelineItem(trackKind: SubtitleTrackKind, itemId: string, preserveRange = false, additive = false) {
		const trackId = trackKind === 'asr' ? 'subtitles' : 'localizedSubtitles';
		const item: TimelineSelectionItem = { kind: 'subtitle', trackId, itemId };
		if (additive) {
			toggleTimelineItemSelection(item);
			return;
		}
		const nextItems = selectionSession.set([item]).items;
		onTimelineSelectionChange?.(nextItems);
		syncTtsSelectionAnchor(item, nextItems);
		onSelectAudioClip?.(null);
		if (preserveRange) preserveRangeThroughCueSync();
		if (trackKind === 'asr') {
			onSelectCue(itemId);
		} else {
			onClearCueSelection?.();
			onSelectLocalizedSubtitle?.(itemId);
		}
	}

	function subtitleCueAtPointer(
		event: PointerEvent | MouseEvent,
		trackKind: SubtitleTrackKind,
		domTargetCue: SubtitleTimelineItem
	) {
		const cues: SubtitleTimelineItem[] = trackKind === 'asr'
			? (draft?.cues ?? [])
			: (draft?.localized_subtitles ?? []);
		const domTargetItemId = subtitleItemId(domTargetCue);
		const resolvedItemId = resolveTimelineSubtitleHit(
			rawTimeFromPointer(event),
			domTargetItemId,
			cues.map((cue) => {
				const time = cueLiveTime(cue, trackKind);
				return {
					itemId: subtitleItemId(cue),
					startMs: time.start_ms,
					endMs: time.end_ms
				};
			})
		);
		return cues.find((cue) => subtitleItemId(cue) === resolvedItemId) ?? domTargetCue;
	}

	function selectSubtitleTimelineItemAtPointer(
		event: MouseEvent,
		trackKind: SubtitleTrackKind,
		domTargetCue: SubtitleTimelineItem
	) {
		// Pointerdown owns mouse selection. Its inspector update can change layout
		// before click; keyboard activation instead selects the focused DOM cue.
		if (event.detail !== 0 || dragState || event.ctrlKey || event.metaKey) return;
		const itemId = subtitleItemId(domTargetCue);
		const trackId = trackKind === 'asr' ? 'subtitles' : 'localizedSubtitles';
		if (!timelineItemSelected({ kind: 'subtitle', trackId, itemId })) {
			selectSubtitleTimelineItem(trackKind, itemId);
		}
	}

	function playSubtitleTimelineItem(event: MouseEvent, trackKind: SubtitleTrackKind, cue: SubtitleTimelineItem) {
		event.preventDefault();
		event.stopPropagation();
		cue = subtitleCueAtPointer(event, trackKind, cue);
		const itemId = subtitleItemId(cue);
		const range = cueLiveTime(cue, trackKind);
		selectSubtitleTimelineItem(trackKind, itemId, true);
		rangeStartMs = range.start_ms;
		rangeEndMs = Math.max(range.start_ms + MIN_RANGE_DURATION_MS, range.end_ms);
		onSelectionRangeCommit?.({ startMs: rangeStartMs, endMs: rangeEndMs });
	}

	function selectAudioTimelineItem(trackId: VideoLocalizationTrackId, itemId: string, additive = false) {
		const item: TimelineSelectionItem = { kind: 'audio', trackId, itemId };
		if (additive) {
			toggleTimelineItemSelection(item);
			return;
		}
		onTimelineSelectionChange?.(selectionSession.set([item]).items);
		onClearCueSelection?.();
		onSelectAudioClip?.(itemId);
	}

	function selectOverviewSubtitle(
		event: MouseEvent | KeyboardEvent,
		trackKind: SubtitleTrackKind,
		item: { id?: string; editable?: boolean }
	) {
		if (!item.id) return;
		if (item.editable) {
			selectSubtitleTimelineItem(trackKind, item.id, false, event.ctrlKey || event.metaKey);
			return;
		}
		const displayCue = (trackKind === 'asr'
			? subtitleDisplay.tracks.asr.cues
			: subtitleDisplay.tracks.localized.cues
		).find((cue) => cue.id === item.id);
		if (displayCue) onSelectSubtitleDisplayCue(displayCue);
	}

	function selectOverviewAudio(
		event: MouseEvent | KeyboardEvent,
		trackId: VideoLocalizationTrackId,
		item: { clip_id?: string }
	) {
		if (item.clip_id) selectAudioTimelineItem(trackId, item.clip_id, event.ctrlKey || event.metaKey);
	}

	async function deleteSubtitleTimelineItem(track: SubtitleTrackKind, itemId: string) {
		await onDeleteSubtitleItem(track, itemId);
		setTimelineSelection(selectedTimelineItems.filter((item) => !(item.kind === 'subtitle' && item.itemId === itemId)));
	}

	async function deleteAudioTimelineItem(itemId: string) {
		if (!(await onDeleteTimelineClip(itemId))) return;
		setTimelineSelection(selectedTimelineItems.filter((item) => !(item.kind === 'audio' && item.itemId === itemId)));
	}

	function selectionContainsLockedItem(items: TimelineSelectionItem[]) {
		return items.some((item) => trackInteractionLocked(item.trackId, item.itemId));
	}

	function timelineItemTtsWorkflowMarker(item: TimelineSelectionItem) {
		if (item.kind !== 'audio' || item.trackId !== 'dub') return null;
		const marker = draft?.timeline_clips.find((clip) => clip.clip_id === item.itemId)?.optimistic_tts_workflow_id;
		return typeof marker === 'string' && marker ? marker : null;
	}

	function timelineItemDeletionBlockedByState(item: TimelineSelectionItem) {
		const lane = item.trackId === 'dub'
			? (dubTrackLayout.laneByClipId.get(item.itemId) ?? 0)
			: 0;
		return timelineItemDeletionBlocked({
			kind: item.kind,
			trackId: item.trackId,
			trackLocked: trackStates[item.trackId].locked === true,
			laneLocked: item.trackId === 'dub' && dubLaneState(lane).locked === true,
			runtimeBusy: trackRuntimeBusy(item.trackId, item.itemId),
			ttsWorkflowMarker: timelineItemTtsWorkflowMarker(item)
		});
	}

	function selectionContainsDeletionBlockedItem(items: TimelineSelectionItem[]) {
		return items.some(timelineItemDeletionBlockedByState);
	}

	function contextMenuDeletionItems(target: TimelineContextMenuTarget) {
		const targetItem: TimelineSelectionItem | null = target.kind === 'subtitle-clip'
			? { kind: 'subtitle', trackId: target.trackId, itemId: target.itemId }
			: target.kind === 'audio-clip'
				? { kind: 'audio', trackId: target.trackId, itemId: target.itemId }
				: null;
		if (!targetItem) return selectedTimelineItems;
		return selectedTimelineItems.some((item) => selectionItemEqual(item, targetItem))
			? selectedTimelineItems
			: [targetItem];
	}

	async function deleteSelectedTimelineItems(items = selectedTimelineItems) {
		const deletable = uniqueTimelineSelection(items);
		if (!deletable.length || selectionContainsDeletionBlockedItem(deletable)) return;
		if (!(await onDeleteTimelineItems(deletable))) return;
		setTimelineSelection(selectedTimelineItems.filter((item) => !deletable.some((deleted) => selectionItemEqual(item, deleted))));
	}

	async function mergeSelectedTimelineSubtitles() {
		if (!canMergeSelectedSubtitles || !selectedSubtitleMerge || !onMergeTimelineSubtitles) return;
		const request = selectedSubtitleMerge;
		await onMergeTimelineSubtitles(request);
		setTimelineSelection([{
			kind: 'subtitle',
			trackId: request.track === 'asr' ? 'subtitles' : 'localizedSubtitles',
			itemId: request.itemIds[0]
		}]);
	}

	async function deleteTimelineTrack(trackId: VideoLocalizationTrackId) {
		const items: TimelineSelectionItem[] = trackId === 'subtitles'
			? (draft?.cues ?? []).map((cue) => ({ kind: 'subtitle', trackId, itemId: cue.cue_id }))
			: trackId === 'localizedSubtitles'
				? (draft?.localized_subtitles ?? []).map((cue) => ({ kind: 'subtitle', trackId, itemId: cue.subtitle_id }))
				: clipsForTrack(trackId).map((clip) => ({ kind: 'audio', trackId, itemId: clip.clip_id }));
		await deleteSelectedTimelineItems(items);
	}

	function isVideoDerivedAudioTrack(trackId: VideoLocalizationTrackId) {
		return trackId === 'original' || trackId === 'vocals' || trackId === 'background';
	}

	function restoreVideoDerivedClipAlignment(clipId: string) {
		const clip = draft?.timeline_clips.find((item) => item.clip_id === clipId);
		if (!clip || !isVideoDerivedAudioTrack(clip.track_id as VideoLocalizationTrackId)) return;
		const sourceStartMs = Math.max(0, clip.source_start_ms ?? 0);
		const clipDurationMs = Math.max(
			audioClipMinimumDurationMs,
			(clip.end_ms ?? sourceStartMs + audioClipMinimumDurationMs) - (clip.start_ms ?? 0)
		);
		const sourceEndMs = Math.max(
			sourceStartMs + audioClipMinimumDurationMs,
			clip.source_end_ms ?? sourceStartMs + clipDurationMs
		);
		liveClipTimes = {
			...liveClipTimes,
			[clipId]: { start_ms: sourceStartMs, end_ms: sourceEndMs, source_start_ms: sourceStartMs, source_end_ms: sourceEndMs }
		};
		onUpdateTimelineClip(clipId, sourceStartMs, sourceEndMs, sourceStartMs, sourceEndMs);
	}

	function restoreVideoDerivedTrackAlignment(trackId: VideoLocalizationTrackId) {
		if (!isVideoDerivedAudioTrack(trackId)) return;
		const clips = clipsForTrack(trackId);
		if (clips.length < 2 || !onMoveTimelineItems) {
			for (const clip of clips) restoreVideoDerivedClipAlignment(clip.clip_id);
			return;
		}
		const moves: TimelineGroupMoveCommitItem[] = clips.map((clip) => {
			const sourceStartMs = Math.max(0, clip.source_start_ms ?? 0);
			const clipDurationMs = Math.max(
				audioClipMinimumDurationMs,
				(clip.end_ms ?? sourceStartMs + audioClipMinimumDurationMs) - (clip.start_ms ?? 0)
			);
			const sourceEndMs = Math.max(
				sourceStartMs + audioClipMinimumDurationMs,
				clip.source_end_ms ?? sourceStartMs + clipDurationMs
			);
			liveClipTimes = {
				...liveClipTimes,
				[clip.clip_id]: {
					start_ms: sourceStartMs,
					end_ms: sourceEndMs,
					source_start_ms: sourceStartMs,
					source_end_ms: sourceEndMs
				}
			};
			return {
				kind: 'audio',
				trackId,
				itemId: clip.clip_id,
				startMs: sourceStartMs,
				endMs: sourceEndMs,
				sourceStartMs,
				sourceEndMs
			};
		});
		onMoveTimelineItems(moves);
	}

	function endTimelinePointerWork() {
		const committedRange = rangeCreateState?.moved ? currentSelectionRange() : null;
		const completedMarquee = marqueeState;
		const completedRangeCreate = rangeCreateState;
		const completedSeek = timelineSeekDrag;
		endCueDrag();
		endClipDrag();
		if (completedRangeCreate && !completedRangeCreate.moved) {
			clearTimelineItemSelection();
			scheduleTimelineSeek(completedRangeCreate.startMs, true);
		}
		if (completedSeek) scheduleTimelineSeek(pendingSeekMs, true);
		gestureSession = gestureSession.cancel();
		if (completedMarquee) finishMarqueeSelection(completedMarquee);
		if (committedRange) {
			suppressNextTimelineDoubleClick = true;
			onSelectionRangeCommit?.(committedRange);
		}
	}

	function cancelTimelinePointerWork() {
		cancelClipDrag();
		gestureSession = gestureSession.cancel();
		snapGuideMs = null;
	}

	function handleWindowBlur() {
		cancelTtsHistoryPointer();
		cancelTimelinePointerWork();
	}

	function timelineLocalPoint(event: PointerEvent) {
		if (!timelineContentEl) return { x: 0, y: 0 };
		const rect = timelineContentEl.getBoundingClientRect();
		return {
			x: Math.max(0, Math.min(rect.width, event.clientX - rect.left)),
			y: Math.max(0, Math.min(rect.height, event.clientY - rect.top))
		};
	}

	function moveMarqueeSelection(event: PointerEvent) {
		if (!marqueeState) return;
		const point = timelineLocalPoint(event);
		const moved = marqueeState.moved || Math.hypot(point.x - marqueeState.startX, point.y - marqueeState.startY) >= 4;
		const nextMarquee = { ...marqueeState, currentX: point.x, currentY: point.y, moved };
		gestureSession = gestureSession.update('marquee', () => nextMarquee);
		if (moved) applyMarqueeSelection(nextMarquee);
	}

	function finishMarqueeSelection(state: MarqueeState) {
		if (!state.moved || !timelineContentEl) {
			if (!state.additive) clearTimelineItemSelection();
			scheduleTimelineSeek(timeFromLocalX(state.startX), true);
			return;
		}
		applyMarqueeSelection(state);
	}

	function applyMarqueeSelection(state: MarqueeState) {
		if (!timelineContentEl) return;
		const contentRect = timelineContentEl.getBoundingClientRect();
		const left = Math.min(state.startX, state.currentX) + contentRect.left;
		const right = Math.max(state.startX, state.currentX) + contentRect.left;
		const top = Math.min(state.startY, state.currentY) + contentRect.top;
		const bottom = Math.max(state.startY, state.currentY) + contentRect.top;
		const hits: TimelineSelectionItem[] = [];
		for (const element of timelineContentEl.querySelectorAll<HTMLElement>('[data-subtitle-item-id],[data-audio-clip-id]')) {
			const rect = element.getBoundingClientRect();
			if (rect.right < left || rect.left > right || rect.bottom < top || rect.top > bottom) continue;
			const row = element.closest<HTMLElement>('[data-track-id]');
			const trackId = row?.dataset.trackId as VideoLocalizationTrackId | undefined;
			if (!trackId) continue;
			if (element.dataset.subtitleItemId) hits.push({ kind: 'subtitle', trackId, itemId: element.dataset.subtitleItemId });
			else if (element.dataset.audioClipId) hits.push({ kind: 'audio', trackId, itemId: element.dataset.audioClipId });
		}
		const nextItems = uniqueTimelineSelection([...state.baseItems, ...hits]);
		if (nextItems.length === selectedTimelineItems.length && nextItems.every((item, index) => selectionItemEqual(item, selectedTimelineItems[index]))) return;
		setTimelineSelection(nextItems);
	}

	function timeFromLocalX(x: number) {
		if (!timelineContentEl) return 0;
		return snapTimeToFrame((x / Math.max(1, timelineContentEl.getBoundingClientRect().width)) * timelineDurationMs, timelineFrameRate, 'nearest', 0, lastPlayableFrameMs);
	}

	function timeFromPointer(event: PointerEvent | MouseEvent) {
		return snapTimeToFrame(rawTimeFromPointer(event), timelineFrameRate, 'nearest', 0, lastPlayableFrameMs);
	}

	function rawTimeFromPointer(event: PointerEvent | MouseEvent) {
		if (!timelineContentEl) return 0;
		const rect = timelineContentEl.getBoundingClientRect();
		const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
		return Math.max(0, Math.min(lastPlayableFrameMs, ratio * timelineDurationMs));
	}

	function moveRangeCreation(event: PointerEvent) {
		if (!rangeCreateState) return;
		const currentMs = timeFromPointer(event);
		const moved = rangeCreateState.moved || Math.abs(event.clientX - rangeCreateState.startX) >= 4;
		if (!moved) return;
		gestureSession = gestureSession.update('range-create', (state) => ({ ...state, moved: true }));
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
		gestureSession = gestureSession.begin({ kind: 'selection-handle', edge });
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		moveSelectionHandle(event);
	}

	function moveSelectionHandle(event: PointerEvent) {
		if (!timelineContentEl || !selectionDrag) return;
		const rect = timelineContentEl.getBoundingClientRect();
		const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
		const timeMs = snapTimeToFrame(ratio * timelineDurationMs, timelineFrameRate, 'nearest', 0, timelineDurationMs);
		const currentStart = rangeStartMs ?? 0;
		const currentEnd = rangeEndMs ?? Math.min(timelineDurationMs, currentStart + 1800);
		if (selectionDrag === 'start') rangeStartMs = Math.min(timeMs, currentEnd - MIN_RANGE_DURATION_MS);
		else rangeEndMs = Math.max(timeMs, currentStart + MIN_RANGE_DURATION_MS);
	}

	function handleTrackWheel(event: WheelEvent) {
		if (!trackCanvasEl) return;
		suspendAutoFollow();
		const intent = timelineWheelIntent({
			ctrlKey: event.ctrlKey,
			metaKey: event.metaKey,
			shiftKey: event.shiftKey,
			deltaX: event.deltaX,
			deltaY: event.deltaY,
			deltaMode: event.deltaMode,
			wheelDeltaY: Number((event as WheelEvent & { wheelDeltaY?: number }).wheelDeltaY ?? 0)
		});
		if (intent === 'trackpad-zoom') {
			event.preventDefault();
			if (Math.abs(event.deltaY) >= 0.5) pinchTimelineAtPointer(event.deltaY, event.clientX);
			updateTimelineViewport(trackCanvasEl);
			return;
		}
		if (intent === 'wheel-zoom') {
			event.preventDefault();
			if (Math.abs(event.deltaY) >= 0.5) zoomTimelineAtPointer(event.deltaY < 0 ? 1 : -1, event.clientX);
			updateTimelineViewport(trackCanvasEl);
			return;
		}
		if (intent === 'pan') {
			event.preventDefault();
			trackCanvasEl.scrollLeft += Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
			updateTimelineViewport(trackCanvasEl);
			return;
		}
		// Continuous vertical tablet/trackpad movement keeps its native scroll behavior.
	}

	function trackHeightBounds(trackId: VideoLocalizationTrackId) {
		return { min: trackId === 'subtitles' || trackId === 'localizedSubtitles' ? 34 : 44, max: 168 };
	}

	function clampTrackHeight(trackId: VideoLocalizationTrackId, value: number) {
		const { min, max } = trackHeightBounds(trackId);
		return Math.max(min, Math.min(max, value));
	}

	function handleTrackHeaderWheel(event: WheelEvent, _trackId: VideoLocalizationTrackId) {
		if (!trackCanvasEl) return;
		if (event.ctrlKey || event.metaKey || event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
			handleTrackWheel(event);
		}
	}

	function openTrackHeightContextMenu(event: MouseEvent, trackId: VideoLocalizationTrackId) {
		const interactiveSelector = trackId === 'localizedSubtitles'
			? 'input,.track-resize-handle,.track-drag-handle'
			: 'button,input,.track-resize-handle,.track-drag-handle';
		if ((event.target as HTMLElement).closest(interactiveSelector)) return;
		event.preventDefault();
		event.stopPropagation();
		if (trackId === 'localizedSubtitles') {
			trackHeightContextMenu = null;
			timelineContextMenu = {
				x: event.clientX,
				y: event.clientY,
				target: {
					kind: 'track',
					hit: 'empty',
					trackId,
					subtitleTrack: 'localized',
					timeMs: currentTimeMs
				}
			};
			openVolumeTrack = null;
			return;
		}
		timelineContextMenu = null;
		trackHeightContextMenu = { x: event.clientX, y: event.clientY, trackId };
	}

	function closeFloatingControls(event: PointerEvent) {
		if (!(event.target as HTMLElement | null)?.closest('.volume-control')) {
			openVolumeTrack = null;
			openDubLaneVolume = null;
		}
		if (!(event.target as HTMLElement | null)?.closest('.context-menu')) timelineContextMenu = null;
		if (!(event.target as HTMLElement | null)?.closest('.context-menu')) trackHeightContextMenu = null;
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
			if (item?.dataset.subtitleItemId && !timelineItemSelected({ kind: 'subtitle', trackId, itemId: item.dataset.subtitleItemId })) {
				selectSubtitleTimelineItem(subtitleTrack, item.dataset.subtitleItemId, true);
			}
		} else {
			const clip = targetElement.closest<HTMLElement>('[data-audio-clip-id]');
			target = clip
				? { kind: 'audio-clip', trackId, itemId: clip.dataset.audioClipId || '', timeMs }
				: { kind: 'track', hit: 'empty', trackId, timeMs };
			if (clip?.dataset.audioClipId && !timelineItemSelected({ kind: 'audio', trackId, itemId: clip.dataset.audioClipId })) {
				selectAudioTimelineItem(trackId, clip.dataset.audioClipId);
			}
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
		const previousCursor = document.body.style.cursor;
		const previousUserSelect = document.body.style.userSelect;
		document.body.style.cursor = 'ns-resize';
		document.body.style.userSelect = 'none';
		const move = (moveEvent: PointerEvent) => {
			trackHeights = { ...trackHeights, [trackId]: clampTrackHeight(trackId, startHeight + moveEvent.clientY - startY) };
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
		resetTrackHeightValue(trackId);
	}

	function resetTrackHeightValue(trackId: VideoLocalizationTrackId) {
		trackHeights = { ...trackHeights, [trackId]: DEFAULT_TRACK_HEIGHTS[trackId] };
	}

	function resetAllTrackHeights() {
		trackHeights = { ...DEFAULT_TRACK_HEIGHTS };
	}

	function cueLiveTime(cue: SubtitleTimelineItem, trackKind: SubtitleTrackKind) {
		const itemId = subtitleItemId(cue);
		const live = liveCueTimes[subtitleLiveKey(trackKind, itemId)];
		const fallbackStart = cue.start_ms ?? 0;
		const fallbackEnd = cue.end_ms ?? fallbackStart + MIN_SUBTITLE_DURATION_MS;
		return live ?? { start_ms: fallbackStart, end_ms: Math.max(fallbackStart + MIN_SUBTITLE_DURATION_MS, fallbackEnd) };
	}

	function groupMoveBounds(groupItems: TimelineGroupMoveItem[], candidates: TimelineGroupMoveItem[]) {
		const selectedKeys = new Set(groupItems.map((item) => `${item.kind}:${item.trackId}:${item.itemId}`));
		const obstacles = candidates.filter((item) => !selectedKeys.has(`${item.kind}:${item.trackId}:${item.itemId}`));
		let minimumDeltaMs = -timelineDurationMs;
		let maximumDeltaMs = timelineDurationMs;
		for (const item of groupItems) {
			for (const obstacle of obstacles) {
				if (obstacle.endMs <= item.startMs) minimumDeltaMs = Math.max(minimumDeltaMs, obstacle.endMs - item.startMs);
				else if (obstacle.startMs >= item.endMs) maximumDeltaMs = Math.min(maximumDeltaMs, obstacle.startMs - item.endMs);
			}
		}
		return { minimumDeltaMs, maximumDeltaMs };
	}

	function selectedSubtitleMoveGroup(trackKind: SubtitleTrackKind, primaryId: string) {
		const trackId = trackKind === 'asr' ? 'subtitles' : 'localizedSubtitles';
		const selection = selectedTimelineItems.filter((item) => item.kind === 'subtitle' && item.trackId === trackId);
		if (selection.length < 2 || !selection.some((item) => item.itemId === primaryId)) return null;
		const trackItems: SubtitleTimelineItem[] = trackKind === 'asr' ? (draft?.cues ?? []) : (draft?.localized_subtitles ?? []);
		const byId = new Map(trackItems.map((item) => [subtitleItemId(item), item]));
		const candidates = trackItems.flatMap((item): TimelineGroupMoveItem[] => {
			const itemId = subtitleItemId(item);
			const time = cueLiveTime(item, trackKind);
			return time.end_ms > time.start_ms ? [{ kind: 'subtitle', trackId, itemId, startMs: time.start_ms, endMs: time.end_ms }] : [];
		});
		const groupItems = selection.flatMap((item) => {
			const source = byId.get(item.itemId);
			if (!source) return [];
			const time = cueLiveTime(source, trackKind);
			return [{ kind: 'subtitle' as const, trackId, itemId: item.itemId, startMs: time.start_ms, endMs: time.end_ms }];
		});
		if (groupItems.length !== selection.length) return null;
		return { groupItems, ...groupMoveBounds(groupItems, candidates) };
	}

	function selectedAudioMoveGroup(trackId: VideoLocalizationTrackId, primaryClip: VideoLocalizationTimelineClip) {
		const selection = selectedTimelineItems.filter((item) => item.kind === 'audio' && item.trackId === trackId);
		if (selection.length < 2 || !selection.some((item) => item.itemId === primaryClip.clip_id)) return null;
		const byId = new Map(clipsForTrack(trackId).map((clip) => [clip.clip_id, clip]));
		const selectedClips = selection.map((item) => byId.get(item.itemId)).filter((clip): clip is VideoLocalizationTimelineClip => Boolean(clip));
		if (selectedClips.length !== selection.length) return null;
		if (trackId === 'dub') {
			const groupItems = selectedClips.map((clip): TimelineGroupMoveItem => {
				const time = timelineClipTime(clip);
				return { kind: 'audio', trackId, itemId: clip.clip_id, startMs: time.start_ms, endMs: time.end_ms };
			});
			return { groupItems, minimumDeltaMs: -timelineDurationMs, maximumDeltaMs: timelineDurationMs };
		}
		const candidates = clipsForTrack(trackId).flatMap((clip): TimelineGroupMoveItem[] => {
			const time = timelineClipTime(clip);
			return [{ kind: 'audio', trackId, itemId: clip.clip_id, startMs: time.start_ms, endMs: time.end_ms }];
		});
		const groupItems = selectedClips.map((clip): TimelineGroupMoveItem => {
			const time = timelineClipTime(clip);
			return { kind: 'audio', trackId, itemId: clip.clip_id, startMs: time.start_ms, endMs: time.end_ms };
		});
		return { groupItems, ...groupMoveBounds(groupItems, candidates) };
	}

	function dubClipDragRanges(
		state: ClipDragState,
		times: Record<string, { start_ms: number; end_ms: number; source_start_ms?: number; source_end_ms?: number | null }> = liveClipTimes
	) {
		const items = state.groupItems.length > 1
			? state.groupItems
			: [{ kind: 'audio' as const, trackId: state.trackId, itemId: state.clipId, startMs: state.startMs, endMs: state.endMs }];
		return items.map((item) => {
			const live = times[item.itemId];
			return { clipId: item.itemId, startMs: live?.start_ms ?? item.startMs, endMs: live?.end_ms ?? item.endMs };
		});
	}

	function dubClipDragLaneRanges(
		state: ClipDragState,
		times: Record<string, { start_ms: number; end_ms: number; source_start_ms?: number; source_end_ms?: number | null }> = liveClipTimes
	) {
		return dubClipDragRanges(state, times).map((range) => ({
			...range,
			lane: state.groupLaneByClipId[range.clipId] ?? state.startLane
		}));
	}

	function clipLaneDropPreviewForLane(lane: number) {
		return clipLaneDropPreviews?.find((preview) => preview.lane === lane) ?? null;
	}

	function updateDubClipLaneTarget(
		event: PointerEvent,
		state: ClipDragState,
		times: Record<string, { start_ms: number; end_ms: number; source_start_ms?: number; source_end_ms?: number | null }>
	) {
		if (state.trackId !== 'dub' || state.mode !== 'move') return;
		const hoverLane = historyDropLaneFromPointer(event);
		const ranges = dubClipDragLaneRanges(state, times);
		const lockedLanes = dubTrackLanes.map((_, lane) => lane).filter((lane) => dubLaneInteractionLocked(lane));
		const placement = hoverLane === null
			? null
			: placeDubClipGroupAtPrimaryLane(
				clipsForTrack('dub'),
				ranges,
				state.clipId,
				hoverLane,
				lockedLanes
			);
		gestureSession = gestureSession.update('clip-drag', (state) => ({
			...state,
			hoverLane,
			targetLane: placement?.primaryLane ?? state.startLane,
			targetLaneByClipId: placement ? { ...placement.laneByClipId } : state.groupLaneByClipId,
			laneDropAllowed: Boolean(placement)
		}));
	}

	function clipIncludedInDrag(clipId: string) {
		return Boolean(clipDragState && (
			clipDragState.clipId === clipId || clipDragState.groupItems.some((item) => item.itemId === clipId)
		));
	}

	function startCueDrag(event: PointerEvent, cue: SubtitleTimelineItem, mode: DragMode, trackKind: SubtitleTrackKind, persistenceTarget: 'asr' | 'localized' | 'dub' = trackKind) {
		if (event.button !== 0) return;
		if (persistenceTarget !== 'dub') cue = subtitleCueAtPointer(event, trackKind, cue);
		const trackId = trackKind === 'asr' ? 'subtitles' : 'localizedSubtitles';
		const itemId = subtitleItemId(cue);
		if (event.ctrlKey || event.metaKey) {
			event.preventDefault();
			event.stopPropagation();
			selectSubtitleTimelineItem(trackKind, itemId, true, true);
			return;
		}
		if (trackInteractionLocked(trackId, itemId)) {
			event.preventDefault();
			event.stopPropagation();
			if (persistenceTarget !== 'dub') selectSubtitleTimelineItem(trackKind, itemId);
			return;
		}
		const time = cueLiveTime(cue, trackKind);
		if (time.end_ms <= time.start_ms) {
			event.preventDefault();
			event.stopPropagation();
			if (persistenceTarget !== 'dub') selectSubtitleTimelineItem(trackKind, itemId);
			return;
		}
		const trackCues = persistenceTarget === 'dub'
			? (draft?.dub_subtitles ?? []).map((item) => ({ cue_id: item.subtitle_id, start_ms: item.start_ms, end_ms: item.end_ms }))
			: trackKind === 'asr'
			? (draft?.cues ?? [])
			: (draft?.localized_subtitles ?? []).map((item) => ({ cue_id: item.subtitle_id, start_ms: item.start_ms, end_ms: item.end_ms }));
		const bounds = persistenceTarget === 'dub'
			? dubSubtitleTimingBounds(draft?.dub_subtitles ?? [], itemId, subtitleTimelineLimitMs)
			: subtitleCueDragBounds(trackCues, itemId, subtitleTimelineLimitMs);
		const group = persistenceTarget !== 'dub' && mode === 'move' ? selectedSubtitleMoveGroup(trackKind, itemId) : null;
		event.preventDefault();
		event.stopPropagation();
		suspendAutoFollow();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		if (!group && persistenceTarget !== 'dub') selectSubtitleTimelineItem(trackKind, itemId, true);
		gestureSession = gestureSession.begin({
			kind: 'cue-drag',
			itemId,
			trackKind,
			persistenceTarget,
			mode,
			startX: event.clientX,
			startMs: time.start_ms,
			endMs: time.end_ms,
			durationMs: time.end_ms - time.start_ms,
			minStartMs: bounds.minStartMs,
			maxEndMs: bounds.maxEndMs,
			groupItems: group?.groupItems ?? [],
			groupMinDeltaMs: group?.minimumDeltaMs ?? -timelineDurationMs,
			groupMaxDeltaMs: group?.maximumDeltaMs ?? timelineDurationMs
		});
	}

	function startDubSubtitleDrag(event: PointerEvent, displayCue: SubtitleDisplayCue, mode: 'trim-start' | 'trim-end') {
		const cue = dubSubtitleCue(displayCue);
		if (!cue) return;
		onSelectSubtitleDisplayCue(displayCue);
		startCueDrag(event, cue, mode, 'localized', 'dub');
	}

	function moveCueDrag(event: PointerEvent) {
		if (!dragState || !timelineContentEl) return;
		const rect = timelineContentEl.getBoundingClientRect();
		const deltaMs = ((event.clientX - dragState.startX) / Math.max(1, rect.width)) * timelineDurationMs;
		if (dragState.mode === 'move' && dragState.groupItems.length > 1) {
			const snappedPrimaryStart = snapTimeToFrame(dragState.startMs + deltaMs, timelineFrameRate, 'nearest', 0, timelineDurationMs);
			const result = resolveTimelineGroupMove({
				selectedItems: dragState.groupItems,
				primary: dragState.groupItems.find((item) => item.itemId === dragState?.itemId) ?? dragState.groupItems[0],
				requestedDeltaMs: snappedPrimaryStart - dragState.startMs,
				timelineDurationMs,
				minimumDeltaMs: dragState.groupMinDeltaMs,
				maximumDeltaMs: dragState.groupMaxDeltaMs
			});
			const nextLiveCueTimes = { ...liveCueTimes };
			for (const item of result.items) {
				nextLiveCueTimes[subtitleLiveKey(dragState.trackKind, item.itemId)] = { start_ms: item.startMs, end_ms: item.endMs };
			}
			liveCueTimes = nextLiveCueTimes;
			snapGuideMs = result.primary.startMs;
			return;
		}
		const next = editFrameInterval({
			mode: dragState.mode,
			startMs: dragState.startMs,
			endMs: dragState.endMs,
			deltaMs,
			frameRate: timelineFrameRate,
			minStartMs: dragState.minStartMs,
			maxEndMs: dragState.maxEndMs,
			minDurationMs: MIN_SUBTITLE_DURATION_MS
		});
		liveCueTimes = { ...liveCueTimes, [subtitleLiveKey(dragState.trackKind, dragState.itemId)]: { start_ms: next.startMs, end_ms: next.endMs } };
		snapGuideMs = dragState.mode === 'trim-end' ? next.endMs : next.startMs;
	}

	function endCueDrag() {
		if (!dragState) return;
		if (dragState.mode === 'move' && dragState.groupItems.length > 1) {
			const items = dragState.groupItems.flatMap((item): TimelineGroupMoveCommitItem[] => {
				const live = liveCueTimes[subtitleLiveKey(dragState!.trackKind, item.itemId)];
				return live ? [{ kind: 'subtitle', trackId: item.trackId as VideoLocalizationTrackId, itemId: item.itemId, startMs: live.start_ms, endMs: live.end_ms }] : [];
			});
			if (items.length === dragState.groupItems.length) onMoveTimelineItems?.(items);
			gestureSession = gestureSession.cancel();
			snapGuideMs = null;
			return;
		}
		const live = liveCueTimes[subtitleLiveKey(dragState.trackKind, dragState.itemId)];
		if (live) {
			if (dragState.persistenceTarget === 'dub') void onUpdateDubSubtitleTime?.(dragState.itemId, live.start_ms, live.end_ms);
			else if (dragState.persistenceTarget === 'asr') onUpdateCueTime(dragState.itemId, live.start_ms, live.end_ms);
			else onUpdateLocalizedSubtitleTime(dragState.itemId, live.start_ms, live.end_ms);
		}
		gestureSession = gestureSession.cancel();
		snapGuideMs = null;
	}

	function startClipDrag(event: PointerEvent, clip: VideoLocalizationTimelineClip, mode: DragMode) {
		if (event.button !== 0) return;
		const trackId = clip.track_id as VideoLocalizationTrackId;
		if (event.ctrlKey || event.metaKey) {
			event.preventDefault();
			event.stopPropagation();
			selectAudioTimelineItem(trackId, clip.clip_id, true);
			return;
		}
		if (trackInteractionLocked(trackId, clip.clip_id)) return;
		const time = timelineClipTime(clip);
		const group = mode === 'move' ? selectedAudioMoveGroup(trackId, clip) : null;
		event.preventDefault();
		event.stopPropagation();
		suspendAutoFollow();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		if (!group) selectAudioTimelineItem(trackId, clip.clip_id);
		const groupItems = group?.groupItems ?? [];
		const laneItems = groupItems.length > 1
			? groupItems
			: [{ kind: 'audio' as const, trackId, itemId: clip.clip_id, startMs: time.start_ms, endMs: time.end_ms }];
		const groupLaneByClipId = Object.fromEntries(laneItems.map((item) => [
			item.itemId,
			trackId === 'dub' ? (dubTrackLayout.laneByClipId.get(item.itemId) ?? 0) : 0
		]));
		gestureSession = gestureSession.begin({
			kind: 'clip-drag',
			clipId: clip.clip_id,
			trackId,
			mode,
			startX: event.clientX,
			startMs: time.start_ms,
			endMs: time.end_ms,
			durationMs: time.end_ms - time.start_ms,
			sourceStartMs: time.source_start_ms ?? 0,
			sourceEndMs: time.source_end_ms ?? null,
			sourceDurationMs: clipSourceDurationMs(clip, time),
			startLane: trackId === 'dub' ? (dubTrackLayout.laneByClipId.get(clip.clip_id) ?? 0) : 0,
			targetLane: trackId === 'dub' ? (dubTrackLayout.laneByClipId.get(clip.clip_id) ?? 0) : 0,
			hoverLane: null,
			laneDropAllowed: false,
			groupLaneByClipId,
			targetLaneByClipId: groupLaneByClipId,
			groupItems,
			groupMinDeltaMs: group?.minimumDeltaMs ?? -timelineDurationMs,
			groupMaxDeltaMs: group?.maximumDeltaMs ?? timelineDurationMs
		});
	}

	function moveClipDrag(event: PointerEvent) {
		if (!clipDragState || !timelineContentEl) return;
		const rect = timelineContentEl.getBoundingClientRect();
		const deltaMs = ((event.clientX - clipDragState.startX) / Math.max(1, rect.width)) * timelineDurationMs;
		if (clipDragState.mode === 'move' && clipDragState.groupItems.length > 1) {
			const snappedPrimaryStart = snapTimeToFrame(clipDragState.startMs + deltaMs, timelineFrameRate, 'nearest', 0, timelineDurationMs);
			const result = resolveTimelineGroupMove({
				selectedItems: clipDragState.groupItems,
				primary: clipDragState.groupItems.find((item) => item.itemId === clipDragState?.clipId) ?? clipDragState.groupItems[0],
				requestedDeltaMs: snappedPrimaryStart - clipDragState.startMs,
				timelineDurationMs,
				minimumDeltaMs: clipDragState.groupMinDeltaMs,
				maximumDeltaMs: clipDragState.groupMaxDeltaMs
			});
			const nextLiveClipTimes = { ...liveClipTimes };
			const clipById = new Map(clipsForTrack(clipDragState.trackId).map((item) => [item.clip_id, item]));
			for (const item of result.items) {
				const source = clipById.get(item.itemId);
				if (!source) continue;
				const sourceTime = timelineClipTime(source);
				nextLiveClipTimes[item.itemId] = {
					start_ms: item.startMs,
					end_ms: item.endMs,
					source_start_ms: sourceTime.source_start_ms ?? 0,
					source_end_ms: sourceTime.source_end_ms ?? null
				};
			}
			liveClipTimes = nextLiveClipTimes;
			updateDubClipLaneTarget(event, clipDragState, nextLiveClipTimes);
			snapGuideMs = result.primary.startMs;
			return;
		}
		const minDurationMs = audioClipMinimumDurationMs;
		const sourceEndMs = clipDragState.sourceEndMs ?? clipDragState.sourceStartMs + Math.max(minDurationMs, clipDragState.durationMs);
		const next = editAudioFrameInterval({
			mode: clipDragState.mode,
			startMs: clipDragState.startMs,
			endMs: clipDragState.endMs,
			sourceStartMs: clipDragState.sourceStartMs,
			sourceEndMs,
			sourceDurationMs: clipDragState.sourceDurationMs,
			deltaMs,
			frameRate: timelineFrameRate,
			timelineDurationMs
		});
		const nextLiveClipTimes = {
			...liveClipTimes,
			[clipDragState.clipId]: {
				start_ms: next.startMs,
				end_ms: next.endMs,
				source_start_ms: next.sourceStartMs,
				source_end_ms: next.sourceEndMs
			}
		};
		liveClipTimes = nextLiveClipTimes;
		updateDubClipLaneTarget(event, clipDragState, nextLiveClipTimes);
		snapGuideMs = clipDragState.mode === 'trim-end' ? next.endMs : next.startMs;
	}

	function cancelClipDrag() {
		if (!clipDragState) return;
		const nextLiveClipTimes = { ...liveClipTimes };
		const draggedIds = new Set([
			clipDragState.clipId,
			...clipDragState.groupItems.map((item) => item.itemId)
		]);
		for (const clipId of draggedIds) delete nextLiveClipTimes[clipId];
		liveClipTimes = nextLiveClipTimes;
		gestureSession = gestureSession.cancel();
		snapGuideMs = null;
	}

	function endClipDrag() {
		if (!clipDragState) return;
		const state = clipDragState;
		if (state.mode === 'move' && state.groupItems.length > 1) {
			const requestedLaneChange = state.trackId === 'dub' && state.hoverLane !== null && state.hoverLane !== state.startLane;
			const verticalLaneChange = requestedLaneChange
				&& state.laneDropAllowed
				&& state.targetLane === state.hoverLane
				&& Object.values(state.targetLaneByClipId).every((lane) => !dubLaneInteractionLocked(lane));
			const items = state.groupItems.flatMap((item): TimelineGroupMoveCommitItem[] => {
				const live = liveClipTimes[item.itemId];
				return live ? [{
					kind: 'audio',
					trackId: item.trackId as VideoLocalizationTrackId,
					itemId: item.itemId,
					startMs: live.start_ms,
					endMs: live.end_ms,
					sourceStartMs: live.source_start_ms ?? 0,
					sourceEndMs: live.source_end_ms ?? null,
					...(verticalLaneChange ? { dubLane: state.targetLaneByClipId[item.itemId] } : {})
				}] : [];
			});
			if (items.length === state.groupItems.length) onMoveTimelineItems?.(items);
			const nextLiveClipTimes = { ...liveClipTimes };
			for (const item of state.groupItems) delete nextLiveClipTimes[item.itemId];
			liveClipTimes = nextLiveClipTimes;
			gestureSession = gestureSession.cancel();
			snapGuideMs = null;
			return;
		}
		const live = liveClipTimes[state.clipId];
		if (live) {
			const requestedLaneChange = state.trackId === 'dub' && state.mode === 'move' && state.hoverLane !== null && state.hoverLane !== state.startLane;
			let verticalLaneChange = false;
			if (requestedLaneChange) {
				verticalLaneChange = state.laneDropAllowed
					&& state.targetLane === state.hoverLane
					&& !dubLaneInteractionLocked(state.targetLane)
					&& state.targetLaneByClipId[state.clipId] === state.targetLane;
			}
			const dubLane = verticalLaneChange ? state.targetLane : undefined;
			onUpdateTimelineClip(state.clipId, live.start_ms, live.end_ms, live.source_start_ms ?? 0, live.source_end_ms ?? null, dubLane);
		}
		const nextLiveClipTimes = { ...liveClipTimes };
		delete nextLiveClipTimes[state.clipId];
		liveClipTimes = nextLiveClipTimes;
		gestureSession = gestureSession.cancel();
		snapGuideMs = null;
	}

	function updateTimelineViewport(element = trackCanvasEl) {
		if (!element) return;
		const width = element.clientWidth;
		if (width <= 0) return;
		// Preserve time across layout changes and keep native scrolling out of label overflow.
		const anchorWidth = timelineViewportWidth || width;
		const anchorLeft = width !== anchorWidth ? timelineScrollLeft : element.scrollLeft;
		element.scrollLeft = resizedTimelineScrollLeft(
			anchorLeft, anchorWidth, width, timelineContentPixelWidth(width, timelineZoom)
		);
		timelineScrollLeft = element.scrollLeft;
		timelineViewportWidth = width;
	}

	function observeTimelineViewport(element: HTMLDivElement) {
		updateTimelineViewport(element);
		const observer = new ResizeObserver(() => {
			updateTimelineViewport(element);
			scheduleTimelineViewportChange(element);
		});
		observer.observe(element);
		return { destroy: () => observer.disconnect() };
	}

	function viewportStartMs(element = trackCanvasEl) {
		if (!element || timelineDurationMs <= 0 || element.clientWidth <= 0) return 0;
		return Math.max(0, Math.min(timelineDurationMs, Math.round((element.scrollLeft / timelineContentPixelWidth(element.clientWidth, timelineZoom)) * timelineDurationMs)));
	}

	function scheduleTimelineViewportChange(element = trackCanvasEl) {
		if (!element || element.clientWidth <= 0 || element.scrollWidth <= 0 || !onTimelineViewportChange || !timelineViewportRestoreReady || !timelineViewportRestored) return;
		pendingViewportStartMs = viewportStartMs(element);
		if (viewportChangeTimer) return;
		viewportChangeTimer = setTimeout(() => {
			viewportChangeTimer = null;
			onTimelineViewportChange?.(pendingViewportStartMs);
		}, 120);
	}

	function restoreTimelineViewport(startMs: number) {
		if (!trackCanvasEl || timelineDurationMs <= 0) return;
		updateTimelineViewport(trackCanvasEl);
		const boundedStartMs = Math.max(0, Math.min(timelineDurationMs, startMs));
		const contentWidth = timelineContentPixelWidth(trackCanvasEl.clientWidth, timelineZoom);
		programmaticTimelineScroll = true;
		trackCanvasEl.scrollLeft = Math.max(
			0,
			Math.min(
				(boundedStartMs / timelineDurationMs) * contentWidth,
				contentWidth - trackCanvasEl.clientWidth
			)
		);
		updateTimelineViewport(trackCanvasEl);
		requestAnimationFrame(() => {
			programmaticTimelineScroll = false;
			timelineViewportRestored = true;
			scheduleTimelineViewportChange(trackCanvasEl);
		});
	}

	function suspendAutoFollow() {
		autoFollowSuspendedUntil = Date.now() + 3000;
	}

	function handleTimelineScroll(element: HTMLDivElement) {
		updateTimelineViewport(element);
		scheduleTimelineViewportChange(element);
		timelineContextMenu = null;
		if (!programmaticTimelineScroll) suspendAutoFollow();
	}

	function scrollTimelineTo(left: number) {
		if (!trackCanvasEl) return;
		updateTimelineViewport(trackCanvasEl);
		programmaticTimelineScroll = true;
		trackCanvasEl.scrollLeft = Math.max(0, Math.min(left, timelineContentPixelWidth(trackCanvasEl.clientWidth, timelineZoom) - trackCanvasEl.clientWidth));
		updateTimelineViewport(trackCanvasEl);
		requestAnimationFrame(() => {
			programmaticTimelineScroll = false;
		});
	}

	function followPlaybackPage(_force = false) {
		if (!trackCanvasEl || !isPlaying || Date.now() < autoFollowSuspendedUntil) return;
		const viewportWidth = trackCanvasEl.clientWidth;
		const contentWidth = timelineContentPixelWidth(viewportWidth, timelineZoom);
		if (viewportWidth <= 0 || contentWidth <= viewportWidth) return;
		const playheadPx = (Math.max(0, Math.min(timelineDurationMs, currentTimeMs)) / timelineDurationMs) * contentWidth;
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
		rangeStartMs;
		rangeEndMs;
		const range = currentSelectionRange();
		const key = range ? `${range.startMs}:${range.endMs}` : 'none';
		if (key === lastSelectionRangeKey) return;
		lastSelectionRangeKey = key;
		onSelectionRangeChange?.(range);
	});

	$effect(() => {
		timelineZoom;
		requestAnimationFrame(() => updateTimelineViewport());
	});

	$effect(() => {
		const nextProjectId = projectId;
		const savedStartMs = timelineViewportStartMs;
		if (!nextProjectId || !timelineViewportRestoreReady) {
			timelineViewportRestored = false;
			if (restoredViewportProjectId === nextProjectId) restoredViewportProjectId = '';
			return;
		}
		if (restoredViewportProjectId === nextProjectId) return;
		restoredViewportProjectId = nextProjectId;
		timelineViewportRestored = false;
		requestAnimationFrame(() => {
			requestAnimationFrame(() => restoreTimelineViewport(savedStartMs));
		});
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

<svelte:window onkeydown={handleTimelineKeydown} onpointerdown={closeFloatingControls} onpointermove={moveTtsHistoryPointer} onpointerup={endTtsHistoryPointer} onpointercancel={cancelTtsHistoryPointer} onmouseup={endTtsHistoryPointer} onblur={handleWindowBlur} />

<section class="cut-timeline" aria-label="视频本土化时间线">
	<div class="timeline-toolbar">
		<div class="timeline-activity"><ActivityNotice kind={noticeSummary ? noticeKind : 'idle'} summary={noticeSummary} detail={noticeDetail} tasks={activityTasks} resetKey={projectId} {onOpenTaskCenter} /></div>
		<div class="transport">
			<button class="icon-btn" type="button" aria-label="跳到视频开始" data-tooltip="跳到视频开始｜Shift+点击跳到全轨道上一个入点或出点。快捷键：Home / Shift+←" onclick={(event) => onTransportAction(event.shiftKey ? 'previous-boundary' : 'start')}><SkipBack size={15} /></button>
			<button class="icon-btn" type="button" aria-label={playbackPreparing ? '取消准备播放' : isPlaying ? '暂停' : '播放'} aria-busy={playbackPreparing} data-tooltip={playbackPreparing ? '正在准备视频和启用的音轨｜点击或按 Space 可取消。' : isPlaying ? '暂停｜停止视频和所有启用轨道的播放。快捷键：Space' : '播放｜从当前指针同步播放视频和启用的轨道。快捷键：Space'} onclick={() => onTransportAction('play-pause')}>
				{#if playbackPreparing}<CommandSpinner size={15} />{:else if isPlaying}<Pause size={15} />{:else}<Play size={15} />{/if}
			</button>
			<button class="icon-btn" type="button" aria-label="跳到视频结束" data-tooltip="跳到视频结束｜Shift+点击跳到全轨道下一个入点或出点。快捷键：End / Shift+→" onclick={(event) => onTransportAction(event.shiftKey ? 'next-boundary' : 'end')}><SkipForward size={15} /></button>
			<button class="icon-btn" type="button" aria-label="撤销时间线编辑" data-tooltip={`撤销：恢复上一次时间线编辑，可撤销 ${undoTimelineCount} 步。快捷键：Cmd/Ctrl+Z`} onclick={onUndoTimelineClip} disabled={!canUndoTimeline}><Undo2 size={15} /></button>
			<button class="icon-btn" type="button" aria-label="重做时间线编辑" data-tooltip={`重做：重新应用刚撤销的编辑，可重做 ${redoTimelineCount} 步。快捷键：Cmd/Ctrl+Shift+Z`} onclick={onRedoTimelineClip} disabled={!canRedoTimeline}><Redo2 size={15} /></button>
			<div class="playback-health" class:ready={playbackHealthKind === 'ready'} class:loading={playbackHealthKind === 'loading'} class:failed={playbackHealthKind === 'failed'} role="status" aria-live="polite" aria-label={playbackHealthLabel} title={playbackHealthLabel}>
				<i aria-hidden="true"></i><span class="playback-health-copy"><HoverMarqueeText text={playbackHealthLabel} /></span>
			</div>
		</div>
		<div class="timeline-actions">
			<span class="toolbar-divider" aria-hidden="true"></span>
			<button
				class="tool-btn icon-tool hover-scrub-toggle"
				class:active={hoverScrubEnabled}
				type="button"
				aria-label={hoverScrubEnabled ? '关闭鼠标预览' : '启用鼠标预览'}
				aria-pressed={hoverScrubEnabled}
				data-tooltip={hoverScrubEnabled ? '鼠标预览已启用｜移动鼠标可预览画面、声音和字幕。快捷键：H' : '启用鼠标预览｜移动鼠标可快速预览画面、声音和字幕。快捷键：H'}
				onclick={() => onHoverScrubChange?.(!hoverScrubEnabled)}
			><span class="hover-preview-icon" aria-hidden="true"><MousePointer2 size={13} /><i></i></span></button>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<div class="edit-tools" aria-label="字幕片段编辑">
				<button class="tool-btn icon-tool" class:active={activeTool === 'razor'} type="button" onclick={() => activateTimelineTool(activeTool === 'razor' ? 'select' : 'razor')} aria-label="剃刀工具" aria-pressed={activeTool === 'razor'} data-tooltip={activeTool === 'razor' ? '剃刀工具已激活：再次点击或按 C 取消，按 V 返回默认选择。' : '剃刀工具：激活后点击片段，在鼠标所在帧的右边界裁开。快捷键：C'}><Icon name="razor-blade" class="razor-blade-icon" size={15} iconNode={razorBladeIconNode} /></button>
				<button class="tool-btn icon-tool" type="button" onclick={mergeSelectedTimelineSubtitles} disabled={!canMergeSelectedSubtitles} aria-label="合并所选字幕片段" data-tooltip="合并：将同一字幕轨上选中的两个及以上字幕按时间顺序拼接。快捷键：Shift+M">⇄</button>
				<button class="tool-btn icon-tool danger" type="button" onclick={() => deleteSelectedTimelineItems()} disabled={!canDeleteSelectedItems} aria-label="删除" data-tooltip="删除：移除当前选中的字幕或音频片段，支持多选。快捷键：E / Delete / Backspace"><Trash2 size={13} /></button>
			</div>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<div class="zoom-stepper" aria-label="时间线缩放">
				<button type="button" onclick={() => zoomTimelineAtPointer(-1)} disabled={timelineZoom <= 1} aria-label="缩小时间线" data-tooltip="缩小时间线：显示更长时间范围。触控板捏合或 Command（⌘）+ 滚动也可缩放。快捷键：- "><ZoomOut size={13} /></button>
				<span>{formatTimelineZoom(timelineZoom)}x</span>
				<button type="button" onclick={() => zoomTimelineAtPointer(1)} disabled={timelineZoom >= 1200} aria-label="放大时间线" data-tooltip="放大时间线：更精细地查看波形和片段。触控板捏合或 Command（⌘）+ 滚动也可缩放。快捷键：+ "><ZoomIn size={13} /></button>
			</div>
			<span class="toolbar-divider" aria-hidden="true"></span>
			<div class="range-marker-tools" aria-label="设置出入点">
				<button class:active={rangeStartMs !== null} type="button" aria-label="设置入点" data-tooltip="设置入点：把当前播放指针设为选区起点。快捷键 I" onclick={() => setSelectionPoint('start')}><ChevronsLeft size={14} /></button>
				<button class:active={rangeEndMs !== null} type="button" aria-label="设置出点" data-tooltip="设置出点：把当前播放指针设为选区终点。快捷键 O" onclick={() => setSelectionPoint('end')}><ChevronsRight size={14} /></button>
			</div>
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
				<strong class="time-readout">
					<span class="time-current">{formatFrameTimecode(currentTimeMs, timelineFrameRate)}</span>
					<span class="time-total">{formatFrameTimecode(contentEndMs, timelineFrameRate)}</span>
				</strong>
			</div>
				<div class="track-label subtitle track-subtitle track-asr-subtitle" role="group" aria-label="ASR 字幕轨" class:track-locked={trackStates.subtitles.locked} class:track-processing={trackRuntimeBusy('subtitles')} style={subtitleTrackStyle('subtitles')} onwheel={(event) => handleTrackHeaderWheel(event, 'subtitles')} oncontextmenu={(event) => openTrackHeightContextMenu(event, 'subtitles')}>
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
					<div class="track-label subtitle track-subtitle track-localized-subtitle" role="group" aria-label="本土化字幕轨" class:track-locked={trackStates.localizedSubtitles.locked} class:track-processing={trackRuntimeBusy('localizedSubtitles')} style={subtitleTrackStyle('localizedSubtitles')} onwheel={(event) => handleTrackHeaderWheel(event, 'localizedSubtitles')} oncontextmenu={(event) => openTrackHeightContextMenu(event, 'localizedSubtitles')}>
				<div>
					{#if editingTrackId === 'localizedSubtitles'}
						<input class="track-name-input" data-track-name="localizedSubtitles" aria-label="修改本土化字幕轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('localizedSubtitles')} onkeydown={(event) => handleTrackNameKeydown(event, 'localizedSubtitles')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.localizedSubtitles.locked} data-tooltip="本土化字幕轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('localizedSubtitles')}>{trackName('localizedSubtitles')}</button>
					{/if}
					<span>{dubSubtitleDisplayEnabled ? '合成配音识别字幕' : '本土化上屏字幕'}</span>
				</div>
					<div class="track-controls">
					<button
						class="subtitle-variant-toggle"
						class:active={dubSubtitleDisplayEnabled}
						type="button"
						disabled={!dubSubtitleDisplayAvailable || localizationBusy}
						aria-label={dubSubtitleDisplayEnabled ? '切换到本土化上屏字幕' : '切换到合成配音字幕'}
						aria-pressed={dubSubtitleDisplayEnabled}
						data-tooltip={dubSubtitleDisplayEnabled
							? '合成配音字幕｜点击切换回本土化上屏字幕。'
							: dubSubtitleDisplayAvailable
								? '本土化上屏字幕｜点击查看根据实际合成配音识别出的字幕。'
								: '合成配音字幕｜请先在轨道空白处右键，根据合成配音生成字幕。'}
						onclick={() => onToggleDubSubtitleDisplay?.()}
					>{dubSubtitleDisplayEnabled ? '配音字幕' : '本土化'}</button>
					<button
						class="track-toggle visibility-toggle"
						class:active={localizedSubtitleVisible}
						type="button"
						aria-label={`${localizedSubtitleVisible ? '隐藏' : '显示'}${dubSubtitleDisplayEnabled ? '合成配音字幕' : '本土化上屏字幕'}`}
						data-tooltip={`${localizedSubtitleVisible ? '隐藏' : '显示'}${dubSubtitleDisplayEnabled ? '合成配音字幕' : '本土化上屏字幕'}｜${localizedSubtitleVisible ? '视频预览中不再显示这条字幕轨。' : '在视频预览中显示这条字幕轨。'}`}
						onclick={() => onToggleSubtitleSource('localized')}
					>{#if localizedSubtitleVisible}<Eye size={13} />{:else}<EyeOff size={13} />{/if}</button>
					<button class="track-toggle lock-toggle" class:active={trackStates.localizedSubtitles.locked} type="button" aria-label={trackStates.localizedSubtitles.locked ? '解锁本土化字幕轨' : '锁定本土化字幕轨'} data-tooltip={trackStates.localizedSubtitles.locked ? '解锁本土化字幕轨｜允许移动和裁切字幕片段。' : '锁定本土化字幕轨｜禁止修改字幕片段。'} onclick={() => toggleLocked('localizedSubtitles')}>{#if trackStates.localizedSubtitles.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
					</div>
					<button class="track-resize-handle" type="button" aria-label="调整本土化字幕轨高度" data-tooltip="调整轨道高度：上下拖动分界线，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'localizedSubtitles')} ondblclick={(event) => resetTrackHeight(event, 'localizedSubtitles')}></button>
				</div>
				<div class="track-label track-audio track-original" role="group" aria-label="原音轨" class:track-muted={trackStates.original.muted} class:track-locked={trackStates.original.locked} class:track-processing={trackRuntimeBusy('original')} class:drag-over={dragOverAudioTrackId === 'original' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'original' && dragOverAudioTrackPlacement === 'after'} style={audioTrackStyle('original')} onwheel={(event) => handleTrackHeaderWheel(event, 'original')} oncontextmenu={(event) => openTrackHeightContextMenu(event, 'original')} ondragover={(event) => markAudioTrackDropTarget(event, 'original')} ondrop={(event) => dropAudioTrack(event, 'original')}>
				<i class="track-title-level" style={`width:${trackMeterPercent('original')}%`}></i>
				<div>
					{#if editingTrackId === 'original'}
						<input class="track-name-input" data-track-name="original" aria-label="修改原音轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('original')} onkeydown={(event) => handleTrackNameKeydown(event, 'original')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.original.locked} data-tooltip="原音轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('original')}>{trackName('original')}</button>
					{/if}
					<span>{hasSourceAudio ? (trackStates.original.muted ? '已静音 · 点击 M 恢复' : '完整视频声音') : '导入后自动抽取'}</span>
				</div>
				<div class="track-controls">
					<button class="track-drag-handle" type="button" draggable="true" aria-label="拖动调整原音轨顺序" data-tooltip="调整轨道顺序｜拖动到其他音轨标题上方。" ondragstart={(event) => beginAudioTrackReorder(event, 'original')} ondragend={endAudioTrackReorder}><GripVertical size={13} /></button>
					<button class="track-toggle" class:active={trackStates.original.muted} type="button" aria-label="静音原音轨" aria-pressed={trackStates.original.muted} data-tooltip="静音原音轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('original')}>M</button>
					<button class="track-toggle" class:active={trackStates.original.solo} type="button" aria-label="独奏原音轨" aria-pressed={trackStates.original.solo} data-tooltip="独奏原音轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('original')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'original'}
							<AudioGainEditor valueDb={gainToDb(trackStates.original.volume, 2)} ariaLabel="原音轨音量 dB" onChange={(db) => updateTrackDb('original', db)} onCommit={() => finishVolumeEdit('original')} onCancel={() => finishVolumeEdit('original')} />
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整原音轨音量" data-tooltip="原音轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'original')} ondblclick={(event) => resetTrackDb(event, 'original')}>{volumeDbLabel('original')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.original.locked} type="button" aria-label={trackStates.original.locked ? '解锁原音轨' : '锁定原音轨'} data-tooltip={trackStates.original.locked ? '解锁原音轨｜允许移动、裁切和删除音频片段。' : '锁定原音轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('original')}>{#if trackStates.original.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整原音轨高度" data-tooltip="调整原音轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'original')} ondblclick={(event) => resetTrackHeight(event, 'original')}></button>
			</div>
				<div class="track-label track-audio track-vocals" role="group" aria-label="人声轨" class:track-muted={trackStates.vocals.muted} class:track-locked={trackStates.vocals.locked} class:track-processing={trackRuntimeBusy('vocals')} class:drag-over={dragOverAudioTrackId === 'vocals' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'vocals' && dragOverAudioTrackPlacement === 'after'} style={audioTrackStyle('vocals')} onwheel={(event) => handleTrackHeaderWheel(event, 'vocals')} oncontextmenu={(event) => openTrackHeightContextMenu(event, 'vocals')} ondragover={(event) => markAudioTrackDropTarget(event, 'vocals')} ondrop={(event) => dropAudioTrack(event, 'vocals')}>
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
					<button class="track-toggle" class:active={trackStates.vocals.muted} type="button" aria-label="静音人声轨" aria-pressed={trackStates.vocals.muted} data-tooltip="静音人声轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('vocals')}>M</button>
					<button class="track-toggle" class:active={trackStates.vocals.solo} type="button" aria-label="独奏人声轨" aria-pressed={trackStates.vocals.solo} data-tooltip="独奏人声轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('vocals')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'vocals'}
							<AudioGainEditor valueDb={gainToDb(trackStates.vocals.volume, 2)} ariaLabel="人声轨音量 dB" onChange={(db) => updateTrackDb('vocals', db)} onCommit={() => finishVolumeEdit('vocals')} onCancel={() => finishVolumeEdit('vocals')} />
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整人声轨音量" data-tooltip="人声轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'vocals')} ondblclick={(event) => resetTrackDb(event, 'vocals')}>{volumeDbLabel('vocals')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.vocals.locked} type="button" aria-label={trackStates.vocals.locked ? '解锁人声轨' : '锁定人声轨'} data-tooltip={trackStates.vocals.locked ? '解锁人声轨｜允许移动、裁切和删除音频片段。' : '锁定人声轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('vocals')}>{#if trackStates.vocals.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整人声轨高度" data-tooltip="调整人声轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'vocals')} ondblclick={(event) => resetTrackHeight(event, 'vocals')}></button>
			</div>
				<div class="track-label track-audio track-background" role="group" aria-label="背景音乐轨" class:track-muted={trackStates.background.muted} class:track-locked={trackStates.background.locked} class:track-processing={trackRuntimeBusy('background')} class:drag-over={dragOverAudioTrackId === 'background' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'background' && dragOverAudioTrackPlacement === 'after'} style={audioTrackStyle('background')} onwheel={(event) => handleTrackHeaderWheel(event, 'background')} oncontextmenu={(event) => openTrackHeightContextMenu(event, 'background')} ondragover={(event) => markAudioTrackDropTarget(event, 'background')} ondrop={(event) => dropAudioTrack(event, 'background')}>
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
					<button class="track-toggle" class:active={trackStates.background.muted} type="button" aria-label="静音背景音乐轨" aria-pressed={trackStates.background.muted} data-tooltip="静音背景音乐轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('background')}>M</button>
					<button class="track-toggle" class:active={trackStates.background.solo} type="button" aria-label="独奏背景音乐轨" aria-pressed={trackStates.background.solo} data-tooltip="独奏背景音乐轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('background')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'background'}
							<AudioGainEditor valueDb={gainToDb(trackStates.background.volume, 2)} ariaLabel="背景音乐轨音量 dB" onChange={(db) => updateTrackDb('background', db)} onCommit={() => finishVolumeEdit('background')} onCancel={() => finishVolumeEdit('background')} />
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整背景音乐轨音量" data-tooltip="背景音乐轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'background')} ondblclick={(event) => resetTrackDb(event, 'background')}>{volumeDbLabel('background')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.background.locked} type="button" aria-label={trackStates.background.locked ? '解锁背景音乐轨' : '锁定背景音乐轨'} data-tooltip={trackStates.background.locked ? '解锁背景音乐轨｜允许移动、裁切和删除音频片段。' : '锁定背景音乐轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('background')}>{#if trackStates.background.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整背景音乐轨高度" data-tooltip="调整背景音乐轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'background')} ondblclick={(event) => resetTrackHeight(event, 'background')}></button>
			</div>
				<div class="track-label track-audio track-dub" role="group" aria-label="合成配音轨 1" class:track-muted={dubLaneState(0).muted} class:track-locked={dubLaneState(0).locked} class:track-processing={trackRuntimeBusy('dub')} class:drag-over={dragOverAudioTrackId === 'dub' && dragOverAudioTrackPlacement === 'before'} class:drag-over-after={dragOverAudioTrackId === 'dub' && dragOverAudioTrackPlacement === 'after'} class:dub-lane-drag-over={dragOverDubLane === 0} style={audioTrackStyle('dub')} onwheel={(event) => handleTrackHeaderWheel(event, 'dub')} oncontextmenu={(event) => openTrackHeightContextMenu(event, 'dub')} ondragover={(event) => { markAudioTrackDropTarget(event, 'dub'); markDubLaneDropTarget(event, 0); }} ondrop={(event) => { if (draggedDubLane === null) dropAudioTrack(event, 'dub'); else dropDubLane(event, 0); }}>
				<i class="track-title-level" style={`width:${trackMeterPercent('dub', 0)}%`}></i>
				<div>
					{#if editingDubLane === 0}
						<input class="track-name-input" data-dub-lane-name="0" aria-label="修改合成配音轨名称" value={editingDubLaneValue} oninput={(event) => (editingDubLaneValue = event.currentTarget.value)} onblur={() => finishDubLaneRename(0)} onkeydown={(event) => handleDubLaneNameKeydown(event, 0)} />
					{:else}
						<button class="track-name-button" type="button" disabled={dubLaneState(0).locked} data-tooltip="合成配音轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginDubLaneRename(0)}>{dubLaneName(0)}</button>
					{/if}
					<span>TTS / 声音克隆生成音频</span>
				</div>
				<div class="track-controls">
					{#if dubTrackLanes.length > 1}<button class="track-drag-handle lane-drag-handle" type="button" draggable="true" aria-label="拖动调整合成配音分轨 1 顺序" data-tooltip="调整配音分轨顺序｜拖动到另一条合成配音轨。" ondragstart={(event) => beginDubLaneReorder(event, 0)} ondragend={endDubLaneReorder}><GripVertical size={13} /></button>{/if}
					<button class="track-drag-handle" type="button" draggable="true" aria-label="拖动调整合成配音轨顺序" data-tooltip="调整轨道顺序｜拖动到其他音轨标题上方。" ondragstart={(event) => beginAudioTrackReorder(event, 'dub')} ondragend={endAudioTrackReorder}><GripVertical size={13} /></button>
					<button class="track-toggle" class:active={dubLaneState(0).muted} type="button" aria-label="静音合成配音轨 1" aria-pressed={dubLaneState(0).muted} data-tooltip="静音当前合成配音轨" onclick={() => toggleDubLaneMuted(0)}>M</button>
					<button class="track-toggle" class:active={dubLaneState(0).solo} type="button" aria-label="独奏合成配音轨 1" aria-pressed={dubLaneState(0).solo} data-tooltip="独奏当前合成配音轨" onclick={() => toggleDubLaneSolo(0)}>S</button>
					<div class="volume-control">
						{#if openDubLaneVolume === 0}
							<AudioGainEditor valueDb={gainToDb(dubLaneState(0).volume, 2)} ariaLabel="合成配音轨 1 音量 dB" onChange={(db) => updateDubLaneDb(0, db)} onCommit={() => (openDubLaneVolume = null)} onCancel={() => (openDubLaneVolume = null)} />
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整合成配音轨 1 音量" data-tooltip="当前分轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整。" onpointerdown={(event) => beginDubLaneVolumeScrub(event, 0)}>{gainToDb(dubLaneState(0).volume).toFixed(1)} dB</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={dubLaneState(0).locked} type="button" aria-label={dubLaneState(0).locked ? '解锁合成配音轨 1' : '锁定合成配音轨 1'} data-tooltip="只锁定当前合成配音轨" onclick={() => toggleDubLaneLocked(0)}>{#if dubLaneState(0).locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整合成配音轨高度" data-tooltip="调整合成配音轨高度：上下拖动，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'dub')} ondblclick={(event) => resetTrackHeight(event, 'dub')}></button>
			</div>
				{#each dubTrackLanes.slice(1) as _, laneOffset (laneOffset)}
				{@const laneIndex = laneOffset + 1}
				<div class="track-label track-audio track-dub secondary-dub-track" role="group" aria-label={`合成配音轨 ${laneIndex + 1}`} class:track-muted={dubLaneState(laneIndex).muted} class:track-locked={dubLaneState(laneIndex).locked} class:dub-lane-drag-over={dragOverDubLane === laneIndex} style={audioTrackStyle('dub')} onwheel={(event) => handleTrackHeaderWheel(event, 'dub')} oncontextmenu={(event) => openTrackHeightContextMenu(event, 'dub')} ondragover={(event) => markDubLaneDropTarget(event, laneIndex)} ondrop={(event) => dropDubLane(event, laneIndex)}>
					<i class="track-title-level" style={`width:${trackMeterPercent('dub', laneIndex)}%`}></i>
					<div>
						{#if editingDubLane === laneIndex}
							<input class="track-name-input" data-dub-lane-name={laneIndex} aria-label={`修改合成配音轨 ${laneIndex + 1} 名称`} value={editingDubLaneValue} oninput={(event) => (editingDubLaneValue = event.currentTarget.value)} onblur={() => finishDubLaneRename(laneIndex)} onkeydown={(event) => handleDubLaneNameKeydown(event, laneIndex)} />
						{:else}
							<button class="track-name-button" type="button" disabled={dubLaneState(laneIndex).locked} data-tooltip="合成配音分轨名称｜点击修改名称。" onclick={() => beginDubLaneRename(laneIndex)}>{dubLaneName(laneIndex)}</button>
						{/if}
						<span>独立分轨</span>
					</div>
					<div class="track-controls compact-shared-controls">
						<button class="track-drag-handle lane-drag-handle" type="button" draggable="true" aria-label={`拖动调整合成配音分轨 ${laneIndex + 1} 顺序`} data-tooltip="调整配音分轨顺序｜拖动到另一条合成配音轨。" ondragstart={(event) => beginDubLaneReorder(event, laneIndex)} ondragend={endDubLaneReorder}><GripVertical size={13} /></button>
						<button class="track-toggle" class:active={dubLaneState(laneIndex).muted} type="button" aria-label={`静音合成配音轨 ${laneIndex + 1}`} aria-pressed={dubLaneState(laneIndex).muted} data-tooltip="静音当前合成配音轨" onclick={() => toggleDubLaneMuted(laneIndex)}>M</button>
						<button class="track-toggle" class:active={dubLaneState(laneIndex).solo} type="button" aria-label={`独奏合成配音轨 ${laneIndex + 1}`} aria-pressed={dubLaneState(laneIndex).solo} data-tooltip="独奏当前合成配音轨" onclick={() => toggleDubLaneSolo(laneIndex)}>S</button>
						<div class="volume-control">
							{#if openDubLaneVolume === laneIndex}
								<AudioGainEditor valueDb={gainToDb(dubLaneState(laneIndex).volume, 2)} ariaLabel={`合成配音轨 ${laneIndex + 1} 音量 dB`} onChange={(db) => updateDubLaneDb(laneIndex, db)} onCommit={() => (openDubLaneVolume = null)} onCancel={() => (openDubLaneVolume = null)} />
							{:else}
								<button class="volume-db-button" type="button" aria-label={`调整合成配音轨 ${laneIndex + 1} 音量`} data-tooltip="当前分轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整。" onpointerdown={(event) => beginDubLaneVolumeScrub(event, laneIndex)}>{gainToDb(dubLaneState(laneIndex).volume).toFixed(1)} dB</button>
							{/if}
						</div>
						<button class="track-toggle lock-toggle" class:active={dubLaneState(laneIndex).locked} type="button" aria-label={dubLaneState(laneIndex).locked ? `解锁合成配音轨 ${laneIndex + 1}` : `锁定合成配音轨 ${laneIndex + 1}`} data-tooltip="只锁定当前合成配音轨" onclick={() => toggleDubLaneLocked(laneIndex)}>{#if dubLaneState(laneIndex).locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
					</div>
					<button class="track-resize-handle" type="button" aria-label="调整所有合成配音轨高度" data-tooltip="所有合成配音分轨共用高度；拖动调整，双击恢复默认高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'dub')} ondblclick={(event) => resetTrackHeight(event, 'dub')}></button>
				</div>
				{/each}
				{#if historyDropPreview && historyDropPreview.lane >= dubTrackLanes.length}
					<div class="track-label track-audio track-dub secondary-dub-track history-drop-new-lane" role="group" aria-label={`即将创建合成配音轨 ${historyDropPreview.lane + 1}`} style={audioTrackStyle('dub')}>
						<div><strong>合成配音 {historyDropPreview.lane + 1}</strong><span>松开后自动创建分轨</span></div>
					</div>
				{/if}
				{#each clipNewLaneIndexes as clipNewLaneIndex (clipNewLaneIndex)}
					<div class="track-label track-audio track-dub secondary-dub-track clip-drag-new-lane" role="group" aria-label={`拖入以创建合成配音轨 ${clipNewLaneIndex + 1}`} style={audioTrackStyle('dub')}>
						<div><strong>合成配音 {clipNewLaneIndex + 1}</strong><span>拖入片段后创建</span></div>
					</div>
				{/each}
				<button class="track-label-width-handle" type="button" aria-label="调整轨道标题宽度" data-tooltip="调整标题宽度：左右拖动改变所有轨道标题栏宽度。" onpointerdown={beginLabelColumnResize}></button>
		</div>

		<div
			class="track-canvas"
			use:observeTimelineViewport
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
				class:razor-tool={activeTool === 'razor'}
				data-render-level={timelineRenderLevel}
				style={`width:${timelineContentPixelWidth(timelineViewportWidth, timelineZoom)}px;--processing-left:${timelineScrollLeft}px;--processing-width:${Math.max(1, timelineViewportWidth)}px`}
				bind:this={timelineContentEl}
				role="application"
				tabindex="-1"
				data-timeline-keyboard-scope
				aria-label="视频配音时间线"
				onpointerdown={handleTimelinePointerDown}
				ondblclick={handleTimelineDoubleClick}
				oncontextmenu={openTimelineContextMenu}
				onpointermove={handleTimelinePointerMove}
				onpointerup={endTimelinePointerWork}
				onpointercancel={cancelTimelinePointerWork}
				onlostpointercapture={cancelTimelinePointerWork}
				onpointerleave={endHoverScrub}
				>
				<div class="timeline-ruler">
					{#if showFramePrecision}
						{#each visibleSecondTicks as tick}
							<span class="second-tick major" style={`left:${tick.percent}%`}>
								<i></i>
								<b>{tick.label}</b>
							</span>
						{/each}
						{#each visibleFrameSubTicks as tick}
							<span class="frame-tick" class:major={tick.major} style={`left:${tick.percent}%`}>
								<i></i>
								{#if tick.label}<b>{tick.label}</b>{/if}
							</span>
						{/each}
					{:else}
						{#each visibleTimelineTicks as tick}
							<span class:major={tick.major} class:medium={tick.level === 1} style={`left:${tick.percent}%`}>
								<i></i>
								{#if tick.label}<b>{tick.label}</b>{/if}
							</span>
						{/each}
					{/if}
					{#if showFramePrecision}
						<div class="frame-coverage playhead-frame" style={`left:${playheadPercent}%;width:${playheadFrameWidthPercent}%`} aria-label={`当前指针覆盖第 ${playheadFrame.frame} 帧`}></div>
						{#if hoverFrame && hoverScrubEnabled && !isPlaying}
							<div class="frame-coverage hover-frame" style={`left:${(hoverFrame.startMs / timelineDurationMs) * 100}%;width:${((hoverFrame.endMs - hoverFrame.startMs) / timelineDurationMs) * 100}%`} aria-label={`鼠标预览覆盖第 ${hoverFrame.frame} 帧`}></div>
						{/if}
					{/if}
						<div class="preview-cache-strip" aria-label={playbackReadiness ? `播放就绪 ${Math.round(playbackCacheProgress * 100)}%` : previewCache ? '预览帧已完成，正在检测媒体' : '播放缓存尚未生成'}>
							{#each visiblePlaybackCacheRanges as range}
								<i
									class="cache-range"
									class:cache-ready={range.status === 'ready'}
									class:cache-rendering={range.status === 'loading'}
									class:cache-failed={range.status === 'failed'}
									class:cache-empty={range.status === 'empty'}
									style={`left:${(range.start_ms / Math.max(1, timelineDurationMs)) * 100}%;width:${((range.end_ms - range.start_ms) / Math.max(1, timelineDurationMs)) * 100}%`}
									role="img"
									aria-label={`${(range.start_ms / 1000).toFixed(1)}s 至 ${(range.end_ms / 1000).toFixed(1)}s：${range.status === 'ready' ? '可即时播放' : range.status === 'loading' ? '缓存中' : range.status === 'failed' ? '播放失败' : '未缓存'}${range.blockers.length ? `，${range.blockers.join('、')}` : ''}`}
								></i>
							{/each}
						</div>
				</div>
				<div class="playhead" style={`left:${playheadPercent}%`}></div>
				{#if hoverTimeMs !== null && (hoverScrubEnabled || activeTool === 'razor') && !isPlaying}
					<div class="hover-playhead" style={`left:${((hoverFrame?.startMs ?? hoverTimeMs) / timelineDurationMs) * 100}%`} aria-hidden="true"></div>
				{/if}
				{#if snapGuideMs !== null && showFramePrecision}
					<div class="frame-snap-guide" style={`left:${(snapGuideMs / timelineDurationMs) * 100}%`} aria-hidden="true"><span>{frameIndexAtTime(snapGuideMs, timelineFrameRate)}f</span></div>
				{/if}
				{#if hasRangeSelection}
					<div class="range-selection" style={`left:${rangeLeftPercent}%;width:${rangeWidthPercent}%`}></div>
				{/if}
				{#if marqueeRect && marqueeState?.moved}
					<div class="clip-marquee" style={`left:${marqueeRect.left}px;top:${marqueeRect.top}px;width:${marqueeRect.width}px;height:${marqueeRect.height}px`} aria-hidden="true"></div>
				{/if}
				{#if rangeStartMs !== null}<button class="range-handle in" type="button" style={`left:${rangeStartPercent}%`} aria-label={`拖动选区入点，当前 ${rangeStartMs} 毫秒`} data-tooltip="选区入点｜拖动修改范围开始位置。快捷键：I" onpointerdown={(event) => beginSelectionDrag(event, 'start')}>I</button>{/if}
				{#if rangeEndMs !== null}<button class="range-handle out" type="button" style={`left:${rangeEndPercent}%`} aria-label={`拖动选区出点，当前 ${rangeEndMs} 毫秒`} data-tooltip="选区出点｜拖动修改范围结束位置。快捷键：O" onpointerdown={(event) => beginSelectionDrag(event, 'end')}>O</button>{/if}
				<div class="track-row subtitle row-subtitle row-asr-subtitle" data-track-row data-track-id="subtitles" role="group" aria-label="ASR 字幕轨时间线" aria-busy={trackRuntimeBusy('subtitles')} class:locked={trackInteractionLocked('subtitles')} class:processing={trackRuntimeBusy('subtitles')} style={subtitleTrackStyle('subtitles')}>
					{#if timelineRenderLevel === 'overview' && subtitleDisplay.tracks.asr.cues.length}
						<TimelineOverviewCanvas items={subtitleDisplay.tracks.asr.cues} durationMs={timelineDurationMs} tone="asr" ariaLabel="ASR 字幕概览，可点击选择字幕" selectedItemIds={selectedTimelineItems.filter((item) => item.trackId === 'subtitles').map((item) => item.itemId)} onSelect={(item, event) => selectOverviewSubtitle(event, 'asr', item)} />
					{:else if subtitleDisplay.tracks.asr.provisional}
						{#each visibleAsrDisplayCues as displayCue (displayCue.key)}
							<button
								class="cue-chip cue-asr preview-cue"
								class:active={subtitleDisplay.selectedCue?.key === displayCue.key}
								class:phase-timing={subtitleDisplay.tracks.asr.phaseLabel === '校时与断句'}
								data-subtitle-item-id={displayCue.id}
								type="button"
								style={`left:${clipLeft(displayCue.start_ms)}%;width:${clipWidth(displayCue.start_ms, displayCue.end_ms)}%`}
								aria-label={`查看阶段 ASR 字幕 ${displayCue.id}`}
								onpointerdown={(event) => event.stopPropagation()}
								onclick={() => onSelectSubtitleDisplayCue(displayCue)}
							>
								<strong>{displayCue.id.replace(/^(preview_|cue_)/, '#')}</strong>
								<span class="cue-text" use:cueTextMarquee><span>{displayCue.text}</span></span>
								<em>{durationLabel(displayCue.end_ms - displayCue.start_ms)}</em>
							</button>
						{/each}
						<div class="asr-preview-phase">
							{#if subtitleDisplay.tracks.asr.isActive}<CommandSpinner size={11} />{:else}<Check size={11} />{/if}
							{subtitleDisplay.tracks.asr.phaseLabel}
						</div>
					{:else if subtitleDisplay.tracks.asr.cues.length}
						{#each visibleAsrDisplayCues as displayCue (displayCue.key)}
							{@const cue = displayCue.raw as VideoLocalizationCue}
							<button
								class="cue-chip cue-asr"
								data-subtitle-item-id={cue.cue_id}
							class:active={cue.cue_id === selectedCueId}
							class:selected={timelineItemSelected({ kind: 'subtitle', trackId: 'subtitles', itemId: cue.cue_id })}
							class:tts-anchor={ttsSelectionSession.anchor?.kind === 'source' && ttsSelectionSession.anchor.itemId === cue.cue_id}
							class:tts-source={ttsSelectionSession.sourceCueIds.includes(cue.cue_id) || ttsSelectionSession.mappedSourceCueIds.includes(cue.cue_id)}
								class:dragging={dragState?.itemId === cue.cue_id && dragState.trackKind === 'asr'}
								class:timing-review={!confirmedTimingCueIds.has(cue.cue_id) && (cue.timing_confidence === 'low' || cue.quality_flags?.includes('timing_review_required'))}
								class:pointer-trim-disabled={!cueAllowsPointerTrim(cue, 'asr')}
								type="button"
								disabled={trackRuntimeBusy('subtitles')}
								style={`left:${cueLeft(cue, 'asr')}%;width:${cueWidth(cue, 'asr')}%`}
							onclick={(event) => selectSubtitleTimelineItemAtPointer(event, 'asr', cue)}
								onpointerdown={(event) => { if (!cutSubtitleAtPointer(event, 'asr', cue.cue_id)) startCueDrag(event, cue, 'move', 'asr'); }}
								ondblclick={(event) => playSubtitleTimelineItem(event, 'asr', cue)}
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
								<span class="cue-text" use:cueTextMarquee><span>{cueLabel(cue)}</span></span>
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
							<button class="track-inline-action" type="button" aria-busy={asrBusy} disabled={asrGenerateCommand.disabled} data-tooltip={asrGenerateCommand.description} onpointerdown={(event) => event.stopPropagation()} onclick={() => void asrGenerateCommand.onSelect()}>
								{#if asrBusy}<CommandSpinner size={11} />{:else}<Captions size={11} />{/if} {asrBusy ? '正在听写' : asrGenerateCommand.label}
							</button>
						</div>
					{:else}
						<div class="pending-block">人声轨有可用音频后，可听写生成 ASR 字幕</div>
					{/if}
				</div>
				<div class="track-row subtitle row-subtitle row-localized-subtitle" data-track-row data-track-id="localizedSubtitles" role="group" aria-label="本土化字幕轨时间线" aria-busy={trackRuntimeBusy('localizedSubtitles')} class:locked={trackInteractionLocked('localizedSubtitles')} class:processing={trackRuntimeBusy('localizedSubtitles')} style={subtitleTrackStyle('localizedSubtitles')}>
					{#if timelineRenderLevel === 'overview' && subtitleDisplay.tracks.localized.cues.length}
						<TimelineOverviewCanvas items={subtitleDisplay.tracks.localized.cues} durationMs={timelineDurationMs} tone="localized" ariaLabel="本土化字幕概览，可点击选择字幕" selectedItemIds={selectedTimelineItems.filter((item) => item.trackId === 'localizedSubtitles').map((item) => item.itemId)} onSelect={(item, event) => selectOverviewSubtitle(event, 'localized', item)} />
					{:else}
					{#each visibleLocalizedDisplayCues as displayCue (displayCue.key)}
						{@const cue = displayCue.raw as VideoLocalizationSubtitleCue}
							{#if displayCue.reviewable}
								{@const dubCue = dubSubtitleCue(displayCue)}
								{#if dubCue}
								<button
									class="cue-chip cue-localized review-cue"
									class:active={subtitleDisplay.selectedCue?.key === displayCue.key}
									class:dragging={dragState?.persistenceTarget === 'dub' && dragState.itemId === dubCue.subtitle_id}
									class:timing-review={dubCue.needs_review === true}
									data-subtitle-item-id={displayCue.id}
									type="button"
									style={`left:${cueLeft(dubCue, 'localized')}%;width:${cueWidth(dubCue, 'localized')}%`}
									aria-label={`查看并调整合成配音字幕 ${displayCue.id}${dubCue.needs_review ? '，建议复听' : ''}`}
									data-tooltip={dubCue.needs_review
										? `建议复听：${dubCue.quality_flags.join('、') || '识别或人物归属需要确认'}`
										: undefined}
									onpointerdown={(event) => event.stopPropagation()}
								onclick={() => onSelectSubtitleDisplayCue(displayCue)}
							>
								<span class="cue-handle" role="slider" tabindex="-1" aria-label="调整合成配音字幕入点" aria-valuemin="0" aria-valuemax={subtitleTimelineLimitMs} aria-valuenow={cueLiveTime(dubCue, 'localized').start_ms} onpointerdown={(event) => startDubSubtitleDrag(event, displayCue, 'trim-start')}></span>
								<strong>{displayCue.id.replace('localized_', '#')}</strong>
								<span class="cue-text" use:cueTextMarquee><span>{displayCue.text}</span></span>
								<em>{durationLabel(cueLiveTime(dubCue, 'localized').end_ms - cueLiveTime(dubCue, 'localized').start_ms)}</em>
								<span class="cue-handle" role="slider" tabindex="-1" aria-label="调整合成配音字幕出点" aria-valuemin="0" aria-valuemax={subtitleTimelineLimitMs} aria-valuenow={cueLiveTime(dubCue, 'localized').end_ms} onpointerdown={(event) => startDubSubtitleDrag(event, displayCue, 'trim-end')}></span>
							</button>
								{/if}
							{:else if !displayCue.editable}
								<button
									class="cue-chip cue-localized preview-cue"
									class:active={subtitleDisplay.selectedCue?.key === displayCue.key}
									data-subtitle-item-id={displayCue.id}
									type="button"
									style={`left:${clipLeft(displayCue.start_ms)}%;width:${clipWidth(displayCue.start_ms, displayCue.end_ms)}%`}
									aria-label={`查看${displayCue.phaseLabel ?? '只读本土化字幕'} ${displayCue.id}`}
									onpointerdown={(event) => event.stopPropagation()}
									onclick={() => onSelectSubtitleDisplayCue(displayCue)}
								>
									<strong>{displayCue.id.replace('localized_', '#')}</strong>
									<span class="cue-text" use:cueTextMarquee><span>{displayCue.text}</span></span>
									<em>{durationLabel(displayCue.end_ms - displayCue.start_ms)}</em>
								</button>
						{:else}
						<button
							class="cue-chip cue-localized"
							data-subtitle-item-id={cue.subtitle_id}
							class:selected={timelineItemSelected({ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: cue.subtitle_id })}
							class:tts-anchor={ttsSelectionSession.anchor?.kind === 'target' && ttsSelectionSession.anchor.itemId === cue.subtitle_id}
							class:tts-target={ttsSelectionSession.localizedSubtitleIds.includes(cue.subtitle_id) || ttsSelectionSession.mappedLocalizedSubtitleIds.includes(cue.subtitle_id)}
							class:dragging={dragState?.itemId === cue.subtitle_id && dragState.trackKind === 'localized'}
							class:pointer-trim-disabled={!cueAllowsPointerTrim(cue, 'localized')}
							type="button"
							style={`left:${cueLeft(cue, 'localized')}%;width:${cueWidth(cue, 'localized')}%`}
							onclick={(event) => selectSubtitleTimelineItemAtPointer(event, 'localized', cue)}
							onpointerdown={(event) => { if (!cutSubtitleAtPointer(event, 'localized', cue.subtitle_id)) startCueDrag(event, cue, 'move', 'localized'); }}
							ondblclick={(event) => playSubtitleTimelineItem(event, 'localized', cue)}
							aria-label={`本土化字幕 ${cue.subtitle_id}`}
						>
							<span class="cue-handle" role="slider" tabindex="-1" aria-label="调整本土化字幕入点" aria-valuemin="0" aria-valuemax={subtitleTimelineLimitMs} aria-valuenow={cueLiveTime(cue, 'localized').start_ms} onpointerdown={(event) => startCueDrag(event, cue, 'trim-start', 'localized')}></span>
							<strong>{cue.subtitle_id.replace('localized_', '#')}</strong>
							<span class="cue-text" use:cueTextMarquee><span>{displayCue.text || '未命名本土化字幕'}</span></span>
							<em>{durationLabel(cueLiveTime(cue, 'localized').end_ms - cueLiveTime(cue, 'localized').start_ms)}</em>
							<span class="cue-handle" role="slider" tabindex="-1" aria-label="调整本土化字幕出点" aria-valuemin="0" aria-valuemax={subtitleTimelineLimitMs} aria-valuenow={cueLiveTime(cue, 'localized').end_ms} onpointerdown={(event) => startCueDrag(event, cue, 'trim-end', 'localized')}></span>
						</button>
						{/if}
					{/each}
					{#if !subtitleDisplay.tracks.localized.cues.length}
						{#if draft?.cues.length}
							<div class="pending-actions" aria-label="本土化字幕轨可用操作">
								<button class="track-inline-action" type="button" aria-busy={localizationBusy} onclick={onGenerateLocalization} disabled={trackStates.localizedSubtitles.locked || localizationBusy} data-tooltip="生成本土化字幕｜理解原文、人物和文化语境，生成上屏字幕与配音台词。" onpointerdown={(event) => event.stopPropagation()}>
									{#if localizationBusy}<CommandSpinner size={11} />{:else}<Wand2 size={11} />{/if} {localizationBusy ? '正在生成' : '生成本土化字幕'}
								</button>
								<button class="track-inline-action secondary" type="button" aria-busy={importingLocalizedSrt} onclick={onImportLocalizedSrt} disabled={trackStates.localizedSubtitles.locked || localizationBusy || importingLocalizedSrt} data-tooltip="导入本土化 SRT｜使用已经准备好的本土化字幕文件。" onpointerdown={(event) => event.stopPropagation()}>
									{#if importingLocalizedSrt}<CommandSpinner size={11} /> 导入中{:else}<FileUp size={11} /> 导入 SRT{/if}
								</button>
							</div>
						{:else}
							<div class="pending-block">ASR 字幕轨有内容后，可生成本土化字幕</div>
						{/if}
					{/if}
					{/if}
				</div>
				<div class="track-row row-original" data-track-row data-track-id="original" data-audio-selection-track="original" aria-busy={trackRuntimeBusy('original')} class:muted={trackStates.original.muted} class:locked={trackInteractionLocked('original')} class:processing={trackRuntimeBusy('original')} style={audioTrackStyle('original')}>
					{#if clipsForTrack('original').length}
						{#if timelineRenderLevel === 'overview'}
							<TimelineOverviewCanvas items={clipsForTrack('original')} durationMs={timelineDurationMs} tone="source" muted={trackStates.original.muted} gain={trackStates.original.volume} waveformSrc={overviewWaveformSrc('original')} waveformItem={overviewWaveformClip('original')} ariaLabel="原音轨概览，可点击选择片段" selectedItemIds={selectedTimelineItems.filter((item) => item.trackId === 'original').map((item) => item.itemId)} onSelect={(item, event) => selectOverviewAudio(event, 'original', item)} />
						{:else}
						{#each visibleClipsForTrack('original') as clip (clip.clip_id)}
							<EditableAudioClip {clip} waveformSrc={clipWaveformSrc(clip, 'original')} waveformPreviewSrc={clipWaveformPreviewSrc(clip, 'original')} label={clipLabel(clip, 'original')} tone={clipTone('original')} gain={trackStates.original.volume} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} selected={timelineItemSelected({ kind: 'audio', trackId: 'original', itemId: clip.clip_id })} locked={trackInteractionLocked('original', clip.clip_id)} processing={trackRuntimeBusy('original', clip.clip_id)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={(event) => selectAudioTimelineItem('original', clip.clip_id, event.ctrlKey || event.metaKey)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
						{/if}
					{:else if canAttemptOriginalRecovery}
						<div class="pending-actions single" aria-label="原音轨恢复操作">
							<button class="track-inline-action" type="button" aria-busy={extractingAudio} onclick={requestOriginalAudioRecovery} disabled={extractingAudio || trackStates.original.locked} data-tooltip="恢复原音轨｜源音频仍在时重新挂载到时间线；文件已经丢失时从原视频重新抽取。" onpointerdown={(event) => event.stopPropagation()}>
								{#if extractingAudio}<CommandSpinner size={11} />{:else}<RefreshCw size={11} />{/if} {extractingAudio ? '正在恢复' : hasSourceAudio ? '恢复原音轨' : '重新生成原音轨'}
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
						{#if timelineRenderLevel === 'overview'}
							<TimelineOverviewCanvas items={clipsForTrack('vocals')} durationMs={timelineDurationMs} tone="vocals" muted={trackStates.vocals.muted} gain={trackStates.vocals.volume} waveformSrc={overviewWaveformSrc('vocals')} waveformItem={overviewWaveformClip('vocals')} ariaLabel="人声轨概览，可点击选择片段" selectedItemIds={selectedTimelineItems.filter((item) => item.trackId === 'vocals').map((item) => item.itemId)} onSelect={(item, event) => selectOverviewAudio(event, 'vocals', item)} />
						{:else}
						{#each visibleClipsForTrack('vocals') as clip (clip.clip_id)}
							<EditableAudioClip {clip} waveformSrc={clipWaveformSrc(clip, 'vocals')} waveformPreviewSrc={clipWaveformPreviewSrc(clip, 'vocals')} label={clipLabel(clip, 'vocals')} tone={clipTone('vocals')} gain={trackStates.vocals.volume} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} selected={timelineItemSelected({ kind: 'audio', trackId: 'vocals', itemId: clip.clip_id })} locked={trackInteractionLocked('vocals', clip.clip_id)} processing={trackRuntimeBusy('vocals', clip.clip_id)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={(event) => selectAudioTimelineItem('vocals', clip.clip_id, event.ctrlKey || event.metaKey)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
						{/if}
					{:else if hasSourceAudio}
						<div class="pending-actions single">
							<button class="track-inline-action" type="button" aria-busy={separatingStems} onclick={onSeparateStems} disabled={stemsReady || separatingStems || trackStates.vocals.locked} data-tooltip="分离人声与背景｜一次处理会同时创建人声轨和背景音乐轨。" onpointerdown={(event) => event.stopPropagation()}>
								{#if separatingStems}<CommandSpinner size={11} />{:else}<Mic2 size={11} />{/if} {separatingStems ? '正在分离' : '分离人声与背景'}
							</button>
						</div>
					{:else}
						<div class="pending-block">原音轨就绪后，可分离出人声音频</div>
					{/if}
				</div>
				<div class="track-row row-background" data-track-row data-track-id="background" data-audio-selection-track="background" aria-busy={trackRuntimeBusy('background')} class:muted={trackStates.background.muted} class:locked={trackInteractionLocked('background')} class:processing={trackRuntimeBusy('background')} style={audioTrackStyle('background')}>
					{#if clipsForTrack('background').length}
						{#if timelineRenderLevel === 'overview'}
							<TimelineOverviewCanvas items={clipsForTrack('background')} durationMs={timelineDurationMs} tone="music" muted={trackStates.background.muted} gain={trackStates.background.volume} waveformSrc={overviewWaveformSrc('background')} waveformItem={overviewWaveformClip('background')} ariaLabel="背景轨概览，可点击选择片段" selectedItemIds={selectedTimelineItems.filter((item) => item.trackId === 'background').map((item) => item.itemId)} onSelect={(item, event) => selectOverviewAudio(event, 'background', item)} />
						{:else}
						{#each visibleClipsForTrack('background') as clip (clip.clip_id)}
							<EditableAudioClip {clip} waveformSrc={clipWaveformSrc(clip, 'background')} waveformPreviewSrc={clipWaveformPreviewSrc(clip, 'background')} label={clipLabel(clip, 'background')} tone={clipTone('background')} gain={trackStates.background.volume} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} selected={timelineItemSelected({ kind: 'audio', trackId: 'background', itemId: clip.clip_id })} locked={trackInteractionLocked('background', clip.clip_id)} processing={trackRuntimeBusy('background', clip.clip_id)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={(event) => selectAudioTimelineItem('background', clip.clip_id, event.ctrlKey || event.metaKey)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
						{/if}
					{:else if hasSourceAudio}
						<div class="pending-actions single">
							<button class="track-inline-action" type="button" aria-busy={separatingStems} onclick={onSeparateStems} disabled={stemsReady || separatingStems || trackStates.background.locked} data-tooltip="分离人声与背景｜一次处理会同时创建人声轨和背景音乐轨。" onpointerdown={(event) => event.stopPropagation()}>
								{#if separatingStems}<CommandSpinner size={11} />{:else}<Mic2 size={11} />{/if} {separatingStems ? '正在分离' : '分离人声与背景'}
							</button>
						</div>
					{:else}
						<div class="pending-block">原音轨就绪后，可分离出背景音乐</div>
					{/if}
				</div>
				<div class="track-row row-dub" data-track-row data-track-id="dub" data-dub-lane="0" data-audio-selection-track="dub" role="group" aria-label="合成配音轨 1 时间线" aria-busy={trackRuntimeBusy('dub')} class:muted={dubLaneState(0).muted} class:locked={dubLaneState(0).locked} class:processing={trackRuntimeBusy('dub')} class:history-drop-target={historyDropPreview?.lane === 0} class:clip-lane-drop-target={clipLaneDropPreviewForLane(0)?.allowed === true} class:clip-lane-drop-blocked={clipLaneDropPreviewForLane(0)?.allowed === false} style={audioTrackStyle('dub')}>
					{#if historyDropPreview?.lane === 0}
						<div class="history-drop-ghost" style={`left:${clipLeft(historyDropPreview.startMs)}%;width:${clipWidth(historyDropPreview.startMs, historyDropPreview.endMs)}%`} aria-label="配音落位预览"><i></i><span>{draggingTtsHistory?.input_text || '配音记录'}</span></div>
					{/if}
					{#if clipLaneDropPreviewForLane(0)}
						{#each clipLaneDropPreviewForLane(0)?.items ?? [] as item (item.clipId)}
							<div class="clip-lane-drop-ghost" class:blocked={clipLaneDropPreviewForLane(0)?.allowed === false} style={`left:${clipLeft(item.startMs)}%;width:${clipWidth(item.startMs, item.endMs)}%`} aria-label={clipLaneDropPreviewForLane(0)?.allowed ? '音频片段落位预览' : '目标轨道无法放置'}></div>
						{/each}
					{/if}
					{#if clipsForTrack('dub').length}
						{#if timelineRenderLevel === 'overview'}
							<TimelineOverviewCanvas items={dubTrackLanes[0] ?? []} durationMs={timelineDurationMs} tone="dub" muted={dubLaneState(0).muted} ariaLabel="合成配音轨 1 概览，可点击选择片段" selectedItemIds={selectedTimelineItems.filter((item) => item.trackId === 'dub').map((item) => item.itemId)} onSelect={(item, event) => selectOverviewAudio(event, 'dub', item)} />
						{:else}
							{#each visibleDubLaneClips(0) as clip (clip.clip_id)}
								<EditableAudioClip {clip} waveformSrc={clipWaveformSrc(clip, 'dub')} waveformPreviewSrc={clipWaveformPreviewSrc(clip, 'dub')} label={clipLabel(clip, 'dub')} tone={clipTone('dub')} gain={dubLaneState(0).volume} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipIncludedInDrag(clip.clip_id)} selected={timelineItemSelected({ kind: 'audio', trackId: 'dub', itemId: clip.clip_id })} locked={trackInteractionLocked('dub', clip.clip_id)} processing={trackRuntimeBusy('dub', clip.clip_id) || ['queued', 'running', 'postprocessing', 'retrying', 'applying'].includes(String(clip.status ?? ''))} progress={typeof clip.generation_progress === 'number' ? clip.generation_progress : null} coverage={timelineClipVerificationCoverage(clip, ttsCoverageByIdentity)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={(event) => selectAudioTimelineItem('dub', clip.clip_id, event.ctrlKey || event.metaKey)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
							{/each}
						{/if}
					{:else}
						<div class="pending-block">准备字幕与音色后，可生成合成配音片段</div>
					{/if}
				</div>
				{#each dubTrackLanes.slice(1) as lane, laneOffset (laneOffset)}
					<div class="track-row row-dub secondary-dub-row" data-track-row data-track-id="dub" data-dub-lane={laneOffset + 1} data-audio-selection-track="dub" role="group" aria-label={`合成配音轨 ${laneOffset + 2} 时间线`} class:muted={dubLaneState(laneOffset + 1).muted} class:locked={dubLaneState(laneOffset + 1).locked} class:history-drop-target={historyDropPreview?.lane === laneOffset + 1} class:clip-lane-drop-target={clipLaneDropPreviewForLane(laneOffset + 1)?.allowed === true} class:clip-lane-drop-blocked={clipLaneDropPreviewForLane(laneOffset + 1)?.allowed === false} style={audioTrackStyle('dub')}>
						{#if historyDropPreview?.lane === laneOffset + 1}
							<div class="history-drop-ghost" style={`left:${clipLeft(historyDropPreview.startMs)}%;width:${clipWidth(historyDropPreview.startMs, historyDropPreview.endMs)}%`} aria-label="配音落位预览"><i></i><span>{draggingTtsHistory?.input_text || '配音记录'}</span></div>
						{/if}
						{#if clipLaneDropPreviewForLane(laneOffset + 1)}
							{#each clipLaneDropPreviewForLane(laneOffset + 1)?.items ?? [] as item (item.clipId)}
								<div class="clip-lane-drop-ghost" class:blocked={clipLaneDropPreviewForLane(laneOffset + 1)?.allowed === false} style={`left:${clipLeft(item.startMs)}%;width:${clipWidth(item.startMs, item.endMs)}%`} aria-label={clipLaneDropPreviewForLane(laneOffset + 1)?.allowed ? '音频片段落位预览' : '目标轨道无法放置'}></div>
							{/each}
						{/if}
						{#if timelineRenderLevel === 'overview'}
							<TimelineOverviewCanvas items={lane} durationMs={timelineDurationMs} tone="dub" muted={dubLaneState(laneOffset + 1).muted} ariaLabel={`合成配音轨 ${laneOffset + 2} 概览，可点击选择片段`} selectedItemIds={selectedTimelineItems.filter((item) => item.trackId === 'dub').map((item) => item.itemId)} onSelect={(item, event) => selectOverviewAudio(event, 'dub', item)} />
						{:else}
							{#each visibleDubLaneClips(laneOffset + 1) as clip (clip.clip_id)}
								<EditableAudioClip {clip} waveformSrc={clipWaveformSrc(clip, 'dub')} waveformPreviewSrc={clipWaveformPreviewSrc(clip, 'dub')} label={clipLabel(clip, 'dub')} tone={clipTone('dub')} gain={dubLaneState(laneOffset + 1).volume} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipIncludedInDrag(clip.clip_id)} selected={timelineItemSelected({ kind: 'audio', trackId: 'dub', itemId: clip.clip_id })} locked={trackInteractionLocked('dub', clip.clip_id)} processing={trackRuntimeBusy('dub', clip.clip_id) || ['queued', 'running', 'postprocessing', 'retrying', 'applying'].includes(String(clip.status ?? ''))} progress={typeof clip.generation_progress === 'number' ? clip.generation_progress : null} coverage={timelineClipVerificationCoverage(clip, ttsCoverageByIdentity)} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onSelect={(event) => selectAudioTimelineItem('dub', clip.clip_id, event.ctrlKey || event.metaKey)} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
							{/each}
						{/if}
					</div>
				{/each}
				{#if historyDropPreview && historyDropPreview.lane >= dubTrackLanes.length}
					<div class="track-row row-dub secondary-dub-row history-drop-new-row history-drop-target" data-track-row data-track-id="dub" data-dub-lane={historyDropPreview.lane} data-audio-selection-track="dub" role="group" aria-label={`即将创建合成配音轨 ${historyDropPreview.lane + 1} 时间线`} style={audioTrackStyle('dub')}>
						<div class="history-drop-ghost" style={`left:${clipLeft(historyDropPreview.startMs)}%;width:${clipWidth(historyDropPreview.startMs, historyDropPreview.endMs)}%`} aria-label="配音落位预览"><i></i><span>{draggingTtsHistory?.input_text || '配音记录'}</span></div>
					</div>
				{/if}
				{#each clipNewLaneIndexes as clipNewLaneIndex (clipNewLaneIndex)}
					<div class="track-row row-dub secondary-dub-row clip-drag-new-row" data-track-row data-track-id="dub" data-dub-lane={clipNewLaneIndex} data-audio-selection-track="dub" role="group" aria-label={`拖入以创建合成配音轨 ${clipNewLaneIndex + 1} 时间线`} class:clip-lane-drop-target={clipLaneDropPreviewForLane(clipNewLaneIndex)?.allowed === true} class:clip-lane-drop-blocked={clipLaneDropPreviewForLane(clipNewLaneIndex)?.allowed === false} style={audioTrackStyle('dub')}>
					{#if clipLaneDropPreviewForLane(clipNewLaneIndex)}
						{#each clipLaneDropPreviewForLane(clipNewLaneIndex)?.items ?? [] as item (item.clipId)}
							<div class="clip-lane-drop-ghost" class:blocked={clipLaneDropPreviewForLane(clipNewLaneIndex)?.allowed === false} style={`left:${clipLeft(item.startMs)}%;width:${clipWidth(item.startMs, item.endMs)}%`} aria-label={clipLaneDropPreviewForLane(clipNewLaneIndex)?.allowed ? '新配音轨落位预览' : '新配音轨无法放置'}></div>
						{/each}
					{/if}
					</div>
				{/each}
			</div>
		</div>
		<div class="timeline-edge-shadow left" aria-hidden="true"></div>
		<div class="timeline-edge-shadow right" aria-hidden="true"></div>
	</div>
	<div class="timeline-function-bar" aria-label="时间线功能区">
		<div class="function-group" aria-label="预览缓存">
			<span class="function-group-label">缓存</span>
			<button class="function-btn cache-refresh" type="button" aria-label="刷新播放缓存" aria-busy={previewCacheRefreshing} data-tooltip={previewCache ? `刷新播放缓存｜当前可即时播放 ${playbackCacheReadyChunks}/${playbackCacheTotalChunks} 段。会重新加载播放器并重建鼠标预览画面，不删除已生成的音频代理。` : '刷新播放缓存｜重新加载播放器并重建时间线鼠标预览画面。'} onclick={() => onRefreshPreviewCache?.()} disabled={!hasRecoverableVideo || previewCacheRefreshing}>
				{#if previewCacheRefreshing}<CommandSpinner size={13} />{:else}<RefreshCw size={13} />{/if}
				<span class="cache-refresh-copy">刷新缓存 <small>{previewCache || playbackReadiness ? `${Math.round(playbackCacheProgress * 100)}%` : '--'}</small></span>
			</button>
		</div>
		<span class="function-divider" aria-hidden="true"></span>
		<div class="function-group" aria-label="选区工作流">
			<span class="function-group-label">选区</span>
			<button class="function-btn" type="button" onclick={setRangeFromSelectedCue} disabled={!canEditSelectedCue} aria-label="用当前字幕设置选区" data-tooltip="用当前字幕设置选区：把当前字幕的入点和出点作为样音范围。快捷键：R"><Captions size={13} /> 字幕设为选区</button>
			<button class="function-btn primary" type="button" onclick={() => handleRangeAction(onGenerateToSelection)} disabled={!hasRangeSelection} aria-label="按选区打开配音" data-tooltip={hasRangeSelection ? '打开配音：保留当前时间范围，并打开右侧配音面板继续选择字幕和调整参数。快捷键：G' : '先设置一个时间范围，再打开配音面板。快捷键：G'}><Wand2 size={13} /> 打开配音</button>
		</div>
		<span class="function-divider" aria-hidden="true"></span>
		<div class="function-group semantic-group-tools" aria-label="配音语义分组">
			<span class="function-group-label">配音组</span>
			<button class="function-btn" type="button" aria-busy={semanticGroupingBusy} onclick={() => onGenerateSemanticTtsGroups?.()} disabled={!draft?.localized_subtitles.length || semanticGroupingBusy} aria-label="按语义组合配音字幕" data-tooltip="语义成组：按连续语义、说话人和场景，把相邻本土化字幕组合成适合一次配音的长段。">
				{#if semanticGroupingBusy}<CommandSpinner size={13} />{:else}<Layers3 size={13} />{/if} {semanticGroupingBusy ? '分组中' : '语义成组'}
			</button>
			{#if semanticTtsGroups.length}
				<label class="semantic-group-picker">
					<span class="visually-hidden">配音语义组编号</span>
					<input type="number" min="1" max={semanticTtsGroups.length} step="1" bind:value={semanticGroupNumber} onkeydown={(event) => { if (event.key === 'Enter') selectSemanticGroupByNumber(); }} aria-label={`配音语义组编号，共 ${semanticTtsGroups.length} 组`} />
					<small>/ {semanticTtsGroups.length}</small>
				</label>
				<button class="function-btn" type="button" onclick={selectSemanticGroupByNumber} aria-label="打开指定配音语义组">打开</button>
			{/if}
		</div>
		<span class="function-divider" aria-hidden="true"></span>
		<div class="function-group" aria-label="媒体准备">
			<span class="function-group-label">媒体</span>
			<button class="function-btn" type="button" aria-busy={extractingAudio} onclick={onExtractAudio} disabled={!hasRecoverableVideo || hasSourceAudio || extractingAudio} aria-label="抽取原音轨" data-tooltip="抽取原音轨：从视频中生成可编辑的原始音频轨。快捷键：E">{#if extractingAudio}<CommandSpinner size={13} /> 抽取中{:else}<FileAudio size={13} /> 抽取原音轨{/if}</button>
			<button class="function-btn" type="button" aria-busy={asrBusy} aria-label={asrGenerateCommand.label} data-tooltip={asrGenerateCommand.description} onclick={() => void asrGenerateCommand.onSelect()} disabled={asrGenerateCommand.disabled}>
				{#if asrBusy}<CommandSpinner size={13} />{:else}<Captions size={13} />{/if} {asrBusy ? '正在听写' : asrGenerateCommand.label}
			</button>
		</div>
	</div>
</section>

<ContextMenu
	open={Boolean((timelineContextMenu && timelineContextMenuItems.length) || (trackHeightContextMenu && trackHeightContextMenuItems.length))}
	x={trackHeightContextMenu?.x ?? timelineContextMenu?.x ?? 0}
	y={trackHeightContextMenu?.y ?? timelineContextMenu?.y ?? 0}
	label={trackHeightContextMenu ? `${TRACK_LABELS[trackHeightContextMenu.trackId]}高度` : timelineContextMenuLabel}
	items={trackHeightContextMenu ? trackHeightContextMenuItems : timelineContextMenuItems}
	onClose={() => { timelineContextMenu = null; trackHeightContextMenu = null; }}
/>

<style>
	.cut-timeline {
		--timeline-ruler-height: 32px;
		--playback-health-width: clamp(140px, 18cqi, 300px);
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #0f1216;
		overflow: visible;
		container: cut-timeline / inline-size;
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
	}

	.timeline-toolbar {
		min-height: 42px;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto;
		align-items: center;
		gap: 8px;
		padding: 6px 8px;
		border-bottom: 1px solid var(--line);
		background: #171c21;
	}

	.timeline-activity {
		min-width: 0;
		max-width: 100%;
		overflow: hidden;
	}

	.transport,
	.timeline-actions {
		display: flex;
		align-items: center;
		gap: 5px;
		min-width: max-content;
		flex-wrap: nowrap;
	}

	.playback-health {
		width: var(--playback-health-width);
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
		flex: 0 0 var(--playback-health-width);
		height: 28px;
		padding: 0 8px;
		box-sizing: border-box;
		overflow: hidden;
		border-left: 1px solid rgba(122, 137, 147, 0.24);
		color: #91a0a9;
		font-size: 11px;
		white-space: nowrap;
	}

	.playback-health i {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: #68757d;
		flex: 0 0 auto;
	}

	.playback-health-copy {
		flex: 1 1 auto;
		min-width: 0;
		overflow: hidden;
	}

	.playback-health.ready { color: #9ed7b7; }
	.playback-health.ready i { background: #43b77d; }
	.playback-health.loading { color: #98c8e4; }
	.playback-health.loading i {
		background: #63b9e8;
		animation: playback-health-pulse 900ms ease-in-out infinite alternate;
	}
	.playback-health.failed { color: #e39a9d; }
	.playback-health.failed i { background: #d9585c; }

	.timeline-actions {
		justify-content: flex-end;
	}

	.track-meter-head {
		position: relative;
		height: var(--timeline-ruler-height);
		overflow: hidden;
		border-bottom: 1px solid var(--line);
		background: #11161b;
		order: 0;
	}

	.track-meter-head .meter-track {
		position: absolute;
		inset: 0;
		overflow: visible;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.018), rgba(0, 0, 0, 0.14));
	}

	.track-meter-head .meter-track::before {
		content: "";
		position: absolute;
		inset: 1px 0;
		background: linear-gradient(90deg, rgba(55, 112, 108, 0.24) 0 73%, rgba(125, 106, 55, 0.22) 73% 91%, rgba(121, 53, 51, 0.2) 91%);
		-webkit-mask: repeating-linear-gradient(90deg, #000 0 4px, transparent 4px 6px);
		mask: repeating-linear-gradient(90deg, #000 0 4px, transparent 4px 6px);
	}

	.track-meter-head .meter-track i {
		position: absolute;
		left: 0;
		top: 1px;
		bottom: 1px;
		background: linear-gradient(90deg, #419e97 0 73%, #a9914e 73% 91%, #b64f4a 91%);
		-webkit-mask: repeating-linear-gradient(90deg, #000 0 4px, transparent 4px 6px);
		mask: repeating-linear-gradient(90deg, #000 0 4px, transparent 4px 6px);
		opacity: 0.78;
		filter: drop-shadow(0 0 2px rgba(65, 158, 151, 0.16));
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
		left: 6px;
		right: 6px;
		top: 2px;
		z-index: 3;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 6px;
		padding: 0;
		border: 0;
		background: transparent;
		font-family: "SF Mono", "Cascadia Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 11px;
		font-weight: 700;
		line-height: 14px;
		font-variant-numeric: tabular-nums;
		letter-spacing: 0.01em;
		white-space: nowrap;
		pointer-events: none;
	}

	.track-meter-head .time-readout > span {
		display: inline-flex;
		align-items: center;
		min-width: 0;
		padding: 2px 5px;
		border: 0;
		border-radius: 4px;
		background: rgba(7, 12, 16, 0.46);
		box-shadow: none;
		text-shadow: 0 1px 1px rgba(0, 0, 0, 0.5);
	}

	.track-meter-head .time-current {
		font-weight: 800;
		color: rgba(238, 248, 250, 0.96);
	}

	.track-meter-head .time-total {
		color: rgba(215, 226, 231, 0.78);
		text-align: right;
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

	.hover-preview-icon {
		position: relative;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 16px;
		height: 16px;
	}

	.hover-preview-icon i {
		position: absolute;
		left: 1px;
		top: 0;
		height: 14px;
		border-left: 1px dashed currentColor;
		opacity: 0.82;
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
	.tool-btn:hover:not(:disabled),
	.track-toggle:hover:not(:disabled):not(.active),
	.range-marker-tools button:hover:not(:disabled) {
		border-color: rgba(145, 161, 171, 0.5);
		background: #293137;
		color: #f3f7f8;
		box-shadow: none;
	}

	.icon-btn:focus-visible,
	.tool-btn:focus-visible,
	.track-toggle:focus-visible,
	.range-marker-tools button:focus-visible {
		outline: 2px solid #78ddd5;
		outline-offset: 2px;
	}

	.tool-btn.active,
	.hover-scrub-toggle.active {
		border-color: rgba(87, 208, 200, 0.86);
		background: #173a37;
		color: #d7fffb;
		box-shadow: inset 0 0 0 1px rgba(118, 235, 226, 0.1);
	}

	.tool-btn {
		min-height: 24px;
		padding: 3px 7px;
		font-size: 11px;
		white-space: nowrap;
	}

	.tool-btn.icon-tool {
		padding: 0;
		line-height: 0;
	}

	.tool-btn.danger {
		color: #ffb5b5;
		border-color: #5e3135;
		background: #251719;
	}

	.icon-btn:disabled,
	.tool-btn:disabled {
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

	.track-label.dub-lane-drag-over {
		background:
			linear-gradient(90deg, color-mix(in srgb, var(--track-color) 24%, transparent), transparent 82%),
			#1a2221;
		box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--track-color) 60%, transparent);
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
		background: linear-gradient(90deg, color-mix(in srgb, var(--track-color) 42%, transparent), color-mix(in srgb, var(--track-color) 12%, transparent));
		mix-blend-mode: screen;
		opacity: 0.76;
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

	.track-label.track-muted > div:first-of-type {
		filter: grayscale(0.82) saturate(0.28) brightness(0.78);
	}

	.track-original { --track-color: #58d1c8; }
	.track-vocals { --track-color: #7da4ff; }
	.track-background { --track-color: #d9b45f; }
	.track-subtitle { --track-color: #ad8cff; }
	.track-dub { --track-color: #65d28f; }
	.secondary-dub-track strong {
		display: block;
		overflow: hidden;
		color: var(--text);
		font-size: 11px;
		font-weight: 650;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.compact-shared-controls { gap: 3px; }

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
		width: 24px;
		height: 24px;
		display: inline-grid;
		place-items: center;
		flex: 0 0 24px;
		padding: 0;
		line-height: 1;
		font-size: 10px;
	}

	.track-toggle.active {
		border-color: #78ddd5;
		background: #173a37;
		color: #d4fffb;
		font-weight: 750;
		box-shadow: inset 0 -2px 0 #78ddd5;
	}

	.track-toggle.active:hover:not(:disabled) {
		border-color: #abfff5;
		background: #23534e;
		color: #efffff;
	}

	.subtitle-variant-toggle {
		min-width: 50px;
		height: 23px;
		padding: 0 6px;
		border: 1px solid #46505a;
		border-radius: 5px;
		background: #20262c;
		color: #c9d1d7;
		font-size: 9px;
		font-weight: 650;
		white-space: nowrap;
	}

	.subtitle-variant-toggle:hover:not(:disabled),
	.subtitle-variant-toggle:focus-visible {
		border-color: #b18cff;
		background: #2b213d;
		color: #f2eaff;
		outline: none;
	}

	.subtitle-variant-toggle.active {
		border-color: #7acb98;
		background: #173326;
		color: #d9ffe6;
	}

	.subtitle-variant-toggle:disabled {
		opacity: 0.42;
		cursor: not-allowed;
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

	.lane-drag-handle {
		color: #7cae91;
	}

	.volume-control {
		position: relative;
		display: inline-flex;
		width: 52px;
		height: 23px;
	}

	.volume-db-button {
		width: 100%;
		min-width: 0;
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
		right: 0;
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

	.timeline-content.razor-tool .track-row,
	.timeline-content.razor-tool .track-row *,
	.timeline-content.razor-tool .track-row :global(.audio-clip),
	.timeline-content.razor-tool .track-row :global(.audio-clip *) {
		cursor: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 28 28'%3E%3Cg transform='translate(2 2) rotate(-18 12 12)' fill='none' stroke='%2379858b' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.45' opacity='.78'%3E%3Cpath d='M22 8h-2V6H4v2H2v8h2v2h16v-2h2Z' fill='%23aeb8bc' fill-opacity='.52'/%3E%3Cpath d='M6 11v2m4-1H6m12 0h-4m4-1v2'/%3E%3Ccircle cx='12' cy='12' r='2' fill='%235e696f' fill-opacity='.7'/%3E%3C/g%3E%3C/svg%3E") 14 14, crosshair;
	}

	.timeline-content.razor-tool .timeline-ruler,
	.timeline-content.razor-tool .timeline-ruler * {
		cursor: default;
	}

	.timeline-content.razor-tool .cue-handle,
	.timeline-content.razor-tool :global(.clip-handle) {
		pointer-events: none;
	}

	:global(.razor-blade-icon) {
		transform: rotate(-18deg);
		color: rgba(174, 184, 188, 0.72);
		opacity: 0.82;
	}

	.playhead {
		position: absolute;
		top: var(--timeline-ruler-height);
		bottom: 0;
		width: 2px;
		background: #f4d36b;
		z-index: 5;
		box-shadow: 0 0 0 1px rgba(244, 211, 107, 0.18);
		pointer-events: none;
	}

	.hover-playhead {
		position: absolute;
		top: var(--timeline-ruler-height);
		bottom: 0;
		z-index: 4;
		width: 1px;
		border-left: 1px dashed rgba(133, 229, 222, 0.78);
		filter: drop-shadow(0 0 3px rgba(87, 208, 200, 0.32));
		pointer-events: none;
	}

	.history-drop-target {
		background-color: rgba(87, 208, 200, 0.035);
		box-shadow: inset 0 0 0 1px rgba(87, 208, 200, 0.16);
	}

	.clip-lane-drop-target {
		box-shadow: inset 0 0 0 1px rgba(101, 210, 143, 0.28);
	}

	.clip-lane-drop-blocked {
		box-shadow: inset 0 0 0 1px rgba(210, 112, 112, 0.3);
	}

	.clip-lane-drop-ghost {
		position: absolute;
		top: 5px;
		bottom: 5px;
		z-index: 12;
		min-width: 12px;
		border: 1px dashed rgba(127, 224, 163, 0.78);
		border-radius: 3px;
		background: rgba(59, 139, 90, 0.24);
		box-shadow: 0 3px 12px rgba(0, 0, 0, 0.18);
		pointer-events: none;
	}

	.clip-lane-drop-ghost.blocked {
		border-color: rgba(221, 127, 127, 0.72);
		background: repeating-linear-gradient(135deg, rgba(138, 58, 58, 0.22) 0 5px, rgba(86, 42, 42, 0.1) 5px 10px);
	}

	.history-drop-ghost {
		position: absolute;
		top: 5px;
		bottom: 5px;
		z-index: 11;
		min-width: 12px;
		border: 1px solid rgba(111, 220, 212, 0.58);
		border-radius: 3px;
		background: rgba(38, 102, 99, 0.34);
		box-shadow: 0 3px 12px rgba(0, 0, 0, 0.2), inset 0 0 0 1px rgba(255, 255, 255, 0.035);
		overflow: hidden;
		pointer-events: none;
		animation: history-drop-in 110ms ease-out;
	}

	.history-drop-ghost i {
		position: absolute;
		inset: 6px 3px;
		opacity: 0.48;
		background: repeating-linear-gradient(90deg, rgba(163, 235, 230, 0.88) 0 1px, transparent 1px 4px);
		mask-image: linear-gradient(to bottom, transparent 0 18%, #000 18% 82%, transparent 82% 100%);
	}

	.history-drop-ghost span {
		position: relative;
		z-index: 1;
		display: block;
		padding: 4px 6px;
		color: rgba(225, 247, 245, 0.84);
		font-size: 9px;
		line-height: 1;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.history-drop-new-lane,
	.history-drop-new-row,
	.clip-drag-new-lane,
	.clip-drag-new-row {
		opacity: 0.76;
		border-top-color: rgba(87, 208, 200, 0.24);
	}

	@keyframes history-drop-in {
		from { opacity: 0.35; transform: translateY(-2px); }
		to { opacity: 1; transform: translateY(0); }
	}

	.frame-snap-guide {
		position: absolute;
		top: var(--timeline-ruler-height);
		bottom: 0;
		z-index: 8;
		width: 1px;
		border-left: 1px solid rgba(244, 211, 107, 0.82);
		filter: drop-shadow(0 0 4px rgba(244, 211, 107, 0.34));
		pointer-events: none;
		animation: frame-snap-pulse 180ms ease-out;
	}

	.frame-snap-guide span {
		position: absolute;
		top: 3px;
		left: 4px;
		border-radius: 3px;
		padding: 1px 4px;
		background: rgba(42, 34, 15, 0.9);
		color: #ffe9a2;
		font-size: 8px;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	.range-selection {
		position: absolute;
		top: var(--timeline-ruler-height);
		bottom: 0;
		z-index: 3;
		border-left: 1px solid rgba(244, 211, 107, 0.72);
		border-right: 1px solid rgba(244, 211, 107, 0.72);
		background: rgba(244, 211, 107, 0.08);
		pointer-events: none;
	}

	.clip-marquee {
		position: absolute;
		z-index: 18;
		box-sizing: border-box;
		border: 1px solid rgba(102, 209, 222, 0.92);
		background: rgba(71, 166, 184, 0.16);
		box-shadow: inset 0 0 0 1px rgba(8, 18, 23, 0.3);
		pointer-events: none;
	}

	.range-handle {
		position: absolute;
		top: 2px;
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
		height: var(--timeline-ruler-height);
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.02), transparent),
			#191e22;
		border-bottom: 1px solid var(--line);
		cursor: grab;
		order: 0;
	}

	.preview-cache-strip {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		z-index: 6;
		height: 1px;
		background: rgba(45, 53, 59, 0.82);
		overflow: hidden;
		pointer-events: none;
	}

	.preview-cache-strip .cache-range {
		position: absolute;
		top: 0;
		height: 1px;
		min-width: 1px;
		padding: 0;
		border: 0;
		border-radius: 0;
		transform: none;
	}

	.preview-cache-strip .cache-range.cache-ready {
		background: #43b77d;
	}

	.preview-cache-strip .cache-range.cache-rendering {
		background: linear-gradient(90deg, #315f82, #63b9e8, #315f82);
		background-size: 200% 100%;
		animation: cache-progress 900ms linear infinite;
	}

	.preview-cache-strip .cache-range.cache-failed { background: rgba(217, 88, 92, 0.9); }

	.preview-cache-strip .cache-range.cache-empty { background: rgba(45, 53, 59, 0.82); }

	.cache-refresh {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		color: #cbd5da;
	}

	.cache-refresh-copy {
		display: inline-flex;
		align-items: baseline;
		gap: 4px;
		font-size: 10px;
		line-height: 1;
	}

	.cache-refresh-copy small {
		color: #8bc7e8;
		font-size: 9px;
		font-variant-numeric: tabular-nums;
	}

	@keyframes cache-progress { to { background-position: -200% 0; } }

	.timeline-function-bar {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 38px;
		box-sizing: border-box;
		padding: 5px 8px;
		border-top: 1px solid #303940;
		background: #14191e;
		overflow-x: auto;
		overflow-y: hidden;
		scrollbar-width: thin;
	}

	.function-group {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		flex: 0 0 auto;
	}

	.function-group-label {
		padding: 0 3px 0 1px;
		color: #707b82;
		font-size: 9px;
		font-weight: 700;
		line-height: 1;
	}

	.semantic-group-picker {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		height: 26px;
		box-sizing: border-box;
		border: 1px solid #3a454c;
		border-radius: 4px;
		padding: 0 6px;
		background: #1b2227;
		color: #c7d0d5;
		font-size: 10px;
	}

	.semantic-group-picker input {
		width: 42px;
		border: 0;
		outline: 0;
		background: transparent;
		color: inherit;
		font: inherit;
		font-variant-numeric: tabular-nums;
	}

	.semantic-group-picker small {
		color: #7f8c94;
		white-space: nowrap;
	}

	.function-divider {
		width: 1px;
		height: 18px;
		flex: 0 0 1px;
		background: #303940;
	}

	.function-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
		height: 26px;
		box-sizing: border-box;
		border: 1px solid #3a454c;
		border-radius: 4px;
		padding: 0 8px;
		background: #1b2227;
		color: #c7d0d5;
		font: inherit;
		font-size: 10px;
		font-weight: 650;
		line-height: 1;
		letter-spacing: 0;
		white-space: nowrap;
		cursor: pointer;
		transition: border-color 100ms ease, background-color 100ms ease, color 100ms ease, transform 80ms ease;
	}

	.function-btn:hover:not(:disabled),
	.function-btn:focus-visible {
		border-color: #60717a;
		background: #242d33;
		color: #eef4f6;
		outline: none;
	}

	.function-btn:active:not(:disabled) {
		transform: translateY(1px);
		background: #11171b;
	}

	.function-btn.primary {
		border-color: rgba(87, 208, 200, 0.58);
		background: #173431;
		color: #d4f7f3;
	}

	.function-btn:disabled {
		opacity: 0.42;
		cursor: not-allowed;
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
		z-index: 2;
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

	.timeline-ruler span.frame-tick {
		top: 12px;
		height: 5px;
		background: rgba(220, 228, 233, 0.3);
	}

	.timeline-ruler span.frame-tick.major {
		top: 10px;
		height: 8px;
		background: rgba(232, 238, 242, 0.54);
	}

	.timeline-ruler span.frame-tick b {
		top: 7px;
		left: 3px;
		color: rgba(159, 170, 177, 0.72);
		font-size: 7px;
		font-weight: 550;
		line-height: 8px;
	}

	.timeline-ruler span.second-tick {
		top: 6px;
		height: 13px;
		z-index: 3;
		background: rgba(235, 241, 244, 0.64);
	}

	.timeline-ruler span.second-tick b {
		top: -5px;
		left: 5px;
		color: #c1cbd0;
		font-size: 8px;
		font-weight: 750;
		line-height: 9px;
	}

	.frame-coverage {
		position: absolute;
		top: 0;
		bottom: 0;
		z-index: 1;
		min-width: 1px;
		border-left: 1px solid currentColor;
		pointer-events: none;
	}

	.frame-coverage.playhead-frame {
		color: rgba(244, 211, 107, 0.76);
		background: rgba(244, 211, 107, 0.13);
	}

	.frame-coverage.hover-frame {
		color: rgba(105, 218, 209, 0.72);
		background: rgba(87, 208, 200, 0.1);
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

	.track-row :global(.audio-clip:not(.locked)) { cursor: grab; }
	.track-row :global(.audio-clip.dragging) { cursor: grabbing; }

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
		top: var(--timeline-ruler-height);
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
		cursor: pointer;
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

	.cue-chip.cue-asr.timing-review,
	.cue-chip.cue-localized.timing-review {
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

	.cue-chip.tts-source,
	.cue-chip.tts-target {
		outline: 1px solid rgba(111, 201, 235, 0.72);
		outline-offset: -2px;
	}

	.cue-chip.tts-anchor {
		outline: 2px solid rgba(112, 221, 213, 0.9);
		outline-offset: -2px;
	}

	.cue-chip.dragging {
		border-color: #f4d36b;
		background: #2f3320;
		box-shadow: 0 0 0 2px rgba(244, 211, 107, 0.18);
		cursor: grabbing;
	}

	.cue-handle {
		position: absolute;
		top: 50%;
		bottom: auto;
		height: min(calc(100% - 8px), 18px);
		width: 8px;
		background: transparent;
		cursor: ew-resize;
		transform: translateY(-50%);
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

	.cue-chip.pointer-trim-disabled .cue-handle {
		pointer-events: none;
		opacity: 0;
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
		position: relative;
		display: block;
		max-width: 100%;
		text-overflow: clip;
		white-space: nowrap;
		overflow-x: hidden;
		overflow-y: hidden;
		scrollbar-width: none;
	}

	.cue-chip .cue-text > span {
		display: inline-block;
		min-width: max-content;
		margin: 0;
		color: inherit;
		line-height: inherit;
		text-overflow: clip;
		overflow: visible;
	}

	.cue-chip .cue-text:global([data-overflow-right="true"][data-overflow-left="false"]) {
		-webkit-mask-image: linear-gradient(90deg, #000 0, #000 calc(100% - 12px), transparent 100%);
		mask-image: linear-gradient(90deg, #000 0, #000 calc(100% - 12px), transparent 100%);
	}

	.cue-chip .cue-text:global([data-overflow-left="true"][data-overflow-right="false"]) {
		-webkit-mask-image: linear-gradient(90deg, transparent 0, #000 12px, #000 100%);
		mask-image: linear-gradient(90deg, transparent 0, #000 12px, #000 100%);
	}

	.cue-chip .cue-text:global([data-overflow-left="true"][data-overflow-right="true"]) {
		-webkit-mask-image: linear-gradient(90deg, transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
		mask-image: linear-gradient(90deg, transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
	}

	.cue-chip .cue-text::-webkit-scrollbar {
		display: none;
	}

	.row-subtitle .cue-chip {
		top: 4px;
		bottom: 4px;
		height: auto;
		min-height: 26px;
		padding: 4px 0;
		border-radius: 5px;
	}

	.row-subtitle .cue-chip strong,
	.row-subtitle .cue-chip em {
		display: none;
	}

	.row-subtitle .cue-chip .cue-text {
		margin: 0 9px;
		line-height: 16px;
		text-overflow: clip;
		overflow: hidden;
	}

	.cue-chip span,
	.cue-chip em {
		color: var(--muted);
		margin-top: 2px;
	}

	@keyframes track-processing { to { background-position: 180px 0, 48px 0; } }
	@keyframes frame-snap-pulse { from { opacity: 0.45; } to { opacity: 1; } }
	@keyframes playback-health-pulse { from { opacity: 0.45; } to { opacity: 1; } }

	/* One feedback rhythm for timeline controls; clip and resize gestures stay put. */
	:is(.icon-btn, .tool-btn, .track-toggle, .subtitle-variant-toggle, .track-drag-handle,
		.volume-db-button, .function-btn, .track-inline-action),
	.zoom-stepper button,
	.range-marker-tools button {
		transition: background-color 120ms ease, border-color 120ms ease,
			color 120ms ease, box-shadow 120ms ease, transform 80ms ease;
	}

	:is(.icon-btn, .tool-btn, .track-toggle, .subtitle-variant-toggle, .volume-db-button,
		.function-btn, .track-inline-action):active:not(:disabled),
	.zoom-stepper button:active:not(:disabled),
	.range-marker-tools button:active:not(:disabled) {
		transform: translateY(1px);
		box-shadow: inset 0 2px 3px rgba(0, 0, 0, 0.45);
	}

	:is(.volume-db-button, .track-drag-handle, .subtitle-variant-toggle):focus-visible,
	.zoom-stepper button:focus-visible {
		outline: 2px solid #78ddd5;
		outline-offset: 2px;
	}

	.track-toggle:disabled {
		opacity: 0.38;
		cursor: not-allowed;
	}

	@media (prefers-reduced-motion: reduce) {
		.track-label.track-processing::after,
		.track-row.processing::after,
		.playback-health.loading i { animation: none; }
		:is(.icon-btn, .tool-btn, .track-toggle, .subtitle-variant-toggle, .track-drag-handle,
			.volume-db-button, .function-btn, .track-inline-action),
		.zoom-stepper button,
		.range-marker-tools button {
			transition: none;
			transform: none;
		}
	}

	@media (max-width: 1180px) {
		.tracks {
			grid-template-columns: 142px minmax(0, 1fr);
		}
	}

</style>
