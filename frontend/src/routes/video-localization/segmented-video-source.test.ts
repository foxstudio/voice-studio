import { describe, expect, it } from 'vitest';
import type { VideoPlaybackProxyStatus } from '$lib/api/types';
import {
	configureAppendWindow,
	segmentIndexesForStatus,
	segmentRetentionRange,
	segmentUrl
} from './segmented-video-source';

function status(overrides: Partial<VideoPlaybackProxyStatus> = {}): VideoPlaybackProxyStatus {
	return {
		contract_version: 'video-playback-proxy-status-v2',
		state: 'partial',
		mode: 'segmented',
		variant: 'segments',
		playable: true,
		profile: 'segmented-h264-fmp4-v1',
		revision: 'generation-1',
		duration_ms: 40_000,
		segment_ms: 4_000,
		requested_range: { start_ms: 16_000, end_ms: 28_000 },
		ready_ranges: [
			{ start_ms: 0, end_ms: 8_000 },
			{ start_ms: 12_000, end_ms: 28_000 }
		],
		active_segment: 7,
		ready_segments: 6,
		total_segments: 10,
		progress: 0.6,
		updated_at: null,
		retryable: false,
		error: null,
		...overrides
	};
}

describe('segmented video source', () => {
	it('loads only a bounded ready window around the requested edit position', () => {
		expect(segmentIndexesForStatus(status())).toEqual([4, 5, 6, 3]);
	});

	it('evicts decoded video outside the same bounded edit window', () => {
		expect(segmentRetentionRange(status())).toEqual({ startSeconds: 12, endSeconds: 32 });
		expect(segmentRetentionRange(status({
			duration_ms: 2_400_000,
			total_segments: 600,
			requested_range: { start_ms: 1_800_000, end_ms: 1_812_000 }
		}))).toEqual({ startSeconds: 1796, endSeconds: 1816 });
	});

	it('keeps segment URLs project scoped and generation stable', () => {
		expect(segmentUrl('project/1', 3, 'generation 1')).toBe(
			'/api/projects/project%2F1/video-localization/source-media/preview-video/segments/3?revision=generation%201'
		);
	});

	it('expands the append window end before advancing its start', () => {
		const writes: string[] = [];
		let appendWindowStart = 0;
		let appendWindowEnd = Number.POSITIVE_INFINITY;
		const sourceBuffer = {
			set timestampOffset(value: number) { writes.push(`offset:${value}`); },
			get appendWindowStart() { return appendWindowStart; },
			set appendWindowStart(value: number) {
				if (value >= appendWindowEnd) throw new RangeError('append window start must precede end');
				appendWindowStart = value;
				writes.push(`start:${value}`);
			},
			get appendWindowEnd() { return appendWindowEnd; },
			set appendWindowEnd(value: number) {
				if (value <= appendWindowStart) throw new RangeError('append window end must follow start');
				appendWindowEnd = value;
				writes.push(`end:${value}`);
			}
		};

		configureAppendWindow(sourceBuffer as SourceBuffer, 4, 8);

		expect(writes).toEqual(['offset:4', 'end:8', 'start:4']);
	});

	it('lowers the append window start before rewinding its end', () => {
		const writes: string[] = [];
		let appendWindowStart = 100;
		let appendWindowEnd = 104;
		const sourceBuffer = {
			set timestampOffset(value: number) { writes.push(`offset:${value}`); },
			get appendWindowStart() { return appendWindowStart; },
			set appendWindowStart(value: number) {
				if (value >= appendWindowEnd) throw new RangeError('append window start must precede end');
				appendWindowStart = value;
				writes.push(`start:${value}`);
			},
			get appendWindowEnd() { return appendWindowEnd; },
			set appendWindowEnd(value: number) {
				if (value <= appendWindowStart) throw new RangeError('append window end must follow start');
				appendWindowEnd = value;
				writes.push(`end:${value}`);
			}
		};

		configureAppendWindow(sourceBuffer as SourceBuffer, 84, 88);

		expect(writes).toEqual(['offset:84', 'start:84', 'end:88']);
	});
});
