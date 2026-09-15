import { Api } from '$lib/api';
import type { VideoPlaybackProxyRequest, VideoPlaybackProxyStatus } from '$lib/api/types';

export type PreviewAudioAsset = 'source_audio' | 'vocals' | 'background';
export type PreviewPlaybackVariant = 'source' | 'preview';

export type PreviewProxyPreparation = {
	changed?: boolean;
	profile?: string;
	variant?: PreviewPlaybackVariant;
};

export type PreviewAudioProxyPreparation = {
	changed?: boolean;
	variants: Partial<Record<PreviewAudioAsset, PreviewPlaybackVariant>>;
};

type PreviewMediaTransport = {
	prepareVideoLocalizationPreviewVideo: (
		projectId: string,
		request: VideoPlaybackProxyRequest
	) => Promise<VideoPlaybackProxyStatus>;
	prepareVideoLocalizationSourceAudioPreview: (projectId: string) => Promise<PreviewProxyPreparation>;
	prepareVideoLocalizationStemAudioPreview: (
		projectId: string,
		kind: 'vocals' | 'background'
	) => Promise<PreviewProxyPreparation>;
};

export class VideoLocalizationPreviewMediaClient {
	constructor(private readonly transport: PreviewMediaTransport = Api) {}

	prepareEditingProxy = (projectId: string, request: VideoPlaybackProxyRequest) =>
		this.transport.prepareVideoLocalizationPreviewVideo(projectId, request);

	prepareAudioPreviewProxies = async (
		projectId: string,
		assets: PreviewAudioAsset[]
	): Promise<PreviewAudioProxyPreparation> => {
		let changed = false;
		const variants: PreviewAudioProxyPreparation['variants'] = {};
		for (const asset of assets) {
			try {
				const result = asset === 'source_audio'
					? await this.transport.prepareVideoLocalizationSourceAudioPreview(projectId)
					: await this.transport.prepareVideoLocalizationStemAudioPreview(projectId, asset);
				changed = changed || result.changed === true;
				if (result.variant) variants[asset] = result.variant;
			} catch {
				// Each proxy is optional; one failed asset must not block the others.
			}
		}
		return { changed, variants };
	};
}
