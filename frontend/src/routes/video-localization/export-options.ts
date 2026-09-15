import type {
	VideoLocalizationDubLaneStates,
	VideoLocalizationTrackStates
} from './studio-state';
import type {
	ProjectMediaHealth,
	VideoLocalizationDraft,
	VideoLocalizationSourceMedia
} from '$lib/api/types';
import { mediaAssetAvailable } from './media-health';

export type MediaExportKind = 'video' | 'audio' | 'subtitle';
export type MediaExportAudioTrackId = 'original' | 'vocals' | 'background' | 'dub';
export type MediaExportSubtitleTrackId = 'asr' | 'localized';
export type MediaExportLocalizedSubtitleVariant = 'localized' | 'dub';
export type StandaloneSubtitleExportSelection =
	| 'asr'
	| 'localized'
	| 'dub'
	| 'bilingual';
export type MediaExportVideoSize = 'source' | '1080p' | '720p';
export type MediaExportVideoQuality = 'source' | 'high' | 'balanced' | 'compact';
export type MediaExportAudioFormat = 'wav' | 'mp3';
export type MediaExportSourceProfile = {
	width: number | null;
	height: number | null;
	frameRate: number | null;
	audioSampleRate: number | null;
	audioChannels: number | null;
};

export type MediaExportRequest = {
	schema_version: 'v1';
	kind: MediaExportKind;
	audio_tracks: MediaExportAudioTrackId[];
	dub_lanes: number[];
	subtitle_tracks: MediaExportSubtitleTrackId[];
	localized_subtitle_variant: MediaExportLocalizedSubtitleVariant;
	video_size: MediaExportVideoSize;
	video_quality: MediaExportVideoQuality;
	audio_format: MediaExportAudioFormat;
	audio_bitrate_kbps: 'source' | 128 | 192 | 256;
};

export type MediaExportAvailability = {
	sourceVideo: boolean;
	audioTracks: Record<'original' | 'vocals' | 'background', boolean>;
	dubLaneMedia: boolean[];
	subtitles: Record<MediaExportSubtitleTrackId | 'dub', boolean>;
	sourceProfile: MediaExportSourceProfile;
};

export function mediaExportSourceProfile(
	sourceMedia: VideoLocalizationSourceMedia | null | undefined
): MediaExportSourceProfile {
	return {
		width: sourceMedia?.width ?? null,
		height: sourceMedia?.height ?? null,
		frameRate: sourceMedia?.frame_rate ?? null,
		audioSampleRate: Number(sourceMedia?.metadata?.audio_sample_rate) || null,
		audioChannels: Number(sourceMedia?.metadata?.audio_channels) || null
	};
}

export function buildMediaExportAvailability(
	draft: VideoLocalizationDraft | null,
	mediaHealth: ProjectMediaHealth | null,
	dubLanes: Array<Array<{
		audio_path?: string | null;
		media_source_clip_id?: string | null;
	}>>
): MediaExportAvailability {
	const fallbackDubAvailable = Boolean(
		draft?.localized_subtitles.some((subtitle) => subtitle.tts_audio_path)
		|| draft?.cues.some((cue) => cue.tts_audio_path)
	);
	const laneCount = Math.max(1, dubLanes.length);
	return {
		sourceVideo: mediaAssetAvailable(mediaHealth, 'source_video'),
		audioTracks: {
			original: mediaAssetAvailable(mediaHealth, 'source_audio'),
			vocals: mediaAssetAvailable(mediaHealth, 'vocals'),
			background: mediaAssetAvailable(mediaHealth, 'background')
		},
		dubLaneMedia: Array.from({ length: laneCount }, (_, lane) =>
			Boolean(
				dubLanes[lane]?.some(
					(clip) => clip.audio_path || clip.media_source_clip_id
				) || (lane === 0 && fallbackDubAvailable)
			)
		),
		subtitles: {
			asr: Boolean(draft?.cues.length),
			localized: Boolean(draft?.localized_subtitles.length),
			dub: Boolean(draft?.dub_subtitles?.length)
		},
		sourceProfile: mediaExportSourceProfile(draft?.source_media)
	};
}

export function buildDefaultMediaExportRequest({
	kind,
	trackStates,
	dubLaneStates,
	subtitleVisibility,
	localizedSubtitleVariant = 'localized',
	availability
}: {
	kind: MediaExportKind;
	trackStates: VideoLocalizationTrackStates;
	dubLaneStates: VideoLocalizationDubLaneStates;
	subtitleVisibility: {
		asr: boolean;
		localized: boolean;
	};
	localizedSubtitleVariant?: MediaExportLocalizedSubtitleVariant;
	availability: MediaExportAvailability;
}): MediaExportRequest {
	// Solo belongs to the timeline audition. A new export starts from the
	// persisted output mix: media that is intentionally muted or silent stays
	// out, while an accidental temporary solo never removes the other tracks.
	const audioTracks: MediaExportAudioTrackId[] = (
		['original', 'vocals', 'background'] as const
	).filter((trackId) => (
		availability.audioTracks[trackId]
		&& !trackStates[trackId].muted
		&& trackStates[trackId].volume > 0
	));
	const dubLanes = availability.dubLaneMedia
		.map((hasMedia, lane) => ({
			lane,
			hasMedia,
			state: dubLaneStates[String(lane)] ?? trackStates.dub
		}))
		.filter(({ hasMedia, state }) => hasMedia && !state.muted && state.volume > 0)
		.map(({ lane }) => lane);
	if (dubLanes.length) audioTracks.push('dub');
	const subtitleTracks: MediaExportSubtitleTrackId[] = [];
	if (
		kind === 'video'
		&& availability.subtitles.asr
		&& subtitleVisibility.asr
	) subtitleTracks.push('asr');
	if (
		kind === 'video'
		&& availability.subtitles[localizedSubtitleVariant]
		&& subtitleVisibility.localized
	) subtitleTracks.push('localized');
	return {
		schema_version: 'v1',
		kind,
		audio_tracks: audioTracks,
		dub_lanes: dubLanes,
		subtitle_tracks: subtitleTracks,
		localized_subtitle_variant: localizedSubtitleVariant,
		video_size: 'source',
		video_quality: 'source',
		audio_format: 'wav',
		audio_bitrate_kbps: 'source'
	};
}

export function buildSubtitleMediaExportRequest(
	selection: StandaloneSubtitleExportSelection
): MediaExportRequest {
	return {
		schema_version: 'v1',
		kind: 'subtitle',
		audio_tracks: [],
		dub_lanes: [],
		subtitle_tracks: selection === 'bilingual'
			? ['asr', 'localized']
			: selection === 'asr'
				? ['asr']
				: ['localized'],
		localized_subtitle_variant: selection === 'dub' ? 'dub' : 'localized',
		video_size: 'source',
		video_quality: 'source',
		audio_format: 'wav',
		audio_bitrate_kbps: 'source'
	};
}
