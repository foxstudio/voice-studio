import type { TimelineSelectionItem } from './timeline-context-menu';

export function timelineSelectionItemEqual(left: TimelineSelectionItem, right: TimelineSelectionItem) {
	return left.kind === right.kind && left.trackId === right.trackId && left.itemId === right.itemId;
}

export function uniqueTimelineSelection(items: readonly TimelineSelectionItem[]) {
	return items.filter(
		(item, index) => items.findIndex((candidate) => timelineSelectionItemEqual(candidate, item)) === index
	);
}

export class TimelineSelectionSession {
	readonly items: TimelineSelectionItem[];
	readonly primary: TimelineSelectionItem | null;

	constructor(items: readonly TimelineSelectionItem[] = []) {
		this.items = uniqueTimelineSelection(items);
		this.primary = this.items.at(-1) ?? null;
	}

	set(items: readonly TimelineSelectionItem[]) {
		const next = uniqueTimelineSelection(items);
		if (
			next.length === this.items.length
			&& next.every((item, index) => timelineSelectionItemEqual(item, this.items[index]))
		) return this;
		return new TimelineSelectionSession(next);
	}

	toggle(item: TimelineSelectionItem, { includeWhenMissing = true } = {}) {
		if (this.contains(item)) {
			return this.set(this.items.filter((candidate) => !timelineSelectionItemEqual(candidate, item)));
		}
		return includeWhenMissing ? this.set([...this.items, item]) : this;
	}

	remove(predicate: (item: TimelineSelectionItem) => boolean) {
		return this.set(this.items.filter((item) => !predicate(item)));
	}

	contains(item: TimelineSelectionItem) {
		return this.items.some((candidate) => timelineSelectionItemEqual(candidate, item));
	}

	clear() {
		return this.items.length ? new TimelineSelectionSession() : this;
	}
}
