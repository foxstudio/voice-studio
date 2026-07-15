<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationSubtitleCue } from '$lib/api/types';
	import { sourceAudioUrl, sourceVideoUrl, stemAudioUrl, timelineClipAudioUrl } from './utils';
	import { defaultSubtitlePreviewState, defaultTrackStates, TRACK_LABELS, type SubtitlePreviewState, type VideoLocalizationTrackId, type VideoLocalizationTrackStates } from './studio-state';

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
	let dubAudioEl = $state<HTMLAudioElement | null>(null);
	let activeDubClipId = $state('');
	let dubAudioSrc = $state('');
	let dragDepth = $state(0);
	let playbackFrame = 0;
	let hoverScrubTimer: ReturnType<typeof setTimeout> | null = null;
	let hoverScrubbing = false;
	let hoverScrubRestoreTime = 0;
	let mixAudioContext: AudioContext | null = null;
	const trackGainNodes = new Map<HTMLAudioElement, GainNode>();

	const previewVideoSrc = $derived(sourceVideoUrl(projectId, draft));
	const originalAudioSrc = $derived(sourceAudioUrl(projectId, draft));
	const vocalsAudioSrc = $derived(stemAudioUrl(projectId, draft, 'vocals'));
	const backgroundAudioSrc = $derived(stemAudioUrl(projectId, draft, 'background'));
	const hasDubMedia = $derived(Boolean(draft?.timeline_clips.some((clip) => clip.track_id === 'dub' && clip.audio_path)));
	const soloTracks = $derived((['original', 'vocals', 'background', 'dub'] as VideoLocalizationTrackId[]).filter((trackId) => trackStates[trackId].solo));
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
	const dragActive = $derived(dragDepth > 0);

	$effect(() => {
		projectId;
		draft?.source_media.video_path;
		draft?.stems.vocals_clean_path;
		draft?.stems.background_path;
		draft?.timeline_clips.length;
		sourceVideoFailed = false;
		activeDubClipId = '';
		dubAudioSrc = '';
		onPlaybackStateChange(false);
	});

	$effect(() => {
		applyTrackMix();
	});

	onMount(() => {
		onControllerReady({ playPause: playPauseFromGesture, play: playFromGesture, seek: seekPreview, scrub: scrubPreview, endScrub: endScrubPreview });
		return () => onControllerReady(null);
	});

	onDestroy(() => {
		stopPlaybackClock();
		if (hoverScrubTimer) clearTimeout(hoverScrubTimer);
		trackGainNodes.clear();
		if (mixAudioContext) void mixAudioContext.close();
		mixAudioContext = null;
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
			previewVideoEl.muted = Boolean(originalAudioSrc || vocalsAudioSrc || backgroundAudioSrc || dubAudioSrc);
		}
		applyAudioTrack(originalAudioEl, originalActive, trackStates.original.volume);
		applyAudioTrack(vocalsAudioEl, vocalsActive, trackStates.vocals.volume);
		applyAudioTrack(backgroundAudioEl, backgroundActive, trackStates.background.volume);
		applyAudioTrack(dubAudioEl, dubActive, trackStates.dub.volume);
		if (previewVideoEl && !previewVideoEl.paused) syncAuxiliaryTracks(true);
	}

	function applyAudioTrack(audio: HTMLAudioElement | null, active: boolean, volume: number) {
		if (!audio) return;
		const gainNode = ensureTrackGain(audio);
		audio.muted = false;
		audio.volume = 1;
		if (gainNode) gainNode.gain.value = active ? clampGain(volume) : 0;
		else audio.volume = active ? Math.min(1, clampGain(volume)) : 0;
		if (!active) {
			audio.pause();
			return;
		}
	}

	function ensureTrackGain(audio: HTMLAudioElement) {
		const existing = trackGainNodes.get(audio);
		if (existing) return existing;
		try {
			mixAudioContext ??= new AudioContext();
			const source = mixAudioContext.createMediaElementSource(audio);
			const gain = mixAudioContext.createGain();
			source.connect(gain).connect(mixAudioContext.destination);
			trackGainNodes.set(audio, gain);
			return gain;
		} catch {
			return null;
		}
	}

	function hasTimelineMedia(trackId: VideoLocalizationTrackId) {
		return Boolean(draft?.timeline_clips.some((clip) => clip.track_id === trackId && clip.audio_path));
	}

	function syncAudioTrack(audio: HTMLAudioElement | null, currentTime: number, playIfNeeded = false) {
		if (!audio) return;
		if (Number.isFinite(audio.duration) && currentTime > audio.duration) {
			audio.pause();
			return;
		}
		if (Math.abs(audio.currentTime - currentTime) > 0.18) audio.currentTime = currentTime;
		if (playIfNeeded && audio.paused) {
			void audio.play().then(
				() => delete audio.dataset.playbackError,
				(error: unknown) => {
					audio.dataset.playbackError = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
				}
			);
		}
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
		else dubAudioEl?.pause();
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
		const clip = draft?.timeline_clips.find((item) => {
			if (item.track_id !== 'dub' || !item.audio_path) return false;
			const start = item.start_ms ?? 0;
			const end = item.end_ms ?? start + 1800;
			return timeMs >= start && timeMs < end;
		});
		if (!clip) {
			dubAudioEl?.pause();
			return;
		}
		if (clip.clip_id !== activeDubClipId) {
			activeDubClipId = clip.clip_id;
			dubAudioSrc = timelineClipAudioUrl(projectId, clip);
			requestAnimationFrame(() => syncDubTrack(currentTime, playIfNeeded));
			return;
		}
		const sourceStartSeconds = Math.max(0, (clip.source_start_ms ?? 0) / 1000);
		const localTime = sourceStartSeconds + Math.max(0, currentTime - (clip.start_ms ?? 0) / 1000);
		syncAudioTrack(dubAudioEl, localTime, playIfNeeded);
	}

	function seekPreview(timeMs: number) {
		if (!previewVideoEl) return;
		hoverScrubbing = false;
		const nextTime = Math.max(0, timeMs / 1000);
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
		if (mixAudioContext?.state === 'suspended') void mixAudioContext.resume();
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
		dubAudioEl?.pause();
	}

	function playPauseFromGesture() {
		if (!previewVideoEl) return;
		if (!previewVideoEl.paused) {
			previewVideoEl.pause();
			return;
		}
		playFromGesture();
	}

	function playFromGesture() {
		if (!previewVideoEl || !previewVideoEl.paused) return;
		if (mixAudioContext?.state === 'suspended') void mixAudioContext.resume();
		const videoPlayRequest = previewVideoEl.play();
		syncAuxiliaryTracks(true);
		void videoPlayRequest.catch((error: unknown) => {
			if (error instanceof DOMException && error.name === 'AbortError') return;
			if (previewVideoEl) previewVideoEl.dataset.playbackError = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
		});
	}

	function handleVideoPlay() {
		if (mixAudioContext?.state === 'suspended') void mixAudioContext.resume();
		onPlaybackStateChange(true);
		applyTrackMix();
		syncAuxiliaryTracks(true);
		startPlaybackClock();
	}

	function handlePlaybackGesturePointer(event: PointerEvent) {
		const target = event.target as HTMLElement | null;
		if (target === previewVideoEl || target?.closest('button[aria-label="播放"]')) beginAuxiliaryPlaybackFromGesture();
	}

	function handlePlaybackGestureKey(event: KeyboardEvent) {
		if (event.code !== 'Space') return;
		const target = event.target as HTMLElement | null;
		if (target?.closest('input,textarea,select,[contenteditable="true"]')) return;
		beginAuxiliaryPlaybackFromGesture();
	}

	function beginAuxiliaryPlaybackFromGesture() {
		if (mixAudioContext?.state === 'suspended') void mixAudioContext.resume();
		syncAuxiliaryTracks(true);
	}

	function handleVideoPause() {
		onPlaybackStateChange(false);
		stopPlaybackClock();
		pauseAuxiliaryTracks();
	}

	function handleVideoSeek(event: Event) {
		if (hoverScrubbing) return;
		const timeMs = Math.round((event.currentTarget as HTMLVideoElement).currentTime * 1000);
		onVideoTimeUpdate(timeMs);
		syncAuxiliaryTracks(!previewVideoEl?.paused);
	}

	function handleVideoTimeUpdate(event: Event) {
		const video = event.currentTarget as HTMLVideoElement;
		if (hoverScrubbing) return;
		if (enforcePlaybackLoop(video)) return;
		onPlaybackStateChange(!video.paused && !video.ended);
		onVideoTimeUpdate(Math.round(video.currentTime * 1000));
		if (!video.paused) syncAuxiliaryTracks(true);
	}

	function startPlaybackClock() {
		stopPlaybackClock();
		const tick = () => {
			if (!previewVideoEl || previewVideoEl.paused) {
				playbackFrame = 0;
				return;
			}
			if (enforcePlaybackLoop(previewVideoEl)) {
				playbackFrame = requestAnimationFrame(tick);
				return;
			}
			onPlaybackStateChange(!previewVideoEl.ended);
			onVideoTimeUpdate(Math.round(previewVideoEl.currentTime * 1000));
			syncAuxiliaryTracks(false);
			playbackFrame = requestAnimationFrame(tick);
		};
		playbackFrame = requestAnimationFrame(tick);
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
		return Math.max(0, Math.min(2, parsed));
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

<svelte:window onpointerdown={handlePlaybackGesturePointer} onkeydown={handlePlaybackGestureKey} />

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
				onerror={() => (sourceVideoFailed = true)}
				onplay={handleVideoPlay}
				onpause={handleVideoPause}
				onseeking={handleVideoSeek}
				onseeked={handleVideoSeek}
				ontimeupdate={handleVideoTimeUpdate}
			></video>
			{#if originalAudioSrc}
				<audio bind:this={originalAudioEl} data-audio-group="video-localization-preview" preload="metadata" src={originalAudioSrc} aria-label="原音轨预览"></audio>
			{/if}
			{#if vocalsAudioSrc}
				<audio bind:this={vocalsAudioEl} data-audio-group="video-localization-preview" preload="metadata" src={vocalsAudioSrc} aria-label="人声轨预览"></audio>
			{/if}
			{#if backgroundAudioSrc}
				<audio bind:this={backgroundAudioEl} data-audio-group="video-localization-preview" preload="metadata" src={backgroundAudioSrc} aria-label="背景音乐轨预览"></audio>
			{/if}
			{#if dubAudioSrc}
				<audio bind:this={dubAudioEl} data-audio-group="video-localization-preview" preload="auto" src={dubAudioSrc} aria-label="合成配音轨预览" onloadedmetadata={() => syncAuxiliaryTracks(Boolean(previewVideoEl && !previewVideoEl.paused))}></audio>
			{/if}
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
