import type { VideoLocalizationOperation } from '$lib/api/types';
import type { MediaExportRequest } from './export-options';

export type ExportDestinationSelection = {
	status: 'selected' | 'cancelled';
	destination_id: string | null;
	display_path: string | null;
};

export type ExportFilenamePreview = {
	schema_version: 'v1';
	output_filename: string;
};

export class VideoLocalizationExportController {
	constructor(
		private readonly fetcher: typeof fetch = fetch
	) {}

	async selectDestination(
		projectId: string,
		mode: 'default' | 'choose'
	): Promise<ExportDestinationSelection> {
		return this.requestJson<ExportDestinationSelection>(
			`/api/projects/${projectId}/video-localization/export/destination`,
			{
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ mode })
			},
			'选择保存目录失败'
		);
	}

	async startMediaExport(
		projectId: string,
		destinationId: string,
		outputFilename: string,
		request: MediaExportRequest
	): Promise<VideoLocalizationOperation> {
		return this.requestJson<VideoLocalizationOperation>(
			`/api/projects/${projectId}/video-localization/export/render`,
			{
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					schema_version: 'v1',
					destination_id: destinationId,
					output_filename: outputFilename,
					render: request
				})
			},
			'启动导出任务失败'
		);
	}

	async defaultFilename(
		projectId: string,
		request: MediaExportRequest
	): Promise<ExportFilenamePreview> {
		return this.requestJson<ExportFilenamePreview>(
			`/api/projects/${projectId}/video-localization/export/filename-preview`,
			{
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(request)
			},
			'生成默认文件名失败'
		);
	}

	async operation(
		projectId: string,
		operationId: string
	): Promise<VideoLocalizationOperation> {
		return this.requestJson<VideoLocalizationOperation>(
			`/api/projects/${projectId}/video-localization/operations/${encodeURIComponent(operationId)}`,
			undefined,
			'读取导出进度失败'
		);
	}

	private async requestJson<T>(
		url: string,
		init: RequestInit | undefined,
		fallbackMessage: string
	): Promise<T> {
		const response = await this.fetcher(url, init);
		if (!response.ok) {
			const data = await response.json().catch(() => null);
			throw new Error(data?.error?.message || fallbackMessage);
		}
		return response.json() as Promise<T>;
	}
}
