import { describe, expect, it } from 'vitest';
import { parseVideoLocalizationTtsHandoffIntent } from './video-localization-tts-handoff';

describe('video-localization TTS handoff intent', () => {
	it('accepts one versioned selection and removes duplicate ids', () => {
		expect(parseVideoLocalizationTtsHandoffIntent(JSON.stringify({
			schema_version: 'video-localization-tts-handoff-v1',
			source: 'video_localization',
			mode: 'reference_only',
			project_id: 'project',
			segment_id: 'localized-1',
			subtitle_id: 'localized-1',
			target_subtitle_ids: ['localized-1', 'localized-1'],
			source_cue_ids: ['cue-1', 'cue-1'],
			created_at: '2026-08-31T00:00:00Z'
		}))).toMatchObject({
			target_subtitle_ids: ['localized-1'],
			source_cue_ids: ['cue-1']
		});
	});

	it('rejects incomplete or unversioned payloads', () => {
		expect(parseVideoLocalizationTtsHandoffIntent('{}')).toBeNull();
		expect(parseVideoLocalizationTtsHandoffIntent('{')).toBeNull();
	});
});
