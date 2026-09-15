import { describe, expect, it, vi } from 'vitest';
import { VideoLocalizationExportController } from './export-controller';
import type { MediaExportRequest } from './export-options';

const request: MediaExportRequest = {
	schema_version: 'v1',
	kind: 'audio',
	audio_tracks: ['background', 'dub'],
	dub_lanes: [0],
	subtitle_tracks: [],
	localized_subtitle_variant: 'localized',
	video_size: 'source',
	video_quality: 'balanced',
	audio_format: 'wav',
	audio_bitrate_kbps: 192
};

describe('VideoLocalizationExportController', () => {
	it('asks the backend for an opaque default destination', async () => {
		const fetcher = vi.fn(async () =>
			Response.json({
				status: 'selected',
				destination_id: 'vld_destination',
				display_path: '/chosen'
			})
		);
		const controller = new VideoLocalizationExportController(fetcher as typeof fetch);

		const selected = await controller.selectDestination('project_1', 'default');

		expect(selected.destination_id).toBe('vld_destination');
		expect(fetcher).toHaveBeenCalledWith(
			'/api/projects/project_1/video-localization/export/destination',
			{
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ mode: 'default' })
			}
		);
	});

	it('submits the destination capability and render contract as a background operation', async () => {
		const fetcher = vi.fn(async () =>
			Response.json({
				operation_id: 'export_1',
				project_id: 'project_1',
				kind: 'media_export',
				status: 'queued',
				label: '导出成品',
				progress: 0,
				error_code: null,
				error_message: null,
				cancel_requested: false,
				result_summary: {},
				parameters: {},
				created_at: '2026-08-04T00:00:00',
				started_at: null,
				completed_at: null
			})
		);
		const controller = new VideoLocalizationExportController(fetcher as typeof fetch);

		const operation = await controller.startMediaExport(
			'project_1',
			'vld_destination',
			'最终成品.wav',
			request
		);

		expect(operation.kind).toBe('media_export');
		expect(fetcher).toHaveBeenCalledWith(
			'/api/projects/project_1/video-localization/export/render',
			{
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					schema_version: 'v1',
					destination_id: 'vld_destination',
					output_filename: '最终成品.wav',
					render: request
				})
			}
		);
	});

	it('asks the backend naming policy for the default editable filename', async () => {
		const fetcher = vi.fn(async () =>
			Response.json({
				schema_version: 'v1',
				output_filename: '项目__音频__背景+配音.wav'
			})
		);
		const controller = new VideoLocalizationExportController(fetcher as typeof fetch);

		const preview = await controller.defaultFilename(
			'project_1',
			request
		);

		expect(preview.output_filename).toBe('项目__音频__背景+配音.wav');
		expect(fetcher).toHaveBeenCalledWith(
			'/api/projects/project_1/video-localization/export/filename-preview',
			{
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(request)
			}
		);
	});

	it('reads real progress from the shared operation endpoint', async () => {
		const fetcher = vi.fn(async () =>
			Response.json({
				operation_id: 'export_1',
				project_id: 'project_1',
				kind: 'media_export',
				status: 'running',
				label: '导出成品',
				progress: 0.42,
				error_code: null,
				error_message: null,
				cancel_requested: false,
				result_summary: { stage: '正在渲染视频 30%' },
				parameters: {},
				created_at: '2026-08-04T00:00:00',
				started_at: '2026-08-04T00:00:01',
				completed_at: null
			})
		);
		const controller = new VideoLocalizationExportController(fetcher as typeof fetch);

		const operation = await controller.operation('project_1', 'export_1');

		expect(operation.progress).toBe(0.42);
		expect(operation.result_summary.stage).toBe('正在渲染视频 30%');
	});
});
