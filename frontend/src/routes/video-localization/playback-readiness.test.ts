import { describe, expect, it } from 'vitest';
import { audioWindowReady, buildPlaybackReadiness, bufferedRangeCovered, pendingPlaybackRanges, playbackMediaIsLoading, playbackReadinessAt, playbackRangesForViewport, type PlaybackMediaRequirement } from './playback-readiness';

function media(overrides: Partial<PlaybackMediaRequirement> = {}): PlaybackMediaRequirement {
	return {
		id: 'video', label: '视频画面', timeline_start_ms: 0, timeline_end_ms: 30_000,
		source_start_ms: 0, source_end_ms: 30_000, buffered: [{ start_ms: 0, end_ms: 30_000 }],
		media_duration_ms: 30_000,
		loading: false, error: false, ...overrides
	};
}

const previewRanges = [0, 10_000, 20_000].map((start_ms) => ({
	start_ms, end_ms: start_ms + 10_000, status: 'ready' as const, frame_count: 20, sprite_rows: 4
}));

describe('playback readiness', () => {
	it('does not report an unmounted or idle media element as actively caching', () => {
		for (const element of [null, { networkState: 0, readyState: 0 }, { networkState: 1, readyState: 0 }, { networkState: 3, readyState: 0 }]) {
			expect(playbackMediaIsLoading(element)).toBe(false);
			const status = buildPlaybackReadiness({ durationMs: 30_000, chunkMs: 10_000,
				previewRanges, media: [media({ buffered: [], loading: playbackMediaIsLoading(element) })] });
			expect(status.state).toBe('empty');
		}
		expect(playbackMediaIsLoading({ networkState: 2, readyState: 0 })).toBe(true);
		expect(playbackMediaIsLoading({ networkState: 2, readyState: 4 })).toBe(false);
	});
	it('bounds overview cache markers while retaining the worst status in each bucket', () => {
		const ranges = Array.from({ length: 240 }, (_, index) => ({
			start_ms: index * 10_000,
			end_ms: (index + 1) * 10_000,
			status: (index === 121 ? 'failed' : 'ready') as 'failed' | 'ready',
			blockers: index === 121 ? ['合成配音'] : []
		}));

		const projected = playbackRangesForViewport(
			ranges,
			{ startMs: 0, endMs: 2_400_000 },
			60
		);

		expect(projected).toHaveLength(60);
		expect(projected.some((range) => range.status === 'failed')).toBe(true);
		expect(projected.find((range) => range.status === 'failed')?.blockers).toContain('合成配音');
	});

	it('keeps exact cache chunks when the visible window is already bounded', () => {
		const ranges = buildPlaybackReadiness({
			durationMs: 30_000,
			chunkMs: 10_000,
			previewRanges,
			media: [media()]
		}).ranges;
		expect(playbackRangesForViewport(ranges, { startMs: 10_000, endMs: 20_000 }, 60)).toEqual([ranges[1]]);
	});
	it('never presents backend preview sprites as playable before media readiness is measured', () => {
		const pending = pendingPlaybackRanges(previewRanges);
		expect(pending.map((range) => range.status)).toEqual(['loading', 'loading', 'loading']);
		expect(pending.some((range) => range.status === 'ready')).toBe(false);
		expect(pending[0].blockers).toEqual(['正在检测视频与音频']);
	});

	it('only marks a segment ready when preview frames and every audible medium cover it', () => {
		const status = buildPlaybackReadiness({
			durationMs: 30_000, chunkMs: 10_000, previewRanges,
			media: [media({ buffered: [{ start_ms: 0, end_ms: 19_990 }] })]
		});
		expect(status.ranges.map((range) => range.status)).toEqual(['ready', 'ready', 'empty']);
		expect(status.progress).toBeCloseTo(2 / 3);
		expect(playbackReadinessAt(status, 25_000)?.blockers).toEqual(['视频画面']);
	});

	it('maps a trimmed clip timeline range back to its source buffer', () => {
		const status = buildPlaybackReadiness({
			durationMs: 30_000, chunkMs: 10_000, previewRanges,
			media: [media({
				id: 'dub', label: '合成配音 2', timeline_start_ms: 10_000, timeline_end_ms: 20_000,
				source_start_ms: 2_000, source_end_ms: 12_000, buffered: [{ start_ms: 2_000, end_ms: 7_000 }], loading: true
			})]
		});
		expect(status.ranges.map((range) => range.status)).toEqual(['ready', 'loading', 'ready']);
		expect(status.ranges[1].blockers).toEqual(['合成配音 2']);
	});

	it('keeps a generated-audio placeholder visibly loading until real audio exists', () => {
		const status = buildPlaybackReadiness({
			durationMs: 30_000, chunkMs: 10_000, previewRanges,
			media: [media({
				id: 'dub-pending', label: '合成配音 2 生成中', timeline_start_ms: 10_000, timeline_end_ms: 20_000,
				source_start_ms: 0, source_end_ms: 10_000, media_duration_ms: null, buffered: [], loading: true
			})]
		});
		expect(status.ranges.map((range) => range.status)).toEqual(['ready', 'loading', 'ready']);
		expect(status.ranges[1].blockers).toEqual(['合成配音 2 生成中']);
	});

	it('turns a formerly ready segment empty as soon as its browser buffer is evicted', () => {
		const ready = buildPlaybackReadiness({ durationMs: 30_000, chunkMs: 10_000, previewRanges, media: [media()] });
		const evicted = buildPlaybackReadiness({ durationMs: 30_000, chunkMs: 10_000, previewRanges, media: [media({ buffered: [] })] });
		expect(ready.progress).toBe(1);
		expect(evicted.progress).toBe(0);
	});

	it('does not show green while a fully buffered media element is still seeking or decoding', () => {
		const status = buildPlaybackReadiness({
			durationMs: 30_000,
			chunkMs: 10_000,
			previewRanges,
			media: [media({ loading: true })]
		});
		expect(status.ranges.every((range) => range.status === 'loading')).toBe(true);
		expect(status.ranges[0].blockers).toEqual(['视频画面']);
	});

	it('does not hide audible holes between buffered ranges', () => {
		expect(bufferedRangeCovered([{ start_ms: 0, end_ms: 5_000 }, { start_ms: 5_001, end_ms: 10_000 }], 0, 10_000)).toBe(true);
		expect(bufferedRangeCovered([{ start_ms: 0, end_ms: 5_000 }, { start_ms: 5_040, end_ms: 10_000 }], 0, 10_000)).toBe(false);
		expect(bufferedRangeCovered([{ start_ms: 0, end_ms: 5_000 }, { start_ms: 5_080, end_ms: 10_000 }], 0, 10_000)).toBe(false);
		expect(bufferedRangeCovered([{ start_ms: 0, end_ms: 5_000 }, { start_ms: 5_500, end_ms: 10_000 }], 0, 10_000)).toBe(false);
	});

	it('accepts a fully buffered clip even when the browser readyState lags at metadata', () => {
			expect(audioWindowReady({
			targetMs: 1_240,
			declaredEndMs: 2_720,
			durationMs: 2_720,
			buffered: [{ start_ms: 0, end_ms: 2_720 }],
			aheadMs: 1_200,
			durationToleranceMs: 50
		})).toBe(true);
	});

	it('uses concrete buffer coverage during clip-boundary seeks and still rejects real gaps', () => {
		const input = {
			targetMs: 1_000,
			declaredEndMs: 2_000,
			durationMs: 2_000,
			buffered: [{ start_ms: 0, end_ms: 1_400 }],
			aheadMs: 1_200,
			durationToleranceMs: 50
		};
		expect(audioWindowReady({ ...input, buffered: [{ start_ms: 0, end_ms: 2_000 }] })).toBe(true);
		expect(audioWindowReady(input)).toBe(false);
	});

	it('downgrades only the segment where a fully buffered medium is stalled', () => {
		const status = buildPlaybackReadiness({
			durationMs: 30_000,
			chunkMs: 10_000,
			previewRanges,
			media: [media({ pending_timeline_ms: 15_000 })]
		});
		expect(status.ranges.map((range) => range.status)).toEqual(['ready', 'loading', 'ready']);
		expect(status.progress).toBeCloseTo(2 / 3);
	});

	it('marks a timeline clip invalid when it extends past the available source audio', () => {
		const status = buildPlaybackReadiness({
			durationMs: 30_000,
			chunkMs: 10_000,
			previewRanges,
			media: [media({
				id: 'dub', label: '合成配音 2', timeline_start_ms: 10_000, timeline_end_ms: 20_000,
				source_start_ms: 2_000, source_end_ms: 9_000, buffered: [{ start_ms: 2_000, end_ms: 9_000 }]
			})]
		});
		expect(status.ranges[1].status).toBe('failed');
		expect(status.ranges[1].blockers).toContain('合成配音 2片段长度超过素材');
	});

	it('uses the decoded media duration even when stale clip metadata claims a longer source', () => {
		const status = buildPlaybackReadiness({
			durationMs: 30_000,
			chunkMs: 10_000,
			previewRanges,
			media: [media({
				id: 'dub', label: '合成配音 1', timeline_start_ms: 0, timeline_end_ms: 10_000,
				source_start_ms: 0, source_end_ms: 10_000, media_duration_ms: 4_000,
				buffered: [{ start_ms: 0, end_ms: 4_000 }]
			})]
		});
		expect(status.ranges[0].status).toBe('failed');
		expect(status.ranges[0].blockers).toContain('合成配音 1片段长度超过素材');
	});
});
