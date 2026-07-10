import { describe, expect, it } from 'vitest';
import { taskDateStartIso, taskServerQuery } from './records-query';

describe('taskServerQuery', () => {
	it('maps Chinese status words and preserves ordinary text', () => {
		expect(taskServerQuery(' 失败 ')).toBe('failed');
		expect(taskServerQuery('狐狸')).toBe('狐狸');
	});
});

describe('taskDateStartIso', () => {
	it('uses local calendar midnight for today', () => {
		const now = new Date(2026, 6, 10, 18, 30, 0);
		expect(taskDateStartIso('today', now)).toBe(new Date(2026, 6, 10).toISOString());
	});

	it('keeps rolling windows for multi-day filters', () => {
		const now = new Date('2026-07-10T10:00:00.000Z');
		expect(taskDateStartIso('7d', now)).toBe('2026-07-03T10:00:00.000Z');
		expect(taskDateStartIso('all', now)).toBeUndefined();
	});
});
