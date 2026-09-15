import type { VideoLocalizationDraft } from '$lib/api/types';
import { ttsInitializationClientId } from './tts-workflow-marker';

export type TtsSubmissionQueueState = {
	activeSubmissionId: string;
	queuedSubmissionIds: string[];
};

export function withTtsSubmissionQueueLabels(
	draft: VideoLocalizationDraft,
	state: TtsSubmissionQueueState
): VideoLocalizationDraft {
	const queuedIds = new Set(state.queuedSubmissionIds);
	return {
		...draft,
		timeline_clips: draft.timeline_clips.map((clip) => {
			const marker = clip.optimistic_tts_workflow_id;
			const clientId = typeof marker === 'string' ? ttsInitializationClientId(marker) : null;
			if (!clientId) return clip;
			if (clientId === state.activeSubmissionId) return { ...clip, status_label: '准备提交' };
			return queuedIds.has(clientId) ? { ...clip, status_label: '排队中' } : clip;
		})
	};
}

type TtsSubmissionQueueEntry = {
	submissionId: string;
	generation: number;
	run: () => Promise<void>;
};

type TtsSubmissionQueueOptions = {
	onStateChange?: (state: TtsSubmissionQueueState) => void;
	onError?: (submissionId: string, error: unknown) => void;
};

/**
 * Serializes only the durable handoff + queue-submit boundary. Generation
 * itself remains in the backend queue, so several user clicks become durable
 * jobs quickly without racing project saves or overwriting one another.
 */
export class TtsSubmissionQueue {
	private activeSubmissionId = '';
	private queuedEntries: TtsSubmissionQueueEntry[] = [];
	private drainPromise: Promise<void> | null = null;
	private generation = 0;

	constructor(private readonly options: TtsSubmissionQueueOptions = {}) {}

	get state(): TtsSubmissionQueueState {
		return {
			activeSubmissionId: this.activeSubmissionId,
			queuedSubmissionIds: this.queuedEntries
				.filter((entry) => entry.generation === this.generation)
				.map((entry) => entry.submissionId)
		};
	}

	has(submissionId: string) {
		return (
			this.activeSubmissionId === submissionId
			|| this.queuedEntries.some((entry) => (
				entry.generation === this.generation
				&& entry.submissionId === submissionId
			))
		);
	}

	enqueue(submissionId: string, run: () => Promise<void>) {
		const normalizedSubmissionId = submissionId.trim();
		if (!normalizedSubmissionId || this.has(normalizedSubmissionId)) return false;
		this.queuedEntries.push({
			submissionId: normalizedSubmissionId,
			generation: this.generation,
			run
		});
		this.publish();
		this.startDrain();
		return true;
	}

	reset() {
		this.generation += 1;
		this.queuedEntries = this.queuedEntries.filter((entry) => entry.generation === this.generation);
		this.publish();
	}

	async whenIdle() {
		while (this.drainPromise) await this.drainPromise;
	}

	private startDrain() {
		if (this.drainPromise) return;
		const drainPromise = this.drain();
		this.drainPromise = drainPromise;
		void drainPromise.finally(() => {
			if (this.drainPromise === drainPromise) this.drainPromise = null;
			if (this.queuedEntries.length) this.startDrain();
		});
	}

	private async drain() {
		while (this.queuedEntries.length) {
			const entry = this.queuedEntries.shift();
			if (!entry || entry.generation !== this.generation) continue;
			this.activeSubmissionId = entry.submissionId;
			this.publish();
			try {
				await entry.run();
			} catch (error) {
				this.options.onError?.(entry.submissionId, error);
			} finally {
				this.activeSubmissionId = '';
				this.publish();
			}
		}
	}

	private publish() {
		this.options.onStateChange?.(this.state);
	}
}
