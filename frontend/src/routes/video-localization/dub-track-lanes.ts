import type { VideoLocalizationTimelineClip } from '$lib/api/types';

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
 * Assigns valid dub clips to the minimum number of non-overlapping lanes.
 * Intervals are half-open, so a clip ending exactly when another starts can
 * share its lane. Existing lane hints are retained whenever that lane is
 * already available without creating empty lanes or an avoidable extra lane.
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
	const laneEnds: number[] = [];
	const laneByClipId = new Map<string, number>();

	for (const { clip } of ordered) {
		const start = clip.start_ms as number;
		const end = clip.end_ms as number;
		const preferred = preferredLane(clip, previous);
		const firstAvailable = laneEnds.findIndex((laneEnd) => laneEnd <= start);
		const preferredIsAvailable =
			preferred !== undefined && preferred < laneEnds.length && laneEnds[preferred] <= start;
		const lane = preferredIsAvailable
			? preferred
			: firstAvailable >= 0
				? firstAvailable
				: laneEnds.length;

		if (!lanes[lane]) lanes[lane] = [];
		lanes[lane].push(clip);
		laneEnds[lane] = end;
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

export function getDubTrackClipLane(
	layout: DubTrackLaneLayout,
	clip: VideoLocalizationTimelineClip | string
) {
	return layout.laneByClipId.get(typeof clip === 'string' ? clip : clip.clip_id);
}

export function resolveDubHistoryDropLane(
	clips: VideoLocalizationTimelineClip[],
	startMs: number,
	endMs: number,
	preferredLane: number
) {
	const previewId = '__tts_history_drop_preview__';
	const preview: VideoLocalizationTimelineClip = {
		clip_id: previewId,
		track_id: 'dub',
		start_ms: startMs,
		end_ms: endMs,
		dub_lane: Math.max(0, Math.floor(preferredLane))
	};
	const layout = buildDubTrackLaneLayout([...clips, preview]);
	return getDubTrackClipLane(layout, previewId) ?? 0;
}
