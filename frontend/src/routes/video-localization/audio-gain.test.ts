import { describe, expect, it } from 'vitest';
import {
	AUDIO_GAIN_MAX_DB,
	AUDIO_GAIN_MIN_DB,
	applyWaveformGain,
	dbToGain,
	gainToDb,
	isPotentialAudioGainDraft,
	normalizeAudioGainDb,
	parseAudioGainDraft,
	projectedPeakState
} from './audio-gain';

describe('audio gain editing', () => {
	it('keeps a leading minus sign as an editable draft instead of converting it to zero', () => {
		expect(isPotentialAudioGainDraft('-')).toBe(true);
		expect(parseAudioGainDraft('-')).toBeNull();
		expect(parseAudioGainDraft('-6.25')).toBe(-6.25);
	});

	it('accepts decimal editing states and rejects unrelated characters', () => {
		expect(isPotentialAudioGainDraft('')).toBe(true);
		expect(isPotentialAudioGainDraft('-.')).toBe(true);
		expect(isPotentialAudioGainDraft('.5')).toBe(true);
		expect(isPotentialAudioGainDraft('1.2.3')).toBe(false);
		expect(isPotentialAudioGainDraft('6 dB')).toBe(false);
	});

	it('clamps committed gain to the supported professional control range', () => {
		expect(normalizeAudioGainDb(-90)).toBe(AUDIO_GAIN_MIN_DB);
		expect(normalizeAudioGainDb(18)).toBe(AUDIO_GAIN_MAX_DB);
		expect(normalizeAudioGainDb(-3.25)).toBe(-3.25);
	});

	it('round-trips between decibels and the linear gain stored by the draft', () => {
		expect(dbToGain(6)).toBeCloseTo(1.995262, 5);
		expect(gainToDb(dbToGain(-18), 2)).toBe(-18);
		expect(dbToGain(AUDIO_GAIN_MIN_DB)).toBe(0);
	});
});

describe('gain-aware waveform visualization', () => {
	it('scales the visible waveform with the same linear gain used for playback', () => {
		expect(applyWaveformGain(0.25, dbToGain(6))).toBeCloseTo(0.498815, 5);
		expect(applyWaveformGain(0.5, dbToGain(-6))).toBeCloseTo(0.250594, 5);
	});

	it('marks the -1 dBFS headroom region as risk and post-gain full scale as clipping', () => {
		expect(projectedPeakState(0.88, 1)).toBe('normal');
		expect(projectedPeakState(0.9, 1)).toBe('risk');
		expect(projectedPeakState(0.6, 2)).toBe('clipping');
	});
});
