import type { TimelineSelectionItem } from './timeline-context-menu';
import type { TimelineGroupMoveItem } from './timeline-group-move';
import type { SubtitleTrackKind } from './timeline-context-menu';
import type { VideoLocalizationTrackId } from './studio-state';

export type TimelineDragMode = 'move' | 'trim-start' | 'trim-end';

export type TimelineCueDragState = {
	kind: 'cue-drag';
	itemId: string;
	trackKind: SubtitleTrackKind;
	persistenceTarget: 'asr' | 'localized' | 'dub';
	mode: TimelineDragMode;
	startX: number;
	startMs: number;
	endMs: number;
	durationMs: number;
	minStartMs: number;
	maxEndMs: number;
	groupItems: TimelineGroupMoveItem[];
	groupMinDeltaMs: number;
	groupMaxDeltaMs: number;
};

export type TimelineClipDragState = {
	kind: 'clip-drag';
	clipId: string;
	trackId: VideoLocalizationTrackId;
	mode: TimelineDragMode;
	startX: number;
	startMs: number;
	endMs: number;
	durationMs: number;
	sourceStartMs: number;
	sourceEndMs: number | null;
	sourceDurationMs: number;
	startLane: number;
	targetLane: number;
	hoverLane: number | null;
	laneDropAllowed: boolean;
	groupLaneByClipId: Record<string, number>;
	targetLaneByClipId: Record<string, number>;
	groupItems: TimelineGroupMoveItem[];
	groupMinDeltaMs: number;
	groupMaxDeltaMs: number;
};

export type TimelineMarqueeState = {
	kind: 'marquee';
	startX: number;
	startY: number;
	currentX: number;
	currentY: number;
	moved: boolean;
	additive: boolean;
	baseItems: TimelineSelectionItem[];
};

export type TimelineGestureState =
	| { kind: 'idle' }
	| TimelineCueDragState
	| TimelineClipDragState
	| { kind: 'pan'; startX: number; scrollLeft: number }
	| { kind: 'seek' }
	| { kind: 'range-create'; startX: number; startMs: number; moved: boolean }
	| TimelineMarqueeState
	| { kind: 'selection-handle'; edge: 'start' | 'end' };

export type TimelineGestureKind = TimelineGestureState['kind'];

export class TimelineGestureSession {
	constructor(readonly state: TimelineGestureState = { kind: 'idle' }) {}

	begin(state: Exclude<TimelineGestureState, { kind: 'idle' }>) {
		return new TimelineGestureSession(state);
	}

	update<K extends Exclude<TimelineGestureKind, 'idle'>>(
		kind: K,
		update: (state: Extract<TimelineGestureState, { kind: K }>) => Extract<TimelineGestureState, { kind: K }>
	) {
		if (this.state.kind !== kind) return this;
		return new TimelineGestureSession(update(this.state as Extract<TimelineGestureState, { kind: K }>));
	}

	finish(): { completed: Exclude<TimelineGestureState, { kind: 'idle' }> | null; session: TimelineGestureSession } {
		return {
			completed: this.state.kind === 'idle' ? null : this.state,
			session: this.cancel()
		};
	}

	cancel() {
		return this.state.kind === 'idle' ? this : new TimelineGestureSession();
	}

	isActive() {
		return this.state.kind !== 'idle';
	}

	is<K extends TimelineGestureKind>(kind: K): this is TimelineGestureSession & {
		state: Extract<TimelineGestureState, { kind: K }>;
	} {
		return this.state.kind === kind;
	}
}
