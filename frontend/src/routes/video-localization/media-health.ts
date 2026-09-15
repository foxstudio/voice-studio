import type {
	ProjectMediaHealth
} from '$lib/api/types';

export type ProjectMediaTrackId = 'original' | 'vocals' | 'background';

export function mediaAssetAvailable(
	health: ProjectMediaHealth | null | undefined,
	asset: 'source_video' | 'source_audio' | 'vocals' | 'background'
) {
	return health?.[asset].status === 'available';
}

export function sourceVideoConfigured(
	health: ProjectMediaHealth | null | undefined
) {
	const status = health?.source_video.status ?? 'unknown';
	return status !== 'unknown' && status !== 'unconfigured';
}

export function mediaTrackResourceId(
	health: ProjectMediaHealth | null | undefined,
	trackId: ProjectMediaTrackId
): string | null {
	if (trackId === 'original') {
		return mediaAssetAvailable(health, 'source_audio')
			? health?.source_audio.resource_id ?? null
			: null;
	}
	if (trackId === 'vocals') {
		return mediaAssetAvailable(health, 'vocals')
			? health?.vocals.resource_id ?? null
			: null;
	}
	return mediaAssetAvailable(health, 'background')
		? health?.background.resource_id ?? null
		: null;
}

export function mediaRepairLabel(
	health: ProjectMediaHealth | null | undefined,
	asset: 'source_video' | 'source_audio' | 'vocals' | 'background'
) {
	const value = health?.[asset];
	if (!value || value.status === 'unknown') return '正在检查媒体';
	if (value.status === 'available') return '';
	if (value.recovery_action === 'extract_source_audio') return '重新抽取原音轨';
	if (value.recovery_action === 'separate_stems') return '重新分离音轨';
	if (value.recovery_action === 'import_source') return '导入源视频';
	if (value.recovery_action === 'rescan_project') return '重新扫描项目';
	return '重新关联媒体';
}
