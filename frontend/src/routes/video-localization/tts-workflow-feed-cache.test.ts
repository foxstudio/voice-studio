import { describe, expect, it } from 'vitest';

import type { VideoLocalizationTtsTask, VideoLocalizationTtsTaskFeed } from '$lib/api';
import { TtsWorkflowFeedCache } from './tts-workflow-feed-cache';

function task(workflowId: string, progress: number): VideoLocalizationTtsTask {
	return {
		workflow_id: workflowId,
		project_id: 'project-1',
		segment_id: `segment-${workflowId}`,
		subtitle_summary: workflowId,
		text: workflowId,
		source_cue_ids: [],
		start_ms: 0,
		end_ms: 1000,
		status: 'running',
		generation_task_id: `generation-${workflowId}`,
		result_id: null,
		timeline_clip_id: null,
		stages: [
			{ kind: 'generation', status: 'running', progress, parameters: {}, error_code: null, error_message: null, started_at: null, completed_at: null },
			{ kind: 'placement', status: 'pending', progress: 0, parameters: {}, error_code: null, error_message: null, started_at: null, completed_at: null }
		],
		created_at: '2026-09-01T00:00:00',
		updated_at: '2026-09-01T00:00:00',
		completed_at: null
	};
}

function feed(overrides: Partial<VideoLocalizationTtsTaskFeed>): VideoLocalizationTtsTaskFeed {
	return {
		schema_version: 'video-localization-tts-task-feed-v1',
		revision: 'projection:1',
		changed: true,
		workflow_ids: ['a', 'b'],
		tasks: [task('a', 0.1), task('b', 0.2)],
		...overrides
	};
}

describe('TtsWorkflowFeedCache', () => {
	it('updates one workflow without replacing untouched task objects', () => {
		const cache = new TtsWorkflowFeedCache();
		const initial = cache.apply(feed({}));
		const untouched = initial[0];

		const updated = cache.apply(feed({
			revision: 'projection:2',
			tasks: [task('a', 0.8)]
		}));

		expect(updated.map((item) => item.workflow_id)).toEqual(['b', 'a']);
		expect(updated[0]).toBe(untouched);
		expect(updated[1].stages[0].progress).toBe(0.8);
	});

	it('preserves the cache for unchanged polls and removes deleted ids on change', () => {
		const cache = new TtsWorkflowFeedCache();
		const initial = cache.apply(feed({}));
		const unchanged = cache.apply(feed({
			changed: false,
			tasks: [],
			workflow_ids: []
		}));
		expect(unchanged).toEqual(initial);

		const afterDelete = cache.apply(feed({
			revision: 'projection:3',
			workflow_ids: ['b'],
			tasks: []
		}));
		expect(afterDelete.map((item) => item.workflow_id)).toEqual(['b']);
	});
});
