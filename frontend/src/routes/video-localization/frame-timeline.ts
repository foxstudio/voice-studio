export type FrameSnapMode = 'floor' | 'nearest' | 'ceil';
export type FrameEditMode = 'move' | 'trim-start' | 'trim-end';

export type FrameInterval = {
	startMs: number;
	endMs: number;
};

export type AudioFrameInterval = FrameInterval & {
	sourceStartMs: number;
	sourceEndMs: number;
};

export type FrameTimelineTick = {
	frame: number;
	timeMs: number;
	percent: number;
	label: string;
	major: boolean;
};

export type SecondTimelineTick = {
	second: number;
	timeMs: number;
	percent: number;
	label: string;
};

const DEFAULT_FRAME_RATE = 30;

export function normalizeFrameRate(value: number | null | undefined) {
	if (!Number.isFinite(value) || Number(value) < 1 || Number(value) > 240) return DEFAULT_FRAME_RATE;
	return Number(value);
}

export function frameDurationMs(frameRate: number) {
	return 1000 / normalizeFrameRate(frameRate);
}

export function frameIndexAtTime(timeMs: number, frameRate: number, mode: FrameSnapMode = 'nearest') {
	const raw = Math.max(0, Number.isFinite(timeMs) ? timeMs : 0) / frameDurationMs(frameRate);
	if (mode === 'floor') return Math.floor(raw + 1e-7);
	if (mode === 'ceil') return Math.ceil(raw - 1e-7);
	return Math.round(raw);
}

export function frameTimeMs(frame: number, frameRate: number) {
	return Math.round(Math.max(0, Math.trunc(frame)) * frameDurationMs(frameRate));
}

export function frameCountForDuration(durationMs: number, frameRate: number) {
	if (!Number.isFinite(durationMs) || durationMs <= 0) return 1;
	return Math.max(1, Math.ceil(durationMs / frameDurationMs(frameRate) - 1e-7));
}

export function lastFrameStartMs(durationMs: number, frameRate: number) {
	return Math.min(Math.max(0, Math.round(durationMs)), frameTimeMs(frameCountForDuration(durationMs, frameRate) - 1, frameRate));
}

export function snapTimeToFrame(
	timeMs: number,
	frameRate: number,
	mode: FrameSnapMode = 'nearest',
	minMs = 0,
	maxMs = Number.POSITIVE_INFINITY
) {
	const minFrame = frameIndexAtTime(Math.max(0, minMs), frameRate, 'ceil');
	const requestedMax = Number.isFinite(maxMs) ? Math.max(minMs, maxMs) : Number.MAX_SAFE_INTEGER;
	const maxFrame = Number.isFinite(requestedMax)
		? Math.max(minFrame, frameIndexAtTime(requestedMax, frameRate, 'floor'))
		: Number.MAX_SAFE_INTEGER;
	const frame = Math.max(minFrame, Math.min(maxFrame, frameIndexAtTime(timeMs, frameRate, mode)));
	return frameTimeMs(frame, frameRate);
}

export function stepFrameTime(timeMs: number, deltaFrames: number, frameRate: number, durationMs: number) {
	const currentFrame = frameIndexAtTime(timeMs, frameRate, 'nearest');
	const maxFrame = frameCountForDuration(durationMs, frameRate) - 1;
	return frameTimeMs(Math.max(0, Math.min(maxFrame, currentFrame + Math.trunc(deltaFrames))), frameRate);
}

export function frameCoverage(timeMs: number, frameRate: number, durationMs: number) {
	const frame = Math.min(
		frameCountForDuration(durationMs, frameRate) - 1,
		frameIndexAtTime(timeMs, frameRate, 'floor')
	);
	const startMs = frameTimeMs(frame, frameRate);
	const endMs = Math.min(Math.max(startMs, durationMs), frameTimeMs(frame + 1, frameRate));
	return { frame, startMs, endMs };
}

export function framePrecisionVisible(
	durationMs: number,
	frameRate: number,
	zoom: number,
	viewportWidth: number,
	minimumPixelsPerFrame = 4
) {
	if (durationMs <= 0 || viewportWidth <= 0) return false;
	const totalFrames = frameCountForDuration(durationMs, frameRate);
	return (Math.max(1, zoom) * viewportWidth) / totalFrames >= minimumPixelsPerFrame;
}

export function buildVisibleFrameTicks({
	durationMs,
	frameRate,
	startMs,
	endMs,
	zoom,
	viewportWidth
}: {
	durationMs: number;
	frameRate: number;
	startMs: number;
	endMs: number;
	zoom: number;
	viewportWidth: number;
}) {
	if (durationMs <= 0) return [] as FrameTimelineTick[];
	const firstFrame = Math.max(0, frameIndexAtTime(startMs, frameRate, 'floor') - 1);
	const finalFrame = Math.min(
		frameCountForDuration(durationMs, frameRate) - 1,
		frameIndexAtTime(endMs, frameRate, 'ceil') + 1
	);
	const pixelsPerFrame = (Math.max(1, zoom) * Math.max(1, viewportWidth)) / frameCountForDuration(durationMs, frameRate);
	const labelStride = niceFrameStride(Math.max(1, Math.ceil(44 / Math.max(1, pixelsPerFrame))));
	const majorStride = Math.max(labelStride, niceFrameStride(Math.max(1, Math.ceil(18 / Math.max(1, pixelsPerFrame)))));
	const nominalFramesPerSecond = Math.max(1, Math.round(frameRate));
	const ticks: FrameTimelineTick[] = [];

	for (let frame = firstFrame; frame <= finalFrame; frame += 1) {
		const timeMs = frameTimeMs(frame, frameRate);
		const frameInSecond = frame % nominalFramesPerSecond;
		ticks.push({
			frame,
			timeMs,
			percent: (timeMs / durationMs) * 100,
			// Frame ruler labels are frame numbers inside the current second, like
			// professional NLEs. A global frame count becomes misleading after 1s.
			label: frameInSecond % labelStride === 0 ? `${frameInSecond}f` : '',
			major: frameInSecond % majorStride === 0
		});
	}
	return ticks;
}

export function buildVisibleSecondTicks({
	durationMs,
	startMs,
	endMs
}: {
	durationMs: number;
	startMs: number;
	endMs: number;
}) {
	if (durationMs <= 0) return [] as SecondTimelineTick[];
	const firstSecond = Math.max(0, Math.floor(startMs / 1000) - 1);
	const finalSecond = Math.min(Math.ceil(durationMs / 1000), Math.ceil(endMs / 1000) + 1);
	const ticks: SecondTimelineTick[] = [];

	for (let second = firstSecond; second <= finalSecond; second += 1) {
		const timeMs = Math.min(durationMs, second * 1000);
		ticks.push({
			second,
			timeMs,
			percent: (timeMs / durationMs) * 100,
			label: formatRulerSecond(second)
		});
	}
	return ticks;
}

export function editFrameInterval({
	mode,
	startMs,
	endMs,
	deltaMs,
	frameRate,
	minStartMs = 0,
	maxEndMs,
	minDurationMs
}: {
	mode: FrameEditMode;
	startMs: number;
	endMs: number;
	deltaMs: number;
	frameRate: number;
	minStartMs?: number;
	maxEndMs: number;
	minDurationMs: number;
}): FrameInterval {
	const startFrame = frameIndexAtTime(startMs, frameRate, 'nearest');
	const endFrame = Math.max(startFrame + 1, frameIndexAtTime(endMs, frameRate, 'nearest'));
	const deltaFrames = Math.round(deltaMs / frameDurationMs(frameRate));
	const minStartFrame = frameIndexAtTime(minStartMs, frameRate, 'ceil');
	const maxEndFrame = Math.max(minStartFrame + 1, frameIndexAtTime(maxEndMs, frameRate, 'floor'));
	const minFrames = Math.max(1, Math.ceil(minDurationMs / frameDurationMs(frameRate) - 1e-7));
	const durationFrames = Math.max(minFrames, endFrame - startFrame);
	let nextStartFrame = startFrame;
	let nextEndFrame = endFrame;

	if (mode === 'move') {
		nextStartFrame = clampInteger(startFrame + deltaFrames, minStartFrame, Math.max(minStartFrame, maxEndFrame - durationFrames));
		nextEndFrame = nextStartFrame + durationFrames;
	} else if (mode === 'trim-start') {
		nextStartFrame = clampInteger(startFrame + deltaFrames, minStartFrame, Math.max(minStartFrame, endFrame - minFrames));
	} else {
		nextEndFrame = clampInteger(endFrame + deltaFrames, startFrame + minFrames, maxEndFrame);
	}

	return {
		startMs: frameTimeMs(nextStartFrame, frameRate),
		endMs: frameTimeMs(nextEndFrame, frameRate)
	};
}

export function editAudioFrameInterval({
	mode,
	startMs,
	endMs,
	sourceStartMs,
	sourceEndMs,
	sourceDurationMs,
	deltaMs,
	frameRate,
	timelineDurationMs,
	minDurationMs
}: {
	mode: FrameEditMode;
	startMs: number;
	endMs: number;
	sourceStartMs: number;
	sourceEndMs: number;
	sourceDurationMs: number;
	deltaMs: number;
	frameRate: number;
	timelineDurationMs: number;
	minDurationMs: number;
}): AudioFrameInterval {
	const safeSourceDuration = Math.max(sourceEndMs, sourceDurationMs);
	const minStartMs = mode === 'trim-start' ? Math.max(0, startMs - sourceStartMs) : 0;
	const maxEndMs = mode === 'trim-end'
		? Math.min(timelineDurationMs, startMs + Math.max(0, safeSourceDuration - sourceStartMs))
		: timelineDurationMs;
	const interval = editFrameInterval({
		mode,
		startMs,
		endMs,
		deltaMs,
		frameRate,
		minStartMs,
		maxEndMs,
		minDurationMs
	});
	let nextSourceStart = sourceStartMs;
	let nextSourceEnd = sourceEndMs;
	if (mode === 'trim-start') nextSourceStart = sourceStartMs + interval.startMs - startMs;
	if (mode === 'trim-end') nextSourceEnd = sourceStartMs + interval.endMs - startMs;

	return {
		...interval,
		sourceStartMs: snapTimeToFrame(nextSourceStart, frameRate, 'nearest', 0, safeSourceDuration),
		sourceEndMs: snapTimeToFrame(nextSourceEnd, frameRate, 'nearest', 0, safeSourceDuration)
	};
}

export function formatFrameTimecode(timeMs: number, frameRate: number) {
	const fps = normalizeFrameRate(frameRate);
	const totalFrames = frameIndexAtTime(timeMs, fps, 'nearest');
	const nominalFps = Math.max(1, Math.round(fps));
	const frames = totalFrames % nominalFps;
	const totalWholeSeconds = Math.floor(totalFrames / fps);
	const seconds = totalWholeSeconds % 60;
	const minutes = Math.floor(totalWholeSeconds / 60) % 60;
	const hours = Math.floor(totalWholeSeconds / 3600);
	return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}:${String(frames).padStart(2, '0')}`;
}

function niceFrameStride(value: number) {
	const normalized = Math.max(1, Math.ceil(value));
	const power = 10 ** Math.floor(Math.log10(normalized));
	const unit = normalized / power;
	const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10;
	return nice * power;
}

function formatRulerSecond(totalSeconds: number) {
	const hours = Math.floor(totalSeconds / 3600);
	const minutes = Math.floor(totalSeconds / 60) % 60;
	const seconds = totalSeconds % 60;
	if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
	return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function clampInteger(value: number, min: number, max: number) {
	return Math.max(min, Math.min(max, Math.trunc(value)));
}
