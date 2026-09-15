import { describe, expect, it } from 'vitest';
import type { VideoLocalizationDraft } from '$lib/api/types';
import {
	buildDubSubtitleReviewRequest
} from './dub-subtitle-review';

function draftWithDubSubtitles(): VideoLocalizationDraft {
	return {
		project_type: 'video_localization',
		schema_version: 'v1',
		status: 'reviewing',
		source_media: { duration_ms: 10_000 } as VideoLocalizationDraft['source_media'],
		stems: {} as VideoLocalizationDraft['stems'],
		speakers: [],
		reference_clips: [],
		cues: [],
		transcription: null,
		localized_subtitles: [],
		dub_subtitles: [
			{
				subtitle_id: 'dub-1',
				start_ms: 100,
				end_ms: 900,
				text: '第一条',
				speaker_id: 'speaker-1',
				source_clip_ids: ['clip-1'],
				dub_lanes: [0],
				source_audio_sha256: 'b'.repeat(64),
				needs_review: false,
				quality_flags: []
			},
			{
				subtitle_id: 'dub-2',
				start_ms: 1_100,
				end_ms: 1_900,
				text: '第二条',
				speaker_id: 'speaker-1',
				source_clip_ids: ['clip-2'],
				dub_lanes: [0],
				source_audio_sha256: 'c'.repeat(64),
				needs_review: false,
				quality_flags: []
			}
		],
		dub_subtitle_source_revision: 'a'.repeat(64),
		localized_spoken_segments: [],
		quality_gate: {} as VideoLocalizationDraft['quality_gate'],
		operations: [],
		glossary: [],
		scene_context: '',
		ui_state: {},
		generated_candidates: [],
		timeline_clips: [],
		updated_at: null
	};
}

describe('dub subtitle text review request', () => {
	it('updates only the selected text while preserving the complete ordered partition', () => {
		const request = buildDubSubtitleReviewRequest(
			draftWithDubSubtitles(),
			'dub-2',
			{ text: '  修改后的第二条  ' }
		);

		expect(request).toEqual({
			source_revision: 'a'.repeat(64),
			cues: [
				{
					subtitle_id: 'dub-1',
					source_subtitle_ids: ['dub-1'],
					start_ms: 100,
					end_ms: 900,
					text: '第一条'
				},
				{
					subtitle_id: 'dub-2',
					source_subtitle_ids: ['dub-2'],
					start_ms: 1_100,
					end_ms: 1_900,
					text: '修改后的第二条'
				}
			]
		});
	});

	it('rejects blank text, missing cues, or a missing source revision', () => {
		const draft = draftWithDubSubtitles();
		expect(() => buildDubSubtitleReviewRequest(draft, 'dub-1', { text: '   ' })).toThrow('配音字幕不能为空');
		expect(() => buildDubSubtitleReviewRequest(draft, 'missing', { text: '字幕' })).toThrow('没有找到要编辑的配音字幕');
		expect(() => buildDubSubtitleReviewRequest({ ...draft, dub_subtitle_source_revision: null }, 'dub-1', { text: '字幕' })).toThrow('配音字幕版本不可用');
	});

	it('updates only the selected in and out points while preserving text and the ordered partition', () => {
		const request = buildDubSubtitleReviewRequest(
			draftWithDubSubtitles(),
			'dub-2',
			{ start_ms: 1_050, end_ms: 2_000 }
		);

		expect(request.cues).toEqual([
			{
				subtitle_id: 'dub-1',
				source_subtitle_ids: ['dub-1'],
				start_ms: 100,
				end_ms: 900,
				text: '第一条'
			},
			{
				subtitle_id: 'dub-2',
				source_subtitle_ids: ['dub-2'],
				start_ms: 1_050,
				end_ms: 2_000,
				text: '第二条'
			}
		]);
	});

	it('rejects invalid or out-of-range timing before sending the review command', () => {
		const draft = draftWithDubSubtitles();
		expect(() => buildDubSubtitleReviewRequest(draft, 'dub-1', { start_ms: 900 })).toThrow('出点必须晚于入点');
		expect(() => buildDubSubtitleReviewRequest(draft, 'dub-1', { start_ms: -1 })).toThrow('不能早于视频开头');
		expect(() => buildDubSubtitleReviewRequest(draft, 'dub-1', { end_ms: 10_001 })).toThrow('不能晚于视频结尾');
	});
});
