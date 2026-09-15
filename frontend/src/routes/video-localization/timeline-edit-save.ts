type SaveReceipt = { updated_at: string | null; revision: string };

/** Save UI first: a failed UI request must not leave a committed clip packet to replay. */
export async function saveTimelineEditWithUiPatch<T extends SaveReceipt>(
	saveTimeline: () => Promise<T>,
	saveUi: (patch: Record<string, unknown>) => Promise<SaveReceipt>,
	uiPatch: Record<string, unknown>
): Promise<T> {
	if (Object.keys(uiPatch).length) await saveUi(uiPatch);
	return saveTimeline();
}
