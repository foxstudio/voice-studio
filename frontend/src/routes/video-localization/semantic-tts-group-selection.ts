export function semanticTtsGroupOrdinal(
	groups: readonly { group_id: string }[],
	groupId: string
): number | null {
	const index = groups.findIndex((group) => group.group_id === groupId);
	return index >= 0 ? index + 1 : null;
}

export function semanticTtsGroupAtOrdinal<T extends { group_id: string }>(
	groups: readonly T[],
	ordinal: number
): T | null {
	if (!Number.isFinite(ordinal)) return null;
	const index = Math.floor(ordinal) - 1;
	return index >= 0 && index < groups.length ? groups[index] : null;
}
