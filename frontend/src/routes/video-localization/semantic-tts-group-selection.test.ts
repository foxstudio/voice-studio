import { describe, expect, it } from 'vitest';
import { semanticTtsGroupAtOrdinal, semanticTtsGroupOrdinal } from './semantic-tts-group-selection';

describe('semantic TTS group selection', () => {
	it('uses stable group ids instead of object identity for the displayed ordinal', () => {
		const groups = [
			{ group_id: 'semantic_group_0001' },
			{ group_id: 'semantic_group_0002' }
		];

		expect(semanticTtsGroupOrdinal(groups, 'semantic_group_0001')).toBe(1);
		expect(semanticTtsGroupOrdinal(groups.map((group) => ({ ...group })), 'semantic_group_0002')).toBe(2);
	});

	it('returns null for a group that is no longer present', () => {
		expect(semanticTtsGroupOrdinal([{ group_id: 'semantic_group_0001' }], 'missing')).toBeNull();
	});

	it('selects any large-plan group by one-based number without rendering options', () => {
		const groups = Array.from({ length: 415 }, (_, index) => ({
			group_id: `semantic_group_${String(index + 1).padStart(4, '0')}`
		}));

		expect(semanticTtsGroupAtOrdinal(groups, 1)?.group_id).toBe('semantic_group_0001');
		expect(semanticTtsGroupAtOrdinal(groups, 415)?.group_id).toBe('semantic_group_0415');
		expect(semanticTtsGroupAtOrdinal(groups, 416)).toBeNull();
	});
});
