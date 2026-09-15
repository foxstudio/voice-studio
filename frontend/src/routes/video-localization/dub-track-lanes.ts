import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import type { VideoLocalizationDubLaneStates } from './studio-state';

export interface DubTrackLaneAssignment {
	clip: VideoLocalizationTimelineClip;
	lane: number;
}

export interface DubTrackLaneLayout {
	assignments: DubTrackLaneAssignment[];
	lanes: VideoLocalizationTimelineClip[][];
	laneCount: number;
	laneByClipId: ReadonlyMap<string, number>;
}

export interface DubMovingClipRange {
	clipId: string;
	startMs: number;
	endMs: number;
}

export interface DubMovingClipLaneRange extends DubMovingClipRange {
	lane: number;
}

export interface DubClipGroupLanePlacement {
	primaryLane: number;
	laneByClipId: Readonly<Record<string, number>>;
	targetLanes: readonly number[];
}

export interface DubSubtitleAlignmentSource {
	hasContent: boolean;
	unmutedLaneIds: number[];
	allContentLanesMuted: boolean;
	unavailableReason: string;
}

const LANE_METADATA_KEYS = ['dub_lane', 'lane_index', 'lane'] as const;

function validTimeRange(clip: VideoLocalizationTimelineClip) {
	return (
		clip.track_id === 'dub' &&
		Number.isFinite(clip.start_ms) &&
		Number.isFinite(clip.end_ms) &&
		(clip.end_ms as number) > (clip.start_ms as number)
	);
}

function explicitLane(clip: VideoLocalizationTimelineClip) {
	for (const key of LANE_METADATA_KEYS) {
		const value = clip[key];
		if (typeof value === 'number' && Number.isInteger(value) && value >= 0) return value;
	}
	return undefined;
}

function preferredLane(clip: VideoLocalizationTimelineClip, previous?: DubTrackLaneLayout) {
	return explicitLane(clip) ?? previous?.laneByClipId.get(clip.clip_id);
}

/**
 * Preserves explicit lane ownership and packs legacy clips without lane metadata
 * into the first available lane. Intervals are half-open, so adjacent clips can
 * share a lane while even a one-millisecond overlap requires another lane.
 */
export function buildDubTrackLaneLayout(
	clips: VideoLocalizationTimelineClip[],
	previous?: DubTrackLaneLayout
): DubTrackLaneLayout {
	const ordered = clips
		.map((clip, inputIndex) => ({ clip, inputIndex }))
		.filter(({ clip }) => validTimeRange(clip))
		.sort(
			(left, right) =>
				(left.clip.start_ms as number) - (right.clip.start_ms as number) ||
				left.inputIndex - right.inputIndex
		);

	const assignments: DubTrackLaneAssignment[] = [];
	const lanes: VideoLocalizationTimelineClip[][] = [];
	const laneByClipId = new Map<string, number>();

	for (const { clip } of ordered) {
		const start = clip.start_ms as number;
		const end = clip.end_ms as number;
		const explicit = explicitLane(clip);
		const preferred = preferredLane(clip, previous);
		const laneAvailable = (lane: number) => !(lanes[lane] ?? []).some((item) =>
			start < (item.end_ms as number) && end > (item.start_ms as number)
		);
		// Persisted lane metadata is ownership, including temporarily overlapping
		// clips. Rendering must not invent a different lane that was never saved.
		let lane = explicit;
		if (lane === undefined && preferred !== undefined && laneAvailable(preferred)) lane = preferred;
		if (lane === undefined) {
			const firstAvailable = lanes.findIndex((_, laneIndex) => laneAvailable(laneIndex));
			lane = firstAvailable >= 0 ? firstAvailable : lanes.length;
		}

		while (lanes.length <= lane) lanes.push([]);
		lanes[lane].push(clip);
		assignments.push({ clip, lane });
		if (!laneByClipId.has(clip.clip_id)) laneByClipId.set(clip.clip_id, lane);
	}

	return {
		assignments,
		lanes,
		laneCount: lanes.length,
		laneByClipId
	};
}

export function getDubTrackLaneCount(layout: DubTrackLaneLayout) {
	return layout.laneCount;
}

/** Keeps explicitly created empty lanes visible so clips can be moved into them. */
export function visibleDubTrackLanes(
	layout: DubTrackLaneLayout,
	laneStateKeys: Iterable<string | number>
) {
	let laneCount = Math.max(1, layout.lanes.length);
	for (const key of laneStateKeys) {
		const lane = Number(key);
		if (Number.isInteger(lane) && lane >= 0) laneCount = Math.max(laneCount, lane + 1);
	}
	return Array.from({ length: laneCount }, (_, lane) => layout.lanes[lane] ?? []);
}

/**
 * Resolves the exact dubbing lanes used by subtitle recognition. Solo and
 * volume are intentionally ignored: this workflow includes every lane that
 * owns usable audio unless that lane is explicitly muted.
 */
export function resolveDubSubtitleGenerationSource(
	layout: DubTrackLaneLayout,
	laneStates: VideoLocalizationDubLaneStates
): DubSubtitleAlignmentSource {
	const contentLaneIds = layout.lanes.flatMap((clips, lane) =>
		clips.some((clip) => typeof clip.audio_path === 'string' && clip.audio_path.trim())
			? [lane]
			: []
	);
	const unmutedLaneIds = contentLaneIds.filter((lane) => laneStates[String(lane)]?.muted !== true);
	const hasContent = contentLaneIds.length > 0;
	return {
		hasContent,
		unmutedLaneIds,
		allContentLanesMuted: hasContent && unmutedLaneIds.length === 0,
		unavailableReason: hasContent
			? ''
			: '合成配音轨有可用音频后，才能重新识别字幕'
	};
}

export function getDubTrackClipLane(
	layout: DubTrackLaneLayout,
	clip: VideoLocalizationTimelineClip | string
) {
	return layout.laneByClipId.get(typeof clip === 'string' ? clip : clip.clip_id);
}

/** Adds lane metadata only to legacy clips. User-assigned lanes are ownership
 * state and must not be compacted merely because an earlier lane has room. */
export function ensureDubTrackLaneMetadata(clips: VideoLocalizationTimelineClip[]) {
	const layout = buildDubTrackLaneLayout(clips);
	let changed = false;
	const next = clips.map((clip) => {
		if (clip.track_id !== 'dub' || explicitLane(clip) !== undefined) return clip;
		changed = true;
		return { ...clip, dub_lane: layout.laneByClipId.get(clip.clip_id) ?? 0 };
	});
	return changed ? next : clips;
}

export function resolveDubHistoryDropLane(
	clips: VideoLocalizationTimelineClip[],
	startMs: number,
	endMs: number,
	preferredLane: number,
	blockedLanes: Iterable<number> = []
) {
	return resolveDubClipLane(clips, startMs, endMs, preferredLane, undefined, blockedLanes);
}

export function canPlaceDubClipInLane(
	clips: VideoLocalizationTimelineClip[],
	startMs: number,
	endMs: number,
	lane: number,
	excludeClipId?: string
) {
	if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return false;
	const requestedLane = Math.max(0, Math.floor(lane));
	const layout = buildDubTrackLaneLayout(clips.filter((clip) => clip.clip_id !== excludeClipId));
	return !(layout.lanes[requestedLane] ?? []).some((clip) =>
		startMs < (clip.end_ms as number) && endMs > (clip.start_ms as number)
	);
}

export function canPlaceDubClipGroupInLane(
	clips: VideoLocalizationTimelineClip[],
	ranges: readonly DubMovingClipRange[],
	lane: number
) {
	if (!ranges.length || ranges.some((range) => (
		!range.clipId || !Number.isFinite(range.startMs) || !Number.isFinite(range.endMs) || range.endMs <= range.startMs
	))) return false;
	for (let index = 0; index < ranges.length; index += 1) {
		for (let otherIndex = index + 1; otherIndex < ranges.length; otherIndex += 1) {
			const left = ranges[index];
			const right = ranges[otherIndex];
			if (left.startMs < right.endMs && left.endMs > right.startMs) return false;
		}
	}
	const movingIds = new Set(ranges.map((range) => range.clipId));
	const requestedLane = Math.max(0, Math.floor(lane));
	const layout = buildDubTrackLaneLayout(clips.filter((clip) => !movingIds.has(clip.clip_id)));
	const obstacles = layout.lanes[requestedLane] ?? [];
	return ranges.every((range) => !obstacles.some((clip) =>
		range.startMs < (clip.end_ms as number) && range.endMs > (clip.start_ms as number)
	));
}

export function canPlaceDubClipGroupAcrossLanes(
	clips: VideoLocalizationTimelineClip[],
	ranges: readonly DubMovingClipLaneRange[],
	blockedLanes: Iterable<number> = []
) {
	if (!ranges.length || ranges.some((range) => (
		!range.clipId
		|| !Number.isFinite(range.startMs)
		|| !Number.isFinite(range.endMs)
		|| range.endMs <= range.startMs
		|| !Number.isInteger(range.lane)
		|| range.lane < 0
	))) return false;
	const blocked = new Set([...blockedLanes].map((lane) => Math.max(0, Math.floor(lane))));
	const movingIds = new Set(ranges.map((range) => range.clipId));
	const stationaryClips = clips.filter((clip) => !movingIds.has(clip.clip_id));
	const rangesByLane = new Map<number, DubMovingClipRange[]>();
	for (const range of ranges) {
		if (blocked.has(range.lane)) return false;
		const laneRanges = rangesByLane.get(range.lane) ?? [];
		laneRanges.push({ clipId: range.clipId, startMs: range.startMs, endMs: range.endMs });
		rangesByLane.set(range.lane, laneRanges);
	}
	return [...rangesByLane].every(([lane, laneRanges]) =>
		canPlaceDubClipGroupInLane(stationaryClips, laneRanges, lane)
	);
}

/**
 * Moves a multi-lane selection by one shared lane offset. This preserves the
 * relative lane structure of overlapping selected clips instead of flattening
 * every clip into one lane.
 */
export function placeDubClipGroupAtPrimaryLane(
	clips: VideoLocalizationTimelineClip[],
	ranges: readonly DubMovingClipLaneRange[],
	primaryClipId: string,
	targetPrimaryLane: number,
	blockedLanes: Iterable<number> = []
): DubClipGroupLanePlacement | null {
	if (!ranges.length || ranges.some((range) => (
		!range.clipId
		|| !Number.isFinite(range.startMs)
		|| !Number.isFinite(range.endMs)
		|| range.endMs <= range.startMs
		|| !Number.isInteger(range.lane)
		|| range.lane < 0
	))) return null;
	const primary = ranges.find((range) => range.clipId === primaryClipId);
	if (!primary || !Number.isInteger(targetPrimaryLane) || targetPrimaryLane < 0) return null;
	const laneDelta = targetPrimaryLane - primary.lane;
	const movingIds = new Set(ranges.map((range) => range.clipId));
	const blocked = new Set([...blockedLanes].map((lane) => Math.max(0, Math.floor(lane))));
	const laneByClipId: Record<string, number> = {};
	for (const range of ranges) {
		const targetLane = range.lane + laneDelta;
		if (targetLane < 0 || blocked.has(targetLane)) return null;
		laneByClipId[range.clipId] = targetLane;
	}
	for (let index = 0; index < ranges.length; index += 1) {
		for (let otherIndex = index + 1; otherIndex < ranges.length; otherIndex += 1) {
			const left = ranges[index];
			const right = ranges[otherIndex];
			if (
				laneByClipId[left.clipId] === laneByClipId[right.clipId]
				&& left.startMs < right.endMs
				&& left.endMs > right.startMs
			) return null;
		}
	}
	const layout = buildDubTrackLaneLayout(clips.filter((clip) => !movingIds.has(clip.clip_id)));
	for (const range of ranges) {
		const obstacles = layout.lanes[laneByClipId[range.clipId]] ?? [];
		if (obstacles.some((clip) =>
			range.startMs < (clip.end_ms as number) && range.endMs > (clip.start_ms as number)
		)) return null;
	}
	return {
		primaryLane: targetPrimaryLane,
		laneByClipId,
		targetLanes: [...new Set(Object.values(laneByClipId))].sort((left, right) => left - right)
	};
}

export function resolveDubClipGroupLanePlacement(
	clips: VideoLocalizationTimelineClip[],
	ranges: readonly DubMovingClipLaneRange[],
	primaryClipId: string,
	existingLaneCount: number,
	blockedLanes: Iterable<number> = []
): DubClipGroupLanePlacement | null {
	const count = Math.max(1, Math.floor(existingLaneCount));
	const span = ranges.length
		? Math.max(...ranges.map((range) => range.lane)) - Math.min(...ranges.map((range) => range.lane))
		: 0;
	for (let lane = 0; lane < count; lane += 1) {
		const placement = placeDubClipGroupAtPrimaryLane(clips, ranges, primaryClipId, lane, blockedLanes);
		if (placement && placement.targetLanes.every((targetLane) => targetLane < count)) return placement;
	}
	let best: DubClipGroupLanePlacement | null = null;
	for (let lane = 0; lane <= count + span + 1; lane += 1) {
		const placement = placeDubClipGroupAtPrimaryLane(clips, ranges, primaryClipId, lane, blockedLanes);
		if (!placement || placement.targetLanes.every((targetLane) => targetLane < count)) continue;
		if (!best || Math.max(...placement.targetLanes) < Math.max(...best.targetLanes)) best = placement;
	}
	return best;
}

export function resolveDubClipGroupLane(
	clips: VideoLocalizationTimelineClip[],
	ranges: readonly DubMovingClipRange[],
	existingLaneCount: number,
	blockedLanes: Iterable<number> = []
) {
	const count = Math.max(1, Math.floor(existingLaneCount));
	const blocked = new Set([...blockedLanes].map((lane) => Math.max(0, Math.floor(lane))));
	for (let lane = 0; lane < count; lane += 1) {
		if (!blocked.has(lane) && canPlaceDubClipGroupInLane(clips, ranges, lane)) return lane;
	}
	return canPlaceDubClipGroupInLane(clips, ranges, count) ? count : null;
}

export function resolveDubClipLane(
	clips: VideoLocalizationTimelineClip[],
	startMs: number,
	endMs: number,
	preferredLane: number,
	excludeClipId?: string,
	blockedLanes: Iterable<number> = []
) {
	const layout = buildDubTrackLaneLayout(clips.filter((clip) => clip.clip_id !== excludeClipId));
	const requested = Math.max(0, Math.floor(preferredLane));
	const blocked = new Set([...blockedLanes].map((lane) => Math.max(0, Math.floor(lane))));
	const candidateLimit = layout.laneCount + blocked.size + 1;
	const candidates = [requested, ...Array.from({ length: candidateLimit + 1 }, (_, lane) => lane).filter((lane) => lane !== requested)];
	return candidates.find((lane) => !blocked.has(lane) && !(layout.lanes[lane] ?? []).some((clip) =>
		startMs < (clip.end_ms as number) && endMs > (clip.start_ms as number)
	)) ?? candidateLimit;
}

export function swapDubTrackLanes(
	clips: VideoLocalizationTimelineClip[],
	fromLane: number,
	toLane: number
) {
	const from = Math.max(0, Math.floor(fromLane));
	const to = Math.max(0, Math.floor(toLane));
	if (from === to) return clips;
	return clips.map((clip) => {
		if (clip.track_id !== 'dub') return clip;
		const lane = explicitLane(clip) ?? 0;
		if (lane !== from && lane !== to) return clip;
		return { ...clip, dub_lane: lane === from ? to : from };
	});
}
