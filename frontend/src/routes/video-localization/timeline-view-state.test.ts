import { describe, expect, it } from 'vitest';
import { normalizeTimelineViewState, readTimelineViewState, resolveTimelineViewState, writeTimelineViewState } from './timeline-view-state';

function memoryStorage() {
	const values = new Map<string, string>();
	return {
		getItem: (key: string) => values.get(key) ?? null,
		setItem: (key: string, value: string) => values.set(key, value),
		removeItem: (key: string) => values.delete(key)
	};
}

describe('timeline session view state', () => {
	it('merges immediate view updates without dropping the other saved fields', () => {
		const storage = memoryStorage();
		writeTimelineViewState(storage, 'project-1', { timeline_zoom: 8, timeline_viewport_start_ms: 12_345 });
		writeTimelineViewState(storage, 'project-1', { playhead_ms: 14_000 });
		expect(readTimelineViewState(storage, 'project-1')).toEqual({
			timeline_zoom: 8,
			timeline_viewport_start_ms: 12_345,
			playhead_ms: 14_000
		});
	});

	it('rejects invalid values and bounds zoom before restoring a page', () => {
		expect(normalizeTimelineViewState({ timeline_zoom: 5000, timeline_viewport_start_ms: -1, playhead_ms: '2500' })).toEqual({
			timeline_zoom: 1200,
			playhead_ms: 2500
		});
	});

	it('keeps the current tab view authoritative without dropping persisted fallbacks', () => {
		expect(resolveTimelineViewState(
			{ timeline_zoom: 3, timeline_viewport_start_ms: 12_000, playhead_ms: 8_000 },
			{ timeline_viewport_start_ms: 24_000, playhead_ms: 31_000 }
		)).toEqual({
			timeline_zoom: 3,
			timeline_viewport_start_ms: 24_000,
			playhead_ms: 31_000
		});
	});
});
