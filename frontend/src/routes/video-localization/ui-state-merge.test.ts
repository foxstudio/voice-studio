import { describe, expect, it } from 'vitest';
import { mergeVideoLocalizationUiState } from './ui-state-merge';

describe('video localization UI-state merge', () => {
	it('updates one dubbing lane without replacing the other lanes', () => {
		const current = {
			dub_lane_states: {
				'0': { label: '主配音', muted: false, volume: 1 },
				'1': { label: '补充配音', muted: false, volume: 0.8 }
			}
		};
		const next = mergeVideoLocalizationUiState(current, {
			dub_lane_states: { '1': { muted: true } }
		});

		expect(next.dub_lane_states).toEqual({
			'0': { label: '主配音', muted: false, volume: 1 },
			'1': { label: '补充配音', muted: true, volume: 0.8 }
		});
	});
});
