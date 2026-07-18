import { describe, expect, it } from 'vitest';
import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import { splitTimelineAudioClip } from './timeline-clip-split';

function clip(overrides: Partial<VideoLocalizationTimelineClip> = {}): VideoLocalizationTimelineClip {
	return {
		clip_id: 'clip_localized_0001',
		track_id: 'dub',
		start_ms: 1000,
		end_ms: 5000,
		source_start_ms: 0,
		source_end_ms: 4000,
		audio_path: '/tmp/example.wav',
		...overrides
	};
}

describe('timeline audio clip splitting', () => {
	it('preserves one stable media source while creating two timeline identities', () => {
		const result = splitTimelineAudioClip([clip()], 'clip_localized_0001', 2500);
		expect(result?.first).toMatchObject({ clip_id: 'clip_localized_0001', media_source_clip_id: 'clip_localized_0001', start_ms: 1000, end_ms: 2500, source_start_ms: 0, source_end_ms: 1500 });
		expect(result?.second).toMatchObject({ clip_id: 'clip_localized_0001_part_2', media_source_clip_id: 'clip_localized_0001', start_ms: 2500, end_ms: 5000, source_start_ms: 1500, source_end_ms: 4000 });
	});

	it('keeps the original media identity across repeated splits', () => {
		const first = splitTimelineAudioClip([clip()], 'clip_localized_0001', 2500);
		const second = splitTimelineAudioClip(first?.clips ?? [], 'clip_localized_0001_part_2', 3500);
		expect(second?.clips.map((item) => item.media_source_clip_id)).toEqual([
			'clip_localized_0001',
			'clip_localized_0001',
			'clip_localized_0001'
		]);
		expect(second?.clips.map((item) => [item.source_start_ms, item.source_end_ms])).toEqual([
			[0, 1500],
			[1500, 2500],
			[2500, 4000]
		]);
	});

	it('rejects cuts too close to either edge', () => {
		expect(splitTimelineAudioClip([clip()], 'clip_localized_0001', 1200)).toBeNull();
		expect(splitTimelineAudioClip([clip()], 'clip_localized_0001', 4800)).toBeNull();
	});
});
