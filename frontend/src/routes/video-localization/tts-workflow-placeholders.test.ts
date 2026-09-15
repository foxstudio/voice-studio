import { describe, expect, it } from 'vitest';
import type { VideoLocalizationDraft, VideoLocalizationTtsTask } from '$lib/api/types';
import { withTtsWorkflowPlaceholders } from './tts-workflow-placeholders';
import {
	createTtsInitializationPlaceholder,
	promoteTtsInitializationPlaceholder
} from './tts-initialization-clips';
import { draftForPersistence } from './draft-ownership';
import { composeTimelineRuntimeDraft } from './timeline-runtime-projection';

function workflow(
	status: VideoLocalizationTtsTask['status'],
	overrides: Partial<VideoLocalizationTtsTask> = {}
): VideoLocalizationTtsTask {
	return {
		workflow_id: 'workflow-1',
		project_id: 'project-1',
		segment_id: 'localized-1',
		subtitle_summary: '测试',
		text: '测试',
		source_cue_ids: ['cue-1'],
		start_ms: 1000,
		end_ms: 2200,
		status,
		generation_task_id: 'generation-1',
		result_id: status === 'success' ? 'result-1' : null,
		timeline_clip_id: 'clip-localized-1',
		stages: [
			{
				kind: 'generation',
				status: status === 'prepared' ? 'pending' : status === 'queued' ? 'queued' : status === 'running' ? 'running' : 'success',
				progress: status === 'success' ? 1 : 0.5,
				parameters: { timeline_clip_id: 'clip-localized-1' },
				error_code: null,
				error_message: null,
				started_at: null,
				completed_at: status === 'success' ? '2026-07-29T12:00:01' : null
			},
			{
				kind: 'placement',
				status: status === 'success' ? 'success' : 'pending',
				progress: status === 'success' ? 1 : 0,
				parameters: {},
				error_code: null,
				error_message: null,
				started_at: null,
				completed_at: status === 'success' ? '2026-07-29T12:00:01' : null
			}
		],
		created_at: '2026-07-29T12:00:00',
		updated_at: '2026-07-29T12:00:01',
		completed_at: status === 'success' ? '2026-07-29T12:00:01' : null,
		...overrides
	};
}

function draft(
	timelineClips: VideoLocalizationDraft['timeline_clips'],
	tasks: VideoLocalizationTtsTask[]
): VideoLocalizationDraft {
	return {
		timeline_clips: timelineClips,
		tts_tasks: tasks
	} as unknown as VideoLocalizationDraft;
}

describe('TTS workflow placeholder continuity', () => {
	it('keeps immediate reuse feedback through delayed autosave, handoff, and result promotion', () => {
		const baseline = draft([], []);
		const initialization = createTtsInitializationPlaceholder([], {
			clientId: 'reuse-1',
			segmentId: 'localized-1',
			primaryCueId: 'cue-1',
			sourceCueIds: ['cue-1'],
			startMs: 1000,
			endMs: 2200
		});
		const withInitialization = draft([initialization], []);

		const transported = draftForPersistence(withInitialization);
		expect(transported.timeline_clips).toEqual([]);

		const afterDelayedAutosave = composeTimelineRuntimeDraft(baseline, [initialization]);
		expect(afterDelayedAutosave.timeline_clips).toEqual([initialization]);

		const handedOff = {
			...afterDelayedAutosave,
			timeline_clips: promoteTtsInitializationPlaceholder(
				afterDelayedAutosave.timeline_clips,
				'reuse-1',
				'workflow-1'
			),
			tts_tasks: [workflow('queued')]
		};
		expect(handedOff.timeline_clips).toEqual([
			expect.objectContaining({
				status_label: '排队中',
				optimistic_tts_workflow_id: 'workflow-1'
			})
		]);

		const persistedResult = {
			clip_id: 'clip-localized-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: '/project/tts/generation-1.wav',
			task_id: 'generation-1',
			generation_id: 'generation-1',
			result_id: 'result-1',
			status: 'ready'
		};
		const completed = composeTimelineRuntimeDraft(
			draft([persistedResult], [workflow('success')]),
			handedOff.timeline_clips
		);

		expect(completed.timeline_clips).toEqual([persistedResult]);
	});

	it('preserves both an active workflow and a second local initialization across a workspace refresh', () => {
		const activeWorkflow = {
			clip_id: 'pending_tts_workflow-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: null,
			status: 'running',
			optimistic_tts_workflow_id: 'workflow-1'
		};
		const secondInitialization = {
			clip_id: 'pending_tts_init_client-2',
			track_id: 'dub',
			subtitle_id: 'localized-2',
			start_ms: 2400,
			end_ms: 3300,
			audio_path: null,
			status: 'queued',
			optimistic_tts_workflow_id: 'init:client-2'
		};
		const current = draft(
			[activeWorkflow, secondInitialization],
			[workflow('running')]
		);
		const refreshedWorkspace = draft([], []);

		const reconciled = composeTimelineRuntimeDraft(
			{ ...refreshedWorkspace, tts_tasks: current.tts_tasks },
			current.timeline_clips
		);

		expect(reconciled.tts_tasks).toEqual(current.tts_tasks);
		expect(reconciled.timeline_clips).toEqual(expect.arrayContaining([
			expect.objectContaining({
				clip_id: 'pending_tts_workflow-1',
				optimistic_tts_workflow_id: 'workflow-1'
			}),
			expect.objectContaining({
				clip_id: 'pending_tts_init_client-2',
				optimistic_tts_workflow_id: 'init:client-2'
			})
		]));
	});

	it('lets a matching persisted result replace its runtime placeholder without removing another initialization', () => {
		const completed = workflow('success');
		const runtimePlaceholder = {
			clip_id: 'pending_tts_workflow-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: null,
			task_id: 'generation-1',
			status: 'running',
			optimistic_tts_workflow_id: 'workflow-1'
		};
		const secondInitialization = {
			clip_id: 'pending_tts_init_client-2',
			track_id: 'dub',
			subtitle_id: 'localized-2',
			start_ms: 2400,
			end_ms: 3300,
			audio_path: null,
			status: 'queued',
			optimistic_tts_workflow_id: 'init:client-2'
		};
		const persistedResult = {
			clip_id: 'clip-localized-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: '/project/tts/generation-1.wav',
			task_id: 'generation-1',
			generation_id: 'generation-1',
			result_id: 'result-1',
			status: 'ready'
		};

		const reconciled = composeTimelineRuntimeDraft(
			draft([persistedResult], [completed]),
			[runtimePlaceholder, secondInitialization]
		);

		expect(reconciled.timeline_clips).toEqual([
			persistedResult,
			secondInitialization
		]);
	});

	it('preserves a local initialization placeholder during an unrelated task sync', () => {
		const initialization = {
			clip_id: 'pending_tts_init_client-1',
			track_id: 'dub',
			subtitle_id: 'localized-init',
			start_ms: 100,
			end_ms: 800,
			audio_path: null,
			status: 'queued',
			optimistic_tts_workflow_id: 'init:client-1'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[initialization],
			[workflow('running')]
		));

		expect(reconciled.timeline_clips).toEqual(expect.arrayContaining([
			expect.objectContaining({ clip_id: 'pending_tts_init_client-1' }),
			expect.objectContaining({ optimistic_tts_workflow_id: 'workflow-1' })
		]));
	});

	it('promotes an initialization placeholder when its authoritative workflow arrives first', () => {
		const initialization = {
			clip_id: 'pending_tts_init_workflow-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: null,
			dub_lane: 0,
			status: 'queued',
			status_label: '排队中',
			optimistic_tts_workflow_id: 'init:workflow-1'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[initialization],
			[workflow('running')]
		));

		expect(reconciled.timeline_clips).toEqual([
			expect.objectContaining({
				clip_id: 'pending_tts_init_workflow-1',
				status: 'running',
				status_label: '生成中',
				optimistic_tts_workflow_id: 'workflow-1'
			})
		]);
	});

	it('keeps only one placeholder when submit handoff temporarily creates duplicate workflow markers', () => {
		const queued = {
			clip_id: 'pending_tts_init_workflow-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: null,
			dub_lane: 0,
			status: 'queued',
			optimistic_tts_workflow_id: 'workflow-1'
		};
		const running = {
			...queued,
			clip_id: 'pending_tts_workflow-1',
			dub_lane: 1,
			status: 'running'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[queued, running],
			[workflow('running')]
		));

		expect(reconciled.timeline_clips).toHaveLength(1);
		expect(reconciled.timeline_clips[0]).toEqual(expect.objectContaining({
			clip_id: 'pending_tts_init_workflow-1',
			dub_lane: 0,
			status: 'running',
			optimistic_tts_workflow_id: 'workflow-1'
		}));
		expect(withTtsWorkflowPlaceholders(reconciled)).toEqual(reconciled);
	});

	it('does not mistake the replaced clip id for the new in-flight result', () => {
		const previousResult = {
			clip_id: 'clip-localized-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: '/project/tts/previous.wav',
			task_id: 'generation-previous',
			generation_id: 'generation-previous',
			result_id: 'result-previous',
			status: 'ready'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[previousResult],
			[workflow('running')]
		));

		expect(reconciled.timeline_clips).toEqual(expect.arrayContaining([
			expect.objectContaining({ clip_id: 'clip-localized-1', result_id: 'result-previous' }),
			expect.objectContaining({
				clip_id: 'pending_tts_workflow-1',
				optimistic_tts_workflow_id: 'workflow-1'
			})
		]));
	});

	it('keeps an older result and puts a repeated subtitle generation on a free lane', () => {
		const previousResult = {
			clip_id: 'clip-localized-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: '/project/tts/previous.wav',
			dub_lane: 0,
			result_id: 'result-previous',
			status: 'ready'
		};
		const repeatedTask = workflow('running', {
			timeline_clip_id: null,
			stages: [
				{
					kind: 'generation',
					status: 'running',
					progress: 0.5,
					parameters: {},
					error_code: null,
					error_message: null,
					started_at: null,
					completed_at: null
				},
				{
					kind: 'placement',
					status: 'pending',
					progress: 0,
					parameters: {},
					error_code: null,
					error_message: null,
					started_at: null,
					completed_at: null
				}
			]
		});

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[previousResult],
			[repeatedTask]
		));

		expect(reconciled.timeline_clips).toEqual(expect.arrayContaining([
			expect.objectContaining({
				clip_id: 'clip-localized-1',
				result_id: 'result-previous',
				dub_lane: 0
			}),
			expect.objectContaining({
				clip_id: 'pending_tts_workflow-1',
				optimistic_tts_workflow_id: 'workflow-1',
				dub_lane: 1
			})
		]));
	});

	it('projects three overlapping queued workflows onto independent lanes', () => {
		const tasks = [0, 1, 2].map((index) => workflow('queued', {
			workflow_id: `workflow-${index + 1}`,
			segment_id: `localized-${index + 1}`,
			generation_task_id: `generation-${index + 1}`,
			start_ms: 1_000,
			end_ms: 2_200,
			stages: [
				{
					kind: 'generation',
					status: 'queued',
					progress: 0,
					parameters: {},
					error_code: null,
					error_message: null,
					started_at: null,
					completed_at: null
				},
				{
					kind: 'placement',
					status: 'pending',
					progress: 0,
					parameters: {},
					error_code: null,
					error_message: null,
					started_at: null,
					completed_at: null
				}
			]
		}));

		const reconciled = withTtsWorkflowPlaceholders(draft([], tasks));
		const placeholders = reconciled.timeline_clips.filter(
			(clip) => clip.optimistic_tts_workflow_id
		);

		expect(placeholders).toHaveLength(3);
		expect(placeholders.map((clip) => clip.optimistic_tts_workflow_id)).toEqual([
			'workflow-1',
			'workflow-2',
			'workflow-3'
		]);
		expect(placeholders.map((clip) => clip.dub_lane)).toEqual([0, 1, 2]);
	});

	it('keeps the same active clip identity and lane while workflow polling updates it', () => {
		const optimistic = {
			clip_id: 'client-visible-clip',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1250,
			end_ms: 2450,
			audio_path: null,
			dub_lane: 3,
			status: 'queued',
			generation_progress: 0,
			optimistic_tts_workflow_id: 'workflow-1'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[optimistic],
			[workflow('running')]
		));

		expect(reconciled.timeline_clips).toEqual([
			expect.objectContaining({
				clip_id: 'client-visible-clip',
				dub_lane: 3,
				start_ms: 1250,
				end_ms: 2450,
				task_id: 'generation-1',
				status: 'running',
				optimistic_tts_workflow_id: 'workflow-1'
			})
		]);
	});

	it('does not erase an active local clip during one empty workflow response', () => {
		const optimistic = {
			clip_id: 'client-visible-clip',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: null,
			status: 'running',
			optimistic_tts_workflow_id: 'workflow-1'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft([optimistic], []));

		expect(reconciled.timeline_clips).toEqual([optimistic]);
	});

	it('drops the placeholder only after the matching persisted audio result arrives', () => {
		const persistedResult = {
			clip_id: 'clip-localized-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: '/project/tts/generation-1.wav',
			task_id: 'generation-1',
			generation_id: 'generation-1',
			result_id: 'result-1',
			status: 'ready'
		};
		const optimistic = {
			...persistedResult,
			clip_id: 'pending_tts_workflow-1',
			audio_path: null,
			status: 'running',
			optimistic_tts_workflow_id: 'workflow-1'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[persistedResult, optimistic],
			[workflow('success')]
		));

		expect(reconciled.timeline_clips).toEqual([persistedResult]);
	});

	it('removes failed and cancelled placeholders from the timeline', () => {
		for (const status of ['failed', 'cancelled'] as const) {
			const optimistic = {
				clip_id: `pending_tts_${status}`,
				track_id: 'dub',
				subtitle_id: 'localized-1',
				start_ms: 1000,
				end_ms: 2200,
				audio_path: null,
				status: 'running',
				optimistic_tts_workflow_id: 'workflow-1'
			};

			expect(withTtsWorkflowPlaceholders(draft(
				[optimistic],
				[workflow(status)]
			)).timeline_clips).toEqual([]);
		}
	});

	it('removes the runtime placeholder when the authoritative workflow is successful', () => {
		const optimistic = {
			clip_id: 'pending_tts_workflow-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			start_ms: 1000,
			end_ms: 2200,
			audio_path: null,
			task_id: 'generation-1',
			generation_id: 'generation-1',
			status: 'running',
			generation_progress: 0.89,
			optimistic_tts_workflow_id: 'workflow-1'
		};

		const reconciled = withTtsWorkflowPlaceholders(draft(
			[optimistic],
			[workflow('success', { timeline_clip_id: null })]
		));

		expect(reconciled.timeline_clips).toEqual([]);
	});

	it('shows post-generation recovery as placement instead of a verification phase', () => {
		const task = workflow('running', {
			stages: [
				{
					kind: 'generation',
					status: 'success',
					progress: 1,
					parameters: {},
					error_code: null,
					error_message: null,
					started_at: '2026-07-29T12:00:00',
					completed_at: '2026-07-29T12:00:01'
				},
				{
					kind: 'placement',
					status: 'pending',
					progress: 0,
					parameters: {},
					error_code: null,
					error_message: null,
					started_at: null,
					completed_at: null
				}
			]
		});

		const reconciled = withTtsWorkflowPlaceholders(draft([], [task]));

		expect(reconciled.timeline_clips).toEqual([
			expect.objectContaining({
				status: 'running',
				status_label: '等待放入轨道'
			})
		]);
	});
});
