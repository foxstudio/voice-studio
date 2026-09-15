import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	ProjectSessionController,
	type ProjectAutosaveRequest,
	type ProjectAutosaveStatus
} from './project-session-controller';

describe('project session autosave controller', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	function setup(save = vi.fn<(request: ProjectAutosaveRequest) => Promise<void>>().mockResolvedValue(undefined)) {
		let projectId = 'project-a';
		let hasDraft = true;
		let externalPending = false;
		let externalPendingRevision = 0;
		const statuses: ProjectAutosaveStatus[] = [];
		const controller = new ProjectSessionController({
			debounceMs: 100,
			maxWaitMs: 300,
			getProjectId: () => projectId,
			canSave: () => hasDraft,
			hasExternalPending: () => externalPending,
			getExternalRevision: () => externalPendingRevision,
			save,
			onStatusChange: (status) => statuses.push(status)
		});
		return {
			controller,
			save,
			statuses,
			setProjectId: (value: string) => (projectId = value),
			setHasDraft: (value: boolean) => (hasDraft = value),
			setExternalPending: (value: boolean) => (externalPending = value),
			advanceExternalPendingRevision: () => (externalPendingRevision += 1)
		};
	}

	it('waits for an idle window instead of saving after every nearby edit', async () => {
		const { controller, save } = setup();

		controller.schedule('draft');
		await vi.advanceTimersByTimeAsync(80);
		controller.schedule('draft');
		await vi.advanceTimersByTimeAsync(80);
		controller.schedule('draft');

		expect(save).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(20);
		expect(save).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(80);

		expect(save).toHaveBeenCalledTimes(1);
	});

	it('saves at the maximum wait while edits continue without becoming idle', async () => {
		const { controller, save } = setup();

		controller.schedule('draft');
		for (let elapsed = 80; elapsed < 300; elapsed += 80) {
			await vi.advanceTimersByTimeAsync(80);
			controller.schedule('draft');
		}
		expect(save).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(60);
		expect(save).toHaveBeenCalledTimes(1);
	});

	it('does not write periodically when nothing changed after a save', async () => {
		const { controller, save } = setup();

		controller.schedule('draft');
		await vi.advanceTimersByTimeAsync(100);
		expect(save).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(5_000);
		expect(save).toHaveBeenCalledTimes(1);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('merges UI patches and saves them through the UI-only path', async () => {
		const { controller, save, statuses } = setup();

		controller.queueUiPatch({ track_states: { dub: { muted: true } } });
		controller.queueUiPatch({ track_states: { dub: { volume: 0.8 } } });
		await vi.advanceTimersByTimeAsync(100);

		expect(save).toHaveBeenCalledTimes(1);
		expect(save).toHaveBeenCalledWith({
			projectId: 'project-a',
			scope: 'ui',
			uiPatch: { track_states: { dub: { muted: true, volume: 0.8 } } }
		});
		expect(statuses).toEqual(['saving', 'saved']);
		expect(controller.hasPending()).toBe(false);
	});

	it('overlays only unsaved UI fields on a refreshed workspace', () => {
		const { controller } = setup();
		controller.queueUiPatch({ track_states: { vocals: { solo: true } } });
		expect(controller.mergePendingUiState({
			track_states: { vocals: { solo: false, muted: true }, background: { volume: 0.4 } },
			sidebar_collapsed: true
		})).toEqual({
			track_states: { vocals: { solo: true, muted: true }, background: { volume: 0.4 } },
			sidebar_collapsed: true
		});
		controller.discardPending();
	});

	it('keeps in-flight UI intent until acknowledged and gives newer clicks precedence', async () => {
		let release!: () => void;
		const { controller } = setup(vi.fn(() => new Promise<void>((resolve) => { release = resolve; })));
		controller.queueUiPatch({ dub_lane_states: { '0': { solo: true } } });
		const saving = controller.run();
		expect(controller.mergePendingUiState({ dub_lane_states: { '0': { solo: false } } }))
			.toEqual({ dub_lane_states: { '0': { solo: true } } });
		controller.queueUiPatch({ dub_lane_states: { '0': { solo: false } } });
		expect(controller.mergePendingUiState({ dub_lane_states: { '0': { solo: true, volume: 0.6 } } }))
			.toEqual({ dub_lane_states: { '0': { solo: false, volume: 0.6 } } });
		release();
		await saving;
		expect(controller.mergePendingUiState({ dub_lane_states: { '0': { solo: true } } }))
			.toEqual({ dub_lane_states: { '0': { solo: false } } });
		controller.discardPending();
	});

	it('releases accepted UI intent so later remote changes remain visible', async () => {
		const { controller } = setup();
		controller.queueUiPatch({ track_states: { vocals: { solo: true } } });
		await controller.run();
		expect(controller.mergePendingUiState({ track_states: { vocals: { solo: false } } }))
			.toEqual({ track_states: { vocals: { solo: false } } });
	});

	it('promotes pending UI work to one full draft save', async () => {
		const { controller, save } = setup();

		controller.queueUiPatch({ sidebar_collapsed: true });
		expect(controller.hasPendingDraft()).toBe(false);
		controller.schedule('draft');
		expect(controller.hasPendingDraft()).toBe(true);
		await vi.advanceTimersByTimeAsync(100);

		expect(save).toHaveBeenCalledTimes(1);
		expect(save).toHaveBeenCalledWith({
			projectId: 'project-a',
			scope: 'draft',
			uiPatch: { sidebar_collapsed: true }
		});
		expect(controller.hasPendingDraft()).toBe(false);
	});

	it('queues edits made while a save is in flight', async () => {
		let releaseFirst!: () => void;
		const save = vi.fn<(request: ProjectAutosaveRequest) => Promise<void>>()
			.mockImplementationOnce(() => new Promise<void>((resolve) => (releaseFirst = resolve)))
			.mockResolvedValueOnce(undefined);
		const { controller } = setup(save);

		controller.schedule('draft');
		await vi.advanceTimersByTimeAsync(100);
		expect(save).toHaveBeenCalledTimes(1);
		expect(controller.hasPendingDraft()).toBe(true);

		controller.queueUiPatch({ sidebar_collapsed: true });
		releaseFirst();
		await Promise.resolve();
		await vi.runAllTimersAsync();

		expect(save).toHaveBeenCalledTimes(2);
		expect(save.mock.calls[1]?.[0]).toEqual({
			projectId: 'project-a',
			scope: 'ui',
			uiPatch: { sidebar_collapsed: true }
		});
	});

	it('continues when external timeline work changes during a save', async () => {
		let releaseFirst!: () => void;
		const save = vi.fn<(request: ProjectAutosaveRequest) => Promise<void>>()
			.mockImplementationOnce(() => new Promise<void>((resolve) => (releaseFirst = resolve)))
			.mockResolvedValueOnce(undefined);
		const {
			controller,
			setExternalPending,
			advanceExternalPendingRevision
		} = setup(save);

		setExternalPending(true);
		controller.schedule('draft');
		await vi.advanceTimersByTimeAsync(100);
		advanceExternalPendingRevision();
		releaseFirst();
		await controller.waitForCurrentSave();
		setExternalPending(false);
		await vi.runAllTimersAsync();

		expect(save).toHaveBeenCalledTimes(2);
	});

	it('flushes immediately instead of waiting for the debounce timer', async () => {
		const { controller, save } = setup();

		controller.schedule('draft');
		expect(await controller.flush()).toBe(true);

		expect(save).toHaveBeenCalledTimes(1);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('flushes a new timeline edit immediately after an in-flight save', async () => {
		let releaseFirst!: () => void;
		const save = vi.fn<(request: ProjectAutosaveRequest) => Promise<void>>()
			.mockImplementationOnce(() => new Promise<void>((resolve) => (releaseFirst = resolve)))
			.mockResolvedValueOnce(undefined);
		const { controller, setExternalPending, advanceExternalPendingRevision } = setup(save);

		controller.schedule('draft');
		const firstSave = controller.run();
		setExternalPending(true);
		advanceExternalPendingRevision();
		controller.schedule('draft');
		const timelineFlush = controller.flush();
		expect(save).toHaveBeenCalledTimes(1);

		releaseFirst();
		await firstSave;
		setExternalPending(false);
		await timelineFlush;

		expect(save).toHaveBeenCalledTimes(2);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('stops a flush when a successful request makes no external save progress', async () => {
		const { controller, save, statuses, setExternalPending } = setup();
		setExternalPending(true);

		controller.schedule('draft');
		expect(await controller.flush()).toBe(false);

		expect(save).toHaveBeenCalledTimes(1);
		expect(statuses.at(-1)).toBe('dirty');
		expect(controller.hasPending()).toBe(true);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('stops a flush when pending work can no longer be saved', async () => {
		const { controller, save, setHasDraft } = setup();

		controller.schedule('draft');
		setHasDraft(false);

		expect(await controller.flush()).toBe(false);
		expect(save).not.toHaveBeenCalled();
		expect(controller.hasPending()).toBe(true);
	});

	it('discards scheduled work and its UI patch', async () => {
		const { controller, save } = setup();

		controller.queueUiPatch({ sidebar_collapsed: true });
		controller.discardPending();
		await vi.runAllTimersAsync();

		expect(save).not.toHaveBeenCalled();
		expect(controller.status).toBe('idle');
		expect(controller.hasPending()).toBe(false);
	});

	it('stops after a failed save and allows an explicit retry', async () => {
		const save = vi.fn<(request: ProjectAutosaveRequest) => Promise<void>>()
			.mockRejectedValueOnce(new Error('offline'))
			.mockResolvedValueOnce(undefined);
		const { controller, statuses, setExternalPending } = setup(save);
		setExternalPending(true);

		controller.schedule('draft');
		expect(await controller.flush()).toBe(false);
		expect(save).toHaveBeenCalledTimes(1);
		expect(vi.getTimerCount()).toBe(0);
		expect(statuses.at(-1)).toBe('failed');
		expect(controller.hasPending()).toBe(true);
		expect(controller.hasPendingDraft()).toBe(true);

		setExternalPending(false);
		expect(await controller.flush()).toBe(true);
		expect(save).toHaveBeenCalledTimes(2);
		expect(statuses.at(-1)).toBe('saved');
	});

	it('retains a failed UI-only request until an explicit flush retries it', async () => {
		const save = vi.fn<(request: ProjectAutosaveRequest) => Promise<void>>()
			.mockRejectedValueOnce(new Error('offline'))
			.mockResolvedValueOnce(undefined);
		const { controller } = setup(save);
		const request = {
			projectId: 'project-a',
			scope: 'ui' as const,
			uiPatch: { sidebar_collapsed: true }
		};

		controller.queueUiPatch(request.uiPatch);
		expect(await controller.flush()).toBe(false);

		expect(save).toHaveBeenCalledTimes(1);
		expect(save).toHaveBeenLastCalledWith(request);
		expect(controller.hasPending()).toBe(true);
		expect(controller.hasPendingDraft()).toBe(false);
		expect(vi.getTimerCount()).toBe(0);

		expect(await controller.flush()).toBe(true);
		expect(save).toHaveBeenCalledTimes(2);
		expect(save).toHaveBeenLastCalledWith(request);
		expect(controller.hasPending()).toBe(false);
	});

	it('merges newer UI edits into a failed in-flight request without auto-retrying', async () => {
		let rejectFirst!: (error: Error) => void;
		const save = vi.fn<(request: ProjectAutosaveRequest) => Promise<void>>()
			.mockImplementationOnce(() => new Promise<void>((_resolve, reject) => (rejectFirst = reject)))
			.mockResolvedValueOnce(undefined);
		const { controller } = setup(save);

		controller.queueUiPatch({ track_states: { dub: { muted: true } } });
		const firstFlush = controller.flush();
		controller.queueUiPatch({ track_states: { dub: { volume: 0.8 } } });
		rejectFirst(new Error('offline'));

		expect(await firstFlush).toBe(false);
		expect(save).toHaveBeenCalledTimes(1);
		expect(controller.hasPending()).toBe(true);
		expect(vi.getTimerCount()).toBe(0);

		expect(await controller.flush()).toBe(true);
		expect(save).toHaveBeenCalledTimes(2);
		expect(save).toHaveBeenLastCalledWith({
			projectId: 'project-a',
			scope: 'ui',
			uiPatch: { track_states: { dub: { muted: true, volume: 0.8 } } }
		});
	});
});
