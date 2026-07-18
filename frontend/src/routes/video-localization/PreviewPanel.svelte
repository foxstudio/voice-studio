<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationSubtitleCue } from '$lib/api/types';
	import { sourceAudioUrl, sourceVideoUrl, stemAudioUrl, timelineClipAudioUrl } from './utils';
	import { defaultSubtitlePreviewState, defaultTrackStates, TRACK_LABELS, type SubtitlePreviewState, type VideoLocalizationTrackId, type VideoLocalizationTrackStates } from './studio-state';
	import { activeTimelineClips, clipSourceTimeSeconds, shouldCorrectAudioDrift, timelineClipKey, upcomingTimelineClips } from './preview-playback';

	type PlaybackController = {
		playPause: () => void;
		play: () => void;
		seek: (timeMs: number) => void;
		scrub: (timeMs: number) => void;
		endScrub: () => void;
	};

	let {
		asrCue,
		localizedSubtitle,
		draft,
		projectId,
		importing = false,
		subtitlePreview = defaultSubtitlePreviewState(),
		trackStates = defaultTrackStates(),
		playbackLoopRange = null,
		onRequestImport = () => {},
		onImportFile = () => {},
		onVideoTimeUpdate = () => {},
		onPlaybackStateChange = () => {},
		onControllerReady = () => {}
	}: {
		asrCue: VideoLocalizationCue | null;
		localizedSubtitle: VideoLocalizationSubtitleCue | null;
		draft: VideoLocalizationDraft | null;
		projectId: string;
		importing?: boolean;
		subtitlePreview?: SubtitlePreviewState;
		trackStates?: VideoLocalizationTrackStates;
		playbackLoopRange?: { start_ms: number; end_ms: number } | null;
		onRequestImport?: () => void;
		onImportFile?: (file: File) => void;
		onVideoTimeUpdate?: (timeMs: number) => void;
		onPlaybackStateChange?: (playing: boolean) => void;
		onControllerReady?: (controller: PlaybackController | null) => void;
	} = $props();

	let sourceVideoFailed = $state(false);
	let videoPreviewEl = $state<HTMLDivElement | null>(null);
	let previewSize = $state<{ width: number; height: number } | null>(null);
	let previewVideoEl = $state<HTMLVideoElement | null>(null);
	let originalAudioEl = $state<HTMLAudioElement | null>(null);
	let vocalsAudioEl = $state<HTMLAudioElement | null>(null);
	let backgroundAudioEl = $state<HTMLAudioElement | null>(null);
	let playbackPositionMs = $state(0);
	let dragDepth = $state(0);
	let playbackFrame = 0;
	let auxiliaryPlaybackFrame = 0;
	let hoverScrubTimer: ReturnType<typeof setTimeout> | null = null;
	let hoverScrubbing = false;
	let hoverScrubRestoreTime = 0;
	let mixAudioContext: AudioContext | null = null;
	let mixAudioResumePromise: Promise<void> | null = null;
	let lastAudioMaintenanceAt = 0;
	let lastPlaybackUiUpdateAt = 0;
	let previewProxyRevision = $state(0);
	let previewProxyKey = '';
	let preparedProxyRevision = 0;
	let mediaResetKey = '';
	let pendingVideoRestore: { time: number; playing: boolean } | null = null;
	let playbackIntentRevision = 0;
	let pendingVideoPlayRevision = 0;
	let playbackWanted = false;
	const PLAYBACK_UI_INTERVAL_MS = 50;
	// Media elements normally share the same clock after an aligned start. Check
	// often enough to recover from decoder stalls without repeatedly seeking them.
	const AUDIO_MAINTENANCE_INTERVAL_MS = 160;
	const trackGainNodes = new Map<HTMLAudioElement, GainNode>();
	const pendingAudioPlayRequests = new WeakSet<HTMLAudioElement>();
	const pendingAudioReadyRecovery = new WeakSet<HTMLAudioElement>();
	const desiredAudioTimes = new WeakMap<HTMLAudioElement, number>();
	const dubAudioElements = new Map<string, HTMLAudioElement>();

	const previewVideoSrc = $derived(sourceVideoUrl(projectId, draft, previewProxyRevision));
	const originalAudioSrc = $derived(sourceAudioUrl(projectId, draft));
	const vocalsAudioSrc = $derived(stemAudioUrl(projectId, draft, 'vocals'));
	const backgroundAudioSrc = $derived(stemAudioUrl(projectId, draft, 'background'));
	const hasDubMedia = $derived(Boolean(draft?.timeline_clips.some((clip) => clip.track_id === 'dub' && clip.audio_path)));
	const soloTracks = $derived(([
		['original', trackMediaAvailable('original', originalAudioSrc)],
		['vocals', trackMediaAvailable('vocals', vocalsAudioSrc)],
		['background', trackMediaAvailable('background', backgroundAudioSrc)],
		['dub', hasDubMedia]
	] as const).filter(([trackId, hasMedia]) => hasMedia && trackStates[trackId].solo).map(([trackId]) => trackId));
	const hasSoloTrack = $derived(soloTracks.length > 0);
	const originalActive = $derived(trackAudible('original', trackMediaAvailable('original', originalAudioSrc)));
	const vocalsActive = $derived(trackAudible('vocals', trackMediaAvailable('vocals', vocalsAudioSrc)));
	const backgroundActive = $derived(trackAudible('background', trackMediaAvailable('background', backgroundAudioSrc)));
	const dubActive = $derived(trackAudible('dub', hasDubMedia));
	const previewModeLabel = $derived(playbackModeLabel());
	const subtitleLines = $derived(buildSubtitleLines(asrCue, localizedSubtitle, subtitlePreview.source));
	const subtitleStyle = $derived(
		`font-size:${subtitlePreview.fontSize}px;--subtitle-bg:${subtitlePreview.backgroundOpacity};`
	);
	const dubTrackClips = $derived((draft?.timeline_clips ?? []).filter((clip) => clip.track_id === 'dub' && Boolean(clip.audio_path)));
	const dubPreloadKeys = $derived(new Set(upcomingTimelineClips(dubTrackClips, 'dub', playbackPositionMs).map(timelineClipKey)));
	const dragActive = $derived(dragDepth > 0);

	$effect(() => {
		const nextMediaResetKey = [
			projectId,
			draft?.source_media.video_path ?? '',
			draft?.stems.vocals_clean_path ?? '',
			draft?.stems.background_path ?? '',
			draft?.timeline_clips.length ?? 0
		].join(':');
		if (nextMediaResetKey === mediaResetKey) return;
		mediaResetKey = nextMediaResetKey;
		sourceVideoFailed = false;
		playbackPositionMs = 0;
		playbackIntentRevision += 1;
		pendingVideoPlayRevision = 0;
		playbackWanted = false;
		onPlaybackStateChange(false);
	});

	$effect(() => {
		applyTrackMix();
	});

	onMount(() => {
		onControllerReady({ playPause: playPauseFromGesture, play: playFromGesture, seek: seekPreview, scrub: scrubPreview, endScrub: endScrubPreview });
		document.addEventListener('visibilitychange', handleVisibilityChange);
		return () => {
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			onControllerReady(null);
		};
	});

	$effect(() => {
		const sourcePath = draft?.source_media.video_path;
		const key = projectId && sourcePath ? `${projectId}:${sourcePath}` : '';
		if (!key || key === previewProxyKey) return;
		previewProxyKey = key;
		previewProxyRevision = 0;
		void prepareEditingProxy(projectId, key);
	});

	onDestroy(() => {
		stopPlaybackClock();
		cancelAuxiliaryPlaybackStart();
		if (hoverScrubTimer) clearTimeout(hoverScrubTimer);
		for (const gain of trackGainNodes.values()) gain.disconnect();
		trackGainNodes.clear();
		dubAudioElements.clear();
		if (mixAudioContext) {
			mixAudioContext.onstatechange = null;
			void mixAudioContext.close();
		}
		mixAudioContext = null;
		mixAudioResumePromise = null;
	});

	function buildSubtitleLines(asrCueValue: VideoLocalizationCue | null, localizedCueValue: VideoLocalizationSubtitleCue | null, source: SubtitlePreviewState['source']) {
		if (!subtitlePreview.enabled) return [];
		const asr = asrCueValue?.en_subtitle_text?.trim() ?? '';
		const localized = localizedCueValue?.text.trim() ?? '';
		if (subtitlePreview.sources) {
			const lines: string[] = [];
			if (subtitlePreview.sources.localized && localized) lines.push(localized);
			if (subtitlePreview.sources.asr && asr) lines.push(asr);
			return lines;
		}
		if (source === 'asr') return asr ? [asr] : [];
		if (source === 'localized') return localized ? [localized] : asr ? [asr] : [];
		return [];
	}

	function trackAudible(trackId: VideoLocalizationTrackId, hasMedia: boolean) {
		const state = trackStates[trackId];
		if (!state || !hasMedia || state.muted || state.volume <= 0) return false;
		return hasSoloTrack ? state.solo : true;
	}

	function trackMediaAvailable(trackId: VideoLocalizationTrackId, fallbackSrc: string) {
		const disabledTracks = Array.isArray(draft?.ui_state?.disabled_media_tracks)
			? draft.ui_state.disabled_media_tracks.map(String)
			: [];
		if (disabledTracks.includes(trackId)) return false;
		return hasTimelineMedia(trackId) || Boolean(fallbackSrc);
	}

	function playbackModeLabel() {
		const labels: string[] = [];
		if (originalActive) labels.push(TRACK_LABELS.original);
		if (vocalsActive) labels.push(TRACK_LABELS.vocals);
		if (backgroundActive) labels.push(TRACK_LABELS.background);
		if (dubActive) labels.push(TRACK_LABELS.dub);
		if (!labels.length) return '静音预览';
		return labels.join(' + ');
	}

	function applyTrackMix() {
		if (previewVideoEl) {
			previewVideoEl.muted = Boolean(originalAudioSrc || vocalsAudioSrc || backgroundAudioSrc || hasDubMedia);
		}
		applyAudioTrack(originalAudioEl, originalActive, trackStates.original.volume);
		applyAudioTrack(vocalsAudioEl, vocalsActive, trackStates.vocals.volume);
		applyAudioTrack(backgroundAudioEl, backgroundActive, trackStates.background.volume);
		for (const audio of dubAudioElements.values()) applyAudioTrack(audio, dubActive, trackStates.dub.volume);
		if (previewVideoEl && !previewVideoEl.paused) syncAuxiliaryTracks(true);
	}

	function applyAudioTrack(audio: HTMLAudioElement | null, active: boolean, volume: number) {
		if (!audio) return;
		const gainNode = trackGainNodes.get(audio);
		audio.muted = false;
		if (gainNode) {
			audio.volume = 1;
			gainNode.gain.value = active ? clampGain(volume) : 0;
		} else {
			audio.volume = active ? Math.min(1, clampGain(volume)) : 0;
		}
		if (!active) {
			audio.pause();
			return;
		}
	}

	function ensureTrackGain(audio: HTMLAudioElement) {
		const existing = trackGainNodes.get(audio);
		if (existing) return existing;
		if (!mixAudioContext || mixAudioContext.state === 'closed') return null;
		try {
			const source = mixAudioContext.createMediaElementSource(audio);
			const gain = mixAudioContext.createGain();
			source.connect(gain).connect(mixAudioContext.destination);
			trackGainNodes.set(audio, gain);
			return gain;
		} catch {
			return null;
		}
	}

	function prepareAudioOutputFromGesture() {
		if (!mixAudioContext && !audioMixerRequired()) {
			applyTrackMix();
			return;
		}
		if (!mixAudioContext || mixAudioContext.state === 'closed') {
			try {
				mixAudioContext = new AudioContext();
				mixAudioContext.onstatechange = handleAudioContextStateChange;
			} catch {
				mixAudioContext = null;
			}
		}
		if (mixAudioContext) {
			for (const audio of [originalAudioEl, vocalsAudioEl, backgroundAudioEl, ...dubAudioElements.values()]) {
				if (audio) ensureTrackGain(audio);
			}
			applyTrackMix();
		}
		void resumeAudioOutput();
	}

	function audioMixerRequired() {
		return (
			(originalActive && trackStates.original.volume > 1) ||
			(vocalsActive && trackStates.vocals.volume > 1) ||
			(backgroundActive && trackStates.background.volume > 1) ||
			(dubActive && trackStates.dub.volume > 1)
		);
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
		if (mixAudioContext?.state === 'suspended' && previewVideoEl && !previewVideoEl.paused) {
			void resumeAudioOutput(true);
		}
	}

	function handleVisibilityChange() {
		if (document.visibilityState !== 'visible' || !previewVideoEl) return;
		if (previewVideoEl.paused || previewVideoEl.ended) {
			onPlaybackStateChange(false);
			stopPlaybackClock();
			pauseAuxiliaryTracks();
			return;
		}
		void resumeAudioOutput(true);
	}

	function hasTimelineMedia(trackId: VideoLocalizationTrackId) {
		return Boolean(draft?.timeline_clips.some((clip) => clip.track_id === trackId && clip.audio_path));
	}

	function syncAudioTrack(audio: HTMLAudioElement | null, currentTime: number, playIfNeeded = false) {
		if (!audio) return;
		desiredAudioTimes.set(audio, currentTime);
		if (audio.readyState < HTMLMediaElement.HAVE_METADATA) {
			if (playIfNeeded) {
				startAudioWhenReady(audio);
				playAudioTrack(audio);
			}
			return;
		}
		if (Number.isFinite(audio.duration) && currentTime > audio.duration) {
			audio.pause();
			return;
		}
		if (shouldCorrectAudioDrift(audio.currentTime, currentTime)) {
			try {
				audio.currentTime = currentTime;
			} catch {
				startAudioWhenReady(audio);
			}
		}
		if (playIfNeeded && (audio.paused || audio.ended)) {
			playAudioTrack(audio);
		}
	}

	function playAudioTrack(audio: HTMLAudioElement) {
		if (audio.readyState < HTMLMediaElement.HAVE_FUTURE_DATA) startAudioWhenReady(audio);
		if (pendingAudioPlayRequests.has(audio)) return;
		pendingAudioPlayRequests.add(audio);
		void audio.play().then(
			() => delete audio.dataset.playbackError,
			(error: unknown) => {
				if (error instanceof DOMException && error.name === 'AbortError') return;
				audio.dataset.playbackError = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
			}
		).finally(() => pendingAudioPlayRequests.delete(audio));
	}

	function startAudioWhenReady(audio: HTMLAudioElement) {
		if (pendingAudioReadyRecovery.has(audio)) return;
		pendingAudioReadyRecovery.add(audio);
		const start = () => {
			audio.removeEventListener('loadedmetadata', start);
			audio.removeEventListener('canplay', start);
			pendingAudioReadyRecovery.delete(audio);
			if (!playbackWanted || !previewVideoEl || previewVideoEl.paused) return;
			syncAuxiliaryTracks(true);
		};
		audio.addEventListener('loadedmetadata', start, { once: true });
		audio.addEventListener('canplay', start, { once: true });
		if (audio.networkState === HTMLMediaElement.NETWORK_EMPTY) audio.load();
	}

	function syncAuxiliaryTracks(playIfNeeded = false) {
		const time = previewVideoEl?.currentTime ?? 0;
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
		const trackClips = draft?.timeline_clips.filter((item) => item.track_id === trackId && item.audio_path) ?? [];
		if (!trackClips.length) {
			syncAudioTrack(audio, currentTime, playIfNeeded);
			return;
		}
		const timeMs = Math.round(currentTime * 1000);
		const clip = trackClips.find((item) => {
			const start = item.start_ms ?? 0;
			const end = item.end_ms ?? start + 1800;
			return timeMs >= start && timeMs < end;
		});
		if (!clip) {
			audio?.pause();
			return;
		}
		const sourceStartSeconds = Math.max(0, (clip.source_start_ms ?? 0) / 1000);
		const localTime = sourceStartSeconds + Math.max(0, currentTime - (clip.start_ms ?? 0) / 1000);
		syncAudioTrack(audio, localTime, playIfNeeded);
	}

	function syncDubTrack(currentTime: number, playIfNeeded = false) {
		const timeMs = Math.round(currentTime * 1000);
		const active = activeTimelineClips(dubTrackClips, 'dub', timeMs);
		const activeKeys = new Set(active.map(timelineClipKey));
		for (const [key, audio] of dubAudioElements) {
			if (!activeKeys.has(key)) audio.pause();
		}
		for (const clip of active) {
			const audio = dubAudioElements.get(timelineClipKey(clip));
			syncAudioTrack(audio ?? null, clipSourceTimeSeconds(clip, currentTime), playIfNeeded);
		}
	}

	function pauseDubTracks() {
		for (const audio of dubAudioElements.values()) audio.pause();
	}

	function registerDubAudio(audio: HTMLAudioElement, key: string) {
		dubAudioElements.set(key, audio);
		if (mixAudioContext) ensureTrackGain(audio);
		applyAudioTrack(audio, dubActive, trackStates.dub.volume);
		if (dubPreloadKeys.has(key) && audio.networkState === HTMLMediaElement.NETWORK_EMPTY) audio.load();
		return {
			destroy() {
				audio.pause();
				dubAudioElements.delete(key);
				const gain = trackGainNodes.get(audio);
				gain?.disconnect();
				trackGainNodes.delete(audio);
			}
		};
	}

	function seekPreview(timeMs: number) {
		if (!previewVideoEl) return;
		hoverScrubbing = false;
		const nextTime = Math.max(0, timeMs / 1000);
		playbackPositionMs = Math.round(nextTime * 1000);
		const fastSeek = (previewVideoEl as HTMLVideoElement & { fastSeek?: (time: number) => void }).fastSeek;
		if (typeof fastSeek === 'function' && Math.abs(previewVideoEl.currentTime - nextTime) > 0.35) fastSeek.call(previewVideoEl, nextTime);
		else previewVideoEl.currentTime = nextTime;
		onVideoTimeUpdate(Math.round(previewVideoEl.currentTime * 1000));
		syncAuxiliaryTracks(!previewVideoEl.paused);
	}

	function scrubPreview(timeMs: number) {
		if (!previewVideoEl || !previewVideoEl.paused) return;
		if (!hoverScrubbing) hoverScrubRestoreTime = previewVideoEl.currentTime;
		hoverScrubbing = true;
		const nextTime = Math.max(0, timeMs / 1000);
		const fastSeek = (previewVideoEl as HTMLVideoElement & { fastSeek?: (time: number) => void }).fastSeek;
		if (typeof fastSeek === 'function' && Math.abs(previewVideoEl.currentTime - nextTime) > 0.35) fastSeek.call(previewVideoEl, nextTime);
		else previewVideoEl.currentTime = nextTime;
		void resumeAudioOutput();
		syncAuxiliaryTracks(true);
		if (hoverScrubTimer) clearTimeout(hoverScrubTimer);
		hoverScrubTimer = setTimeout(() => {
			hoverScrubTimer = null;
			if (previewVideoEl?.paused) pauseAuxiliaryTracks();
		}, 150);
	}

	function endScrubPreview() {
		if (hoverScrubTimer) clearTimeout(hoverScrubTimer);
		hoverScrubTimer = null;
		if (!hoverScrubbing || !previewVideoEl) return;
		hoverScrubbing = false;
		previewVideoEl.currentTime = hoverScrubRestoreTime;
		pauseAuxiliaryTracks();
		syncAuxiliaryTracks(false);
	}

	function pauseAuxiliaryTracks() {
		originalAudioEl?.pause();
		vocalsAudioEl?.pause();
		backgroundAudioEl?.pause();
		pauseDubTracks();
	}

	function playPauseFromGesture() {
		if (!previewVideoEl) return;
		if (!previewVideoEl.paused || playbackWanted) {
			playbackIntentRevision += 1;
			playbackWanted = false;
			previewVideoEl.pause();
			pauseAuxiliaryTracks();
			onPlaybackStateChange(false);
			return;
		}
		playFromGesture();
	}

	function playFromGesture() {
		if (!previewVideoEl || (!previewVideoEl.paused && playbackWanted)) return;
		if (hoverScrubbing) endScrubPreview();
		const intentRevision = ++playbackIntentRevision;
		pendingVideoPlayRevision = intentRevision;
		playbackWanted = true;
		onPlaybackStateChange(true);
		prepareAudioOutputFromGesture();
		// Issue every active media play request while the user gesture is still
		// active. Browsers can otherwise reject a delayed canplay callback.
		syncAuxiliaryTracks(true);
		const videoPlayRequest = previewVideoEl.play();
		void videoPlayRequest
			.then(() => {
				if (intentRevision !== playbackIntentRevision || !playbackWanted) previewVideoEl?.pause();
			})
			.catch((error: unknown) => {
				if (intentRevision !== playbackIntentRevision || (error instanceof DOMException && error.name === 'AbortError')) return;
				playbackWanted = false;
				onPlaybackStateChange(false);
				if (previewVideoEl) previewVideoEl.dataset.playbackError = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
			})
			.finally(() => {
				if (pendingVideoPlayRevision === intentRevision) pendingVideoPlayRevision = 0;
			});
	}

	async function prepareEditingProxy(targetProjectId: string, key: string) {
		try {
			const beforeVersion = await fetchMediaVersion(`/api/projects/${targetProjectId}/video-localization/source-media/preview-video`);
			const response = await fetch(`/api/projects/${targetProjectId}/video-localization/source-media/preview-video`, {
				method: 'POST'
			});
			if (!response.ok || previewProxyKey !== key) return;
			const payload = (await response.json()) as { profile?: string };
			if (payload.profile !== '720p-h264-v1') return;
			const afterVersion = await fetchMediaVersion(`/api/projects/${targetProjectId}/video-localization/source-media/preview-video`);
			if (beforeVersion && afterVersion && beforeVersion === afterVersion) return;
			preparedProxyRevision = Date.now();
			activatePreparedProxyIfIdle();
		} catch {
			// The original source remains available when proxy preparation fails.
		}
	}

	async function fetchMediaVersion(url: string) {
		try {
			const response = await fetch(url, { method: 'HEAD', cache: 'no-store' });
			if (!response.ok) return '';
			return [response.headers.get('etag'), response.headers.get('last-modified'), response.headers.get('content-length')].join(':');
		} catch {
			return '';
		}
	}

	function activatePreparedProxyIfIdle() {
		if (!preparedProxyRevision || (previewVideoEl && !previewVideoEl.paused)) return;
		pendingVideoRestore = previewVideoEl ? { time: previewVideoEl.currentTime, playing: false } : null;
		previewProxyRevision = preparedProxyRevision;
		preparedProxyRevision = 0;
	}

	function handleVideoLoadedMetadata() {
		const restore = pendingVideoRestore;
		if (!restore || !previewVideoEl) return;
		pendingVideoRestore = null;
		previewVideoEl.currentTime = Math.min(restore.time, previewVideoEl.duration || restore.time);
		if (restore.playing) void previewVideoEl.play().catch(() => undefined);
	}

	function handleVideoPlay() {
		if (pendingVideoPlayRevision && pendingVideoPlayRevision !== playbackIntentRevision) {
			previewVideoEl?.pause();
			return;
		}
		if (!playbackWanted) {
			playbackIntentRevision += 1;
			playbackWanted = true;
		}
		void resumeAudioOutput();
		onPlaybackStateChange(true);
		startPlaybackClock();
		scheduleAuxiliaryPlaybackStart();
	}

	function handlePlaybackGesturePointer(event: PointerEvent) {
		const target = event.target as HTMLElement | null;
		if (target === previewVideoEl) prepareAudioOutputFromGesture();
	}

	function handleVideoPause() {
		if (!pendingVideoPlayRevision) playbackWanted = false;
		onPlaybackStateChange(false);
		stopPlaybackClock();
		cancelAuxiliaryPlaybackStart();
		pauseAuxiliaryTracks();
		activatePreparedProxyIfIdle();
	}

	function handleVideoSeek(event: Event) {
		if (hoverScrubbing) return;
		const timeMs = Math.round((event.currentTarget as HTMLVideoElement).currentTime * 1000);
		playbackPositionMs = timeMs;
		onVideoTimeUpdate(timeMs);
		syncAuxiliaryTracks(!previewVideoEl?.paused);
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
			if (now - lastPlaybackUiUpdateAt >= PLAYBACK_UI_INTERVAL_MS) {
				lastPlaybackUiUpdateAt = now;
				playbackPositionMs = Math.round(previewVideoEl.currentTime * 1000);
				onVideoTimeUpdate(playbackPositionMs);
			}
			if (now - lastAudioMaintenanceAt >= AUDIO_MAINTENANCE_INTERVAL_MS) {
				lastAudioMaintenanceAt = now;
				if (mixAudioContext?.state === 'suspended') void resumeAudioOutput(true);
				syncAuxiliaryTracks(true);
			}
			playbackFrame = requestAnimationFrame(tick);
		};
		playbackFrame = requestAnimationFrame(tick);
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
		if (!previewVideoEl || previewVideoEl.paused || audio.dataset.stallRecoveryPending === 'true') return;
		audio.dataset.stallRecoveryPending = 'true';
		void resumeAudioOutput().finally(() => {
			setTimeout(() => {
				delete audio.dataset.stallRecoveryPending;
				if (previewVideoEl && !previewVideoEl.paused) syncAuxiliaryTracks(true);
			}, 120);
		});
	}

	function handlePreviewDragEnter(event: DragEvent) {
		event.preventDefault();
		if (!hasDraggedFiles(event)) return;
		dragDepth += 1;
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
	}

	function handlePreviewDragOver(event: DragEvent) {
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
	}

	function handlePreviewDragLeave(event: DragEvent) {
		event.preventDefault();
		dragDepth = Math.max(0, dragDepth - 1);
	}

	function handlePreviewDrop(event: DragEvent) {
		event.preventDefault();
		dragDepth = 0;
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
		{#if previewVideoSrc && !sourceVideoFailed}
			<!-- svelte-ignore a11y_media_has_caption -->
			<video
				class="preview-video"
				bind:this={previewVideoEl}
				controls
				preload="auto"
				playsinline
				src={previewVideoSrc}
				onloadedmetadata={handleVideoLoadedMetadata}
				onerror={() => (sourceVideoFailed = true)}
				onplay={handleVideoPlay}
				onpause={handleVideoPause}
				onseeking={handleVideoSeek}
				onseeked={handleVideoSeek}
				ontimeupdate={handleVideoTimeUpdate}
			></video>
			{#if originalAudioSrc}
				<audio bind:this={originalAudioEl} data-audio-group="video-localization-preview" preload="auto" src={originalAudioSrc} aria-label="原音轨预览" onwaiting={handleAuxiliaryPlaybackStall} onstalled={handleAuxiliaryPlaybackStall}></audio>
			{/if}
			{#if vocalsAudioSrc}
				<audio bind:this={vocalsAudioEl} data-audio-group="video-localization-preview" preload="auto" src={vocalsAudioSrc} aria-label="人声轨预览" onwaiting={handleAuxiliaryPlaybackStall} onstalled={handleAuxiliaryPlaybackStall}></audio>
			{/if}
			{#if backgroundAudioSrc}
				<audio bind:this={backgroundAudioEl} data-audio-group="video-localization-preview" preload="auto" src={backgroundAudioSrc} aria-label="背景音乐轨预览" onwaiting={handleAuxiliaryPlaybackStall} onstalled={handleAuxiliaryPlaybackStall}></audio>
			{/if}
			{#each dubTrackClips as clip (timelineClipKey(clip))}
				<audio
					class="dub-preload"
					data-audio-group="video-localization-preview"
					data-dub-clip={clip.clip_id}
					preload={dubPreloadKeys.has(timelineClipKey(clip)) ? 'auto' : 'metadata'}
					src={timelineClipAudioUrl(projectId, clip)}
					aria-label={`合成配音轨预览 ${clip.clip_id}`}
					use:registerDubAudio={timelineClipKey(clip)}
					onloadedmetadata={() => syncAuxiliaryTracks(Boolean(previewVideoEl && !previewVideoEl.paused))}
					oncanplay={() => syncAuxiliaryTracks(Boolean(previewVideoEl && !previewVideoEl.paused))}
					onwaiting={handleAuxiliaryPlaybackStall}
					onstalled={handleAuxiliaryPlaybackStall}
				></audio>
			{/each}
			<div class="playback-mode-chip">{previewModeLabel}</div>
		{:else}
			<div class="video-empty-state">
				<div class="empty-copy">
					<strong>导入一个视频开始本土化配音</strong>
					<span>{sourceVideoFailed ? '视频加载失败，可以重新导入。' : '拖入或选择 MP4 / MOV / MKV，导入后会创建草稿并抽取原音轨。'}</span>
					<button type="button" data-tooltip="导入视频：创建新的本土化项目并自动抽取原音轨。" onclick={onRequestImport} disabled={importing}>{importing ? '导入中' : '导入视频'}</button>
				</div>
			</div>
		{/if}
		{#if subtitleLines.length}
			<div
				class="subtitle-overlay"
				class:middle={subtitlePreview.position === 'middle'}
				class:yellow-outline={subtitlePreview.stylePreset === 'yellow-outline'}
				class:boxed={subtitlePreview.stylePreset === 'boxed'}
				class:clean-shadow={subtitlePreview.stylePreset === 'clean-shadow'}
				class:strong-outline={subtitlePreview.stylePreset === 'strong-outline'}
				style={subtitleStyle}
			>
				{#each subtitleLines as line, index}
					<p class:secondary={index > 0}>{line}</p>
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

	.subtitle-overlay p {
		display: inline-block;
		margin: 3px 0;
		padding: 1px 7px;
		border-radius: 5px;
		background: rgba(0, 0, 0, var(--subtitle-bg));
		font-size: inherit;
		font-weight: 700;
		max-width: min(92%, 980px);
	}

	.subtitle-overlay p.secondary {
		display: block;
		color: rgba(255, 255, 255, 0.82);
		font-size: 0.72em;
		font-weight: 650;
	}

	.subtitle-overlay.yellow-outline {
		color: #fff1a8;
		text-shadow: 0 2px 2px #000, 0 0 4px #000, 1px 1px 0 #000, -1px -1px 0 #000;
	}

	.subtitle-overlay.boxed {
		color: #fff;
		text-shadow: 0 1px 2px rgba(0, 0, 0, 0.85);
	}

	.subtitle-overlay.boxed p {
		background: rgba(0, 0, 0, max(var(--subtitle-bg), 0.48));
	}

	.subtitle-overlay.clean-shadow {
		color: white;
		text-shadow: 0 2px 7px rgba(0, 0, 0, 0.72);
	}

	.subtitle-overlay.strong-outline {
		color: white;
		text-shadow: 1px 1px #000, -1px -1px #000, 1px -1px #000, -1px 1px #000, 0 2px 5px #000;
	}

	.preview-video {
		width: 100%;
		height: 100%;
		object-fit: contain;
		background: #050608;
	}

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
</style>
