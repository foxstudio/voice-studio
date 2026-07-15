import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	TOOLTIP_HIDE_DELAY_MS,
	TOOLTIP_SHOW_DELAY_MS,
	TooltipSession,
	computeTooltipPosition
} from './hover-tooltip';

afterEach(() => vi.useRealTimers());

describe('TooltipSession', () => {
	it('waits on first hover and switches adjacent targets immediately once warmed', () => {
		vi.useFakeTimers();
		const shown: string[] = [];
		const session = new TooltipSession<string>({ show: (target) => shown.push(target), hide: () => shown.push('hidden') });

		session.enterTarget('first');
		vi.advanceTimersByTime(TOOLTIP_SHOW_DELAY_MS - 1);
		expect(shown).toEqual([]);
		vi.advanceTimersByTime(1);
		expect(shown).toEqual(['first']);

		session.leaveTarget('first');
		session.enterTarget('second');
		expect(shown).toEqual(['first', 'second']);
	});

	it('keeps the overlay open across the pointer safety interval', () => {
		vi.useFakeTimers();
		const shown: string[] = [];
		const session = new TooltipSession<string>({ show: (target) => shown.push(target), hide: () => shown.push('hidden') });

		session.enterTarget('button');
		vi.advanceTimersByTime(TOOLTIP_SHOW_DELAY_MS);
		session.leaveTarget('button');
		vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS - 20);
		session.enterLayer();
		vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS);
		expect(shown).toEqual(['button']);

		session.leaveLayer();
		vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS);
		expect(shown).toEqual(['button', 'hidden']);
	});

	it('resets the warm session after an explicit close', () => {
		vi.useFakeTimers();
		const shown: string[] = [];
		const session = new TooltipSession<string>({ show: (target) => shown.push(target), hide: () => shown.push('hidden') });
		session.enterTarget('first');
		vi.advanceTimersByTime(TOOLTIP_SHOW_DELAY_MS);
		session.close();
		session.enterTarget('second');
		expect(shown).toEqual(['first', 'hidden']);
		vi.advanceTimersByTime(TOOLTIP_SHOW_DELAY_MS);
		expect(shown).toEqual(['first', 'hidden', 'second']);
	});

	it('cancels a pending first hover when the session closes', () => {
		vi.useFakeTimers();
		const shown: string[] = [];
		const session = new TooltipSession<string>({ show: (target) => shown.push(target), hide: () => shown.push('hidden') });
		session.enterTarget('button');
		session.close();
		vi.advanceTimersByTime(TOOLTIP_SHOW_DELAY_MS);
		expect(shown).toEqual(['hidden']);
	});
});

describe('computeTooltipPosition', () => {
	it('flips below a target near the top edge', () => {
		expect(computeTooltipPosition(
			{ top: 2, right: 120, bottom: 26, left: 80, width: 40, height: 24 },
			{ width: 160, height: 60 },
			{ width: 800, height: 600 }
		)).toMatchObject({ placement: 'bottom', top: 34 });
	});

	it('clamps the overlay inside the right viewport edge', () => {
		const position = computeTooltipPosition(
			{ top: 220, right: 798, bottom: 244, left: 758, width: 40, height: 24 },
			{ width: 180, height: 60 },
			{ width: 800, height: 600 }
		);
		expect(position.left).toBe(612);
		expect(position.top).toBeGreaterThanOrEqual(8);
	});
});
