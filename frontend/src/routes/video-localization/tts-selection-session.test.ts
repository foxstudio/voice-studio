import { describe, expect, it } from 'vitest';
import {
	ensureDirectTtsTargetSelection,
	resolveTtsSelectionSession,
	ttsSelectionAnchorIsPassive,
	ttsSelectionIsContiguous,
	ttsSourceSelectionAdvisory,
	ttsSourceSelectionRange,
	updateTtsSelectionSession
} from './tts-selection-session';

const cues = [
	{ cue_id: 'cue_1', start_ms: 0, end_ms: 1000 },
	{ cue_id: 'cue_2', start_ms: 1000, end_ms: 2000 },
	{ cue_id: 'cue_3', start_ms: 2000, end_ms: 3000 }
] as any[];

const subtitles = [
	{ subtitle_id: 'localized_1', start_ms: 0, end_ms: 1000, text: '一', source_cue_ids: ['cue_1'] },
	{ subtitle_id: 'localized_2', start_ms: 1000, end_ms: 2000, text: '二', source_cue_ids: ['cue_1', 'cue_2'] },
	{ subtitle_id: 'localized_3', start_ms: 2000, end_ms: 3000, text: '三', linked_cue_id: 'cue_3' }
] as any[];

describe('TTS dual-track selection session', () => {
	it('replaces a stale target when a subtitle is selected directly at another time', () => {
		const stale = resolveTtsSelectionSession({
			anchor: { kind: 'target', itemId: 'localized_1' },
			cues,
			localizedSubtitles: subtitles
		});

		const current = ensureDirectTtsTargetSelection({
			current: stale,
			subtitleId: 'localized_3',
			cues,
			localizedSubtitles: subtitles
		});

		expect(current.anchor).toEqual({ kind: 'target', itemId: 'localized_3' });
		expect(current.localizedSubtitleIds).toEqual(['localized_3']);
		expect(current.sourceCueIds).toEqual(['cue_3']);
	});

	it('keeps every selected localized subtitle when the primary item opens the inspector', () => {
		const stale = resolveTtsSelectionSession({
			anchor: { kind: 'target', itemId: 'localized_3' },
			cues,
			localizedSubtitles: subtitles
		});

		const current = ensureDirectTtsTargetSelection({
			current: stale,
			subtitleId: 'localized_2',
			selectionItems: [
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_1' },
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_2' }
			],
			cues,
			localizedSubtitles: subtitles
		});

		expect(current.localizedSubtitleIds).toEqual(['localized_1', 'localized_2']);
		expect(current.sourceCueIds).toEqual(['cue_1', 'cue_2']);
	});

	it('uses a source anchor to highlight every mapped localization target', () => {
		const session = resolveTtsSelectionSession({
			anchor: { kind: 'source', itemId: 'cue_1' },
			cues,
			localizedSubtitles: subtitles
		});

		expect(session).toMatchObject({
			anchor: { kind: 'source', itemId: 'cue_1' },
			sourceCueIds: ['cue_1'],
			localizedSubtitleIds: ['localized_1', 'localized_2']
		});
	});

	it('uses linked_cue_id only when authoritative source_cue_ids are empty', () => {
		const session = resolveTtsSelectionSession({
			anchor: { kind: 'target', itemId: 'localized_stale' },
			cues,
			localizedSubtitles: [{
				subtitle_id: 'localized_stale', start_ms: 0, end_ms: 1000, text: '旧主映射',
				source_cue_ids: ['cue_2'], linked_cue_id: 'cue_1'
			}] as any[]
		});

		expect(session.sourceCueIds).toEqual(['cue_2']);
	});

	it('allows excluding and restoring a passive target without moving the source anchor', () => {
		const initial = resolveTtsSelectionSession({
			anchor: { kind: 'source', itemId: 'cue_1' }, cues, localizedSubtitles: subtitles
		});
		expect(ttsSelectionAnchorIsPassive(initial, { kind: 'target', itemId: 'localized_2' })).toBe(true);
		const excluded = updateTtsSelectionSession({
			current: initial,
			clicked: { kind: 'target', itemId: 'localized_2' },
			selectionItems: [],
			additive: true,
			cues,
			localizedSubtitles: subtitles
		});
		expect(excluded.anchor).toEqual({ kind: 'source', itemId: 'cue_1' });
		expect(excluded.localizedSubtitleIds).toEqual(['localized_1']);
		expect(ttsSelectionAnchorIsPassive(excluded, { kind: 'target', itemId: 'localized_2' })).toBe(true);
		expect(ttsSelectionAnchorIsPassive(excluded, { kind: 'target', itemId: 'localized_3' })).toBe(false);

		const restored = updateTtsSelectionSession({
			current: excluded,
			clicked: { kind: 'target', itemId: 'localized_2' },
			selectionItems: [],
			additive: true,
			cues,
			localizedSubtitles: subtitles
		});
		expect(restored.localizedSubtitleIds).toEqual(['localized_1', 'localized_2']);
	});

	it('moves or clears the anchor when its explicit item is toggled off', () => {
		const initial = resolveTtsSelectionSession({
			anchor: { kind: 'source', itemId: 'cue_2' },
			selectionItems: [
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_1' },
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_2' }
			],
			cues,
			localizedSubtitles: subtitles
		});
		const moved = updateTtsSelectionSession({
			current: initial,
			clicked: { kind: 'source', itemId: 'cue_2' },
			selectionItems: [{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_1' }],
			additive: true,
			cues,
			localizedSubtitles: subtitles
		});
		expect(moved.anchor).toEqual({ kind: 'source', itemId: 'cue_1' });

		const cleared = updateTtsSelectionSession({
			current: moved,
			clicked: { kind: 'source', itemId: 'cue_1' },
			selectionItems: [],
			additive: true,
			cues,
			localizedSubtitles: subtitles
		});
		expect(cleared.anchor).toBeNull();
		expect(cleared.localizedSubtitleIds).toEqual([]);
	});

	it('starts a fresh combination for a non-additive selection at another time', () => {
		const first = resolveTtsSelectionSession({ anchor: { kind: 'source', itemId: 'cue_1' }, cues, localizedSubtitles: subtitles });
		const second = updateTtsSelectionSession({
			current: first,
			clicked: { kind: 'source', itemId: 'cue_3' },
			selectionItems: [{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_3' }],
			additive: false,
			cues,
			localizedSubtitles: subtitles
		});
		expect(second.sourceCueIds).toEqual(['cue_3']);
		expect(second.localizedSubtitleIds).toEqual(['localized_3']);
	});

	it('keeps a source anchor while explicitly adding an unrelated target at another time', () => {
		const first = resolveTtsSelectionSession({ anchor: { kind: 'source', itemId: 'cue_1' }, cues, localizedSubtitles: subtitles });
		const combined = updateTtsSelectionSession({
			current: first,
			clicked: { kind: 'target', itemId: 'localized_3' },
			selectionItems: [
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_1' },
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_3' }
			],
			additive: true,
			cues,
			localizedSubtitles: subtitles
		});

		expect(combined.anchor).toEqual({ kind: 'source', itemId: 'cue_1' });
		expect(combined.explicitSourceCueIds).toEqual(['cue_1']);
		expect(combined.explicitLocalizedSubtitleIds).toEqual(['localized_3']);
		expect(combined.sourceCueIds).toEqual(['cue_1']);
		expect(combined.localizedSubtitleIds).toEqual(['localized_3']);
		expect(combined.mappedSourceCueIds).toEqual(['cue_3']);
		expect(combined.mappedLocalizedSubtitleIds).toEqual(['localized_1', 'localized_2']);
	});

	it('describes source selections that should be adjusted after opening generation', () => {
		const discontinuous = resolveTtsSelectionSession({
			anchor: { kind: 'source', itemId: 'cue_3' },
			selectionItems: [
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_1' },
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_3' }
			],
			cues,
			localizedSubtitles: subtitles
		});
		expect(ttsSourceSelectionAdvisory(discontinuous, cues)).toContain('进入语音合成页后');

		const crossSpeakerCues = [
			{ ...cues[0], speaker_id: 'speaker_a' },
			{ ...cues[1], speaker_id: 'speaker_b' },
			cues[2]
		];
		const crossSpeaker = resolveTtsSelectionSession({
			anchor: { kind: 'source', itemId: 'cue_2' },
			selectionItems: [
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_1' },
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_2' }
			],
			cues: crossSpeakerCues,
			localizedSubtitles: subtitles
		});
		expect(ttsSourceSelectionAdvisory(crossSpeaker, crossSpeakerCues)).toContain('多个说话人');

		const unknownAndKnownCues = [
			{ ...cues[0], speaker_id: null },
			{ ...cues[1], speaker_id: 'speaker_a' },
			cues[2]
		];
		const unknownAndKnown = resolveTtsSelectionSession({
			anchor: { kind: 'source', itemId: 'cue_2' },
			selectionItems: [
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_1' },
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_2' }
			],
			cues: unknownAndKnownCues,
			localizedSubtitles: subtitles
		});
		expect(ttsSourceSelectionAdvisory(unknownAndKnown, unknownAndKnownCues)).toBeNull();
	});

	it('uses the complete selected ASR span as the reference range', () => {
		const session = resolveTtsSelectionSession({
			anchor: { kind: 'source', itemId: 'cue_2' },
			selectionItems: [
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_1' },
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_2' }
			],
			cues,
			localizedSubtitles: subtitles
		});

		expect(ttsSourceSelectionRange(session, cues)).toEqual({
			startMs: 0,
			endMs: 2000
		});
	});

	it('keeps target multi-selection explicit and derives all valid source cues', () => {
		const session = resolveTtsSelectionSession({
			anchor: { kind: 'target', itemId: 'localized_2' },
			selectionItems: [
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_1' },
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_2' }
			],
			cues,
			localizedSubtitles: subtitles
		});

		expect(session.sourceCueIds).toEqual(['cue_1', 'cue_2']);
		expect(session.localizedSubtitleIds).toEqual(['localized_1', 'localized_2']);
		expect(ttsSelectionIsContiguous(session, subtitles)).toBe(true);
	});

	it('filters dangling source ids instead of creating a false highlight', () => {
		const session = resolveTtsSelectionSession({
			anchor: { kind: 'target', itemId: 'localized_dangling' },
			cues,
			localizedSubtitles: [{
				subtitle_id: 'localized_dangling', start_ms: 0, end_ms: 1000, text: '悬空', source_cue_ids: ['missing']
			}] as any[]
		});

		expect(session.sourceCueIds).toEqual([]);
		expect(session.localizedSubtitleIds).toEqual(['localized_dangling']);
	});
});
