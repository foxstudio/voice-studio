import { describe, expect, it } from 'vitest';
import type { VideoLocalizationDubSubtitleCue } from '$lib/api/types';
import { dubSubtitleTimingBounds } from './dub-subtitle-state';

function cue(id: string, startMs: number, endMs: number, lanes: number[]): VideoLocalizationDubSubtitleCue {
	return {
		subtitle_id: id,
		start_ms: startMs,
		end_ms: endMs,
		text: id,
		speaker_id: null,
		source_clip_ids: [`clip-${id}`],
		dub_lanes: lanes,
		source_audio_sha256: 'a'.repeat(64),
		needs_review: false,
		quality_flags: []
	};
}

describe('dub subtitle timing state', () => {
	it('uses only cues that share an audible lane as trim boundaries', () => {
		const subtitles = [
			cue('previous', 100, 500, [0]),
			cue('selected', 700, 1_200, [0]),
			cue('other-lane', 1_250, 1_450, [1]),
			cue('next', 1_600, 2_000, [0])
		];

		expect(dubSubtitleTimingBounds(subtitles, 'selected', 3_000)).toEqual({
			minStartMs: 500,
			maxEndMs: 1_600
		});
	});
});
