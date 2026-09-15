import { describe, expect, it } from 'vitest';
import type { HistoryItem, VideoLocalizationTimelineClip } from '$lib/api/types';
import {
	historyAudioIdentityLabel,
	historyCueIds,
	timelineDubClipLabel
} from './audio-identity-label';

function history(overrides: Partial<HistoryItem> = {}): HistoryItem {
	return {
		result_id: 'result-abcdef',
		task_id: 'task-1',
		engine_id: 'omnivoice',
		voice_id: null,
		voice_name: null,
		project_id: 'project-1',
		segment_id: 'localized_cue_0001',
		cue_id: null,
		longform_task_id: null,
		longform_segment_index: null,
		longform_segment_count: null,
		longform_export_id: null,
		input_text: '台词',
		output_audio_id: null,
		output_path: null,
		duration_ms: 1000,
		generation_time_ms: 100,
		verification: null,
		verification_error: null,
		parameter_snapshot: {},
		favorite: false,
		created_at: '2026-09-05T00:00:00Z',
		...overrides
	};
}

describe('audio identity labels', () => {
	it('shows all source cue names saved with a grouped history result', () => {
		const item = history({
			cue_id: 'cue_0001',
			parameter_snapshot: {
				video_localization_source_cue_ids: ['cue_0001', 'cue_0002']
			}
		});
		expect(historyCueIds(item)).toEqual(['cue_0001', 'cue_0002']);
		expect(historyAudioIdentityLabel(item)).toBe('cue_0001 + cue_0002 · 音频 abcdef');
	});

	it('uses the same audio identity suffix on timeline clips', () => {
		const clip = {
			clip_id: 'clip-1',
			track_id: 'dub',
			cue_id: 'cue_0059',
			result_id: 'result-abcdef'
		} satisfies VideoLocalizationTimelineClip;
		expect(timelineDubClipLabel(clip)).toBe('cue_0059 · 音频 abcdef');
		expect(timelineDubClipLabel({ ...clip, result_id: 'result-fedcba' })).toBe(
			'cue_0059 · 音频 fedcba'
		);
	});

	it('numbers slices that share one audio result', () => {
		const parent = {
			clip_id: 'clip-source',
			track_id: 'dub',
			cue_id: 'cue_0059',
			result_id: 'result-abcdef',
			start_ms: 1000,
			source_start_ms: 0
		} satisfies VideoLocalizationTimelineClip;
		const child = {
			...parent,
			clip_id: 'clip-source_part_2',
			media_source_clip_id: 'clip-source',
			start_ms: 1500,
			source_start_ms: 500
		};
		expect(timelineDubClipLabel(parent, [child, parent])).toBe(
			'cue_0059 · 音频 abcdef · 切片 1/2'
		);
		expect(timelineDubClipLabel(child, [child, parent])).toBe(
			'cue_0059 · 音频 abcdef · 切片 2/2'
		);
	});
});
