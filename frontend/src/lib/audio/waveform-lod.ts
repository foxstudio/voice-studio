export const OVERVIEW_WAVEFORM_BINS = 4_800;

const WAVEFORM_BIN_BUCKETS = [
	32,
	64,
	128,
	256,
	512,
	1_024,
	2_400,
	4_800,
	9_600,
	19_200,
	38_400,
	76_800,
	180_000
] as const;

export function waveformBinsForPixels(pixelWidth: number, devicePixelRatio = 1) {
	const safeWidth = Math.max(1, finiteOr(pixelWidth, 1));
	const safePixelRatio = Math.max(1, Math.min(2, finiteOr(devicePixelRatio, 1)));
	const target = Math.ceil(safeWidth * safePixelRatio * 1.5);
	return WAVEFORM_BIN_BUCKETS.find((bucket) => bucket >= target)
		?? WAVEFORM_BIN_BUCKETS[WAVEFORM_BIN_BUCKETS.length - 1];
}

export function waveformPreviewBins(detailBins: number) {
	const safeDetailBins = Math.max(
		WAVEFORM_BIN_BUCKETS[0],
		finiteOr(detailBins, WAVEFORM_BIN_BUCKETS[0])
	);
	if (safeDetailBins <= WAVEFORM_BIN_BUCKETS[2]) return safeDetailBins;
	return safeDetailBins <= OVERVIEW_WAVEFORM_BINS
		? WAVEFORM_BIN_BUCKETS[2]
		: OVERVIEW_WAVEFORM_BINS;
}

function finiteOr(value: number, fallback: number) {
	return Number.isFinite(value) ? value : fallback;
}
