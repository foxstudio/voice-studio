export type ProjectDeleteQueueState = {
	activeProjectId: string;
	queuedProjectIds: string[];
};

type ProjectDeleteQueueEntry = {
	projectId: string;
	run: () => Promise<void>;
};

type ProjectDeleteQueueOptions = {
	onStateChange?: (state: ProjectDeleteQueueState) => void;
	onError?: (projectId: string, error: unknown) => void;
};

export class ProjectDeleteQueue {
	private activeProjectId = '';
	private queuedEntries: ProjectDeleteQueueEntry[] = [];
	private drainPromise: Promise<void> | null = null;

	constructor(private readonly options: ProjectDeleteQueueOptions = {}) {}

	get state(): ProjectDeleteQueueState {
		return {
			activeProjectId: this.activeProjectId,
			queuedProjectIds: this.queuedEntries.map((entry) => entry.projectId)
		};
	}

	has(projectId: string) {
		return (
			this.activeProjectId === projectId
			|| this.queuedEntries.some((entry) => entry.projectId === projectId)
		);
	}

	enqueue(projectId: string, run: () => Promise<void>) {
		const normalizedProjectId = projectId.trim();
		if (!normalizedProjectId || this.has(normalizedProjectId)) return false;
		this.queuedEntries.push({
			projectId: normalizedProjectId,
			run
		});
		this.publish();
		this.startDrain();
		return true;
	}

	async whenIdle() {
		while (this.drainPromise) await this.drainPromise;
	}

	private startDrain() {
		if (this.drainPromise) return;
		const drainPromise = this.drain();
		this.drainPromise = drainPromise;
		void drainPromise.finally(() => {
			if (this.drainPromise === drainPromise) {
				this.drainPromise = null;
			}
			if (this.queuedEntries.length) this.startDrain();
		});
	}

	private async drain() {
		while (this.queuedEntries.length) {
			const entry = this.queuedEntries.shift();
			if (!entry) break;
			this.activeProjectId = entry.projectId;
			this.publish();
			try {
				await entry.run();
			} catch (error) {
				this.options.onError?.(entry.projectId, error);
			} finally {
				this.activeProjectId = '';
				this.publish();
			}
		}
	}

	private publish() {
		this.options.onStateChange?.(this.state);
	}
}
