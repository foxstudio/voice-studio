import { describe, expect, it } from 'vitest';
import type { ProjectMediaHealth, VideoLocalizationDraft } from '$lib/api/types';
import { attachAvailableMediaTracks } from './media-track-projection';

function mediaHealth(
	overrides: Partial<ProjectMediaHealth> = {}
): ProjectMediaHealth {
	return {
		contract_version: 'project-media-health-v1',
		package_status: 'available',
		source_video: {
			asset: 'source_video',
			status: 'available',
			resource_id: 'source_video',
			revision: 'video-r1',
			reason_code: null,
			recovery_action: 'none'
		},
		source_audio: {
			asset: 'source_audio',
			status: 'available',
			resource_id: 'source_audio',
			revision: 'audio-r1',
			reason_code: null,
			recovery_action: 'none',
			selected_source: 'source_media',
			candidates: []
		},
		vocals: {
			asset: 'vocals',
			status: 'available',
			resource_id: 'vocals',
			revision: 'vocals-r1',
			reason_code: null,
			recovery_action: 'none'
		},
		background: {
			asset: 'background',
			status: 'available',
			resource_id: 'background',
			revision: 'background-r1',
			reason_code: null,
			recovery_action: 'none'
		},
		stems_status: 'complete',
		...overrides
	};
}

function draft(overrides: Partial<VideoLocalizationDraft> = {}): VideoLocalizationDraft {
	return {
		source_media: {
			duration_ms: 12_000
		},
		stems: {},
		timeline_clips: [],
		ui_state: {
			track_states: {
				original: { muted: false, solo: true, volume: 1 },
				vocals: { muted: false, solo: false, volume: 1 },
				background: { muted: false, solo: false, volume: 1 },
				dub: { muted: false, solo: false, volume: 1 }
			}
		},
		...overrides
	} as unknown as VideoLocalizationDraft;
}

describe('media track projection', () => {
	it('attaches available media without changing the user-owned mix', () => {
		const value = draft();
		const projected = attachAvailableMediaTracks(value, mediaHealth());

		expect(projected.timeline_clips.map((clip) => clip.track_id)).toEqual([
			'original',
			'vocals',
			'background'
		]);
		expect(projected.timeline_clips.map((clip) => clip.media_source_clip_id)).toEqual([
			'media_original',
			'media_vocals',
			'media_background'
		]);
		expect(projected.timeline_clips.every((clip) => !clip.audio_path)).toBe(true);
		expect(projected.ui_state).toEqual(value.ui_state);
	});

	it('keeps an explicitly detached media track detached', () => {
		const projected = attachAvailableMediaTracks(draft({
			ui_state: {
				disabled_media_tracks: ['original'],
				track_states: {
					original: { muted: false, solo: true, volume: 1 }
				}
			}
		} as Partial<VideoLocalizationDraft>), mediaHealth());

		expect(projected.timeline_clips.some((clip) => clip.track_id === 'original')).toBe(false);
		expect(projected.timeline_clips.some((clip) => clip.track_id === 'vocals')).toBe(true);
	});

	it('does not duplicate an existing arrangement clip', () => {
		const projected = attachAvailableMediaTracks(draft({
			timeline_clips: [{
				clip_id: 'custom-original',
				track_id: 'original',
				start_ms: 500,
				end_ms: 4_500,
				audio_path: '/project/source.wav'
			}]
		} as Partial<VideoLocalizationDraft>), mediaHealth());

		expect(projected.timeline_clips.filter((clip) => clip.track_id === 'original')).toHaveLength(1);
		expect(projected.timeline_clips.find((clip) => clip.track_id === 'original')?.clip_id).toBe('custom-original');
	});

	it('does not create ghost clips for configured but missing media', () => {
		const missingAsset = (asset: 'source_video' | 'source_audio' | 'vocals' | 'background') => ({
			asset,
			status: 'missing' as const,
			resource_id: null,
			revision: null,
			reason_code: 'media_file_missing',
			recovery_action: 'relink_source' as const
		});
		const projected = attachAvailableMediaTracks(
			draft(),
			mediaHealth({
				source_video: missingAsset('source_video'),
				source_audio: {
					...missingAsset('source_audio'),
					asset: 'source_audio',
					selected_source: null,
					candidates: []
				},
				vocals: missingAsset('vocals'),
				background: missingAsset('background'),
				stems_status: 'missing'
			})
		);

		expect(projected.timeline_clips).toEqual([]);
	});

	it('uses the selected fallback original candidate and keeps only available stems', () => {
		const projected = attachAvailableMediaTracks(draft({
			source_media: {
				duration_ms: 12_000,
				audio_path: '/project/stale-source.wav'
			},
			stems: {
				original_audio_path: '/project/original.wav',
				vocals_clean_path: '/project/vocals.wav',
				background_path: '/project/missing-background.wav'
			},
		} as Partial<VideoLocalizationDraft>), mediaHealth({
			source_audio: {
				...mediaHealth().source_audio,
				selected_source: 'original_stem'
			},
			background: {
				asset: 'background',
				status: 'missing',
				resource_id: null,
				revision: null,
				reason_code: 'media_file_missing',
				recovery_action: 'separate_stems'
			},
			stems_status: 'partial'
		}));

		expect(projected.timeline_clips.map((clip) => clip.track_id)).toEqual(['original', 'vocals']);
		expect(projected.timeline_clips.find((clip) => clip.track_id === 'original')?.media_source_clip_id)
			.toBe('media_original');
	});
});
