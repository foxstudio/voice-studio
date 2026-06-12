import type { EngineDetail, GenerationTask, LongformTask, TTSVerificationResponse, VoiceAsset } from '$lib/api/types';
import { taskStatusLabel } from '$lib/labels';

export type TaskStatusTab = 'all' | 'active' | 'success' | 'failed';
export type TaskSourceFilter = 'all' | 'local' | 'cloud';
export type TaskDateFilter = 'all' | 'today' | '7d' | '30d';
export type TaskSortBy = 'latest' | 'oldest' | 'duration_desc';

export function statusIsActive(status: string) { return ['pending', 'queued', 'running', 'postprocessing', 'retrying'].includes(status); }
export function taskIsActive(t: GenerationTask) { return statusIsActive(t.status); }
export function taskIsWaiting(t: GenerationTask) { return t.status === 'pending' || t.status === 'queued'; }
export function taskIsProcessing(t: GenerationTask) { return t.status === 'running' || t.status === 'postprocessing' || t.status === 'retrying'; }
export function taskIsSuccess(t: GenerationTask) { return t.status === 'success'; }
export function taskIsFailed(t: GenerationTask) { return t.status === 'failed' || t.status === 'cancelled'; }
export function taskCanDelete(t: GenerationTask) { return !taskIsActive(t); }

export function taskIsLongformSegment(t: GenerationTask) { return Boolean(t.longform_task_id && t.longform_segment_index && t.longform_segment_count); }
export function taskIsLongformExport(t: GenerationTask) { return Boolean(t.longform_task_id && t.task_type === 'export'); }

export function longformResultLabel(t: GenerationTask) {
	if (taskIsLongformExport(t)) return '完整片段';
	if (taskIsLongformSegment(t)) return `片段 ${t.longform_segment_index}/${t.longform_segment_count}`;
	return '';
}

export function longformResultTitle(t: GenerationTask) {
	if (taskIsLongformExport(t)) return '合并后的完整长文本音频';
	if (taskIsLongformSegment(t)) return `同一篇长文本的第 ${t.longform_segment_index} 段，共 ${t.longform_segment_count} 段`;
	return '';
}

export function displayTitle(task: GenerationTask) {
	const title = task.input_text.trim() || '未命名任务';
	return taskIsLongformExport(task) ? `完整长文本：${title}` : title;
}

export function voiceName(task: GenerationTask, voiceMap: Map<string, VoiceAsset>) {
	return task.voice_id ? voiceMap.get(task.voice_id)?.name ?? '' : '';
}

export function voiceBadgeLabel(task: GenerationTask, voiceMap: Map<string, VoiceAsset>) {
	const localVoice = voiceName(task, voiceMap);
	if (localVoice) return localVoice;
	const mimoVoice = task.parameters.mimo_voice;
	if (typeof mimoVoice === 'string' && mimoVoice.trim()) return mimoVoice.trim();
	const speakerId = task.parameters.speaker_id;
	if (typeof speakerId === 'string' && speakerId.trim()) return speakerId.trim();
	if (typeof task.parameters.voice_design_prompt === 'string' && task.parameters.voice_design_prompt.trim()) return '声音设计';
	return '未选音色';
}

export function engineKind(engineId: string, engineMap: Map<string, EngineDetail>) {
	return engineMap.get(engineId)?.manifest.engine_type ?? (engineId.startsWith('mimo-') ? 'cloud' : 'local');
}

export function engineTypeLabel(engineId: string, engineMap: Map<string, EngineDetail>) {
	return engineKind(engineId, engineMap) === 'cloud' ? '云端' : '本地';
}

export function formatTime(value: string | null) {
	if (!value) return '';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
}

export function formatSeconds(totalSeconds: number) {
	const m = Math.floor(totalSeconds / 60);
	const s = totalSeconds % 60;
	return `${m}:${s.toString().padStart(2, '0')}`;
}

export function formatAudioDuration(durationMs: number | null) {
	if (!durationMs) return '';
	return `${(durationMs / 1000).toFixed(1)}s`;
}

export function elapsedSeconds(task: GenerationTask) {
	if (!task.started_at) return 0;
	const started = new Date(task.started_at).getTime();
	if (!Number.isFinite(started)) return 0;
	const end = task.completed_at ? new Date(task.completed_at).getTime() : Date.now();
	return Math.max(0, Math.floor((end - started) / 1000));
}

export function waitingSeconds(task: GenerationTask) {
	const created = new Date(task.created_at).getTime();
	if (!Number.isFinite(created)) return 0;
	return Math.max(0, Math.floor((Date.now() - created) / 1000));
}

export function elapsedLabel(task: GenerationTask) { return formatSeconds(elapsedSeconds(task)); }

export function progressLabel(task: GenerationTask, queueOrderedTasks: GenerationTask[]) {
	if (taskIsWaiting(task)) {
		const pos = queueOrderedTasks.filter(t => taskIsWaiting(t)).findIndex(t => t.task_id === task.task_id) + 1;
		return pos ? `等待 ${pos}` : '等待';
	}
	if (task.status === 'running') return `${Math.round((task.progress || 0) * 100)}%`;
	if (task.status === 'postprocessing') return '收尾';
	if (task.status === 'success') return '100%';
	return taskStatusLabel(task.status);
}

export function taskStatusPillLabel(task: GenerationTask, queueCounts: { processing: number; waiting: number }, queueOrderedTasks: GenerationTask[]) {
	if (task.status === 'queued' || task.status === 'pending') {
		const pos = queueOrderedTasks.filter(t => taskIsWaiting(t)).findIndex(t => t.task_id === task.task_id) + 1;
		if (queueCounts.processing === 0) return '等待接手';
		return pos ? `排队 ${pos}` : '排队中';
	}
	if (task.status === 'running') return '渲染中';
	if (task.status === 'postprocessing') return '写入中';
	return taskStatusLabel(task.status);
}

export function taskProgressWidth(task: GenerationTask) {
	if (taskIsWaiting(task)) return 0;
	return Math.max(8, Math.round((task.progress || 0) * 100));
}

export function taskTimingLine(task: GenerationTask) {
	if (taskIsWaiting(task)) return `已等待 ${formatSeconds(waitingSeconds(task))}`;
	if (task.generation_time_ms) {
		const s = (task.generation_time_ms / 1000).toFixed(1);
		return task.status === 'failed' ? `失败前运行 ${s}s` : `生成耗时 ${s}s`;
	}
	const el = elapsedLabel(task);
	if (!el || el === '0:00') return '等待开始';
	if (task.status === 'failed') return `失败前运行 ${el}`;
	if (task.status === 'cancelled') return `取消前运行 ${el}`;
	if (task.status === 'success') return `总耗时 ${el}`;
	return `已运行 ${el}`;
}

export function taskStageLabel(task: GenerationTask, queueOrderedTasks: GenerationTask[]) {
	if (taskIsWaiting(task)) {
		const pos = queueOrderedTasks.filter(t => taskIsWaiting(t)).findIndex(t => t.task_id === task.task_id) + 1;
		return pos ? `等待队列第 ${pos} 位` : '等待后台接手';
	}
	if (task.status === 'cancelled') return '已取消';
	if (task.status === 'failed') return '已失败';
	if (task.status === 'success') return '已完成';
	if (task.status === 'postprocessing') return '后处理';
	if ((task.progress ?? 0) < 0.2) return '预热模型';
	if ((task.progress ?? 0) < 0.55) return '声学推理';
	if ((task.progress ?? 0) < 0.88) return '写入音频';
	return '收尾处理中';
}

export function queueSummaryText(queueCounts: { processing: number; waiting: number }, queueOrderedTasks: GenerationTask[], engineMap: Map<string, EngineDetail>) {
	if (!queueCounts.processing && !queueCounts.waiting) return '';
	if (queueCounts.processing) {
		const current = queueOrderedTasks.find(t => taskIsProcessing(t));
		const engineName = current ? engineMap.get(current.engine_id)?.manifest.display_name ?? current.engine_id : '后台任务';
		const waiting = queueCounts.waiting ? `，后面等待 ${queueCounts.waiting} 条` : '';
		return `正在执行：${engineName}${waiting}。`;
	}
	const next = queueOrderedTasks.find(t => taskIsWaiting(t));
	if (!next) return `等待 ${queueCounts.waiting} 条任务。`;
	const engine = engineMap.get(next.engine_id);
	const engineName = engine?.manifest.display_name ?? next.engine_id;
	const engineState = engine?.state.status;
	const stateText = engineState && engineState !== 'loaded' ? `，当前引擎状态：${engineState}` : '';
	return `等待后台 worker 接手：下一条是 ${engineName}${stateText}。如果这里长时间不变，通常是后台服务刚恢复、任务没有进入内存队列，或任务参数缺少必需音色。`;
}

const RUNTIME_PROFILES: Record<string, { slowAfterSeconds: number; timeoutSeconds: number }> = {
	omnivoice: { slowAfterSeconds: 480, timeoutSeconds: 600 },
	'indextts-v2': { slowAfterSeconds: 150, timeoutSeconds: 420 },
	emotivoice: { slowAfterSeconds: 150, timeoutSeconds: 420 },
	'f5-tts': { slowAfterSeconds: 210, timeoutSeconds: 600 },
	'cosyvoice-sft': { slowAfterSeconds: 360, timeoutSeconds: 900 },
	'cosyvoice-zero-shot': { slowAfterSeconds: 360, timeoutSeconds: 900 },
	'mimo-v2.5-tts-preset': { slowAfterSeconds: 90, timeoutSeconds: 300 },
	'mimo-v2.5-tts-voicedesign': { slowAfterSeconds: 90, timeoutSeconds: 300 },
	'mimo-v2.5-tts-voiceclone': { slowAfterSeconds: 120, timeoutSeconds: 300 }
};

export function taskEtaLabel(task: GenerationTask) {
	if (taskIsWaiting(task) || !taskIsActive(task) || !task.started_at) return '';
	const progress = task.progress ?? 0;
	const profile = RUNTIME_PROFILES[task.engine_id] ?? { slowAfterSeconds: 180, timeoutSeconds: 300 };
	const elapsed = elapsedSeconds(task);
	if (progress >= 0.9) {
		const r = profile.timeoutSeconds - elapsed;
		return r > 10 ? `保护窗口剩 ${formatSeconds(r)}` : '';
	}
	if (progress < 0.18 || elapsed < 2) return '';
	const remaining = Math.max(0, Math.round(elapsed / progress - elapsed));
	if (!Number.isFinite(remaining) || remaining <= 1) return '';
	return `预计剩余 ${formatSeconds(remaining)}`;
}

export function taskRuntimeHint(task: GenerationTask, queueCounts: { processing: number; waiting: number }, queueOrderedTasks: GenerationTask[]) {
	if (taskIsWaiting(task)) {
		if (queueCounts.processing === 0) return '没有正在渲染的任务；此任务正在等待后台 worker 接手。若持续不动，请检查后台服务或任务参数。';
		const pos = queueOrderedTasks.filter(t => taskIsWaiting(t)).findIndex(t => t.task_id === task.task_id) + 1;
		return pos && pos > 1 ? `前面还有 ${pos - 1} 条任务，当前 worker 会按创建时间顺序处理。` : '前面有任务正在渲染，完成后会轮到这一条。';
	}
	if (!taskIsActive(task) || !task.started_at) return '';
	const profile = RUNTIME_PROFILES[task.engine_id] ?? { slowAfterSeconds: 180, timeoutSeconds: 300 };
	const elapsed = elapsedSeconds(task);
	if (elapsed >= profile.timeoutSeconds) return '已超过超时保护窗口，等待后台收敛状态。';
	if (elapsed >= profile.slowAfterSeconds) return '已超过常规时长，仍在等待模型返回。';
	if ((task.progress ?? 0) >= 0.9) return '接近收尾，长音频可能会在最后阶段停留一会儿。';
	return '';
}


export function verificationStatusLabel(status: TTSVerificationResponse['status']) {
	return { passed: '校对通过', warning: '需要复听', failed: '缺句风险', skipped: '未校对' }[status] ?? status;
}

export function numericParam(task: GenerationTask, key: string) { const v = task.parameters[key]; return typeof v === 'number' ? v : null; }
export function textParam(task: GenerationTask, key: string) { const v = task.parameters[key]; return typeof v === 'string' && v.trim() ? v : null; }
export function taskSupportsParam(task: GenerationTask, key: string, engineMap: Map<string, EngineDetail>) {
	return (engineMap.get(task.engine_id)?.manifest.parameter_schema ?? []).some(p => p.key === key);
}

type ParameterEntry = { label: string; value: string };

export function taskParameterEntries(task: GenerationTask, engineMap: Map<string, EngineDetail>, voiceMap: Map<string, VoiceAsset>): ParameterEntry[] {
	const e: ParameterEntry[] = [
		{ label: '引擎', value: engineMap.get(task.engine_id)?.manifest.display_name ?? task.engine_id },
		{ label: '来源', value: engineTypeLabel(task.engine_id, engineMap) }
	];
	const vn = voiceName(task, voiceMap);
	if (vn) e.push({ label: '音色', value: vn });
	if (taskSupportsParam(task, 'language', engineMap) && textParam(task, 'language')) e.push({ label: '语言', value: textParam(task, 'language')! });
	if (taskSupportsParam(task, 'emotion', engineMap) && textParam(task, 'emotion')) e.push({ label: '情绪', value: textParam(task, 'emotion')! });
	if (taskSupportsParam(task, 'mimo_voice', engineMap) && textParam(task, 'mimo_voice')) e.push({ label: 'MiMo 音色', value: textParam(task, 'mimo_voice')! });
	if (taskSupportsParam(task, 'speaker_id', engineMap) && textParam(task, 'speaker_id')) e.push({ label: '预置音色', value: textParam(task, 'speaker_id')! });
	if (taskSupportsParam(task, 'speed', engineMap) && numericParam(task, 'speed') !== null) e.push({ label: '语速', value: numericParam(task, 'speed')!.toFixed(2) });
	if (taskSupportsParam(task, 'temperature', engineMap) && numericParam(task, 'temperature') !== null) e.push({ label: 'Temperature', value: numericParam(task, 'temperature')!.toFixed(2) });
	if (taskSupportsParam(task, 'top_p', engineMap) && numericParam(task, 'top_p') !== null) e.push({ label: 'Top-P', value: numericParam(task, 'top_p')!.toFixed(2) });
	if (taskSupportsParam(task, 'top_k', engineMap) && numericParam(task, 'top_k') !== null) e.push({ label: 'Top-K', value: String(numericParam(task, 'top_k')) });
	if (taskSupportsParam(task, 'emo_alpha', engineMap) && numericParam(task, 'emo_alpha') !== null) e.push({ label: '情绪强度', value: numericParam(task, 'emo_alpha')!.toFixed(2) });
	if (taskSupportsParam(task, 'diffusion_steps', engineMap) && numericParam(task, 'diffusion_steps') !== null) e.push({ label: '扩散步数', value: String(numericParam(task, 'diffusion_steps')) });
	if (taskSupportsParam(task, 'cfg_rate', engineMap) && numericParam(task, 'cfg_rate') !== null) e.push({ label: 'CFG', value: numericParam(task, 'cfg_rate')!.toFixed(2) });
	if (textParam(task, 'output_format')) e.push({ label: '格式', value: textParam(task, 'output_format')!.toUpperCase() });
	return e;
}

export function taskParameterCopyText(task: GenerationTask, engineMap: Map<string, EngineDetail>, voiceMap: Map<string, VoiceAsset>) {
	return taskParameterEntries(task, engineMap, voiceMap)
		.map((entry) => `${entry.label}: ${entry.value}`)
		.join('\n');
}

export function knownErrorMessage(message: string | null | undefined) {
	if (!message) return '';
	const known: Record<string, string> = {
		'400: IndexTTS v2 需要参考音频': 'IndexTTS v2 需要先选择一个带参考音频的本地音色。',
		'IndexTTS v2 需要参考音频': 'IndexTTS v2 需要先选择一个带参考音频的本地音色。',
		'REFERENCE_TEXT_REQUIRED': '这个引擎需要准确的参考台词，请先在音色库补全参考文本。',
		'MIMO_API_KEY_MISSING': '缺少 MiMo API Key，请先到设置里配置。',
		'MIMO_CLOUD_DISABLED': 'MiMo 云端引擎还没有启用，请先到设置里打开。'
	};
	return known[message] ?? message;
}

export function resultDownloadName(task: GenerationTask) {
	const safeTitle = displayTitle(task).replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40) || 'tts';
	const format = typeof task.parameters.output_format === 'string' ? task.parameters.output_format : 'wav';
	return `${safeTitle}_${task.task_id.slice(0, 8)}.${format}`;
}

export function longformTitle(task: LongformTask) { return task.input_text.trim() || '长文本任务'; }
export function longformStatusText(task: LongformTask) {
	const success = task.segments.filter(s => s.status === 'success').length;
	return `${taskStatusLabel(task.status)} · ${success}/${task.segments.length} 段`;
}
export function longformDownloadUrl(task: LongformTask) { return task.export_id ? `/api/longform/${task.longform_task_id}/download` : ''; }

export function longformGroupSortTime(task: GenerationTask, group: GenerationTask[], sortBy: TaskSortBy) {
	const times = group.map(t => new Date(t.created_at).getTime()).filter(Number.isFinite);
	if (!times.length) return new Date(task.created_at).getTime() || 0;
	return sortBy === 'oldest' ? Math.min(...times) : Math.max(...times);
}

export function longformItemRank(task: GenerationTask) {
	if (taskIsLongformExport(task)) return 0;
	if (taskIsLongformSegment(task)) return task.longform_segment_index ?? 999;
	return 999;
}

export function compareLongformGroupOrder(a: GenerationTask, b: GenerationTask, scope: GenerationTask[], sortBy: TaskSortBy) {
	const ga = a.longform_task_id ? scope.filter(t => t.longform_task_id === a.longform_task_id) : [a];
	const gb = b.longform_task_id ? scope.filter(t => t.longform_task_id === b.longform_task_id) : [b];
	const gkA = a.longform_task_id ?? a.task_id;
	const gkB = b.longform_task_id ?? b.task_id;
	if (gkA !== gkB) {
		const ta = longformGroupSortTime(a, ga, sortBy);
		const tb = longformGroupSortTime(b, gb, sortBy);
		if (ta !== tb) return sortBy === 'oldest' ? ta - tb : tb - ta;
		return gkA.localeCompare(gkB);
	}
	const rd = longformItemRank(a) - longformItemRank(b);
	if (rd !== 0) return rd;
	return a.created_at.localeCompare(b.created_at) || a.task_id.localeCompare(b.task_id);
}
