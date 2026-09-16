import { describe, expect, it } from 'vitest';
import type { EngineDetail, EngineInstallation } from '$lib/api/types';
import {
	installationRuntimeEngineId,
	standaloneResources,
	engineAvailability,
	engineFamilyId,
	engineTask,
	resourceAvailability,
	resourceGroup,
	resourceRoleLabel,
	resourceSizeLabel
} from './engine-hub-presentation';

function engine(
	id: string,
	capabilities: string[],
	type: 'local' | 'cloud' = 'local',
	status: EngineDetail['state']['status'] = 'stopped'
): EngineDetail {
	return {
		manifest: {
			engine_id: id,
			display_name: id,
			engine_type: type,
			provider: 'test',
			version: '1',
			description: '',
			supported_languages: [],
			capabilities,
			sample_rate: null,
			max_tokens: null,
			privacy_level: '',
			default_use_case: '',
			parameter_schema: []
		},
		state: { engine_id: id, status, model_path: null, error_message: null, loaded_at: null }
	};
}

function installation(overrides: Partial<EngineInstallation> = {}): EngineInstallation {
	return {
		engine_id: 'test-model',
		source_url: '',
		source_label: '',
		install_kind: 'model',
		license_note: '',
		preferred_path: null,
		installed: false,
		installation_status: 'not_installed',
		discovered_paths: [],
		automatic_download_supported: false,
		automatic_download_blockers: [],
		download_sources: [],
		download_policy: '',
		reuse_note: '',
		...overrides
	};
}

describe('engine hub presentation', () => {
	it('classifies engines by explicit primary task', () => {
		expect(engineTask(engine('doubao-seed-audio-1.0', ['audio_generation'], 'cloud'))).toBe('audio_generation');
		expect(engineTask(engine('mimo-v2.5-asr', ['speech_recognition'], 'cloud'))).toBe('asr');
		expect(engineTask(engine('indextts-v2', ['voice_cloning']))).toBe('tts');
	});

	it('groups variants into recognizable model families', () => {
		expect(engineFamilyId('vibevoice-asr-mlx-4bit')).toBe('vibevoice-asr');
		expect(engineFamilyId('cosyvoice-zero-shot')).toBe('cosyvoice');
		expect(engineFamilyId('mimo-v2.5-tts-preset')).toBe('mimo-v2.5');
		expect(engineFamilyId('doubao-tts-voice-clone')).toBe('doubao');
	});

	it('separates runnable status from model installation detail', () => {
		const local = engine('indextts-v2', ['voice_cloning']);
		expect(engineAvailability(local, installation({ installed: true, runtime_ready: true }))).toMatchObject({ label: '可启动', detail: '模型已下载 · 环境正常' });
		expect(engineAvailability(engine('indextts-v2', [], 'local', 'loaded'), installation({ installed: true, runtime_ready: true }))).toMatchObject({ label: '运行中' });
		expect(engineAvailability(local, installation())).toMatchObject({ label: '需安装' });
		expect(engineAvailability(local, installation({ installed: true, runtime_ready: false }))).toMatchObject({ label: '需配置' });
		expect(engineAvailability(engine('mimo-v2.5-asr', [], 'cloud'))).toMatchObject({ label: '未检查' });
		expect(engineAvailability(engine('mimo-v2.5-asr', [], 'cloud', 'loaded'))).toMatchObject({ label: '连接正常' });
	});

	it('places workflow dependencies and reference variants in the model inventory', () => {
		expect(resourceGroup(installation({ engine_id: 'bs-roformer' }))).toBe('workflow');
		expect(resourceGroup(installation({ engine_id: 'moss-transcribe-diarize-mlx' }))).toBe('workflow');
		expect(resourceGroup(installation({ engine_id: 'campplus-modelscope' }))).toBe('workflow');
		expect(resourceGroup(installation({ engine_id: 'vibevoice-asr-official', reference_only: true }))).toBe('asr');
		expect(resourceRoleLabel(installation({ engine_id: 'campplus-modelscope' }))).toBe('声纹复核');
	});

	it('uses textual resource states independent of the neutral resource strip', () => {
		expect(resourceAvailability(installation({ installed: true, runtime_ready: true }))).toMatchObject({ label: '已下载' });
		expect(resourceAvailability(installation({ installation_status: 'installing' }))).toMatchObject({ label: '下载中' });
		expect(resourceAvailability(installation({ reference_only: true, installed: true }))).toMatchObject({ label: '仅供参考' });
		expect(resourceAvailability(installation({ installation_status: 'failed', error: 'broken' }))).toMatchObject({ label: '文件不完整' });
	});

	it('hides unknown download sizes instead of claiming zero megabytes', () => {
		expect(resourceSizeLabel(installation({ total_bytes: 0 }))).toBeNull();
		expect(resourceSizeLabel(installation({ total_bytes: 639_000_000 }))).toBe('下载约 639 MB');
		expect(resourceSizeLabel(installation({ installed: true, size_bytes: 3_270_000_000 }))).toBe('3.27 GB');
	});
});


describe('unified engine and model inventory', () => {
 it('shows linked engines once while retaining auxiliary and reference files', () => {
  const runtime = engine('vibevoice-asr-mlx-4bit', ['speech_recognition']);
  const linked = installation({engine_id: 'vibevoice-asr-4bit', family_id: 'vibevoice-asr', variant_id: '4bit'});
  const reference = installation({engine_id: 'vibevoice-asr-official', family_id: 'vibevoice-asr', reference_only: true});
  const auxiliary = installation({engine_id: 'bs-roformer'});
  expect(installationRuntimeEngineId(linked)).toBe(runtime.manifest.engine_id);
  expect(standaloneResources([runtime], [linked, reference, auxiliary])).toEqual([reference, auxiliary]);
  expect(standaloneResources([], [linked, reference, auxiliary])).toEqual([linked, reference, auxiliary]);
 });
 it('keeps reference resources separate even if runtime metadata is present', () => {
  expect(installationRuntimeEngineId(installation({reference_only: true, runtime_engine_id: 'test-model'}))).toBeNull();
 });
 it('shows an active download and a failed installation on the engine card', () => {
  const runtime = engine('omnivoice', ['voice_cloning']);
  expect(engineAvailability(runtime, installation({installation_status: 'installing'}))).toMatchObject({label:'下载中'});
  expect(engineAvailability(runtime, installation({installation_status:'failed', error:'network unavailable'}))).toMatchObject({key:'error', detail:'network unavailable'});
 });
});
