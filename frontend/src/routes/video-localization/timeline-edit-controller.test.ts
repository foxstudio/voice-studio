import { describe, expect, it } from 'vitest';
import type {
	VideoLocalizationCue,
	VideoLocalizationDraft,
	VideoLocalizationSubtitleCue,
	VideoLocalizationTimelineEditPatchResponse,
	VideoLocalizationTimelineClip
} from '$lib/api/types';
import { mergeDraftAfterConflict } from './draft-ownership';
import { TimelineEditController } from './timeline-edit-controller';
import { mergeTimelineMutation } from './timeline-history-command';

function clip(
	clipId: string,
	overrides: Partial<VideoLocalizationTimelineClip> = {}
): VideoLocalizationTimelineClip {
	return {
		clip_id: clipId,
		track_id: 'dub',
		start_ms: 1_000,
		end_ms: 2_000,
		source_start_ms: 0,
		source_end_ms: 1_000,
		dub_lane: 0,
		audio_path: `/audio/${clipId}.wav`,
		task_id: `task-${clipId}`,
		generation_identity: `task-${clipId}`,
		status: 'ready',
		...overrides
	};
}

function draft(
	clips: VideoLocalizationTimelineClip[],
	laneStates: Record<string, Record<string, unknown>> = {
		'0': { muted: false, solo: false, volume: 1, label: '主配音' },
		'1': { muted: false, solo: false, volume: 0.8, label: '补充配音' }
	}
): VideoLocalizationDraft {
	return {
		project_type: 'video_localization',
		schema_version: 'v1',
		status: 'draft',
		source_media: {} as VideoLocalizationDraft['source_media'],
		stems: {} as VideoLocalizationDraft['stems'],
		speakers: [],
		reference_clips: [],
		cues: [],
		transcription: null,
		localized_subtitles: [],
		localized_spoken_segments: [],
		quality_gate: {} as VideoLocalizationDraft['quality_gate'],
		operations: [],
		glossary: [],
		scene_context: '',
		ui_state: { dub_lane_states: laneStates },
		generated_candidates: [],
		timeline_clips: clips,
		updated_at: null
	};
}

function byId(controller: TimelineEditController, clipId: string) {
	return controller.draft.timeline_clips.find((item) => item.clip_id === clipId);
}

function laneState(controller: TimelineEditController, lane: string) {
	return (controller.draft.ui_state.dub_lane_states as Record<string, Record<string, unknown>>)[lane];
}

function compactReceipt(
	controller: TimelineEditController,
	overrides: Partial<VideoLocalizationTimelineEditPatchResponse> = {}
): VideoLocalizationTimelineEditPatchResponse {
	return {
		schema_version: 'timeline-edit-patch-v2',
		updated_at: null,
		revision: '2',
		timeline_clips: controller.draft.timeline_clips,
		dub_lane_states: controller.draft.ui_state.dub_lane_states as Record<string, Record<string, unknown>>,
		...overrides
	};
}

function cue(cueId: string, overrides: Partial<VideoLocalizationCue> = {}): VideoLocalizationCue {
	return {
		cue_id: cueId,
		speaker_id: null,
		start_ms: 1_000,
		end_ms: 2_000,
		audio_route: 'clone_from_source',
		en_subtitle_text: 'hello',
		zh_localized_subtitle_text: '你好',
		tts_recommended_text: '你好',
		reference_clip_id: null,
		tts_result_id: null,
		tts_audio_path: null,
		tts_batch_task_id: null,
		tts_batch_status: null,
		tts_batch_error: null,
		tts_attempted_at: null,
		source_duration_ms: 1_000,
		generated_duration_ms: null,
		source_word_ids: [],
		source_text_raw: null,
		timing_confidence: 'high',
		transcription_revision_id: null,
		review_status: 'ready',
		quality_flags: [],
		notes: null,
		...overrides
	};
}

function subtitle(
	subtitleId: string,
	overrides: Partial<VideoLocalizationSubtitleCue> = {}
): VideoLocalizationSubtitleCue {
	return {
		subtitle_id: subtitleId,
		start_ms: 1_000,
		end_ms: 2_000,
		text: '你好',
		quality_flags: [],
		...overrides
	};
}

describe('timeline edit controller', () => {
	it('distinguishes unsaved local additions from clips that existed on the server', () => {
		const controller = new TimelineEditController(draft([clip('server')]));
		controller.dispatch({
			type: 'transaction',
			addClips: [{ clip: clip('local-new') }]
		});

		expect(controller.addedTimelineClipIds).toEqual(new Set(['local-new']));
	});

	it('replays only locally touched timing and lane fields over fresh task metadata', () => {
		const controller = new TimelineEditController(draft([
			clip('clip-1', { result_id: 'result-old' })
		]));

		controller.dispatch({
			type: 'transaction',
			clipPatches: [{
				clipId: 'clip-1',
				patch: { start_ms: 1_400, end_ms: 2_400, dub_lane: 1 }
			}]
		});
		controller.mergeRefresh(draft([
			clip('clip-1', {
				start_ms: 1_000,
				end_ms: 2_000,
				dub_lane: 0,
				audio_path: '/audio/new.wav',
				task_id: 'task-clip-1',
				result_id: 'result-new',
				status: 'success'
			})
		]));

		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_400,
			end_ms: 2_400,
			dub_lane: 1,
			audio_path: '/audio/new.wav',
			task_id: 'task-clip-1',
			result_id: 'result-new',
			status: 'success'
		});

		controller.undo();
		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_000,
			end_ms: 2_000,
			dub_lane: 0,
			audio_path: '/audio/new.wav',
			task_id: 'task-clip-1',
			result_id: 'result-new'
		});
	});

	it('keeps a trimmed clip through refresh and save acknowledgement', () => {
		const controller = new TimelineEditController(draft([
			clip('trimmed', {
				start_ms: 1_000,
				end_ms: 3_000,
				source_start_ms: 0,
				source_end_ms: 2_000
			})
		]));
		controller.dispatch({
			type: 'transaction',
			label: '裁剪配音片段',
			clipPatches: [{
				clipId: 'trimmed',
				patch: {
					start_ms: 1_400,
					end_ms: 2_600,
					source_start_ms: 400,
					source_end_ms: 1_600
				}
			}]
		});
		const packet = controller.prepareSave();

		controller.mergeRefresh(draft([
			clip('trimmed', {
				start_ms: 1_000,
				end_ms: 3_000,
				source_start_ms: 0,
				source_end_ms: 2_000,
				audio_path: '/audio/refreshed.wav',
				status: 'success'
			})
		]));
		expect(byId(controller, 'trimmed')).toMatchObject({
			start_ms: 1_400,
			end_ms: 2_600,
			source_start_ms: 400,
			source_end_ms: 1_600,
			audio_path: '/audio/refreshed.wav'
		});

		controller.acknowledgeSave(packet.packetId, draft([
			clip('trimmed', {
				start_ms: 1_400,
				end_ms: 2_600,
				source_start_ms: 400,
				source_end_ms: 1_600,
				audio_path: '/audio/saved.wav',
				status: 'success'
			})
		]));
		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.draft.timeline_clips).toHaveLength(1);
		expect(byId(controller, 'trimmed')).toMatchObject({
			start_ms: 1_400,
			end_ms: 2_600,
			source_start_ms: 400,
			source_end_ms: 1_600,
			audio_path: '/audio/saved.wav'
		});
	});

	it('absorbs page-side metadata changes without turning local timing into the server baseline', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{
				clipId: 'clip-1',
				patch: { start_ms: 1_400, end_ms: 2_400 }
			}]
		});
		controller.synchronizeExternalDraft(draft([
			clip('clip-1', {
				start_ms: 1_400,
				end_ms: 2_400,
				audio_path: '/audio/page-side.wav',
				task_id: 'task-page-side'
			})
		]));

		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_400,
			end_ms: 2_400,
			audio_path: '/audio/page-side.wav',
			task_id: 'task-page-side'
		});
		controller.undo();
		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_000,
			end_ms: 2_000,
			audio_path: '/audio/page-side.wav',
			task_id: 'task-page-side'
		});
	});

	it('keeps pending subtitle collection fields out of the external baseline before compact save', () => {
		const original = draft([clip('clip-1')]);
		original.cues = [cue('cue-1')];
		original.localized_subtitles = [subtitle('subtitle-1')];
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			replaceCues: [cue('cue-1', { start_ms: 1_300, end_ms: 2_300 })],
			replaceLocalizedSubtitles: [subtitle('subtitle-1', { text: '本地新文本' })]
		});
		const pageDraft = controller.draft;
		pageDraft.cues[0] = {
			...pageDraft.cues[0],
			tts_result_id: 'server-result',
			tts_audio_path: '/audio/server.wav'
		};
		controller.synchronizeExternalDraft(pageDraft);

		const request = controller.prepareCompactSave()?.request;
		expect(request?.cue_collection_change?.expected[0]).toMatchObject({
			cue_id: 'cue-1',
			start_ms: 1_000,
			end_ms: 2_000,
			tts_result_id: 'server-result',
			tts_audio_path: '/audio/server.wav'
		});
		expect(request?.cue_collection_change?.desired[0]).toMatchObject({
			cue_id: 'cue-1',
			start_ms: 1_300,
			end_ms: 2_300,
			tts_result_id: 'server-result',
			tts_audio_path: '/audio/server.wav'
		});
		expect(request?.localized_subtitle_collection_change).toMatchObject({
			expected: [{ subtitle_id: 'subtitle-1', text: '你好' }],
			desired: [{ subtitle_id: 'subtitle-1', text: '本地新文本' }]
		});
	});

	it('treats multi-clip and lane-state edits as one undoable transaction', () => {
		const controller = new TimelineEditController(draft([
			clip('clip-a'),
			clip('clip-b', { start_ms: 3_000, end_ms: 4_000, dub_lane: 1 })
		]));

		controller.dispatch({
			type: 'transaction',
			label: '整组跨分轨移动',
			clipPatches: [
				{ clipId: 'clip-a', patch: { start_ms: 1_500, end_ms: 2_500, dub_lane: 1 } },
				{ clipId: 'clip-b', patch: { start_ms: 3_500, end_ms: 4_500, dub_lane: 1 } }
			],
			dubLaneStatePatches: [{
				lane: 1,
				patch: { muted: true, label: '移动后的分轨' }
			}]
		});

		expect(controller.undoCount).toBe(1);
		expect(byId(controller, 'clip-a')).toMatchObject({ start_ms: 1_500, dub_lane: 1 });
		expect(byId(controller, 'clip-b')).toMatchObject({ start_ms: 3_500, dub_lane: 1 });
		expect(laneState(controller, '1')).toMatchObject({ muted: true, label: '移动后的分轨', volume: 0.8 });

		controller.undo();
		expect(controller.redoCount).toBe(1);
		expect(byId(controller, 'clip-a')).toMatchObject({ start_ms: 1_000, dub_lane: 0 });
		expect(byId(controller, 'clip-b')).toMatchObject({ start_ms: 3_000, dub_lane: 1 });
		expect(laneState(controller, '1')).toMatchObject({ muted: false, label: '补充配音', volume: 0.8 });

		controller.redo();
		expect(byId(controller, 'clip-a')).toMatchObject({ start_ms: 1_500, dub_lane: 1 });
		expect(laneState(controller, '1')).toMatchObject({ muted: true, label: '移动后的分轨' });
	});

	it('adds a split child and updates its parent as one transaction', () => {
		const controller = new TimelineEditController(draft([clip('parent')]));
		controller.dispatch({
			type: 'transaction',
			label: '拆分音频片段',
			clipPatches: [{
				clipId: 'parent',
				patch: { end_ms: 1_500, source_end_ms: 500 }
			}],
			addClips: [{
				clip: clip('child', {
					start_ms: 1_500,
					end_ms: 2_000,
					source_start_ms: 500,
					source_end_ms: 1_000
				}),
				index: 1
			}]
		});

		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual(['parent', 'child']);
		expect(byId(controller, 'parent')).toMatchObject({ end_ms: 1_500, source_end_ms: 500 });
		controller.synchronizeExternalDraft(controller.draft);
		const packet = controller.prepareSave();
		expect(packet.dirtyFieldsByClipId.get('parent')).toContain('timing');
		expect(packet.dirtyFieldsByClipId.get('child')).toEqual(new Set(['timing', 'lane']));
		controller.undo();
		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual(['parent']);
		expect(byId(controller, 'parent')).toMatchObject({ end_ms: 2_000, source_end_ms: 1_000 });
	});

	it('keeps every split source slice after refresh, save acknowledgement and reprojection', () => {
		const sourceFields = {
			media_source_clip_id: 'source-candidate',
			candidate_id: 'candidate-1',
			result_id: 'result-1',
			target_subtitle_ids: ['localized-1', 'localized-2'],
			audio_path: '/audio/source-candidate.wav'
		};
		const controller = new TimelineEditController(draft([
			clip('source-candidate', {
				...sourceFields,
				start_ms: 1_000,
				end_ms: 3_000,
				source_start_ms: 0,
				source_end_ms: 2_000
			})
		]));
		controller.dispatch({
			type: 'transaction',
			label: '按气口切分候选',
			clipPatches: [{
				clipId: 'source-candidate',
				patch: { end_ms: 1_800, source_end_ms: 800 }
			}],
			addClips: [{
				clip: clip('source-candidate__part_002', {
					...sourceFields,
					start_ms: 1_920,
					end_ms: 3_000,
					source_start_ms: 920,
					source_end_ms: 2_000
				}),
				index: 1
			}]
		});
		const packet = controller.prepareSave();

		// A stale projection can still contain only the original source clip.
		controller.mergeRefresh(draft([
			clip('source-candidate', {
				...sourceFields,
				start_ms: 1_000,
				end_ms: 3_000,
				source_start_ms: 0,
				source_end_ms: 2_000
			})
		]));
		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual([
			'source-candidate',
			'source-candidate__part_002'
		]);

		const persisted = draft([
			clip('source-candidate', {
				...sourceFields,
				start_ms: 1_000,
				end_ms: 1_800,
				source_start_ms: 0,
				source_end_ms: 800
			}),
			clip('source-candidate__part_002', {
				...sourceFields,
				start_ms: 1_920,
				end_ms: 3_000,
				source_start_ms: 920,
				source_end_ms: 2_000
			})
		]);
		controller.acknowledgeSave(packet.packetId, persisted);
		controller.mergeRefresh(persisted);

		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.draft.timeline_clips).toMatchObject([
			{
				clip_id: 'source-candidate',
				media_source_clip_id: 'source-candidate',
				target_subtitle_ids: ['localized-1', 'localized-2'],
				source_start_ms: 0,
				source_end_ms: 800
			},
			{
				clip_id: 'source-candidate__part_002',
				media_source_clip_id: 'source-candidate',
				target_subtitle_ids: ['localized-1', 'localized-2'],
				source_start_ms: 920,
				source_end_ms: 2_000
			}
		]);
	});

	it('keeps delete tombstones across refresh and removes them on undo', () => {
		const controller = new TimelineEditController(draft([clip('clip-delete')]));

		controller.dispatch({
			type: 'transaction',
			deleteClipIds: ['clip-delete'],
			uiStatePatch: {
				disabled_media_tracks: ['original'],
				discarded_tts_task_ids: ['task-clip-delete']
			}
		});
		expect(controller.draft.timeline_clips).toEqual([]);
		expect(controller.deletedTimelineClipIds).toEqual(new Set(['clip-delete']));
		expect(controller.draft.ui_state.disabled_media_tracks).toEqual(['original']);
		expect(controller.draft.ui_state.discarded_tts_task_ids).toEqual(['task-clip-delete']);

		controller.mergeRefresh(draft([
			clip('clip-delete', { audio_path: '/audio/late.wav', task_id: 'task-clip-delete' })
		]));
		expect(controller.draft.timeline_clips).toEqual([]);
		expect(controller.deletedTimelineClipIds).toEqual(new Set(['clip-delete']));

		controller.undo();
		expect(byId(controller, 'clip-delete')).toBeTruthy();
		expect(controller.deletedTimelineClipIds).toEqual(new Set());
		expect(controller.draft.ui_state.disabled_media_tracks).toBeUndefined();
		expect(controller.draft.ui_state.discarded_tts_task_ids).toBeUndefined();
	});

	it('drops a stale deletion when the same clip id now belongs to a new generation', () => {
		const controller = new TimelineEditController(draft([clip('clip-delete')]));
		controller.dispatch({
			type: 'transaction',
			deleteClipIds: ['clip-delete'],
			uiStatePatch: { discarded_tts_task_ids: ['task-clip-delete'] }
		});

		controller.mergeRefresh(draft([
			clip('clip-delete', {
				task_id: 'task-new',
				result_id: 'result-new',
				generation_identity: 'result-new'
			})
		]));

		expect(byId(controller, 'clip-delete')).toMatchObject({
			task_id: 'task-new',
			result_id: 'result-new'
		});
		expect(controller.deletedTimelineClipIds).toEqual(new Set());
		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.undoCount).toBe(0);
	});

	it('does not undo a saved split against a child replaced through a history mutation', () => {
		const controller = new TimelineEditController(draft([clip('parent')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'parent', patch: { end_ms: 1_500, source_end_ms: 500 } }],
			addClips: [{ clip: clip('child', {
				media_source_clip_id: 'parent', start_ms: 1_500, source_start_ms: 500
			}) }]
		});
		const packet = controller.prepareCompactSave()!;
		controller.acknowledgeCompactSave(packet.packetId, compactReceipt(controller));
		const replacement = clip('child', {
			generation_identity: 'task-new', task_id: 'task-new',
			audio_path: '/audio/new.wav', start_ms: 1_500
		});
		const latest = mergeTimelineMutation(controller.draft, {
			schema_version: 'video-localization-timeline-mutation-v1', revision: '2', updated_at: null,
			affected_clip_ids: ['child'], timeline_clips: [replacement],
			cues: [], localized_subtitles: [], dub_lane_states: {}, discarded_tts_task_ids: []
		});
		// Same entry point as applyTimelineMutationResult, followed by a poll.
		controller.synchronizeExternalDraft(latest);
		controller.mergeRefresh(latest);

		expect(controller.undo()).toMatchObject({ status: 'noop' });
		expect(byId(controller, 'child')).toEqual(replacement);
		expect(controller.prepareCompactSave()).toBeNull();
	});

	it('drops old pending edits on a history replacement but preserves independent work', () => {
		const controller = new TimelineEditController(draft([clip('old'), clip('other')]));
		controller.dispatch({ type: 'transaction', deleteClipIds: ['old'] });
		controller.dispatch({
			type: 'transaction', clipPatches: [{ clipId: 'other', patch: { start_ms: 1_100 } }]
		});
		controller.synchronizeExternalDraft(draft([
			clip('old', {
				generation_identity: 'task-new', task_id: 'task-new',
				start_ms: 1_300, end_ms: 2_600, source_end_ms: 1_300
			}),
			clip('other'), clip('server-added')
		]));

		expect(byId(controller, 'old')).toMatchObject({
			generation_identity: 'task-new', start_ms: 1_300, end_ms: 2_600, source_end_ms: 1_300
		});
		expect(byId(controller, 'other')?.start_ms).toBe(1_100);
		expect(byId(controller, 'server-added')).toBeTruthy();
		expect(controller.prepareCompactSave()?.request.deleted_clips).toEqual([]);
		expect(controller.undoCount).toBe(0);
	});

	it.each(['save', 'persisted'] as const)('invalidates old undo history before adopting a %s draft with new media', (entry) => {
		const controller = new TimelineEditController(draft([clip('old')]));
		controller.dispatch({
			type: 'transaction', clipPatches: [{ clipId: 'old', patch: { start_ms: 1_100 } }]
		});
		const latest = draft([clip('old', { generation_identity: 'task-new' }), clip('server-added')]);
		if (entry === 'save') {
			const packet = controller.prepareSave();
			controller.acknowledgeSave(packet.packetId, latest);
		} else {
			controller.adoptPersisted({ type: 'transaction' }, latest);
		}

		expect(controller.undo()).toMatchObject({ status: 'noop' });
		expect(byId(controller, 'old')?.start_ms).toBe(1_000);
		expect(byId(controller, 'server-added')).toBeTruthy();
		expect(controller.hasPendingChanges).toBe(false);
	});

	it('keeps saved split undo after same-generation external synchronization and an unrelated new clip', () => {
		const controller = new TimelineEditController(draft([clip('parent')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'parent', patch: { end_ms: 1_500, source_end_ms: 500 } }],
			addClips: [{ clip: clip('child', {
				media_source_clip_id: 'parent', start_ms: 1_500, source_start_ms: 500
			}) }]
		});
		const packet = controller.prepareCompactSave()!;
		controller.acknowledgeCompactSave(packet.packetId, compactReceipt(controller));
		controller.synchronizeExternalDraft(draft([...controller.draft.timeline_clips, clip('server-added')]));

		expect(controller.undo()).toMatchObject({ status: 'applied' });
		expect(byId(controller, 'parent')?.end_ms).toBe(2_000);
		expect(byId(controller, 'child')).toBeUndefined();
		expect(byId(controller, 'server-added')).toBeTruthy();
		expect(controller.prepareCompactSave()?.request.deleted_clips).toEqual([
			{
				clip_id: 'child',
				expected_generation_identity: 'task-child',
				expected_editable_fields: {
					start_ms: 1_500,
					end_ms: 2_000,
					source_start_ms: 500,
					source_end_ms: 1_000,
					media_source_clip_id: 'parent',
					dub_lane: 0
				}
			}
		]);
	});

	it('does not re-add the persisted command history when that command touches replaced media', () => {
		const controller = new TimelineEditController(draft([clip('old')]));
		const latest = draft([clip('old', { generation_identity: 'task-new', start_ms: 1_200 })]);
		const result = controller.adoptPersisted({
			type: 'transaction', clipPatches: [{ clipId: 'old', patch: { start_ms: 1_200 } }]
		}, latest);

		expect(result.status).toBe('noop');
		expect(controller.undo()).toMatchObject({ status: 'noop' });
		expect(byId(controller, 'old')).toEqual(latest.timeline_clips[0]);
	});

	it('still records an independent persisted subtitle edit when unrelated media was replaced', () => {
		const original = draft([clip('old')]);
		original.localized_subtitles = [subtitle('localized-1')];
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction', clipPatches: [{ clipId: 'old', patch: { start_ms: 1_100 } }]
		});
		const latest = draft([clip('old', { generation_identity: 'task-new' })]);
		latest.localized_subtitles = [subtitle('localized-1', { text: '已经保存' })];
		controller.adoptPersisted({
			type: 'transaction', replaceLocalizedSubtitles: latest.localized_subtitles
		}, latest);

		expect(controller.undoCount).toBe(1);
		controller.undo();
		expect(controller.draft.localized_subtitles[0].text).toBe('你好');
		expect(byId(controller, 'old')).toEqual(latest.timeline_clips[0]);
	});

	it('limits undo and redo history to thirty transactions', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));

		for (let index = 1; index <= 35; index += 1) {
			controller.dispatch({
				type: 'transaction',
				clipPatches: [{
					clipId: 'clip-1',
					patch: { start_ms: 1_000 + index, end_ms: 2_000 + index }
				}]
			});
		}
		expect(controller.undoCount).toBe(30);

		for (let index = 0; index < 30; index += 1) controller.undo();
		expect(controller.undoCount).toBe(0);
		expect(controller.redoCount).toBe(30);
		expect(byId(controller, 'clip-1')).toMatchObject({ start_ms: 1_005, end_ms: 2_005 });

		for (let index = 0; index < 30; index += 1) controller.redo();
		expect(byId(controller, 'clip-1')).toMatchObject({ start_ms: 1_035, end_ms: 2_035 });
	});

	it('retains undo history after save acknowledgement and turns a later undo into pending work', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_500, end_ms: 2_500 } }]
		});
		const packet = controller.prepareSave();

		expect(packet.hasChanges).toBe(true);
		expect(packet.deletedClipIds).toEqual(new Set());
		expect(packet.dirtyFieldsByClipId.get('clip-1')).toEqual(new Set(['timing']));

		controller.acknowledgeSave(packet.packetId, draft([
			clip('clip-1', {
				start_ms: 1_500,
				end_ms: 2_500,
				audio_path: '/audio/saved-new.wav',
				task_id: 'task-clip-1'
			})
		]));

		expect(controller.undoCount).toBe(1);
		expect(controller.hasPendingChanges).toBe(false);
		controller.undo();
		expect(controller.hasPendingChanges).toBe(true);
		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_000,
			end_ms: 2_000,
			audio_path: '/audio/saved-new.wav',
			task_id: 'task-clip-1'
		});

		controller.mergeRefresh(draft([
			clip('clip-1', {
				start_ms: 1_500,
				end_ms: 2_500,
				audio_path: '/audio/refreshed-again.wav',
				task_id: 'task-clip-1'
			})
		]));
		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_000,
			end_ms: 2_000,
			audio_path: '/audio/refreshed-again.wav',
			task_id: 'task-clip-1'
		});
	});

	it('settles an unsent edit and undo that returns exactly to the persisted baseline', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_400, end_ms: 2_400 } }]
		});
		controller.undo();

		expect(controller.hasPendingChanges).toBe(true);
		expect(controller.prepareCompactSave()).toBeNull();
		expect(controller.settlePendingNoop()).toBe(true);
		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.redoCount).toBe(1);

		controller.redo();
		expect(controller.prepareCompactSave()?.request.clip_patches).toMatchObject([{
			clip_id: 'clip-1',
			start_ms: 1_400,
			end_ms: 2_400
		}]);
	});

	it('does not write a touched dub lane when its net state returned to the baseline', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			dubLaneStatePatches: [{ lane: 1, patch: { muted: true } }]
		});
		controller.undo();

		expect(controller.prepareCompactSave()).toBeNull();
		expect(controller.settlePendingNoop()).toBe(true);
		expect(controller.hasPendingChanges).toBe(false);
	});

	it('settles pending work already present in an authoritative refresh without losing undo history', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_400, end_ms: 2_400 } }]
		});
		controller.mergeRefresh(draft([
			clip('clip-1', { start_ms: 1_400, end_ms: 2_400 })
		]));

		expect(controller.prepareCompactSave()).toBeNull();
		expect(controller.settlePendingNoop()).toBe(true);
		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.undoCount).toBe(1);
		controller.undo();
		expect(controller.prepareCompactSave()?.request.clip_patches).toMatchObject([{
			clip_id: 'clip-1',
			start_ms: 1_000,
			end_ms: 2_000
		}]);
	});

	it('does not settle an edit undone after its save packet was sent', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_400, end_ms: 2_400 } }]
		});
		const packet = controller.prepareCompactSave()!;
		controller.undo();

		expect(controller.prepareCompactSave()).toBeNull();
		expect(controller.settlePendingNoop()).toBe(false);
		expect(controller.hasPendingChanges).toBe(true);

		controller.acknowledgeCompactSave(packet.packetId, compactReceipt(controller, {
			timeline_clips: [clip('clip-1', { start_ms: 1_400, end_ms: 2_400 })]
		}));
		expect(controller.hasPendingChanges).toBe(true);
		expect(controller.prepareCompactSave()?.request.clip_patches).toMatchObject([{
			clip_id: 'clip-1',
			start_ms: 1_000,
			end_ms: 2_000
		}]);
	});

	it('does not settle controller-owned clip changes unsupported by the compact request', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			replaceTimelineClips: [clip('clip-1', { track_id: 'vocals' })]
		});

		expect(controller.prepareCompactSave()).toBeNull();
		expect(controller.settlePendingNoop()).toBe(false);
		expect(controller.hasPendingChanges).toBe(true);
	});

	it('reports clips added by a pending split in the save packet', () => {
		const controller = new TimelineEditController(draft([clip('parent')]));
		controller.dispatch({
			type: 'transaction',
			addClips: [{
				clip: clip('child', {
					start_ms: 1_500,
					end_ms: 2_000,
					media_source_clip_id: 'parent'
				}),
				index: 1
			}]
		});

		const packet = controller.prepareSave();

		expect(packet.addedClipIds).toEqual(new Set(['child']));
	});

	it('persists a split through the bounded timeline command instead of a full draft save', () => {
		const controller = new TimelineEditController(draft([clip('parent', {
			start_ms: 1_000,
			end_ms: 3_000,
			source_start_ms: 0,
			source_end_ms: 2_000
		})]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{
				clipId: 'parent',
				patch: { end_ms: 2_000, source_end_ms: 1_000 }
			}],
			addClips: [{
				clip: clip('child', {
					media_source_clip_id: 'parent',
					start_ms: 2_000,
					end_ms: 3_000,
					source_start_ms: 1_000,
					source_end_ms: 2_000
				}),
				index: 1
			}]
		});

		const packet = controller.prepareCompactSave();

		expect(packet?.request).toMatchObject({
			clip_patches: [{ clip_id: 'parent', end_ms: 2_000, source_end_ms: 1_000 }],
			added_clips: [{
				clip_id: 'child',
				media_source_clip_id: 'parent',
				start_ms: 2_000,
				end_ms: 3_000,
				source_start_ms: 1_000,
				source_end_ms: 2_000
			}],
			deleted_clips: []
		});
	});

	it('acknowledges only edits included in a save packet and replays later transactions', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_200, end_ms: 2_200 } }]
		});
		const packet = controller.prepareSave();
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_600, end_ms: 2_600 } }]
		});

		controller.acknowledgeSave(packet.packetId, draft([
			clip('clip-1', {
				start_ms: 1_200,
				end_ms: 2_200,
				audio_path: '/audio/from-save.wav',
				task_id: 'task-from-save'
			})
		]));

		expect(controller.hasPendingChanges).toBe(true);
		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_600,
			end_ms: 2_600,
			audio_path: '/audio/from-save.wav',
			task_id: 'task-from-save'
		});
	});

	it('replays only touched dub lane state fields over a server refresh', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			dubLaneStatePatches: [{ lane: 1, patch: { muted: true } }]
		});

		controller.mergeRefresh(draft([clip('clip-1')], {
			'0': { muted: false, solo: false, volume: 1, label: '主配音' },
			'1': { muted: false, solo: true, volume: 0.45, label: '服务端新名称' }
		}));

		expect(laneState(controller, '1')).toEqual({
			muted: true,
			solo: true,
			volume: 0.45,
			label: '服务端新名称'
		});
		const packet = controller.prepareSave();
		expect(packet.dirtyDubLaneStateKeys).toEqual(new Set(['1']));
	});

	it('absorbs page-side lane fields that are not owned by a pending timeline edit', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			dubLaneStatePatches: [{ lane: 1, patch: { label: '重排后的分轨' } }]
		});

		controller.synchronizeExternalDraft(draft([clip('clip-1')], {
			'0': { muted: false, solo: false, volume: 1, label: '主配音' },
			'1': { muted: true, solo: false, volume: 0.35, label: '重排后的分轨' }
		}));

		expect(laneState(controller, '1')).toEqual({
			muted: true,
			solo: false,
			volume: 0.35,
			label: '重排后的分轨'
		});
		controller.undo();
		expect(laneState(controller, '1')).toEqual({
			muted: true,
			solo: false,
			volume: 0.35,
			label: '补充配音'
		});
	});

	it('preserves concurrent page controls across the full server merge sequence', () => {
		const original = draft([clip('clip-1')]);
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			dubLaneStatePatches: [{ lane: 1, patch: { label: '重排后的分轨' } }]
		});
		const local = controller.draft;
		local.ui_state.dub_lane_states = {
			...(local.ui_state.dub_lane_states as Record<string, Record<string, unknown>>),
			'1': {
				...laneState(controller, '1'),
				muted: true,
				volume: 0.35
			}
		};
		const server = draft([
			clip('clip-1', { audio_path: '/audio/fresh.wav', status: 'success' })
		]);
		const merged = mergeDraftAfterConflict(server, local);

		controller.mergeRefresh(server);
		controller.synchronizeExternalDraft(merged);

		expect(laneState(controller, '1')).toEqual({
			muted: true,
			solo: false,
			volume: 0.35,
			label: '重排后的分轨'
		});
		expect(byId(controller, 'clip-1')).toMatchObject({
			audio_path: '/audio/fresh.wav',
			status: 'success'
		});
	});

	it('does not create history for a no-op transaction', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		const result = controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_000, end_ms: 2_000 } }]
		});

		expect(result.status).toBe('noop');
		expect(controller.undoCount).toBe(0);
		expect(controller.hasPendingChanges).toBe(false);
	});

	it('treats cue, localized subtitle, and clip collection changes as one history entry', () => {
		const original = draft([clip('clip-1')]);
		original.cues = [cue('cue-1')];
		original.localized_subtitles = [subtitle('localized-1')];
		const controller = new TimelineEditController(original);

		controller.dispatch({
			type: 'transaction',
			label: '拆分字幕及关联片段',
			replaceCues: [
				cue('cue-1', { end_ms: 1_500, source_duration_ms: 500 }),
				cue('cue-2', { start_ms: 1_500, en_subtitle_text: 'world' })
			],
			replaceLocalizedSubtitles: [
				subtitle('localized-1', { end_ms: 1_500 }),
				subtitle('localized-2', { start_ms: 1_500, text: '世界' })
			],
			replaceTimelineClips: [
				clip('clip-1', { end_ms: 1_500, source_end_ms: 500 }),
				clip('clip-2', { start_ms: 1_500, source_start_ms: 500 })
			]
		});

		expect(controller.undoCount).toBe(1);
		expect(controller.draft.cues.map((item) => item.cue_id)).toEqual(['cue-1', 'cue-2']);
		expect(controller.draft.localized_subtitles.map((item) => item.subtitle_id)).toEqual([
			'localized-1',
			'localized-2'
		]);
		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual(['clip-1', 'clip-2']);

		controller.undo();
		expect(controller.draft.cues.map((item) => item.cue_id)).toEqual(['cue-1']);
		expect(controller.draft.localized_subtitles.map((item) => item.subtitle_id)).toEqual(['localized-1']);
		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual(['clip-1']);

		controller.redo();
		expect(controller.draft.cues.map((item) => item.cue_id)).toEqual(['cue-1', 'cue-2']);
		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual(['clip-1', 'clip-2']);
	});

	it('preserves newer runtime results when undoing a subtitle edit after refresh', () => {
		const original = draft([]);
		original.cues = [cue('cue-1')];
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			replaceCues: [cue('cue-1', { start_ms: 1_400, end_ms: 2_400 })]
		});

		const refreshed = draft([]);
		refreshed.cues = [cue('cue-1', {
			start_ms: 1_000,
			end_ms: 2_000,
			en_subtitle_text: 'server corrected text',
			tts_result_id: 'result-new',
			tts_audio_path: '/audio/new.wav'
		})];
		controller.mergeRefresh(refreshed);
		expect(controller.draft.cues[0]).toMatchObject({
			start_ms: 1_400,
			end_ms: 2_400,
			en_subtitle_text: 'server corrected text',
			tts_result_id: 'result-new',
			tts_audio_path: '/audio/new.wav'
		});

		controller.undo();
		expect(controller.draft.cues[0]).toMatchObject({
			start_ms: 1_000,
			end_ms: 2_000,
			en_subtitle_text: 'server corrected text',
			tts_result_id: 'result-new',
			tts_audio_path: '/audio/new.wav'
		});
	});

	it('replays a pending cue edit without removing a cue added by a server refresh', () => {
		const original = draft([]);
		original.cues = [cue('cue-1')];
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			replaceCues: [cue('cue-1', { start_ms: 1_400, end_ms: 2_400 })]
		});

		const refreshed = draft([]);
		refreshed.cues = [
			cue('cue-1'),
			cue('cue-server', { start_ms: 3_000, end_ms: 4_000 })
		];
		controller.mergeRefresh(refreshed);

		expect(controller.draft.cues.map((item) => item.cue_id)).toEqual(['cue-1', 'cue-server']);
		expect(controller.draft.cues[0]).toMatchObject({ start_ms: 1_400, end_ms: 2_400 });
	});

	it('inserts a pending split child relative to its sibling across a server refresh', () => {
		const original = draft([]);
		original.cues = [
			cue('cue-1'),
			cue('cue-2', { start_ms: 3_000, end_ms: 4_000 })
		];
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			replaceCues: [
				cue('cue-1', { end_ms: 1_500 }),
				cue('cue-child', { start_ms: 1_500, end_ms: 2_000 }),
				cue('cue-2', { start_ms: 3_000, end_ms: 4_000 })
			]
		});

		const refreshed = draft([]);
		refreshed.cues = [
			cue('cue-server', { start_ms: 0, end_ms: 500 }),
			cue('cue-1'),
			cue('cue-2', { start_ms: 3_000, end_ms: 4_000 })
		];
		controller.mergeRefresh(refreshed);

		expect(controller.draft.cues.map((item) => item.cue_id)).toEqual([
			'cue-server',
			'cue-1',
			'cue-child',
			'cue-2'
		]);
	});

	it('keeps refreshed clip task metadata while replaying a collection timing edit', () => {
		const controller = new TimelineEditController(draft([
			clip('clip-1', { result_id: 'result-old' })
		]));
		controller.dispatch({
			type: 'transaction',
			replaceTimelineClips: [
				clip('clip-1', {
					start_ms: 1_400,
					end_ms: 2_400,
					result_id: 'result-old'
				})
			]
		});
		controller.mergeRefresh(draft([
			clip('clip-1', {
				result_id: 'result-new',
				task_id: 'task-clip-1',
				audio_path: '/audio/new.wav'
			}),
			clip('clip-server', { start_ms: 3_000, end_ms: 4_000 })
		]));

		expect(byId(controller, 'clip-1')).toMatchObject({
			start_ms: 1_400,
			end_ms: 2_400,
			result_id: 'result-new',
			task_id: 'task-clip-1',
			audio_path: '/audio/new.wav'
		});
		expect(byId(controller, 'clip-server')).toBeTruthy();
	});

	it('records an already-persisted timeline edit without adding pending save work', () => {
		const original = draft([]);
		original.localized_subtitles = [subtitle('localized-1')];
		const controller = new TimelineEditController(original);
		const persisted = draft([]);
		persisted.localized_subtitles = [subtitle('localized-1', { text: '已经保存' })];

		const result = controller.adoptPersisted({
			type: 'transaction',
			label: '编辑本土化字幕',
			replaceLocalizedSubtitles: persisted.localized_subtitles
		}, persisted);

		expect(result.status).toBe('applied');
		expect(controller.undoCount).toBe(1);
		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.draft.localized_subtitles[0].text).toBe('已经保存');

		controller.undo();
		expect(controller.hasPendingChanges).toBe(true);
		expect(controller.draft.localized_subtitles[0].text).toBe('你好');
	});

	it('adopts a persisted subtitle edit without acknowledging earlier pending clip work', () => {
		const original = draft([clip('clip-1')]);
		original.localized_subtitles = [subtitle('localized-1')];
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{
				clipId: 'clip-1',
				patch: { start_ms: 1_400, end_ms: 2_400 }
			}]
		});
		const persisted = draft([clip('clip-1')]);
		persisted.localized_subtitles = [subtitle('localized-1', { text: '已经保存' })];

		controller.adoptPersisted({
			type: 'transaction',
			replaceLocalizedSubtitles: persisted.localized_subtitles
		}, persisted);

		expect(controller.hasPendingChanges).toBe(true);
		expect(byId(controller, 'clip-1')).toMatchObject({ start_ms: 1_400, end_ms: 2_400 });
		expect(controller.draft.localized_subtitles[0].text).toBe('已经保存');
		const packet = controller.prepareSave();
		expect(packet.dirtyFieldsByClipId.get('clip-1')).toEqual(new Set(['timing']));
	});

	it('prepares a compact save for clip timing and lane edits', () => {
		const original = draft([clip('clip-1')]);
		original.updated_at = '2026-08-30T10:00:00';
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{
				clipId: 'clip-1',
				patch: { start_ms: 1_400, end_ms: 2_400, dub_lane: 1 }
			}],
			dubLaneStatePatches: [{ lane: 1, patch: { muted: true } }]
		});

		const packet = controller.prepareCompactSave();

		expect(packet?.request).toEqual({
			schema_version: 'timeline-edit-patch-v2',
			request_id: expect.any(String),
			added_clips: [],
			ui_state_patch: {},
			clip_patches: [{
				clip_id: 'clip-1',
				expected_generation_identity: 'task-clip-1',
				expected_editable_fields: {
					start_ms: 1_000,
					end_ms: 2_000,
					source_start_ms: 0,
					source_end_ms: 1_000,
					media_source_clip_id: null,
					dub_lane: 0
				},
				start_ms: 1_400,
				end_ms: 2_400,
				dub_lane: 1
			}],
			dub_lane_state_patches: [{
				lane: 1,
				muted: true,
				solo: false,
				volume: 0.8,
				label: '补充配音'
			}],
			deleted_clips: []
		});
	});

	it('compacts exact clip deletion without replacing the draft', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			deleteClipIds: ['clip-1']
		});

		expect(controller.prepareCompactSave()?.request).toMatchObject({
			clip_patches: [],
			added_clips: [],
			deleted_clips: [{
				clip_id: 'clip-1',
				expected_generation_identity: 'task-clip-1'
			}]
		});
	});

	it('keeps clip deletion on the typed save path when deletion also updates timeline UI state', () => {
		const controller = new TimelineEditController(draft([clip('clip-delete')]));
		controller.dispatch({
			type: 'transaction',
			deleteClipIds: ['clip-delete'],
			uiStatePatch: {
				disabled_media_tracks: ['original'],
				discarded_tts_task_ids: ['task-clip-delete']
			}
		});

		expect(controller.prepareCompactSave()?.request).toMatchObject({
			deleted_clips: [{
				clip_id: 'clip-delete',
				expected_generation_identity: 'task-clip-delete'
			}],
			ui_state_patch: {
				disabled_media_tracks: ['original'],
				discarded_tts_task_ids: ['task-clip-delete']
			}
		});
	});

	it('keeps an explicit empty discarded-task decision with clip deletion', () => {
		const initial = draft([clip('clip-delete')]);
		initial.ui_state.discarded_tts_task_ids = [];
		const controller = new TimelineEditController(initial);
		controller.dispatch({ type: 'transaction', deleteClipIds: ['clip-delete'] });

		expect(controller.prepareCompactSave()?.request.ui_state_patch).toMatchObject({
			discarded_tts_task_ids: []
		});
	});

	it('converts a structural clip replacement into one typed delta', () => {
		const controller = new TimelineEditController(draft([clip('parent'), clip('remove')]));
		controller.dispatch({
			type: 'transaction',
			replaceTimelineClips: [
				clip('parent', { end_ms: 1_500, source_end_ms: 500 }),
				clip('child', {
					media_source_clip_id: 'parent',
					start_ms: 1_500,
					end_ms: 2_000,
					source_start_ms: 500,
					source_end_ms: 1_000
				})
			]
		});

		expect(controller.prepareCompactSave()?.request).toMatchObject({
			clip_patches: [{
				clip_id: 'parent',
				expected_generation_identity: 'task-parent',
				end_ms: 1_500,
				source_end_ms: 500
			}],
			added_clips: [{ clip_id: 'child', media_source_clip_id: 'parent' }],
			deleted_clips: [{
				clip_id: 'remove',
				expected_generation_identity: 'task-remove'
			}]
		});
	});

	it('retries an uncertain save unchanged before saving edits made while offline', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({ type: 'transaction', clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_200 } }] });
		const packet = controller.prepareCompactSave()!;
		const receipt = compactReceipt(controller);
		expect(packet.request.request_id).toBeTruthy();
		controller.retainCompactSaveForRetry(packet);
		controller.dispatch({ type: 'transaction', clipPatches: [{ clipId: 'clip-1', patch: { end_ms: 2_500 } }] });
		expect(controller.prepareCompactSave()).toEqual(packet);
		controller.acknowledgeCompactSave(packet.packetId, receipt);
		const next = controller.prepareCompactSave()!;
		expect(next.request.request_id).not.toBe(packet.request.request_id);
		expect(next.request.clip_patches).toMatchObject([{
			clip_id: 'clip-1', end_ms: 2_500, expected_editable_fields: { start_ms: 1_200 }
		}]);
		expect(next.request.clip_patches[0]).not.toHaveProperty('start_ms');
	});

	it('keeps an uncertain save through undo and clears its retry only after a definite response', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({ type: 'transaction', clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_200 } }] });
		const packet = controller.prepareCompactSave()!;
		const receipt = compactReceipt(controller);
		controller.retainCompactSaveForRetry(packet);
		controller.undo();
		expect(controller.settlePendingNoop()).toBe(false);
		expect(controller.prepareCompactSave()).toEqual(packet);
		controller.acknowledgeCompactSave(packet.packetId, receipt);
		expect(controller.prepareCompactSave()!.request.clip_patches).toMatchObject([{
			start_ms: 1_000, expected_editable_fields: { start_ms: 1_200 }
		}]);
		const inverse = controller.prepareCompactSave()!;
		controller.retainCompactSaveForRetry(inverse);
		controller.rejectSave(inverse.packetId);
		expect(controller.prepareCompactSave()!.request.request_id).not.toBe(inverse.request.request_id);
	});

	it('confirms an old replay receipt without overlaying a newer refreshed baseline', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_200 } }]
		});
		const packet = controller.prepareCompactSave()!;
		const oldReceipt = compactReceipt(controller);
		controller.retainCompactSaveForRetry(packet);

		controller.mergeRefresh(draft([
			clip('clip-1', { start_ms: 1_350 }),
			clip('remote', { start_ms: 4_000, end_ms: 5_000 })
		]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { end_ms: 2_600 } }]
		});
		controller.acknowledgeCompactSave(packet.packetId, oldReceipt, {
			applyReceipt: false
		});

		expect(byId(controller, 'clip-1')).toMatchObject({ start_ms: 1_350, end_ms: 2_600 });
		expect(byId(controller, 'remote')).toBeTruthy();
		expect(controller.hasPendingChanges).toBe(true);
		expect(controller.prepareCompactSave()?.request.clip_patches).toMatchObject([{
			clip_id: 'clip-1',
			expected_editable_fields: { start_ms: 1_350 },
			end_ms: 2_600
		}]);
	});

	it('acknowledges a compact save without cloning a server draft', () => {
		const controller = new TimelineEditController(draft([clip('clip-1')]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_400, end_ms: 2_400 } }]
		});
		const packet = controller.prepareCompactSave();
		expect(packet).not.toBeNull();

		controller.acknowledgeCompactSave(packet!.packetId, compactReceipt(controller, {
			updated_at: '2026-08-30T10:01:00'
		}));

		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.draft.updated_at).toBe('2026-08-30T10:01:00');
		expect(byId(controller, 'clip-1')).toMatchObject({ start_ms: 1_400, end_ms: 2_400 });
	});

	it('adopts authoritative server normalization and replays only edits made after the checkpoint', () => {
		const controller = new TimelineEditController(draft([
			clip('clip-1'),
			clip('unrelated', { start_ms: 4_000, end_ms: 5_000 })
		]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_400 } }]
		});
		const packet = controller.prepareCompactSave()!;
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { end_ms: 2_500 } }]
		});
		const authoritativeClip = clip('clip-1', {
			generation_identity: 'timeline-edit:server-child',
			start_ms: 1_420,
			end_ms: 2_420
		});

		controller.acknowledgeCompactSave(packet.packetId, compactReceipt(controller, {
			timeline_clips: [authoritativeClip]
		}));

		expect(byId(controller, 'clip-1')).toMatchObject({
			generation_identity: 'timeline-edit:server-child',
			start_ms: 1_420,
			end_ms: 2_500
		});
		expect(byId(controller, 'unrelated')).toMatchObject({ start_ms: 4_000, end_ms: 5_000 });
		expect(controller.hasPendingChanges).toBe(true);
		expect(controller.prepareCompactSave()?.request.clip_patches).toMatchObject([{
			clip_id: 'clip-1',
			expected_generation_identity: 'timeline-edit:server-child',
			end_ms: 2_500
		}]);
	});

	it('uses the server identity when undoing a saved split child', () => {
		const controller = new TimelineEditController(draft([clip('parent')]));
		const child = clip('child', {
			media_source_clip_id: 'parent',
			start_ms: 1_500,
			end_ms: 2_000,
			source_start_ms: 500,
			source_end_ms: 1_000
		});
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'parent', patch: { end_ms: 1_500, source_end_ms: 500 } }],
			addClips: [{ clip: child }]
		});
		const packet = controller.prepareCompactSave()!;
		controller.acknowledgeCompactSave(packet.packetId, compactReceipt(controller, {
			timeline_clips: [
				clip('parent', { end_ms: 1_500, source_end_ms: 500 }),
				{ ...child, generation_identity: 'timeline-edit:child-v1' }
			]
		}));

		expect(controller.undo()).toMatchObject({ status: 'applied' });
		expect(controller.prepareCompactSave()?.request.deleted_clips).toMatchObject([{
			clip_id: 'child',
			expected_generation_identity: 'timeline-edit:child-v1'
		}]);
	});

	it('drops only the stale transaction for a same-media edit conflict', () => {
		const controller = new TimelineEditController(draft([
			clip('conflict'),
			clip('unrelated', { start_ms: 3_000, end_ms: 4_000 })
		]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'conflict', patch: { end_ms: 1_800 } }]
		});
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'unrelated', patch: { start_ms: 3_200 } }]
		});

		expect(controller.discardConflictingClipEdits(new Set(['conflict']))).toEqual(new Set(['conflict']));
		expect(byId(controller, 'conflict')?.end_ms).toBe(2_000);
		expect(byId(controller, 'unrelated')?.start_ms).toBe(3_200);
		expect(controller.hasPendingChanges).toBe(true);
		expect(controller.undoCount).toBe(0);
	});

	it('saves subtitle collections through the same compact transaction', () => {
		const original = draft([clip('clip-1')]);
		original.localized_subtitles = [subtitle('localized-1')];
		const controller = new TimelineEditController(original);
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'clip-1', patch: { start_ms: 1_400 } }],
			replaceLocalizedSubtitles: [subtitle('localized-1', { text: '待保存字幕' })]
		});

		const packet = controller.prepareCompactSave();
		expect(packet).not.toBeNull();
		expect(packet!.request.localized_subtitle_collection_change).toEqual({
			expected: [subtitle('localized-1')],
			desired: [subtitle('localized-1', { text: '待保存字幕' })]
		});
		controller.acknowledgeCompactSave(packet!.packetId, compactReceipt(controller, {
			localized_subtitles: [subtitle('localized-1', { text: '服务端确认字幕', review_status: 'ready' })]
		}));

		expect(byId(controller, 'clip-1')?.start_ms).toBe(1_400);
		expect(controller.draft.localized_subtitles[0].text).toBe('服务端确认字幕');
		expect(controller.hasPendingChanges).toBe(false);
		expect(controller.prepareCompactSave()).toBeNull();
	});

	it('never adopts runtime placeholders into editable state or deletion intent', () => {
		const pending = clip('pending-1', {
			audio_path: null,
			status: 'running',
			optimistic_tts_workflow_id: 'workflow-1'
		});
		const controller = new TimelineEditController(draft([clip('stable-1'), pending]));

		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual(['stable-1']);
		expect(controller.deletedTimelineClipIds).toEqual(new Set());
		expect(controller.prepareCompactSave()).toBeNull();
	});

	it('ignores runtime overlays in a mixed replacement while preserving durable edits', () => {
		const controller = new TimelineEditController(draft([clip('stable-1')]));
		controller.dispatch({
			type: 'transaction',
			replaceTimelineClips: [
				clip('stable-1', { start_ms: 1_200, end_ms: 2_200 }),
				clip('pending-1', {
					audio_path: null,
					status: 'applying',
					optimistic_history_result_id: 'history-1'
				})
			]
		});

		expect(controller.draft.timeline_clips.map((item) => item.clip_id)).toEqual(['stable-1']);
		expect(byId(controller, 'stable-1')).toMatchObject({ start_ms: 1_200, end_ms: 2_200 });
		expect(controller.deletedTimelineClipIds).toEqual(new Set());
	});
});
