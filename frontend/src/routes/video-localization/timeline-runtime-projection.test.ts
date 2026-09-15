import { describe, expect, it } from 'vitest';
import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip,
	VideoLocalizationTtsTask
} from '$lib/api/types';
import {
	composeTimelineRuntimeDraft,
	durableTimelineDraft,
	reconcileTimelineRuntimeClips
} from './timeline-runtime-projection';
import { TimelineEditController } from './timeline-edit-controller';
import { HistoryPlacementSessionController } from './history-placement-session-controller';

function draft(
	timelineClips: VideoLocalizationTimelineClip[],
	tasks: VideoLocalizationTtsTask[] = []
): VideoLocalizationDraft {
	return {
		source_media: { duration_ms: 10_000 },
		stems: {},
		cues: [],
		localized_subtitles: [],
		glossary: [],
		scene_context: {},
		timeline_clips: timelineClips,
		ui_state: {},
		tts_tasks: tasks
	} as unknown as VideoLocalizationDraft;
}

function runtimeClip(overrides: Partial<VideoLocalizationTimelineClip> = {}) {
	return {
		clip_id: 'pending_tts_workflow-1',
		track_id: 'dub',
		start_ms: 1_000,
		end_ms: 2_000,
		audio_path: null,
		status: 'running',
		optimistic_tts_workflow_id: 'workflow-1',
		...overrides
	} as VideoLocalizationTimelineClip;
}

function task(status: VideoLocalizationTtsTask['status']): VideoLocalizationTtsTask {
	return {
		workflow_id: 'workflow-1',
		status,
		segment_id: 'localized-1',
		source_cue_ids: ['cue-1'],
		start_ms: 1_000,
		end_ms: 2_000,
		generation_task_id: 'generation-1',
		result_id: status === 'success' ? 'result-1' : null,
		stages: [
			{ kind: 'generation', status: status === 'success' ? 'success' : 'running', progress: status === 'success' ? 1 : 0.4 },
			{ kind: 'placement', status: status === 'success' ? 'success' : 'pending', progress: status === 'success' ? 1 : 0 }
		]
	} as VideoLocalizationTtsTask;
}

describe('timeline runtime projection', () => {
	it('keeps runtime overlays out of the durable draft', () => {
		const stable = { clip_id: 'stable', track_id: 'dub', start_ms: 0, end_ms: 500 } as VideoLocalizationTimelineClip;
		const durable = durableTimelineDraft(draft([stable, runtimeClip()]));

		expect(durable.timeline_clips).toEqual([stable]);
	});

	it('composes active workflow placeholders only at the render boundary', () => {
		const stable = { clip_id: 'stable', track_id: 'dub', start_ms: 0, end_ms: 500 } as VideoLocalizationTimelineClip;
		const durable = draft([stable], [task('running')]);

		expect(composeTimelineRuntimeDraft(durable, [runtimeClip()]).timeline_clips.map((clip) => clip.clip_id))
			.toEqual(['stable', 'pending_tts_workflow-1']);
		expect(durable.timeline_clips).toEqual([stable]);
	});

	it('drops a terminal placeholder once its stable result is present without touching other clips', () => {
		const oldClip = { clip_id: 'old', track_id: 'dub', start_ms: 0, end_ms: 500 } as VideoLocalizationTimelineClip;
		const generated = {
			clip_id: 'generated',
			track_id: 'dub',
			start_ms: 1_000,
			end_ms: 2_000,
			audio_path: '/generated.wav',
			generation_id: 'generation-1',
			result_id: 'result-1'
		} as VideoLocalizationTimelineClip;
		const durable = draft([oldClip, generated], [task('success')]);

		expect(reconcileTimelineRuntimeClips(durable, [runtimeClip()])).toEqual([]);
		expect(composeTimelineRuntimeDraft(durable, [runtimeClip()]).timeline_clips.map((clip) => clip.clip_id))
			.toEqual(['old', 'generated']);
	});

	it('keeps history placement runtime separate until its owning command settles', () => {
		const history = runtimeClip({
			clip_id: 'history-pending',
			optimistic_tts_workflow_id: undefined,
			optimistic_history_result_id: 'history-1',
			audio_path: 'history:history-1',
			status: 'applying'
		});
		const durable = draft([]);

		expect(reconcileTimelineRuntimeClips(durable, [history])).toEqual([history]);
		expect(durable.timeline_clips).toEqual([]);
	});

	it('prefers an authoritative history placement over the same runtime clip and drops the overlay', () => {
		const placed = runtimeClip({
			clip_id: 'history-placed',
			optimistic_tts_workflow_id: undefined,
			optimistic_history_result_id: undefined,
			audio_path: '/history-placed.wav',
			result_id: 'history-1',
			status: 'ready'
		});
		const overlay = {
			...placed,
			audio_path: 'history:history-1',
			status: 'applying',
			optimistic_history_result_id: 'history-1'
		};
		const durable = draft([placed]);

		expect(composeTimelineRuntimeDraft(durable, [overlay]).timeline_clips).toEqual([placed]);
		expect(reconcileTimelineRuntimeClips(durable, [overlay])).toEqual([]);
		expect(composeTimelineRuntimeDraft(draft([]), reconcileTimelineRuntimeClips(durable, [overlay])).timeline_clips)
			.toEqual([]);
	});

	it('never renders the same runtime clip id twice', () => {
		const first = runtimeClip({
			clip_id: 'history-duplicate',
			optimistic_tts_workflow_id: undefined,
			optimistic_history_result_id: 'history-1',
			status: 'applying'
		});
		const second = { ...first, status_label: 'retrying placement' };

		expect(composeTimelineRuntimeDraft(draft([]), [first, second]).timeline_clips.map((clip) => clip.clip_id))
			.toEqual(['history-duplicate']);
	});

	it('keeps distinct clips that intentionally share a generated result', () => {
		const first = runtimeClip({
			clip_id: 'take-one',
			optimistic_tts_workflow_id: undefined,
			result_id: 'shared-result',
			audio_path: '/shared.wav',
			status: 'ready'
		});
		const second = { ...first, clip_id: 'take-two', start_ms: 3_000, end_ms: 4_000 };

		expect(composeTimelineRuntimeDraft(draft([first, second]), []).timeline_clips.map((clip) => clip.clip_id))
			.toEqual(['take-one', 'take-two']);
	});

	it('keeps a history placement safe while its receipt refresh is still pending', async () => {
		const placed = runtimeClip({
			clip_id: 'history-delayed-receipt',
			optimistic_tts_workflow_id: undefined,
			optimistic_history_result_id: undefined,
			audio_path: '/history-delayed-receipt.wav',
			result_id: 'history-1',
			status: 'ready'
		});
		const overlay = {
			...placed,
			audio_path: 'history:history-1',
			status: 'applying',
			optimistic_history_result_id: 'history-1'
		};
		let currentDraft = draft([]);
		let runtimeClips: VideoLocalizationTimelineClip[] = [overlay];
		let settled = false;
		let applied = false;
		let beginRefresh!: () => void;
		let releaseRefresh!: () => void;
		const refreshStarted = new Promise<void>((resolve) => (beginRefresh = resolve));
		const refresh = new Promise<void>((resolve) => (releaseRefresh = resolve));
		const controller = new HistoryPlacementSessionController(() => 'project');
		const context = controller.capture('history-delayed-receipt');

		const pending = controller.execute(context, {
			currentSave: Promise.resolve(),
			request: async () => placed,
			applyReceipt: (receipt) => {
				currentDraft = draft([receipt]);
				runtimeClips = reconcileTimelineRuntimeClips(currentDraft, runtimeClips);
				return false;
			},
			refresh: async () => {
				beginRefresh();
				await refresh;
			},
			onApplied: () => (applied = true),
			onSettled: () => {
				settled = true;
				runtimeClips = [];
			}
		});

		await refreshStarted;
		expect(settled).toBe(false);
		expect(applied).toBe(false);
		expect(runtimeClips).toEqual([]);
		expect(composeTimelineRuntimeDraft(currentDraft, runtimeClips).timeline_clips).toEqual([placed]);

		currentDraft = draft([]);
		expect(composeTimelineRuntimeDraft(currentDraft, runtimeClips).timeline_clips).toEqual([]);

		releaseRefresh();
		await pending;
		expect(applied).toBe(true);
		expect(settled).toBe(true);
	});

	it('merges a terminal TTS result through pending edits without deleting unrelated stable clips', () => {
		const first = { clip_id: 'first', track_id: 'dub', start_ms: 0, end_ms: 500 } as VideoLocalizationTimelineClip;
		const later = { clip_id: 'later', track_id: 'dub', start_ms: 3_000, end_ms: 3_500 } as VideoLocalizationTimelineClip;
		const controller = new TimelineEditController(draft([first, later]));
		controller.dispatch({
			type: 'transaction',
			clipPatches: [{ clipId: 'first', patch: { start_ms: 100, end_ms: 600 } }]
		});
		const generated = {
			clip_id: 'generated',
			track_id: 'dub',
			start_ms: 1_000,
			end_ms: 2_000,
			audio_path: '/generated.wav',
			generation_id: 'generation-1',
			result_id: 'result-1'
		} as VideoLocalizationTimelineClip;
		const server = draft([first, generated, later], [task('success')]);

		controller.mergeRefresh(server);
		const durable = controller.draft;
		const view = composeTimelineRuntimeDraft(durable, [runtimeClip()]);

		expect(view.timeline_clips.map((clip) => clip.clip_id)).toEqual(['first', 'generated', 'later']);
		expect(view.timeline_clips.find((clip) => clip.clip_id === 'first')).toMatchObject({
			start_ms: 100,
			end_ms: 600
		});
		expect(controller.deletedTimelineClipIds).toEqual(new Set());
	});
});
