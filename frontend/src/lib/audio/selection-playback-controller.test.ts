import { describe, expect, it } from 'vitest';
import {
	SelectionPlaybackController,
	normalizeSelectionRange,
	selectionContainsTime
} from './selection-playback-controller';

const range = { start: 5, end: 10 };

describe('selection playback controller', () => {
	it('starts from the current playhead when it is inside the selection', () => {
		const controller = new SelectionPlaybackController();

		expect(controller.start(range, true, 7.25)).toMatchObject({
			phase: 'playing',
			loopActive: true,
			position: 7.25,
			seekTo: 7.25,
			shouldPlay: true
		});
	});

	it.each<[string, number]>([
		['before IN', 2],
		['at OUT', 10],
		['after OUT', 12]
	])('starts from IN when the current playhead is %s', (_label, currentTime) => {
		const controller = new SelectionPlaybackController();

		expect(controller.start(range, false, currentTime)).toMatchObject({
			phase: 'playing',
			position: 5,
			seekTo: 5,
			shouldPlay: true
		});
	});

	it('keeps playing outside the selection and arms the loop only after entering it', () => {
		const controller = new SelectionPlaybackController();
		controller.start(range, true);
		controller.seekDuringPlayback(2, range, true);

		expect(controller.tick(3, range, true)).toMatchObject({
			loopActive: false,
			position: 3,
			seekTo: null
		});
		expect(controller.tick(5.2, range, true)).toMatchObject({
			loopActive: true,
			position: 5.2,
			seekTo: null
		});
		expect(controller.tick(10, range, true)).toMatchObject({
			loopActive: true,
			position: 5,
			seekTo: 5
		});
	});

	it('does not move the live playhead while an OUT handle is held', () => {
		const controller = new SelectionPlaybackController();
		controller.start(range, true);
		controller.beginSelectionEdit(8, true);

		expect(controller.tick(11.25, range, true)).toMatchObject({
			phase: 'editing',
			loopActive: false,
			position: 11.25,
			seekTo: null
		});
	});

	it('continues looping after release when the live playhead remains inside the final range', () => {
		const controller = new SelectionPlaybackController();
		controller.start(range, true);
		controller.beginSelectionEdit(8, true);

		expect(controller.finishSelectionEdit(9.25, { start: 7, end: 11 }, true, true)).toMatchObject({
			phase: 'playing',
			loopActive: true,
			position: 9.25,
			seekTo: null
		});
		expect(controller.tick(11, { start: 7, end: 11 }, true)).toMatchObject({
			position: 7,
			seekTo: 7
		});
	});

	it('continues forward without seeking after release when the playhead is outside the final range', () => {
		const controller = new SelectionPlaybackController();
		controller.start(range, true);
		controller.beginSelectionEdit(8, true);

		expect(controller.finishSelectionEdit(11.25, { start: 5, end: 9 }, true, true)).toMatchObject({
			phase: 'playing',
			loopActive: false,
			position: 11.25,
			seekTo: null
		});
		expect(controller.tick(12, { start: 5, end: 9 }, true)).toMatchObject({
			loopActive: false,
			position: 12,
			seekTo: null
		});
	});

	it('does not resume a selection edit that reached the source end', () => {
		const controller = new SelectionPlaybackController();
		controller.start(range, true);
		controller.beginSelectionEdit(8, true);
		controller.sourceEnded(14, range, true);

		expect(controller.finishSelectionEdit(14, range, true, false)).toMatchObject({
			phase: 'stopped',
			loopActive: false,
			position: 14,
			seekTo: null
		});
		expect(controller.start(range, true)).toMatchObject({
			phase: 'playing',
			position: 5,
			seekTo: 5
		});
	});

	it('plays the selection once and leaves the playhead at OUT when looping is disabled', () => {
		const controller = new SelectionPlaybackController();
		controller.start(range, false);

		expect(controller.tick(10.5, range, false)).toMatchObject({
			phase: 'stopped',
			loopActive: false,
			position: 10,
			seekTo: 10,
			shouldPause: true
		});
	});

	it('pauses without moving the playhead and resumes from that point', () => {
		const controller = new SelectionPlaybackController();
		controller.start(range, false, 6.5);

		expect(controller.pauseAt(7.4)).toMatchObject({
			phase: 'stopped',
			position: 7.4,
			seekTo: null,
			shouldPause: false
		});
		expect(controller.start(range, false, 7.4)).toMatchObject({
			phase: 'playing',
			position: 7.4,
			seekTo: 7.4,
			shouldPlay: true
		});
	});
});

describe('selection range helpers', () => {
	it('normalizes an unordered finite range and rejects an empty range', () => {
		expect(normalizeSelectionRange(10, 5)).toEqual(range);
		expect(normalizeSelectionRange(5, 5)).toBeNull();
		expect(normalizeSelectionRange(Number.NaN, 5)).toBeNull();
	});

	it('treats IN and OUT as part of the settled selection', () => {
		expect(selectionContainsTime(range, 5)).toBe(true);
		expect(selectionContainsTime(range, 10)).toBe(true);
		expect(selectionContainsTime(range, 10.01)).toBe(false);
	});
});
