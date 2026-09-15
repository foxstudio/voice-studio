import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api } from './index';

afterEach(() => vi.unstubAllGlobals());

describe('Seed Audio asset API', () => {
	it('uploads image as multipart with explicit license status', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/seed-audio/assets/image');
			expect(init?.method).toBe('POST');
			expect(init?.body).toBeInstanceOf(FormData);
			const form = init?.body as FormData;
			expect(form.get('file')).toBeInstanceOf(File);
			expect(form.get('license_status')).toBe('self_voice');
			return new Response(JSON.stringify({
				file_id: 'image-1', asset_type: 'seed_audio_image', source: 'upload', license_status: 'self_voice',
				original_name: 'scene.png', mime_type: 'image/png', media_format: 'png', size_bytes: 8, created_at: '2026-07-11T00:00:00Z'
			}), { status: 201, headers: { 'Content-Type': 'application/json' } });
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await Api.uploadSeedAudioImage(new File([new Uint8Array(8)], 'scene.png', { type: 'image/png' }), 'self_voice');

		expect(result.file_id).toBe('image-1');
		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Reference audio clip API', () => {
	it('creates an emotion reference clip without calling the ASR endpoint', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/voices/files/source%2F1/clip');
			expect(url).not.toContain('clip-transcribe');
			expect(init?.method).toBe('POST');
			expect(JSON.parse(String(init?.body))).toEqual({ start_ms: 1_000, end_ms: 7_000 });
			return new Response(JSON.stringify({
				file_id: 'clip-1', filename: 'clip-1.wav', path: '/voices/clip-1.wav',
				voice_file: { file_id: 'clip-1', duration_ms: 6_000, size_bytes: 12, mime_type: 'audio/wav' },
				quality: { warnings: [] }
			}), { status: 200, headers: { 'Content-Type': 'application/json' } });
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await Api.clipVoice('source/1', { start_ms: 1_000, end_ms: 7_000 });

		expect(result.file_id).toBe('clip-1');
		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('API cache policy', () => {
	it('always revalidates mutable GET responses', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/health');
			expect(init?.cache).toBe('no-store');
			return new Response(JSON.stringify({ status: 'ok', version: 'test', engines: {}, uptime_seconds: 1 }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await Api.health();

		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Project catalog API', () => {
	it('loads lightweight script summaries instead of full project records', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/projects/summaries?kind=script');
			expect(init?.method).toBeUndefined();
			return new Response(JSON.stringify([]), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await Api.projectSummaries('script');

		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it('syncs only lightweight disk-backed localization summaries', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/projects/video-localization/sync-project-summaries');
			expect(init?.method).toBe('POST');
			return new Response(JSON.stringify([]), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await Api.syncVideoLocalizationProjectSummaries();

		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Video localization operation feed API', () => {
	it('encodes bounded v2 history page parameters', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe(
				'/api/projects/project/1/video-localization/operations/feed-v2?cursor=opaque%2Fcursor&history_limit=25'
			);
			expect(init?.cache).toBe('no-store');
			return new Response(JSON.stringify({
				schema_version: 'operation-feed-v2',
				revision: 18,
				history_revision: 4,
				changed: true,
				active_operations: [],
				history: [],
				history_total: 75,
				next_cursor: null
			}), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await Api.videoLocalizationOperationFeedV2('project/1', {
			cursor: 'opaque/cursor',
			historyLimit: 25
		});

		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Synthesized-dub subtitle workflow API', () => {
	it('submits the typed development target request to its dedicated endpoint', async () => {
		const request = {
			engine_id: 'qwen3-asr-mlx',
			execution_mode: 'development_target' as const,
			development_target_step_id: 'transcribe_track' as const,
			development_session_id: 'dub-debug-1'
		};
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe(
				'/api/projects/project-1/video-localization/operations/dub-subtitles'
			);
			expect(init?.method).toBe('POST');
			expect(JSON.parse(String(init?.body))).toEqual(request);
			return new Response(JSON.stringify({
				operation_id: 'operation-1',
				project_id: 'project-1',
				kind: 'dub_subtitle_generation',
				status: 'queued',
				label: '根据合成配音生成字幕',
				progress: 0,
				error_code: null,
				error_message: null,
				cancel_requested: false,
				result_summary: {},
				parameters: request,
				created_at: '2026-08-03T00:00:00Z',
				started_at: null,
				completed_at: null
			}), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		const operation = await Api.submitVideoLocalizationDubSubtitleOperation(
			'project-1',
			request
		);

		expect(operation.kind).toBe('dub_subtitle_generation');
		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it('submits the complete typed review partition to its dedicated endpoint', async () => {
		const request = {
			source_revision: 'a'.repeat(64),
			cues: [{
				subtitle_id: 'dub-1',
				source_subtitle_ids: ['dub-1'],
				start_ms: 100,
				end_ms: 900,
				text: '修改后的字幕'
			}]
		};
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/projects/project-1/video-localization/dub-subtitles/review');
			expect(init?.method).toBe('POST');
			expect(JSON.parse(String(init?.body))).toEqual(request);
			return new Response(JSON.stringify({
				project_type: 'video_localization',
				schema_version: 'v1',
				dub_subtitles: request.cues
			}), { status: 200, headers: { 'Content-Type': 'application/json' } });
		});
		vi.stubGlobal('fetch', fetchMock);

		const updated = await Api.reviewVideoLocalizationDubSubtitles('project-1', request);

		expect(updated.dub_subtitles?.[0].text).toBe('修改后的字幕');
		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Video localization preview proxy API', () => {
	it.each([
		[
			'source video',
			() => Api.prepareVideoLocalizationPreviewVideo('project/1', {
				source_playable: false,
				start_ms: 0
			}),
			'/api/projects/project/1/video-localization/source-media/preview-video'
		],
		[
			'source audio',
			() => Api.prepareVideoLocalizationSourceAudioPreview('project/1'),
			'/api/projects/project/1/video-localization/source-media/audio-preview'
		],
		[
			'background stem',
			() => Api.prepareVideoLocalizationStemAudioPreview('project/1', 'background'),
			'/api/projects/project/1/video-localization/stems/background/audio-preview'
		]
	] as const)('prepares the %s proxy with a typed POST command', async (_label, invoke, expectedUrl) => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe(expectedUrl);
			expect(init?.method).toBe('POST');
			return new Response(JSON.stringify({ changed: true, profile: 'test-profile' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await expect(invoke()).resolves.toMatchObject({ changed: true });
		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Video localization subtitle mutation API', () => {
	it.each([
		['delete cue', () => Api.deleteVideoLocalizationCue('project/1', 'cue/1'), '/api/projects/project/1/video-localization/cues/cue%2F1', 'DELETE', undefined],
		['merge cues', () => Api.mergeVideoLocalizationCues('project/1', { cue_ids: ['cue_1', 'cue_2'], survivor_cue_id: 'cue_1' }), '/api/projects/project/1/video-localization/cues/merge', 'POST', { cue_ids: ['cue_1', 'cue_2'], survivor_cue_id: 'cue_1' }],
		['split cue', () => Api.splitVideoLocalizationCue('project/1', 'cue/1', { replacements: [] }), '/api/projects/project/1/video-localization/cues/cue%2F1/split', 'POST', { replacements: [] }],
		['split localized subtitle', () => Api.splitVideoLocalizationLocalizedSubtitle('project/1', 'localized/1', { children: [] }), '/api/projects/project/1/video-localization/localized-subtitles/localized%2F1/split', 'POST', { children: [] }]
	] as const)('calls the %s transaction endpoint', async (_label, invoke, expectedUrl, expectedMethod, expectedBody) => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe(expectedUrl);
			expect(init?.method).toBe(expectedMethod);
			if (expectedBody) expect(JSON.parse(String(init?.body))).toEqual(expectedBody);
			return new Response(JSON.stringify({ project_type: 'video_localization', schema_version: 'v1' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await invoke();

		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Video localization dubbing timeline API', () => {
	it('submits an explicitly selected group through the canonical executor', async () => {
		const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
			expect(JSON.parse(String(init?.body))).toEqual({
				schema_version: 'dubbing-production-execute-v1',
				scope: 'single_group',
				group_id: 'group-2'
			});
			return new Response(JSON.stringify({ schema_version: 'dubbing-production-execution-v1' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await Api.executeVideoLocalizationDubbingProductionRun('project/1', 'single_group', 'group-2');

		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it.each([
		['read production run', () => Api.videoLocalizationDubbingProductionRun('project/1'), '/api/projects/project/1/video-localization/dubbing/production-run', undefined, undefined],
		['execute remaining groups', () => Api.executeVideoLocalizationDubbingProductionRun('project/1', 'all_remaining'), '/api/projects/project/1/video-localization/dubbing/production-run/execute', 'POST', 'body']
	] as const)('calls the %s endpoint', async (_label, invoke, expectedUrl, expectedMethod, expectedBody) => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe(expectedUrl);
			expect(init?.method).toBe(expectedMethod);
			if (expectedBody === 'body') expect(JSON.parse(String(init?.body))).toBeTruthy();
			return new Response(JSON.stringify({ schema_version: 'test' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			});
		});
		vi.stubGlobal('fetch', fetchMock);

		await invoke();

		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('Managed model installation API', () => {
	it('loads the effective ASR auto-selection policy', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/engines/asr-selection');
			expect(init?.cache).toBe('no-store');
			return new Response(JSON.stringify({
				mode: 'auto', engine_id: 'vibevoice-asr-mlx-4bit',
				diarization_engine_id: 'vibevoice-asr-mlx-4bit', reason: 'test',
				physical_memory_bytes: 137_438_953_472,
				policy: { short_or_single_speaker: 'qwen3-asr-mlx', long_multi_speaker_preference: [], official_full_model_auto_selected: false }
			}), { status: 200, headers: { 'Content-Type': 'application/json' } });
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await Api.asrSelection();

		expect(result.engine_id).toBe('vibevoice-asr-mlx-4bit');
		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it('sends the exact accepted model license identifier', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/engines/installations/omnivoice/install');
			expect(init?.method).toBe('POST');
			expect(JSON.parse(String(init?.body))).toEqual({
				accepted_license_id: 'omnivoice-weights-cc-by-nc'
			});
			return new Response(JSON.stringify({
				engine_id: 'omnivoice',
				installation_status: 'installing'
			}), { status: 202, headers: { 'Content-Type': 'application/json' } });
		});
		vi.stubGlobal('fetch', fetchMock);

		await Api.installEngineModel('omnivoice', 'omnivoice-weights-cc-by-nc');

		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it('starts a VibeVoice install without a license payload', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/engines/installations/vibevoice-asr-4bit/install');
			expect(init?.method).toBe('POST');
			expect(init?.body).toBeUndefined();
			return new Response(JSON.stringify({
				engine_id: 'vibevoice-asr-4bit', installation_status: 'installing'
			}), { status: 202, headers: { 'Content-Type': 'application/json' } });
		});
		vi.stubGlobal('fetch', fetchMock);

		await Api.installEngineModel('vibevoice-asr-4bit');

		expect(fetchMock).toHaveBeenCalledOnce();
	});
});
