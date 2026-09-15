import { describe, expect, it } from 'vitest';
import {
	SEED_AUDIO_ADVANCED_PARAMETER_KEYS,
	SEED_AUDIO_ADVANCED_CONTROL_ORDER,
	SEED_AUDIO_BASIC_PARAMETER_KEYS,
	SEED_AUDIO_MODE_OPTIONS,
	seedAudioPromptHelp,
	seedAudioRequiredInputs
} from './ui';

describe('Seed Audio UI field grouping', () => {
	it('keeps the compact mode selector ordered and defaults are defined by state, not the UI', () => {
		expect(SEED_AUDIO_MODE_OPTIONS).toEqual([
			{ value: 'text', label: '文本' },
			{ value: 'audio', label: '语音' },
			{ value: 'image', label: '图片' }
		]);
	});

	it('keeps non-defaultable mode inputs outside advanced settings', () => {
		expect(seedAudioRequiredInputs('text')).toEqual(['prompt']);
		expect(seedAudioRequiredInputs('audio')).toEqual(['audio_references', 'prompt']);
		expect(seedAudioRequiredInputs('image')).toEqual(['image_reference', 'prompt']);
	});

	it('separates always-visible output controls from optional advanced controls', () => {
		expect(SEED_AUDIO_BASIC_PARAMETER_KEYS).toEqual(['speech_rate', 'format']);
		expect(SEED_AUDIO_ADVANCED_PARAMETER_KEYS).toEqual([
			'sample_rate',
			'enable_subtitle',
			'loudness_rate',
			'pitch_rate',
			'aigc_watermark',
			'aigc_metadata'
		]);
		expect(SEED_AUDIO_ADVANCED_PARAMETER_KEYS.some((key) => SEED_AUDIO_BASIC_PARAMETER_KEYS.includes(key as never))).toBe(false);
	});

	it('keeps advanced controls on one row ordered by visual type and includes the duration limit', () => {
		expect(SEED_AUDIO_ADVANCED_CONTROL_ORDER).toEqual([
			'sample_rate',
			'loudness_rate',
			'pitch_rate',
			'enable_subtitle',
			'aigc_watermark',
			'aigc_metadata'
		]);
		for (const mode of ['text', 'audio', 'image'] as const) {
			expect(seedAudioPromptHelp(mode)).toContain('最长可生成 120 秒');
		}
	});
});
