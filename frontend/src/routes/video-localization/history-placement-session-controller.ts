export type HistoryPlacementContext = Readonly<{
	projectId: string;
	requestId: string;
	epoch: number;
}>;

type HistoryPlacementOperation<Result> = {
	currentSave: Promise<void>;
	request: () => Promise<Result | null>;
	applyReceipt: (result: Result) => boolean;
	refresh: () => Promise<void>;
	onApplied: () => void;
	onSettled: () => void;
};

/** Owns in-flight placement commands; their runtime overlays are not editable clips. */
export class HistoryPlacementSessionController {
	private readonly pending = new Map<string, Promise<unknown>>();
	private epoch = 0;

	constructor(private readonly getProjectId: () => string) {}

	capture(requestId: string): HistoryPlacementContext {
		return Object.freeze({ projectId: this.getProjectId(), requestId, epoch: this.epoch });
	}

	isCurrent(context: HistoryPlacementContext) {
		return Boolean(context.projectId && context.projectId === this.getProjectId() && context.epoch === this.epoch);
	}

	reset() {
		this.epoch += 1;
		this.pending.clear();
	}

	get hasPending() {
		return this.pending.size > 0;
	}

	execute<T>(context: HistoryPlacementContext, operation: HistoryPlacementOperation<T>): Promise<T | null> {
		let tracked: Promise<T | null>;
		tracked = Promise.resolve().then(async () => {
			try {
				if (!this.isCurrent(context)) return null;
				await operation.currentSave;
				if (!this.isCurrent(context)) return null;
				const result = await operation.request();
				if (!this.isCurrent(context)) return null;
				// A discarded receipt may already be committed. Read, never resubmit.
				if (result === null || !operation.applyReceipt(result)) await operation.refresh();
				if (!this.isCurrent(context)) return null;
				operation.onApplied();
				return result;
			} finally {
				if (this.isCurrent(context)) operation.onSettled();
			}
		}).finally(() => {
			if (this.pending.get(context.requestId) === tracked) this.pending.delete(context.requestId);
		});
		this.pending.set(context.requestId, tracked);
		return tracked;
	}

	async waitForPending() {
		while (this.pending.size) {
			await Promise.allSettled([...this.pending.values()]);
		}
	}
}
