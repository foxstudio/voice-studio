import type { EngineStatus, TaskStatus } from '$lib/api/types';

export function engineStatusLabel(status: EngineStatus | string) {
	return {
		not_installed: '未安装',
		stopped: '已停止',
		loading: '加载中',
		loaded: '已加载',
		running: '运行中',
		error: '错误'
	}[status] ?? status;
}

export function taskStatusLabel(status: TaskStatus | string) {
	return {
		pending: '待处理',
		queued: '排队中',
		running: '生成中',
		postprocessing: '后处理',
		success: '成功',
		failed: '失败',
		cancelled: '取消',
		retrying: '重试中'
	}[status] ?? status;
}

export function taskTypeLabel(type: string) {
	return {
		single: '语音合成',
		segment: '段落生成',
		batch: '批量生成',
		export: '导出'
	}[type] ?? type;
}

export function capabilityLabel(capability: string) {
	return {
		local_inference: '本地推理',
		voice_clone: '声音克隆',
		zero_shot: '零样本',
		emotion_control: '情绪控制',
		long_text: '长文本',
		pinyin_control: '拼音校正',
		voice_design: '声音设计',
		multilingual: '多语言',
		nonverbal_tags: '非语言标签',
		cloud_api: '云端 API',
		preset_voice: '预置音色',
		natural_language_control: '自然语言控制',
		audio_tags: '音频标签',
		singing: '唱歌',
		speech_recognition: '语音识别',
		transcription: '转写',
		language_identification: '语言识别'
		}[capability] ?? capability;
}

export function segmentStatusLabel(status: string) {
	return {
		empty: '空',
		ready: '就绪',
		queued: '排队中',
		generating: '生成中',
		completed: '完成',
		failed: '失败',
		locked: '锁定'
	}[status] ?? status;
}

export function licenseLabel(license: string) {
	return {
		self_voice: '本人声音',
		company_authorized: '公司授权',
		authorized: '已授权',
		localized_dub_source: '本土化',
		test_only: '仅测试',
		unknown: '未知',
		commercial_forbidden: '禁止商用'
	}[license] ?? license;
}

export const VOICE_AUTH_TAG_KEYWORDS = ['测试', '授权', '许可', '商用', '自有', '试用', '本土化'];

export function voiceAuthTags(tags: string[]) {
	return tags
		.filter((tag) => VOICE_AUTH_TAG_KEYWORDS.some((keyword) => tag.includes(keyword)))
		.slice(0, 3);
}
