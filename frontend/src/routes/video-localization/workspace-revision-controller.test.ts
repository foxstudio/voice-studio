import { describe, expect, it } from 'vitest';
import type { VideoLocalizationDraft, VideoLocalizationTimelineMutationResponse } from '$lib/api/types';
import { WorkspaceRevisionController } from './workspace-revision-controller';
import { mergeTimelineMutation } from './timeline-history-command';
import { draftWithLiveTimelineProjection } from './workspace-timeline-projection';

describe('workspace revision ownership', () => {
	it('accepts the first complete read after importing into an empty project session', () => {
		const revisions = new WorkspaceRevisionController();
		revisions.reset('imported');
		expect(revisions.needsRefresh('imported', '', 'workspace')).toBe(true);
		expect(revisions.canApply('imported', '2')).toBe(true);
		revisions.consume('imported', '2', 'workspace');
		expect(revisions.needsRefresh('imported', '2', 'workspace')).toBe(false);
		revisions.reset();
		expect(revisions.canApply('imported', '3')).toBe(false);
		expect(revisions.canApply('', '3')).toBe(false);
	});
	it('fetches an unseen remote clip after a later local partial mutation receipt', () => {
		const revisions = new WorkspaceRevisionController();
		revisions.activate('project', '1');
		let local = { timeline_clips: [{ clip_id: 'A', start_ms: 0 }], cues: [], localized_subtitles: [], ui_state: {} } as unknown as VideoLocalizationDraft;
		// Another writer added B at R2; saving A at R3 returns only A.
		const receipt = { revision: '3', affected_clip_ids: ['A'], timeline_clips: [{ clip_id: 'A', start_ms: 100 }], cues: [], localized_subtitles: [], dub_lane_states: {}, discarded_tts_task_ids: [] } as unknown as VideoLocalizationTimelineMutationResponse;
		local = mergeTimelineMutation(local, receipt);
		revisions.observe('project', receipt.revision);
		expect(local.timeline_clips.map(c => c.clip_id)).toEqual(['A']);
		expect(revisions.needsRefresh('project', '3', 'timeline')).toBe(true);
		const projection = { revision: '3', timeline_clips: [...local.timeline_clips, { clip_id: 'B', track_id: 'dub', start_ms: 500, end_ms: 700 }] };
		expect(revisions.canApply('project', projection.revision)).toBe(true);
		local = draftWithLiveTimelineProjection(local, projection);
		revisions.consume('project', projection.revision, 'timeline');
		expect(local.timeline_clips.map(c => c.clip_id)).toEqual(['A', 'B']);
		expect(local.timeline_clips[0].start_ms).toBe(100);
		expect(revisions.needsRefresh('project', '3', 'timeline')).toBe(false);
	});

	it.each(['timeline edit', 'UI state', 'history adoption'])('%s receipt never consumes a complete read', () => {
		const revisions = new WorkspaceRevisionController();
		revisions.activate('project', '10');
		revisions.observe('project', '12');
		expect(revisions.needsRefresh('project', '12', 'workspace')).toBe(true);
		expect(revisions.needsRefresh('project', '12', 'timeline')).toBe(true);
		expect(revisions.canApply('project', '11')).toBe(false);
		revisions.consume('project', '11', 'workspace');
		expect(revisions.needsRefresh('project', '12', 'workspace')).toBe(true);
	});

	it('timeline refresh does not suppress a later full workspace refresh', () => {
		const revisions = new WorkspaceRevisionController();
		revisions.activate('project', '1');
		revisions.consume('project', '2', 'timeline');
		expect(revisions.needsRefresh('project', '2', 'timeline')).toBe(false);
		expect(revisions.needsRefresh('project', '2', 'workspace')).toBe(true);
		revisions.consume('project', '2', 'workspace');
		expect(revisions.needsRefresh('project', '2', 'workspace')).toBe(false);
		expect(revisions.needsRefresh('project', '2', 'timeline')).toBe(false);
	});

	it('failed or invalidated reads do not advance consumed versions', () => {
		const revisions = new WorkspaceRevisionController();
		revisions.activate('project', '1');
		revisions.observe('project', '2');
		expect(revisions.canApply('project', '2')).toBe(true);
		// Read failed or its request epoch was invalidated: no consume call.
		expect(revisions.needsRefresh('project', '2', 'timeline')).toBe(true);
		revisions.observe('project', '3');
		expect(revisions.canApply('project', '2')).toBe(false);
		expect(revisions.needsRefresh('project', '2', 'timeline')).toBe(true);
	});

	it('does not lower the observed version for late receipts or responses', () => {
		const revisions = new WorkspaceRevisionController();
		revisions.activate('project', '10');
		revisions.observe('project', '12');
		revisions.observe('project', '11');
		expect(revisions.canApply('project', '11')).toBe(false);
		expect(revisions.canApply('project', '12')).toBe(true);
	});

	it('scopes all observations to the active project', () => {
		const revisions = new WorkspaceRevisionController();
		revisions.activate('old', '100');
		revisions.activate('new', '1');
		revisions.observe('old', '101');
		revisions.consume('old', '101', 'workspace');
		expect(revisions.canApply('old', '101')).toBe(false);
		expect(revisions.canApply('new', '1')).toBe(true);
		expect(revisions.needsRefresh('new', '1', 'workspace')).toBe(false);
	});
});
