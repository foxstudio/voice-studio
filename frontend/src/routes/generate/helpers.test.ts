import { describe, it, expect } from 'vitest';
import {
	statusIsActive,
	taskIsActive,
	taskIsWaiting,
	taskIsProcessing,
	taskIsSuccess,
	taskIsFailed,
	taskCanDelete,
	taskIsLongformSegment,
	taskIsLongformExport,
	longformResultLabel,
	displayTitle,
	requestFromTask,
	taskParameterCopyText,
	voiceBadgeLabel,
	formatSeconds,
	formatAudioDuration,
	resultDownloadNameForScope,
	taskSupportsBrowserPreview,
	verificationStatusLabel,
} from './helpers';
import type { EngineDetail, GenerationTask, ParameterSchema, VoiceAsset } from '$lib/api/types';

function makeTask(overrides: Partial<GenerationTask> = {}): GenerationTask {
	return {
		task_id: 't1',
		input_text: '测试文本',
		status: 'success',
		engine_id: 'indextts-v2',
		voice_id: '',
		created_at: '2026-01-01T00:00:00Z',
		parameters: {},
		...overrides,
	} as GenerationTask;
}

it('labels non-speech Seed Audio verification without showing a false failure', () => {
	expect(verificationStatusLabel('skipped')).toBe('无需台词校对');
});

function parameter(overrides: Partial<ParameterSchema> & Pick<ParameterSchema, 'key' | 'label'>): ParameterSchema {
	return {
		description: null,
		type: 'number',
		level: 'basic',
		default: null,
		min: null,
		max: null,
		step: null,
		options: [],
		required: false,
		capability: null,
		...overrides,
	};
}

function engineDetail(engineId: string, parameterSchema: ParameterSchema[]): EngineDetail {
	return {
		manifest: {
			engine_id: engineId,
			display_name: engineId,
			engine_type: engineId.startsWith('mimo-') ? 'cloud' : 'local',
			provider: 'test',
			version: 'test',
			description: '',
			supported_languages: [],
			capabilities: [],
			sample_rate: 22050,
			max_tokens: null,
			privacy_level: 'local_only',
			default_use_case: '',
			parameter_schema: parameterSchema,
		},
		state: {
			engine_id: engineId,
			status: 'stopped',
			model_path: null,
			error_message: null,
			loaded_at: null,
		},
	};
}

function engineMap(entries: EngineDetail[]) {
	return new Map(entries.map((entry) => [entry.manifest.engine_id, entry]));
}

function voiceMap(entries: VoiceAsset[] = []) {
	return new Map(entries.map((entry) => [entry.voice_id, entry]));
}

describe('statusIsActive', () => {
	it('returns true for active statuses', () => {
		expect(statusIsActive('pending')).toBe(true);
		expect(statusIsActive('queued')).toBe(true);
		expect(statusIsActive('running')).toBe(true);
		expect(statusIsActive('postprocessing')).toBe(true);
		expect(statusIsActive('retrying')).toBe(true);
	});

	it('returns false for inactive statuses', () => {
		expect(statusIsActive('success')).toBe(false);
		expect(statusIsActive('failed')).toBe(false);
		expect(statusIsActive('cancelled')).toBe(false);
	});
});

describe('task helpers', () => {
	it('taskIsActive', () => {
		expect(taskIsActive(makeTask({ status: 'running' }))).toBe(true);
		expect(taskIsActive(makeTask({ status: 'success' }))).toBe(false);
	});

	it('taskIsWaiting', () => {
		expect(taskIsWaiting(makeTask({ status: 'pending' }))).toBe(true);
		expect(taskIsWaiting(makeTask({ status: 'queued' }))).toBe(true);
		expect(taskIsWaiting(makeTask({ status: 'running' }))).toBe(false);
	});

	it('taskIsProcessing', () => {
		expect(taskIsProcessing(makeTask({ status: 'running' }))).toBe(true);
		expect(taskIsProcessing(makeTask({ status: 'postprocessing' }))).toBe(true);
		expect(taskIsProcessing(makeTask({ status: 'queued' }))).toBe(false);
	});

	it('taskIsSuccess', () => {
		expect(taskIsSuccess(makeTask({ status: 'success' }))).toBe(true);
		expect(taskIsSuccess(makeTask({ status: 'failed' }))).toBe(false);
	});

	it('taskIsFailed', () => {
		expect(taskIsFailed(makeTask({ status: 'failed' }))).toBe(true);
		expect(taskIsFailed(makeTask({ status: 'cancelled' }))).toBe(true);
		expect(taskIsFailed(makeTask({ status: 'success' }))).toBe(false);
	});

	it('taskCanDelete', () => {
		expect(taskCanDelete(makeTask({ status: 'success' }))).toBe(true);
		expect(taskCanDelete(makeTask({ status: 'running' }))).toBe(false);
	});
});

describe('longform helpers', () => {
	it('taskIsLongformSegment', () => {
		expect(taskIsLongformSegment(makeTask({ longform_task_id: 'lf1', longform_segment_index: 1, longform_segment_count: 3 }))).toBe(true);
		expect(taskIsLongformSegment(makeTask())).toBe(false);
	});

	it('taskIsLongformExport', () => {
		expect(taskIsLongformExport(makeTask({ longform_task_id: 'lf1', task_type: 'export' }))).toBe(true);
		expect(taskIsLongformExport(makeTask({ longform_task_id: 'lf1' }))).toBe(false);
	});

	it('longformResultLabel', () => {
		expect(longformResultLabel(makeTask({ longform_task_id: 'lf1', task_type: 'export' }))).toBe('完整片段');
		expect(longformResultLabel(makeTask({ longform_task_id: 'lf1', longform_segment_index: 2, longform_segment_count: 5 }))).toBe('片段 2/5');
		expect(longformResultLabel(makeTask())).toBe('');
	});
});

describe('displayTitle', () => {
	it('returns input_text', () => {
		expect(displayTitle(makeTask({ input_text: '你好' }))).toBe('你好');
	});

	it('returns fallback for empty text', () => {
		expect(displayTitle(makeTask({ input_text: '' }))).toBe('未命名任务');
	});

	it('prefixes longform export', () => {
		expect(displayTitle(makeTask({ input_text: '长文', longform_task_id: 'lf1', task_type: 'export' }))).toBe('完整长文本：长文');
	});
});

describe('requestFromTask', () => {
	it('preserves the Seed Audio envelope for parameter reuse', () => {
		const request = requestFromTask(makeTask({
			engine_id: 'doubao-seed-audio-1.0',
			input_text: '@音频1 说话',
			parameters: {
				engine_id: 'doubao-seed-audio-1.0',
				text: '@音频1 说话',
				input_mode: 'audio',
				input_assets: [{ asset_id: 'speaker-1', type: 'speaker', source: 'cloud_speaker', speaker_id: 'speaker-1' }],
				engine_parameters: { format: 'mp3', sample_rate: 48000 }
			}
		}));

		expect(request.input_mode).toBe('audio');
		expect(request.input_assets).toEqual([expect.objectContaining({ speaker_id: 'speaker-1' })]);
		expect(request.engine_parameters).toEqual({ format: 'mp3', sample_rate: 48000 });
	});
	it('rebuilds a full request from a normal task parameter snapshot', () => {
		const request = requestFromTask(makeTask({
			engine_id: 'omnivoice',
			voice_id: 'voice-1',
			input_text: '任务文本',
			parameters: {
				text: '参数文本',
				engine_id: 'omnivoice',
				voice_id: 'voice-2',
				language: 'en',
				emotion: 'happy',
				speed: 1.2,
				output_format: 'mp3'
			}
		}));

		expect(request.text).toBe('参数文本');
		expect(request.engine_id).toBe('omnivoice');
		expect(request.voice_id).toBe('voice-2');
		expect(request.language).toBe('en');
		expect(request.emotion_mode).toBe('emotion_vector');
		expect(request.output_format).toBe('mp3');
		expect(request.speed).toBe(1.2);
	});

	it('falls back to task fields for longform export snapshots without request fields', () => {
		const request = requestFromTask(makeTask({
			task_type: 'export',
			longform_task_id: 'lf1',
			engine_id: 'omnivoice',
			voice_id: 'voice-1',
			input_text: '完整长文本',
			parameters: {
				language: 'auto',
				output_format: 'wav',
				source_result_ids: ['r1', 'r2']
			}
		}));

		expect(request.text).toBe('完整长文本');
		expect(request.engine_id).toBe('omnivoice');
		expect(request.voice_id).toBe('voice-1');
		expect(request.language).toBe('auto');
		expect(request.emotion_mode).toBe('follow_reference');
		expect(request.output_format).toBe('wav');
	});
});

describe('taskParameterCopyText', () => {
	it('lists all model schema parameters instead of a small hard-coded subset', () => {
		const text = taskParameterCopyText(
			makeTask({
				engine_id: 'f5-tts',
				parameters: {
					engine_id: 'f5-tts',
					speed: 1.05,
					nfe_step: 32,
					cfg_strength: 2,
					target_rms: 0.1,
					cross_fade_duration: 0.15,
					sway_sampling_coef: -1,
					fix_duration: 0,
					remove_silence: false,
					output_format: 'wav'
				}
			}),
			engineMap([
				engineDetail('f5-tts', [
					parameter({ key: 'speed', label: '语速', type: 'slider' }),
					parameter({ key: 'nfe_step', label: '采样步数 NFE', type: 'slider' }),
					parameter({ key: 'cfg_strength', label: '引导强度 CFG', type: 'slider' }),
					parameter({ key: 'target_rms', label: '响度目标 RMS', type: 'slider' }),
					parameter({ key: 'cross_fade_duration', label: '分段交叉淡化', type: 'slider' }),
					parameter({ key: 'sway_sampling_coef', label: '采样摆动 Sway', type: 'slider' }),
					parameter({ key: 'fix_duration', label: '固定总时长 s', type: 'number' }),
					parameter({ key: 'remove_silence', label: '移除静音', type: 'toggle' }),
				])
			]),
			voiceMap()
		);

		expect(text).toContain('采样步数 NFE: 32');
		expect(text).toContain('响度目标 RMS: 0.1');
		expect(text).toContain('采样摆动 Sway: -1');
		expect(text).toContain('移除静音: 否');
		expect(text).toContain('格式: WAV');
	});

	it('maps cloud speaker id to the official option label in result badges', () => {
		const task = makeTask({
			engine_id: 'doubao-tts-preset',
			parameters: {
				speaker_id: 'zh_female_peiqi_uranus_bigtts'
			}
		});
		const label = voiceBadgeLabel(
			task,
			voiceMap(),
			engineMap([
				engineDetail('doubao-tts-preset', [
					parameter({
						key: 'speaker_id',
						label: '豆包官方音色',
						type: 'select',
						options: [{ label: '佩奇猪 2.0 · 角色音', value: 'zh_female_peiqi_uranus_bigtts' }]
					})
				])
			])
		);

		expect(label).toBe('佩奇猪 2.0 · 角色音');
	});

	it('unwraps longform export generate_request and includes merge parameters', () => {
		const text = taskParameterCopyText(
			makeTask({
				task_type: 'export',
				longform_task_id: 'lf1',
				longform_segment_count: 2,
				engine_id: 'cosyvoice-sft',
				parameters: {
					generate_request: {
						engine_id: 'cosyvoice-sft',
						speaker_id: '中文女',
						speed: 1.3,
						output_format: 'wav'
					},
					verify_enabled: true,
					merge_enabled: true,
					max_retries: 2,
					asr_engine_id: 'qwen3-asr-mlx',
					silence_ms: 300,
					source_result_ids: ['r1', 'r2']
				}
			}),
			engineMap([
				engineDetail('cosyvoice-sft', [
					parameter({ key: 'speaker_id', label: '预置音色', type: 'select', options: [{ label: '中文女声', value: '中文女' }] }),
					parameter({ key: 'speed', label: '语速', type: 'slider' }),
				])
			]),
			voiceMap()
		);

		expect(text).toContain('预置音色: 中文女声');
		expect(text).toContain('语速: 1.3');
		expect(text).toContain('长文本段数: 2');
		expect(text).toContain('自动校对: 是');
		expect(text).toContain('校对 ASR: qwen3-asr-mlx');
		expect(text).toContain('来源结果数: 2');
	});
});

describe('formatSeconds', () => {
	it('formats correctly', () => {
		expect(formatSeconds(0)).toBe('0:00');
		expect(formatSeconds(65)).toBe('1:05');
		expect(formatSeconds(3661)).toBe('61:01');
	});
});

describe('formatAudioDuration', () => {
	it('formats ms to seconds', () => {
		expect(formatAudioDuration(null)).toBe('');
		expect(formatAudioDuration(0)).toBe('');
		expect(formatAudioDuration(1500)).toBe('1.5s');
		expect(formatAudioDuration(60000)).toBe('60.0s');
	});
});

describe('resultDownloadNameForScope', () => {
	it('uses a per-day sequence and readable prompt title', () => {
		const first = makeTask({
			task_id: 'a',
			result_id: 'r-a',
			input_text: '第一条文本',
			created_at: '2026-07-08T01:00:00+08:00',
			completed_at: '2026-07-08T01:01:00+08:00',
			parameters: { output_format: 'wav' }
		});
		const second = makeTask({
			task_id: 'b',
			result_id: 'r-b',
			input_text: '说每天雷打不动训练至少三四小时',
			created_at: '2026-07-08T02:00:00+08:00',
			completed_at: '2026-07-08T02:01:00+08:00',
			parameters: { output_format: 'mp3' }
		});
		const nextDay = makeTask({
			task_id: 'c',
			result_id: 'r-c',
			input_text: '第二天文本',
			created_at: '2026-07-09T01:00:00+08:00',
			parameters: { output_format: 'wav' }
		});

		expect(resultDownloadNameForScope(second, [nextDay, second, first])).toBe('002-说每天雷打不动训练至少三四小时.mp3');
		expect(resultDownloadNameForScope(nextDay, [nextDay, second, first])).toBe('001-第二天文本.wav');
	});

	it('sanitizes punctuation and limits long prompts', () => {
		const task = makeTask({
			task_id: 'a',
			result_id: 'r-a',
			input_text: '那 AI 应该怎么办？比如隐私/效率:成本，需要非常非常非常非常长的标题',
			created_at: '2026-07-08T01:00:00+08:00',
			parameters: { output_format: 'WAV' }
		});

		expect(resultDownloadNameForScope(task, [task])).toBe('001-那-AI-应该怎么办-比如隐私-效率-成本-需要非常非常非常非常.wav');
	});

	it.each([
		['pcm', '001-原始-PCM.pcm'],
		['ogg_opus', '001-OGG-Opus.ogg']
	])('keeps the selected raw provider format in the history download name (%s)', (outputFormat, expectedName) => {
		const task = makeTask({
			task_id: `raw-${outputFormat}`,
			result_id: `result-${outputFormat}`,
			input_text: outputFormat === 'pcm' ? '原始 PCM' : 'OGG Opus',
			created_at: '2026-07-15T01:00:00+08:00',
			parameters: { output_format: outputFormat }
		});

		expect(resultDownloadNameForScope(task, [task])).toBe(expectedName);
	});

	it('keeps PCM download-only while OGG Opus remains previewable', () => {
		expect(taskSupportsBrowserPreview(makeTask({ parameters: { output_format: 'pcm' } }))).toBe(false);
		expect(taskSupportsBrowserPreview(makeTask({ parameters: { output_format: 'ogg_opus' } }))).toBe(true);
	});
});
