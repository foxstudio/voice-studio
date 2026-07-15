import { describe, expect, it } from 'vitest';
import { resolveSelectionPlayback, resolveSelectionReleasePlayback } from './selection-playback';

describe('resolveSelectionPlayback', () => {
	it('keeps playback seamless when the current playhead remains inside the new selection', () => {
		expect(resolveSelectionPlayback(7.25, 5, 12, false)).toEqual({ nextPosition: 7.25, enforceSelection: true, shouldRestartAtStart: false, atSelectionEnd: false });
	});

	it('treats both IN and OUT as part of the settled selection', () => {
		expect(resolveSelectionPlayback(5, 5, 12, false).nextPosition).toBe(5);
		expect(resolveSelectionPlayback(12, 5, 12, false).nextPosition).toBe(12);
	});

	it('returns to the new IN point when the current playhead is before the selection', () => {
		expect(resolveSelectionPlayback(2, 5, 12, false)).toMatchObject({ nextPosition: 5, shouldRestartAtStart: true });
	});

	it('returns to the new IN point when the current playhead has passed the selection', () => {
		expect(resolveSelectionPlayback(15, 5, 12, false)).toMatchObject({ nextPosition: 5, shouldRestartAtStart: true });
	});

	it('returns to IN after an edit even when playback has already reached the source end', () => {
		expect(resolveSelectionPlayback(40, 5, 12, false)).toEqual({ nextPosition: 5, enforceSelection: true, shouldRestartAtStart: true, atSelectionEnd: false });
	});

	it('keeps a settled range that still contains the source-end playhead', () => {
		expect(resolveSelectionPlayback(40, 5, 40, false)).toEqual({ nextPosition: 40, enforceSelection: true, shouldRestartAtStart: false, atSelectionEnd: true });
	});

	it('does not enforce the temporary selection while an IN or OUT handle is held', () => {
		expect(resolveSelectionPlayback(15, 5, 12, true)).toEqual({ nextPosition: 15, enforceSelection: false, shouldRestartAtStart: false, atSelectionEnd: false });
	});

	it('continues from the live playhead after release when the final IN/OUT range catches up with it', () => {
		// While OUT is being dragged, the playhead is allowed to run past the
		// old OUT point.  Once the handle is released at 10s, 9.5s belongs to
		// the new settled range, so preview must not jump back to IN (8.5s).
		const whileDragging = resolveSelectionPlayback(9.5, 5, 8, true);
		expect(whileDragging).toMatchObject({ nextPosition: 9.5, enforceSelection: false });

		expect(resolveSelectionReleasePlayback(whileDragging.nextPosition, 8.5, 10, false, true)).toEqual({
			nextPosition: 9.5,
			enforceSelection: true,
			shouldRestartAtStart: false,
			atSelectionEnd: false
		});
	});

	it('keeps playback at the final OUT instead of restarting when the released range still contains it', () => {
		// The playhead can land exactly on the new OUT while the handle is being
		// released.  That is still part of the settled range: the caller may now
		// apply its normal loop/end rule, but must not seek back to IN first.
		expect(resolveSelectionReleasePlayback(10, 8.5, 10, false, true)).toEqual({
			nextPosition: 10,
			enforceSelection: true,
			shouldRestartAtStart: false,
			atSelectionEnd: true
		});
	});

	it('returns to IN after release only when the final range leaves the live playhead outside', () => {
		const whileDragging = resolveSelectionPlayback(9.5, 5, 8, true);

		expect(resolveSelectionReleasePlayback(whileDragging.nextPosition, 5, 8, false, true)).toEqual({
			nextPosition: 5,
			enforceSelection: true,
			shouldRestartAtStart: true,
			atSelectionEnd: false
		});
	});

	it('does not restart when the settled range still contains the playhead', () => {
		expect(resolveSelectionPlayback(9, 5, 12, false).shouldRestartAtStart).toBe(false);
	});

	it('marks a final playhead at OUT so a loop can restart only after the edit settles', () => {
		expect(resolveSelectionPlayback(12, 5, 12, false)).toMatchObject({
			shouldRestartAtStart: false,
			atSelectionEnd: true
		});
	});

	it('restarts a settled loop at IN when the source ended exactly at OUT', () => {
		expect(resolveSelectionReleasePlayback(40, 5, 40, true, true)).toMatchObject({
			nextPosition: 5,
			shouldRestartAtStart: true,
			atSelectionEnd: true
		});
	});

	it('does not replay a settled non-looping selection that ended at OUT', () => {
		expect(resolveSelectionReleasePlayback(40, 5, 40, true, false)).toMatchObject({
			nextPosition: 40,
			shouldRestartAtStart: false,
			atSelectionEnd: true
		});
	});

	it('handles an unordered range defensively', () => {
		expect(resolveSelectionPlayback(7, 12, 5, false).nextPosition).toBe(7);
	});

	it('uses IN when the browser has not provided a usable playhead', () => {
		expect(resolveSelectionPlayback(Number.NaN, 5, 12, false).nextPosition).toBe(5);
	});
});
