export function mergeTaskPageWithPinned<T extends { task_id: string }>(
	pageItems: readonly T[],
	currentItems: readonly T[],
	pinnedTaskIds: ReadonlySet<string>
) {
	const pageIds = new Set(pageItems.map((item) => item.task_id));
	const pinned = currentItems.filter(
		(item) => pinnedTaskIds.has(item.task_id) && !pageIds.has(item.task_id)
	);
	return [...pinned, ...pageItems];
}

export function upsertTaskPreservingOrder<T extends { task_id: string }>(
	items: readonly T[],
	next: T
) {
	const existingIndex = items.findIndex((item) => item.task_id === next.task_id);
	if (existingIndex < 0) return [next, ...items];
	return items.map((item, index) => index === existingIndex ? next : item);
}

export function taskIdentityKey<T extends { task_id: string }>(items: readonly T[]) {
	return items.map((item) => item.task_id).join('|');
}

export function stableTaskCardRenderLimit(
	currentLimit: number,
	total: number,
	initialLimit = 4
) {
	if (total <= 0) return 0;
	return Math.min(total, Math.max(currentLimit, Math.min(total, initialLimit)));
}
