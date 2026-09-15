export type PreviewVideoSurfaceResumeInput = {
	visible: boolean;
	hasVideo: boolean;
	paused: boolean;
	ended: boolean;
	currentTimeSeconds: number;
};

export type PreviewVideoSurfaceResumePlan = {
	restoreTimeSeconds: number;
	resumePlayback: boolean;
};

export function previewVideoSurfaceKey(sourceUrl: string, elementRevision: number) {
	return `${sourceUrl}:surface:${elementRevision}`;
}

export class PreviewVideoSurfaceResumeController {
	private suspended = false;

	markSuspended() {
		this.suspended = true;
	}

	reset() {
		this.suspended = false;
	}

	consumeResume(input: PreviewVideoSurfaceResumeInput): PreviewVideoSurfaceResumePlan | null {
		if (!this.suspended || !input.visible) return null;
		if (!input.hasVideo) return null;
		this.suspended = false;
		if (input.ended) return null;
		return {
			restoreTimeSeconds: Math.max(0, Number.isFinite(input.currentTimeSeconds) ? input.currentTimeSeconds : 0),
			resumePlayback: !input.paused
		};
	}
}
