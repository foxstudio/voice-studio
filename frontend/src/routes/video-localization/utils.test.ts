import { describe, expect, it } from 'vitest';
import type { VideoLocalizationOperation, VideoLocalizationTimelineClip } from '$lib/api/types';
import { localizeVideoLocalizationError, operationStatusLabel, summarizeVideoLocalizationError, timelineClipAudioUrl, timelineClipWaveformUrl } from './utils';

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
});

describe('timeline clip media versions', () => {
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
			track_id: 'dub',
			audio_path: '/tmp/first.wav'
		};

		expect(timelineClipAudioUrl('project-1', clip)).toContain('/timeline-clips/clip_localized_0001/audio');
		expect(timelineClipWaveformUrl('project-1', clip)).toContain('/timeline-clips/clip_localized_0001/waveform');
	});
});
