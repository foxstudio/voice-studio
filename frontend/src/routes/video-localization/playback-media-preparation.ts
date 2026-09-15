export function preparePlaybackMedia<T>(input: {
	time: number;
	syncAt: (time: number) => void;
	primeUpcomingAt: (time: number) => void;
	activeAt: (time: number) => T[];
	isReady: (media: T) => boolean;
	startPreparing: (media: T) => void;
}) {
	input.syncAt(input.time);
	input.primeUpcomingAt(input.time);
	const active = [...new Set(input.activeAt(input.time))];
	for (const media of active) {
		if (!input.isReady(media)) input.startPreparing(media);
	}
	return active;
}

/** Restore the requested source position after media.load() resets it to zero. */
export function restorePreparedMediaTime(
	media: Pick<HTMLMediaElement, 'currentTime' | 'duration' | 'readyState'>,
	desiredTime: number,
	toleranceSeconds = 0.04
) {
	if (media.readyState < 1 || !Number.isFinite(desiredTime)) return false;
	const boundedTime = Number.isFinite(media.duration)
		? Math.min(Math.max(0, desiredTime), media.duration)
		: Math.max(0, desiredTime);
	if (Math.abs(media.currentTime - boundedTime) <= toleranceSeconds) return false;
	try {
		media.currentTime = boundedTime;
		return true;
	} catch {
		return false;
	}
}
