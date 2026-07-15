import { describe, expect, it, vi } from 'vitest';
import { buildTimelineContextMenuItems, type TimelineContextMenuTarget } from './timeline-context-menu';

describe('timeline context menu', () => {
	const context = (overrides = {}) => ({
		itemCount: 8,
		locked: false,
		canGenerateAsr: true,
		asrBusy: false,
		trackBusy: false,
		asrUnavailableReason: '',
		hasSelectionPoints: false,
		onGenerateAsr: vi.fn(),
		onClearSubtitleTrack: vi.fn(),
		onDeleteSubtitleItem: vi.fn(),
		onDeleteAudioClip: vi.fn(),
		onFillSubtitleGaps: vi.fn(),
		onSetSelectionStart: vi.fn(),
		onSetSelectionEnd: vi.fn(),
		onClearSelection: vi.fn(),
		...overrides
	});

	it('registers generate and clear commands for empty ASR track space', async () => {
		const clear = vi.fn();
		const target: TimelineContextMenuTarget = {
			kind: 'track',
			hit: 'empty',
			trackId: 'subtitles',
			subtitleTrack: 'asr',
			timeMs: 1200
		};
		const items = buildTimelineContextMenuItems(target, context({ onClearSubtitleTrack: clear }));

		expect(items).toHaveLength(6);
		expect(items[0]).toMatchObject({ id: 'generate-asr-subtitles', label: '重新生成 ASR 字幕', disabled: false });
		expect(items[1]).toMatchObject({ id: 'fill-asr-subtitle-gaps', separatorBefore: true });
		expect(items[2]).toMatchObject({ id: 'clear-asr-subtitle-track', tone: 'danger', disabled: false });
		expect(items[3]).toMatchObject({ id: 'set-selection-start', separatorBefore: true });
		await items[2].onSelect();
		expect(clear).toHaveBeenCalledWith('asr');
	});

	it('keeps subtitle and selection commands available over a subtitle clip', () => {
		const items = buildTimelineContextMenuItems(
			{ kind: 'subtitle-clip', trackId: 'subtitles', subtitleTrack: 'asr', itemId: 'cue_0001', timeMs: 1200 },
			context({ itemCount: 1 })
		);
		expect(items.map((item) => item.id)).toEqual([
			'delete-asr-subtitle-cue_0001',
			'generate-asr-subtitles',
			'fill-asr-subtitle-gaps',
			'clear-asr-subtitle-track',
			'set-selection-start',
			'set-selection-end',
			'clear-selection'
		]);
	});

	it('keeps localized track cleanup and short-gap commands available', () => {
		const target: TimelineContextMenuTarget = {
			kind: 'track',
			hit: 'empty',
			trackId: 'localizedSubtitles',
			subtitleTrack: 'localized',
			timeMs: 0
		};
		const items = buildTimelineContextMenuItems(target, context({ itemCount: 4 }));
		expect(items).toHaveLength(5);
		expect(items[0].id).toBe('fill-localized-subtitle-gaps');
		expect(items[1].id).toBe('clear-localized-subtitle-track');
	});

	it('deletes the selected audio clip without exposing subtitle commands', async () => {
		const remove = vi.fn();
		const items = buildTimelineContextMenuItems(
			{ kind: 'audio-clip', trackId: 'vocals', itemId: 'clip_01', timeMs: 900 },
			context({ itemCount: 0, onDeleteAudioClip: remove })
		);
		expect(items[0]).toMatchObject({ id: 'delete-audio-clip-clip_01', tone: 'danger' });
		await items[0].onSelect();
		expect(remove).toHaveBeenCalledWith('clip_01');
	});

	it('adds selection commands to every audio track and uses the pointer time', async () => {
		const setStart = vi.fn();
		const setEnd = vi.fn();
		const clear = vi.fn();
		const items = buildTimelineContextMenuItems(
			{ kind: 'track', hit: 'empty', trackId: 'vocals', timeMs: 4321 },
			context({ hasSelectionPoints: true, onSetSelectionStart: setStart, onSetSelectionEnd: setEnd, onClearSelection: clear })
		);
		expect(items.map((item) => item.id)).toEqual(['set-selection-start', 'set-selection-end', 'clear-selection']);
		await items[0].onSelect();
		await items[1].onSelect();
		await items[2].onSelect();
		expect(setStart).toHaveBeenCalledWith(4321);
		expect(setEnd).toHaveBeenCalledWith(4321);
		expect(clear).toHaveBeenCalledOnce();
	});

	it('keeps ASR generation visible with a reason when its source is unavailable', () => {
		const target: TimelineContextMenuTarget = {
			kind: 'track', hit: 'empty', trackId: 'subtitles', subtitleTrack: 'asr', timeMs: 0
		};
		const items = buildTimelineContextMenuItems(target, context({ itemCount: 0, canGenerateAsr: false, asrUnavailableReason: '人声轨为空' }));
		expect(items[0]).toMatchObject({ id: 'generate-asr-subtitles', label: '从人声轨生成 ASR 字幕', disabled: true, description: '人声轨为空' });
		expect(items[1].disabled).toBe(true);
	});

	it('disables ASR mutation commands while transcription is running', () => {
		const target: TimelineContextMenuTarget = {
			kind: 'track', hit: 'empty', trackId: 'subtitles', subtitleTrack: 'asr', timeMs: 0
		};
		const items = buildTimelineContextMenuItems(target, context({ asrBusy: true }));
		const asrMutations = items.filter((item) => item.id === 'generate-asr-subtitles' || item.id === 'clear-asr-subtitle-track');
		expect(asrMutations.every((item) => item.disabled)).toBe(true);
		expect(items.find((item) => item.id === 'set-selection-start')?.disabled).not.toBe(true);
		expect(items.find((item) => item.id === 'set-selection-end')?.disabled).not.toBe(true);
	});
});
