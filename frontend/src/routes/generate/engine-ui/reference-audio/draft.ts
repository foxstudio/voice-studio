export const REFERENCE_AUDIO_DRAFT_SNAPSHOT_VERSION = 1 as const;
export const MIN_REFERENCE_AUDIO_TRIM_MS = 100;

export type ReferenceAudioSourceKind = 'upload' | 'voice_library' | 'preset' | 'history';

export interface ReferenceAudioFileDraft {
	fileId: string;
	fileName: string;
	path: string;
	previewUrl: string;
	durationMs: number | null;
	mimeType: string;
	sizeBytes: number | null;
}

export interface ReferenceAudioTrimRange {
	startMs: number | null;
	endMs: number | null;
}

export interface ReferenceAudioTranscriptDraft {
	text: string;
	srt: string;
	segmentCount: number;
	transcriptionId: string;
}

/**
 * Serializable state for one reference-audio editor instance.
 *
 * Browser-only resources such as File, AudioBuffer, WaveSurfer and object URL
 * ownership intentionally stay in the component runtime. previewUrl is only a
 * display hint and must not be persisted as a stable asset identifier.
 */
export interface ReferenceAudioDraft {
	draftId: string;
	sourceKind: ReferenceAudioSourceKind;
	source: ReferenceAudioFileDraft;
	clip: ReferenceAudioFileDraft;
	trim: ReferenceAudioTrimRange;
	transcript: ReferenceAudioTranscriptDraft;
	confirmed: boolean;
	selectionDirty: boolean;
	busy: boolean;
	error: string;
	qualityWarnings: string[];
}

export interface ReferenceAudioDraftSnapshot {
	version: typeof REFERENCE_AUDIO_DRAFT_SNAPSHOT_VERSION;
	draft: ReferenceAudioDraft;
}

export type ReferenceAudioDraftOverrides = Partial<
	Omit<ReferenceAudioDraft, 'draftId' | 'source' | 'clip' | 'trim' | 'transcript' | 'qualityWarnings'>
> & {
	source?: Partial<ReferenceAudioFileDraft>;
	clip?: Partial<ReferenceAudioFileDraft>;
	trim?: Partial<ReferenceAudioTrimRange>;
	transcript?: Partial<ReferenceAudioTranscriptDraft>;
	qualityWarnings?: string[];
};

/**
 * Structural compatibility contract for today's GenerateStoreState fields.
 * Keeping this adapter here avoids importing the global store into the model,
 * and gives the future ReferenceAudioEditor a safe incremental migration path.
 */
export interface LegacyCustomVoiceState {
	customVoiceFileName: string;
	customVoiceFileId: string;
	customVoicePreviewUrl: string;
	customVoiceReferenceAudioPath: string;
	customVoiceSourceFileId: string;
	customVoiceSourceAudioPath: string;
	customVoiceSourceDurationMs: number | null;
	customVoiceTrimStartMs: number | null;
	customVoiceTrimEndMs: number | null;
	customVoiceTranscript: string;
	customVoiceSrt: string;
	customVoiceDurationMs: number | null;
	customVoiceSrtSegmentCount: number;
	customVoiceTranscriptionId: string;
	customVoiceConfirmed: boolean;
	customVoiceBusy: boolean;
	customVoiceError: string;
	customVoiceQualityWarnings: string[];
}

function emptyFile(): ReferenceAudioFileDraft {
	return {
		fileId: '',
		fileName: '',
		path: '',
		previewUrl: '',
		durationMs: null,
		mimeType: '',
		sizeBytes: null
	};
}

function emptyTranscript(): ReferenceAudioTranscriptDraft {
	return { text: '', srt: '', segmentCount: 0, transcriptionId: '' };
}

function finiteNonNegative(value: number | null | undefined): number | null {
	if (value === null || value === undefined || !Number.isFinite(value)) return null;
	return Math.max(0, Math.round(value));
}

export function normalizeReferenceAudioTrim(
	durationMs: number | null,
	startMs: number | null,
	endMs: number | null,
	minimumMs = MIN_REFERENCE_AUDIO_TRIM_MS
): ReferenceAudioTrimRange {
	const duration = finiteNonNegative(durationMs);
	const minimum = Math.max(1, Math.round(Number.isFinite(minimumMs) ? minimumMs : MIN_REFERENCE_AUDIO_TRIM_MS));
	if (duration === null) {
		const start = finiteNonNegative(startMs);
		const end = finiteNonNegative(endMs);
		if (start === null || end === null) return { startMs: start, endMs: end };
		return { startMs: start, endMs: Math.max(start + minimum, end) };
	}
	if (duration === 0) return { startMs: null, endMs: null };

	const effectiveMinimum = Math.min(duration, minimum);
	const requestedStart = finiteNonNegative(startMs) ?? 0;
	const requestedEnd = finiteNonNegative(endMs) ?? duration;
	const start = Math.min(requestedStart, Math.max(0, duration - effectiveMinimum));
	const end = Math.max(start + effectiveMinimum, Math.min(duration, requestedEnd));
	return { startMs: start, endMs: Math.min(duration, end) };
}

export function createReferenceAudioDraft(
	draftId: string,
	overrides: ReferenceAudioDraftOverrides = {}
): ReferenceAudioDraft {
	if (!draftId.trim()) throw new Error('ReferenceAudioDraft draftId 不能为空');
	const source = { ...emptyFile(), ...overrides.source };
	const clip = { ...emptyFile(), ...overrides.clip };
	const transcript = { ...emptyTranscript(), ...overrides.transcript };
	const trim = normalizeReferenceAudioTrim(
		source.durationMs,
		overrides.trim?.startMs ?? null,
		overrides.trim?.endMs ?? null
	);
	return {
		draftId,
		sourceKind: overrides.sourceKind ?? 'upload',
		source,
		clip,
		trim,
		transcript,
		confirmed: overrides.confirmed ?? false,
		selectionDirty: overrides.selectionDirty ?? false,
		busy: overrides.busy ?? false,
		error: overrides.error ?? '',
		qualityWarnings: [...(overrides.qualityWarnings ?? [])]
	};
}

export function cloneReferenceAudioDraft(draft: ReferenceAudioDraft): ReferenceAudioDraft {
	return createReferenceAudioDraft(draft.draftId, {
		...draft,
		source: { ...draft.source },
		clip: { ...draft.clip },
		trim: { ...draft.trim },
		transcript: { ...draft.transcript },
		qualityWarnings: [...draft.qualityWarnings]
	});
}

/**
 * Applies a new non-destructive selection. Any processed clip/transcription is
 * invalidated, while the original source remains available for reprocessing.
 */
export function withReferenceAudioTrim(
	draft: ReferenceAudioDraft,
	startMs: number | null,
	endMs: number | null
): ReferenceAudioDraft {
	const trim = normalizeReferenceAudioTrim(draft.source.durationMs, startMs, endMs);
	const changed = trim.startMs !== draft.trim.startMs || trim.endMs !== draft.trim.endMs;
	if (!changed) return cloneReferenceAudioDraft(draft);
	const hadProcessedResult = Boolean(draft.clip.path || draft.transcript.transcriptionId || draft.transcript.text);
	return createReferenceAudioDraft(draft.draftId, {
		...draft,
		source: { ...draft.source },
		clip: emptyFile(),
		trim,
		transcript: emptyTranscript(),
		confirmed: false,
		selectionDirty: draft.selectionDirty || hadProcessedResult,
		qualityWarnings: []
	});
}

export function snapshotReferenceAudioDraft(draft: ReferenceAudioDraft): ReferenceAudioDraftSnapshot {
	return {
		version: REFERENCE_AUDIO_DRAFT_SNAPSHOT_VERSION,
		draft: cloneReferenceAudioDraft(draft)
	};
}

export function restoreReferenceAudioDraft(snapshot: ReferenceAudioDraftSnapshot): ReferenceAudioDraft {
	if (snapshot.version !== REFERENCE_AUDIO_DRAFT_SNAPSHOT_VERSION) {
		throw new Error(`不支持的 ReferenceAudioDraft 快照版本：${String(snapshot.version)}`);
	}
	return cloneReferenceAudioDraft(snapshot.draft);
}

export function referenceAudioDraftFromLegacyState(
	state: LegacyCustomVoiceState,
	draftId = 'legacy-custom-voice'
): ReferenceAudioDraft {
	const sourceDurationMs = finiteNonNegative(state.customVoiceSourceDurationMs);
	return createReferenceAudioDraft(draftId, {
		sourceKind: 'history',
		source: {
			...emptyFile(),
			fileId: state.customVoiceSourceFileId,
			fileName: state.customVoiceFileName,
			path: state.customVoiceSourceAudioPath,
			previewUrl: state.customVoicePreviewUrl,
			durationMs: sourceDurationMs
		},
		clip: {
			...emptyFile(),
			fileId: state.customVoiceFileId,
			fileName: state.customVoiceFileName,
			path: state.customVoiceReferenceAudioPath,
			previewUrl: state.customVoicePreviewUrl,
			durationMs: finiteNonNegative(state.customVoiceDurationMs)
		},
		trim: {
			startMs: state.customVoiceTrimStartMs,
			endMs: state.customVoiceTrimEndMs
		},
		transcript: {
			text: state.customVoiceTranscript,
			srt: state.customVoiceSrt,
			segmentCount: Math.max(0, Math.round(state.customVoiceSrtSegmentCount)),
			transcriptionId: state.customVoiceTranscriptionId
		},
		confirmed: state.customVoiceConfirmed,
		selectionDirty: false,
		busy: state.customVoiceBusy,
		error: state.customVoiceError,
		qualityWarnings: state.customVoiceQualityWarnings
	});
}

export function legacyCustomVoicePatchFromDraft(draft: ReferenceAudioDraft): LegacyCustomVoiceState {
	return {
		customVoiceFileName: draft.source.fileName || draft.clip.fileName,
		customVoiceFileId: draft.clip.fileId,
		customVoicePreviewUrl: draft.clip.previewUrl || draft.source.previewUrl,
		customVoiceReferenceAudioPath: draft.clip.path,
		customVoiceSourceFileId: draft.source.fileId,
		customVoiceSourceAudioPath: draft.source.path,
		customVoiceSourceDurationMs: draft.source.durationMs,
		customVoiceTrimStartMs: draft.trim.startMs,
		customVoiceTrimEndMs: draft.trim.endMs,
		customVoiceTranscript: draft.transcript.text,
		customVoiceSrt: draft.transcript.srt,
		customVoiceDurationMs: draft.clip.durationMs,
		customVoiceSrtSegmentCount: draft.transcript.segmentCount,
		customVoiceTranscriptionId: draft.transcript.transcriptionId,
		customVoiceConfirmed: draft.confirmed,
		customVoiceBusy: draft.busy,
		customVoiceError: draft.error,
		customVoiceQualityWarnings: [...draft.qualityWarnings]
	};
}
