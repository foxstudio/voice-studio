import type {
	VideoLocalizationDevelopmentAsrResult,
	VideoLocalizationDevelopmentInitialAnalysisResult,
	VideoLocalizationDevelopmentEntityNormalizationResult,
	VideoLocalizationDevelopmentReviewDecisionsResult,
	VideoLocalizationDevelopmentWholeRecheckResult,
	VideoLocalizationDraft,
	VideoLocalizationOperation
} from '$lib/api/types';

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
	isActive: boolean;
	sourceTrackId?: string;
	sourceAudioSha256?: string;
	cues: AsrPreviewCue[];
};

const PHASE_LABELS: Record<AsrOperationPreview['phase'], string> = {
	asr_draft: '原始听写稿',
	text_review: '文本校对',
	timing_segmentation: '校时与断句'
};

export function resolveAsrOperationPreview(operations: VideoLocalizationOperation[]): AsrOperationPreview | null {
	const asrOperations = operations.filter((item) => item.kind === 'english_asr');
	const operation = asrOperations
		.filter((item) => item.status === 'queued' || item.status === 'running')
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
		isActive: operation.status === 'queued' || operation.status === 'running',
		cues
	};
}

export function resolveDevelopmentAsrPreviewOperation(
	operations: VideoLocalizationOperation[]
): VideoLocalizationOperation | null {
	const asrOperations = operations.filter((item) => item.kind === 'english_asr');
	if (asrOperations.some((item) =>
		(item.status === 'queued' || item.status === 'running')
		&& resetsExistingSubtitlePreview(item)
	)) return null;
	const partial = asrOperations
		.filter((item) => (
			item.status === 'success'
			&& hasDevelopmentSubtitleSnapshot(item)
			&& item.result_summary?.artifact_available === true
		))
		.sort((a, b) => operationTimestamp(b) - operationTimestamp(a))[0];
	if (!partial) return null;
	const formal = asrOperations
		.filter((item) => item.status === 'success' && !isDevelopmentAsrOperation(item))
		.sort((a, b) => operationTimestamp(b) - operationTimestamp(a))[0];
	return !formal || operationTimestamp(partial) > operationTimestamp(formal) ? partial : null;
}

export function buildDevelopmentAsrPreview(
	operation: VideoLocalizationOperation,
	result: VideoLocalizationDevelopmentAsrResult
): AsrOperationPreview {
	return buildDevelopmentPreview(
		operation,
		result.input.source_track_id,
		result.input.audio_sha256,
		result.segments
	);
}

export function buildDevelopmentInitialAnalysisPreview(
	operation: VideoLocalizationOperation,
	result: VideoLocalizationDevelopmentInitialAnalysisResult
): AsrOperationPreview {
	const rawAsr = result.analysis.raw_asr;
	return buildDevelopmentPreview(
		operation,
		rawAsr.input.source_track_id,
		rawAsr.input.audio_sha256,
		result.joined_transcript.segments
	);
}

export function buildDevelopmentEntityNormalizationPreview(
	operation: VideoLocalizationOperation,
	result: VideoLocalizationDevelopmentEntityNormalizationResult
): AsrOperationPreview {
	return buildDevelopmentPreview(
		operation,
		result.input.source_track_id,
		result.input.source_audio_sha256,
		result.updated_segments,
		'text_review'
	);
}

export function buildDevelopmentReviewDecisionsPreview(
	operation: VideoLocalizationOperation,
	result: VideoLocalizationDevelopmentReviewDecisionsResult
): AsrOperationPreview {
	return buildDevelopmentPreview(
		operation,
		result.input.source_track_id,
		result.input.source_audio_sha256,
		result.updated_segments,
		'text_review'
	);
}

export function buildDevelopmentWholeRecheckPreview(
	operation: VideoLocalizationOperation,
	result: VideoLocalizationDevelopmentWholeRecheckResult
): AsrOperationPreview {
	return buildDevelopmentPreview(
		operation,
		result.input.source_track_id,
		result.input.source_audio_sha256,
		result.input.segments,
		'text_review'
	);
}

export function developmentAsrResultMatchesDraft(
	result: VideoLocalizationDevelopmentAsrResult,
	draft: VideoLocalizationDraft
): boolean {
	return developmentSourceMatchesDraft(
		result.input.source_track_id,
		result.input.audio_sha256,
		draft
	);
}

export function developmentInitialAnalysisResultMatchesDraft(
	result: VideoLocalizationDevelopmentInitialAnalysisResult,
	draft: VideoLocalizationDraft
): boolean {
	return developmentSourceMatchesDraft(
		result.analysis.raw_asr.input.source_track_id,
		result.analysis.raw_asr.input.audio_sha256,
		draft
	);
}

export function developmentEntityNormalizationResultMatchesDraft(
	result: VideoLocalizationDevelopmentEntityNormalizationResult,
	draft: VideoLocalizationDraft
): boolean {
	return developmentSourceMatchesDraft(
		result.input.source_track_id,
		result.input.source_audio_sha256,
		draft
	);
}

export function developmentReviewDecisionsResultMatchesDraft(
	result: VideoLocalizationDevelopmentReviewDecisionsResult,
	draft: VideoLocalizationDraft
): boolean {
	return developmentSourceMatchesDraft(
		result.input.source_track_id,
		result.input.source_audio_sha256,
		draft
	);
}

export function developmentWholeRecheckResultMatchesDraft(
	result: VideoLocalizationDevelopmentWholeRecheckResult,
	draft: VideoLocalizationDraft
): boolean {
	return developmentSourceMatchesDraft(
		result.input.source_track_id,
		result.input.source_audio_sha256,
		draft
	);
}

export function developmentAsrPreviewMatchesDraft(
	preview: AsrOperationPreview | null,
	draft: VideoLocalizationDraft | null
): boolean {
	if (!preview || !draft) return false;
	if (!preview.sourceTrackId || !preview.sourceAudioSha256) return true;
	const currentFingerprint = currentTrackFingerprint(draft, preview.sourceTrackId);
	return !currentFingerprint || currentFingerprint === preview.sourceAudioSha256;
}

export function canDisplayDevelopmentAsrPreview(
	preview: AsrOperationPreview | null,
	draft: VideoLocalizationDraft | null,
	selectedOperationId: string | null,
	loadingOperationId: string
): boolean {
	if (!developmentAsrPreviewMatchesDraft(preview, draft) || !preview) return false;
	if (selectedOperationId === preview.operationId) return true;
	return Boolean(
		selectedOperationId
		&& loadingOperationId
		&& selectedOperationId === loadingOperationId
	);
}

export function resolveAsrPreviewTextAtTime(
	preview: Pick<AsrOperationPreview, 'cues'> | null,
	timeMs: number
): string | null {
	if (!preview || !Number.isFinite(timeMs)) return null;
	return preview.cues.find((cue) => timeMs >= cue.start_ms && timeMs < cue.end_ms)?.text ?? null;
}

function operationTimestamp(operation: VideoLocalizationOperation) {
	const value = operation.completed_at || operation.started_at || operation.created_at;
	const timestamp = value ? new Date(value).getTime() : 0;
	return Number.isFinite(timestamp) ? timestamp : 0;
}

function isDevelopmentAsrOperation(operation: VideoLocalizationOperation) {
	return operation.parameters?.execution_mode === 'stop_after';
}

function hasDevelopmentSubtitleSnapshot(operation: VideoLocalizationOperation) {
	return operation.parameters?.execution_mode === 'stop_after'
		&& (
			operation.parameters?.stop_after_step === 'asr'
			|| operation.parameters?.stop_after_step === 'initial_analysis'
			|| operation.parameters?.stop_after_step === 'normalize_entities'
			|| operation.parameters?.stop_after_step === 'review_decisions_r1'
			|| operation.parameters?.stop_after_step === 'whole_recheck_r1'
		);
}

function resetsExistingSubtitlePreview(operation: VideoLocalizationOperation) {
	if (!isDevelopmentAsrOperation(operation)) return true;
	return operation.parameters?.stop_after_step === 'asr'
		|| operation.parameters?.stop_after_step === 'initial_analysis';
}

function buildDevelopmentPreview(
	operation: VideoLocalizationOperation,
	sourceTrackId: string,
	sourceAudioSha256: string,
	segments: VideoLocalizationDevelopmentAsrResult['segments'],
	phase: AsrOperationPreview['phase'] = 'asr_draft'
): AsrOperationPreview {
	return {
		operationId: operation.operation_id,
		phase,
		phaseLabel: PHASE_LABELS[phase],
		stage: typeof operation.result_summary?.stage === 'string'
			? operation.result_summary.stage
			: '原始听写已完成（开发单步）',
		progress: 1,
		isActive: false,
		sourceTrackId,
		sourceAudioSha256,
		cues: segments.flatMap((segment, index) => {
			const text = String(segment.corrected_text || segment.raw_text || '').trim();
			const startMs = Number(segment.start_ms);
			const endMs = Number(segment.end_ms);
			if (!text || !Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return [];
			return [{
				cue_id: String(segment.segment_id || `preview_${index + 1}`),
				start_ms: Math.max(0, Math.round(startMs)),
				end_ms: Math.max(1, Math.round(endMs)),
				text
			}];
		})
	};
}

function developmentSourceMatchesDraft(
	sourceTrackId: string,
	sourceAudioSha256: string,
	draft: VideoLocalizationDraft
): boolean {
	const currentFingerprint = currentTrackFingerprint(draft, sourceTrackId);
	return !currentFingerprint || currentFingerprint === sourceAudioSha256;
}

function currentTrackFingerprint(draft: VideoLocalizationDraft, sourceTrackId: string): string | null {
	if (sourceTrackId === 'vocals') return draft.stems.vocals_clean_sha256 ?? null;
	if (sourceTrackId === 'background') return draft.stems.background_sha256 ?? null;
	if (sourceTrackId === 'original') {
		return draft.source_media.audio_sha256 ?? draft.stems.original_audio_sha256 ?? null;
	}
	return null;
}
