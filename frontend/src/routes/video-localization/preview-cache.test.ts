import { describe, expect, it } from 'vitest';
import type { VideoPreviewCacheStatus } from '$lib/api/types';
import { cachedPreviewRangeAt, containedMediaRect, previewCacheActivityLabel, previewSpriteCell } from './preview-cache';

function cache(): VideoPreviewCacheStatus {
	return {
		contract_version: 'video-preview-cache-status-v1',
		state: 'partial', profile: 'test', revision: 'r1', mode: 'auto', duration_ms: 25_000,
		phase: 'idle', active_chunk: null, started_at: null, updated_at: null, retryable: false,
		chunk_ms: 10_000, frame_interval_ms: 500, frame_width: 480, frame_height: 270,
		sprite_columns: 5, sprite_rows: 4, ready_chunks: 2, total_chunks: 3, progress: 2 / 3,
		cached_bytes: 100, capacity_bytes: 1_000, error: null,
		ranges: [
			{ start_ms: 0, end_ms: 10_000, status: 'ready', frame_count: 20, sprite_rows: 4 },
			{ start_ms: 10_000, end_ms: 20_000, status: 'rendering', frame_count: 0, sprite_rows: 0 },
			{ start_ms: 20_000, end_ms: 25_000, status: 'ready', frame_count: 10, sprite_rows: 2 }
		]
	};
}

describe('timeline preview cache', () => {
	it('only treats completed ranges as immediately previewable', () => {
		expect(cachedPreviewRangeAt(cache(), 9_999)?.start_ms).toBe(0);
		expect(cachedPreviewRangeAt(cache(), 15_000)).toBeNull();
		expect(cachedPreviewRangeAt(cache(), 24_999)?.start_ms).toBe(20_000);
	});

	it('maps time to a sprite cell and clamps the short final chunk', () => {
		expect(previewSpriteCell(cache(), 1_250)).toMatchObject({ chunkIndex: 0, frameIndex: 2, column: 2, row: 0, rows: 4 });
		expect(previewSpriteCell(cache(), 24_999)).toMatchObject({ chunkIndex: 2, frameIndex: 9, column: 4, row: 1, rows: 2 });
	});

	it('fits cached frames into the same contained media rectangle as the source video', () => {
		expect(containedMediaRect(1000, 800, 1920, 1080)).toEqual({ left: 0, top: 14.84375, width: 100, height: 70.3125 });
		expect(containedMediaRect(1200, 500, 1920, 1080)).toEqual({ left: 12.962962962962962, top: 0, width: 74.07407407407408, height: 100 });
		expect(containedMediaRect(0, 0, 0, 0)).toEqual({ left: 0, top: 0, width: 100, height: 100 });
	});

	it('explains the real backend cache phase without pretending a chunk is rendering', () => {
		const current = cache();
		current.state = 'building';
		current.phase = 'queued';
		expect(previewCacheActivityLabel(current)).toBe('预览画面已排队，等待生成');

		current.phase = 'rendering';
		current.active_chunk = 1;
		expect(previewCacheActivityLabel(current)).toBe('正在生成预览画面 2/3');

		current.state = 'failed';
		current.phase = 'failed';
		expect(previewCacheActivityLabel(current)).toContain('刷新缓存');
	});
});
