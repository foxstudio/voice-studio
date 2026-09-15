export type SelectionRange = Readonly<{
	start: number;
	end: number;
}>;

export type SelectionPlaybackPhase = 'stopped' | 'playing' | 'editing';

export type SelectionPlaybackCommand = Readonly<{
	phase: SelectionPlaybackPhase;
	loopActive: boolean;
	position: number;
	seekTo: number | null;
	shouldPlay: boolean;
	shouldPause: boolean;
}>;

export function normalizeSelectionRange(start: number, end: number): SelectionRange | null {
	if (![start, end].every(Number.isFinite)) return null;
	const normalizedStart = Math.min(start, end);
	const normalizedEnd = Math.max(start, end);
	if (normalizedEnd <= normalizedStart) return null;
	return { start: normalizedStart, end: normalizedEnd };
}

export function selectionContainsTime(range: SelectionRange, time: number): boolean {
	const normalized = normalizeSelectionRange(range.start, range.end);
	return Boolean(normalized && Number.isFinite(time) && time >= normalized.start && time <= normalized.end);
}

/**
 * Owns the playback intent for an editable IN/OUT selection.
 *
 * The controller is deliberately independent from HTMLAudioElement. Callers
 * apply the returned seek/play/pause commands, while every input path (button,
 * Space, playhead drag, range drag, and media clock) shares this state machine.
 */
export class SelectionPlaybackController {
	private phase: SelectionPlaybackPhase = 'stopped';
	private selectionActive = false;
	private loopActive = false;
	private resumeAfterSelectionEdit = false;

	constructor(private readonly boundaryTolerance = 0.01) {}

	get status(): Readonly<{ phase: SelectionPlaybackPhase; loopActive: boolean }> {
		return { phase: this.phase, loopActive: this.loopActive };
	}

	start(
		range: SelectionRange,
		loopEnabled: boolean,
		currentTime: number = range.start
	): SelectionPlaybackCommand {
		const normalized = normalizeSelectionRange(range.start, range.end);
		const requestedPosition = this.safePosition(currentTime);
		let position = requestedPosition;
		if (
			normalized
			&& (
				requestedPosition < normalized.start
				|| requestedPosition >= normalized.end - this.boundaryTolerance
			)
		) position = normalized.start;
		this.phase = normalized ? 'playing' : 'stopped';
		this.selectionActive = Boolean(normalized);
		this.loopActive = this.selectionActive && loopEnabled;
		this.resumeAfterSelectionEdit = false;
		return this.command(position, normalized ? position : null, Boolean(normalized), false);
	}

	stop(range: SelectionRange): SelectionPlaybackCommand {
		const normalized = normalizeSelectionRange(range.start, range.end);
		const position = normalized?.start ?? 0;
		this.phase = 'stopped';
		this.selectionActive = false;
		this.loopActive = false;
		this.resumeAfterSelectionEdit = false;
		return this.command(position, normalized?.start ?? null, false, true);
	}

	pauseAt(currentTime: number): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		this.phase = 'stopped';
		this.selectionActive = false;
		this.loopActive = false;
		this.resumeAfterSelectionEdit = false;
		return this.command(position);
	}

	beginSelectionEdit(currentTime: number, sourcePlaying: boolean): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		this.resumeAfterSelectionEdit = sourcePlaying && this.phase === 'playing';
		this.phase = this.resumeAfterSelectionEdit ? 'editing' : 'stopped';
		this.selectionActive = false;
		this.loopActive = false;
		return this.command(position);
	}

	finishSelectionEdit(
		currentTime: number,
		range: SelectionRange,
		loopEnabled: boolean,
		sourcePlaying: boolean
	): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		const continuePlaying = this.resumeAfterSelectionEdit && sourcePlaying;
		this.phase = continuePlaying ? 'playing' : 'stopped';
		this.selectionActive = continuePlaying && selectionContainsTime(range, position);
		this.loopActive = this.selectionActive && loopEnabled;
		this.resumeAfterSelectionEdit = false;
		return this.command(position);
	}

	selectionChanged(
		currentTime: number,
		range: SelectionRange,
		loopEnabled: boolean,
		sourcePlaying: boolean
	): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		if (this.phase !== 'playing' || !sourcePlaying) {
			this.selectionActive = false;
			this.loopActive = false;
			return this.command(position);
		}
		this.selectionActive = selectionContainsTime(range, position);
		this.loopActive = this.selectionActive && loopEnabled;
		return this.command(position);
	}

	seekDuringPlayback(currentTime: number, range: SelectionRange, loopEnabled: boolean): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		if (this.phase === 'playing') {
			this.selectionActive = selectionContainsTime(range, position);
			this.loopActive = this.selectionActive && loopEnabled;
		}
		return this.command(position);
	}

	setLoopEnabled(enabled: boolean, currentTime: number, range: SelectionRange): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		this.selectionActive = this.phase === 'playing' && selectionContainsTime(range, position);
		this.loopActive = this.selectionActive && enabled;
		return this.command(position);
	}

	tick(currentTime: number, range: SelectionRange, loopEnabled: boolean): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		const normalized = normalizeSelectionRange(range.start, range.end);
		if (this.phase !== 'playing' || !normalized) return this.command(position);

		if (this.selectionActive && position < normalized.start) this.selectionActive = false;
		if (!this.selectionActive && selectionContainsTime(normalized, position)) this.selectionActive = true;
		this.loopActive = this.selectionActive && loopEnabled;
		if (this.selectionActive && position >= normalized.end - this.boundaryTolerance) {
			if (loopEnabled) return this.command(normalized.start, normalized.start);
			this.phase = 'stopped';
			this.selectionActive = false;
			this.loopActive = false;
			return this.command(normalized.end, normalized.end, false, true);
		}
		return this.command(position);
	}

	sourceEnded(currentTime: number, range: SelectionRange, loopEnabled: boolean): SelectionPlaybackCommand {
		const position = this.safePosition(currentTime);
		const normalized = normalizeSelectionRange(range.start, range.end);
		if (
			this.phase === 'playing'
			&& this.selectionActive
			&& normalized
			&& position >= normalized.end - this.boundaryTolerance
		) {
			if (loopEnabled) return this.command(normalized.start, normalized.start, true);
			this.phase = 'stopped';
			this.selectionActive = false;
			this.loopActive = false;
			return this.command(normalized.end, normalized.end, false, true);
		}
		if (this.phase === 'editing') {
			this.resumeAfterSelectionEdit = false;
			this.selectionActive = false;
			this.loopActive = false;
			return this.command(position);
		}
		this.phase = 'stopped';
		this.selectionActive = false;
		this.loopActive = false;
		this.resumeAfterSelectionEdit = false;
		return this.command(position);
	}

	reset(position = 0): SelectionPlaybackCommand {
		this.phase = 'stopped';
		this.selectionActive = false;
		this.loopActive = false;
		this.resumeAfterSelectionEdit = false;
		return this.command(this.safePosition(position));
	}

	private safePosition(position: number): number {
		return Number.isFinite(position) ? Math.max(0, position) : 0;
	}

	private command(
		position: number,
		seekTo: number | null = null,
		shouldPlay = false,
		shouldPause = false
	): SelectionPlaybackCommand {
		return {
			phase: this.phase,
			loopActive: this.loopActive,
			position,
			seekTo,
			shouldPlay,
			shouldPause
		};
	}
}
