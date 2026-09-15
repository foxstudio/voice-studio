const NESTED_STATE_KEYS = ['track_states', 'dub_lane_states'] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function mergeVideoLocalizationUiState(
	base: Record<string, unknown>,
	patch: Record<string, unknown>
) {
	const merged = { ...base, ...patch };
	for (const key of NESTED_STATE_KEYS) {
		const previousValue = base[key];
		const nextValue = patch[key];
		if (!isRecord(previousValue) || !isRecord(nextValue)) continue;
		const nested: Record<string, unknown> = { ...previousValue };
		for (const [itemKey, itemPatch] of Object.entries(nextValue)) {
			const previousItem = nested[itemKey];
			nested[itemKey] = isRecord(previousItem) && isRecord(itemPatch)
				? { ...previousItem, ...itemPatch }
				: itemPatch;
		}
		merged[key] = nested;
	}
	return merged;
}
