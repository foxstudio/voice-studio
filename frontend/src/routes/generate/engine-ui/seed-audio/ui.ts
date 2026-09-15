import type { SeedAudioMode } from './state';

export const SEED_AUDIO_MODE_OPTIONS: ReadonlyArray<{ value: SeedAudioMode; label: string }> = [
	{ value: 'text', label: '文本' },
	{ value: 'audio', label: '语音' },
	{ value: 'image', label: '图片' }
];

export const SEED_AUDIO_BASIC_PARAMETER_KEYS = ['speech_rate', 'format'] as const;

export const SEED_AUDIO_ADVANCED_PARAMETER_KEYS = [
	'sample_rate',
	'enable_subtitle',
	'loudness_rate',
	'pitch_rate',
	'aigc_watermark',
	'aigc_metadata'
] as const;

export const SEED_AUDIO_ADVANCED_CONTROL_ORDER = [
	'sample_rate',
	'loudness_rate',
	'pitch_rate',
	'enable_subtitle',
	'aigc_watermark',
	'aigc_metadata'
] as const;

export function seedAudioPromptHelp(mode: SeedAudioMode): string {
	const suffix = '单次最长可生成 120 秒。';
	if (mode === 'audio') return `先添加 1～3 条参考声音，再写清谁在何时说什么；需要指定某条声音时插入 @音频1～3。${suffix}`;
	if (mode === 'image') return `先上传一张角色或场景图片，再写清谁要说什么、环境声音和收尾效果。${suffix}`;
	return `把场景、环境、人物声音、情绪、对白和收尾音效写清楚；描述越具体，生成的声音层次越明确。${suffix}`;
}

export type SeedAudioRequiredInputKind = 'prompt' | 'audio_references' | 'image_reference';

export function seedAudioRequiredInputs(mode: SeedAudioMode): SeedAudioRequiredInputKind[] {
	if (mode === 'audio') return ['audio_references', 'prompt'];
	if (mode === 'image') return ['image_reference', 'prompt'];
	return ['prompt'];
}
