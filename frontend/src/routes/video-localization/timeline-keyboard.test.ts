import { describe, expect, it } from 'vitest';
import { timelineDeleteShortcutAllowed } from './timeline-keyboard';

describe('timeline destructive keyboard shortcuts', () => {
	it('requires the timeline to own keyboard focus', () => {
		expect(timelineDeleteShortcutAllowed('Delete', false, true)).toBe(false);
		expect(timelineDeleteShortcutAllowed('Backspace', false, true)).toBe(false);
		expect(timelineDeleteShortcutAllowed('e', false, true)).toBe(false);
		expect(timelineDeleteShortcutAllowed('Delete', true, false)).toBe(true);
		expect(timelineDeleteShortcutAllowed('Backspace', true, false)).toBe(true);
		expect(timelineDeleteShortcutAllowed('Enter', true, true)).toBe(false);
	});

	it('uses E for deletion only when a deletable timeline selection exists', () => {
		expect(timelineDeleteShortcutAllowed('e', true, true)).toBe(true);
		expect(timelineDeleteShortcutAllowed('E', true, true)).toBe(true);
		expect(timelineDeleteShortcutAllowed('e', true, false)).toBe(false);
	});
});
