import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { selectMenuPlacement } from './select-menu';

const selectComponent = readFileSync(new URL('./Select.svelte', import.meta.url), 'utf8');
const voiceSelector = readFileSync(
	new URL('../../../routes/generate/components/VoiceSelector.svelte', import.meta.url),
	'utf8'
);
const appStyles = readFileSync(new URL('../../../app.css', import.meta.url), 'utf8');

describe('select menu placement', () => {
	it('keeps a medium menu aligned to the trigger when there is room', () => {
		expect(selectMenuPlacement(
			{ left: 120, right: 260, width: 140 },
			1200,
			340
		)).toBe('start');
	});

	it('aligns the menu to the trigger end near the right viewport edge', () => {
		expect(selectMenuPlacement(
			{ left: 760, right: 900, width: 140 },
			920,
			340
		)).toBe('end');
	});

	it('keeps the trigger alignment on narrow screens where both sides are constrained', () => {
		expect(selectMenuPlacement(
			{ left: 12, right: 288, width: 276 },
			300,
			340
		)).toBe('start');
	});

	it('keeps scroll interaction while hiding menu scrollbars', () => {
		expect(selectComponent).toContain('select-options menu-scroll-region');
		expect(selectComponent).toContain('overflow-y: auto');
		expect(selectComponent).toContain('overflow-x: hidden');
		expect(appStyles).toContain('.menu-scroll-region::-webkit-scrollbar');
		expect(appStyles).toContain('scrollbar-width: none');
	});

	it('gives long voice labels a medium menu width', () => {
		expect(selectComponent).toContain('menuWidth = 300');
		expect(selectComponent).toContain('max(100%, var(--select-menu-width))');
		expect(voiceSelector).toContain('menuWidth={340}');
	});
});
