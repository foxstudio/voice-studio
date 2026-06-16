import type { BatchTask, GenerateRequest, VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationOperation, VideoLocalizationReferenceClip } from '$lib/api/types';

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
		{ label: '英文 ASR', status: hasAsr ? 'done' : hasSourceAudio ? 'active' : 'pending' },
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
	if (operation.status === 'running') return '处理中';
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
		review_status: 'needs_review',
		quality_flags: ['手动新增'],
		notes: null
	};
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
