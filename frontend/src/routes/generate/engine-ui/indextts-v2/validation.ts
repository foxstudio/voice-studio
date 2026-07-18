import { activeEmotionReferenceSlot, type IndexTtsEmotionState } from './state';

export interface EmotionReferenceValidation {
	valid: boolean;
	errors: string[];
	warnings: string[];
}

export function validateIndexTtsEmotionState(state: IndexTtsEmotionState): EmotionReferenceValidation {
	if (!state.enabled) return { valid: true, errors: [], warnings: [] };
	const slot = activeEmotionReferenceSlot(state);
	const errors: string[] = [];
	const warnings: string[] = [];
	if (!slot.draft) errors.push(state.source === 'voice_library' ? '请选择一个带本地样音的情绪音色。' : '请上传一段情绪参考音频。');
	else {
		const { startMs, endMs } = slot.draft.trim;
		const selectedMs = startMs == null || endMs == null ? 0 : endMs - startMs;
		if (selectedMs < 100) errors.push('情绪参考片段不能短于 0.1 秒。');
		if (slot.draft.selectionDirty || !slot.draft.confirmed || !slot.draft.clip.path) errors.push('片段已调整，请先点击“使用这个片段”。');
		if (selectedMs > 15_000) warnings.push('建议选择情绪最明确的一小段，通常 3–15 秒更容易保持稳定。');
	}
	if (state.alpha < 0 || state.alpha > 1) errors.push('情绪参考强度必须在 0 到 1 之间。');
	return { valid: errors.length === 0, errors, warnings };
}
