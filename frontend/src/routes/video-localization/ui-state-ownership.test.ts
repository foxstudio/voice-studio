import { describe, expect, it } from 'vitest';
import {
	clientVideoLocalizationUiPatch,
	mergeVideoLocalizationUiStateAfterConflict
} from './ui-state-ownership';

describe('video localization UI state ownership', () => {
	it('keeps the UI patch endpoint limited to client-owned fields', () => {
		expect(clientVideoLocalizationUiPatch({
			sidebar_collapsed: true,
			subtitle_display_mode: 'dub',
			track_states: { original: { muted: true } },
			automatic_dub_mix_configured: true,
			initial_track_mix_configured: true,
			latest_tts_task_by_segment: { localized_1: 'task-stale' },
			discarded_tts_task_ids: [],
			client_timeline_edit_intent: { dub_lane_clip_ids: ['clip-stale'] },
			unknown_future_backend_state: 'stale'
		})).toEqual({
			sidebar_collapsed: true,
			subtitle_display_mode: 'dub',
			track_states: { original: { muted: true } }
		});
	});

	it('preserves backend task state while merging client controls and monotonic tombstones', () => {
		const merged = mergeVideoLocalizationUiStateAfterConflict({
			latest_tts_task_by_segment: {},
			discarded_tts_task_ids: ['task-server'],
			track_states: {
				original: { muted: false, solo: true, volume: 0.8 }
			},
			unknown_future_backend_state: 'fresh'
		}, {
			latest_tts_task_by_segment: { localized_1: 'task-stale' },
			discarded_tts_task_ids: ['task-local'],
			sidebar_collapsed: true,
			track_states: {
				original: { muted: true }
			},
			unknown_future_backend_state: 'stale'
		});

		expect(merged).toEqual({
			latest_tts_task_by_segment: {},
			discarded_tts_task_ids: ['task-server', 'task-local'],
			sidebar_collapsed: true,
			track_states: {
				original: { muted: true, solo: true, volume: 0.8 }
			},
			unknown_future_backend_state: 'fresh'
		});
	});
});
