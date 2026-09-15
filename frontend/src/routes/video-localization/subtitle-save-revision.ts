export function nextSubtitleSaveRevision(revisions: Map<string, number>, subtitleId: string) {
	const revision = (revisions.get(subtitleId) ?? 0) + 1;
	revisions.set(subtitleId, revision);
	return revision;
}

export function subtitleSaveRevisionIsCurrent(
	revisions: ReadonlyMap<string, number>,
	subtitleId: string,
	revision: number,
	epoch: number,
	currentEpoch: number
) {
	return epoch === currentEpoch && revisions.get(subtitleId) === revision;
}
