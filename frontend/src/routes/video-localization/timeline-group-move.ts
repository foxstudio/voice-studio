export type TimelineGroupMoveItemKind = 'subtitle' | 'audio';

export type TimelineGroupMoveItem = {
	kind: TimelineGroupMoveItemKind;
	trackId: string;
	itemId: string;
	startMs: number;
	endMs: number;
};

export type TimelineGroupMovePrimary = TimelineGroupMoveItem;

export type TimelineGroupMoveRequest = {
	selectedItems: readonly TimelineGroupMoveItem[];
	primary: TimelineGroupMovePrimary;
	requestedDeltaMs: number;
	timelineDurationMs: number;
	minimumDeltaMs?: number;
	maximumDeltaMs?: number;
};

export type TimelineGroupMoveResultItem = {
	kind: TimelineGroupMoveItemKind;
	trackId: string;
	itemId: string;
	originalStartMs: number;
	originalEndMs: number;
	startMs: number;
	endMs: number;
	durationMs: number;
};

export type TimelineGroupMoveResult = {
	requestedDeltaMs: number;
	appliedDeltaMs: number;
	minDeltaMs: number;
	maxDeltaMs: number;
	constrained: boolean;
	primary: TimelineGroupMoveResultItem;
	items: TimelineGroupMoveResultItem[];
};

export function resolveTimelineGroupMove(request: TimelineGroupMoveRequest): TimelineGroupMoveResult {
	const timelineDurationMs = requireFiniteNumber(request.timelineDurationMs, 'timelineDurationMs');
	const requestedDeltaMs = requireFiniteNumber(request.requestedDeltaMs, 'requestedDeltaMs');
	if (timelineDurationMs < 0) throw new RangeError('timelineDurationMs must be at least 0');
	if (!request.selectedItems.length) throw new RangeError('selectedItems must not be empty');

	const selectedItems = request.selectedItems.map((item, index) => validateItem(item, timelineDurationMs, `selectedItems[${index}]`));
	const primary = validateItem(request.primary, timelineDurationMs, 'primary');
	const identities = new Set<string>();
	for (const item of selectedItems) {
		const key = itemKey(item);
		if (identities.has(key)) throw new RangeError(`selectedItems contains duplicate item ${key}`);
		identities.add(key);
	}

	const selectedPrimary = selectedItems.find((item) => itemKey(item) === itemKey(primary));
	if (!selectedPrimary) throw new RangeError('primary must be included in selectedItems');
	if (selectedPrimary.startMs !== primary.startMs || selectedPrimary.endMs !== primary.endMs) {
		throw new RangeError('primary range must match the selected item range');
	}

	const earliestStartMs = Math.min(...selectedItems.map((item) => item.startMs));
	const latestEndMs = Math.max(...selectedItems.map((item) => item.endMs));
	const timelineMinDeltaMs = earliestStartMs === 0 ? 0 : -earliestStartMs;
	const timelineMaxDeltaMs = timelineDurationMs - latestEndMs;
	const minimumDeltaMs = request.minimumDeltaMs === undefined
		? timelineMinDeltaMs
		: requireFiniteNumber(request.minimumDeltaMs, 'minimumDeltaMs');
	const maximumDeltaMs = request.maximumDeltaMs === undefined
		? timelineMaxDeltaMs
		: requireFiniteNumber(request.maximumDeltaMs, 'maximumDeltaMs');
	const minDeltaMs = Math.max(timelineMinDeltaMs, minimumDeltaMs);
	const maxDeltaMs = Math.min(timelineMaxDeltaMs, maximumDeltaMs);
	if (minDeltaMs > maxDeltaMs) throw new RangeError('group move constraints do not leave a valid range');
	const appliedDeltaMs = clamp(requestedDeltaMs, minDeltaMs, maxDeltaMs);
	const items = selectedItems.map((item) => movedItem(item, appliedDeltaMs));
	const movedPrimary = items.find((item) => itemKey(item) === itemKey(primary));
	if (!movedPrimary) throw new Error('primary move result is missing');

	return {
		requestedDeltaMs,
		appliedDeltaMs,
		minDeltaMs,
		maxDeltaMs,
		constrained: appliedDeltaMs !== requestedDeltaMs,
		primary: movedPrimary,
		items
	};
}

function validateItem(item: TimelineGroupMoveItem, timelineDurationMs: number, label: string): TimelineGroupMoveItem {
	if (item.kind !== 'subtitle' && item.kind !== 'audio') throw new TypeError(`${label}.kind is invalid`);
	if (!item.trackId) throw new TypeError(`${label}.trackId must not be empty`);
	if (!item.itemId) throw new TypeError(`${label}.itemId must not be empty`);
	const startMs = requireFiniteNumber(item.startMs, `${label}.startMs`);
	const endMs = requireFiniteNumber(item.endMs, `${label}.endMs`);
	if (startMs < 0 || endMs <= startMs || endMs > timelineDurationMs) {
		throw new RangeError(`${label} must be a non-empty range inside the timeline`);
	}
	return { ...item, startMs, endMs };
}

function movedItem(item: TimelineGroupMoveItem, deltaMs: number): TimelineGroupMoveResultItem {
	return {
		kind: item.kind,
		trackId: item.trackId,
		itemId: item.itemId,
		originalStartMs: item.startMs,
		originalEndMs: item.endMs,
		startMs: item.startMs + deltaMs,
		endMs: item.endMs + deltaMs,
		durationMs: item.endMs - item.startMs
	};
}

function itemKey(item: Pick<TimelineGroupMoveItem, 'kind' | 'trackId' | 'itemId'>) {
	return `${item.kind}\u0000${item.trackId}\u0000${item.itemId}`;
}

function requireFiniteNumber(value: number, label: string) {
	if (!Number.isFinite(value)) throw new TypeError(`${label} must be finite`);
	return value;
}

function clamp(value: number, min: number, max: number) {
	return Math.max(min, Math.min(max, value));
}
