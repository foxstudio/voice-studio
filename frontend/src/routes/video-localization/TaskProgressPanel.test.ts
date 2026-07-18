import { describe, expect, it } from 'vitest';
import {
	DEFAULT_VISIBLE_HISTORY_TASKS,
	defaultHistoryTaskLimit,
	hiddenHistoryTaskCount
} from './TaskProgressPanel.svelte';

describe('task progress history visibility', () => {
	it('shows ten history tasks by default in the full task panel', () => {
		expect(DEFAULT_VISIBLE_HISTORY_TASKS).toBe(10);
		expect(defaultHistoryTaskLimit(true, 0)).toBe(10);
		expect(defaultHistoryTaskLimit(true, 3)).toBe(10);
	});

	it('keeps the compact panel within the shared default task budget', () => {
		expect(defaultHistoryTaskLimit(false, 3)).toBe(7);
		expect(defaultHistoryTaskLimit(false, 10)).toBe(0);
	});

	it('reports exactly how many history tasks remain hidden', () => {
		expect(hiddenHistoryTaskCount(45, 10)).toBe(35);
		expect(hiddenHistoryTaskCount(8, 10)).toBe(0);
	});
});
