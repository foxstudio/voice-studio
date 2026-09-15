import { describe, expect, it } from 'vitest';
import { TimelineGestureSession, type TimelineGestureState } from './timeline-gesture-session';

describe('timeline gesture session', () => {
	it('owns exactly one active gesture and replaces stale pointer work at the boundary', () => {
		const pan: TimelineGestureState = { kind: 'pan', startX: 120, scrollLeft: 480 };
		const seek: TimelineGestureState = { kind: 'seek' };

		const session = new TimelineGestureSession().begin(pan).begin(seek);

		expect(session.state).toEqual(seek);
		expect(session.isActive()).toBe(true);
		expect(session.is('pan')).toBe(false);
		expect(session.is('seek')).toBe(true);
	});

	it('updates only the currently owned gesture', () => {
		const session = new TimelineGestureSession().begin({
			kind: 'range-create',
			startX: 40,
			startMs: 1_000,
			moved: false
		});

		const ignored = session.update('marquee', (state) => ({ ...state, moved: true }));
		const updated = session.update('range-create', (state) => ({ ...state, moved: true }));

		expect(ignored).toBe(session);
		expect(updated.state).toMatchObject({ kind: 'range-create', moved: true });
	});

	it('returns the completed gesture once and resets to idle', () => {
		const session = new TimelineGestureSession().begin({
			kind: 'selection-handle',
			edge: 'end'
		});

		const finished = session.finish();

		expect(finished.completed).toEqual({ kind: 'selection-handle', edge: 'end' });
		expect(finished.session.state).toEqual({ kind: 'idle' });
		expect(finished.session.finish().completed).toBeNull();
	});

	it('cancels every gesture through the same idempotent lifecycle path', () => {
		const active = new TimelineGestureSession().begin({
			kind: 'marquee',
			startX: 10,
			startY: 20,
			currentX: 30,
			currentY: 40,
			moved: true,
			additive: false,
			baseItems: []
		});

		const cancelled = active.cancel();

		expect(cancelled.state).toEqual({ kind: 'idle' });
		expect(cancelled.cancel()).toBe(cancelled);
	});
});
