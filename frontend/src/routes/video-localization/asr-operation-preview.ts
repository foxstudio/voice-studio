import type { VideoLocalizationOperation } from '$lib/api/types';

export type AsrPreviewCue = {
	cue_id: string;
	start_ms: number;
	end_ms: number;
	text: string;
};

export type AsrOperationPreview = {
	operationId: string;
	phase: 'asr_draft' | 'text_review' | 'timing_segmentation';
	phaseLabel: string;
	stage: string;
	progress: number;
	cues: AsrPreviewCue[];
};

const PHASE_LABELS: Record<AsrOperationPreview['phase'], string> = {
	asr_draft: 'ASR 初稿',
	text_review: '文本校对',
	timing_segmentation: '校时与断句'
};

export function resolveAsrOperationPreview(operations: VideoLocalizationOperation[]): AsrOperationPreview | null {
	const operation = operations
		.filter((item) => item.kind === 'english_asr' && (item.status === 'queued' || item.status === 'running'))
		.sort((a, b) => operationTimestamp(b) - operationTimestamp(a))[0];
	if (!operation || operation.cancel_requested) return null;
	const summary = operation.result_summary ?? {};
	const phase = summary.preview_phase;
	if (phase !== 'asr_draft' && phase !== 'text_review' && phase !== 'timing_segmentation') return null;
	const rawCues = Array.isArray(summary.preview_cues) ? summary.preview_cues : [];
	const cues = rawCues.flatMap((raw, index) => {
		if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
		const cue = raw as Record<string, unknown>;
		const startMs = Number(cue.start_ms);
		const endMs = Number(cue.end_ms);
		const text = String(cue.text ?? '').trim();
		if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs || !text) return [];
		return [{
			cue_id: String(cue.cue_id || `preview_${index + 1}`),
			start_ms: Math.max(0, Math.round(startMs)),
			end_ms: Math.max(1, Math.round(endMs)),
			text
		}];
	});
	return {
		operationId: operation.operation_id,
		phase,
		phaseLabel: PHASE_LABELS[phase],
		stage: typeof summary.stage === 'string' ? summary.stage : PHASE_LABELS[phase],
		progress: Math.max(0, Math.min(1, operation.progress ?? 0)),
		cues
	};
}

function operationTimestamp(operation: VideoLocalizationOperation) {
	const value = operation.started_at || operation.created_at;
	const timestamp = value ? new Date(value).getTime() : 0;
	return Number.isFinite(timestamp) ? timestamp : 0;
}
