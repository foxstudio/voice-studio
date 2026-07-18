import { describe, expect, it } from 'vitest';
import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import {
	activeTimelineClips,
	clipSourceTimeSeconds,
	shouldCorrectAudioDrift,
	timelineClipKey,
	upcomingTimelineClips
} from './preview-playback';

function clip(overrides: Partial<VideoLocalizationTimelineClip> = {}): VideoLocalizationTimelineClip {
	return {
		clip_id: 'dub_1',
		track_id: 'dub',
		start_ms: 1000,
		end_ms: 3000,
		source_start_ms: 250,
		audio_path: '/tmp/dub.wav',
		...overrides
	};
}

describe('preview playback scheduling', () => {
	it('keeps every overlapping clip active instead of silently choosing one', () => {
		const clips = [clip(), clip({ clip_id: 'dub_2', start_ms: 1800, end_ms: 4200 })];
		expect(activeTimelineClips(clips, 'dub', 2000).map((item) => item.clip_id)).toEqual(['dub_1', 'dub_2']);
	});

	it('maps the video master time into the clip source without changing playback speed', () => {
		expect(clipSourceTimeSeconds(clip(), 2.4)).toBeCloseTo(1.65);
		expect(clipSourceTimeSeconds(clip(), 0.5)).toBeCloseTo(0.25);
	});

	it('corrects meaningful drift while leaving tiny clock differences alone', () => {
		expect(shouldCorrectAudioDrift(4, 4.08)).toBe(false);
		expect(shouldCorrectAudioDrift(4, 4.13)).toBe(true);
	});

	it('preloads the active and nearest future clips in timeline order', () => {
		const clips = [
			clip({ clip_id: 'later', start_ms: 8000, end_ms: 9000 }),
			clip({ clip_id: 'past', start_ms: 0, end_ms: 500 }),
			clip({ clip_id: 'active', start_ms: 1000, end_ms: 3000 }),
			clip({ clip_id: 'next', start_ms: 4000, end_ms: 5000 })
		];
		expect(upcomingTimelineClips(clips, 'dub', 2000, 2).map((item) => item.clip_id)).toEqual(['active', 'next']);
		expect(timelineClipKey(clips[2])).toBe('active:/tmp/dub.wav');
	});
});
