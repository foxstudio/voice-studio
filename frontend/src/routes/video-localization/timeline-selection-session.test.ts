import { describe, expect, it } from 'vitest';
import { TimelineSelectionSession } from './timeline-selection-session';

const cue1 = { kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_01' } as const;
const cue2 = { kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_02' } as const;
const clip = { kind: 'audio', trackId: 'dub', itemId: 'clip_01' } as const;

describe('timeline selection session', () => {
	it('deduplicates external selection and keeps the last unique item primary', () => {
		const session = new TimelineSelectionSession([cue1, cue2, cue1]);

		expect(session.items).toEqual([cue1, cue2]);
		expect(session.primary).toEqual(cue2);
	});

	it('adds and removes items without maintaining a second primary state', () => {
		const selected = new TimelineSelectionSession([cue1]).toggle(cue2);
		const removed = selected.toggle(cue2);

		expect(selected.items).toEqual([cue1, cue2]);
		expect(selected.primary).toEqual(cue2);
		expect(removed.items).toEqual([cue1]);
		expect(removed.primary).toEqual(cue1);
	});

	it('can report a passive click without adding it to the edit selection', () => {
		const session = new TimelineSelectionSession([cue1]);

		expect(session.toggle(cue2, { includeWhenMissing: false })).toBe(session);
		expect(session.items).toEqual([cue1]);
	});

	it('removes matching items and clears through immutable session boundaries', () => {
		const session = new TimelineSelectionSession([cue1, cue2, clip]);
		const subtitlesRemoved = session.remove((item) => item.kind === 'subtitle');
		const cleared = subtitlesRemoved.clear();

		expect(subtitlesRemoved.items).toEqual([clip]);
		expect(subtitlesRemoved.primary).toEqual(clip);
		expect(cleared.items).toEqual([]);
		expect(cleared.clear()).toBe(cleared);
	});
});
