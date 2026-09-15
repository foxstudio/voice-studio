import { describe, expect, it } from 'vitest';
import type { VideoLocalizationDraft } from '$lib/api/types';
import { activeDubbingPlanGroups } from './dubbing-production-view';

describe('dubbing production view', () => {
	it('projects the active versioned plan as the timeline group source', () => {
		const draft = {
			dubbing_production: {
				schema_version: 'dubbing-production-state-v2',
				candidate_reports: [],
				latest_timeline_audit: null,
				active_plan: {
					schema_version: 'dubbing-generation-plan-v1',
					source_revision: 'revision',
					status: 'passed',
					semantic_units: [],
					speech_islands: [],
					findings: [],
					groups: [{
						group_id: 'dubbing_group_0001',
						island_id: 'speech_island_0001',
						unit_ids: ['unit_1'],
						subtitle_ids: ['localized_1'],
						speaker_id: 'speaker_1',
						scene_id: null,
						spoken_text: '自然台词',
						target_start_ms: 0,
						target_end_ms: 1_000,
						source_reference_start_ms: 0,
						source_reference_end_ms: 1_000
					}]
				}
			}
		} as unknown as VideoLocalizationDraft;

		expect(activeDubbingPlanGroups(draft)).toEqual([{
			group_id: 'dubbing_group_0001',
			subtitle_ids: ['localized_1'],
			text_preview: '自然台词',
			char_count: 4
		}]);
	});

	it('returns null when the canonical production plan is absent', () => {
		expect(activeDubbingPlanGroups(null)).toBeNull();
	});

});
