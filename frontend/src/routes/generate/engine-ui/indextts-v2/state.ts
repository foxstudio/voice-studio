import { createReferenceAudioDraft, type ReferenceAudioDraft } from '../reference-audio/draft';

export const INDEX_TTS_ENGINE_ID = 'indextts-v2';
export type EmotionReferenceSource = 'voice_library' | 'upload';

export interface EmotionReferenceSlot {
	voiceId: string;
	audioId: string;
	displayName: string;
	draft: ReferenceAudioDraft | null;
}

export interface IndexTtsEmotionState {
	enabled: boolean;
	source: EmotionReferenceSource;
	alpha: number;
	library: EmotionReferenceSlot;
	upload: EmotionReferenceSlot;
}

function emptySlot(): EmotionReferenceSlot {
	return { voiceId: '', audioId: '', displayName: '', draft: null };
}

export function createDefaultIndexTtsEmotionState(alpha = 0.6): IndexTtsEmotionState {
	return {
		enabled: false,
		source: 'voice_library',
		alpha,
		library: emptySlot(),
		upload: emptySlot()
	};
}

export function cloneIndexTtsEmotionState(state: IndexTtsEmotionState): IndexTtsEmotionState {
	return {
		...state,
		library: { ...state.library, draft: state.library.draft ? createReferenceAudioDraft(state.library.draft.draftId, state.library.draft) : null },
		upload: { ...state.upload, draft: state.upload.draft ? createReferenceAudioDraft(state.upload.draft.draftId, state.upload.draft) : null }
	};
}

export function activeEmotionReferenceSlot(state: IndexTtsEmotionState): EmotionReferenceSlot {
	return state.source === 'voice_library' ? state.library : state.upload;
}

export function setEmotionReferenceSource(state: IndexTtsEmotionState, source: EmotionReferenceSource): IndexTtsEmotionState {
	return { ...cloneIndexTtsEmotionState(state), source };
}

export function setEmotionReferenceSlot(
	state: IndexTtsEmotionState,
	source: EmotionReferenceSource,
	slot: EmotionReferenceSlot
): IndexTtsEmotionState {
	return { ...cloneIndexTtsEmotionState(state), [source === 'voice_library' ? 'library' : 'upload']: slot };
}

export function emotionReferenceReady(state: IndexTtsEmotionState): boolean {
	const slot = activeEmotionReferenceSlot(state);
	return Boolean(slot.draft?.clip.path && slot.draft.confirmed && !slot.draft.selectionDirty);
}
