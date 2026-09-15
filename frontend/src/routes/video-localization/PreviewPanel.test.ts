import { render } from 'svelte/server';
import { describe, expect, it, vi } from 'vitest';
import type {
	ProjectMediaAssetHealth,
	ProjectMediaHealth,
	ProjectSourceAudioHealth,
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip
} from '$lib/api/types';
import { defaultTrackStates } from './studio-state';
import type { SubtitleDisplayFrame } from './subtitle-display';
import PreviewPanel from './PreviewPanel.svelte';
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

function asset(
	name: ProjectMediaAssetHealth['asset'],
	status: ProjectMediaAssetHealth['status'] = 'missing',
	revision: string | null = null
): ProjectMediaAssetHealth {
	return {
		asset: name,
		status,
		resource_id: status === 'available' ? name : null,
		revision,
		reason_code: status === 'available' ? null : `${name}_missing`,
		recovery_action: 'none'
	};
}

function sourceAudio(
	status: ProjectSourceAudioHealth['status'] = 'missing',
	revision: string | null = null
): ProjectSourceAudioHealth {
	return {
		...asset('source_audio', status, revision),
		asset: 'source_audio',
		selected_source: status === 'available' ? 'source_media' : null,
		candidates: []
	};
}

function health(overrides: Partial<ProjectMediaHealth> = {}): ProjectMediaHealth {
	return {
		contract_version: 'project-media-health-v1',
		package_status: 'available',
		source_video: asset('source_video'),
		source_audio: sourceAudio(),
		vocals: asset('vocals'),
		background: asset('background'),
		stems_status: 'missing',
		...overrides
	};
}

function frame(): SubtitleDisplayFrame {
	return {
		timeMs: 0,
		position: 'bottom',
		currentCues: { asr: null, localized: null },
		lines: []
	};
}

function draft(clips: VideoLocalizationTimelineClip[] = []): VideoLocalizationDraft {
	return {
		source_media: {
			duration_ms: 60_000,
			width: 1920,
			height: 1080
		},
		stems: {},
		cues: [],
		localized_subtitles: [],
		timeline_clips: clips,
		ui_state: {}
	} as unknown as VideoLocalizationDraft;
}

function renderPreview(
	mediaHealth: ProjectMediaHealth | null,
	value = draft(),
	overrides: Record<string, unknown> = {}
) {
	return render(PreviewPanel, {
		props: {
			subtitleFrame: frame(),
			draft: value,
			mediaHealth,
			projectId: 'project-1',
			onPlaybackIssue: vi.fn(),
			...overrides
		}
	});
}

describe('preview playback recovery policy', () => {
	it('retries a failed dubbing clip once before surfacing a playback failure', async () => {
		const source = await import('node:fs/promises').then(({ readFile }) => readFile(
			new URL('./PreviewPanel.svelte', import.meta.url),
			'utf8'
		));
		expect(source).toContain('if (recoverFailedDubAudio(audio)) return;');
		expect(source).toContain("dubAudioRecoveryAttempts.set(clipId, 1)");
		expect(source).toContain("dubAudioRecoveryAttempts.delete(dubClipId)");
		expect(source).toContain('[clipId]: Date.now()');
		const recoveryStart = source.indexOf('function recoverFailedDubAudio');
		const recoveryEnd = source.indexOf('function recoverFailedAudioPreview', recoveryStart);
		expect(source.slice(recoveryStart, recoveryEnd)).not.toContain('mediaReloadRevision = Date.now()');
	});

	it('falls back only when a rebuildable audio preview proxy fails', () => {
		expect(shouldFallbackFromAudioPreview('preview')).toBe(true);
		expect(shouldFallbackFromAudioPreview('source')).toBe(false);
	});

	it('rejects stale playback completions without cancelling the current intent', () => {
		expect(playbackRequestStillCurrent({
			requestRevision: 3,
			intentRevision: 4,
			pendingRevision: 4,
			playbackWanted: true
		})).toBe(false);
		expect(playbackRequestStillCurrent({
			requestRevision: 4,
			intentRevision: 4,
			pendingRevision: 4,
			playbackWanted: true
		})).toBe(true);
		expect(playbackRequestStillCurrent({
			requestRevision: 4,
			intentRevision: 4,
			pendingRevision: 4,
			playbackWanted: false
		})).toBe(false);
	});

	it('treats AbortError as a media race and keeps runtime stalls until the clock advances', () => {
		expect(isMediaAbortError(new DOMException('interrupted', 'AbortError'))).toBe(true);
		expect(isMediaAbortError(new Error('decoder failed'))).toBe(false);
		expect(mediaClockAdvanced(4.019, 4)).toBe(false);
		expect(mediaClockAdvanced(4.021, 4)).toBe(true);
		expect(mediaClockAdvanced(3.97, 4)).toBe(true);
	});

	it('keys resets by every source identity and restarts only a pending paused intent', () => {
		const value = health({
			source_video: asset('source_video', 'available', 'video-r1'),
			source_audio: sourceAudio('available', 'audio-r1'),
			vocals: asset('vocals', 'available', 'vocals-r1'),
			background: asset('background', 'available', 'background-r1')
		});
		expect(previewMediaIdentityKey('project-1', value)).toBe(
			'project-1:video-r1:audio-r1:vocals-r1:background-r1'
		);
		expect(shouldRestartPendingPlayback(true, true)).toBe(true);
		expect(shouldRestartPendingPlayback(true, false)).toBe(false);
		expect(shouldRestartPendingPlayback(false, true)).toBe(false);
		expect(shouldCommitMediaPause(false, false)).toBe(false);
		expect(shouldCommitMediaPause(true, false)).toBe(true);
		expect(shouldCommitMediaPause(false, true)).toBe(true);
	});

	it('labels silent, ordinary, solo, preparing, and blocked mixes in one policy', () => {
		expect(previewPlaybackModeLabel({
			activeTrackLabels: [],
			hasSoloTrack: false,
			preparing: false
		})).toBe('静音预览');
		expect(previewPlaybackModeLabel({
			activeTrackLabels: ['原音轨', '合成配音轨'],
			hasSoloTrack: true,
			preparing: false
		})).toBe('原音轨 + 合成配音轨（独奏）');
		expect(previewPlaybackModeLabel({
			activeTrackLabels: ['原音轨'],
			hasSoloTrack: false,
			preparing: true
		})).toBe('正在准备音频 · 原音轨');
		expect(previewPlaybackModeLabel({
			activeTrackLabels: ['原音轨'],
			hasSoloTrack: false,
			preparing: true,
			blockers: ['原音轨未缓存']
		})).toBe('正在缓冲 · 原音轨未缓存');
	});
});

describe('preview panel server-rendered behavior', () => {
	it('leaves global service health to the app header instead of covering the video', () => {
		const { body } = renderPreview(health());
		expect(body).not.toContain('backend-status-chip');
		expect(body).not.toContain('正在连接本地服务');
	});

	it('keeps project editing available when configured source media is missing', () => {
		const { body } = renderPreview(health());
		expect(body).toContain('源视频文件当前不可用');
		expect(body).toContain('项目编辑数据仍然保留');
		expect(body).not.toContain('<video');
	});

	it('keeps every audio source unmounted while the preview is idle', () => {
		const mediaHealth = health({
			source_video: asset('source_video', 'available', 'video-r1'),
			source_audio: sourceAudio('available', 'audio-r1'),
			vocals: asset('vocals', 'available', 'vocals-r1'),
			background: asset('background', 'available', 'background-r1')
		});
		const trackStates = defaultTrackStates();
		trackStates.original.solo = true;
		const { body } = renderPreview(mediaHealth, draft(), { trackStates });
		expect(body).not.toContain('<video');
		expect(body).toContain('正在准备当前位置的画面');
		expect(body).toContain('首个短片段完成后即可播放');
		expect(body).not.toContain('<audio');
		expect(body).not.toContain('aria-label="原音轨预览"');
		expect(body).not.toContain('aria-label="人声轨预览"');
		expect(body).not.toContain('aria-label="背景音乐轨预览"');
	});

	it('does not preload dubbing media before a playback session starts', () => {
		const clips = Array.from({ length: 20 }, (_, index): VideoLocalizationTimelineClip => ({
			clip_id: `dub_${index}`,
			track_id: 'dub',
			start_ms: index * 1_000,
			end_ms: index * 1_000 + 900,
			audio_path: `/managed/dub_${index}.wav`
		}));
		const mediaHealth = health({
			source_video: asset('source_video', 'available', 'video-r1')
		});
		const { body } = renderPreview(mediaHealth, draft(clips));
		const mountedIds = [...body.matchAll(/data-dub-clip="([^"]+)"/g)].map((match) => match[1]);
		expect(mountedIds).toEqual([]);
		expect(body).not.toContain('<audio');
	});

	it('keeps scrub preview visual-only instead of starting large audio tracks', async () => {
		const source = await import('node:fs/promises').then(({ readFile }) => readFile(
			new URL('./PreviewPanel.svelte', import.meta.url),
			'utf8'
		));
		const scrubStart = source.indexOf('function scrubPreview');
		const scrubEnd = source.indexOf('function endScrubPreview', scrubStart);
		expect(source.slice(scrubStart, scrubEnd)).not.toContain('auditionScrubAudio');
		expect(source).not.toContain('function playScrubAudioAt');
	});

	it('loads only video metadata while the preview is idle', async () => {
		const source = await import('node:fs/promises').then(({ readFile }) => readFile(
			new URL('./PreviewPanel.svelte', import.meta.url),
			'utf8'
		));
		expect(source).toContain('<video\n\t\t\t\t\tclass="preview-video"');
		expect(source).toContain('preload="metadata"');
	});
});
