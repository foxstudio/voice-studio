import { describe, expect, it } from 'vitest';
import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineMutationResponse
} from '$lib/api/types';
import { createHistoryTimelineClipId, mergeTimelineMutation, resolveHistoryTimelineClip, timelineDubSegmentId } from './timeline-history-command';
import { TimelineEditController } from './timeline-edit-controller';

function draftWithClips(clips: VideoLocalizationDraft['timeline_clips']) {
	return {
		updated_at: null,
		cues: [],
		localized_subtitles: [],
		timeline_clips: clips,
		ui_state: {}
	} as unknown as VideoLocalizationDraft;
}

function mutation(
	clip: VideoLocalizationDraft['timeline_clips'][number]
): VideoLocalizationTimelineMutationResponse {
	return {
		schema_version: 'video-localization-timeline-mutation-v1',
		updated_at: '2026-08-31T10:00:00Z',
		revision: '12',
		affected_clip_ids: [clip.clip_id],
		timeline_clips: [clip],
		cues: [],
		localized_subtitles: [],
		dub_lane_states: {},
		discarded_tts_task_ids: []
	};
}

describe('timeline history command identity', () => {
	it.each([
		{ subtitle_id: 'subtitle', cue_id: 'cue', segment: 'subtitle' },
		{ subtitle_id: 'group_subtitle', segment: 'group_subtitle' },
		{ cue_id: 'cue', segment: 'cue' }
	])('adopts history into the selected split child: $segment', ({ segment, ...identity }) => {
		const first = { clip_id: 'first', track_id: 'dub', start_ms: 0, end_ms: 500, ...identity };
		const child = { ...first, clip_id: 'child', start_ms: 500, end_ms: 1000 };
		expect(timelineDubSegmentId(child)).toBe(segment);
		expect(resolveHistoryTimelineClip([first, child], child, segment, false)).toBe(child);
	});

	it('uses the subtitle match only when the selection is unrelated or not a dub clip', () => {
		const first = { clip_id: 'first', track_id: 'dub', subtitle_id: 'subtitle', start_ms: 0, end_ms: 500 };
		for (const selected of [null, { ...first, subtitle_id: 'other' }, { ...first, track_id: 'vocals' }]) {
			expect(resolveHistoryTimelineClip([first], selected, 'subtitle', false)).toBe(first);
		}
		expect(resolveHistoryTimelineClip([first], first, '', false)).toBeNull();
		expect(resolveHistoryTimelineClip([first], { ...first, clip_id: 'runtime_only' }, 'subtitle', false)).toBeNull();
	});

	it('resolves a grouped clip without subtitle metadata and keeps cue fallback explicit', () => {
		const group = { clip_id: 'clip_group_subtitle', track_id: 'dub', start_ms: 0, end_ms: 500 };
		expect(timelineDubSegmentId(group)).toBe('group_subtitle');
		expect(resolveHistoryTimelineClip([group], group, 'group_subtitle', false)).toBe(group);
		const cue = { ...group, clip_id: 'cue_clip', cue_id: 'cue' };
		expect(resolveHistoryTimelineClip([cue], null, 'cue', false)).toBeNull();
		expect(resolveHistoryTimelineClip([cue], null, 'cue', true)).toBe(cue);
	});
	it('uses one stable clip id from optimistic placement through server acknowledgement', () => {
		const clipId = createHistoryTimelineClipId('5f3a7f8e-7932-4eb1-8331-401aa5ec9811');
		const untouched = { clip_id: 'untouched', track_id: 'dub', start_ms: 0, end_ms: 900 };
		const optimistic = {
			clip_id: clipId,
			track_id: 'dub',
			start_ms: 1_000,
			end_ms: 2_000,
			status: 'applying',
			optimistic_history_result_id: 'history-1'
		};
		const authoritative = {
			...optimistic,
			status: 'ready',
			result_id: 'history-1',
			optimistic_history_result_id: undefined
		};
		const before = draftWithClips([untouched, optimistic]);

		const after = mergeTimelineMutation(before, mutation(authoritative));

		expect(after.timeline_clips.map((clip) => clip.clip_id)).toEqual(['untouched', clipId]);
		expect(after.timeline_clips[0]).toBe(untouched);
		expect(after.timeline_clips[1]).toMatchObject({ status: 'ready', result_id: 'history-1' });
	});

	it('keeps an applying history overlay out of edit history and adopts the authoritative placement', () => {
		const clipId = createHistoryTimelineClipId('5f3a7f8e-7932-4eb1-8331-401aa5ec9811');
		const optimistic = {
			clip_id: clipId,
			track_id: 'dub',
			start_ms: 1_000,
			end_ms: 2_000,
			source_start_ms: 0,
			source_end_ms: 1_000,
			status: 'applying',
			optimistic_history_result_id: 'history-1'
		};
		const before = draftWithClips([optimistic]);
		const controller = new TimelineEditController(before);
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{
				clipId,
				patch: { start_ms: 1_400, end_ms: 2_400 }
			}]
		});

		const authoritative = {
			...optimistic,
			status: 'ready',
			result_id: 'history-1',
			optimistic_history_result_id: undefined
		};
		controller.synchronizeExternalDraft(
			mergeTimelineMutation(before, mutation(authoritative))
		);

		expect(controller.draft.timeline_clips[0]).toMatchObject({
			clip_id: clipId,
			status: 'ready',
			start_ms: 1_000,
			end_ms: 2_000,
			result_id: 'history-1'
		});
		expect(controller.hasPendingChanges).toBe(false);
	});
});
