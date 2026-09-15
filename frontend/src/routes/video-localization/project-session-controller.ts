import { mergeVideoLocalizationUiState } from './ui-state-merge';

export type ProjectAutosaveScope = 'ui' | 'draft';
export type ProjectAutosaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'failed';

export type ProjectAutosaveRequest = {
	projectId: string;
	scope: ProjectAutosaveScope;
	uiPatch: Record<string, unknown>;
};

type ProjectSessionControllerOptions = {
	debounceMs?: number;
	maxWaitMs?: number;
	getProjectId: () => string;
	canSave: () => boolean;
	hasExternalPending: () => boolean;
	getExternalRevision?: () => number;
	save: (request: ProjectAutosaveRequest) => Promise<void>;
	onStatusChange?: (status: ProjectAutosaveStatus) => void;
	onSaved?: () => void;
	onError?: (error: unknown) => void;
};

export class ProjectSessionController {
	private readonly debounceMs: number;
	private readonly maxWaitMs: number;
	private readonly options: ProjectSessionControllerOptions;
	private timer: ReturnType<typeof setTimeout> | null = null;
	private maxWaitTimer: ReturnType<typeof setTimeout> | null = null;
	private inFlight: Promise<void> | null = null;
	private inFlightScope: ProjectAutosaveScope | null = null;
	private inFlightUiPatch: Record<string, unknown> = {};
	private queued = false;
	private scope: ProjectAutosaveScope | null = null;
	private pendingUiPatch: Record<string, unknown> = {};
	private currentStatus: ProjectAutosaveStatus = 'idle';
	private pendingSince: number | null = null;
	private lastActivityAt: number | null = null;

	constructor(options: ProjectSessionControllerOptions) {
		this.options = options;
		this.debounceMs = options.debounceMs ?? 8_000;
		this.maxWaitMs = Math.max(
			this.debounceMs,
			options.maxWaitMs ?? 60_000
		);
	}

	get status() {
		return this.currentStatus;
	}

	queueUiPatch(patch: Record<string, unknown>) {
		if (!this.canSchedule()) return;
		this.pendingUiPatch = mergeVideoLocalizationUiState(this.pendingUiPatch, patch);
		this.schedule('ui');
	}

	/** Remote reads and save receipts cannot acknowledge a later local click. */
	mergePendingUiState(state: Record<string, unknown>) {
		return mergeVideoLocalizationUiState(
			mergeVideoLocalizationUiState(state, this.inFlightUiPatch),
			this.pendingUiPatch
		);
	}

	schedule(scope: ProjectAutosaveScope = 'draft') {
		if (!this.canSchedule()) return;
		this.scope = this.scope === 'draft' || scope === 'draft' ? 'draft' : 'ui';
		const now = Date.now();
		this.pendingSince ??= now;
		this.lastActivityAt = now;
		// UI preferences are still persisted, but they are not unsaved project
		// content and must not raise the dirty warning on selection alone.
		if (this.scope === 'draft') this.setStatus('dirty');
		this.scheduleRun();
	}

	hasPending() {
		return Boolean(
			this.timer
			|| this.maxWaitTimer
			|| this.inFlight
			|| this.queued
			|| this.scope
			|| Object.keys(this.pendingUiPatch).length
			|| this.options.hasExternalPending()
		);
	}

	hasPendingDraft() {
		return Boolean(
			this.scope === 'draft'
			|| this.inFlightScope === 'draft'
			|| this.options.hasExternalPending()
		);
	}

	cancelScheduledRun() {
		if (this.timer !== null) clearTimeout(this.timer);
		if (this.maxWaitTimer !== null) clearTimeout(this.maxWaitTimer);
		this.timer = null;
		this.maxWaitTimer = null;
	}

	discardPending(resetStatus = true) {
		this.cancelScheduledRun();
		this.queued = false;
		this.scope = null;
		this.pendingUiPatch = {};
		this.inFlightUiPatch = {};
		this.pendingSince = null;
		this.lastActivityAt = null;
		if (resetStatus) this.setStatus('idle');
	}

	async waitForCurrentSave() {
		if (this.inFlight) await this.inFlight;
	}

	run(): Promise<void> {
		this.cancelScheduledRun();
		if (this.inFlight) {
			this.queued = true;
			return this.inFlight;
		}
		if (!this.canSchedule()) return Promise.resolve();
		const projectId = this.options.getProjectId();
		this.queued = false;
		this.inFlight = this.perform(projectId).finally(() => {
			this.inFlight = null;
		});
		return this.inFlight;
	}

	async flush() {
		while (true) {
			this.cancelScheduledRun();
			const externalPendingBefore = this.options.hasExternalPending();
			if (this.inFlight) await this.inFlight;
			else if (
				this.currentStatus === 'dirty'
				|| this.queued
				|| this.scope !== null
				|| Object.keys(this.pendingUiPatch).length > 0
				|| externalPendingBefore
			) {
				if (!this.canSchedule()) return false;
				await this.run();
			}
			else break;
			if (this.currentStatus === 'failed' && !this.inFlight && !this.queued) return false;
			if (
				externalPendingBefore
				&& this.options.hasExternalPending()
				&& !this.hasOwnedPending()
				&& !this.inFlight
			) return false;
		}
		return this.currentStatus !== 'failed';
	}

	private canSchedule() {
		return Boolean(this.options.getProjectId() && this.options.canSave());
	}

	private hasOwnedPending() {
		return Boolean(
			this.queued
			|| this.scope !== null
			|| Object.keys(this.pendingUiPatch).length > 0
		);
	}

	private scheduleRun() {
		this.cancelScheduledRun();
		const now = Date.now();
		this.pendingSince ??= now;
		this.lastActivityAt ??= now;
		const idleDelay = Math.max(
			0,
			this.debounceMs - (now - this.lastActivityAt)
		);
		const maxDelay = Math.max(
			0,
			this.maxWaitMs - (now - this.pendingSince)
		);
		this.timer = setTimeout(() => {
			this.timer = null;
			void this.run();
		}, idleDelay);
		this.maxWaitTimer = setTimeout(() => {
			this.maxWaitTimer = null;
			void this.run();
		}, maxDelay);
	}

	private setStatus(status: ProjectAutosaveStatus) {
		this.currentStatus = status;
		this.options.onStatusChange?.(status);
	}

	private async perform(projectId: string) {
		const externalRevision = this.options.getExternalRevision?.() ?? 0;
		const request: ProjectAutosaveRequest = {
			projectId,
			scope: this.scope ?? 'draft',
			uiPatch: this.pendingUiPatch
		};
		this.inFlightScope = request.scope;
		this.inFlightUiPatch = request.uiPatch;
		this.scope = null;
		this.pendingUiPatch = {};
		this.pendingSince = null;
		this.lastActivityAt = null;
		this.setStatus('saving');
		let succeeded = false;
		try {
			await this.options.save(request);
			succeeded = true;
			this.setStatus('saved');
			this.options.onSaved?.();
		} catch (error) {
			this.cancelScheduledRun();
			this.queued = false;
			this.scope = this.scope === 'draft' || request.scope === 'draft' ? 'draft' : 'ui';
			this.pendingUiPatch = mergeVideoLocalizationUiState(
				request.uiPatch,
				this.pendingUiPatch
			);
			const now = Date.now();
			this.pendingSince ??= now;
			this.lastActivityAt ??= now;
			this.setStatus('failed');
			this.options.onError?.(error);
		} finally {
			this.inFlightScope = null;
			this.inFlightUiPatch = {};
			const hasOwnedPending = this.hasOwnedPending();
			const hasExternalPending = this.options.hasExternalPending();
			const externalChanged = (
				(this.options.getExternalRevision?.() ?? 0) !== externalRevision
			);
			if (succeeded && (hasOwnedPending || hasExternalPending)) {
				this.setStatus('dirty');
				if ((hasOwnedPending || externalChanged) && this.canSchedule()) {
					this.queued = false;
					this.scheduleRun();
				}
			}
		}
	}
}
