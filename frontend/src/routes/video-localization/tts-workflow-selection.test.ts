import { describe, expect, it } from 'vitest';
import type { VideoLocalizationTtsTask } from '$lib/api/types';
import { workflowSegmentIdForSubtitleSelection } from './tts-workflow-selection';

function workflow(segmentId: string, subtitleIds: string[]): VideoLocalizationTtsTask {
	return {
		workflow_id: `workflow-${segmentId}`,
		project_id: 'project-1',
		segment_id: segmentId,
		subtitle_summary: '测试',
		text: '测试',
		source_cue_ids: ['cue-1'],
		start_ms: 1000,
		end_ms: 2200,
		status: 'failed',
		generation_task_id: 'generation-1',
		result_id: 'result-1',
		timeline_clip_id: null,
		stages: [
			{
				kind: 'generation',
				status: 'success',
				progress: 1,
				parameters: {
					video_localization_target_subtitle_ids: subtitleIds
				},
				error_code: null,
				error_message: null,
				started_at: '2026-07-29T12:00:00',
				completed_at: '2026-07-29T12:00:01'
			},
			{
				kind: 'placement',
				status: 'failed',
				progress: 0,
				parameters: {},
				error_code: 'VIDEO_LOCALIZATION_TTS_PLACEMENT_FAILED',
				error_message: '未采用到时间线',
				started_at: null,
				completed_at: '2026-07-29T12:00:01'
			}
		],
		created_at: '2026-07-29T12:00:00',
		updated_at: '2026-07-29T12:00:01',
		completed_at: '2026-07-29T12:00:01'
	};
}

describe('TTS workflow selection identity', () => {
	it('keeps the authoritative group segment after a failed placement removes its clip', () => {
		const tasks = [
			workflow('group_localized_0001_localized_0003_3_digest', [
				'localized_0001',
				'localized_0002',
				'localized_0003'
			])
		];

		expect(workflowSegmentIdForSubtitleSelection(tasks, [
			'localized_0001',
			'localized_0002',
			'localized_0003'
		])).toBe('group_localized_0001_localized_0003_3_digest');
	});

	it('does not reuse a group identity for a different subtitle selection', () => {
		expect(workflowSegmentIdForSubtitleSelection([
			workflow('group-old', ['localized_0001', 'localized_0002'])
		], ['localized_0002', 'localized_0003'])).toBe('');
	});
});
