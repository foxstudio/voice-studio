import type { VideoPreviewCacheStatus } from '$lib/api/types';

export function cachedPreviewRangeAt(cache: VideoPreviewCacheStatus | null, timeMs: number) {
	if (!cache) return null;
	return cache.ranges.find((range) => range.status === 'ready' && timeMs >= range.start_ms && timeMs < range.end_ms) ?? null;
}

export function previewCacheActivityLabel(cache: VideoPreviewCacheStatus | null) {
	if (!cache) return '播放缓存尚未生成';
	if (cache.state === 'failed') return '预览画面生成失败 · 点击“刷新缓存”重试';
	if (cache.phase === 'queued') return '预览画面已排队，等待生成';
	if (cache.phase === 'rendering') {
		const current = cache.active_chunk == null ? '' : ` ${cache.active_chunk + 1}/${cache.total_chunks}`;
		return `正在生成预览画面${current}`;
	}
	return '正在检测视频与全部音轨';
}

export function previewSpriteCell(cache: VideoPreviewCacheStatus, timeMs: number) {
	const chunkMs = Math.max(1, cache.chunk_ms);
	const intervalMs = Math.max(1, cache.frame_interval_ms);
	const chunkIndex = Math.max(0, Math.floor(timeMs / chunkMs));
	const range = cache.ranges[chunkIndex];
	const localMs = Math.max(0, timeMs - chunkIndex * chunkMs);
	const frameCount = Math.max(1, range?.frame_count ?? 1);
	const frameIndex = Math.min(frameCount - 1, Math.floor(localMs / intervalMs));
	const columns = Math.max(1, cache.sprite_columns);
	return {
		chunkIndex,
		frameIndex,
		column: frameIndex % columns,
		row: Math.floor(frameIndex / columns),
		columns,
		rows: Math.max(1, range?.sprite_rows ?? cache.sprite_rows)
	};
}

export function containedMediaRect(containerWidth: number, containerHeight: number, mediaWidth: number, mediaHeight: number) {
	if (containerWidth <= 0 || containerHeight <= 0 || mediaWidth <= 0 || mediaHeight <= 0) {
		return { left: 0, top: 0, width: 100, height: 100 };
	}
	const containerRatio = containerWidth / containerHeight;
	const mediaRatio = mediaWidth / mediaHeight;
	if (containerRatio > mediaRatio) {
		const width = (mediaRatio / containerRatio) * 100;
		return { left: (100 - width) / 2, top: 0, width, height: 100 };
	}
	const height = (containerRatio / mediaRatio) * 100;
	return { left: 0, top: (100 - height) / 2, width: 100, height };
}
