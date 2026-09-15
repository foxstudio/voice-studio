import { describe, expect, it } from 'vitest';
import type { GenerateRequest, HistoryItem, VideoLocalizationOperation, VideoLocalizationTimelineClip } from '$lib/api/types';
import { buildTtsCoverageByIdentity, localizeVideoLocalizationError, operationStatusLabel, previewCacheSpriteUrl, requestWithHistoryParameters, summarizeVideoLocalizationError, timelineClipAudioUrl, timelineClipPreviewWaveformUrl, timelineClipVerificationCoverage, timelineClipWaveformUrl, upsertOperation, waveformBinsForTimelineZoom } from './utils';

describe('video localization media resource URLs', () => {
	it('builds a bounded and encoded preview sprite URL', () => {
		expect(previewCacheSpriteUrl('project/1', -2.4, 'revision/1')).toBe(
			'/api/projects/project%2F1/video-localization/source-media/preview-cache/sprite/0?revision=revision%2F1'
		);
	});
});

describe('video localization history reuse', () => {
	it('keeps the current timeline binding when old history contains another route', () => {
		const base = {
			text: '当前字幕', source: 'video_localization', project_id: 'project-current', segment_id: 'localized-current',
			localized_subtitle_id: 'localized-current', cue_id: 'cue-current', timeline_clip_id: 'clip-current',
			bind_to_video_localization: true, engine_id: 'indextts-v2'
		} as GenerateRequest;
		const history = {
			parameter_snapshot: {
				bind_to_video_localization: false, project_id: 'project-old', segment_id: 'localized-old',
				localized_subtitle_id: 'localized-old', cue_id: 'cue-old', timeline_clip_id: 'clip-old', speed: 1.2
			}
		} as unknown as HistoryItem;

		const request = requestWithHistoryParameters(base, history);

		expect(request).toMatchObject({
			project_id: 'project-current', segment_id: 'localized-current', localized_subtitle_id: 'localized-current',
			cue_id: 'cue-current', timeline_clip_id: 'clip-current', bind_to_video_localization: true, speed: 1.2
		});
	});
});

describe('video localization TTS verification coverage', () => {
	it('keeps timeline coverage available after a fresh draft replaces transient clip fields', () => {
		const history = [{
			result_id: 'result-1', task_id: 'task-1', generation_id: 'generation-1',
			verification: { status: 'passed', coverage: 0.96 }
		}] as unknown as HistoryItem[];
		const coverage = buildTtsCoverageByIdentity(history);
		const clip = { clip_id: 'clip-1', track_id: 'dub', result_id: 'result-1' } as VideoLocalizationTimelineClip;

		expect(timelineClipVerificationCoverage(clip, coverage)).toBe(96);
	});
});

describe('video localization error messages', () => {
	it('localizes persisted English ASR errors', () => {
		expect(localizeVideoLocalizationError('English ASR did not return subtitle text')).toBe(
			'语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。'
		);
	});

	it('localizes errors that contain a cue id', () => {
		expect(localizeVideoLocalizationError('Cue cue_0007 does not have a reference clip')).toBe(
			'字幕片段 cue_0007 还没有绑定参考音。'
		);
	});

	it('keeps unknown diagnostics intact', () => {
		expect(localizeVideoLocalizationError('worker exited with status 137')).toBe('worker exited with status 137');
	});

	it('uses plain Chinese in the status slot while keeping raw details available', () => {
		expect(summarizeVideoLocalizationError('Method Not Allowed')).toBe('当前服务还没有加载这项操作，请刷新服务后重试。');
		expect(summarizeVideoLocalizationError('worker exited with status 137')).toBe('操作没有完成，请打开详情查看具体原因。');
	});
});

describe('video localization operation status', () => {
	it('shows the active stage and numeric progress', () => {
		const operation: VideoLocalizationOperation = {
			operation_id: 'operation-1',
			project_id: 'project-1',
			kind: 'english_asr',
			status: 'running',
			label: '听写字幕',
			progress: 0.58,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			result_summary: { stage: '正在生成逐词时间码' },
			parameters: {},
			created_at: '2026-07-15T00:00:00',
			started_at: '2026-07-15T00:00:01',
			completed_at: null
		};

		expect(operationStatusLabel(operation)).toBe('正在生成逐词时间码 · 58%');
	});

	it('replaces an existing operation by id instead of duplicating a retry', () => {
		const previous = {
			operation_id: 'operation-1',
			created_at: '2026-07-15T00:00:00',
			status: 'failed'
		} as VideoLocalizationOperation;
		const retry = {
			...previous,
			status: 'queued',
			result_summary: { stage: '重新排队' }
		} as VideoLocalizationOperation;

		const updated = upsertOperation([previous], retry);

		expect(updated).toHaveLength(1);
		expect(updated[0]).toEqual(retry);
	});
});

describe('timeline clip media versions', () => {
	it('keeps URLs across a path-redacted edit receipt for generated and legacy audio', () => {
		for (const result_id of ['result-1', undefined]) {
			const before: VideoLocalizationTimelineClip = {
				clip_id: 'new-clip', track_id: 'dub', audio_path: '/managed/audio.wav',
				generation_identity: 'media-token', result_id, has_audio_source: true
			};
			const { audio_path, ...receipt } = before;
			expect(timelineClipAudioUrl('project', receipt)).toBe(timelineClipAudioUrl('project', before));
			expect(timelineClipWaveformUrl('project', receipt, 320)).toBe(timelineClipWaveformUrl('project', before, 320));
		}
	});
	it('changes audio and waveform URLs when a clip adopts another history result', () => {
		const clip: VideoLocalizationTimelineClip = {
			clip_id: 'clip_localized_0001',
			track_id: 'dub',
			audio_path: '/tmp/first.wav',
			result_id: 'result-first'
		};
		const replaced = { ...clip, audio_path: '/tmp/second.wav', result_id: 'result-second' };

		expect(timelineClipAudioUrl('project-1', clip)).toContain('v=result-first');
		expect(timelineClipWaveformUrl('project-1', clip)).toContain('v=result-first');
		expect(timelineClipWaveformUrl('project-1', replaced)).toContain('v=result-second');
		expect(timelineClipWaveformUrl('project-1', replaced)).not.toBe(timelineClipWaveformUrl('project-1', clip));
	});

	it('uses the stable media source id for a split timeline piece', () => {
		const clip: VideoLocalizationTimelineClip = {
			clip_id: 'clip_localized_0001_part_2',
			media_source_clip_id: 'clip_localized_0001',
			track_id: 'dub'
		};

		expect(timelineClipAudioUrl('project-1', clip)).toContain('/timeline-clips/clip_localized_0001/audio');
		expect(timelineClipWaveformUrl('project-1', clip)).toContain('/timeline-clips/clip_localized_0001/waveform');
	});

	it('keeps one source-level preview waveform across clip cuts', () => {
		const first: VideoLocalizationTimelineClip = {
			clip_id: 'clip-part-1', media_source_clip_id: 'clip-source', track_id: 'dub',
			result_id: 'result-1', start_ms: 1_000, end_ms: 2_000,
			source_start_ms: 0, source_end_ms: 1_000
		};
		const second: VideoLocalizationTimelineClip = {
			...first, clip_id: 'clip-part-2', start_ms: 2_000, end_ms: 4_000,
			source_start_ms: 1_000, source_end_ms: 3_000
		};

		expect(timelineClipPreviewWaveformUrl('project-1', first))
			.toBe(timelineClipPreviewWaveformUrl('project-1', second));
		expect(timelineClipPreviewWaveformUrl('project-1', first)).not.toContain('start_ms');
	});

	it('plays and draws an optimistic history drop before the timeline commit finishes', () => {
		const clip: VideoLocalizationTimelineClip = {
			clip_id: 'pending_history_result-1',
			track_id: 'dub',
			audio_path: 'history:result-1',
			status: 'applying',
			optimistic_history_result_id: 'result-1'
		};

		expect(timelineClipAudioUrl('project-1', clip)).toBe('/api/history/result-1/audio');
		expect(timelineClipWaveformUrl('project-1', clip)).toBe('/api/history/result-1/waveform?bins=320');
	});

	it('changes waveform precision only when timeline zoom crosses a stable band', () => {
		expect(waveformBinsForTimelineZoom(1)).toBe(4800);
		expect(waveformBinsForTimelineZoom(3)).toBe(4800);
		expect(waveformBinsForTimelineZoom(10)).toBe(19_200);
		expect(waveformBinsForTimelineZoom(30)).toBe(76_800);
		expect(waveformBinsForTimelineZoom(120)).toBe(180_000);
		expect(timelineClipWaveformUrl('project-1', { clip_id: 'media_original', track_id: 'original' }, 19_200)).toContain('&bins=19200');
		expect(timelineClipWaveformUrl(
			'project-1',
			{ clip_id: 'media_original', track_id: 'original' },
			19_200,
			{ startMs: 110_000, endMs: 140_000 }
		)).toContain('&start_ms=110000&end_ms=140000');
	});
});
