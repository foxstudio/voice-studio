import { describe, expect, it } from 'vitest';
import { TimelineEditController } from './timeline-edit-controller';
import { mergeTimelineMutation } from './timeline-history-command';
import { WorkspaceRevisionController } from './workspace-revision-controller';
import { HistoryPlacementSessionController } from './history-placement-session-controller';
import { ApiError } from '$lib/api/client';
import type { VideoLocalizationDraft, VideoLocalizationTimelineEditPatchResponse, VideoLocalizationTimelineMutationResponse } from '$lib/api/types';

function fixture() {
	const parent = { clip_id: 'parent', generation_identity: 'take-1', track_id: 'dub', start_ms: 1000, end_ms: 4000, source_start_ms: 0, source_end_ms: 3000, media_source_clip_id: 'parent' };
	const initial = { timeline_clips: [parent], cues: [], localized_subtitles: [], ui_state: {} } as unknown as VideoLocalizationDraft;
	const edits = new TimelineEditController(initial);
	const revisions = new WorkspaceRevisionController();
	revisions.activate('project', '11');
	const receipt = { revision: '10', affected_clip_ids: ['parent'], timeline_clips: [parent], cues: [], localized_subtitles: [], dub_lane_states: {}, discarded_tts_task_ids: [] } as unknown as VideoLocalizationTimelineMutationResponse;
	const apply = (value: VideoLocalizationTimelineMutationResponse) => revisions.applyReceipt('project', value.revision, () => {
		edits.synchronizeExternalDraft(mergeTimelineMutation(edits.draft, value));
		return edits.draft;
	});
	return { edits, revisions, receipt, apply };
}

describe('history placement and timeline edit ownership', () => {
	it('rejects an old receipt instead of restoring the full parent over a saved split', () => {
		const { edits, receipt, apply } = fixture();
		edits.dispatch({ type: 'transaction', clipPatches: [{ clipId: 'parent', patch: { end_ms: 2500, source_end_ms: 1500 } }], addClips: [{ clip: { ...receipt.timeline_clips[0], clip_id: 'child', start_ms: 2500, source_start_ms: 1500 } }] });
		edits.acknowledgeCompactSave(edits.prepareCompactSave()!.packetId, {
			schema_version: 'timeline-edit-patch-v2',
			updated_at: null,
			revision: '12',
			timeline_clips: edits.draft.timeline_clips,
			dub_lane_states: {}
		} satisfies VideoLocalizationTimelineEditPatchResponse);
		const saved = structuredClone(edits.draft.timeline_clips);
		expect(apply(receipt)).toBeNull();
		expect(edits.draft.timeline_clips).toEqual(saved);
	});

	it('preserves unrelated pending edits when a new partial history receipt lands', () => {
		const { edits, receipt, apply, revisions } = fixture();
		edits.dispatch({ type: 'transaction', clipPatches: [{ clipId: 'parent', patch: { end_ms: 2500, source_end_ms: 1500 } }] });
		const newer = { ...receipt, revision: '12', affected_clip_ids: ['new'], timeline_clips: [{ ...receipt.timeline_clips[0], clip_id: 'new' }] };
		expect(apply(newer)).not.toBeNull();
		expect(edits.draft.timeline_clips.map((clip) => clip.clip_id)).toEqual(['parent', 'new']);
		expect(edits.draft.timeline_clips[0].end_ms).toBe(2500);
		expect(edits.hasPendingChanges).toBe(true);
		expect(revisions.needsRefresh('project', '12', 'timeline')).toBe(true);
	});

	it.each(['invalidated', 'older'] as const)('cleans an %s receipt overlay and refreshes without issuing a second request', async (kind) => {
		const { apply, receipt } = fixture();
		const commands = new HistoryPlacementSessionController(() => 'project');
		let posts = 0;
		let refreshes = 0;
		let overlay = true;
		await commands.execute(commands.capture('one-user-command'), {
			currentSave: Promise.resolve(),
			request: async () => { posts += 1; return kind === 'invalidated' ? null : receipt; },
			applyReceipt: (value) => apply(value) !== null,
			refresh: async () => { refreshes += 1; },
			onApplied() {},
			onSettled: () => { overlay = false; }
		});
		expect(posts).toBe(1);
		expect(refreshes).toBe(1);
		expect(overlay).toBe(false);
		expect(commands.hasPending).toBe(false);
	});

	it.each([new ApiError('片段已变化', 409, 'CONFLICT'), new ApiError('请求超时', 0, 'TIMEOUT')])('does not swallow or retry $code failures', async (failure) => {
		const commands = new HistoryPlacementSessionController(() => 'project');
		let posts = 0;
		let overlay = true;
		const operation = commands.execute(commands.capture('request'), {
			currentSave: Promise.resolve(), request: async () => { posts += 1; throw failure; },
			applyReceipt: () => true, refresh: async () => {}, onApplied() {}, onSettled: () => { overlay = false; }
		});
		await expect(operation).rejects.toBe(failure);
		expect(posts).toBe(1);
		expect(overlay).toBe(false);
		expect(commands.hasPending).toBe(false);
	});

	it('does not send an old project command after waiting for its previous save', async () => {
		let projectId = 'old';
		let release!: () => void;
		const currentSave = new Promise<void>((resolve) => { release = resolve; });
		const commands = new HistoryPlacementSessionController(() => projectId);
		const events: string[] = [];
		const operation = commands.execute(commands.capture('request'), {
			currentSave, request: async () => { events.push('POST'); return {}; },
			applyReceipt: () => true, refresh: async () => {},
			onApplied: () => events.push('update-new-project'), onSettled: () => events.push('cleanup-new-project')
		});
		await Promise.resolve();
		projectId = 'new';
		commands.reset();
		release();
		await operation;
		expect(events).toEqual([]);
	});
});
