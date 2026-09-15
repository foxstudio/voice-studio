import { describe, expect, it } from 'vitest';
import type { VideoLocalizationDraft } from '$lib/api/types';
import {
	draftWithLiveTimelineProjection,
	reconcileTimelineClipPayloads,
	repositoryRevisionIsOlder
} from './workspace-timeline-projection';

function draft(): VideoLocalizationDraft {
	return {
		project_type: 'video_localization',
		schema_version: 'v1',
		status: 'draft',
		source_media: { duration_ms: 10_000 },
		stems: {},
		transcription: null,
		speakers: [],
		cues: [{ cue_id: 'cue-1', start_ms: 0, end_ms: 1_000 }],
		localized_spoken_segments: [],
		localized_subtitles: [],
		dub_subtitles: [],
		dub_subtitle_source_revision: null,
		dub_subtitle_generated_at: null,
		reference_clips: [],
		language_config: { source_language: 'auto', target_language: 'zh-Hans', detected_source_language: null },
		localization_state: {},
		quality_gate: {},
		dubbing_production: {},
		operations: [],
		tts_tasks: [],
		glossary: [],
		scene_context: '',
		ui_state: { selected_cue_id: 'cue-1' },
		generated_candidates: [],
		timeline_clips: [{ clip_id: 'old', track_id: 'dub', start_ms: 0, end_ms: 500 }],
		updated_at: 'before'
	} as unknown as VideoLocalizationDraft;
}

describe('live timeline projection', () => {
	it('rejects a delayed repository projection older than the accepted write', () => {
		expect(repositoryRevisionIsOlder('41', '42')).toBe(true);
		expect(repositoryRevisionIsOlder('42', '42')).toBe(false);
		expect(repositoryRevisionIsOlder('43', '42')).toBe(false);
		expect(repositoryRevisionIsOlder('legacy-a', '42')).toBe(false);
	});

	it('replaces only timeline-owned state', () => {
		const current = draft();
		const next = draftWithLiveTimelineProjection(current, {
			revision: 'after',
			timeline_clips: [{ clip_id: 'new', track_id: 'dub', start_ms: 1_000, end_ms: 1_500 }]
		});

		expect(next.timeline_clips.map((clip) => clip.clip_id)).toEqual(['new']);
		expect(next.updated_at).toBe('before');
		expect(next.cues).toBe(current.cues);
		expect(next.transcription).toBe(current.transcription);
		expect(next.ui_state).toBe(current.ui_state);
	});

	it('preserves object identity for unchanged clips and replaces only changed clips', () => {
		const current = draft();
		current.timeline_clips = [
			{ clip_id: 'stable', track_id: 'dub', start_ms: 0, end_ms: 500, source_cue_ids: ['cue-1'] },
			{ clip_id: 'changed', track_id: 'dub', start_ms: 500, end_ms: 1_000 }
		];
		const stable = current.timeline_clips[0];
		const changed = current.timeline_clips[1];

		const next = draftWithLiveTimelineProjection(current, {
			revision: 'after',
			timeline_clips: [
				{ clip_id: 'stable', track_id: 'dub', start_ms: 0, end_ms: 500, source_cue_ids: ['cue-1'] },
				{ clip_id: 'changed', track_id: 'dub', start_ms: 550, end_ms: 1_050 }
			]
		});

		expect(next.timeline_clips[0]).toBe(stable);
		expect(next.timeline_clips[1]).not.toBe(changed);
		expect(next.timeline_clips[1]).toMatchObject({ start_ms: 550, end_ms: 1_050 });
	});

	it('reconciles a local cut without replacing unrelated clip resources', () => {
		const untouched = { clip_id: 'untouched', track_id: 'dub', start_ms: 0, end_ms: 500 };
		const edited = { clip_id: 'edited', track_id: 'dub', start_ms: 500, end_ms: 1_500 };
		const next = reconcileTimelineClipPayloads(
			[untouched, edited],
			[
				{ ...untouched },
				{ ...edited, end_ms: 1_000 },
				{ ...edited, clip_id: 'edited_part_2', start_ms: 1_000 }
			]
		);

		expect(next[0]).toBe(untouched);
		expect(next[1]).not.toBe(edited);
		expect(next.map((clip) => clip.clip_id)).toEqual(['untouched', 'edited', 'edited_part_2']);
	});

	it('does not copy client-side runtime submissions into the durable projection', () => {
		const current = draft();
		current.timeline_clips.push({
			clip_id: 'pending_tts_init-submission-a',
			track_id: 'dub',
			start_ms: 2_000,
			end_ms: 3_000,
			status: 'queued',
			status_label: '等待提交',
			optimistic_tts_workflow_id: 'init:submission-a'
		});

		const next = draftWithLiveTimelineProjection(current, {
			revision: 'after',
			timeline_clips: [{ clip_id: 'new', track_id: 'dub', start_ms: 1_000, end_ms: 1_500 }]
		});

		expect(next.timeline_clips.map((clip) => clip.clip_id)).toEqual(['new']);
	});

	it('adopts a completed TTS result while preserving unrelated unsaved clip edits', () => {
		const current = draft();
		current.timeline_clips = [{
			clip_id: 'editing',
			track_id: 'dub',
			start_ms: 2_000,
			end_ms: 3_400,
			dub_lane: 2
		}];

		const next = draftWithLiveTimelineProjection(current, {
			revision: 'after',
			timeline_clips: [{
				clip_id: 'editing',
				track_id: 'dub',
				start_ms: 1_000,
				end_ms: 2_000,
				dub_lane: 0
			}, {
				clip_id: 'generated',
				track_id: 'dub',
				start_ms: 4_000,
				end_ms: 5_000,
				audio_path: '/tmp/generated.wav',
				status: 'ready'
			}]
		}, {
			dirtyFieldsByClipId: new Map([['editing', new Set(['timing', 'lane'] as const)]])
		});

		expect(next.timeline_clips).toEqual([
			expect.objectContaining({ clip_id: 'editing', start_ms: 2_000, end_ms: 3_400, dub_lane: 2 }),
			expect.objectContaining({ clip_id: 'generated', audio_path: '/tmp/generated.wav', status: 'ready' })
		]);
	});

	it('does not resurrect a locally deleted clip when a task projection arrives', () => {
		const current = draft();
		const next = draftWithLiveTimelineProjection(current, {
			revision: 'after',
			timeline_clips: [
				{ clip_id: 'old', track_id: 'dub', start_ms: 0, end_ms: 500 },
				{ clip_id: 'generated', track_id: 'dub', start_ms: 500, end_ms: 1_000 }
			]
		}, { deletedClipIds: ['old'] });

		expect(next.timeline_clips.map((clip) => clip.clip_id)).toEqual(['generated']);
	});

	it('drops a stale edit when the server atomically replaced its target clip', () => {
		const current = draft();
		current.timeline_clips = [{
			clip_id: 'old',
			track_id: 'dub',
			start_ms: 250,
			end_ms: 750
		}];

		const next = draftWithLiveTimelineProjection(current, {
			revision: 'after',
			timeline_clips: [{
				clip_id: 'replacement',
				track_id: 'dub',
				start_ms: 0,
				end_ms: 500
			}]
		}, {
			dirtyFieldsByClipId: new Map([['old', new Set(['timing'] as const)]])
		});

		expect(next.timeline_clips.map((clip) => clip.clip_id)).toEqual(['replacement']);
	});

	it('keeps a genuinely local new clip until its first save lands', () => {
		const current = draft();
		current.timeline_clips = [{
			clip_id: 'local-new',
			track_id: 'dub',
			start_ms: 250,
			end_ms: 750
		}];

		const next = draftWithLiveTimelineProjection(current, {
			revision: 'after',
			timeline_clips: []
		}, {
			addedClipIds: ['local-new']
		});

		expect(next.timeline_clips.map((clip) => clip.clip_id)).toEqual(['local-new']);
	});
});
