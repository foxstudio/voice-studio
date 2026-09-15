import { describe, expect, it } from 'vitest';
import { buildTtsSubmissionSnapshot } from './tts-submission-snapshot';

const subtitle = (id: string, startMs: number, endMs: number) => ({
	subtitle_id: id,
	start_ms: startMs,
	end_ms: endMs,
	text: `台词 ${id}`,
	tts_text: null,
	linked_cue_id: `cue_${id}`,
	source_cue_ids: [`cue_${id}`],
	quality_flags: []
});

describe('TTS submission snapshot', () => {
	it('freezes a contiguous multi-subtitle selection into one stable group', () => {
		const selected = [subtitle('0001', 1000, 1800), subtitle('0002', 1800, 2700)];
		const snapshot = buildTtsSubmissionSnapshot({
			clientId: 'local-1',
			selectedSubtitle: selected[0],
			selectedSubtitles: selected,
			selectedClip: null,
			groupedClipSegmentId: ''
		});

		expect(snapshot?.segmentId).toBe('group_0001_0002_2');
		expect(snapshot?.sourceCueIds).toEqual(['cue_0001', 'cue_0002']);
		expect(snapshot?.localizedSubtitleIds).toEqual(['0001', '0002']);
		expect(snapshot?.startMs).toBe(1000);
		expect(snapshot?.endMs).toBe(2700);
		selected[0].text = '用户随后修改的文字';
		expect(snapshot?.subtitles[0].text).toBe('台词 0001');
	});

	it('freezes explicit source and localized ids from the TTS selection session', () => {
		const selected = [subtitle('0001', 1000, 1800), subtitle('0002', 1800, 2700)];
		const snapshot = buildTtsSubmissionSnapshot({
			clientId: 'session-1',
			selectedSubtitle: selected[1],
			selectedSubtitles: selected,
			selectedClip: null,
			groupedClipSegmentId: '',
			sourceCueIds: ['cue_explicit_1', 'cue_explicit_2'],
			localizedSubtitleIds: ['0001', '0002']
		});

		expect(snapshot?.sourceCueIds).toEqual(['cue_explicit_1', 'cue_explicit_2']);
		expect(snapshot?.localizedSubtitleIds).toEqual(['0001', '0002']);
	});

	it('captures the selected replacement clip instead of reading future selection state', () => {
		const selected = subtitle('0003', 3000, 4000);
		const snapshot = buildTtsSubmissionSnapshot({
			clientId: 'local-2',
			selectedSubtitle: selected,
			selectedSubtitles: [],
			selectedClip: {
				clip_id: 'dub-3',
				track_id: 'dub',
				subtitle_id: '0003',
				cue_id: 'cue_0003',
				start_ms: 3100,
				end_ms: 4200,
				dub_lane: 2
			},
			groupedClipSegmentId: ''
		});

		expect(snapshot?.targetClip?.clip_id).toBe('dub-3');
		expect(snapshot?.targetClip?.dub_lane).toBe(2);
	});

	it('preserves an explicit non-contiguous target selection in timeline order', () => {
		const selected = [subtitle('0001', 1000, 1800), subtitle('0003', 3000, 3800)];
		const snapshot = buildTtsSubmissionSnapshot({
			clientId: 'local-3',
			selectedSubtitle: selected[0],
			selectedSubtitles: selected,
			selectedClip: null,
			groupedClipSegmentId: ''
		});

		expect(snapshot?.localizedSubtitleIds).toEqual(['0001', '0003']);
		expect(snapshot?.subtitles.map((item) => item.subtitle_id)).toEqual(['0001', '0003']);
		expect(snapshot?.summary).toBe('台词 0001 台词 0003');
		expect(snapshot?.startMs).toBe(1000);
		expect(snapshot?.endMs).toBe(3800);
	});
});
