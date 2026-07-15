import type { BatchTask, GenerateRequest, VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationGeneratedCandidate, VideoLocalizationOperation, VideoLocalizationReferenceClip, VideoLocalizationSpeaker, VideoLocalizationTimelineClip } from '$lib/api/types';

export type WorkflowStep = {
	label: string;
	status: 'done' | 'active' | 'blocked' | 'pending';
};

export function buildWorkflow(current: VideoLocalizationDraft | null): WorkflowStep[] {
	const hasSource = Boolean(current?.source_media.filename || current?.source_media.video_path);
	const hasSourceAudio = Boolean(current?.source_media.audio_path || current?.stems.original_audio_path);
	const stemsReady = current?.stems.separation_status === 'completed';
	const hasAsr = Boolean(current?.cues.some((cue) => cue.en_subtitle_text?.trim()));
	const hasSpeakers = Boolean(current?.speakers.length);
	const hasReviewed = Boolean(current?.cues.some((cue) => cue.review_status === 'ready' || cue.review_status === 'locked'));
	const hasTts = Boolean(current?.cues.some((cue) => cue.tts_audio_path || cue.tts_result_id));
	const readyForTts = Boolean(current?.cues.some((cue) => cue.review_status === 'ready' && cue.tts_recommended_text?.trim()));
	const blocked = current?.quality_gate.status === 'blocked';
	return [
		{ label: '导入', status: hasSource ? 'done' : 'active' },
		{ label: '人声分离', status: stemsReady ? 'done' : hasSource ? 'active' : 'pending' },
		{ label: '生成 ASR 字幕', status: hasAsr ? 'done' : hasSourceAudio ? 'active' : 'pending' },
		{ label: '说话人', status: hasSpeakers ? 'done' : hasAsr ? 'active' : 'pending' },
		{ label: '人工校对', status: blocked ? 'blocked' : hasReviewed ? 'active' : 'pending' },
		{ label: 'TTS', status: hasTts ? 'done' : readyForTts ? 'active' : 'pending' },
		{ label: 'JSON', status: current ? 'active' : 'pending' }
	];
}

export function statusLabel(status: VideoLocalizationCue['review_status']) {
	return {
		ready: '可生成',
		needs_review: '待校对',
		blocked: '阻断',
		locked: '已锁定'
	}[status];
}

export function gateLabel(status: VideoLocalizationDraft['quality_gate']['status'] | undefined) {
	return {
		pass: '质量门通过',
		warning: '存在警告',
		blocked: '存在阻断',
		unknown: '未检查'
	}[status ?? 'unknown'];
}

export function gateBadgeClass(status: VideoLocalizationDraft['quality_gate']['status'] | undefined) {
	if (status === 'pass') return 'ok';
	if (status === 'blocked') return 'fail';
	if (status === 'warning') return 'warn';
	return '';
}

export function speakerColor(speakerId: string | null | undefined) {
	const colors = ['#4f9cf9', '#42c49b', '#e4ad42', '#b58cff', '#ff8c8c'];
	const index = Math.abs([...(speakerId ?? 'unknown')].reduce((sum, char) => sum + char.charCodeAt(0), 0)) % colors.length;
	return colors[index];
}

export function msLabel(ms: number | null | undefined) {
	if (ms === null || ms === undefined) return '--:--.--';
	const totalSeconds = ms / 1000;
	const minutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(2).padStart(5, '0')}`;
}

export function timeLabel(cue: VideoLocalizationCue) {
	return `${msLabel(cue.start_ms)} - ${msLabel(cue.end_ms)}`;
}

export function durationLabel(ms: number | null | undefined) {
	if (!ms) return '未知';
	return `${(ms / 1000).toFixed(1)}s`;
}

export function ttsAudioUrl(projectId: string, cue: VideoLocalizationCue) {
	return projectId && cue.tts_audio_path ? `/api/projects/${projectId}/video-localization/cues/${cue.cue_id}/tts-audio` : '';
}

export function sourceVideoUrl(projectId: string, current: VideoLocalizationDraft | null) {
	return projectId && current?.source_media.video_path ? `/api/projects/${projectId}/video-localization/source-media/video` : '';
}

export function sourceAudioUrl(projectId: string, current: VideoLocalizationDraft | null) {
	return projectId && (current?.source_media.audio_path || current?.stems.original_audio_path) ? `/api/projects/${projectId}/video-localization/source-media/audio` : '';
}

export function stemAudioUrl(projectId: string, current: VideoLocalizationDraft | null, kind: 'vocals' | 'background') {
	if (!projectId || !current) return '';
	if (kind === 'vocals' && !current.stems.vocals_clean_path) return '';
	if (kind === 'background' && !current.stems.background_path) return '';
	return `/api/projects/${projectId}/video-localization/stems/${kind}/audio`;
}

export function referenceAudioUrl(projectId: string, clip: VideoLocalizationReferenceClip) {
	return projectId && clip.audio_path ? `/api/projects/${projectId}/video-localization/reference-clips/${clip.reference_clip_id}/audio` : '';
}

export function referenceCoverUrl(projectId: string, clip: VideoLocalizationReferenceClip) {
	return projectId && clip.cover_frame_path ? `/api/projects/${projectId}/video-localization/reference-clips/${clip.reference_clip_id}/cover` : '';
}

export function candidateAudioUrl(projectId: string, candidate: VideoLocalizationGeneratedCandidate) {
	return projectId && candidate.audio_path ? `/api/projects/${projectId}/video-localization/candidates/${candidate.candidate_id}/audio` : '';
}

export function timelineClipAudioUrl(projectId: string, clip: VideoLocalizationTimelineClip) {
	return projectId && (clip.clip_id === 'media_original' || clip.audio_path) ? `/api/projects/${projectId}/video-localization/timeline-clips/${clip.clip_id}/audio` : '';
}

export function timelineClipWaveformUrl(projectId: string, clip: VideoLocalizationTimelineClip) {
	return projectId && (clip.clip_id === 'media_original' || clip.audio_path) ? `/api/projects/${projectId}/video-localization/timeline-clips/${clip.clip_id}/waveform` : '';
}

export function sourceCueAudioUrl(projectId: string, cue: VideoLocalizationCue) {
	return projectId && cue.start_ms !== null && cue.end_ms !== null ? `/api/projects/${projectId}/video-localization/cues/${cue.cue_id}/source-audio` : '';
}

export function sortOperations(items: VideoLocalizationOperation[]) {
	return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function isActiveOperation(operation: VideoLocalizationOperation) {
	return operation.status === 'queued' || operation.status === 'running';
}

export function operationStatusLabel(operation: VideoLocalizationOperation | null | undefined) {
	if (!operation) return '未开始';
	if (operation.status === 'queued') return '排队中';
	if (operation.status === 'running') {
		const stage = typeof operation.result_summary?.stage === 'string' ? operation.result_summary.stage.trim() : '';
		const percent = Math.round(Math.max(0, Math.min(1, operation.progress ?? 0)) * 100);
		return `${stage || '处理中'} · ${percent}%`;
	}
	if (operation.status === 'success') return '已完成';
	if (operation.status === 'failed') return '失败';
	if (operation.status === 'cancelled') return '已取消';
	return operation.status;
}

export function operationBadgeClass(operation: VideoLocalizationOperation | null | undefined) {
	if (!operation) return '';
	if (operation.status === 'success') return 'ok';
	if (operation.status === 'failed' || operation.status === 'cancelled') return 'fail';
	if (isActiveOperation(operation)) return 'active';
	return '';
}

const VIDEO_LOCALIZATION_ERROR_MESSAGES: Record<string, string> = {
	'English ASR did not return subtitle text': '语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。',
	'English ASR source track must be auto, original, or vocals': '语音识别的来源轨道无效，请选择原音轨或分离后的人声轨。',
	'Import a source video before extracting audio': '请先导入视频，再抽取原音轨。',
	'Source video file is missing': '源视频文件不存在，请重新导入视频。',
	'Extract source audio before running this operation': '请先抽取原音轨，再执行此操作。',
	'Extract source audio before running stem separation': '请先抽取原音轨，再分离人声和背景音乐。',
	'Source audio file is missing': '原音频文件不存在，请重新抽取原音轨。',
	'Separate clean vocals before running English ASR from vocals': '请先分离人声和背景音乐，再识别人声轨字幕。',
	'Separate clean vocals before creating reference clips': '请先分离出干净人声，再创建参考音片段。',
	'Clean vocals file is missing': '分离后的人声音频不存在，请重新执行人声分离。',
	'Source audio is empty': '原音轨没有有效声音，无法分离人声和背景音乐。',
	'Failed to separate vocals and background': '人声与背景音乐分离失败，请检查原音轨后重试。',
	'No cue has speaker and time range for reference clipping': '没有可用于截取参考音的字幕片段，请先设置说话人和时间范围。',
	'Reference selection requires a valid start and end time': '参考音选区缺少有效的入点或出点。',
	'Reference selection exceeds source duration': '参考音选区超出了源音频时长。',
	'Reference clip not found': '参考音片段不存在，可能已被删除。',
	'Clean reference clips must come from separated clean vocals': '干净参考音必须从分离后的人声轨中截取。',
	'Reference audio file is missing': '参考音文件不存在，请重新截取或选择其他音色。',
	'Verified reference clips require ASR text': '通过校验的参考音需要填写对应的识别文本。',
	'Cue update is invalid': '字幕片段数据无效，请检查时间和文本内容。',
	'Cue not found': '字幕片段不存在，可能已被删除。',
	'Speaker already exists': '该说话人已存在。',
	'Speaker not found': '说话人不存在，可能已被删除。',
	'Create English ASR cues before generating Chinese localization draft': '请先生成原文字幕，再创建本土化草稿。',
	'All cues already have Chinese subtitle and TTS text': '所有字幕片段都已有本土化文本和配音台词，无需重复生成。',
	'All cues already have Chinese subtitles': '所有字幕片段都已有本土化文本，无需重复生成。',
	'No timed subtitle cues are available': '当前没有带时间码的字幕片段可供导出。',
	'No valid SRT subtitle entries were found': 'SRT 文件中没有识别到有效的字幕条目。',
	'Run source-language ASR before importing source subtitles': '请先生成原文字幕，再导入对应字幕内容。',
	'Only mp4, mov, m4v, webm, and mkv videos are supported': '仅支持 MP4、MOV、M4V、WEBM 和 MKV 视频。',
	'Uploaded video is empty': '导入的视频文件为空，请选择其他文件。',
	'Could not allocate a unique video path': '无法为视频创建唯一的存储位置，请修改文件名后重试。',
	'ffmpeg is required to extract source audio': '缺少 FFmpeg，无法抽取原音轨。',
	'Failed to extract source audio': '原音轨抽取失败，请检查视频文件后重试。',
	'ffmpeg is required to create reference clips': '缺少 FFmpeg，无法创建参考音片段。',
	'Reference clip time range is invalid': '参考音片段的时间范围无效。',
	'Failed to create reference clip': '参考音片段创建失败，请调整选区后重试。',
	'Timeline clip audio not found': '时间线上的音频文件不存在，请重新生成或导入。',
	'Operation not found': '任务不存在，可能已被清理。',
	'Operation is still active': '任务仍在处理中，请等待完成或先取消任务。',
	'Project not found': '项目不存在，可能已从本地删除。',
	'Project changed while this draft was being edited': '项目内容已在其他位置更新，已停止覆盖保存，请刷新后继续。',
	'No ready clone-from-source cues can be submitted': '没有可提交的配音片段，请先完成字幕、音色和台词准备。',
	'TTS batch task not found': '批量配音任务不存在，可能已被清理。',
	'Batch task has no segment results': '批量配音任务没有返回任何片段结果。',
	'TTS audio file not found': '合成音频文件不存在，请重新生成。',
	'Generated candidate not found': '候选声音不存在，可能已被删除。',
	'Generated candidate audio is not available': '候选声音的音频文件不可用，请重新生成。',
	'No routed speech clips are available to render': '没有可用于导出的配音片段。',
	'No timeline audio clips could be rendered': '时间线上没有可渲染的音频片段。',
	'Source video is required to render localized video': '导出本土化视频需要可用的源视频。',
	'ffmpeg is required to render localized video': '缺少 FFmpeg，无法导出本土化视频。',
	'Failed to render localized video': '本土化视频导出失败，请检查时间线素材后重试。',
	'Request validation failed': '提交的数据不完整或格式不正确，请检查后重试。',
	'Method Not Allowed': '当前服务还没有加载这项操作，请刷新服务后重试。',
	'Internal Server Error': '服务处理时出现异常，请稍后重试或打开详情继续排查。',
	'Bad Gateway': '后台服务暂时没有响应，请稍后重试。',
	'Service Unavailable': '后台服务暂时不可用，请稍后重试。',
	'Failed to fetch': '无法连接本地服务，请检查服务是否正在运行。'
};

export function localizeVideoLocalizationError(message: string | null | undefined) {
	if (!message) return '';
	const normalized = message.trim();
	const known = VIDEO_LOCALIZATION_ERROR_MESSAGES[normalized];
	if (known) return known;
	const cueTextMatch = normalized.match(/^Cue (.+) does not have production-ready TTS text$/);
	if (cueTextMatch) return `字幕片段 ${cueTextMatch[1]} 还没有可用于生成的配音台词。`;
	const cueReferenceMatch = normalized.match(/^Cue (.+) does not have a reference clip$/);
	if (cueReferenceMatch) return `字幕片段 ${cueReferenceMatch[1]} 还没有绑定参考音。`;
	const referenceMissingMatch = normalized.match(/^Reference audio is missing for (.+)$/);
	if (referenceMissingMatch) return `参考音 ${referenceMissingMatch[1]} 的音频文件不存在。`;
	const unsupportedOperationMatch = normalized.match(/^Unsupported operation: (.+)$/);
	if (unsupportedOperationMatch) return `不支持的任务类型：${unsupportedOperationMatch[1]}。`;
	return normalized;
}

export function summarizeVideoLocalizationError(message: string | null | undefined) {
	const localized = localizeVideoLocalizationError(message);
	if (!localized) return '';
	const normalized = message?.trim() ?? '';
	if (localized !== normalized) return localized;
	const hasChinese = /[\u3400-\u9fff]/.test(localized);
	const looksTechnical = /[A-Za-z]{4,}|\b(?:HTTP|API|JSON|traceback|exception|error|failed|status)\b/i.test(localized);
	return !hasChinese && looksTechnical ? '操作没有完成，请打开详情查看具体原因。' : localized;
}

export function batchProjectId(batch: BatchTask) {
	const parameters = batch.parameters?.parameters;
	if (!parameters || typeof parameters !== 'object') return '';
	const value = (parameters as Record<string, unknown>).project_id;
	return typeof value === 'string' ? value : '';
}

export function batchOptionLabel(batch: BatchTask) {
	const success = batch.segments.filter((segment) => segment.status === 'success').length;
	const failed = batch.segments.filter((segment) => segment.status === 'failed').length;
	return `${batch.batch_task_id} · ${ttsBatchLabel(batch.status)} · 成功 ${success}/${batch.segments.length}${failed ? ` · 失败 ${failed}` : ''}`;
}

export function ttsBatchLabel(status: string | null | undefined) {
	return {
		queued: '队列中',
		running: '生成中',
		postprocessing: '处理中',
		success: '已生成',
		failed: '失败',
		cancelled: '已取消',
		retrying: '重试中'
	}[status ?? ''] ?? '待生成';
}

export function createManualCue(draft: VideoLocalizationDraft): VideoLocalizationCue {
	const index = draft.cues.length + 1;
	return {
		cue_id: `cue_${String(index).padStart(4, '0')}`,
		speaker_id: draft.speakers[0]?.speaker_id ?? null,
		start_ms: null,
		end_ms: null,
		audio_route: 'manual_review',
		en_subtitle_text: '',
		zh_localized_subtitle_text: '',
		tts_recommended_text: '',
		reference_clip_id: null,
		tts_result_id: null,
		tts_audio_path: null,
		tts_batch_task_id: null,
		tts_batch_status: null,
		tts_batch_error: null,
		tts_attempted_at: null,
		source_duration_ms: null,
		generated_duration_ms: null,
		source_word_ids: [],
		source_text_raw: null,
		timing_confidence: null,
		transcription_revision_id: null,
		review_status: 'needs_review',
		quality_flags: ['手动新增'],
		notes: null
	};
}

export function suggestSpeakerSeed(speakers: VideoLocalizationSpeaker[]) {
	const usedIds = new Set(speakers.map((speaker) => speaker.speaker_id));
	let index = speakers.length + 1;
	while (usedIds.has(`speaker_${String(index).padStart(2, '0')}`)) index += 1;
	return {
		speaker_id: `speaker_${String(index).padStart(2, '0')}`,
		display_name: speakerDisplaySeed(index - 1)
	};
}

function speakerDisplaySeed(index: number) {
	const labels = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
	return labels[index] ?? `S${index + 1}`;
}

export function buildGenerateRequest(projectId: string, cue: VideoLocalizationCue, reference: VideoLocalizationReferenceClip | null | undefined): GenerateRequest {
	return {
		text: cue.tts_recommended_text?.trim() ?? '',
		engine_id: 'indextts-v2',
		source: 'video_localization',
		project_id: projectId,
		segment_id: cue.cue_id,
		voice_id: null,
		voice_source: 'reference_audio',
		reference_audio_path: reference?.audio_path ?? null,
		reference_audio_license_status: '本土化',
		reference_audio_tags: ['视频本土化', '本土化', cue.speaker_id ?? 'unknown'],
		ref_text: reference?.asr_text || cue.en_subtitle_text || null,
		custom_reference_source_audio_path: reference?.audio_path ?? null,
		custom_reference_source_duration_ms: reference?.duration_ms ?? null,
		custom_reference_trim_start_ms: null,
		custom_reference_trim_end_ms: null,
		language: 'zh',
		emotion_mode: 'follow_reference',
		emotion: null,
		emotion_values: null,
		emotion_text: null,
		style_instruction: null,
		voice_design_prompt: null,
		optimize_text_preview: false,
		mimo_voice: null,
		speaker_id: null,
		prompt: null,
		nfe_step: 32,
		cfg_strength: 2,
		target_rms: 0.1,
		cross_fade_duration: 0.15,
		sway_sampling_coef: -1,
		fix_duration: 0,
		remove_silence: false,
		emo_alpha: 0.6,
		speed: 1,
		temperature: 0.8,
		top_p: 0.8,
		top_k: 30,
		repetition_penalty: 10,
		seed: null,
		max_mel_tokens: 1500,
		max_text_tokens_per_segment: 120,
		interval_silence: 200,
		segment_overlap_ms: 50,
		diffusion_steps: 25,
		cfg_rate: 0.7,
		guidance_scale: 2,
		duration: 0,
		output_format: 'wav'
	};
}
