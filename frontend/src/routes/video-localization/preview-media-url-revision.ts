export function stablePlaybackRevision(
	contentRevision: string | number | null | undefined,
	reloadRevision: number
) {
	const stable = String(contentRevision ?? '').trim();
	return reloadRevision
		? `${stable || 'media'}:reload:${reloadRevision}`
		: stable;
}

export function appendPlaybackReloadRevision(url: string, reloadRevision: number) {
	if (!url || !reloadRevision) return url;
	return `${url}${url.includes('?') ? '&' : '?'}playback_reload=${reloadRevision}`;
}
