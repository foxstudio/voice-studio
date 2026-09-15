import { describe, expect, it } from 'vitest';
import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import {
	activeTimelineClips,
	audioPlaybackRateForDrift,
	clipSourceTimeSeconds,
	scheduledTimelineClips,
	shouldCorrectAudioDrift,
	shouldHardCorrectAudioDrift,
	timelineClipEndMs,
	timelineClipKey,
	timelineClipHasAudioSource,
	timelineClipPlayableRange,
	upcomingTimelineClips
} from './preview-playback';

it('retains the playback node identity when an edit receipt redacts its path', () => {
	for (const result_id of ['result-1', undefined]) {
		const before: VideoLocalizationTimelineClip = { clip_id: 'clip', track_id: 'dub',
			audio_path: '/managed/audio.wav', generation_identity: 'media-token', result_id, has_audio_source: true };
		const { audio_path, ...receipt } = before;
		expect(timelineClipHasAudioSource(receipt)).toBe(true);
		expect(timelineClipKey(receipt)).toBe(timelineClipKey(before));
		expect(timelineClipHasAudioSource({ clip_id: 'pending', track_id: 'dub', generation_identity: 'task' })).toBe(false);
	}
});

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

	it('plays split clips through their shared media source without a copied audio path', () => {
		const split = clip({
			clip_id: 'dub_1_part_2',
			media_source_clip_id: 'dub_1',
			audio_path: null
		});

		expect(activeTimelineClips([split], 'dub', 2000)).toEqual([split]);
		expect(upcomingTimelineClips([split], 'dub', 0)).toEqual([split]);
	});

	it('maps the video master time into the clip source without changing playback speed', () => {
		expect(clipSourceTimeSeconds(clip(), 2.4)).toBeCloseTo(1.65);
		expect(clipSourceTimeSeconds(clip(), 0.5)).toBeCloseTo(0.25);
	});

	it('uses only the common timeline/source duration for a short source crop', () => {
		const shortSource = clip({ start_ms: 10_000, end_ms: 15_833, source_start_ms: 0, source_end_ms: 5_792 });
		expect(timelineClipPlayableRange(shortSource)).toEqual({
			timelineStartMs: 10_000,
			timelineEndMs: 15_792,
			sourceStartMs: 0,
			sourceEndMs: 5_792
		});
		expect(activeTimelineClips([shortSource], 'dub', 15_791)).toEqual([shortSource]);
		expect(activeTimelineClips([shortSource], 'dub', 15_792)).toEqual([]);
		expect(clipSourceTimeSeconds(shortSource, 15.9)).toBeCloseTo(5.792);
	});

	it('does not read past a timeline crop when the source is longer', () => {
		const shortTimeline = clip({ start_ms: 10_000, end_ms: 15_792, source_start_ms: 300, source_end_ms: 6_300 });
		expect(timelineClipPlayableRange(shortTimeline)).toEqual({
			timelineStartMs: 10_000,
			timelineEndMs: 15_792,
			sourceStartMs: 300,
			sourceEndMs: 6_092
		});
		expect(clipSourceTimeSeconds(shortTimeline, 15.9)).toBeCloseTo(6.092);
	});

	it('corrects meaningful drift while leaving tiny clock differences alone', () => {
		expect(shouldCorrectAudioDrift(4, 4.04)).toBe(false);
		expect(shouldCorrectAudioDrift(4, 4.08)).toBe(true);
		expect(shouldHardCorrectAudioDrift(4, 4.3)).toBe(false);
		expect(shouldHardCorrectAudioDrift(4, 4.7)).toBe(true);
	});

	it('uses bounded rate correction for ordinary playback drift', () => {
		expect(audioPlaybackRateForDrift(4, 4.04)).toBe(1);
		expect(audioPlaybackRateForDrift(4, 4.3)).toBeCloseTo(1.05);
		expect(audioPlaybackRateForDrift(4.3, 4)).toBeCloseTo(0.95);
		expect(audioPlaybackRateForDrift(4, 4.7)).toBe(1);
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

	it('uses the same legacy duration fallback for active and upcoming clips', () => {
		const legacy = clip({ clip_id: 'legacy', start_ms: 5000, end_ms: null });
		expect(timelineClipEndMs(legacy)).toBe(6800);
		expect(activeTimelineClips([legacy], 'dub', 6000)).toEqual([legacy]);
		expect(upcomingTimelineClips([legacy], 'dub', 6000)).toEqual([legacy]);
	});

	it('keeps the dub media pool bounded around the current playback position', () => {
		const clips = Array.from({ length: 20 }, (_, index) => clip({
			clip_id: `dub_${index}`,
			start_ms: index * 1000,
			end_ms: index * 1000 + 900
		}));
		expect(scheduledTimelineClips(clips, 'dub', 5_500, {
			futureLimit: 4,
			lookaheadMs: 10_000
		}).map((item) => item.clip_id)).toEqual(['dub_5', 'dub_6', 'dub_7', 'dub_8', 'dub_9']);
	});

	it('always keeps every active overlap even when it exceeds the future preload limit', () => {
		const clips = [
			clip({ clip_id: 'active-a', start_ms: 1000, end_ms: 3000 }),
			clip({ clip_id: 'active-b', start_ms: 1500, end_ms: 3500 }),
			clip({ clip_id: 'next', start_ms: 4000, end_ms: 5000 })
		];
		expect(scheduledTimelineClips(clips, 'dub', 2_000, {
			futureLimit: 1,
			lookaheadMs: 10_000
		}).map((item) => item.clip_id)).toEqual(['active-a', 'active-b', 'next']);
	});
});
