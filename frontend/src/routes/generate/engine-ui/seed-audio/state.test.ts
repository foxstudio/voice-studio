import { describe, expect, it } from 'vitest';
import { createReferenceAudioDraft } from '../reference-audio/draft';
import {
	activeSeedAudioDraft,
	createDefaultSeedAudioState,
	resetActiveSeedAudioParameters,
	setSeedAudioImage,
	setSeedAudioMode,
	setSeedAudioReference,
	updateSeedAudioParameters,
	updateSeedAudioPrompt,
	type SeedAudioReferenceAsset
} from './state';

function audioAsset(id: string): SeedAudioReferenceAsset {
	return {
		assetId: id,
		type: 'audio',
		source: 'upload',
		displayName: `${id}.wav`,
		voiceId: '',
		speakerId: '',
		licenseStatus: 'self_voice',
		referenceAudio: createReferenceAudioDraft(id, {
			source: { fileId: `${id}-source`, durationMs: 5_000 },
			clip: { fileId: `${id}-clip`, durationMs: 5_000, mimeType: 'audio/wav', sizeBytes: 1000 }
		})
	};
}

describe('Seed Audio mode state isolation', () => {
	it('keeps prompt and parameters independently for all three modes', () => {
		let state = createDefaultSeedAudioState();
		state = updateSeedAudioPrompt(state, '文字模式草稿');
		state = updateSeedAudioParameters(state, { speech_rate: 15 });
		state = setSeedAudioMode(state, 'audio');
		state = updateSeedAudioPrompt(state, '@音频1 参考模式草稿');
		state = updateSeedAudioParameters(state, { speech_rate: -20 });
		state = setSeedAudioMode(state, 'image');
		state = updateSeedAudioPrompt(state, '图片模式草稿');

		expect(state.drafts.text).toMatchObject({ prompt: '文字模式草稿', parameters: { speech_rate: 15 } });
		expect(state.drafts.audio).toMatchObject({ prompt: '@音频1 参考模式草稿', parameters: { speech_rate: -20 } });
		expect(state.drafts.image).toMatchObject({ prompt: '图片模式草稿', parameters: { speech_rate: 0 } });
		expect(activeSeedAudioDraft(state)).toBe(state.drafts.image);
	});

	it('keeps exactly three fixed reference slots and edits only the requested slot', () => {
		let state = createDefaultSeedAudioState();
		const first = audioAsset('first');
		const third = audioAsset('third');
		state = setSeedAudioReference(state, 1, first);
		state = setSeedAudioReference(state, 3, third);
		const unchanged = setSeedAudioReference(state, 4 as never, audioAsset('fourth'));

		expect(unchanged.drafts.audio.references).toHaveLength(3);
		expect(unchanged.drafts.audio.references.map((slot) => slot.asset?.assetId ?? null)).toEqual(['first', null, 'third']);
		expect(setSeedAudioReference(unchanged, 1, null).drafts.audio.references[2].asset).toBe(third);
	});

	it('keeps image and audio drafts while switching modes and resetting current parameters', () => {
		let state = setSeedAudioReference(createDefaultSeedAudioState(), 1, audioAsset('voice'));
		state = setSeedAudioImage(state, {
			assetId: 'image', source: 'upload', fileId: 'image-file', displayName: 'scene.png', previewUrl: 'blob:test',
			mimeType: 'image/png', sizeBytes: 1000, licenseStatus: 'self_voice'
		});
		state = setSeedAudioMode(state, 'audio');
		state = updateSeedAudioParameters(state, { pitch_rate: 7 });
		state = resetActiveSeedAudioParameters(state);

		expect(state.drafts.audio.parameters.pitch_rate).toBe(0);
		expect(state.drafts.audio.references[0].asset?.assetId).toBe('voice');
		expect(state.drafts.image.image?.assetId).toBe('image');
	});
});
