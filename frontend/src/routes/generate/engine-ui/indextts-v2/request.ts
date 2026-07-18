import type { GenerateRequest } from '$lib/api/types';
import { createReferenceAudioDraft } from '../reference-audio/draft';
import {
	activeEmotionReferenceSlot,
	createDefaultIndexTtsEmotionState,
	type IndexTtsEmotionState
} from './state';

export type IndexTtsEmotionRequest = Pick<
	GenerateRequest,
	| 'emotion_mode'
	| 'emo_alpha'
	| 'emotion_reference_voice_id'
	| 'emotion_reference_audio_path'
	| 'emotion_reference_source_audio_path'
	| 'emotion_reference_source_duration_ms'
	| 'emotion_reference_trim_start_ms'
	| 'emotion_reference_trim_end_ms'
>;

function managedFileId(path: string): string {
	const filename = path.split('/').pop() ?? '';
	return filename.replace(/\.[^.]+$/, '');
}

function managedPreviewUrl(fileId: string): string {
	return fileId ? `/api/voices/files/${encodeURIComponent(fileId)}/audio` : '';
}

export function indexTtsEmotionStateToRequest(
	state: IndexTtsEmotionState,
	fallbackMode: GenerateRequest['emotion_mode'],
	fallbackAlpha: number
): IndexTtsEmotionRequest {
	if (!state.enabled) {
		return {
			emotion_mode: fallbackMode,
			emo_alpha: fallbackAlpha
		};
	}
	const slot = activeEmotionReferenceSlot(state);
	const draft = slot.draft;
	return {
		emotion_mode: 'emotion_reference',
		emo_alpha: state.alpha,
		emotion_reference_voice_id: state.source === 'voice_library' ? slot.voiceId || null : null,
		emotion_reference_audio_path: draft?.clip.path || null,
		emotion_reference_source_audio_path: draft?.source.path || null,
		emotion_reference_source_duration_ms: draft?.source.durationMs ?? null,
		emotion_reference_trim_start_ms: draft?.trim.startMs ?? null,
		emotion_reference_trim_end_ms: draft?.trim.endMs ?? null
	};
}

export function indexTtsEmotionStateFromRequest(request: Partial<GenerateRequest>): IndexTtsEmotionState {
	const state = createDefaultIndexTtsEmotionState(request.emo_alpha ?? 0.6);
	if (request.emotion_mode !== 'emotion_reference') return state;
	const source = request.emotion_reference_voice_id ? 'voice_library' : 'upload';
	const audioPath = request.emotion_reference_audio_path ?? '';
	const sourcePath = request.emotion_reference_source_audio_path ?? audioPath;
	const sourceFileId = managedFileId(sourcePath);
	const clipFileId = managedFileId(audioPath);
	const draft = createReferenceAudioDraft(`emotion-history-${request.emotion_reference_voice_id || audioPath || 'missing'}`, {
		sourceKind: 'history',
		source: {
			fileId: sourceFileId,
			path: sourcePath,
			fileName: sourcePath.split('/').pop() ?? '',
			previewUrl: managedPreviewUrl(sourceFileId),
			durationMs: request.emotion_reference_source_duration_ms ?? null
		},
		clip: {
			fileId: clipFileId,
			path: audioPath,
			fileName: audioPath.split('/').pop() ?? '',
			previewUrl: managedPreviewUrl(clipFileId),
			durationMs:
				request.emotion_reference_trim_start_ms != null && request.emotion_reference_trim_end_ms != null
					? Math.max(0, request.emotion_reference_trim_end_ms - request.emotion_reference_trim_start_ms)
					: null
		},
		trim: {
			startMs: request.emotion_reference_trim_start_ms ?? 0,
			endMs: request.emotion_reference_trim_end_ms ?? request.emotion_reference_source_duration_ms ?? null
		},
		confirmed: Boolean(audioPath)
	});
	return {
		...state,
		enabled: true,
		source,
		[source === 'voice_library' ? 'library' : 'upload']: {
			voiceId: request.emotion_reference_voice_id ?? '',
			audioId: sourceFileId || clipFileId,
			displayName: request.emotion_reference_voice_id ? '历史情绪音色' : draft.source.fileName || '历史情绪片段',
			draft
		}
	};
}
