import { describe, expect, it } from 'vitest';
import type { ProjectMediaHealth, VideoLocalizationDraft } from '$lib/api/types';
import {
	draftForPersistence,
	mergeDraftAfterConflict,
	preserveClientContentAfterUiStateSave,
	snapshotDraftForConflictMerge,
	timelineDeletionIntentForSave
} from './draft-ownership';
import { normalizeWorkspaceDraft } from './workspace-draft';

function mediaHealth(): ProjectMediaHealth {
	return {
		contract_version: 'project-media-health-v1',
		package_status: 'available',
		source_video: {
			asset: 'source_video',
			status: 'available',
			resource_id: 'source_video',
			revision: 'video-r1',
			reason_code: null,
			recovery_action: 'none'
		},
		source_audio: {
			asset: 'source_audio',
			status: 'available',
			resource_id: 'source_audio',
			revision: 'audio-r1',
			reason_code: null,
			recovery_action: 'none',
			selected_source: 'source_media',
			candidates: []
		},
		vocals: {
			asset: 'vocals',
			status: 'available',
			resource_id: 'vocals',
			revision: 'vocals-r1',
			reason_code: null,
			recovery_action: 'none'
		},
		background: {
			asset: 'background',
			status: 'available',
			resource_id: 'background',
			revision: 'background-r1',
			reason_code: null,
			recovery_action: 'none'
		},
		stems_status: 'complete'
	};
}

function draft(overrides: Partial<VideoLocalizationDraft> = {}): VideoLocalizationDraft {
	return {
		source_media: { duration_ms: 12_000 },
		stems: {},
		cues: [],
		localized_subtitles: [],
		glossary: [],
		scene_context: {},
		timeline_clips: [],
		ui_state: {},
		tts_tasks: [],
		...overrides
	} as unknown as VideoLocalizationDraft;
}

describe('workspace draft normalization', () => {
	it('keeps client-only TTS initialization clips out of the durable workspace draft', () => {
		const firstInitialization = {
			clip_id: 'pending_tts_init_client-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1_000,
			end_ms: 2_200,
			audio_path: null,
			status: 'queued',
			optimistic_tts_workflow_id: 'init:client-1'
		};
		const secondInitialization = {
			clip_id: 'pending_tts_init_client-2',
			track_id: 'dub',
			subtitle_id: 'localized-2',
			start_ms: 2_400,
			end_ms: 3_500,
			audio_path: null,
			status: 'queued',
			optimistic_tts_workflow_id: 'init:client-2'
		};

		const normalized = normalizeWorkspaceDraft(
			draft({ timeline_clips: [firstInitialization, secondInitialization] }),
			mediaHealth()
		);

		expect(normalized.timeline_clips).not.toEqual(expect.arrayContaining([
			expect.objectContaining(firstInitialization),
			expect.objectContaining(secondInitialization)
		]));
	});

	it('freezes controller deletions from the save packet while retaining external command deletions', () => {
		const saving = timelineDeletionIntentForSave(
			['controller-old', 'external-command'],
			['controller-old'],
			['controller-packet']
		);

		expect(saving).toEqual(new Set(['external-command', 'controller-packet']));
	});

	it('serializes timeline deletion as explicit client intent', () => {
		const current = draft({
			timeline_clips: [{ clip_id: 'clip-1', track_id: 'dub' }]
		});

		const persisted = draftForPersistence(
			{ ...current, timeline_clips: [] },
			{ deletedTimelineClipIdentities: new Map([['clip-1', 'task-old']]) }
		);

		expect(persisted.ui_state.client_timeline_edit_intent).toEqual({
			deleted_timeline_clips: [{
				clip_id: 'clip-1',
				expected_generation_identity: 'task-old'
			}]
		});
	});

	it('serializes a newly split timeline clip as explicit client intent', () => {
		const current = draft({
			timeline_clips: [
				{ clip_id: 'clip-1', track_id: 'dub' },
				{ clip_id: 'clip-1-part-2', track_id: 'dub', media_source_clip_id: 'clip-1' }
			]
		});

		const persisted = draftForPersistence(current, {
			addedTimelineClipIds: ['clip-1-part-2']
		});

		expect(persisted.ui_state.client_timeline_edit_intent).toEqual({
			added_timeline_clip_ids: ['clip-1-part-2']
		});
	});

	it('keeps available media tracks when a UI-state save returns an unprojected draft', () => {
		const health = mediaHealth();
		const currentWorkspaceDraft = normalizeWorkspaceDraft(draft(), health);
		const uiSaveResponse = draft({
			ui_state: { sidebar_collapsed: false }
		});
		const savedWorkspaceDraft = normalizeWorkspaceDraft(uiSaveResponse, health);
		const merged = mergeDraftAfterConflict(
			savedWorkspaceDraft,
			currentWorkspaceDraft,
			{ baseDraft: snapshotDraftForConflictMerge(currentWorkspaceDraft) }
		);

		expect(merged.timeline_clips.map((clip) => clip.track_id)).toEqual([
			'original',
			'vocals',
			'background'
		]);
	});

	it('does not let a UI-only save refresh a stale content conflict token', () => {
		const current = draft({
			updated_at: '2026-08-22T21:12:53Z',
			timeline_clips: [{ clip_id: 'stale-clip', track_id: 'dub' }],
			ui_state: { sidebar_collapsed: false }
		});
		const saved = draft({
			updated_at: '2026-08-22T21:51:08Z',
			timeline_clips: [{ clip_id: 'server-clip', track_id: 'dub' }],
			ui_state: { sidebar_collapsed: true }
		});

		const reconciled = preserveClientContentAfterUiStateSave(saved, current, current);

		expect(reconciled.updated_at).toBe('2026-08-22T21:12:53Z');
		expect(reconciled.timeline_clips).toEqual(current.timeline_clips);
		expect(reconciled.ui_state).toEqual(saved.ui_state);
	});

	it('does not let an older UI-only save response undo a newer mute choice', () => {
		const submitted = draft({
			ui_state: {
				track_states: { background: { muted: true, solo: false, volume: 1, locked: false } },
				sidebar_collapsed: false
			}
		});
		const current = draft({
			ui_state: {
				track_states: { background: { muted: false, solo: false, volume: 1, locked: false } },
				sidebar_collapsed: false
			}
		});
		const saved = draft({
			ui_state: {
				track_states: { background: { muted: true, solo: false, volume: 1, locked: false } },
				sidebar_collapsed: true,
				discarded_tts_task_ids: ['server-task']
			}
		});

		const reconciled = preserveClientContentAfterUiStateSave(saved, current, submitted);

		expect(reconciled.ui_state.track_states).toEqual(current.ui_state.track_states);
		expect(reconciled.ui_state.sidebar_collapsed).toBe(true);
		expect(reconciled.ui_state.discarded_tts_task_ids).toEqual(['server-task']);
	});
});
