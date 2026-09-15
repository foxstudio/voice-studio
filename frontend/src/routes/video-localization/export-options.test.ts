import { describe, expect, it } from 'vitest';
import {
	buildDefaultMediaExportRequest,
	buildMediaExportAvailability,
	buildSubtitleMediaExportRequest
} from './export-options';
import {
	defaultTrackStates,
	resolveDubLaneStates
} from './studio-state';

describe('buildDefaultMediaExportRequest', () => {
	it('builds standalone subtitle requests without media-only options', () => {
		expect(buildSubtitleMediaExportRequest('asr')).toMatchObject({
			kind: 'subtitle',
			audio_tracks: [],
			dub_lanes: [],
			subtitle_tracks: ['asr'],
			localized_subtitle_variant: 'localized'
		});
		expect(buildSubtitleMediaExportRequest('dub')).toMatchObject({
			kind: 'subtitle',
			subtitle_tracks: ['localized'],
			localized_subtitle_variant: 'dub'
		});
		expect(buildSubtitleMediaExportRequest('bilingual')).toMatchObject({
			kind: 'subtitle',
			subtitle_tracks: ['asr', 'localized'],
			localized_subtitle_variant: 'localized'
		});
	});

	it('treats split dub clips that reference a source clip as available media', () => {
		const availability = buildMediaExportAvailability(
			{
				cues: [],
				localized_subtitles: [],
				dub_subtitles: [],
				source_media: {}
			} as never,
			null,
			[[
				{
					audio_path: null,
					media_source_clip_id: 'source-dub-clip'
				}
			]]
		);

		expect(availability.dubLaneMedia).toEqual([true]);
	});

	it('defaults to the subtitle tracks that are visible and audio tracks that are audible', () => {
		const trackStates = defaultTrackStates();
		trackStates.original.solo = false;
		trackStates.original.muted = true;
		trackStates.vocals.muted = true;
		trackStates.localizedSubtitles.muted = true;
		const dubLaneStates = resolveDubLaneStates(
			{ '0': { muted: false }, '1': { muted: true } },
			2,
			trackStates.dub
		);

		const request = buildDefaultMediaExportRequest({
			kind: 'video',
			trackStates,
			dubLaneStates,
			subtitleVisibility: {
				asr: true,
				localized: false
			},
			availability: {
				sourceVideo: true,
				audioTracks: { original: true, vocals: true, background: true },
				dubLaneMedia: [true, true],
				subtitles: { asr: true, localized: true, dub: true },
				sourceProfile: { width: 3840, height: 2160, frameRate: 24, audioSampleRate: 48000, audioChannels: 2 }
			}
		});

		expect(request.audio_tracks).toEqual(['background', 'dub']);
		expect(request.dub_lanes).toEqual([0]);
		expect(request.subtitle_tracks).toEqual(['asr']);
		expect(request.video_size).toBe('source');
		expect(request.video_quality).toBe('source');
		expect(request.audio_bitrate_kbps).toBe('source');
		expect(request.localized_subtitle_variant).toBe('localized');
	});

	it('exports the currently displayed synthesized-dub subtitle variant', () => {
		const trackStates = defaultTrackStates();
		const dubLaneStates = resolveDubLaneStates({}, 1, trackStates.dub);

		const request = buildDefaultMediaExportRequest({
			kind: 'video',
			trackStates,
			dubLaneStates,
			subtitleVisibility: {
				asr: true,
				localized: true
			},
			localizedSubtitleVariant: 'dub',
			availability: {
				sourceVideo: true,
				audioTracks: { original: true, vocals: true, background: true },
				dubLaneMedia: [true],
				subtitles: { asr: true, localized: true, dub: true },
				sourceProfile: { width: 1920, height: 1080, frameRate: 24, audioSampleRate: 48000, audioChannels: 2 }
			}
		});

		expect(request.subtitle_tracks).toContain('localized');
		expect(request.localized_subtitle_variant).toBe('dub');
	});

	it('keeps the output mix when temporary solo playback is active', () => {
		const trackStates = defaultTrackStates();
		trackStates.original.solo = false;
		trackStates.background.solo = true;
		const dubLaneStates = resolveDubLaneStates({}, 1, trackStates.dub);

		const request = buildDefaultMediaExportRequest({
			kind: 'audio',
			trackStates,
			dubLaneStates,
			subtitleVisibility: {
				asr: false,
				localized: false
			},
			availability: {
				sourceVideo: true,
				audioTracks: { original: true, vocals: true, background: true },
				dubLaneMedia: [true],
				subtitles: { asr: true, localized: true, dub: true },
				sourceProfile: { width: null, height: null, frameRate: null, audioSampleRate: null, audioChannels: null }
			}
		});

		expect(request.audio_tracks).toEqual(['original', 'vocals', 'background', 'dub']);
		expect(request.dub_lanes).toEqual([0]);
		expect(request.subtitle_tracks).toEqual([]);
	});

	it('uses the actual subtitle display state instead of stale track mute fields', () => {
		const trackStates = defaultTrackStates();
		trackStates.subtitles.muted = false;
		trackStates.localizedSubtitles.muted = true;
		const dubLaneStates = resolveDubLaneStates({}, 1, trackStates.dub);

		const request = buildDefaultMediaExportRequest({
			kind: 'video',
			trackStates,
			dubLaneStates,
			subtitleVisibility: {
				asr: false,
				localized: true
			},
			localizedSubtitleVariant: 'dub',
			availability: {
				sourceVideo: true,
				audioTracks: { original: true, vocals: true, background: true },
				dubLaneMedia: [true],
				subtitles: { asr: true, localized: true, dub: true },
				sourceProfile: {
					width: 3840,
					height: 2160,
					frameRate: 24,
					audioSampleRate: 48000,
					audioChannels: 2
				}
			}
		});

		expect(request.subtitle_tracks).toEqual(['localized']);
		expect(request.localized_subtitle_variant).toBe('dub');
	});
});
