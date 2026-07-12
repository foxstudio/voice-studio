import { describe, expect, it } from 'vitest';
import {
	createReferenceAudioDraft,
	legacyCustomVoicePatchFromDraft,
	normalizeReferenceAudioTrim,
	referenceAudioDraftFromLegacyState,
	restoreReferenceAudioDraft,
	snapshotReferenceAudioDraft,
	withReferenceAudioTrim,
	type LegacyCustomVoiceState
} from './draft';

function legacyState(overrides: Partial<LegacyCustomVoiceState> = {}): LegacyCustomVoiceState {
	return {
		customVoiceFileName: 'source.wav',
		customVoiceFileId: 'clip-1',
		customVoicePreviewUrl: '/api/voices/files/clip-1/audio',
		customVoiceReferenceAudioPath: 'voices/files/clip-1.wav',
		customVoiceSourceFileId: 'source-1',
		customVoiceSourceAudioPath: 'voices/files/source-1.wav',
		customVoiceSourceDurationMs: 30_000,
		customVoiceTrimStartMs: 2_000,
		customVoiceTrimEndMs: 8_000,
		customVoiceTranscript: '参考台词',
		customVoiceSrt: '1\n00:00:00,000 --> 00:00:01,000\n参考台词',
		customVoiceDurationMs: 6_000,
		customVoiceSrtSegmentCount: 1,
		customVoiceTranscriptionId: 'transcription-1',
		customVoiceConfirmed: true,
		customVoiceBusy: false,
		customVoiceError: '',
		customVoiceQualityWarnings: ['峰值较低'],
		...overrides
	};
}

describe('ReferenceAudioDraft isolation', () => {
	it('creates independent editor instances including nested arrays and objects', () => {
		const first = createReferenceAudioDraft('audio-1', {
			source: { fileName: 'first.wav', durationMs: 5_000 },
			qualityWarnings: ['first warning']
		});
		const second = createReferenceAudioDraft('audio-2', {
			source: { fileName: 'second.wav', durationMs: 9_000 }
		});

		first.source.fileName = 'changed.wav';
		first.qualityWarnings.push('new warning');

		expect(second.source.fileName).toBe('second.wav');
		expect(second.qualityWarnings).toEqual([]);
		expect(first.draftId).not.toBe(second.draftId);
	});
});

describe('reference audio trim boundaries', () => {
	it('clamps out-of-range selections and preserves the 100ms minimum', () => {
		expect(normalizeReferenceAudioTrim(10_000, -400, 20_000)).toEqual({ startMs: 0, endMs: 10_000 });
		expect(normalizeReferenceAudioTrim(10_000, 9_990, 9_995)).toEqual({ startMs: 9_900, endMs: 10_000 });
		expect(normalizeReferenceAudioTrim(60, 50, 51)).toEqual({ startMs: 0, endMs: 60 });
		expect(normalizeReferenceAudioTrim(null, 500, 1_500)).toEqual({ startMs: 500, endMs: 1_500 });
		expect(normalizeReferenceAudioTrim(0, 0, 10)).toEqual({ startMs: null, endMs: null });
	});

	it('invalidates only the edited draft processed result', () => {
		const first = referenceAudioDraftFromLegacyState(legacyState(), 'audio-1');
		const second = referenceAudioDraftFromLegacyState(legacyState({ customVoiceFileId: 'clip-2' }), 'audio-2');

		const edited = withReferenceAudioTrim(first, 3_000, 7_000);

		expect(edited.source.fileId).toBe('source-1');
		expect(edited.clip.fileId).toBe('');
		expect(edited.transcript.text).toBe('');
		expect(edited.confirmed).toBe(false);
		expect(edited.selectionDirty).toBe(true);
		expect(second.clip.fileId).toBe('clip-2');
		expect(second.transcript.text).toBe('参考台词');
	});
});

describe('ReferenceAudioDraft snapshot and legacy bridge', () => {
	it('round-trips all current single-reference store fields without sharing arrays', () => {
		const legacy = legacyState();
		const draft = referenceAudioDraftFromLegacyState(legacy);
		const restored = restoreReferenceAudioDraft(snapshotReferenceAudioDraft(draft));
		const patch = legacyCustomVoicePatchFromDraft(restored);

		expect(patch).toEqual(legacy);
		restored.qualityWarnings.push('restored-only');
		expect(draft.qualityWarnings).toEqual(['峰值较低']);
		expect(legacy.customVoiceQualityWarnings).toEqual(['峰值较低']);
	});

	it('preserves valid trim metadata when historical source duration is unavailable', () => {
		const legacy = legacyState({ customVoiceSourceDurationMs: null });
		const patch = legacyCustomVoicePatchFromDraft(referenceAudioDraftFromLegacyState(legacy));

		expect(patch.customVoiceSourceDurationMs).toBeNull();
		expect(patch.customVoiceTrimStartMs).toBe(2_000);
		expect(patch.customVoiceTrimEndMs).toBe(8_000);
	});

	it('normalizes invalid legacy trim metadata during recovery', () => {
		const draft = referenceAudioDraftFromLegacyState(
			legacyState({ customVoiceTrimStartMs: 40_000, customVoiceTrimEndMs: -1 })
		);

		expect(draft.trim).toEqual({ startMs: 29_900, endMs: 30_000 });
	});

	it('rejects snapshots from an unsupported future version', () => {
		const snapshot = snapshotReferenceAudioDraft(createReferenceAudioDraft('audio-1'));
		expect(() => restoreReferenceAudioDraft({ ...snapshot, version: 2 } as never)).toThrow(
			'不支持的 ReferenceAudioDraft 快照版本'
		);
	});
});
