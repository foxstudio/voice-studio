export const AUDIO_GAIN_MIN_DB = -60;
export const AUDIO_GAIN_MAX_DB = 12;
export const AUDIO_GAIN_RISK_DBFS = -1;

const POTENTIAL_GAIN_DRAFT = /^-?(?:\d*(?:\.\d*)?)?$/;
const COMPLETE_GAIN_DRAFT = /^-?(?:\d+(?:\.\d*)?|\.\d+)$/;
const RISK_LINEAR_PEAK = Math.pow(10, AUDIO_GAIN_RISK_DBFS / 20);

export type ProjectedPeakState = 'normal' | 'risk' | 'clipping';

export function isPotentialAudioGainDraft(value: string): boolean {
	return POTENTIAL_GAIN_DRAFT.test(value.trim());
}

export function parseAudioGainDraft(value: string): number | null {
	const normalized = value.trim();
	if (!COMPLETE_GAIN_DRAFT.test(normalized)) return null;
	const numeric = Number(normalized);
	return Number.isFinite(numeric) ? numeric : null;
}

export function normalizeAudioGainDb(value: number): number {
	if (!Number.isFinite(value)) return 0;
	return Math.max(AUDIO_GAIN_MIN_DB, Math.min(AUDIO_GAIN_MAX_DB, value));
}

export function gainToDb(gain: number | null | undefined, precision = 1): number {
	const safeGain = Math.max(0, Number.isFinite(gain) ? Number(gain) : 1);
	if (safeGain <= 0.001) return AUDIO_GAIN_MIN_DB;
	const factor = Math.pow(10, precision);
	return Math.round(20 * Math.log10(safeGain) * factor) / factor;
}

export function dbToGain(db: number): number {
	const normalized = normalizeAudioGainDb(db);
	if (normalized <= AUDIO_GAIN_MIN_DB) return 0;
	return Math.max(0, Math.min(4, Math.pow(10, normalized / 20)));
}

export function formatAudioGainDb(value: number): string {
	const rounded = Math.round(normalizeAudioGainDb(value) * 100) / 100;
	return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2).replace(/0+$/, '');
}

export function applyWaveformGain(peak: number, gain: number): number {
	const safePeak = Math.max(0, Number.isFinite(peak) ? peak : 0);
	const safeGain = Math.max(0, Number.isFinite(gain) ? gain : 1);
	return safePeak * safeGain;
}

export function projectedPeakState(peak: number, gain: number): ProjectedPeakState {
	const projected = applyWaveformGain(peak, gain);
	if (projected >= 1) return 'clipping';
	if (projected >= RISK_LINEAR_PEAK) return 'risk';
	return 'normal';
}
