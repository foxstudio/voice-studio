<script lang="ts">
	import { Captions, ChevronDown, ChevronRight, ChevronsLeft, ChevronsRight, FileAudio, Lock, Mic2, Pause, Play, Redo2, Save, Scissors, SkipBack, SkipForward, Trash2, Undo2, Unlock, Wand2, ZoomIn, ZoomOut } from 'lucide-svelte';
	import { tick } from 'svelte';
	import { buildTimelineTicks, formatTimecode, formatTimelineZoom } from '$lib/audio/waveform';
	import type { VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationOperation, VideoLocalizationTimelineClip } from '$lib/api/types';
	import { durationLabel, isActiveOperation, operationStatusLabel, timelineClipAudioUrl } from './utils';
	import { TRACK_LABELS, type SubtitlePreviewSource, type SubtitlePreviewState, type VideoLocalizationTrackId, type VideoLocalizationTrackState, type VideoLocalizationTrackStates } from './studio-state';
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
		transcribingAsr,
		trackStates,
		timelineZoom,
		subtitlePreview,
		onSelectCue,
		onExtractAudio,
		onSeparateStems,
		onTranscribeEnglish,
		onTransportAction,
		onTrackStateChange,
		onTimelineZoomChange,
		onToggleSubtitleSource,
		onSeekTimeline,
		onUpdateCueTime,
		onSplitCue,
		onMergeCue,
		onDeleteCue,
		onSaveSelectionAsVoice,
		onGenerateToSelection,
		onUpdateTimelineClip,
		onDeleteTimelineClip,
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
		transcribingAsr: boolean;
		trackStates: VideoLocalizationTrackStates;
		timelineZoom: number;
		subtitlePreview: SubtitlePreviewState;
		onSelectCue: (cueId: string) => void;
		onExtractAudio: () => void;
		onSeparateStems: () => void;
		onTranscribeEnglish: () => void;
		onTransportAction: (action: 'start' | 'play-pause' | 'next') => void;
		onTrackStateChange: (trackId: VideoLocalizationTrackId, patch: Partial<VideoLocalizationTrackState>) => void;
		onTimelineZoomChange: (zoom: number) => void;
		onToggleSubtitleSource: (source: Exclude<SubtitlePreviewSource, 'auto' | 'compare'>) => void;
		onSeekTimeline: (timeMs: number) => void;
		onUpdateCueTime: (cueId: string, startMs: number, endMs: number) => void;
		onSplitCue: () => void;
		onMergeCue: () => void;
		onDeleteCue: () => void;
		onSaveSelectionAsVoice: (startMs: number, endMs: number) => void;
		onGenerateToSelection: (startMs: number, endMs: number) => void;
		onUpdateTimelineClip: (clipId: string, startMs: number, endMs: number, sourceStartMs: number, sourceEndMs: number | null) => void;
		onDeleteTimelineClip: (clipId: string) => void;
		onUndoTimelineClip: () => void;
		onRedoTimelineClip: () => void;
		canUndoTimeline: boolean;
		canRedoTimeline: boolean;
	} = $props();

	type DragMode = 'move' | 'trim-start' | 'trim-end';
	type CueDragState = {
		cueId: string;
		mode: DragMode;
		startX: number;
		startMs: number;
		endMs: number;
		durationMs: number;
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
	let pendingSeekMs = 0;
	let seekAnimationFrame = 0;
	let selectionDrag = $state<'start' | 'end' | null>(null);
	let rangeCreateState = $state<{ startX: number; startMs: number; moved: boolean } | null>(null);
	let rangeStartMs = $state<number | null>(null);
	let rangeEndMs = $state<number | null>(null);
	let labelColumnWidth = $state(236);
	let editingTrackId = $state<VideoLocalizationTrackId | null>(null);
	let editingTrackValue = $state('');
	let openVolumeTrack = $state<VideoLocalizationTrackId | null>(null);
	let volumeClickTimer: ReturnType<typeof setTimeout> | null = null;
	let dubWaveforms = $state<Record<string, { bars: number[]; durationSeconds: number }>>({});
	let trackHeights = $state<Record<VideoLocalizationTrackId, number>>({
		original: 58,
		vocals: 58,
		background: 58,
		subtitles: 72,
		dub: 58
	});

	const hasVideo = $derived(Boolean(draft?.source_media.video_path || draft?.source_media.filename));
	const hasSourceAudio = $derived(Boolean(draft?.source_media.audio_path || draft?.stems.original_audio_path));
	const stemsReady = $derived(draft?.stems.separation_status === 'completed');
	const hasAsr = $derived(Boolean(draft?.cues.some((cue) => cue.en_subtitle_text?.trim())));
	const durationMs = $derived(draft?.source_media.duration_ms ?? Math.max(...(draft?.cues ?? []).map((cue) => cue.end_ms ?? 0), 0));
	const timelineDurationMs = $derived(durationMs ? Math.max(durationMs, 1000) : 60000);
	const timelineTicks = $derived(buildTimelineTicks(timelineDurationMs / 1000, timelineZoom));
	const playheadPercent = $derived(Math.max(0, Math.min(100, (currentTimeMs / timelineDurationMs) * 100)));
	const activeOperationText = $derived(latestOperation && isActiveOperation(latestOperation) ? `${latestOperation.label ?? latestOperation.kind} · ${operationStatusLabel(latestOperation)}` : '');
	const selectedCue = $derived(draft?.cues.find((cue) => cue.cue_id === selectedCueId) ?? null);
	const canEditSelectedCue = $derived(Boolean(selectedCue) && !trackStates.subtitles.locked);
	const canSplitSelectedCue = $derived(Boolean(!trackStates.subtitles.locked && selectedCue && selectedCue.start_ms !== null && selectedCue.end_ms !== null && selectedCue.end_ms - selectedCue.start_ms >= 700));
	const canMergeSelectedCue = $derived(Boolean(!trackStates.subtitles.locked && selectedCue && nextCueAfter(selectedCue)));
	const hasRangeSelection = $derived(rangeStartMs !== null && rangeEndMs !== null && Math.abs(rangeEndMs - rangeStartMs) >= 300);
	const rangeStartValue = $derived(rangeStartMs ?? 0);
	const rangeEndValue = $derived(rangeEndMs ?? rangeStartValue);
	const rangeStartPercent = $derived(Math.max(0, Math.min(100, (rangeStartValue / timelineDurationMs) * 100)));
	const rangeEndPercent = $derived(Math.max(0, Math.min(100, (rangeEndValue / timelineDurationMs) * 100)));
	const rangeLeftPercent = $derived(hasRangeSelection ? Math.max(0, Math.min(100, (Math.min(rangeStartValue, rangeEndValue) / timelineDurationMs) * 100)) : 0);
	const rangeWidthPercent = $derived(hasRangeSelection ? Math.max(0.4, Math.min(100 - rangeLeftPercent, (Math.abs(rangeEndValue - rangeStartValue) / timelineDurationMs) * 100)) : 0);
	const masterLevel = $derived(isPlaying ? estimateMasterLevel() : 0);
	const subtitleTrackCollapsed = $derived(trackStates.subtitles.collapsed === true);
	const subtitleTrackHeight = $derived(subtitleTrackCollapsed ? 34 : 72);

	$effect(() => {
		projectId;
	});

	function cueLeft(cue: VideoLocalizationCue) {
		const time = cueLiveTime(cue);
		return Math.max(0, Math.min(98, (time.start_ms / timelineDurationMs) * 100));
	}

	function cueWidth(cue: VideoLocalizationCue) {
		const { start_ms: start, end_ms: end } = cueLiveTime(cue);
		const left = Math.max(0, Math.min(98, (start / timelineDurationMs) * 100));
		return Math.max(0.1, Math.min(100 - left, (Math.max(200, end - start) / timelineDurationMs) * 100));
	}

	function clipLeft(startMs: number | null | undefined) {
		return Math.max(0, Math.min(98, (((startMs ?? 0) / timelineDurationMs) * 100)));
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

	function cueLabel(cue: VideoLocalizationCue) {
		return cue.zh_localized_subtitle_text?.trim() || cue.en_subtitle_text?.trim() || '未命名字幕';
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

	function toggleSubtitleTrack() {
		onTrackStateChange('subtitles', { collapsed: !subtitleTrackCollapsed });
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
		if (subtitleTrackCollapsed) return;
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
		}
	}

	function setSelectionPoint(edge: 'start' | 'end') {
		const timeMs = Math.max(0, Math.min(timelineDurationMs, currentTimeMs));
		if (edge === 'start') {
			rangeStartMs = Math.min(timeMs, (rangeEndMs ?? timelineDurationMs) - 300);
			if (rangeEndMs !== null && rangeEndMs <= rangeStartMs) rangeEndMs = null;
			return;
		}
		rangeEndMs = Math.max(timeMs, (rangeStartMs ?? 0) + 300);
	}

	function setRangeFromSelectedCue() {
		if (!selectedCue || selectedCue.start_ms === null || selectedCue.end_ms === null) return;
		rangeStartMs = selectedCue.start_ms;
		rangeEndMs = Math.max(selectedCue.start_ms + 300, selectedCue.end_ms);
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
		if (trackId === 'original' && !hasSourceAudio) return false;
		if ((trackId === 'vocals' || trackId === 'background') && !stemsReady) return false;
		if (trackStates[trackId]?.muted) return false;
		const soloTracks = (['original', 'vocals', 'background', 'dub'] as VideoLocalizationTrackId[]).filter((candidate) => trackStates[candidate]?.solo);
		return !soloTracks.length || soloTracks.includes(trackId);
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
		if (event.button === 1 && (target.closest('.timeline-ruler') || target.closest('[data-track-row]'))) {
			event.preventDefault();
			if (target.closest('[data-track-row]')) {
				const startMs = timeFromPointer(event);
				rangeCreateState = { startX: event.clientX, startMs, moved: false };
			} else {
				timelinePanState = { startX: event.clientX, scrollLeft: trackCanvasEl?.scrollLeft ?? 0 };
			}
			(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
			return;
		}
		if (event.button !== 0) return;
		if (target.closest('button,input,.cue-chip,.clip-handle,.range-handle,.track-resize-handle,.track-label-width-handle')) return;
		event.preventDefault();
		if (target.closest('[data-track-row]')) return;
		timelineSeekDrag = true;
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		seekFromPointer(event);
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
	}

	function endTimelinePointerWork() {
		endCueDrag();
		endClipDrag();
		if (rangeCreateState && !rangeCreateState.moved) onSeekTimeline(rangeCreateState.startMs);
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
			rangeEndMs = Math.min(timelineDurationMs, Math.max(rangeCreateState.startMs + 300, currentMs));
		} else {
			rangeStartMs = Math.max(0, Math.min(rangeCreateState.startMs - 300, currentMs));
			rangeEndMs = rangeCreateState.startMs;
		}
	}

	function beginSelectionDrag(event: PointerEvent, edge: 'start' | 'end') {
		event.preventDefault();
		event.stopPropagation();
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
		if (selectionDrag === 'start') rangeStartMs = Math.min(timeMs, currentEnd - 300);
		else rangeEndMs = Math.max(timeMs, currentStart + 300);
	}

	function handleTrackWheel(event: WheelEvent) {
		if (!trackCanvasEl) return;
		event.preventDefault();
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
		const minHeight = trackId === 'subtitles' ? 58 : 44;
		const move = (moveEvent: PointerEvent) => {
			trackHeights = { ...trackHeights, [trackId]: Math.max(minHeight, Math.min(140, startHeight + moveEvent.clientY - startY)) };
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
	}

	function cueLiveTime(cue: VideoLocalizationCue) {
		const live = liveCueTimes[cue.cue_id];
		const fallbackStart = cue.start_ms ?? 0;
		const fallbackEnd = cue.end_ms ?? fallbackStart + 1800;
		return live ?? { start_ms: fallbackStart, end_ms: Math.max(fallbackStart + 300, fallbackEnd) };
	}

	function startCueDrag(event: PointerEvent, cue: VideoLocalizationCue, mode: DragMode) {
		if (event.button !== 0) return;
		if (trackStates.subtitles.locked) return;
		const time = cueLiveTime(cue);
		if (time.end_ms <= time.start_ms) return;
		event.preventDefault();
		event.stopPropagation();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		onSelectCue(cue.cue_id);
		dragState = {
			cueId: cue.cue_id,
			mode,
			startX: event.clientX,
			startMs: time.start_ms,
			endMs: time.end_ms,
			durationMs: time.end_ms - time.start_ms
		};
	}

	function moveCueDrag(event: PointerEvent) {
		if (!dragState || !timelineContentEl) return;
		const rect = timelineContentEl.getBoundingClientRect();
		const deltaMs = ((event.clientX - dragState.startX) / Math.max(1, rect.width)) * timelineDurationMs;
		const minDurationMs = 300;
		let nextStart = dragState.startMs;
		let nextEnd = dragState.endMs;
		if (dragState.mode === 'move') {
			const duration = Math.max(minDurationMs, dragState.durationMs);
			nextStart = clampMs(dragState.startMs + deltaMs, 0, Math.max(0, timelineDurationMs - duration));
			nextEnd = nextStart + duration;
		} else if (dragState.mode === 'trim-start') {
			nextStart = clampMs(dragState.startMs + deltaMs, 0, dragState.endMs - minDurationMs);
		} else {
			nextEnd = clampMs(dragState.endMs + deltaMs, dragState.startMs + minDurationMs, timelineDurationMs);
		}
		liveCueTimes = { ...liveCueTimes, [dragState.cueId]: { start_ms: Math.round(nextStart), end_ms: Math.round(nextEnd) } };
	}

	function endCueDrag() {
		if (!dragState) return;
		const live = liveCueTimes[dragState.cueId];
		if (live) onUpdateCueTime(dragState.cueId, live.start_ms, live.end_ms);
		dragState = null;
	}

	function startClipDrag(event: PointerEvent, clip: VideoLocalizationTimelineClip, mode: DragMode) {
		if (event.button !== 0) return;
		const trackId = clip.track_id as VideoLocalizationTrackId;
		if (trackStates[trackId]?.locked) return;
		const time = timelineClipTime(clip);
		event.preventDefault();
		event.stopPropagation();
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

	$effect(() => {
		draft?.updated_at;
		draft?.cues.length;
		if (!dragState) liveCueTimes = {};
		draft?.timeline_clips.length;
		if (!clipDragState) liveClipTimes = {};
	});

	$effect(() => {
		selectedCueId;
		rangeStartMs = null;
		rangeEndMs = null;
	});

	$effect(() => {
		timelineZoom;
		requestAnimationFrame(() => updateTimelineViewport());
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
			<div class="subtitle-layer-controls" aria-label="视频字幕显示">
				<Captions size={13} />
				<button class:active={subtitlePreview.sources?.asr === true} type="button" data-tooltip="ASR 字幕｜显示或隐藏视频预览中的原文识别字幕。" onclick={() => onToggleSubtitleSource('asr')}>ASR</button>
				<button class:active={subtitlePreview.sources?.localized === true} type="button" data-tooltip="本土化字幕｜显示或隐藏视频预览中的本土化译文。" onclick={() => onToggleSubtitleSource('localized')}>本土化</button>
				<button class:active={subtitlePreview.sources?.tts === true} type="button" data-tooltip="TTS 文本｜显示或隐藏视频预览中的实际配音台词。" onclick={() => onToggleSubtitleSource('tts')}>TTS</button>
			</div>
		</div>
		<div class="timeline-actions">
			{#if activeOperationText}
				<span class="operation-chip">{activeOperationText}</span>
			{/if}
			<div class="edit-tools" aria-label="字幕片段编辑">
				<button class="tool-btn icon-tool" type="button" onclick={onSplitCue} disabled={!canSplitSelectedCue} aria-label="拆分字幕片段" data-tooltip="拆分字幕片段：按当前指针或中点切开当前字幕。"><Scissors size={13} /></button>
				<button class="tool-btn icon-tool" type="button" onclick={onMergeCue} disabled={!canMergeSelectedCue} aria-label="合并下一字幕片段" data-tooltip="合并下一字幕片段：把当前字幕和后一段合并。">⇄</button>
				<button class="tool-btn icon-tool danger" type="button" onclick={onDeleteCue} disabled={!canEditSelectedCue} aria-label="删除当前字幕片段" data-tooltip="删除当前字幕片段：从时间线移除当前字幕。"><Trash2 size={13} /></button>
			</div>
			<div class="edit-tools" aria-label="选区工作流">
				<button class="tool-btn icon-tool" type="button" onclick={setRangeFromSelectedCue} disabled={!canEditSelectedCue} aria-label="用当前字幕设置选区" data-tooltip="用当前字幕设置选区：把当前字幕的入点和出点作为样音范围。"><Captions size={13} /></button>
				<button class="tool-btn icon-tool" type="button" onclick={() => handleRangeAction(onSaveSelectionAsVoice)} disabled={!hasRangeSelection} aria-label="保存选区为音色" data-tooltip={hasRangeSelection ? '保存选区为音色：把当前时间范围保存为项目音色样音。' : '先设置一个时间范围，才能保存样音。'}><Save size={13} /></button>
				<button class="primary-tool icon-tool" type="button" onclick={() => handleRangeAction(onGenerateToSelection)} disabled={!hasRangeSelection} aria-label="生成到选区" data-tooltip={hasRangeSelection ? '生成到选区：把生成语音放入当前时间范围。' : '先设置一个时间范围，才能生成到选区。'}><Wand2 size={13} /></button>
			</div>
			<div class="zoom-stepper" aria-label="时间线缩放">
				<button type="button" onclick={() => stepTimelineZoom(-1)} disabled={timelineZoom <= 1} aria-label="缩小时间线" data-tooltip="缩小时间线：显示更长时间范围。"><ZoomOut size={13} /></button>
				<span>{formatTimelineZoom(timelineZoom)}x</span>
				<button type="button" onclick={() => stepTimelineZoom(1)} disabled={timelineZoom >= 1200} aria-label="放大时间线" data-tooltip="放大时间线：更精细地查看波形和片段。"><ZoomIn size={13} /></button>
			</div>
			<div class="range-marker-tools" aria-label="设置出入点">
				<button class:active={rangeStartMs !== null} type="button" aria-label="设置入点" data-tooltip="设置入点：把当前播放指针设为选区起点。快捷键 I" onclick={() => setSelectionPoint('start')}><ChevronsLeft size={14} /></button>
				<button class:active={rangeEndMs !== null} type="button" aria-label="设置出点" data-tooltip="设置出点：把当前播放指针设为选区终点。快捷键 O" onclick={() => setSelectionPoint('end')}><ChevronsRight size={14} /></button>
			</div>
			<button class="tool-btn icon-tool" type="button" onclick={onExtractAudio} disabled={!hasVideo || hasSourceAudio || extractingAudio} aria-label="抽取原音轨" data-tooltip="抽取原音轨：从视频中生成可编辑的原始音频轨。">
				<FileAudio size={13} />
			</button>
			<button class="tool-btn icon-tool" type="button" onclick={onSeparateStems} disabled={!hasSourceAudio || stemsReady || separatingStems} aria-label="生成人声和背景轨" data-tooltip="生成人声和背景轨：把原音轨分离成人声与伴奏/环境声。">
				<Mic2 size={13} />
			</button>
			<button class="primary-tool icon-tool" type="button" onclick={onTranscribeEnglish} disabled={!hasSourceAudio || hasAsr || transcribingAsr || trackStates.subtitles.locked} aria-label="生成字幕轨" data-tooltip="生成字幕轨：识别原音频并创建可编辑字幕片段。">
				<Captions size={13} />
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
			<div class="track-label subtitle track-subtitle" class:collapsed={subtitleTrackCollapsed} class:track-locked={trackStates.subtitles.locked} style={`height:${subtitleTrackHeight}px`}>
				<div>
					{#if editingTrackId === 'subtitles'}
						<input class="track-name-input" data-track-name="subtitles" aria-label="修改字幕轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('subtitles')} onkeydown={(event) => handleTrackNameKeydown(event, 'subtitles')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.subtitles.locked} data-tooltip="字幕轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('subtitles')}>{trackName('subtitles')}</button>
					{/if}
					<span>点击片段同步右侧编辑</span>
				</div>
				<div class="track-controls">
					<button class="track-action" type="button" data-tooltip="生成 ASR 字幕｜识别原音轨并创建可编辑字幕片段。" onclick={onTranscribeEnglish} disabled={!hasSourceAudio || hasAsr || transcribingAsr || trackStates.subtitles.locked}>ASR</button>
					<button class="track-collapse" type="button" aria-label={subtitleTrackCollapsed ? '展开字幕轨' : '折叠字幕轨'} data-tooltip={subtitleTrackCollapsed ? '展开字幕轨｜显示完整字幕内容，片段高度同步增大。' : '折叠字幕轨｜收紧轨道与字幕片段，只显示单行摘要。'} onclick={toggleSubtitleTrack}>
						{#if subtitleTrackCollapsed}<ChevronRight size={13} />{:else}<ChevronDown size={13} />{/if}
					</button>
					<button class="track-toggle lock-toggle" class:active={trackStates.subtitles.locked} type="button" aria-label={trackStates.subtitles.locked ? '解锁字幕轨' : '锁定字幕轨'} data-tooltip={trackStates.subtitles.locked ? '解锁字幕轨｜允许移动、裁切和删除字幕片段。' : '锁定字幕轨｜禁止修改字幕片段，但不影响显示和播放。'} onclick={() => toggleLocked('subtitles')}>{#if trackStates.subtitles.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
			</div>
			<div class="track-label track-audio track-original" class:track-muted={trackStates.original.muted} class:track-locked={trackStates.original.locked} style={`height:${trackHeights.original}px`}>
				<i class="track-title-level" style={`width:${trackMeterPercent('original')}%`}></i>
				<div>
					{#if editingTrackId === 'original'}
						<input class="track-name-input" data-track-name="original" aria-label="修改原音轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('original')} onkeydown={(event) => handleTrackNameKeydown(event, 'original')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.original.locked} data-tooltip="原音轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('original')}>{trackName('original')}</button>
					{/if}
					<span>{hasSourceAudio ? '完整视频声音' : '导入后自动抽取'}</span>
				</div>
				<div class="track-controls">
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
				<button class="track-resize-handle" type="button" aria-label="调整原音轨高度" data-tooltip="调整原音轨高度：上下拖动改变轨道显示高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'original')}></button>
			</div>
			<div class="track-label track-audio track-vocals" class:track-muted={trackStates.vocals.muted} class:track-locked={trackStates.vocals.locked} style={`height:${trackHeights.vocals}px`}>
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
				<button class="track-resize-handle" type="button" aria-label="调整人声轨高度" data-tooltip="调整人声轨高度：上下拖动改变轨道显示高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'vocals')}></button>
			</div>
			<div class="track-label track-audio track-background" class:track-muted={trackStates.background.muted} class:track-locked={trackStates.background.locked} style={`height:${trackHeights.background}px`}>
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
				<button class="track-resize-handle" type="button" aria-label="调整背景音乐轨高度" data-tooltip="调整背景音乐轨高度：上下拖动改变轨道显示高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'background')}></button>
			</div>
			<div class="track-label track-audio track-dub" class:track-muted={trackStates.dub.muted} class:track-locked={trackStates.dub.locked} style={`height:${trackHeights.dub}px`}>
				<i class="track-title-level" style={`width:${trackMeterPercent('dub')}%`}></i>
				<div>
					{#if editingTrackId === 'dub'}
						<input class="track-name-input" data-track-name="dub" aria-label="修改中文配音轨名称" value={editingTrackValue} oninput={(event) => (editingTrackValue = event.currentTarget.value)} onblur={() => finishTrackRename('dub')} onkeydown={(event) => handleTrackNameKeydown(event, 'dub')} />
					{:else}
						<button class="track-name-button" type="button" disabled={trackStates.dub.locked} data-tooltip="中文配音轨名称｜点击修改轨道名称。锁定时不可编辑。" onclick={() => beginTrackRename('dub')}>{trackName('dub')}</button>
					{/if}
					<span>后续放入生成音频</span>
				</div>
				<div class="track-controls">
					<button class="track-toggle" class:active={trackStates.dub.muted} type="button" data-tooltip="静音中文配音轨｜关闭当前轨道声音，片段变为灰色。" onclick={() => toggleMuted('dub')}>M</button>
					<button class="track-toggle" class:active={trackStates.dub.solo} type="button" data-tooltip="独奏中文配音轨｜只播放当前轨道；所有 S 均关闭时播放全部未静音轨道。" onclick={() => toggleSolo('dub')}>S</button>
					<div class="volume-control">
						{#if openVolumeTrack === 'dub'}
							<div class="volume-inline-editor" role="group" aria-label="中文配音轨音量" onpointerdown={(event) => event.stopPropagation()}>
								<input data-volume-input="dub" aria-label="中文配音轨音量 dB" type="number" min="-60" max="6.02" step="0.01" value={volumeToDb(trackStates.dub.volume, 2)} oninput={(event) => updateTrackDb('dub', Number(event.currentTarget.value))} onblur={() => finishVolumeEdit('dub')} onkeydown={(event) => handleVolumeInputKeydown(event, 'dub')} />
								<span>dB</span>
							</div>
						{:else}
							<button class="volume-db-button" type="button" aria-label="调整中文配音轨音量" data-tooltip="中文配音轨音量：单击精确输入，按住左右拖动以 0.1 dB 调整，双击重置为 0 dB。" onpointerdown={(event) => beginVolumeScrub(event, 'dub')} ondblclick={(event) => resetTrackDb(event, 'dub')}>{volumeDbLabel('dub')}</button>
						{/if}
					</div>
					<button class="track-toggle lock-toggle" class:active={trackStates.dub.locked} type="button" aria-label={trackStates.dub.locked ? '解锁中文配音轨' : '锁定中文配音轨'} data-tooltip={trackStates.dub.locked ? '解锁中文配音轨｜允许移动、裁切和删除音频片段。' : '锁定中文配音轨｜禁止修改片段，但不影响声音播放。'} onclick={() => toggleLocked('dub')}>{#if trackStates.dub.locked}<Lock size={12} />{:else}<Unlock size={12} />{/if}</button>
				</div>
				<button class="track-resize-handle" type="button" aria-label="调整中文配音轨高度" data-tooltip="调整中文配音轨高度：上下拖动改变轨道显示高度。" onpointerdown={(event) => beginTrackHeightResize(event, 'dub')}></button>
			</div>
			<button class="track-label-width-handle" type="button" aria-label="调整轨道标题宽度" data-tooltip="调整标题宽度：左右拖动改变所有轨道标题栏宽度。" onpointerdown={beginLabelColumnResize}></button>
		</div>

		<div
			class="track-canvas"
			bind:this={trackCanvasEl}
			role="region"
			aria-label="音频与字幕轨道滚动区域"
			onscroll={(event) => updateTimelineViewport(event.currentTarget as HTMLDivElement)}
			onwheel={handleTrackWheel}
			onpointerenter={(event) => updateTimelineViewport(event.currentTarget as HTMLDivElement)}
		>
			<div
				class="timeline-content"
				class:dragging={Boolean(dragState)}
				class:panning={Boolean(timelinePanState)}
				style={`width:${timelineZoom * 100}%`}
				bind:this={timelineContentEl}
				role="application"
				aria-label="视频配音时间线"
				onpointerdown={handleTimelinePointerDown}
				onpointermove={handleTimelinePointerMove}
				onpointerup={endTimelinePointerWork}
				onpointercancel={endTimelinePointerWork}
				onlostpointercapture={endTimelinePointerWork}
			>
				<div class="timeline-ruler">
					{#each timelineTicks as tick}
						<span class:major={tick.major} class:medium={tick.level === 1} style={`left:${tick.percent}%`}>
							<i></i>
							{#if tick.label}<b>{tick.label}</b>{/if}
						</span>
					{/each}
				</div>
				<div class="playhead" style={`left:${playheadPercent}%`}></div>
				{#if hasRangeSelection}
					<div class="range-selection" style={`left:${rangeLeftPercent}%;width:${rangeWidthPercent}%`}></div>
				{/if}
				{#if rangeStartMs !== null}<button class="range-handle in" type="button" style={`left:${rangeStartPercent}%`} aria-label={`拖动选区入点，当前 ${rangeStartMs} 毫秒`} data-tooltip="选区入点｜拖动修改范围开始位置。快捷键：I" onpointerdown={(event) => beginSelectionDrag(event, 'start')}>I</button>{/if}
				{#if rangeEndMs !== null}<button class="range-handle out" type="button" style={`left:${rangeEndPercent}%`} aria-label={`拖动选区出点，当前 ${rangeEndMs} 毫秒`} data-tooltip="选区出点｜拖动修改范围结束位置。快捷键：O" onpointerdown={(event) => beginSelectionDrag(event, 'end')}>O</button>{/if}
				<div class="track-row subtitle row-subtitle" data-track-row class:collapsed={subtitleTrackCollapsed} class:locked={trackStates.subtitles.locked} style={`height:${subtitleTrackHeight}px`}>
					{#if draft?.cues.length}
						{#each draft.cues as cue}
							<button
								class="cue-chip"
								class:active={cue.cue_id === selectedCueId}
								class:dragging={dragState?.cueId === cue.cue_id}
								class:collapsed={subtitleTrackCollapsed}
								type="button"
								style={`left:${cueLeft(cue)}%;width:${cueWidth(cue)}%`}
								onclick={() => !dragState && onSelectCue(cue.cue_id)}
								onpointerdown={(event) => startCueDrag(event, cue, 'move')}
								aria-label={`选择字幕 ${cue.cue_id}`}
								data-tooltip={`选择字幕：打开 ${cue.cue_id}，拖动片段可调整位置。`}
							>
								<span
									class="cue-handle"
									role="slider"
									tabindex="-1"
									aria-label="调整字幕入点"
									aria-valuemin="0"
									aria-valuemax={timelineDurationMs}
									aria-valuenow={cueLiveTime(cue).start_ms}
									onpointerdown={(event) => startCueDrag(event, cue, 'trim-start')}
								></span>
								<strong>{cue.cue_id.replace('cue_', '#')}</strong>
								<span class="cue-text" onwheel={handleCueTextWheel}>{cueLabel(cue)}</span>
								<em>{durationLabel(cueLiveTime(cue).end_ms - cueLiveTime(cue).start_ms)}</em>
								<span
									class="cue-handle"
									role="slider"
									tabindex="-1"
									aria-label="调整字幕出点"
									aria-valuemin="0"
									aria-valuemax={timelineDurationMs}
									aria-valuenow={cueLiveTime(cue).end_ms}
									onpointerdown={(event) => startCueDrag(event, cue, 'trim-end')}
								></span>
							</button>
						{/each}
					{:else}
						<div class="pending-block">生成 ASR 后，字幕片段会出现在这里</div>
					{/if}
				</div>
				<div class="track-row row-original" data-track-row data-audio-selection-track="original" class:muted={trackStates.original.muted} class:locked={trackStates.original.locked} style={`height:${trackHeights.original}px`}>
					{#if clipsForTrack('original').length}
						{#each clipsForTrack('original') as clip}
							<EditableAudioClip {clip} audioSrc={timelineClipAudioUrl(projectId, clip)} label={clipLabel(clip, 'original')} tone={clipTone('original')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} locked={trackStates.original.locked} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
					{:else}
						<div class="pending-block">导入视频后，原音轨会出现在这里</div>
					{/if}
				</div>
				<div class="track-row row-vocals" data-track-row data-audio-selection-track="vocals" class:muted={trackStates.vocals.muted} class:locked={trackStates.vocals.locked} style={`height:${trackHeights.vocals}px`}>
					{#if clipsForTrack('vocals').length}
						{#each clipsForTrack('vocals') as clip}
							<EditableAudioClip {clip} audioSrc={timelineClipAudioUrl(projectId, clip)} label={clipLabel(clip, 'vocals')} tone={clipTone('vocals')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} locked={trackStates.vocals.locked} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
					{:else}
						<div class="pending-block">点击“生成人声/背景”得到人声轨</div>
					{/if}
				</div>
				<div class="track-row row-background" data-track-row data-audio-selection-track="background" class:muted={trackStates.background.muted} class:locked={trackStates.background.locked} style={`height:${trackHeights.background}px`}>
					{#if clipsForTrack('background').length}
						{#each clipsForTrack('background') as clip}
							<EditableAudioClip {clip} audioSrc={timelineClipAudioUrl(projectId, clip)} label={clipLabel(clip, 'background')} tone={clipTone('background')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} locked={trackStates.background.locked} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
					{:else}
						<div class="pending-block">背景音乐轨会与人声轨同时生成</div>
					{/if}
				</div>
				<div class="track-row row-dub" data-track-row data-audio-selection-track="dub" class:muted={trackStates.dub.muted} class:locked={trackStates.dub.locked} style={`height:${trackHeights.dub}px`}>
					{#if clipsForTrack('dub').length}
						{#each clipsForTrack('dub') as clip}
							<EditableAudioClip {clip} audioSrc={timelineClipAudioUrl(projectId, clip)} label={clipLabel(clip, 'dub')} tone={clipTone('dub')} left={clipLeft(timelineClipTime(clip).start_ms)} width={clipWidth(timelineClipTime(clip).start_ms, timelineClipTime(clip).end_ms)} dragging={clipDragState?.clipId === clip.clip_id} locked={trackStates.dub.locked} startMs={timelineClipTime(clip).start_ms} endMs={timelineClipTime(clip).end_ms} sourceStartMs={timelineClipTime(clip).source_start_ms ?? 0} sourceEndMs={timelineClipTime(clip).source_end_ms ?? null} {timelineDurationMs} {timelineZoom} {timelineScrollLeft} {timelineViewportWidth} onMove={(event) => startClipDrag(event, clip, 'move')} onTrimStart={(event) => startClipDrag(event, clip, 'trim-start')} onTrimEnd={(event) => startClipDrag(event, clip, 'trim-end')} onDelete={() => onDeleteTimelineClip(clip.clip_id)} onAnalysis={(bars, durationSeconds) => updateDubWaveform(clip.clip_id, bars, durationSeconds)} />
						{/each}
					{:else}
						<div class="pending-block">生成的中文配音会作为 clip 放入这里</div>
					{/if}
				</div>
			</div>
		</div>
		<div class="timeline-edge-shadow left" aria-hidden="true"></div>
		<div class="timeline-edge-shadow right" aria-hidden="true"></div>
	</div>
</section>

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
		grid-template-columns: auto minmax(0, 1fr);
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

	.subtitle-layer-controls {
		display: inline-flex;
		align-items: center;
		gap: 2px;
		margin-left: 3px;
		padding: 2px 3px;
		border-left: 1px solid #303941;
		color: #82909a;
	}

	.subtitle-layer-controls button {
		height: 22px;
		border: 0;
		border-radius: 4px;
		padding: 0 6px;
		background: transparent;
		color: #8f9aa2;
		font-size: 9px;
		cursor: pointer;
	}

	.subtitle-layer-controls button:hover,
	.subtitle-layer-controls button.active {
		background: #183a37;
		color: #cffffa;
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
		padding: 2px;
		border: 1px solid #303941;
		border-radius: 6px;
		background: #101419;
	}

	.zoom-stepper {
		display: inline-flex;
		align-items: center;
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 2px;
		background: #101419;
		color: var(--muted);
		font-size: 11px;
		white-space: nowrap;
	}

	.zoom-stepper button {
		width: 22px;
		height: 22px;
		border: 0;
		border-radius: 4px;
		background: transparent;
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
		padding: 2px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101419;
	}

	.range-marker-tools button {
		width: 24px;
		height: 24px;
		padding: 0;
		border: 1px solid transparent;
		border-radius: 5px;
		background: transparent;
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

	.operation-chip {
		border: 1px solid var(--line);
		border-radius: 999px;
		padding: 2px 7px;
		color: var(--muted);
		background: #101419;
		font-size: 11px;
		white-space: nowrap;
	}

	.operation-chip {
		color: #cbd9ff;
		border-color: #415b9c;
		background: #171f34;
	}

	.icon-btn,
	.tool-btn,
	.primary-tool,
	.track-toggle,
	.track-action {
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #20262c;
		color: var(--text);
		cursor: pointer;
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
	.track-action:hover:not(:disabled),
	.track-action:focus-visible,
	.track-collapse:hover:not(:disabled),
	.track-collapse:focus-visible,
	.range-marker-tools button:hover:not(:disabled),
	.range-marker-tools button:focus-visible {
		border-color: rgba(113, 224, 215, 0.72);
		background: #26343a;
		color: #efffff;
		box-shadow: 0 0 0 1px rgba(87, 208, 200, 0.12);
		outline: none;
	}

	.tool-btn,
	.primary-tool,
	.track-action {
		min-height: 24px;
		padding: 3px 7px;
		font-size: 11px;
		white-space: nowrap;
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
	.primary-tool:disabled,
	.track-action:disabled {
		opacity: 0.52;
		cursor: not-allowed;
	}

	.tracks {
		display: grid;
		position: relative;
		align-items: start;
	}

	.track-labels {
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

	.track-label > :not(.track-title-level) {
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
		font-size: 10px;
	}

	.track-toggle.active {
		border-color: #78ddd5;
		background: #173a37;
		color: #d4fffb;
	}

	.lock-toggle {
		display: grid;
		place-items: center;
	}

	.track-action {
		min-height: 26px;
		padding: 3px 8px;
	}

	.track-collapse {
		width: 24px;
		height: 24px;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: #20262c;
		color: #aab6be;
		display: grid;
		place-items: center;
		cursor: pointer;
	}

	.track-label.collapsed {
		padding-block: 4px;
	}

	.track-label.collapsed > div:first-of-type > span {
		display: none;
	}

	.track-controls {
		display: flex;
		align-items: center;
		gap: 4px;
		flex-wrap: wrap;
		justify-content: flex-end;
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

	.track-resize-handle {
		position: absolute;
		left: 0;
		right: 0;
		bottom: -3px;
		z-index: 8;
		height: 6px;
		border: 0;
		background: transparent;
		cursor: ns-resize;
	}

	.track-resize-handle:hover {
		background: rgba(87, 208, 200, 0.28);
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
	}

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
		--row-tint: rgba(88, 209, 200, 0.06);
		border-bottom: 1px solid #2e363d;
		background:
			linear-gradient(90deg, transparent 0 9.8%, rgba(255, 255, 255, 0.052) 10%, transparent 10.2%) 0 0 / 20% 100%,
			linear-gradient(180deg, var(--row-tint), transparent),
			#11161b;
		overflow: hidden;
	}

	.row-original { --row-tint: rgba(88, 209, 200, 0.07); }
	.row-vocals { --row-tint: rgba(125, 164, 255, 0.07); }
	.row-background { --row-tint: rgba(217, 180, 95, 0.065); }
	.row-subtitle { --row-tint: rgba(173, 140, 255, 0.06); }
	.row-dub { --row-tint: rgba(101, 210, 143, 0.06); }

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
		left: 4%;
		top: 50%;
		transform: translateY(-50%);
		min-width: min(360px, 58%);
		max-width: 82%;
		max-height: calc(100% - 6px);
		box-sizing: border-box;
		border: 1px dashed #56616a;
		border-radius: 5px;
		padding: 4px 9px;
		color: var(--muted);
		background: rgba(255, 255, 255, 0.03);
		font-size: 11px;
		line-height: 15px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
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

	.cue-chip {
		min-width: 12px;
	}

	.cue-chip.active {
		border-color: #70ddd5;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.04), transparent),
			#173a37;
		box-shadow: inset 0 0 0 1px rgba(112, 221, 213, 0.18);
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

	.row-subtitle:not(.collapsed) .cue-chip .cue-text {
		overflow-x: auto;
		text-overflow: clip;
	}

	.cue-chip .cue-text::-webkit-scrollbar {
		display: none;
	}

	.row-subtitle.collapsed .cue-chip {
		top: 4px;
		height: 26px;
		padding: 4px 9px;
		border-radius: 5px;
	}

	.row-subtitle.collapsed .cue-chip strong,
	.row-subtitle.collapsed .cue-chip em {
		display: none;
	}

	.row-subtitle.collapsed .cue-chip .cue-text {
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
