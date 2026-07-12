import { describe, expect, it } from 'vitest';
import { createReferenceAudioDraft } from '../reference-audio/draft';
import {
	compileAudioPromptReferences,
	insertAudioPromptReference,
	parseAudioPromptReferences,
	validateAudioPromptReferences
} from './prompt-references';
import { createDefaultSeedAudioState, setSeedAudioReference, type SeedAudioReferenceAsset } from './state';

function asset(id: string): SeedAudioReferenceAsset {
	return { assetId: id, type: 'audio', source: 'upload', displayName: id, voiceId: '', speakerId: '', licenseStatus: 'self_voice', referenceAudio: createReferenceAudioDraft(id) };
}

describe('@音频 prompt references', () => {
	it('parses every reference with its source location', () => {
		expect(parseAudioPromptReferences('@音频1 开场，随后 @音频2 回答，再次 @音频1')).toEqual([
			expect.objectContaining({ slot: 1, start: 0, raw: '@音频1' }),
			expect.objectContaining({ slot: 2, raw: '@音频2' }),
			expect.objectContaining({ slot: 1, raw: '@音频1' })
		]);
	});

	it('reports empty and invalid references, plus filled but unused slots', () => {
		let state = createDefaultSeedAudioState();
		state = setSeedAudioReference(state, 1, asset('one'));
		state = setSeedAudioReference(state, 3, asset('three'));
		const result = validateAudioPromptReferences('@音频2 和 @音频4', state.drafts.audio.references);

		expect(result.errors.map((entry) => entry.code)).toEqual(['reference_slot_empty', 'reference_out_of_range']);
		expect(result.warnings.map((entry) => entry.slot)).toEqual([1, 3]);
	});

	it('compacts sparse slots and rewrites official numbers without replacement collisions', () => {
		let state = createDefaultSeedAudioState();
		state = setSeedAudioReference(state, 2, asset('two'));
		state = setSeedAudioReference(state, 3, asset('three'));
		const compiled = compileAudioPromptReferences('@音频3 回应 @音频2', state.drafts.audio.references);

		expect(compiled.prompt).toBe('@音频2 回应 @音频1');
		expect(compiled.bindings).toEqual([
			{ slot: 2, requestIndex: 1, assetId: 'two' },
			{ slot: 3, requestIndex: 2, assetId: 'three' }
		]);
	});

	it('inserts a token at the cursor with readable spacing', () => {
		expect(insertAudioPromptReference('开场回答', 2, 2)).toBe('开场 @音频2 回答');
		expect(insertAudioPromptReference('', 1)).toBe('@音频1');
	});
});
