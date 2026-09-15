export type MediaRuntimeStall = {
	mediaTime: number;
	timelineMs: number;
};

/**
 * Owns the mutable browser-media resources used by PreviewPanel.
 *
 * The component still decides when media should play. This registry owns how
 * elements, recovery callbacks, timers and Web Audio nodes are registered and
 * released so a Svelte remount cannot leave an old playback runtime behind.
 */
export class PreviewMediaRegistry {
	private readonly sourceNodes = new Map<HTMLAudioElement, MediaElementAudioSourceNode>();
	private readonly gainNodes = new Map<HTMLAudioElement, GainNode>();
	private readonly pendingPlayRequests = new WeakSet<HTMLAudioElement>();
	private readonly pendingFetches = new WeakSet<HTMLAudioElement>();
	private readonly readyRecoveries = new Map<HTMLAudioElement, () => void>();
	private readonly stallChecks = new Map<HTMLAudioElement, ReturnType<typeof setTimeout>>();
	private readonly runtimeStalls = new Map<HTMLMediaElement, MediaRuntimeStall>();
	private readonly desiredTimes = new WeakMap<HTMLAudioElement, number>();
	private readonly desiredEndTimes = new WeakMap<HTMLAudioElement, number>();
	private readonly fixedElements = new Set<HTMLAudioElement>();
	private readonly dubElements = new Map<string, HTMLAudioElement>();
	private readonly releasedAudio = new WeakSet<HTMLAudioElement>();

	registerFixed(audio: HTMLAudioElement) {
		this.releasedAudio.delete(audio);
		this.fixedElements.add(audio);
	}

	releaseFixed(audio: HTMLAudioElement) {
		this.fixedElements.delete(audio);
		this.releaseAudio(audio);
	}

	registerDub(key: string, audio: HTMLAudioElement) {
		const previous = this.dubElements.get(key);
		if (previous && previous !== audio) this.releaseAudio(previous);
		this.releasedAudio.delete(audio);
		this.dubElements.set(key, audio);
	}

	releaseDub(key: string, audio: HTMLAudioElement) {
		if (this.dubElements.get(key) === audio) this.dubElements.delete(key);
		this.releaseAudio(audio);
	}

	dubAudio(key: string) {
		return this.dubElements.get(key);
	}

	dubEntries() {
		return this.dubElements.entries();
	}

	dubValues() {
		return this.dubElements.values();
	}

	allAudio() {
		return this.audioElements();
	}

	isReleased(audio: HTMLAudioElement) {
		return this.releasedAudio.has(audio);
	}

	gainFor(audio: HTMLAudioElement) {
		return this.gainNodes.get(audio);
	}

	setAudioGraph(audio: HTMLAudioElement, source: MediaElementAudioSourceNode, gain: GainNode) {
		this.sourceNodes.set(audio, source);
		this.gainNodes.set(audio, gain);
	}

	hasGains() {
		return this.gainNodes.size > 0;
	}

	isPlayPending(audio: HTMLAudioElement) {
		return this.pendingPlayRequests.has(audio);
	}

	markPlayPending(audio: HTMLAudioElement) {
		this.pendingPlayRequests.add(audio);
	}

	clearPlayPending(audio: HTMLAudioElement) {
		this.pendingPlayRequests.delete(audio);
	}

	isFetchPending(audio: HTMLAudioElement) {
		return this.pendingFetches.has(audio);
	}

	markFetchPending(audio: HTMLAudioElement) {
		this.pendingFetches.add(audio);
	}

	clearFetchPending(audio: HTMLAudioElement) {
		this.pendingFetches.delete(audio);
	}

	readyRecovery(audio: HTMLAudioElement) {
		return this.readyRecoveries.get(audio);
	}

	setReadyRecovery(audio: HTMLAudioElement, cleanup: () => void) {
		this.cancelReadyRecovery(audio);
		this.readyRecoveries.set(audio, cleanup);
	}

	deleteReadyRecovery(audio: HTMLAudioElement) {
		this.readyRecoveries.delete(audio);
	}

	cancelReadyRecovery(audio: HTMLAudioElement) {
		this.readyRecoveries.get(audio)?.();
		this.readyRecoveries.delete(audio);
	}

	cancelAllReadyRecoveries() {
		for (const audio of [...this.readyRecoveries.keys()]) this.cancelReadyRecovery(audio);
	}

	hasStallCheck(audio: HTMLAudioElement) {
		return this.stallChecks.has(audio);
	}

	setStallCheck(audio: HTMLAudioElement, timer: ReturnType<typeof setTimeout>) {
		const previous = this.stallChecks.get(audio);
		if (previous) clearTimeout(previous);
		this.stallChecks.set(audio, timer);
	}

	deleteStallCheck(audio: HTMLAudioElement) {
		this.stallChecks.delete(audio);
	}

	clearStallCheck(audio: HTMLAudioElement) {
		const timer = this.stallChecks.get(audio);
		if (timer) clearTimeout(timer);
		this.stallChecks.delete(audio);
		delete audio.dataset.stallRecoveryPending;
	}

	clearAllStallChecks() {
		for (const audio of [...this.stallChecks.keys()]) this.clearStallCheck(audio);
	}

	setRuntimeStall(media: HTMLMediaElement, stall: MediaRuntimeStall) {
		this.runtimeStalls.set(media, stall);
	}

	runtimeStall(media: HTMLMediaElement) {
		return this.runtimeStalls.get(media);
	}

	deleteRuntimeStall(media: HTMLMediaElement) {
		this.runtimeStalls.delete(media);
	}

	clearRuntimeStalls() {
		const changed = this.runtimeStalls.size > 0;
		this.runtimeStalls.clear();
		return changed;
	}

	setDesiredRange(audio: HTMLAudioElement, startSeconds: number, endSeconds: number) {
		this.desiredTimes.set(audio, startSeconds);
		this.desiredEndTimes.set(audio, endSeconds);
	}

	desiredTime(audio: HTMLAudioElement) {
		return this.desiredTimes.get(audio);
	}

	desiredEndTime(audio: HTMLAudioElement) {
		return this.desiredEndTimes.get(audio);
	}

	resetRuntime() {
		this.cancelAllReadyRecoveries();
		this.clearAllStallChecks();
		for (const audio of this.audioElements()) {
			this.pendingFetches.delete(audio);
			this.pendingPlayRequests.delete(audio);
			this.desiredTimes.delete(audio);
			this.desiredEndTimes.delete(audio);
			delete audio.dataset.stallRecoveryPending;
		}
		this.runtimeStalls.clear();
	}

	dispose() {
		this.resetRuntime();
		for (const audio of this.audioElements()) this.releaseAudio(audio);
		for (const source of this.sourceNodes.values()) source.disconnect();
		for (const gain of this.gainNodes.values()) gain.disconnect();
		this.sourceNodes.clear();
		this.gainNodes.clear();
		this.fixedElements.clear();
		this.dubElements.clear();
		this.runtimeStalls.clear();
	}

	private audioElements() {
		return [...new Set([...this.fixedElements, ...this.dubElements.values()])];
	}

	private releaseAudio(audio: HTMLAudioElement) {
		if (this.releasedAudio.has(audio)) return;
		this.releasedAudio.add(audio);
		audio.pause();
		this.cancelReadyRecovery(audio);
		this.clearStallCheck(audio);
		this.pendingFetches.delete(audio);
		this.pendingPlayRequests.delete(audio);
		this.runtimeStalls.delete(audio);
		const gain = this.gainNodes.get(audio);
		const source = this.sourceNodes.get(audio);
		source?.disconnect();
		gain?.disconnect();
		this.sourceNodes.delete(audio);
		this.gainNodes.delete(audio);
		audio.removeAttribute('src');
		audio.load();
	}
}
