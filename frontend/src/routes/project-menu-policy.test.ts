import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const videoLocalizationSource = readFileSync(
	new URL('./video-localization/+page.svelte', import.meta.url),
	'utf8'
);
const projectCatalogControllerSource = readFileSync(
	new URL('./video-localization/project-catalog-controller.ts', import.meta.url),
	'utf8'
);
const scriptStudioSource = readFileSync(
	new URL('./script-studio/+page.svelte', import.meta.url),
	'utf8'
);
const audioToolsSource = readFileSync(
	new URL('./audio-tools/+page.svelte', import.meta.url),
	'utf8'
);

describe('project menu data boundaries', () => {
	it('uses disk-backed lightweight summaries for the localization history menu', () => {
		expect(videoLocalizationSource).toContain('new VideoLocalizationProjectCatalogClient()');
		expect(videoLocalizationSource).not.toContain('Api.syncVideoLocalizationProjectSummaries()');
		expect(projectCatalogControllerSource).toContain(
			'this.transport.syncVideoLocalizationProjectSummaries()'
		);
		expect(videoLocalizationSource).not.toContain('Api.syncVideoLocalizationProjects()');
	});

	it('keeps localization projects out of script and transcription target menus', () => {
		expect(scriptStudioSource).toContain("Api.projectSummaries('script')");
		expect(scriptStudioSource).toContain('Api.project(currentProjectId)');
		expect(audioToolsSource).toContain("Api.projectSummaries('script')");
		expect(scriptStudioSource).not.toContain('Api.projects()');
		expect(audioToolsSource).not.toContain('Api.projects()');
	});
});
