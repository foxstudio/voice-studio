import type { EngineDetail, EngineInstallation } from '$lib/api/types';

export type HubView = 'engines' | 'models';
export type EngineTask = 'tts' | 'asr' | 'audio_generation';
export type ResourceGroup = 'tts' | 'asr' | 'workflow';
export type AvailabilityKey = 'ready' | 'needs_install' | 'needs_setup' | 'error';
export type AvailabilityFilter = 'all' | AvailabilityKey;
export type StatusTone = 'neutral' | 'ok' | 'warning' | 'fail';

export interface StatusPresentation {
	key: AvailabilityKey;
	label: string;
	detail: string;
	tone: StatusTone;
}

export const engineTaskGroups: Array<{ id: EngineTask; label: string; description: string }> = [
	{ id: 'tts', label: '语音合成', description: '把文字变成语音，包括预置音色、声音设计和声音克隆。' },
	{ id: 'asr', label: '语音识别', description: '把音频转成文字，包括本地转写和云端识别。' },
	{ id: 'audio_generation', label: '音频生成', description: '生成音效、音乐或其他非语音合成音频。' }
];

export const resourceGroups: Array<{ id: ResourceGroup; label: string; description: string }> = [
	{ id: 'tts', label: '语音合成模型', description: '供本地语音合成引擎读取的模型文件。' },
	{ id: 'asr', label: '语音识别模型', description: '供本地转写引擎读取的模型文件，参考版本也归入对应家族。' },
	{ id: 'workflow', label: '流程辅助模型', description: '由视频与音频工作流调用，包括分离、说话人分析和声纹复核。' }
];

export function engineTask(engine: EngineDetail): EngineTask {
	const capabilities = engine.manifest.capabilities;
	if (capabilities.includes('audio_generation')) return 'audio_generation';
	if (capabilities.includes('speech_recognition') || capabilities.includes('transcription')) return 'asr';
	return 'tts';
}

export function engineFamilyId(engineId: string): string {
	if (engineId.startsWith('vibevoice-asr')) return 'vibevoice-asr';
	if (engineId.startsWith('cosyvoice-')) return 'cosyvoice';
	if (engineId.startsWith('mimo-v2.5-')) return 'mimo-v2.5';
	if (engineId.startsWith('doubao-')) return 'doubao';
	return engineId;
}

export function engineFamilyLabel(engine: EngineDetail): string {
	const familyId = engineFamilyId(engine.manifest.engine_id);
	if (familyId === 'vibevoice-asr') return 'VibeVoice ASR';
	if (familyId === 'cosyvoice') return 'CosyVoice';
	if (familyId === 'mimo-v2.5') return 'MiMo V2.5';
	if (familyId === 'doubao') return '豆包语音';
	return engine.manifest.display_name;
}

export function engineVariantLabel(engine: EngineDetail): string {
	const id = engine.manifest.engine_id;
	if (id.endsWith('-4bit')) return '4bit';
	if (id.endsWith('-8bit')) return '8bit';
	if (id.endsWith('-sft')) return 'SFT 预置音色';
	if (id.endsWith('-zero-shot')) return 'Zero-Shot 克隆';
	if (id.includes('voicedesign')) return '声音设计';
	if (id.includes('voiceclone') || id.includes('voice-clone')) return '声音克隆';
	if (id.includes('preset')) return '预置音色';
	if (id.endsWith('-asr')) return '语音识别';
	return engine.manifest.display_name;
}

export function engineAvailability(
	engine: EngineDetail,
	installation?: EngineInstallation
): StatusPresentation {
	if (engine.compatibility?.compatible === false) {
		return { key: 'error', label: '当前设备不可用', detail: engine.compatibility.message, tone: 'fail' };
	}
	if (engine.manifest.engine_type === 'cloud') {
		if (engine.state.status === 'error') {
			return { key: 'error', label: '连接异常', detail: engine.state.error_message || '请检查凭据和服务配置', tone: 'fail' };
		}
		if (engine.state.status === 'loaded' || engine.state.status === 'running') {
			return { key: 'ready', label: '连接正常', detail: '已通过连接检查', tone: 'ok' };
		}
		return { key: 'needs_setup', label: '未检查', detail: '按需连接 · 可执行连接检查', tone: 'neutral' };
	}
	if (engine.state.status === 'error') {
		return { key: 'error', label: '运行错误', detail: engine.state.error_message || '请运行环境检查', tone: 'fail' };
	}
	if (engine.state.status === 'loaded' || engine.state.status === 'running') {
		return { key: 'ready', label: '运行中', detail: '引擎已加载', tone: 'ok' };
	}
	if (!installation || !installation.installed) {
		return { key: 'needs_install', label: '需安装', detail: '模型尚未下载', tone: 'warning' };
	}
	if (installation.installation_status === 'failed' || installation.error) {
		return { key: 'error', label: '运行错误', detail: installation.error || '模型文件校验失败', tone: 'fail' };
	}
	if (installation.runtime_ready !== true) {
		return { key: 'needs_setup', label: '需配置', detail: '模型已下载 · 运行环境未就绪', tone: 'warning' };
	}
	return { key: 'ready', label: '可启动', detail: '模型已下载 · 环境正常', tone: 'ok' };
}

export function resourceGroup(installation: EngineInstallation, engine?: EngineDetail): ResourceGroup {
	const id = installation.engine_id.toLowerCase();
	if (
		installation.category === 'media_tool' ||
		id.includes('roformer') ||
		id.includes('moss-transcribe-diarize') ||
		id.includes('campplus') ||
		installation.category === 'localization_model'
	) return 'workflow';
	if (
		installation.category === 'asr_model' ||
		id.includes('vibevoice-asr') ||
		id.includes('whisper') ||
		id.includes('qwen3-asr') ||
		(engine && engineTask(engine) === 'asr')
	) return 'asr';
	return 'tts';
}

export function resourceRoleLabel(installation: EngineInstallation): string {
	const id = installation.engine_id.toLowerCase();
	if (id.includes('roformer')) return '人声/背景分离';
	if (id.includes('moss-transcribe-diarize')) return '说话人分离';
	if (id.includes('campplus')) return '声纹复核';
	if (id.includes('forced-aligner')) return '字幕时间对齐';
	if (id.includes('labse')) return '跨语言语义对齐';
	if (installation.reference_only) return '参考版本';
	return '引擎模型';
}

export function resourceSizeLabel(installation: EngineInstallation): string | null {
	const bytes = Number(
		installation.installed
			? installation.size_bytes || installation.total_bytes || 0
			: installation.total_bytes || 0
	);
	if (bytes <= 0) return null;
	const size = bytes >= 1_000_000_000
		? `${(bytes / 1_000_000_000).toFixed(2)} GB`
		: `${Math.round(bytes / 1_000_000)} MB`;
	return installation.installed ? size : `下载约 ${size}`;
}

export function resourceAvailability(installation: EngineInstallation): StatusPresentation {
	if (installation.reference_only && installation.installed) {
		return { key: 'ready', label: '仅供参考', detail: '已下载 · 不参与自动选择', tone: 'neutral' };
	}
	if (installation.installation_status === 'installing') {
		return { key: 'needs_install', label: '下载中', detail: '正在写入并校验模型文件', tone: 'warning' };
	}
	if (installation.installation_status === 'failed' || installation.error || installation.integrity === 'invalid') {
		return { key: 'error', label: '文件不完整', detail: installation.error || '请重新下载或检查模型目录', tone: 'fail' };
	}
	if (!installation.installed) {
		return { key: 'needs_install', label: '未下载', detail: '本机没有完整模型文件', tone: 'neutral' };
	}
	return { key: 'ready', label: '已下载', detail: installation.runtime_ready === false ? '文件完整 · 运行环境需另行配置' : '文件完整', tone: 'ok' };
}

export function availabilityMatches(filter: AvailabilityFilter, key: AvailabilityKey): boolean {
	return filter === 'all' || filter === key;
}
