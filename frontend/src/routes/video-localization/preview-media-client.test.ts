import { describe, expect, it, vi } from 'vitest';
import { VideoLocalizationPreviewMediaClient } from './preview-media-client';

describe('VideoLocalizationPreviewMediaClient', () => {
	it('delegates editing proxy preparation through the typed transport', async () => {
		const prepareVideo = vi.fn(async () => ({
			contract_version: 'video-playback-proxy-status-v2' as const,
			state: 'ready' as const,
			mode: 'source' as const,
			variant: 'source' as const,
			playable: true,
			profile: 'source',
			revision: 'proxy-r1',
			duration_ms: 10_000,
			segment_ms: 4_000,
			requested_range: { start_ms: 0, end_ms: 10_000 },
			ready_ranges: [{ start_ms: 0, end_ms: 10_000 }],
			active_segment: null,
			ready_segments: 3,
			total_segments: 3,
			progress: 1,
			updated_at: null,
			retryable: false,
			error: null
		}));
		const client = new VideoLocalizationPreviewMediaClient({
			prepareVideoLocalizationPreviewVideo: prepareVideo,
			prepareVideoLocalizationSourceAudioPreview: vi.fn(),
			prepareVideoLocalizationStemAudioPreview: vi.fn()
		});

		const request = { source_playable: true, start_ms: 0 };
		await expect(client.prepareEditingProxy('project-a', request)).resolves.toEqual({
			contract_version: 'video-playback-proxy-status-v2',
			state: 'ready',
			mode: 'source',
			variant: 'source',
			playable: true,
			profile: 'source',
			revision: 'proxy-r1',
			duration_ms: 10_000,
			segment_ms: 4_000,
			requested_range: { start_ms: 0, end_ms: 10_000 },
			ready_ranges: [{ start_ms: 0, end_ms: 10_000 }],
			active_segment: null,
			ready_segments: 3,
			total_segments: 3,
			progress: 1,
			updated_at: null,
			retryable: false,
			error: null
		});
		expect(prepareVideo).toHaveBeenCalledWith('project-a', request);
	});

	it('prepares only requested audio assets and reports whether any proxy changed', async () => {
		const prepareSource = vi.fn(async () => ({ changed: false, variant: 'preview' as const }));
		const prepareStem = vi.fn(async (_projectId: string, kind: 'vocals' | 'background') => ({
			changed: kind === 'background',
			variant: 'preview' as const
		}));
		const client = new VideoLocalizationPreviewMediaClient({
			prepareVideoLocalizationPreviewVideo: vi.fn(),
			prepareVideoLocalizationSourceAudioPreview: prepareSource,
			prepareVideoLocalizationStemAudioPreview: prepareStem
		});

		await expect(client.prepareAudioPreviewProxies(
			'project-a',
			['source_audio', 'background']
		)).resolves.toEqual({
			changed: true,
			variants: { source_audio: 'preview', background: 'preview' }
		});
		expect(prepareSource).toHaveBeenCalledWith('project-a');
		expect(prepareStem).toHaveBeenCalledWith('project-a', 'background');
		expect(prepareStem).not.toHaveBeenCalledWith('project-a', 'vocals');
	});

	it('continues preparing remaining audio assets after one optional proxy fails', async () => {
		const prepareStem = vi.fn(async (_projectId: string, kind: 'vocals' | 'background') => {
			if (kind === 'vocals') throw new Error('codec unavailable');
			return { changed: true, variant: 'preview' as const };
		});
		const client = new VideoLocalizationPreviewMediaClient({
			prepareVideoLocalizationPreviewVideo: vi.fn(),
			prepareVideoLocalizationSourceAudioPreview: vi.fn(),
			prepareVideoLocalizationStemAudioPreview: prepareStem
		});

		await expect(client.prepareAudioPreviewProxies(
			'project-a',
			['vocals', 'background']
		)).resolves.toEqual({ changed: true, variants: { background: 'preview' } });
		expect(prepareStem).toHaveBeenCalledTimes(2);
	});
});
