import {
	normalizeSelectionRange,
	selectionContainsTime
} from '$lib/audio/selection-playback-controller';

export type PlaybackLoopRange = {
	start_ms: number;
	end_ms: number;
};

export type PlaybackDriver = {
	playPause: () => void;
	play: () => void;
	seek: (timeMs: number) => void;
	scrub: (timeMs: number) => void;
	endScrub: () => void;
	refreshMediaCache: () => void;
};

type PlaybackSessionControllerOptions = {
	getProjectId: () => string;
	getDraftProjectId: () => string;
	hasDraft: () => boolean;
	getTimeMs: () => number;
	getPlaying: () => boolean;
	setTimeMs: (timeMs: number) => void;
	setPlaying: (playing: boolean) => void;
	setPreparing: (preparing: boolean) => void;
	setHoverTimeMs: (timeMs: number | null) => void;
	setLoopRange: (range: PlaybackLoopRange | null) => void;
	setRestored: (restored: boolean) => void;
	persistPlayhead: (projectId: string, playheadMs: number) => void;
	persistIntervalMs?: number;
};

export class PlaybackSessionController {
	private readonly options: PlaybackSessionControllerOptions;
	private driver: PlaybackDriver | null = null;
	private restored = false;
	private loopRange: PlaybackLoopRange | null = null;
	private readonly persistIntervalMs: number;
	private pendingPersistence: { projectId: string; timeMs: number } | null = null;
	private persistTimer: ReturnType<typeof setTimeout> | null = null;
	private lastPersistedAt = Number.NEGATIVE_INFINITY;

	constructor(options: PlaybackSessionControllerOptions) {
		this.options = options;
		this.persistIntervalMs = Math.max(0, Math.round(options.persistIntervalMs ?? 250));
	}

	register(driver: PlaybackDriver | null) {
		this.flushPersistence();
		this.driver?.endScrub();
		this.driver = driver;
		this.restored = false;
		this.options.setRestored(false);
	}

	restore(expectedProjectId: string) {
		if (
			!this.driver
			|| !expectedProjectId
			|| expectedProjectId !== this.options.getProjectId()
			|| this.options.getDraftProjectId() !== expectedProjectId
		) return false;
		this.driver.seek(this.options.getTimeMs());
		this.restored = true;
		this.options.setRestored(true);
		return true;
	}

	updateTime(timeMs: number) {
		if (!this.restored) return;
		const boundedTimeMs = this.normalizeTime(timeMs);
		this.options.setTimeMs(boundedTimeMs);
		this.requestPersistence(boundedTimeMs);
	}

	updatePlaying(playing: boolean) {
		this.options.setPlaying(playing);
		if (playing) this.options.setHoverTimeMs(null);
		else this.persistCurrentTime();
	}

	updatePreparing(preparing: boolean) {
		this.options.setPreparing(preparing);
	}

	persistCurrentTime() {
		if (!this.restored || !this.options.hasDraft()) return;
		const projectId = this.options.getProjectId();
		if (!projectId) return;
		this.pendingPersistence = {
			projectId,
			timeMs: this.normalizeTime(this.options.getTimeMs())
		};
		this.flushPersistence();
	}

	seek(timeMs: number) {
		const boundedTimeMs = this.normalizeTime(timeMs);
		this.options.setTimeMs(boundedTimeMs);
		this.requestPersistence(boundedTimeMs);
		this.driver?.seek(boundedTimeMs);
	}

	hoverScrub(timeMs: number) {
		const boundedTimeMs = this.normalizeTime(timeMs);
		this.options.setHoverTimeMs(boundedTimeMs);
		this.driver?.scrub(boundedTimeMs);
	}

	endHoverScrub() {
		this.options.setHoverTimeMs(null);
		this.driver?.endScrub();
	}

	disableHoverScrub() {
		this.options.setHoverTimeMs(null);
		this.setLoopRange(null);
		this.driver?.endScrub();
	}

	updateSelectionRange(range: PlaybackLoopRange | null) {
		if (!range) this.setLoopRange(null);
		else if (this.loopRange) this.setLoopRange(range);
	}

	playRange(range: PlaybackLoopRange) {
		this.options.setHoverTimeMs(null);
		this.driver?.endScrub();
		this.setLoopRange(range);
		this.options.setTimeMs(range.start_ms);
		this.driver?.seek(range.start_ms);
		this.driver?.play();
	}

	togglePlayback(selectionRange: PlaybackLoopRange | null) {
		if (!this.options.getPlaying()) {
			const selection = selectionRange
				? normalizeSelectionRange(selectionRange.start_ms, selectionRange.end_ms)
				: null;
			const loopSelection = selection && selectionContainsTime(selection, this.options.getTimeMs())
				? selection
				: null;
			this.setLoopRange(loopSelection ? { start_ms: loopSelection.start, end_ms: loopSelection.end } : null);
		}
		this.driver?.playPause();
	}

	previewSeek(timeMs: number) {
		const boundedTimeMs = this.normalizeTime(timeMs);
		this.clearLoopRangeOutside(boundedTimeMs);
		this.options.setTimeMs(boundedTimeMs);
		this.driver?.scrub(boundedTimeMs);
	}

	commitSeek(timeMs: number) {
		const boundedTimeMs = this.normalizeTime(timeMs);
		this.clearLoopRangeOutside(boundedTimeMs);
		this.driver?.endScrub();
		this.seek(boundedTimeMs);
	}

	private clearLoopRangeOutside(timeMs: number) {
		if (
			this.loopRange
			&& !selectionContainsTime(
				{ start: this.loopRange.start_ms, end: this.loopRange.end_ms },
				timeMs
			)
		) this.setLoopRange(null);
	}

	setLoopRange(range: PlaybackLoopRange | null) {
		this.loopRange = range;
		this.options.setLoopRange(range);
	}

	play() {
		this.driver?.play();
	}

	playPause() {
		this.driver?.playPause();
	}

	refreshMediaCache() {
		this.driver?.refreshMediaCache();
	}

	dispose() {
		this.flushPersistence();
		this.driver?.endScrub();
		this.driver = null;
	}

	private requestPersistence(timeMs: number) {
		if (!this.restored || !this.options.hasDraft()) return;
		const projectId = this.options.getProjectId();
		if (!projectId) return;
		this.pendingPersistence = { projectId, timeMs: this.normalizeTime(timeMs) };
		const elapsed = Date.now() - this.lastPersistedAt;
		if (!this.persistTimer && elapsed >= this.persistIntervalMs) {
			this.flushPersistence();
			return;
		}
		if (this.persistTimer) return;
		this.persistTimer = setTimeout(
			() => this.flushPersistence(),
			Math.max(0, this.persistIntervalMs - elapsed)
		);
	}

	private flushPersistence() {
		if (this.persistTimer) clearTimeout(this.persistTimer);
		this.persistTimer = null;
		const pending = this.pendingPersistence;
		this.pendingPersistence = null;
		if (!pending) return;
		this.options.persistPlayhead(pending.projectId, pending.timeMs);
		this.lastPersistedAt = Date.now();
	}

	private normalizeTime(timeMs: number) {
		return Math.max(0, Math.round(timeMs));
	}
}
