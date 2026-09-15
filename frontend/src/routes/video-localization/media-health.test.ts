import { describe, expect, it } from 'vitest';
import type { ProjectMediaHealth } from '$lib/api/types';
import {
	mediaAssetAvailable,
	mediaRepairLabel,
	mediaTrackResourceId,
	sourceVideoConfigured
} from './media-health';
import { sourceAudioUrl, sourceVideoUrl, stemAudioUrl } from './utils';

function health(
	overrides: Partial<ProjectMediaHealth> = {}
): ProjectMediaHealth {
	const asset = (name: 'source_video' | 'vocals' | 'background') => ({
		asset: name,
		status: 'missing' as const,
		resource_id: null,
		revision: null,
		reason_code: `${name}_missing`,
		recovery_action: 'relink_source' as const
	});
	return {
		contract_version: 'project-media-health-v1',
		package_status: 'available',
		source_video: asset('source_video'),
		source_audio: {
			asset: 'source_audio',
			status: 'missing',
			resource_id: null,
			revision: null,
			reason_code: 'source_audio_missing',
			recovery_action: 'extract_source_audio',
			selected_source: null,
			candidates: []
		},
		vocals: asset('vocals'),
		background: asset('background'),
		stems_status: 'missing',
		...overrides
	};
}

describe('project media health selectors', () => {
	it('does not treat configured but missing media as available', () => {
		const value = health();
		expect(sourceVideoConfigured(value)).toBe(true);
		expect(mediaAssetAvailable(value, 'source_video')).toBe(false);
		expect(sourceVideoUrl('project-1', value)).toBe('');
		expect(mediaRepairLabel(value, 'source_audio')).toBe('重新抽取原音轨');
	});

	it('uses the resolver-selected original-audio fallback', () => {
		const value = health({
			source_audio: {
				asset: 'source_audio',
				status: 'available',
				resource_id: 'source-audio',
				revision: 'rev/audio',
				reason_code: null,
				recovery_action: 'none',
				selected_source: 'original_stem',
				candidates: []
			}
		});
		expect(mediaTrackResourceId(value, 'original')).toBe('source-audio');
		expect(sourceAudioUrl('project-1', value)).toContain('rev%2Faudio');
	});

	it('only emits preview URLs for available assets', () => {
		const value = health({
			source_video: {
				asset: 'source_video',
				status: 'available',
				resource_id: 'source-video',
				revision: 'video-1',
				reason_code: null,
				recovery_action: 'none'
			},
			vocals: {
				asset: 'vocals',
				status: 'available',
				resource_id: 'vocals',
				revision: 'vocals-1',
				reason_code: null,
				recovery_action: 'none'
			}
		});
		expect(sourceVideoUrl('project-1', value, 0, 'source')).toContain('variant=source');
		expect(sourceVideoUrl('project-1', value, 123, 'preview')).toContain('variant=preview');
		expect(stemAudioUrl('project-1', value, 'vocals', 0, 'source')).toContain('variant=source');
		expect(stemAudioUrl('project-1', value, 'vocals', 123, 'preview')).toContain('variant=preview');
		expect(stemAudioUrl('project-1', value, 'background')).toBe('');
	});
});
