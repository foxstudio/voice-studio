import { describe, expect, it, vi } from 'vitest';
import { saveTimelineEditWithUiPatch } from './timeline-edit-save';

describe('compact timeline and UI save', () => {
	it('persists mix changes in the same autosave as clip edits', async () => {
		const timeline = { updated_at: 'after', revision: '11', timeline_clips: [{ clip_id: 'edited' }] };
		const saveTimeline = vi.fn().mockResolvedValue(timeline);
		const saveUi = vi.fn().mockResolvedValue({ updated_at: 'before', revision: '10' });
		const patch = { dub_lane_states: { '0': { solo: true } } };
		expect(await saveTimelineEditWithUiPatch(saveTimeline, saveUi, patch)).toBe(timeline);
		expect(saveUi).toHaveBeenCalledWith(patch);
		expect(saveUi.mock.invocationCallOrder[0]).toBeLessThan(saveTimeline.mock.invocationCallOrder[0]);
	});

	it('does not add a UI request to plain clip saves', async () => {
		const saveUi = vi.fn();
		const result = { updated_at: null, revision: '10' };
		expect(await saveTimelineEditWithUiPatch(async () => result, saveUi, {})).toBe(result);
		expect(saveUi).not.toHaveBeenCalled();
	});

	it('does not acknowledge a combined save when its UI patch fails', async () => {
		const saveUi = vi.fn().mockRejectedValue(new Error('offline'));
		const saveTimeline = vi.fn().mockResolvedValue({ updated_at: null, revision: '10' });
		await expect(saveTimelineEditWithUiPatch(
			saveTimeline, saveUi,
			{ track_states: { background: { muted: true } } }
		)).rejects.toThrow('offline');
		expect(saveTimeline).not.toHaveBeenCalled();
	});
});
