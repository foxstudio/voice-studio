<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { API_RECOVERED_EVENT } from '$lib/api-health-recovery';
	import type { ProjectMediaHealth, VideoLocalizationDraft, VideoPlaybackProxyRequest, VideoPlaybackProxyStatus, VideoPreviewCacheStatus } from '$lib/api/types';
	import { previewCacheSpriteUrl, sourceAudioUrl, sourceVideoUrl, stemAudioUrl, timelineClipAudioUrl } from './utils';
	import { mediaAssetAvailable, mediaRepairLabel, sourceVideoConfigured } from './media-health';
	import { defaultTrackStates, resolveAudibleMix, TRACK_LABELS, type VideoLocalizationDubLaneStates, type VideoLocalizationTrackId, type VideoLocalizationTrackState, type VideoLocalizationTrackStates } from './studio-state';
	import type { SubtitleDisplayFrame, SubtitleDisplayStyle } from './subtitle-display';
	import { buildDubTrackLaneLayout, getDubTrackClipLane } from './dub-track-lanes';
	import { activeTimelineClips, audioPlaybackRateForDrift, clipSourceTimeSeconds, scheduledTimelineClips, shouldCorrectAudioDrift, shouldHardCorrectAudioDrift, timelineClipEndMs, timelineClipHasAudioSource, timelineClipKey, timelineClipPlayableRange, upcomingTimelineClips } from './preview-playback';
	import {
		isMediaAbortError,
		mediaClockAdvanced,
		playbackRequestStillCurrent,
		previewMediaIdentityKey,
		previewPlaybackModeLabel,
		shouldCommitMediaPause,
		shouldFallbackFromAudioPreview,
		shouldRestartPendingPlayback
	} from './preview-playback-policy';
	import { cachedPreviewRangeAt, containedMediaRect, previewSpriteCell } from './preview-cache';
	import { audioWindowReady, bufferedRangeCovered, buildPlaybackReadiness, playbackMediaIsLoading, playbackReadinessAt, type PlaybackMediaRequirement, type PlaybackReadinessStatus } from './playback-readiness';
	import { PreviewMediaRegistry } from './preview-media-registry';
	import { preparePlaybackMedia, restorePreparedMediaTime } from './playback-media-preparation';
	import {
		VIDEO_BLACK_SURFACE_MIN_ADVANCE_SECONDS,
		VIDEO_BLACK_SURFACE_TIMEOUT_MS,
		videoFramePixelsVisible,
		videoFramePresentationStalled
	} from './preview-video-health';
	import CommandSpinner from './CommandSpinner.svelte';
	import type { PreviewAudioAsset, PreviewAudioProxyPreparation, PreviewPlaybackVariant } from './preview-media-client';
	import {
		PreviewVideoProxyController,
		type PreviewVideoProxyState
	} from './preview-video-proxy-controller';
	import {
		appendPlaybackReloadRevision,
		stablePlaybackRevision
	} from './preview-media-url-revision';
	import {
		PreviewVideoSurfaceResumeController,
		previewVideoSurfaceKey
	} from './preview-video-surface';
	import { SegmentedVideoSourceController } from './segmented-video-source';
	import { probeDirectVideoSource } from './video-source-capability';

	type PlaybackController = {
		playPause: () => void;
		play: () => void;
		seek: (timeMs: number) => void;
		scrub: (timeMs: number) => void;
		endScrub: () => void;
		refreshMediaCache: () => void;
	};

	let {
		subtitleFrame,
		draft,
		mediaHealth,
		projectId,
		importing = false,
		trackStates = defaultTrackStates(),
		dubLaneStates = { '0': defaultTrackStates().dub },
		previewCache = null,
		playbackLoopRange = null,
		onRequestImport = () => {},
		onImportFile = () => {},
		onVideoTimeUpdate = () => {},
		onPlaybackStateChange = () => {},
		onPlaybackPreparingChange = () => {},
		onPlaybackReadinessChange = () => {},
		onPlaybackIssue = () => {},
		onRequestPreviewCache = () => {},
		onPrepareEditingProxy = async () => null,
		onPrepareAudioPreviewProxies = async () => null,
		onControllerReady = () => {}
	}: {
		subtitleFrame: SubtitleDisplayFrame;
		draft: VideoLocalizationDraft | null;
		mediaHealth: ProjectMediaHealth | null;
		projectId: string;
		importing?: boolean;
		trackStates?: VideoLocalizationTrackStates;
		dubLaneStates?: VideoLocalizationDubLaneStates;
		previewCache?: VideoPreviewCacheStatus | null;
		playbackLoopRange?: { start_ms: number; end_ms: number } | null;
		onRequestImport?: () => void;
		onImportFile?: (file: File) => void;
		onVideoTimeUpdate?: (timeMs: number) => void;
		onPlaybackStateChange?: (playing: boolean) => void;
		onPlaybackPreparingChange?: (preparing: boolean) => void;
		onPlaybackReadinessChange?: (status: PlaybackReadinessStatus | null) => void;
		onPlaybackIssue?: (message: string) => void;
		onRequestPreviewCache?: (timeMs: number) => void;
		onPrepareEditingProxy?: (
			projectId: string,
			request: VideoPlaybackProxyRequest
		) => Promise<VideoPlaybackProxyStatus | null>;
		onPrepareAudioPreviewProxies?: (
			projectId: string,
			assets: PreviewAudioAsset[]
		) => Promise<PreviewAudioProxyPreparation | null>;
		onControllerReady?: (controller: PlaybackController | null) => void;
	} = $props();

	let sourceVideoFailed = $state(false);
	let sourceVideoErrorTimer: ReturnType<typeof setTimeout> | null = null;
	let sourceVideoAutoRetryKey = '';
	let mediaReconnectPending = false;
	let videoPreviewEl = $state<HTMLDivElement | null>(null);
	let previewSize = $state<{ width: number; height: number } | null>(null);
	let previewVideoEl = $state<HTMLVideoElement | null>(null);
	let originalAudioEl = $state<HTMLAudioElement | null>(null);
	let vocalsAudioEl = $state<HTMLAudioElement | null>(null);
	let backgroundAudioEl = $state<HTMLAudioElement | null>(null);
	let playbackPositionMs = $state(0);
	let dragDepth = $state(0);
	let playbackFrame = 0;
	let presentedVideoFrame = 0;
	let presentedVideoFrameWatchActive = false;
	let firstPresentedVideoFramePending = false;
	let videoFrameProbeCanvas: HTMLCanvasElement | null = null;
	let lastVideoFramePixelSampleAt = 0;
	let lastPresentedVideoFrameAt = 0;
	let lastPresentedVideoMediaTime = 0;
	let videoFrameRecoveryCount = 0;
	let lastVideoFrameRecoveryAt = 0;
	let auxiliaryPlaybackFrame = 0;
	let hoverFallbackTimer: ReturnType<typeof setTimeout> | null = null;
	let hoverScrubbing = $state(false);
	let hoverScrubRestoreTime = 0;
	let hoverVideoWasSeeked = false;
	let hoverFrameSrc = $state('');
	let hoverFrameStyle = $state('');
	let hoverFrameViewportStyle = $state('');
	let hoverFramePendingSrc = $state('');
	let hoverFramePendingStyle = $state('');
	let mixAudioContext: AudioContext | null = null;
	let mixAudioResumePromise: Promise<void> | null = null;
	let lastAudioMaintenanceAt = 0;
	let lastPlaybackUiUpdateAt = 0;
	let mediaReloadRevision = $state(0);
	let dubReloads = $state<Record<string, number>>({});
	let videoSurfaceElementRevision = $state(0);
	let previewProxyKey = '';
	let segmentedVideoSrc = $state('');
	let previewVideoProxyState = $state<PreviewVideoProxyState>({
		projectId: '',
		mediaRevision: '',
		status: 'idle',
		mode: null,
		variant: null,
		playable: false,
		profile: '',
		revision: '',
		durationMs: 0,
		segmentMs: 4_000,
		requestedRange: { start_ms: 0, end_ms: 0 },
		readyRanges: [],
		activeSegment: null,
		readySegments: 0,
		totalSegments: 0,
		progress: 0,
		updatedAt: null,
		retryable: false,
		error: null,
		playbackStatus: null
	});
	const segmentedVideoSourceController = new SegmentedVideoSourceController({
		onSourceChange: (sourceUrl) => {
			segmentedVideoSrc = sourceUrl;
		},
		onError: (detail) => {
			sourceVideoFailed = true;
			onPlaybackIssue(detail);
		}
	});
	const previewVideoProxyController = new PreviewVideoProxyController({
		prepare: async (targetProjectId, request) => {
			const result = await onPrepareEditingProxy(targetProjectId, request);
			return result ?? {
				contract_version: 'video-playback-proxy-status-v2',
				state: 'failed',
				mode: 'segmented',
				variant: 'segments',
				playable: false,
				profile: 'segmented-h264-fmp4-v1',
				revision: '',
				duration_ms: draft?.source_media.duration_ms ?? 0,
				segment_ms: 4_000,
				requested_range: { start_ms: request.start_ms ?? 0, end_ms: request.end_ms ?? 0 },
				ready_ranges: [],
				active_segment: null,
				ready_segments: 0,
				total_segments: 0,
				progress: 0,
				updated_at: null,
				retryable: true,
				error: '没有取得兼容视频状态'
			};
		},
		probeSource: async (targetProjectId, mediaRevision) => probeDirectVideoSource(
			sourceVideoUrl(targetProjectId, mediaHealth, mediaRevision, 'source')
		),
		onStateChange: (state) => {
			previewVideoProxyState = state;
			if (state.mode === 'source') {
				segmentedVideoSourceController.dispose();
			} else if (state.playable && state.playbackStatus) {
				segmentedVideoSourceController.activate(state.projectId, state.playbackStatus);
			}
			if (state.playable) sourceVideoFailed = false;
		}
	});
	let sourceAudioVariant = $state<PreviewPlaybackVariant>('source');
	let vocalsAudioVariant = $state<PreviewPlaybackVariant>('source');
	let backgroundAudioVariant = $state<PreviewPlaybackVariant>('source');
	let audioPreviewKey = '';
	let preparedAudioPreviewReady = false;
	let preparedAudioPreviewVariants: PreviewAudioProxyPreparation['variants'] = {};
	let mediaResetKey = '';
	let pendingVideoRestore: { time: number; playing: boolean } | null = null;
	let playbackIntentRevision = 0;
	let pendingVideoPlayRevision = 0;
	let playbackWanted = $state(false);
	let audioSessionStarted = $state(false);
	let playbackPreparing = $state(false);
	let playbackStalledForAudio = false;
	let playbackReadiness = $state<PlaybackReadinessStatus | null>(null);
	let playbackReadinessTimer: ReturnType<typeof setInterval> | null = null;
	let playbackReadinessSignature = '';

	$effect(() => {
		onPlaybackPreparingChange(playbackPreparing);
	});
	$effect(() => {
		onPlaybackReadinessChange(playbackReadiness);
	});
	const PLAYBACK_UI_INTERVAL_MS = 50;
	// Media elements normally share the same clock after an aligned start. Check
	// often enough to recover from decoder stalls without repeatedly seeking them.
	const AUDIO_MAINTENANCE_INTERVAL_MS = 80;
	const mediaRegistry = new PreviewMediaRegistry();
	const dubAudioRecoveryAttempts = new Map<string, number>();
	const videoSurfaceResumeController = new PreviewVideoSurfaceResumeController();
	let audioStallRecoveryTimer: ReturnType<typeof setTimeout> | null = null;
	const AUDIO_PREFLIGHT_AHEAD_MS = 1200;
	const AUDIO_DURATION_TOLERANCE_MS = 50;
	const DUB_PREFETCH_LOOKAHEAD_MS = 4_000;
	const DUB_PREFETCH_CLIP_COUNT = 2;
	const VIDEO_FRAME_RECOVERY_RESET_MS = 5_000;
	const VIDEO_FRAME_PIXEL_SAMPLE_INTERVAL_MS = 250;

	const sourceVideoRevision = $derived(
		mediaHealth?.source_video.revision
			?? mediaHealth?.source_video.status
			?? ''
	);
	const previewVideoSrc = $derived(
		previewVideoProxyState.playable
		&& previewVideoProxyState.projectId === projectId
		&& previewVideoProxyState.mediaRevision === sourceVideoRevision
			? previewVideoProxyState.mode === 'source'
				? sourceVideoUrl(
					projectId,
					mediaHealth,
					stablePlaybackRevision(
						previewVideoProxyState.revision || sourceVideoRevision,
						mediaReloadRevision
					),
					'source'
				)
				: segmentedVideoSrc
			: ''
	);
	const originalAudioSrc = $derived(sourceAudioUrl(
		projectId,
		mediaHealth,
		stablePlaybackRevision(mediaHealth?.source_audio.revision, mediaReloadRevision),
		sourceAudioVariant
	));
	const vocalsAudioSrc = $derived(stemAudioUrl(
		projectId,
		mediaHealth,
		'vocals',
		stablePlaybackRevision(mediaHealth?.vocals.revision, mediaReloadRevision),
		vocalsAudioVariant
	));
	const backgroundAudioSrc = $derived(stemAudioUrl(
		projectId,
		mediaHealth,
		'background',
		stablePlaybackRevision(mediaHealth?.background.revision, mediaReloadRevision),
		backgroundAudioVariant
	));
	const dubTimelineClips = $derived((draft?.timeline_clips ?? []).filter((clip) => clip.track_id === 'dub'));
	// A dragged history item already has a playable history URL.  Mount it
	// immediately while the project-owned copy is committed in the background.
	const dubTrackClips = $derived(dubTimelineClips.filter(timelineClipHasAudioSource));
	const pendingDubTrackClips = $derived(dubTimelineClips.filter((clip) => !dubTrackClips.includes(clip) && ['prepared', 'queued', 'running', 'postprocessing', 'retrying', 'applying'].includes(String(clip.status ?? ''))));
	const dubLaneLayout = $derived(buildDubTrackLaneLayout(dubTrackClips));
	const dubClipByKey = $derived(new Map(dubTrackClips.map((clip) => [timelineClipKey(clip), clip])));
	const hasDubMedia = $derived(dubTrackClips.length > 0);
	const audibleMix = $derived(resolveAudibleMix({
		trackStates,
		trackMedia: {
			original: trackMediaAvailable('original', originalAudioSrc),
			vocals: trackMediaAvailable('vocals', vocalsAudioSrc),
			background: trackMediaAvailable('background', backgroundAudioSrc)
		},
		dubLanes: dubLaneLayout.lanes.map((lane, laneIndex) => ({
			lane: laneIndex,
			hasMedia: lane.length > 0,
			state: dubLaneState(laneIndex)
		}))
	}));
	const hasSoloTrack = $derived(audibleMix.hasSoloTrack);
	const originalActive = $derived(trackAudible('original', trackMediaAvailable('original', originalAudioSrc)));
	const vocalsActive = $derived(trackAudible('vocals', trackMediaAvailable('vocals', vocalsAudioSrc)));
	const backgroundActive = $derived(trackAudible('background', trackMediaAvailable('background', backgroundAudioSrc)));
	const dubActive = $derived(audibleMix.audibleDubLaneIds.length > 0);
	const previewModeLabel = $derived(playbackModeLabel());
	function subtitleLineStyle(style: SubtitleDisplayStyle) {
		return `font-size:${style.fontSize}px;--subtitle-bg:${style.backgroundOpacity};--subtitle-offset-x:${style.offsetX}px;--subtitle-offset-y:${style.offsetY}px;`;
	}

	function subtitleStyleClass(style: SubtitleDisplayStyle) {
		return `subtitle-line ${style.stylePreset}`;
	}
	const mountedDubTrackClips = $derived((playbackWanted || audioSessionStarted)
		? scheduledTimelineClips(
			dubTrackClips,
			'dub',
			playbackPositionMs,
			{
				futureLimit: DUB_PREFETCH_CLIP_COUNT,
				lookaheadMs: DUB_PREFETCH_LOOKAHEAD_MS
			}
		)
		: []);
	const dubPreloadKeys = $derived(new Set(mountedDubTrackClips.map(timelineClipKey)));
	const dragActive = $derived(dragDepth > 0);
	$effect(() => {
		if (playbackWanted) audioSessionStarted = true;
	});

	$effect(() => {
		const nextMediaResetKey = previewMediaIdentityKey(projectId, mediaHealth);
		if (nextMediaResetKey === mediaResetKey) return;
		mediaResetKey = nextMediaResetKey;
		audioSessionStarted = false;
		if (sourceVideoErrorTimer) clearTimeout(sourceVideoErrorTimer);
		sourceVideoErrorTimer = null;
		sourceVideoFailed = false;
		sourceVideoAutoRetryKey = '';
		mediaReconnectPending = false;
		playbackPositionMs = 0;
		playbackIntentRevision += 1;
		pendingVideoPlayRevision = 0;
		playbackWanted = false;
		playbackPreparing = false;
		playbackStalledForAudio = false;
		pendingVideoRestore = null;
		mediaReloadRevision = 0;
		dubReloads = {};
		videoSurfaceElementRevision = 0;
		videoSurfaceResumeController.reset();
		preparedAudioPreviewReady = false;
		preparedAudioPreviewVariants = {};
		sourceAudioVariant = 'source';
		vocalsAudioVariant = 'source';
		backgroundAudioVariant = 'source';
		previewVideoEl?.pause();
		pauseAuxiliaryTracks();
		clearAllAudioStallChecks();
		mediaRegistry.resetRuntime();
		resetVideoFrameHealth();
		clearMediaRuntimeStalls();
		onPlaybackStateChange(false);
	});

	$effect(() => {
		const readinessKey = [
			projectId,
			draft?.updated_at ?? '',
			previewCache?.revision ?? '',
			previewCache?.ready_chunks ?? 0,
			JSON.stringify(trackStates),
			JSON.stringify(dubLaneStates)
		].join(':');
		void readinessKey;
		queueMicrotask(refreshPlaybackReadiness);
	});

	$effect(() => {
		applyTrackMix();
	});

	onMount(() => {
		onControllerReady({ playPause: playPauseFromGesture, play: playFromGesture, seek: seekPreview, scrub: scrubPreview, endScrub: endScrubPreview, refreshMediaCache });
		document.addEventListener('visibilitychange', handleVisibilityChange);
		window.addEventListener('blur', handleWindowBlur);
		window.addEventListener('focus', handleWindowFocus);
		window.addEventListener(API_RECOVERED_EVENT, handleApiRecovered);
		playbackReadinessTimer = setInterval(refreshPlaybackReadiness, 2_000);
		refreshPlaybackReadiness();
		return () => {
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			window.removeEventListener('blur', handleWindowBlur);
			window.removeEventListener('focus', handleWindowFocus);
			window.removeEventListener(API_RECOVERED_EVENT, handleApiRecovered);
			onControllerReady(null);
		};
	});

	$effect(() => {
		const key = projectId && mediaAssetAvailable(mediaHealth, 'source_video')
			? `${projectId}:${sourceVideoRevision || 'available'}`
			: '';
		if (!key) {
			previewProxyKey = '';
			previewVideoProxyController.activate('', '');
			segmentedVideoSourceController.dispose();
			return;
		}
		if (key === previewProxyKey) return;
		previewProxyKey = key;
		previewVideoProxyController.activate(
			projectId,
			sourceVideoRevision || 'available'
		);
	});

	$effect(() => {
		const key = [
			projectId,
			mediaHealth?.source_audio.revision ?? mediaHealth?.source_audio.status ?? '',
			mediaHealth?.vocals.revision ?? mediaHealth?.vocals.status ?? '',
			mediaHealth?.background.revision ?? mediaHealth?.background.status ?? ''
		].join(':');
		if (!projectId || key === audioPreviewKey) return;
		audioPreviewKey = key;
		sourceAudioVariant = 'source';
		vocalsAudioVariant = 'source';
		backgroundAudioVariant = 'source';
		void prepareAudioPreviewProxies(projectId, key);
	});

	onDestroy(() => {
		stopPlaybackClock();
		cancelPresentedVideoFrameWatch();
		cancelAuxiliaryPlaybackStart();
		if (hoverFallbackTimer) clearTimeout(hoverFallbackTimer);
		if (sourceVideoErrorTimer) clearTimeout(sourceVideoErrorTimer);
		if (playbackReadinessTimer) clearInterval(playbackReadinessTimer);
		playbackReadinessTimer = null;
		mediaRegistry.cancelAllReadyRecoveries();
		clearAllAudioStallChecks();
		mediaRegistry.dispose();
		previewVideoProxyController.dispose();
		segmentedVideoSourceController.dispose();
		if (mixAudioContext) {
			mixAudioContext.onstatechange = null;
			void mixAudioContext.close();
		}
		mixAudioContext = null;
		mixAudioResumePromise = null;
		previewVideoEl?.pause();
		previewVideoEl?.removeAttribute('src');
		previewVideoEl?.load();
		onPlaybackReadinessChange(null);
	});

	function trackAudible(trackId: VideoLocalizationTrackId, hasMedia: boolean) {
		if (!hasMedia || trackId === 'dub' || trackId === 'subtitles' || trackId === 'localizedSubtitles') return false;
		return audibleMix.audibleTrackIds.includes(trackId);
	}

	function dubLaneState(lane: number): VideoLocalizationTrackState {
		return dubLaneStates[String(lane)] ?? (lane === 0
			? dubLaneStates['0'] ?? trackStates.dub
			: { muted: false, solo: false, volume: 1, locked: false });
	}

	function dubLaneForClip(clip: (typeof dubTrackClips)[number]) {
		return getDubTrackClipLane(dubLaneLayout, clip) ?? 0;
	}

	function dubLaneAudible(lane: number) {
		return audibleMix.audibleDubLaneIds.includes(lane);
	}

	function trackMediaAvailable(trackId: VideoLocalizationTrackId, fallbackSrc: string) {
		const disabledTracks = Array.isArray(draft?.ui_state?.disabled_media_tracks)
			? draft.ui_state.disabled_media_tracks.map(String)
			: [];
		if (disabledTracks.includes(trackId)) return false;
		if (trackId === 'original') return mediaAssetAvailable(mediaHealth, 'source_audio');
		if (trackId === 'vocals') return mediaAssetAvailable(mediaHealth, 'vocals');
		if (trackId === 'background') return mediaAssetAvailable(mediaHealth, 'background');
		return hasTimelineMedia(trackId) || Boolean(fallbackSrc);
	}

	function playbackModeLabel() {
		const labels: string[] = [];
		if (originalActive) labels.push(TRACK_LABELS.original);
		if (vocalsActive) labels.push(TRACK_LABELS.vocals);
		if (backgroundActive) labels.push(TRACK_LABELS.background);
		if (dubActive) labels.push(TRACK_LABELS.dub);
		const blockers = playbackReadinessAt(playbackReadiness, playbackPositionMs)?.blockers ?? [];
		return previewPlaybackModeLabel({
			activeTrackLabels: labels,
			hasSoloTrack,
			preparing: playbackPreparing,
			blockers
		});
	}

	function allAudioElements() {
		return mediaRegistry.allAudio();
	}

	function mediaBufferedRanges(media: HTMLMediaElement | null) {
		if (!media) return [];
		const ranges: { start_ms: number; end_ms: number }[] = [];
		for (let index = 0; index < media.buffered.length; index += 1) {
			ranges.push({
				start_ms: Math.max(0, Math.round(media.buffered.start(index) * 1000)),
				end_ms: Math.max(0, Math.round(media.buffered.end(index) * 1000))
			});
		}
		return ranges;
	}

	function audioReadyAtDesiredTime(audio: HTMLAudioElement) {
		if (audio.seeking) return false;
		return audioBufferedAtDesiredTime(audio);
	}

	function audioBufferedAtDesiredTime(audio: HTMLAudioElement) {
		const targetMs = Math.max(0, Math.round((mediaRegistry.desiredTime(audio) ?? audio.currentTime) * 1000));
		const declaredEndMs = Math.max(targetMs, Math.round((mediaRegistry.desiredEndTime(audio) ?? Number.POSITIVE_INFINITY) * 1000));
		const durationMs = Number.isFinite(audio.duration) ? Math.max(0, Math.round(audio.duration * 1000)) : null;
		return audioWindowReady({
			targetMs,
			declaredEndMs,
			durationMs,
			buffered: mediaBufferedRanges(audio),
			aheadMs: AUDIO_PREFLIGHT_AHEAD_MS,
			durationToleranceMs: AUDIO_DURATION_TOLERANCE_MS
		});
	}

	function audioOutputReady() {
		return !mixAudioContext || mixAudioContext.state === 'running';
	}

	function clearAudioStallCheck(audio: HTMLAudioElement) {
		mediaRegistry.clearStallCheck(audio);
	}

	function clearAllAudioStallChecks() {
		mediaRegistry.clearAllStallChecks();
		if (audioStallRecoveryTimer) clearTimeout(audioStallRecoveryTimer);
		audioStallRecoveryTimer = null;
	}

	function scheduleAudioStallRecoveryCheck() {
		if (audioStallRecoveryTimer) clearTimeout(audioStallRecoveryTimer);
		audioStallRecoveryTimer = setTimeout(() => {
			audioStallRecoveryTimer = null;
			if (!previewVideoEl || !playbackWanted || !playbackStalledForAudio) return;
			syncAuxiliaryTracks(false);
			const activeAudio = activeAudioElementsAtCurrentTime();
			if (activeAudio.every(audioReadyAtDesiredTime)) {
				resumePlaybackAfterAudioStall();
				return;
			}
			for (const audio of activeAudio) startAudioWhenReady(audio);
		}, 120);
	}

	function mediaRequirement(
		id: string,
		label: string,
		media: HTMLMediaElement | null,
		timelineStartMs: number,
		timelineEndMs: number,
		sourceStartMs = 0,
		sourceEndMs = timelineEndMs - timelineStartMs
	): PlaybackMediaRequirement {
		return {
			id,
			label,
			timeline_start_ms: Math.max(0, timelineStartMs),
			timeline_end_ms: Math.max(timelineStartMs, timelineEndMs),
			source_start_ms: Math.max(0, sourceStartMs),
			source_end_ms: Math.max(sourceStartMs, sourceEndMs),
			media_duration_ms: media && Number.isFinite(media.duration) ? Math.max(0, Math.round(media.duration * 1000)) : null,
			buffered: mediaBufferedRanges(media),
			loading: playbackMediaIsLoading(media),
			pending_timeline_ms: media ? mediaRegistry.runtimeStall(media)?.timelineMs ?? null : null,
			error: Boolean(media?.error || media?.dataset.playbackError)
		};
	}

	function appendTimelineTrackRequirements(
		items: PlaybackMediaRequirement[],
		trackId: VideoLocalizationTrackId,
		label: string,
		audio: HTMLAudioElement | null,
		active: boolean,
		durationMs: number
	) {
		if (!active) return;
		const clips = draft?.timeline_clips.filter((clip) => clip.track_id === trackId && timelineClipHasAudioSource(clip)) ?? [];
		if (!clips.length) {
			items.push(mediaRequirement(trackId, label, audio, 0, durationMs, 0, durationMs));
			return;
		}
		for (const clip of clips) {
			const range = timelineClipPlayableRange(clip);
			items.push(mediaRequirement(
				`${trackId}:${clip.clip_id}`,
				label,
				audio,
				range.timelineStartMs,
				range.timelineEndMs,
				range.sourceStartMs,
				range.sourceEndMs
			));
		}
	}

	function refreshPlaybackReadiness() {
		const durationMs = Math.max(
			0,
			draft?.source_media.duration_ms ?? Math.round((previewVideoEl?.duration || 0) * 1000),
			...(draft?.cues ?? []).map((cue) => cue.end_ms ?? 0),
			...(draft?.localized_subtitles ?? []).map((cue) => cue.end_ms),
			...(draft?.timeline_clips ?? []).map((clip) => timelineClipEndMs(clip))
		);
		if (!durationMs || !previewCache) {
			playbackReadiness = null;
			playbackReadinessSignature = '';
			return;
		}
		const media: PlaybackMediaRequirement[] = [mediaRequirement('video', '视频画面', previewVideoEl, 0, durationMs, 0, durationMs)];
		appendTimelineTrackRequirements(media, 'original', TRACK_LABELS.original, originalAudioEl, originalActive, durationMs);
		appendTimelineTrackRequirements(media, 'vocals', TRACK_LABELS.vocals, vocalsAudioEl, vocalsActive, durationMs);
		appendTimelineTrackRequirements(media, 'background', TRACK_LABELS.background, backgroundAudioEl, backgroundActive, durationMs);
		// Runtime playback only mounts the active clip and a small look-ahead window.
		// Readiness follows that same window instead of rescanning every dub clip in
		// a long project on a timer.
		for (const clip of mountedDubTrackClips) {
			const lane = dubLaneForClip(clip);
			if (!dubLaneAudible(lane)) continue;
			const range = timelineClipPlayableRange(clip);
			media.push(mediaRequirement(
				`dub:${timelineClipKey(clip)}`,
				`${TRACK_LABELS.dub} ${lane + 1}`,
				mediaRegistry.dubAudio(timelineClipKey(clip)) ?? null,
				range.timelineStartMs,
				range.timelineEndMs,
				range.sourceStartMs,
				range.sourceEndMs
			));
		}
		for (const clip of pendingDubTrackClips) {
			const lane = Math.max(0, Math.floor(Number(clip.dub_lane ?? 0)));
			if (!dubLaneAudible(lane)) continue;
			const startMs = Math.max(0, clip.start_ms ?? 0);
			const endMs = Math.max(startMs + 1, timelineClipEndMs(clip));
			if (endMs <= playbackPositionMs || startMs > playbackPositionMs + DUB_PREFETCH_LOOKAHEAD_MS) continue;
			media.push({
				id: `dub-pending:${timelineClipKey(clip)}`,
				label: `${TRACK_LABELS.dub} ${lane + 1} 生成中`,
				timeline_start_ms: startMs,
				timeline_end_ms: endMs,
				source_start_ms: 0,
				source_end_ms: endMs - startMs,
				media_duration_ms: null,
				buffered: [],
				loading: true,
				error: false
			});
		}
		const audioOutputRequired = Boolean(mixAudioContext && mediaRegistry.hasGains() && media.some((item) => item.id !== 'video'));
		if (audioOutputRequired) {
			const audioOutputRunning = mixAudioContext?.state === 'running';
			media.push({
				id: 'audio-output',
				label: '音频输出',
				timeline_start_ms: 0,
				timeline_end_ms: durationMs,
				source_start_ms: 0,
				source_end_ms: durationMs,
				media_duration_ms: durationMs,
				buffered: audioOutputRunning ? [{ start_ms: 0, end_ms: durationMs }] : [],
				loading: playbackWanted && !audioOutputRunning,
				error: Boolean(document.documentElement.dataset.audioPlaybackError)
			});
		}
		const next = buildPlaybackReadiness({ durationMs, chunkMs: previewCache.chunk_ms, previewRanges: previewCache.ranges, media });
		const signature = JSON.stringify(next);
		if (signature === playbackReadinessSignature) return;
		playbackReadinessSignature = signature;
		playbackReadiness = next;
	}

	function applyTrackMix() {
		if (previewVideoEl) {
			previewVideoEl.muted = Boolean(originalAudioSrc || vocalsAudioSrc || backgroundAudioSrc || hasDubMedia);
		}
		applyAudioTrack(originalAudioEl, originalActive, trackStates.original.volume);
		applyAudioTrack(vocalsAudioEl, vocalsActive, trackStates.vocals.volume);
		applyAudioTrack(backgroundAudioEl, backgroundActive, trackStates.background.volume);
		for (const [key, audio] of mediaRegistry.dubEntries()) {
			const clip = dubClipByKey.get(key);
			const lane = clip ? dubLaneForClip(clip) : 0;
			const state = dubLaneState(lane);
			applyAudioTrack(audio, dubLaneAudible(lane), state.volume);
		}
		if (previewVideoEl && !previewVideoEl.paused) syncAuxiliaryTracks(true);
	}

	function applyAudioTrack(audio: HTMLAudioElement | null, active: boolean, volume: number) {
		if (!audio) return;
		const gainNode = mediaRegistry.gainFor(audio);
		audio.muted = false;
		if (gainNode) {
			audio.volume = 1;
			gainNode.gain.value = active ? clampGain(volume) : 0;
		} else {
			audio.volume = active ? Math.min(1, clampGain(volume)) : 0;
		}
		if (!active) {
			audio.pause();
			resetAudioPlaybackRate(audio);
			return;
		}
	}

	function ensureTrackGain(audio: HTMLAudioElement) {
		const existing = mediaRegistry.gainFor(audio);
		if (existing) return existing;
		if (!mixAudioContext || mixAudioContext.state === 'closed') return null;
		try {
			const source = mixAudioContext.createMediaElementSource(audio);
			const gain = mixAudioContext.createGain();
			source.connect(gain).connect(mixAudioContext.destination);
			mediaRegistry.setAudioGraph(audio, source, gain);
			return gain;
		} catch {
			return null;
		}
	}

	function prepareAudioOutputFromGesture() {
		if (!mixAudioContext || mixAudioContext.state === 'closed') {
			try {
				mixAudioContext = new AudioContext();
				mixAudioContext.onstatechange = handleAudioContextStateChange;
			} catch {
				mixAudioContext = null;
			}
		}
		if (mixAudioContext) {
			for (const audio of mediaRegistry.allAudio()) {
				if (audio) ensureTrackGain(audio);
			}
			applyTrackMix();
		}
		void resumeAudioOutput();
	}

	function resumeAudioOutput(resyncAfterResume = false) {
		const context = mixAudioContext;
		if (!context || context.state === 'closed') return Promise.resolve();
		if (context.state === 'running') {
			if (resyncAfterResume && previewVideoEl && !previewVideoEl.paused) syncAuxiliaryTracks(true);
			return Promise.resolve();
		}
		if (mixAudioResumePromise) return mixAudioResumePromise;
		mixAudioResumePromise = context.resume()
			.then(() => {
				delete document.documentElement.dataset.audioPlaybackError;
				applyTrackMix();
				if (resyncAfterResume && previewVideoEl && !previewVideoEl.paused) syncAuxiliaryTracks(true);
			})
			.catch((error: unknown) => {
				document.documentElement.dataset.audioPlaybackError = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
			})
			.finally(() => {
				mixAudioResumePromise = null;
			});
		return mixAudioResumePromise;
	}

	function handleAudioContextStateChange() {
		refreshPlaybackReadiness();
		if (mixAudioContext?.state !== 'suspended' || !previewVideoEl || previewVideoEl.paused) return;
		playbackPreparing = true;
		playbackWanted = false;
		previewVideoEl.pause();
		pauseAuxiliaryTracks();
		onPlaybackStateChange(false);
		onPlaybackIssue('音频输出已暂停，点击播放即可恢复。');
	}

	function handleWindowBlur() {
		videoSurfaceResumeController.markSuspended();
	}

	function handleWindowFocus() {
		reconnectVideoSurfaceAfterResume();
	}

	function handleVisibilityChange() {
		if (document.visibilityState !== 'visible') {
			videoSurfaceResumeController.markSuspended();
			return;
		}
		reconnectVideoSurfaceAfterResume();
	}

	function reconnectVideoSurfaceAfterResume() {
		const video = previewVideoEl;
		const plan = videoSurfaceResumeController.consumeResume({
			visible: document.visibilityState === 'visible',
			hasVideo: Boolean(video),
			paused: video?.paused ?? true,
			ended: video?.ended ?? false,
			currentTimeSeconds: video?.currentTime ?? playbackPositionMs / 1000
		});
		if (!plan || !video) {
			if (!video || !video.ended) return;
			onPlaybackStateChange(false);
			stopPlaybackClock();
			pauseAuxiliaryTracks();
			return;
		}
		const recoveryRevision = ++playbackIntentRevision;
		pendingVideoRestore = {
			time: plan.restoreTimeSeconds,
			playing: plan.resumePlayback
		};
		pendingVideoPlayRevision = plan.resumePlayback ? recoveryRevision : -1;
		playbackWanted = plan.resumePlayback;
		playbackPreparing = plan.resumePlayback;
		playbackStalledForAudio = false;
		cancelPresentedVideoFrameWatch();
		stopPlaybackClock();
		video.pause();
		pauseAuxiliaryTracks();
		clearAllAudioStallChecks();
		sourceVideoFailed = false;
		videoSurfaceElementRevision = Math.max(
			Date.now(),
			videoSurfaceElementRevision + 1
		);
		queueMicrotask(refreshPlaybackReadiness);
	}

	function hasTimelineMedia(trackId: VideoLocalizationTrackId) {
		return Boolean(draft?.timeline_clips.some((clip) => clip.track_id === trackId && timelineClipHasAudioSource(clip)));
	}

	function syncAudioTrack(audio: HTMLAudioElement | null, currentTime: number, playIfNeeded = false, sourceEndTime?: number) {
		if (!audio) return;
		mediaRegistry.setDesiredRange(
			audio,
			currentTime,
			sourceEndTime ?? (Number.isFinite(audio.duration) ? audio.duration : currentTime + AUDIO_PREFLIGHT_AHEAD_MS / 1000)
		);
		if (audio.readyState < HTMLMediaElement.HAVE_METADATA) {
			if (playIfNeeded) {
				startAudioWhenReady(audio);
				playAudioTrack(audio);
			}
			return;
		}
		if (Number.isFinite(audio.duration) && currentTime > audio.duration + AUDIO_DURATION_TOLERANCE_MS / 1000) {
			audio.pause();
			resetAudioPlaybackRate(audio);
			return;
		}
		const playing = !audio.paused && !audio.ended;
		const needsSeek = playing
			? shouldHardCorrectAudioDrift(audio.currentTime, currentTime)
			: shouldCorrectAudioDrift(audio.currentTime, currentTime);
		if (needsSeek) {
			try {
				audio.currentTime = currentTime;
			} catch {
				startAudioWhenReady(audio);
			}
		}
		setAudioPlaybackRate(audio, playing ? audioPlaybackRateForDrift(audio.currentTime, currentTime) : 1);
		if (playIfNeeded && (audio.paused || audio.ended)) {
			playAudioTrack(audio);
		}
	}

	function setAudioPlaybackRate(audio: HTMLAudioElement, rate: number) {
		const nextRate = Math.max(0.95, Math.min(1.05, Number.isFinite(rate) ? rate : 1));
		if (Math.abs(audio.playbackRate - nextRate) > 0.001) audio.playbackRate = nextRate;
	}

	function resetAudioPlaybackRate(audio: HTMLAudioElement | null) {
		if (audio) setAudioPlaybackRate(audio, 1);
	}

	function playAudioTrack(audio: HTMLAudioElement) {
		if (!audioReadyAtDesiredTime(audio)) {
			startAudioWhenReady(audio);
			return;
		}
		if (mediaRegistry.isPlayPending(audio)) return;
		mediaRegistry.markPlayPending(audio);
		void audio.play().then(
			() => delete audio.dataset.playbackError,
			(error: unknown) => {
				if (isMediaAbortError(error)) return;
				audio.dataset.playbackError = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
			}
		).finally(() => mediaRegistry.clearPlayPending(audio));
	}

	function startAudioWhenReady(audio: HTMLAudioElement) {
		const pendingRecovery = mediaRegistry.readyRecovery(audio);
		if (pendingRecovery) {
			if (!audioReadyAtDesiredTime(audio)) return;
			pendingRecovery();
			if (playbackStalledForAudio && playbackWanted) scheduleAudioStallRecoveryCheck();
		}
		let timeout = 0;
		const cleanup = () => {
			audio.removeEventListener('loadedmetadata', start);
			audio.removeEventListener('canplay', start);
			audio.removeEventListener('progress', start);
			audio.removeEventListener('seeked', start);
			audio.removeEventListener('playing', start);
			audio.removeEventListener('error', handleError);
			if (timeout) clearTimeout(timeout);
			mediaRegistry.deleteReadyRecovery(audio);
			mediaRegistry.clearFetchPending(audio);
		};
		const fail = () => {
			cleanup();
			refreshPlaybackReadiness();
			if (playbackWanted && activeAudioElementsAtCurrentTime().includes(audio)) {
				reportAudioPlaybackFailure(audio);
			}
		};
		const handleError = () => {
			if (!audio.error) return;
			fail();
		};
		const start = () => {
			const desiredTime = mediaRegistry.desiredTime(audio);
			if (desiredTime != null && restorePreparedMediaTime(audio, desiredTime)) return;
			if (!audioReadyAtDesiredTime(audio)) return;
			cleanup();
			clearAudioStallCheck(audio);
			refreshPlaybackReadiness();
			if (playbackStalledForAudio && playbackWanted) {
				resumePlaybackAfterAudioStall();
				return;
			}
			if (playbackWanted && previewVideoEl && !previewVideoEl.paused) {
				syncAuxiliaryTracks(true);
				playbackPreparing = activeAudioElementsAtCurrentTime().some((item) => !audioReadyAtDesiredTime(item));
				return;
			}
		};
		audio.addEventListener('loadedmetadata', start);
		audio.addEventListener('canplay', start);
		audio.addEventListener('progress', start);
		audio.addEventListener('seeked', start);
		audio.addEventListener('playing', start);
		audio.addEventListener('error', handleError);
		mediaRegistry.setReadyRecovery(audio, cleanup);
		timeout = window.setTimeout(fail, 15_000);
		ensureAudioFetch(audio);
		if (audioReadyAtDesiredTime(audio)) queueMicrotask(start);
	}

	function ensureAudioFetch(audio: HTMLAudioElement) {
		if (audioReadyAtDesiredTime(audio) || mediaRegistry.isFetchPending(audio)) return;
		const targetMs = Math.max(0, Math.round((mediaRegistry.desiredTime(audio) ?? audio.currentTime) * 1000));
		const declaredEndMs = Math.max(targetMs, Math.round((mediaRegistry.desiredEndTime(audio) ?? Number.POSITIVE_INFINITY) * 1000));
		const durationMs = Number.isFinite(audio.duration) ? Math.max(0, Math.round(audio.duration * 1000)) : Number.POSITIVE_INFINITY;
		const preflightEndMs = Math.min(declaredEndMs, targetMs + AUDIO_PREFLIGHT_AHEAD_MS, durationMs);
		const targetCovered = bufferedRangeCovered(mediaBufferedRanges(audio), targetMs, preflightEndMs);
		const shouldFetch = audio.networkState === HTMLMediaElement.NETWORK_EMPTY
			|| (audio.networkState === HTMLMediaElement.NETWORK_IDLE && !targetCovered);
		if (!shouldFetch) return;
		mediaRegistry.markFetchPending(audio);
		audio.preload = 'auto';
		audio.load();
	}

	function pausePlaybackForAudioBuffering(activeAudio: HTMLAudioElement[]) {
		if (!previewVideoEl || !playbackWanted || !activeAudio.length) return;
		playbackPreparing = true;
		for (const audio of activeAudio) startAudioWhenReady(audio);
	}

	function markMediaRuntimeStall(media: HTMLMediaElement) {
		mediaRegistry.setRuntimeStall(media, {
			mediaTime: media.currentTime,
			timelineMs: Math.max(0, Math.round((previewVideoEl?.currentTime ?? playbackPositionMs / 1000) * 1000))
		});
		refreshPlaybackReadiness();
	}

	function clearMediaRuntimeStallIfAdvanced(media: HTMLMediaElement) {
		const stalled = mediaRegistry.runtimeStall(media);
		if (!stalled || media.seeking || media.readyState < HTMLMediaElement.HAVE_FUTURE_DATA) return;
		if (!mediaClockAdvanced(media.currentTime, stalled.mediaTime)) return;
		mediaRegistry.deleteRuntimeStall(media);
		refreshPlaybackReadiness();
	}

	function clearMediaRuntimeStalls() {
		if (!mediaRegistry.clearRuntimeStalls()) return;
		refreshPlaybackReadiness();
	}

	function reportAudioPlaybackFailure(audio: HTMLAudioElement) {
		playbackPreparing = false;
		playbackStalledForAudio = false;
		delete audio.dataset.stallRecoveryPending;
		onPlaybackIssue(`音频加载失败，视频已继续播放：${audio.getAttribute('aria-label') || '音频轨'}`);
	}

	function resumePlaybackAfterAudioStall() {
		if (!previewVideoEl || !playbackWanted || !playbackStalledForAudio) return;
		const activeAudio = activeAudioElementsAtCurrentTime();
		if (activeAudio.some((audio) => !audioReadyAtDesiredTime(audio))) return;
		playbackStalledForAudio = false;
		playbackPreparing = false;
		if (audioStallRecoveryTimer) clearTimeout(audioStallRecoveryTimer);
		audioStallRecoveryTimer = null;
		const resumeRevision = playbackIntentRevision;
		pendingVideoPlayRevision = resumeRevision;
		void previewVideoEl.play()
			.catch((error: unknown) => {
				if (!playbackRequestStillCurrent({
					requestRevision: resumeRevision,
					intentRevision: playbackIntentRevision,
					pendingRevision: pendingVideoPlayRevision,
					playbackWanted
				})) return;
				if (isMediaAbortError(error)) {
					playbackWanted = false;
					playbackPreparing = false;
					onPlaybackStateChange(false);
					pauseAuxiliaryTracks();
					return;
				}
				playbackWanted = false;
				playbackPreparing = false;
				onPlaybackStateChange(false);
				const detail = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
				previewVideoEl!.dataset.playbackError = detail;
				onPlaybackIssue(`视频恢复播放失败：${detail}`);
			})
			.finally(() => {
				if (pendingVideoPlayRevision === resumeRevision) pendingVideoPlayRevision = 0;
			});
	}

	function syncAuxiliaryTracks(playIfNeeded = false, requestedTime?: number) {
		const time = requestedTime ?? previewVideoEl?.currentTime ?? 0;
		if (originalActive) syncTimelineTrack('original', originalAudioEl, time, playIfNeeded);
		else originalAudioEl?.pause();
		if (vocalsActive) syncTimelineTrack('vocals', vocalsAudioEl, time, playIfNeeded);
		else vocalsAudioEl?.pause();
		if (backgroundActive) syncTimelineTrack('background', backgroundAudioEl, time, playIfNeeded);
		else backgroundAudioEl?.pause();
		if (dubActive) syncDubTrack(time, playIfNeeded);
		else pauseDubTracks();
	}

	function syncTimelineTrack(trackId: VideoLocalizationTrackId, audio: HTMLAudioElement | null, currentTime: number, playIfNeeded = false) {
		const trackClips = draft?.timeline_clips.filter((item) => item.track_id === trackId && timelineClipHasAudioSource(item)) ?? [];
		if (!trackClips.length) {
			syncAudioTrack(audio, currentTime, playIfNeeded);
			return;
		}
		const timeMs = Math.round(currentTime * 1000);
		const clip = activeTimelineClips(trackClips, trackId, timeMs)[0];
		if (!clip) {
			audio?.pause();
			resetAudioPlaybackRate(audio);
			return;
		}
		const range = timelineClipPlayableRange(clip);
		syncAudioTrack(audio, clipSourceTimeSeconds(clip, currentTime), playIfNeeded, range.sourceEndMs / 1000);
	}

	function syncDubTrack(currentTime: number, playIfNeeded = false) {
		const timeMs = Math.round(currentTime * 1000);
		const active = activeTimelineClips(dubTrackClips, 'dub', timeMs).filter((clip) => dubLaneAudible(dubLaneForClip(clip)));
		const activeKeys = new Set(active.map(timelineClipKey));
		for (const [key, audio] of mediaRegistry.dubEntries()) {
			if (!activeKeys.has(key)) {
				audio.pause();
				resetAudioPlaybackRate(audio);
			}
		}
		for (const clip of active) {
			const audio = mediaRegistry.dubAudio(timelineClipKey(clip));
			const range = timelineClipPlayableRange(clip);
			syncAudioTrack(audio ?? null, clipSourceTimeSeconds(clip, currentTime), playIfNeeded, range.sourceEndMs / 1000);
		}
	}

	function primeUpcomingDubTracks(currentTime: number) {
		const timeMs = Math.round(currentTime * 1000);
		for (const lane of dubLaneLayout.lanes) {
			for (const clip of upcomingTimelineClips(lane, 'dub', timeMs, DUB_PREFETCH_CLIP_COUNT)) {
				if ((clip.start_ms ?? 0) - timeMs > DUB_PREFETCH_LOOKAHEAD_MS) continue;
				const audio = mediaRegistry.dubAudio(timelineClipKey(clip));
				if (!audio) continue;
				const range = timelineClipPlayableRange(clip);
				const sourceStart = range.sourceStartMs / 1000;
				mediaRegistry.setDesiredRange(audio, sourceStart, range.sourceEndMs / 1000);
				if (audio.readyState >= HTMLMediaElement.HAVE_METADATA && audio.paused && !audio.seeking && shouldCorrectAudioDrift(audio.currentTime, sourceStart)) {
					try {
						audio.currentTime = sourceStart;
					} catch {
						// A following metadata/seeked event will retry readiness.
					}
				}
				if (!audioReadyAtDesiredTime(audio)) {
					startAudioWhenReady(audio);
				}
			}
		}
	}

	function pauseDubTracks() {
		for (const audio of mediaRegistry.dubValues()) {
			audio.pause();
			resetAudioPlaybackRate(audio);
		}
	}

	function registerDubAudio(audio: HTMLAudioElement, key: string) {
		mediaRegistry.registerDub(key, audio);
		if (mixAudioContext) ensureTrackGain(audio);
		const clip = dubClipByKey.get(key);
		const lane = clip ? dubLaneForClip(clip) : 0;
		applyAudioTrack(audio, dubLaneAudible(lane), dubLaneState(lane).volume);
		if (playbackWanted) {
			queueMicrotask(() => {
				if (!playbackWanted) return;
				preparePlaybackMediaAt(previewVideoEl?.currentTime ?? playbackPositionMs / 1000);
			});
		}
		queueMicrotask(refreshPlaybackReadiness);
		return {
			destroy() {
				mediaRegistry.releaseDub(key, audio);
				queueMicrotask(refreshPlaybackReadiness);
			}
		};
	}

	function registerFixedAudio(audio: HTMLAudioElement) {
		mediaRegistry.registerFixed(audio);
		if (mixAudioContext) ensureTrackGain(audio);
		applyTrackMix();
		if (playbackWanted) {
			queueMicrotask(() => {
				if (!playbackWanted) return;
				preparePlaybackMediaAt(previewVideoEl?.currentTime ?? playbackPositionMs / 1000);
			});
		}
		queueMicrotask(refreshPlaybackReadiness);
		return {
			destroy() {
				mediaRegistry.releaseFixed(audio);
				queueMicrotask(refreshPlaybackReadiness);
			}
		};
	}

	function refreshMediaCache() {
		playbackIntentRevision += 1;
		playbackWanted = false;
		playbackPreparing = false;
		playbackStalledForAudio = false;
		pendingVideoPlayRevision = -1;
		previewVideoEl?.pause();
		pauseAuxiliaryTracks();
		clearAllAudioStallChecks();
		resetVideoFrameHealth();
		clearMediaRuntimeStalls();
		onPlaybackStateChange(false);
		if (sourceVideoErrorTimer) clearTimeout(sourceVideoErrorTimer);
		sourceVideoErrorTimer = null;
		sourceVideoFailed = false;
		sourceVideoAutoRetryKey = '';
		mediaReconnectPending = false;
		preparedAudioPreviewReady = false;
		preparedAudioPreviewVariants = {};
		sourceAudioVariant = 'source';
		vocalsAudioVariant = 'source';
		backgroundAudioVariant = 'source';
		const revision = Date.now();
		mediaReloadRevision = revision;
		playbackReadiness = null;
		playbackReadinessSignature = '';
		queueMicrotask(refreshPlaybackReadiness);
		if (projectId && previewProxyKey) previewVideoProxyController.retry();
		if (projectId && audioPreviewKey) void prepareAudioPreviewProxies(projectId, audioPreviewKey);
	}

	function reconnectFailedMedia() {
		const restoreTime = previewVideoEl?.currentTime ?? playbackPositionMs / 1000;
		const shouldResume = Boolean(previewVideoEl && playbackWanted && !previewVideoEl.ended);
		refreshMediaCache();
		pendingVideoRestore = { time: restoreTime, playing: shouldResume };
		if (shouldResume) {
			playbackWanted = true;
			playbackPreparing = true;
			pendingVideoPlayRevision = playbackIntentRevision;
		}
		clearHoverPreviewFrame();
		onPlaybackIssue('服务已恢复，正在重新连接视频和音轨。');
	}

	function handleApiRecovered() {
		if (!mediaReconnectPending || !projectId) return;
		reconnectFailedMedia();
	}

	function handleSourceVideoError(event: Event) {
		const element = event.currentTarget as HTMLVideoElement;
		const failedSrc = element.getAttribute('src') ?? '';
		if (sourceVideoErrorTimer) clearTimeout(sourceVideoErrorTimer);
		sourceVideoErrorTimer = setTimeout(() => {
			sourceVideoErrorTimer = null;
			if (element !== previewVideoEl || failedSrc !== previewVideoSrc || !element.error) return;
			if (element.error.code === MediaError.MEDIA_ERR_ABORTED) return;
			mediaReconnectPending = true;
			const retryKey = `${projectId}:${mediaHealth?.source_video.revision ?? ''}`;
			if (sourceVideoAutoRetryKey !== retryKey) {
				sourceVideoAutoRetryKey = retryKey;
				pendingVideoRestore = { time: element.currentTime, playing: playbackWanted };
				mediaReloadRevision = Date.now();
				return;
			}
			sourceVideoFailed = true;
			refreshPlaybackReadiness();
		}, 350);
	}

	function dubPlaybackAudioUrl(clip: (typeof dubTrackClips)[number]) {
		return appendPlaybackReloadRevision(
			timelineClipAudioUrl(projectId, clip),
			Math.max(mediaReloadRevision, dubReloads[clip.clip_id] ?? 0)
		);
	}

	function seekPreview(timeMs: number) {
		const restartPendingPlayback = previewVideoEl
			? shouldRestartPendingPlayback(playbackWanted, previewVideoEl.paused)
			: false;
		if (restartPendingPlayback) {
			playbackIntentRevision += 1;
			pendingVideoPlayRevision = 0;
			playbackWanted = false;
			playbackPreparing = false;
			playbackStalledForAudio = false;
			mediaRegistry.cancelAllReadyRecoveries();
			clearAllAudioStallChecks();
			pauseAuxiliaryTracks();
		}
		hoverScrubbing = false;
		clearHoverPreviewFrame();
		clearMediaRuntimeStalls();
		const nextTime = Math.max(0, timeMs / 1000);
		playbackPositionMs = Math.round(nextTime * 1000);
		previewVideoProxyController.ensureRange(playbackPositionMs);
		if (!previewVideoEl) {
			pendingVideoRestore = { time: nextTime, playing: restartPendingPlayback };
			onVideoTimeUpdate(playbackPositionMs);
			return;
		}
		if (previewVideoEl.readyState < HTMLMediaElement.HAVE_METADATA) {
			pendingVideoRestore = { time: nextTime, playing: restartPendingPlayback };
			preparePlaybackMediaAt(nextTime);
			onVideoTimeUpdate(playbackPositionMs);
			return;
		}
		previewVideoEl.currentTime = nextTime;
		onVideoTimeUpdate(Math.round(nextTime * 1000));
		queueMicrotask(() => {
			if (!previewVideoEl) return;
			preparePlaybackMediaAt(nextTime);
			if (restartPendingPlayback) void playFromGesture();
		});
	}

	function scrubPreview(timeMs: number) {
		if (!previewVideoEl || !previewVideoEl.paused) return;
		if (!hoverScrubbing) {
			hoverScrubRestoreTime = previewVideoEl.currentTime;
			hoverVideoWasSeeked = false;
			pauseAuxiliaryTracks();
		}
		hoverScrubbing = true;
		const boundedTimeMs = Math.max(0, timeMs);
		if (previewFrameReadyAt(boundedTimeMs) && previewCache) {
			if (hoverFallbackTimer) clearTimeout(hoverFallbackTimer);
			hoverFallbackTimer = null;
			queueHoverPreviewFrame(boundedTimeMs);
			return;
		}
		onRequestPreviewCache(boundedTimeMs);
		hoverFrameSrc = '';
		if (hoverFallbackTimer) clearTimeout(hoverFallbackTimer);
		hoverFallbackTimer = setTimeout(() => {
			hoverFallbackTimer = null;
			if (!hoverScrubbing || !previewVideoEl?.paused || previewVideoEl.seeking) return;
			const nextTime = boundedTimeMs / 1000;
			const fastSeek = (previewVideoEl as HTMLVideoElement & { fastSeek?: (time: number) => void }).fastSeek;
			if (typeof fastSeek === 'function' && Math.abs(previewVideoEl.currentTime - nextTime) > 0.35) fastSeek.call(previewVideoEl, nextTime);
			else previewVideoEl.currentTime = nextTime;
			hoverVideoWasSeeked = true;
		}, 120);
	}

	function endScrubPreview() {
		if (hoverFallbackTimer) clearTimeout(hoverFallbackTimer);
		hoverFallbackTimer = null;
		if (!hoverScrubbing || !previewVideoEl) return;
		hoverScrubbing = false;
		clearHoverPreviewFrame();
		const shouldRestoreVideo = hoverVideoWasSeeked;
		if (shouldRestoreVideo) previewVideoEl.currentTime = hoverScrubRestoreTime;
		hoverVideoWasSeeked = false;
		pauseAuxiliaryTracks();
		if (shouldRestoreVideo) preparePlaybackMediaAt(hoverScrubRestoreTime);
	}

	function previewFrameReadyAt(timeMs: number) {
		return Boolean(cachedPreviewRangeAt(previewCache, timeMs));
	}

	function clearHoverPreviewFrame() {
		hoverFrameSrc = '';
		hoverFrameStyle = '';
		hoverFrameViewportStyle = '';
		hoverFramePendingSrc = '';
		hoverFramePendingStyle = '';
	}

	function queueHoverPreviewFrame(timeMs: number) {
		if (!previewCache) return;
		const cell = previewSpriteCell(previewCache, timeMs);
		const viewport = containedMediaRect(
			videoPreviewEl?.clientWidth ?? 0,
			videoPreviewEl?.clientHeight ?? 0,
			draft?.source_media.width ?? previewCache.frame_width,
			draft?.source_media.height ?? previewCache.frame_height
		);
		const nextSrc = previewCacheSpriteUrl(projectId, cell.chunkIndex, previewCache.revision);
		const nextStyle = `width:${cell.columns * 100}%;height:${cell.rows * 100}%;left:-${cell.column * 100}%;top:-${cell.row * 100}%`;
		hoverFrameViewportStyle = `left:${viewport.left}%;top:${viewport.top}%;width:${viewport.width}%;height:${viewport.height}%`;
		if (nextSrc === hoverFrameSrc) {
			hoverFrameStyle = nextStyle;
			hoverFramePendingSrc = '';
			hoverFramePendingStyle = '';
			return;
		}
		hoverFramePendingSrc = nextSrc;
		hoverFramePendingStyle = nextStyle;
	}

	function activatePendingHoverFrame(src: string) {
		if (!hoverScrubbing || src !== hoverFramePendingSrc) return;
		hoverFrameSrc = src;
		hoverFrameStyle = hoverFramePendingStyle;
		hoverFramePendingSrc = '';
		hoverFramePendingStyle = '';
	}

	function pauseAuxiliaryTracks() {
		for (const audio of [originalAudioEl, vocalsAudioEl, backgroundAudioEl]) {
			audio?.pause();
			resetAudioPlaybackRate(audio);
		}
		pauseDubTracks();
	}

	function playPauseFromGesture() {
		if (!previewVideoEl) return;
		if (!previewVideoEl.paused || playbackWanted) {
			playbackIntentRevision += 1;
			playbackWanted = false;
			playbackPreparing = false;
			playbackStalledForAudio = false;
			clearMediaRuntimeStalls();
			pendingVideoPlayRevision = 0;
			previewVideoEl.pause();
			pauseAuxiliaryTracks();
			onPlaybackStateChange(false);
			return;
		}
		void playFromGesture();
	}

	async function playFromGesture() {
		if (!previewVideoEl || (!previewVideoEl.paused && playbackWanted)) return;
		if (hoverScrubbing) endScrubPreview();
		const intentRevision = ++playbackIntentRevision;
		playbackWanted = true;
		onPlaybackStateChange(true);
		prepareAudioOutputFromGesture();
		// The first gesture mounts audio; pauses retain the bounded nearby set.
		// Let Svelte mount newly audible tracks before preparing their ranges.
		await tick();
		if (!previewVideoEl || intentRevision !== playbackIntentRevision || !playbackWanted) return;
		const activeAudio = preparePlaybackMediaAt(previewVideoEl.currentTime);
		playbackPreparing = activeAudio.some((audio) => !audioReadyAtDesiredTime(audio));
		void startPlaybackWhenAudioReady(intentRevision, activeAudio);
	}

	async function startPlaybackWhenAudioReady(intentRevision: number, activeAudio: HTMLAudioElement[]) {
		const startedAt = performance.now();
		while (
			(activeAudio.some((audio) => !audioReadyAtDesiredTime(audio)) || !audioOutputReady()) &&
			performance.now() - startedAt < 15_000 &&
			intentRevision === playbackIntentRevision &&
			playbackWanted
		) await new Promise((resolve) => setTimeout(resolve, 30));
		if (!previewVideoEl || intentRevision !== playbackIntentRevision || !playbackWanted) return;
		const unavailable = activeAudio.filter((audio) => !audioReadyAtDesiredTime(audio));
		const outputUnavailable = !audioOutputReady();
		if (unavailable.length || outputUnavailable) {
			playbackWanted = false;
			playbackPreparing = false;
			onPlaybackStateChange(false);
			const blockers = unavailable.map((audio) => audio.getAttribute('aria-label') || '音频轨');
			if (outputUnavailable) blockers.push('音频输出');
			previewVideoEl.dataset.playbackError = `音频准备超时：${blockers.join('、')}`;
			onPlaybackIssue(previewVideoEl.dataset.playbackError);
			refreshPlaybackReadiness();
			return;
		}
		pendingVideoPlayRevision = intentRevision;
		const videoPlayRequest = previewVideoEl.play();
		syncAuxiliaryTracks(true);
		void videoPlayRequest
			.then(() => {
				if (!playbackRequestStillCurrent({
					requestRevision: intentRevision,
					intentRevision: playbackIntentRevision,
					playbackWanted
				})) previewVideoEl?.pause();
			})
			.catch((error: unknown) => {
				if (intentRevision !== playbackIntentRevision) return;
				playbackWanted = false;
				playbackPreparing = false;
				onPlaybackStateChange(false);
				pauseAuxiliaryTracks();
				if (previewVideoEl && !isMediaAbortError(error)) {
					previewVideoEl.dataset.playbackError = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
				}
			})
				.finally(() => {
					if (pendingVideoPlayRevision === intentRevision) pendingVideoPlayRevision = 0;
				});
	}

	function preparePlaybackMediaAt(time: number) {
		return preparePlaybackMedia({
			time,
			syncAt: (targetTime) => syncAuxiliaryTracks(false, targetTime),
			primeUpcomingAt: primeUpcomingDubTracks,
			activeAt: activeAudioElementsAt,
			isReady: audioReadyAtDesiredTime,
			startPreparing: startAudioWhenReady
		});
	}

	function activeAudioElementsAt(time: number) {
		const items: HTMLAudioElement[] = [];
		if (originalActive && timelineTrackActiveAt('original', time) && originalAudioEl) items.push(originalAudioEl);
		if (vocalsActive && timelineTrackActiveAt('vocals', time) && vocalsAudioEl) items.push(vocalsAudioEl);
		if (backgroundActive && timelineTrackActiveAt('background', time) && backgroundAudioEl) items.push(backgroundAudioEl);
		if (dubActive) {
			for (const clip of activeTimelineClips(dubTrackClips, 'dub', Math.round(time * 1000)).filter((item) => dubLaneAudible(dubLaneForClip(item)))) {
				const audio = mediaRegistry.dubAudio(timelineClipKey(clip));
				if (audio) items.push(audio);
			}
		}
		return [...new Set(items)];
	}

	function activeAudioElementsAtCurrentTime() {
		return activeAudioElementsAt(previewVideoEl?.currentTime ?? 0);
	}

	function timelineTrackActiveAt(trackId: VideoLocalizationTrackId, time: number) {
		const clips = draft?.timeline_clips.filter((clip) => clip.track_id === trackId && timelineClipHasAudioSource(clip)) ?? [];
		if (!clips.length) return true;
		const timeMs = Math.round(time * 1000);
		return activeTimelineClips(clips, trackId, timeMs).length > 0;
	}

	async function prepareAudioPreviewProxies(targetProjectId: string, key: string) {
		const assets: PreviewAudioAsset[] = [];
		if (mediaAssetAvailable(mediaHealth, 'source_audio')) assets.push('source_audio');
		if (mediaAssetAvailable(mediaHealth, 'vocals')) assets.push('vocals');
		if (mediaAssetAvailable(mediaHealth, 'background')) assets.push('background');
		try {
			const payload = await onPrepareAudioPreviewProxies(targetProjectId, assets);
			if (!payload || audioPreviewKey !== key || !Object.keys(payload.variants).length) return;
			preparedAudioPreviewVariants = { ...payload.variants };
			preparedAudioPreviewReady = true;
			activatePreparedAudioPreviewsIfIdle();
		} catch {
			// Keep using the editing masters when audio proxies cannot be prepared.
		}
	}

	function activatePreparedAudioPreviewsIfIdle() {
		if (!preparedAudioPreviewReady || (previewVideoEl && !previewVideoEl.paused) || playbackWanted) return;
		if (preparedAudioPreviewVariants.source_audio) sourceAudioVariant = preparedAudioPreviewVariants.source_audio;
		if (preparedAudioPreviewVariants.vocals) vocalsAudioVariant = preparedAudioPreviewVariants.vocals;
		if (preparedAudioPreviewVariants.background) backgroundAudioVariant = preparedAudioPreviewVariants.background;
		preparedAudioPreviewReady = false;
		preparedAudioPreviewVariants = {};
	}

	function handleVideoLoadedMetadata() {
		sourceVideoFailed = false;
		sourceVideoAutoRetryKey = '';
		const restore = pendingVideoRestore;
		resetVideoFrameHealth(!restore);
		if (!restore || !previewVideoEl) return;
		pendingVideoRestore = null;
		previewVideoEl.currentTime = Math.min(restore.time, previewVideoEl.duration || restore.time);
		preparePlaybackMediaAt(previewVideoEl.currentTime);
		if (restore.playing) void playFromGesture();
	}

	async function handleVideoPlay() {
		if (pendingVideoPlayRevision && pendingVideoPlayRevision !== playbackIntentRevision) {
			previewVideoEl?.pause();
			return;
		}
		if (!pendingVideoPlayRevision) {
			const intentRevision = ++playbackIntentRevision;
			playbackWanted = true;
			playbackPreparing = true;
			pendingVideoPlayRevision = intentRevision;
			previewVideoEl?.pause();
			prepareAudioOutputFromGesture();
			await tick();
			if (!previewVideoEl || intentRevision !== playbackIntentRevision || !playbackWanted) return;
			const activeAudio = preparePlaybackMediaAt(previewVideoEl?.currentTime ?? 0);
			void startPlaybackWhenAudioReady(intentRevision, activeAudio);
			return;
		}
		void resumeAudioOutput();
		if (!presentedVideoFrameWatchActive) beginPresentedVideoFrameWatch();
		syncAuxiliaryTracks(true);
		onPlaybackStateChange(true);
		startPlaybackClock();
		scheduleAuxiliaryPlaybackStart();
	}

	function handleVideoWaiting() {
		if (!playbackWanted) return;
		if (previewVideoEl) markMediaRuntimeStall(previewVideoEl);
		playbackPreparing = true;
		pauseAuxiliaryTracks();
	}

	function handleVideoPlaying() {
		if (!playbackWanted) return;
		if (previewVideoEl) delete previewVideoEl.dataset.playbackError;
		if (!presentedVideoFrameWatchActive) beginPresentedVideoFrameWatch();
		playbackPreparing = activeAudioElementsAtCurrentTime().some((audio) => !audioReadyAtDesiredTime(audio));
		syncAuxiliaryTracks(true);
	}

	function handlePlaybackGesturePointer(event: PointerEvent) {
		const target = event.target as HTMLElement | null;
		if (target === previewVideoEl) {
			if (pendingVideoPlayRevision === -1) pendingVideoPlayRevision = 0;
			prepareAudioOutputFromGesture();
		}
	}

	function handleVideoPause() {
		if (previewVideoEl && !shouldCommitMediaPause(previewVideoEl.paused, previewVideoEl.ended)) return;
		cancelPresentedVideoFrameWatch();
		if (playbackStalledForAudio && playbackWanted) {
			playbackPreparing = true;
			stopPlaybackClock();
			cancelAuxiliaryPlaybackStart();
			pauseAuxiliaryTracks();
			return;
		}
		if (pendingVideoPlayRevision && playbackWanted) {
			stopPlaybackClock();
			cancelAuxiliaryPlaybackStart();
			pauseAuxiliaryTracks();
			return;
		}
		if (!pendingVideoPlayRevision) playbackWanted = false;
		playbackPreparing = false;
		clearAllAudioStallChecks();
		onPlaybackStateChange(false);
		stopPlaybackClock();
		cancelAuxiliaryPlaybackStart();
		pauseAuxiliaryTracks();
		activatePreparedAudioPreviewsIfIdle();
	}

	function handleVideoSeek(event: Event) {
		if (hoverScrubbing) return;
		const video = event.currentTarget as HTMLVideoElement;
		if (event.type === 'seeking' && playbackWanted && !video.paused) beginPresentedVideoFrameWatch();
		const timeMs = Math.round(video.currentTime * 1000);
		playbackPositionMs = timeMs;
		if (previewVideoProxyState.mode === 'segmented') {
			previewVideoProxyController.ensureRange(timeMs);
		}
		clearAllAudioStallChecks();
		onVideoTimeUpdate(timeMs);
		if (playbackStalledForAudio && playbackWanted) {
			mediaRegistry.cancelAllReadyRecoveries();
			for (const audio of allAudioElements()) delete audio.dataset.stallRecoveryPending;
			const activeAudio = preparePlaybackMediaAt(timeMs / 1000);
			if (activeAudio.every(audioReadyAtDesiredTime)) {
				resumePlaybackAfterAudioStall();
			} else {
				for (const audio of activeAudio) startAudioWhenReady(audio);
			}
			return;
		}
		if (previewVideoEl?.paused) preparePlaybackMediaAt(timeMs / 1000);
		else {
			primeUpcomingDubTracks(timeMs / 1000);
			syncAuxiliaryTracks(true, timeMs / 1000);
		}
	}

	function handleVideoTimeUpdate(event: Event) {
		const video = event.currentTarget as HTMLVideoElement;
		if (hoverScrubbing) return;
		playbackPositionMs = Math.round(video.currentTime * 1000);
		if (enforcePlaybackLoop(video)) return;
		if (!video.paused && playbackFrame) return;
		onPlaybackStateChange(!video.paused && !video.ended);
		onVideoTimeUpdate(Math.round(video.currentTime * 1000));
	}

	function startPlaybackClock() {
		stopPlaybackClock();
		lastPlaybackUiUpdateAt = 0;
		lastAudioMaintenanceAt = performance.now();
		const tick = () => {
			if (!previewVideoEl || previewVideoEl.paused) {
				playbackFrame = 0;
				return;
			}
			if (enforcePlaybackLoop(previewVideoEl)) {
				playbackFrame = requestAnimationFrame(tick);
				return;
			}
			const now = performance.now();
			sampleVideoFramePixels(now);
			const nativeFrameWatch = supportsVideoFrameCallbacks(previewVideoEl);
			if (videoFramePresentationStalled({
				nowMs: now,
				lastPresentedAtMs: lastPresentedVideoFrameAt,
				mediaTimeSeconds: previewVideoEl.currentTime,
				lastPresentedMediaTimeSeconds: lastPresentedVideoMediaTime,
				playing: presentedVideoFrameWatchActive && !previewVideoEl.paused && !previewVideoEl.ended,
				seeking: previewVideoEl.seeking,
				readyState: previewVideoEl.readyState,
				firstFramePending: firstPresentedVideoFramePending,
				timeoutMs: nativeFrameWatch ? undefined : VIDEO_BLACK_SURFACE_TIMEOUT_MS,
				minAdvanceSeconds: nativeFrameWatch ? undefined : VIDEO_BLACK_SURFACE_MIN_ADVANCE_SECONDS
			})) {
				recoverVideoFramePresentation();
				return;
			}
			if (now - lastPlaybackUiUpdateAt >= PLAYBACK_UI_INTERVAL_MS) {
				lastPlaybackUiUpdateAt = now;
				playbackPositionMs = Math.round(previewVideoEl.currentTime * 1000);
				onPlaybackStateChange(true);
				onVideoTimeUpdate(playbackPositionMs);
			}
			if (now - lastAudioMaintenanceAt >= AUDIO_MAINTENANCE_INTERVAL_MS) {
				lastAudioMaintenanceAt = now;
				if (mixAudioContext?.state === 'suspended') void resumeAudioOutput(true);
				primeUpcomingDubTracks(previewVideoEl.currentTime);
				syncAuxiliaryTracks(true);
				const activeAudio = activeAudioElementsAtCurrentTime();
				clearMediaRuntimeStallIfAdvanced(previewVideoEl);
				for (const audio of activeAudio) clearMediaRuntimeStallIfAdvanced(audio);
				if (activeAudio.some((audio) => !audioBufferedAtDesiredTime(audio))) {
					pausePlaybackForAudioBuffering(activeAudio);
				} else if (!playbackStalledForAudio) {
					playbackPreparing = false;
				}
			}
			playbackFrame = requestAnimationFrame(tick);
		};
		playbackFrame = requestAnimationFrame(tick);
	}

	function beginPresentedVideoFrameWatch() {
		const video = previewVideoEl;
		cancelPresentedVideoFrameWatch();
		lastPresentedVideoFrameAt = performance.now();
		lastPresentedVideoMediaTime = video?.currentTime ?? 0;
		presentedVideoFrameWatchActive = Boolean(video);
		firstPresentedVideoFramePending = Boolean(video);
		if (!video || !supportsVideoFrameCallbacks(video)) return;
		const recordPresentedFrame = (now: number, metadata: VideoFrameCallbackMetadata) => {
			if (video !== previewVideoEl) return;
			recordPresentedVideoFrame(now, metadata.mediaTime);
			presentedVideoFrame = video.requestVideoFrameCallback(recordPresentedFrame);
		};
		presentedVideoFrame = video.requestVideoFrameCallback(recordPresentedFrame);
	}

	function sampleVideoFramePixels(now: number) {
		const video = previewVideoEl;
		if (
			!presentedVideoFrameWatchActive
			|| !video
			|| supportsVideoFrameCallbacks(video)
			|| video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
			|| !video.videoWidth
			|| !video.videoHeight
			|| now - lastVideoFramePixelSampleAt < VIDEO_FRAME_PIXEL_SAMPLE_INTERVAL_MS
		) return;
		lastVideoFramePixelSampleAt = now;
		try {
			videoFrameProbeCanvas ??= document.createElement('canvas');
			videoFrameProbeCanvas.width = 24;
			videoFrameProbeCanvas.height = 14;
			const context = videoFrameProbeCanvas.getContext('2d', { willReadFrequently: true });
			if (!context) {
				presentedVideoFrameWatchActive = false;
				return;
			}
			context.drawImage(video, 0, 0, videoFrameProbeCanvas.width, videoFrameProbeCanvas.height);
			const pixels = context.getImageData(0, 0, videoFrameProbeCanvas.width, videoFrameProbeCanvas.height).data;
			if (videoFramePixelsVisible(pixels)) recordPresentedVideoFrame(now, video.currentTime);
		} catch {
			// A browser may forbid video pixel reads even for a same-origin proxy.
			// Disable this fallback instead of treating an unreadable frame as black.
			presentedVideoFrameWatchActive = false;
		}
	}

	function supportsVideoFrameCallbacks(video: HTMLVideoElement) {
		return typeof (video as unknown as { requestVideoFrameCallback?: unknown }).requestVideoFrameCallback === 'function';
	}

	function recordPresentedVideoFrame(now: number, mediaTime: number) {
		lastPresentedVideoFrameAt = now;
		lastPresentedVideoMediaTime = mediaTime;
		firstPresentedVideoFramePending = false;
		if (videoFrameRecoveryCount && now - lastVideoFrameRecoveryAt >= VIDEO_FRAME_RECOVERY_RESET_MS) {
			videoFrameRecoveryCount = 0;
		}
	}

	function cancelPresentedVideoFrameWatch() {
		const video = previewVideoEl;
		if (presentedVideoFrame && video?.cancelVideoFrameCallback) video.cancelVideoFrameCallback(presentedVideoFrame);
		presentedVideoFrame = 0;
		presentedVideoFrameWatchActive = false;
		firstPresentedVideoFramePending = false;
		lastVideoFramePixelSampleAt = 0;
	}

	function resetVideoFrameHealth(resetRecoveryBudget = true) {
		cancelPresentedVideoFrameWatch();
		lastPresentedVideoFrameAt = performance.now();
		lastPresentedVideoMediaTime = previewVideoEl?.currentTime ?? 0;
		if (resetRecoveryBudget) {
			videoFrameRecoveryCount = 0;
			lastVideoFrameRecoveryAt = 0;
		}
	}

	function recoverVideoFramePresentation() {
		const video = previewVideoEl;
		if (!video) return;
		if (videoFrameRecoveryCount >= 1 && performance.now() - lastVideoFrameRecoveryAt < VIDEO_FRAME_RECOVERY_RESET_MS) {
			playbackIntentRevision += 1;
			playbackWanted = false;
			playbackPreparing = false;
			pendingVideoPlayRevision = 0;
			video.pause();
			pauseAuxiliaryTracks();
			onPlaybackStateChange(false);
			sourceVideoFailed = true;
			onPlaybackIssue('视频画面仍未恢复，请重新加载画面。');
			return;
		}
		const restoreTime = video.currentTime;
		const shouldResume = playbackWanted && !video.ended;
		videoFrameRecoveryCount += 1;
		lastVideoFrameRecoveryAt = performance.now();
		const recoveryRevision = ++playbackIntentRevision;
		pendingVideoRestore = { time: restoreTime, playing: shouldResume };
		pendingVideoPlayRevision = shouldResume ? recoveryRevision : -1;
		playbackPreparing = shouldResume;
		cancelPresentedVideoFrameWatch();
		video.pause();
		pauseAuxiliaryTracks();
		sourceVideoFailed = false;
		mediaReloadRevision = Date.now();
		onPlaybackIssue('检测到视频画面停止更新，正在自动恢复。');
	}

	function scheduleAuxiliaryPlaybackStart() {
		cancelAuxiliaryPlaybackStart();
		auxiliaryPlaybackFrame = requestAnimationFrame(() => {
			auxiliaryPlaybackFrame = 0;
			if (!previewVideoEl || previewVideoEl.paused) return;
			syncAuxiliaryTracks(true);
		});
	}

	function cancelAuxiliaryPlaybackStart() {
		if (!auxiliaryPlaybackFrame) return;
		cancelAnimationFrame(auxiliaryPlaybackFrame);
		auxiliaryPlaybackFrame = 0;
	}

	function enforcePlaybackLoop(video: HTMLVideoElement) {
		const range = playbackLoopRange;
		if (!range || video.paused || range.end_ms <= range.start_ms) return false;
		if (video.currentTime * 1000 + 24 < range.end_ms) return false;
		const startMs = Math.max(0, Math.round(range.start_ms));
		video.currentTime = startMs / 1000;
		onVideoTimeUpdate(startMs);
		syncAuxiliaryTracks(true);
		return true;
	}

	function stopPlaybackClock() {
		if (!playbackFrame) return;
		cancelAnimationFrame(playbackFrame);
		playbackFrame = 0;
	}

	function clampGain(value: number | null | undefined) {
		const parsed = Number(value);
		if (!Number.isFinite(parsed)) return 1;
		return Math.max(0, Math.min(4, parsed));
	}

	function handleAuxiliaryPlaybackStall(event: Event) {
		const audio = event.currentTarget as HTMLAudioElement;
		if (!previewVideoEl || !playbackWanted || mediaRegistry.hasStallCheck(audio)) return;
		if (!activeAudioElementsAtCurrentTime().includes(audio)) {
			refreshPlaybackReadiness();
			return;
		}
		markMediaRuntimeStall(audio);
		audio.dataset.stallRecoveryPending = 'true';
		startAudioWhenReady(audio);
		void resumeAudioOutput();
		const timer = setTimeout(() => {
			mediaRegistry.deleteStallCheck(audio);
			delete audio.dataset.stallRecoveryPending;
			if (!previewVideoEl || !playbackWanted || !activeAudioElementsAtCurrentTime().includes(audio)) return;
			if (audioReadyAtDesiredTime(audio)) {
				syncAuxiliaryTracks(true);
				refreshPlaybackReadiness();
				return;
			}
			playbackPreparing = true;
			startAudioWhenReady(audio);
		}, 180);
		mediaRegistry.setStallCheck(audio, timer);
	}

	function handleAuxiliaryPlaybackReady(event: Event) {
		const audio = event.currentTarget as HTMLAudioElement;
		const dubClipId = audio.dataset.dubClip;
		if (dubClipId) dubAudioRecoveryAttempts.delete(dubClipId);
		clearAudioStallCheck(audio);
		refreshPlaybackReadiness();
		if (playbackStalledForAudio && playbackWanted) {
			syncAuxiliaryTracks(false);
			if (activeAudioElementsAtCurrentTime().every(audioReadyAtDesiredTime)) resumePlaybackAfterAudioStall();
			else scheduleAudioStallRecoveryCheck();
		} else if (playbackWanted && activeAudioElementsAtCurrentTime().every(audioReadyAtDesiredTime)) {
			playbackPreparing = false;
		}
	}

	function handleAuxiliaryPlaybackError(event: Event) {
		const audio = event.currentTarget as HTMLAudioElement;
		if (mediaRegistry.isReleased(audio)) return;
		clearAudioStallCheck(audio);
		refreshPlaybackReadiness();
		if (!audio.error) return;
		if (recoverFailedAudioPreview(audio)) return;
		if (recoverFailedDubAudio(audio)) return;
		mediaReconnectPending = true;
		if (playbackWanted && activeAudioElementsAtCurrentTime().includes(audio)) {
			reportAudioPlaybackFailure(audio);
			return;
		}
		onPlaybackIssue(`音频加载失败：${audio.getAttribute('aria-label') || '音频轨'}`);
	}

	function recoverFailedDubAudio(audio: HTMLAudioElement) {
		const clipId = audio.dataset.dubClip;
		if (!clipId || (dubAudioRecoveryAttempts.get(clipId) ?? 0) >= 1) return false;
		dubAudioRecoveryAttempts.set(clipId, 1);
		const wasActive = playbackWanted && activeAudioElementsAtCurrentTime().includes(audio);
		if (wasActive) {
			playbackPreparing = true;
		}
		mediaReconnectPending = false;
		dubReloads = {
			...dubReloads,
			[clipId]: Date.now()
		};
		onPlaybackIssue('合成配音临时加载失败，正在自动重连。');
		return true;
	}

	function recoverFailedAudioPreview(audio: HTMLAudioElement) {
		const isSourceAudio = audio === originalAudioEl;
		const isVocalsAudio = audio === vocalsAudioEl;
		const isBackgroundAudio = audio === backgroundAudioEl;
		const variant = isSourceAudio
			? sourceAudioVariant
			: isVocalsAudio
				? vocalsAudioVariant
				: isBackgroundAudio
					? backgroundAudioVariant
					: null;
		if (!variant || !shouldFallbackFromAudioPreview(variant)) return false;

		if (isSourceAudio) sourceAudioVariant = 'source';
		else if (isVocalsAudio) vocalsAudioVariant = 'source';
		else if (isBackgroundAudio) backgroundAudioVariant = 'source';
		else return false;

		const wasActive = playbackWanted
			&& activeAudioElementsAtCurrentTime().includes(audio);
		mediaReconnectPending = false;
		delete audio.dataset.stallRecoveryPending;
		if (wasActive) {
			playbackPreparing = true;
			queueMicrotask(() => {
				const recoveredAudio = isSourceAudio
					? originalAudioEl
					: isVocalsAudio
						? vocalsAudioEl
						: backgroundAudioEl;
				if (!recoveredAudio) return;
				startAudioWhenReady(recoveredAudio);
			});
		}
		if (projectId && audioPreviewKey) {
			void prepareAudioPreviewProxies(projectId, audioPreviewKey);
		}
		onPlaybackIssue(
			`${audio.getAttribute('aria-label') || '音频轨'}播放缓存已失效，`
			+ '已自动切回母文件并重建缓存。'
		);
		return true;
	}

	function handlePreviewDragEnter(event: DragEvent) {
		event.preventDefault();
		if (importing || !hasDraggedFiles(event)) {
			if (event.dataTransfer) event.dataTransfer.dropEffect = 'none';
			return;
		}
		dragDepth += 1;
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
	}

	function handlePreviewDragOver(event: DragEvent) {
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = importing ? 'none' : 'copy';
	}

	function handlePreviewDragLeave(event: DragEvent) {
		event.preventDefault();
		dragDepth = Math.max(0, dragDepth - 1);
	}

	function handlePreviewDrop(event: DragEvent) {
		event.preventDefault();
		dragDepth = 0;
		if (importing) return;
		const file = event.dataTransfer?.files?.[0];
		if (file) onImportFile(file);
	}

	function hasDraggedFiles(event: DragEvent) {
		const transfer = event.dataTransfer;
		if (!transfer) return false;
		return Array.from(transfer.types ?? []).includes('Files') || transfer.files.length > 0;
	}

	function beginPreviewResize(event: PointerEvent) {
		if (!videoPreviewEl) return;
		event.preventDefault();
		event.stopPropagation();
		const rect = videoPreviewEl.getBoundingClientRect();
		const startX = event.clientX;
		const startY = event.clientY;
		const parentWidth = videoPreviewEl.parentElement?.clientWidth ?? rect.width;
		const move = (moveEvent: PointerEvent) => {
			previewSize = {
				width: Math.max(320, Math.min(parentWidth, rect.width + moveEvent.clientX - startX)),
				height: Math.max(240, Math.min(900, rect.height + moveEvent.clientY - startY))
			};
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
	}
</script>

<svelte:window onpointerdown={handlePlaybackGesturePointer} />

<section class="panel preview-panel">
	<div
		class="video-preview"
		class:drag-active={dragActive}
		bind:this={videoPreviewEl}
		style={previewSize ? `width:min(100%, ${previewSize.width}px);height:${previewSize.height}px` : undefined}
		role="region"
		aria-label="视频预览与拖拽导入区域"
		ondragenter={handlePreviewDragEnter}
		ondragover={handlePreviewDragOver}
		ondragleave={handlePreviewDragLeave}
		ondrop={handlePreviewDrop}
	>
		{#if previewVideoSrc}
			{#key previewVideoSurfaceKey(previewVideoSrc, videoSurfaceElementRevision)}
				<!-- svelte-ignore a11y_media_has_caption -->
				<video
					class="preview-video"
					bind:this={previewVideoEl}
					controls
					preload="metadata"
					playsinline
					src={previewVideoSrc}
					onloadedmetadata={handleVideoLoadedMetadata}
					onprogress={refreshPlaybackReadiness}
					oncanplay={refreshPlaybackReadiness}
					onemptied={refreshPlaybackReadiness}
					onerror={handleSourceVideoError}
					onplay={handleVideoPlay}
					onplaying={handleVideoPlaying}
					onwaiting={handleVideoWaiting}
					onstalled={handleVideoWaiting}
					onpause={handleVideoPause}
					onseeking={handleVideoSeek}
					onseeked={handleVideoSeek}
					ontimeupdate={handleVideoTimeUpdate}
				></video>
			{/key}
			{#if sourceVideoFailed}
				<div class="media-error-overlay" role="status">
					<strong>视频暂时无法加载</strong>
					<span>项目和时间线数据仍然保留，可以重新连接媒体。</span>
					<button type="button" onclick={refreshMediaCache}>重新加载</button>
				</div>
			{/if}
			{#if hoverScrubbing && (hoverFrameSrc || hoverFramePendingSrc)}
				<div class="scrub-preview-frame" aria-hidden="true">
					<div class="scrub-preview-viewport" style={hoverFrameViewportStyle}>
						{#if hoverFrameSrc}<img class="scrub-preview-sprite" style={hoverFrameStyle} src={hoverFrameSrc} alt="" />{/if}
						{#if hoverFramePendingSrc}<img class="scrub-preview-preload" src={hoverFramePendingSrc} alt="" onload={(event) => activatePendingHoverFrame((event.currentTarget as HTMLImageElement).getAttribute('src') ?? '')} onerror={() => { mediaReconnectPending = true; hoverFramePendingSrc = ''; }} />{/if}
					</div>
				</div>
			{/if}
			{#if previewVideoProxyState.mode === 'segmented' && previewVideoProxyState.status !== 'ready'}
				<div class="proxy-progress-chip">
					当前范围可播放 · 后台兼容画面 {Math.round(previewVideoProxyState.progress * 100)}%
				</div>
			{/if}
			<div class="playback-mode-chip">{previewModeLabel}</div>
		{:else if mediaAssetAvailable(mediaHealth, 'source_video') && previewVideoProxyState.status !== 'failed'}
			<div class="video-empty-state" role="status" aria-live="polite">
				<div class="empty-copy">
					<CommandSpinner size={20} />
					<strong>正在准备当前位置的画面</strong>
					<span>已完成 {Math.round(previewVideoProxyState.progress * 100)}%，首个短片段完成后即可播放，其他部分会在后台继续。</span>
				</div>
			</div>
		{:else if mediaAssetAvailable(mediaHealth, 'source_video') && previewVideoProxyState.status === 'failed'}
			<div class="video-empty-state" role="status">
				<div class="empty-copy">
					<strong>兼容画面准备失败</strong>
					<span>{previewVideoProxyState.error || '源视频仍然保留，可以重新准备兼容画面。'}</span>
					<button type="button" onclick={() => previewVideoProxyController.retry()}>重新准备</button>
				</div>
			</div>
		{:else}
			<div class="video-empty-state">
				<div class="empty-copy">
					{#if sourceVideoConfigured(mediaHealth)}
						<strong>源视频文件当前不可用</strong>
						<span>项目编辑数据仍然保留。请{mediaRepairLabel(mediaHealth, 'source_video')}，恢复后预览会自动出现。</span>
					{:else}
						<strong>导入一个视频开始本土化配音</strong>
						<span>拖入或选择 MP4 / MOV / MKV，导入后会创建草稿并抽取原音轨。</span>
					{/if}
					<button type="button" aria-busy={importing} data-tooltip="导入视频：创建新的本土化项目并自动抽取原音轨。" onclick={onRequestImport} disabled={importing}>{#if importing}<CommandSpinner size={13} /> 导入中{:else}导入视频{/if}</button>
				</div>
			</div>
		{/if}
		{#if (playbackWanted || audioSessionStarted) && originalActive && originalAudioSrc}
			<audio bind:this={originalAudioEl} use:registerFixedAudio data-audio-group="video-localization-preview" preload="auto" src={originalAudioSrc} aria-label="原音轨预览" onprogress={handleAuxiliaryPlaybackReady} oncanplay={handleAuxiliaryPlaybackReady} onplaying={handleAuxiliaryPlaybackReady} onemptied={refreshPlaybackReadiness} onerror={handleAuxiliaryPlaybackError} onwaiting={handleAuxiliaryPlaybackStall} onstalled={handleAuxiliaryPlaybackStall}></audio>
		{/if}
		{#if (playbackWanted || audioSessionStarted) && vocalsActive && vocalsAudioSrc}
			<audio bind:this={vocalsAudioEl} use:registerFixedAudio data-audio-group="video-localization-preview" preload="auto" src={vocalsAudioSrc} aria-label="人声轨预览" onprogress={handleAuxiliaryPlaybackReady} oncanplay={handleAuxiliaryPlaybackReady} onplaying={handleAuxiliaryPlaybackReady} onemptied={refreshPlaybackReadiness} onerror={handleAuxiliaryPlaybackError} onwaiting={handleAuxiliaryPlaybackStall} onstalled={handleAuxiliaryPlaybackStall}></audio>
		{/if}
		{#if (playbackWanted || audioSessionStarted) && backgroundActive && backgroundAudioSrc}
			<audio bind:this={backgroundAudioEl} use:registerFixedAudio data-audio-group="video-localization-preview" preload="auto" src={backgroundAudioSrc} aria-label="背景音乐轨预览" onprogress={handleAuxiliaryPlaybackReady} oncanplay={handleAuxiliaryPlaybackReady} onplaying={handleAuxiliaryPlaybackReady} onemptied={refreshPlaybackReadiness} onerror={handleAuxiliaryPlaybackError} onwaiting={handleAuxiliaryPlaybackStall} onstalled={handleAuxiliaryPlaybackStall}></audio>
		{/if}
		{#each mountedDubTrackClips as clip (timelineClipKey(clip))}
			<audio
				class="dub-preload"
				data-audio-group="video-localization-preview"
				data-dub-clip={clip.clip_id}
				preload="auto"
				src={dubPlaybackAudioUrl(clip)}
				aria-label={`合成配音轨 ${dubLaneForClip(clip) + 1} 预览 ${clip.clip_id}`}
				use:registerDubAudio={timelineClipKey(clip)}
				onloadedmetadata={() => { syncAuxiliaryTracks(Boolean(previewVideoEl && !previewVideoEl.paused)); refreshPlaybackReadiness(); }}
				onprogress={handleAuxiliaryPlaybackReady}
				oncanplay={(event) => { handleAuxiliaryPlaybackReady(event); syncAuxiliaryTracks(Boolean(previewVideoEl && !previewVideoEl.paused)); }}
				onplaying={handleAuxiliaryPlaybackReady}
				onemptied={refreshPlaybackReadiness}
				onerror={handleAuxiliaryPlaybackError}
				onwaiting={handleAuxiliaryPlaybackStall}
				onstalled={handleAuxiliaryPlaybackStall}
			></audio>
		{/each}
		{#if subtitleFrame.lines.length}
			<div
				class="subtitle-overlay"
				class:middle={subtitleFrame.position === 'middle'}
			>
				{#each subtitleFrame.lines as line (line.key)}
					<p
						class={subtitleStyleClass(line.style)}
						class:placeholder={line.placeholder}
						style={subtitleLineStyle(line.style)}
					>{line.text}</p>
				{/each}
			</div>
		{/if}
		{#if dragActive}
			<div class="drop-overlay">
				<strong>松开导入视频</strong>
				<span>会创建一个新的本土化项目</span>
			</div>
		{/if}
		<button class="preview-resize-handle" type="button" aria-label="调整视频预览大小" data-tooltip="调整预览大小：拖动可分别改变视频预览宽度和高度。" onpointerdown={beginPreviewResize}></button>
	</div>
</section>

<style>
	.dub-preload { display: none; }
	.preview-panel {
		min-width: 0;
		border: 0;
		background: transparent;
		display: grid;
		justify-items: center;
	}

	.video-preview {
		position: relative;
		width: 100%;
		height: clamp(320px, 48vh, 630px);
		min-width: min(420px, 100%);
		min-height: 240px;
		max-width: 100%;
		border-radius: 7px;
		overflow: hidden;
		background:
			linear-gradient(130deg, rgba(79, 156, 249, 0.18), transparent 42%),
			linear-gradient(25deg, rgba(66, 196, 155, 0.14), transparent 34%),
			#0c0f13;
		border: 1px solid var(--line);
		margin-inline: auto;
	}

	.media-error-overlay {
		position: absolute;
		inset: 0;
		z-index: 6;
		display: grid;
		place-content: center;
		justify-items: center;
		gap: 8px;
		background: rgba(9, 13, 17, 0.78);
		color: #e8eef1;
		text-align: center;
	}

	.media-error-overlay span {
		color: rgba(220, 231, 235, 0.68);
		font-size: 12px;
	}

	.media-error-overlay button {
		margin-top: 4px;
	}

	.preview-resize-handle {
		position: absolute;
		right: 3px;
		bottom: 3px;
		z-index: 14;
		width: 25px;
		height: 25px;
		border: 0;
		border-radius: 4px;
		background:
			linear-gradient(135deg, transparent 0 47%, rgba(225, 235, 240, 0.68) 48% 54%, transparent 55%) 8px 8px / 12px 12px no-repeat,
			linear-gradient(135deg, transparent 0 47%, rgba(225, 235, 240, 0.68) 48% 54%, transparent 55%) 13px 13px / 8px 8px no-repeat,
			rgba(8, 12, 15, 0.34);
		cursor: nwse-resize;
		touch-action: none;
	}

	.preview-resize-handle:hover {
		background-color: rgba(87, 208, 200, 0.14);
	}

	.video-preview.drag-active {
		border-color: rgba(87, 208, 200, 0.85);
		box-shadow:
			inset 0 0 0 1px rgba(87, 208, 200, 0.42),
			0 0 0 1px rgba(87, 208, 200, 0.16);
	}

	.video-empty-state {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		text-align: center;
		background:
			linear-gradient(180deg, rgba(9, 12, 15, 0.22), rgba(9, 12, 15, 0.66)),
			url('/images/video-localization/empty-preview-bg-2k.png') center / cover no-repeat,
			#101315;
	}

	.empty-copy {
		position: relative;
		z-index: 1;
		display: grid;
		gap: 10px;
		justify-items: center;
		width: min(760px, calc(100% - 48px));
		padding: 18px 24px;
	}

	.empty-copy strong {
		font-size: 18px;
	}

	.empty-copy span {
		color: var(--muted);
		font-size: 13px;
		line-height: 1.5;
		white-space: nowrap;
	}

	.empty-copy button {
		border: 1px solid #78ddd5;
		border-radius: 7px;
		background: #58d1c8;
		color: #0d1112;
		min-height: 28px;
		padding: 3px 11px;
		font-size: 12px;
		line-height: 1.2;
		font-weight: 800;
		cursor: pointer;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
	}

	.empty-copy button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.drop-overlay {
		position: absolute;
		inset: 12px;
		z-index: 12;
		display: grid;
		place-content: center;
		gap: 8px;
		text-align: center;
		border: 1px dashed rgba(126, 232, 223, 0.85);
		border-radius: 9px;
		background: rgba(8, 14, 17, 0.72);
		backdrop-filter: blur(3px);
		pointer-events: none;
	}

	.drop-overlay strong {
		color: #e4fffc;
		font-size: 18px;
	}

	.drop-overlay span {
		color: #9bc6c2;
		font-size: 12px;
	}

	.subtitle-overlay {
		position: absolute;
		z-index: 6;
		left: 18px;
		right: 18px;
		bottom: 52px;
		text-align: center;
		pointer-events: none;
		line-height: 1.32;
	}

	.subtitle-overlay.middle {
		bottom: auto;
		top: 48%;
		transform: translateY(-50%);
	}

	.subtitle-overlay p.subtitle-line {
		display: block;
		width: fit-content;
		margin: 3px auto;
		padding: 1px 7px;
		border-radius: 5px;
		background: rgba(0, 0, 0, var(--subtitle-bg));
		font-weight: 700;
		min-height: 1.32em;
		max-width: min(92%, 980px);
		transform: translate(var(--subtitle-offset-x), var(--subtitle-offset-y));
	}

	.subtitle-overlay p.placeholder {
		visibility: hidden;
	}

	.subtitle-overlay .yellow-outline {
		color: #fff1a8;
		text-shadow: 0 2px 2px #000, 0 0 4px #000, 1px 1px 0 #000, -1px -1px 0 #000;
	}

	.subtitle-overlay .boxed {
		color: #fff;
		text-shadow: 0 1px 2px rgba(0, 0, 0, 0.85);
	}

	.subtitle-overlay p.boxed {
		background: rgba(0, 0, 0, max(var(--subtitle-bg), 0.48));
	}

	.subtitle-overlay .clean-shadow {
		color: white;
		text-shadow: 0 2px 7px rgba(0, 0, 0, 0.72);
	}

	.subtitle-overlay .strong-outline {
		color: white;
		text-shadow: 1px 1px #000, -1px -1px #000, 1px -1px #000, -1px 1px #000, 0 2px 5px #000;
	}

	.subtitle-overlay .warm-outline {
		color: #fff4dc;
		text-shadow: 0 1px 2px #251b14, 1px 1px 0 rgba(37, 27, 20, 0.92), -1px -1px 0 rgba(37, 27, 20, 0.92);
	}

	.subtitle-overlay .cyan-outline {
		color: #ddfbff;
		text-shadow: 0 2px 4px #042f35, 1px 1px 0 #063f46, -1px -1px 0 #063f46;
	}

	.subtitle-overlay .caption-bar {
		color: #fff;
		text-shadow: none;
	}

	.subtitle-overlay p.caption-bar {
		padding: 3px 11px;
		border-radius: 2px;
		background: rgba(4, 6, 8, max(var(--subtitle-bg), 0.68));
	}

	.subtitle-overlay .soft-panel {
		color: #f8fbfc;
		text-shadow: 0 1px 4px rgba(0, 0, 0, 0.58);
	}

	.subtitle-overlay p.soft-panel {
		padding: 3px 10px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		background: rgba(20, 28, 33, max(var(--subtitle-bg), 0.46));
		box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22);
	}

	.preview-video {
		width: 100%;
		height: 100%;
		object-fit: contain;
		background: #050608;
	}

	.scrub-preview-frame {
		position: absolute;
		inset: 0;
		z-index: 4;
		overflow: hidden;
		background: #050608;
		opacity: 1;
		pointer-events: none;
	}

	.scrub-preview-viewport {
		position: absolute;
		overflow: hidden;
	}

	.scrub-preview-sprite {
		position: absolute;
		max-width: none;
		pointer-events: none;
	}

	.scrub-preview-preload { display: none; }

	.playback-mode-chip {
		position: absolute;
		top: 12px;
		right: 12px;
		max-width: min(360px, calc(100% - 24px));
		border: 1px solid rgba(255, 255, 255, 0.2);
		border-radius: 999px;
		padding: 5px 10px;
		background: rgba(5, 8, 10, 0.68);
		color: rgba(255, 255, 255, 0.86);
		font-size: 11px;
		font-weight: 760;
		line-height: 1.2;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		backdrop-filter: blur(10px);
	}

	.proxy-progress-chip {
		position: absolute;
		top: 12px;
		left: 12px;
		max-width: min(420px, calc(100% - 24px));
		border: 1px solid rgba(93, 220, 209, 0.28);
		border-radius: 999px;
		padding: 5px 10px;
		background: rgba(5, 20, 21, 0.72);
		color: #b9f5ef;
		font-size: 11px;
		font-weight: 720;
		line-height: 1.2;
		backdrop-filter: blur(10px);
	}

</style>
