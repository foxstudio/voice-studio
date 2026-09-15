import { render } from 'svelte/server';
import { describe, expect, it, vi } from 'vitest';
import ExportDialog from './ExportDialog.svelte';
import type {
	MediaExportAvailability,
	MediaExportRequest
} from './export-options';

const subtitleRequest: MediaExportRequest = {
	schema_version: 'v1',
	kind: 'subtitle',
	audio_tracks: [],
	dub_lanes: [],
	subtitle_tracks: ['localized'],
	localized_subtitle_variant: 'dub',
	video_size: 'source',
	video_quality: 'source',
	audio_format: 'wav',
	audio_bitrate_kbps: 'source'
};

const availability: MediaExportAvailability = {
	sourceVideo: true,
	audioTracks: {
		original: true,
		vocals: true,
		background: true
	},
	dubLaneMedia: [true],
	subtitles: {
		asr: true,
		localized: true,
		dub: true
	},
	sourceProfile: {
		width: 1920,
		height: 1080,
		frameRate: 24,
		audioSampleRate: 48_000,
		audioChannels: 2
	}
};

describe('ExportDialog subtitle delivery', () => {
	it('renders destination, exact filename, and the shared export action', () => {
		const { body } = render(ExportDialog, {
			props: {
				request: subtitleRequest,
				availability,
				destinationPath: '/chosen/subtitles',
				outputFilename: '最终配音字幕.srt',
				onClose: vi.fn(),
				onChooseDestination: vi.fn(),
				onRequestChange: vi.fn(),
				onOutputFilenameChange: vi.fn(),
				onExport: vi.fn()
			}
		});

		expect(body).toContain('保存位置');
		expect(body).toContain('/chosen/subtitles');
		expect(body).toContain('文件名称');
		expect(body).toContain('value="最终配音字幕.srt"');
		expect(body).toContain('导出字幕');
		expect(body).toContain('配音字幕');
		expect(body).not.toContain('单独下载字幕');
	});
});
