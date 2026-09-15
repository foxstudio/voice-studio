export type SubmissionEngineKind = 'local' | 'cloud';

export type SubmissionPreviewDraft = {
	inputText: string;
	engineId: string;
	engineName: string;
	engineKind: SubmissionEngineKind;
	voiceLabel: string;
};

export type SubmissionPreview = Readonly<SubmissionPreviewDraft & {
	submissionId: string;
	taskId: string | null;
	createdAt: string;
	stage: 'initializing';
}>;

export function createSubmissionPreview(
	draft: SubmissionPreviewDraft,
	meta: { submissionId: string; createdAt: string }
): SubmissionPreview {
	return Object.freeze({
		submissionId: meta.submissionId,
		taskId: null,
		inputText: String(draft.inputText),
		engineId: String(draft.engineId),
		engineName: String(draft.engineName),
		engineKind: draft.engineKind,
		voiceLabel: String(draft.voiceLabel),
		createdAt: meta.createdAt,
		stage: 'initializing' as const
	});
}

export function attachSubmissionTask(
	preview: SubmissionPreview | null,
	submissionId: string,
	taskId: string
): SubmissionPreview | null {
	if (!preview || preview.submissionId !== submissionId) return preview;
	return Object.freeze({ ...preview, taskId });
}

export function completeSubmissionTask(
	preview: SubmissionPreview | null,
	taskId: string
): SubmissionPreview | null {
	if (!preview || preview.taskId !== taskId) return preview;
	return null;
}

export function clearSubmissionPreview(
	preview: SubmissionPreview | null,
	submissionId: string
): SubmissionPreview | null {
	if (!preview || preview.submissionId !== submissionId) return preview;
	return null;
}
