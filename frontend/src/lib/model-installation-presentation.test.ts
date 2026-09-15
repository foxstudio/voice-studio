import { describe, expect, it } from 'vitest';

import type { EngineInstallation } from './api/types';
import {
	modelInstallationGuidance,
	modelInstallationStatus
} from './model-installation-presentation';

function installation(
	overrides: Partial<EngineInstallation> = {}
): EngineInstallation {
	return {
		engine_id: 'example',
		source_url: 'https://example.com/model',
		source_label: '官方来源',
		install_kind: 'external_runtime',
		license_note: '遵守官方许可。',
		preferred_path: '/models/example',
		installed: false,
		installation_status: 'model_missing',
		discovered_paths: [],
		automatic_download_supported: false,
		automatic_download_blockers: ['managed_install_not_supported'],
		download_sources: [],
		download_policy: '手动安装',
		reuse_note: '可复用',
		...overrides
	};
}

describe('model installation presentation', () => {
	it('does not claim readiness when only model files are present', () => {
		const item = installation({
			installed: true,
			runtime_ready: false,
			installation_status: 'files_present_runtime_unavailable',
			runtime_detail: 'Python package is missing'
		});

		expect(modelInstallationStatus(item)).toBe('模型已在本机');
		expect(modelInstallationGuidance(item)).toContain('运行环境未就绪');
		expect(modelInstallationGuidance(item)).toContain('Python package is missing');
	});

	it('reports full readiness only when runtime health passes', () => {
		const item = installation({
			installed: true,
			runtime_ready: true,
			installation_status: 'ready'
		});

		expect(modelInstallationStatus(item)).toBe('模型与环境已就绪');
		expect(modelInstallationGuidance(item)).toBeNull();
	});

	it('explains missing IndexTTS reference-audio assets without calling the runtime broken', () => {
		const item = installation({
			engine_id: 'indextts-v2',
			installed: true,
			runtime_ready: false,
			runtime_status: 'reference_preprocessing_missing',
			runtime_detail: '上传 WAV 参考音频所需的配套模型尚未安装。'
		});

		expect(modelInstallationGuidance(item)).toBe('上传 WAV 参考音频所需的配套模型尚未安装。');
	});

	it('explains license blockers instead of calling every model installable', () => {
		const item = installation({
			automatic_download_blockers: [
				'managed_install_not_supported',
				'model_license_unverified'
			]
		});

		expect(modelInstallationGuidance(item)).toContain('许可尚未完整核验');
	});

	it('explains integrity and manual-install blockers separately', () => {
		expect(
			modelInstallationGuidance(
				installation({ automatic_download_blockers: ['model_checksum_missing'] })
			)
		).toContain('完整性校验信息不完整');
		expect(modelInstallationGuidance(installation())).toContain('手动安装');
	});
});
