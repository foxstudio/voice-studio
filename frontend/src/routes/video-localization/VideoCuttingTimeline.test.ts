import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import ts from 'typescript';
import * as timelineViewport from './timeline-viewport';

const source = readFileSync(new URL('./VideoCuttingTimeline.svelte', import.meta.url), 'utf8');

// Execute the real component handler with its UI dependencies isolated.
function timelineHandler(name: string, dependencies: Record<string, unknown>) {
	const script = source.slice(source.indexOf('<script lang="ts">') + '<script lang="ts">'.length, source.indexOf('</script>'));
	const parsed = ts.createSourceFile('timeline.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
	const handler = parsed.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name);
	if (!handler) throw new Error(`Missing timeline handler ${name}`);
	const emitted = ts.transpileModule(handler.getText(parsed), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
	return new Function(...Object.keys(dependencies), `${emitted}; return ${name};`)(...Object.values(dependencies));
}

function subtitleClickHarness() {
	const selected: string[] = [];
	let hitTests = 0;
	const dependencies = {
		dragState: null,
		subtitleCueAtPointer: () => { hitTests += 1; return { subtitle_id: 'next-cue' }; },
		subtitleItemId: (cue: { subtitle_id: string }) => cue.subtitle_id,
		timelineItemSelected: () => false,
		selectSubtitleTimelineItem: (_track: string, id: string) => selected.push(id)
	};
	const click = timelineHandler('selectSubtitleTimelineItemAtPointer', dependencies);
	return { click, selected, hitTests: () => hitTests };
}

function subtitlePointerHarness(locked = false) {
	const cue = { subtitle_id: 'pressed-cue', start_ms: 1000, end_ms: 3000 };
	const selections: unknown[][] = [];
	const gestures: Array<{ kind: string; mode: string; itemId: string }> = [];
	const session = { begin: (gesture: typeof gestures[number]) => { gestures.push(gesture); return session; } };
	const down = timelineHandler('startCueDrag', {
		subtitleCueAtPointer: () => cue,
		subtitleItemId: () => cue.subtitle_id,
		selectSubtitleTimelineItem: (...args: unknown[]) => selections.push(args),
		trackInteractionLocked: () => locked,
		cueLiveTime: () => cue,
		draft: { localized_subtitles: [cue] },
		subtitleTimelineLimitMs: 10_000,
		timelineDurationMs: 10_000,
		subtitleCueDragBounds: () => ({ minStartMs: 0, maxEndMs: 10_000 }),
		selectedSubtitleMoveGroup: () => null,
		suspendAutoFollow: () => {},
		gestureSession: session
	});
	const event = { button: 0, pointerId: 1, clientX: 100, preventDefault() {}, stopPropagation() {}, currentTarget: { setPointerCapture() {} } };
	return { down: (mode: string, modifiers = {}) => down({ ...event, ...modifiers }, cue, mode, 'localized'), selections, gestures };
}

describe('subtitle click lifecycle', () => {
	it('does not reselect after pointerdown when opening the inspector shifts the timeline', () => {
		const harness = subtitleClickHarness();
		harness.click({ detail: 1 }, 'localized', { subtitle_id: 'pressed-cue' });
		expect(harness.selected).toEqual([]);
		expect(harness.hitTests()).toBe(0);
	});

	it('selects the focused subtitle for keyboard activation without treating clientX zero as timeline time', () => {
		const harness = subtitleClickHarness();
		harness.click({ detail: 0, clientX: 0 }, 'localized', { subtitle_id: 'focused-cue' });
		expect(harness.selected).toEqual(['focused-cue']);
		expect(harness.hitTests()).toBe(0);
	});

	it('leaves additive mouse selection to pointerdown and the second click to the doubleclick handler', () => {
		const harness = subtitleClickHarness();
		for (const event of [{ detail: 1, ctrlKey: true }, { detail: 1, metaKey: true }, { detail: 2 }]) {
			harness.click(event, 'localized', { subtitle_id: 'pressed-cue' });
		}
		expect(harness.selected).toEqual([]);
	});

	it.each(['move', 'trim-start', 'trim-end'])('still starts real %s gestures and selects on pointerdown', (mode) => {
		const harness = subtitlePointerHarness();
		harness.down(mode);
		expect(harness.selections).toEqual([['localized', 'pressed-cue', true]]);
		expect(harness.gestures).toMatchObject([{ kind: 'cue-drag', mode, itemId: 'pressed-cue' }]);
	});

	it('keeps locked subtitles selectable without starting an edit gesture', () => {
		const harness = subtitlePointerHarness(true);
		harness.down('move');
		expect(harness.selections).toEqual([['localized', 'pressed-cue']]);
		expect(harness.gestures).toEqual([]);
	});

	it.each(['ctrlKey', 'metaKey'])('preserves %s additive pointer selection without starting a drag', (modifier) => {
		const harness = subtitlePointerHarness();
		harness.down('move', { [modifier]: true });
		expect(harness.selections).toEqual([['localized', 'pressed-cue', true, true]]);
		expect(harness.gestures).toEqual([]);
	});

	it('keeps razor splitting on pointerdown without a later click changing selection', () => {
		const splits: unknown[][] = [];
		const cut = timelineHandler('cutSubtitleAtPointer', {
			activeTool: 'razor',
			razorSplitMs: () => 2000,
			draft: { localized_subtitles: [{ subtitle_id: 'pressed-cue', start_ms: 1000, end_ms: 3000 }] },
			trackInteractionLocked: () => false,
			MIN_SUBTITLE_DURATION_MS: 80,
			onSplitLocalizedSubtitle: (...args: unknown[]) => splits.push(args)
		});
		expect(cut({ preventDefault() {}, stopPropagation() {} }, 'localized', 'pressed-cue')).toBe(true);
		expect(splits).toEqual([['pressed-cue', 2000]]);
		const harness = subtitleClickHarness();
		harness.click({ detail: 1 }, 'localized', { subtitle_id: 'pressed-cue' });
		expect(harness.selected).toEqual([]);
	});

	it('retains doubleclick selection-range playback', () => {
		const selections: unknown[][] = [];
		const ranges: unknown[] = [];
		const cue = { subtitle_id: 'pressed-cue', start_ms: 1000, end_ms: 3000 };
		const doubleclick = timelineHandler('playSubtitleTimelineItem', {
			subtitleCueAtPointer: () => cue,
			subtitleItemId: () => cue.subtitle_id,
			cueLiveTime: () => cue,
			selectSubtitleTimelineItem: (...args: unknown[]) => selections.push(args),
			rangeStartMs: null,
			rangeEndMs: null,
			MIN_RANGE_DURATION_MS: 80,
			onSelectionRangeCommit: (range: unknown) => ranges.push(range)
		});
		doubleclick({ detail: 2, preventDefault() {}, stopPropagation() {} }, 'localized', cue);
		expect(selections).toEqual([['localized', 'pressed-cue', true]]);
		expect(ranges).toEqual([{ startMs: 1000, endMs: 3000 }]);
	});
});

describe('timeline viewport save scheduling', () => {
	it('keeps native scrolling within the logical timeline instead of overflowing end labels', () => {
		const update = timelineHandler('updateTimelineViewport', {
			trackCanvasEl: null, timelineScrollLeft: 56561, timelineViewportWidth: 494,
			timelineZoom: 171.5, ...timelineViewport
		});
		const element = { clientWidth: 494, scrollWidth: 84751, scrollLeft: 84257 };
		update(element);
		expect(element.scrollLeft).toBe(84227);
	});

	function harness() {
		const saved: number[] = [];
		const timers: Array<{ callback: () => void; delay: number }> = [];
		const schedule = timelineHandler('scheduleTimelineViewportChange', {
			trackCanvasEl: null,
			onTimelineViewportChange: (startMs: number) => saved.push(startMs),
			timelineViewportRestoreReady: true,
			timelineViewportRestored: true,
			pendingViewportStartMs: 0,
			viewportChangeTimer: null,
			viewportStartMs: timelineHandler('viewportStartMs', {
				trackCanvasEl: null,
				timelineDurationMs: 10_000,
				timelineZoom: 10,
				timelineContentPixelWidth: timelineViewport.timelineContentPixelWidth
			}),
			setTimeout: (callback: () => void, delay: number) => { timers.push({ callback, delay }); return timers.length; }
		});
		return { saved, timers, schedule };
	}

	it.each([
		{ clientWidth: 0, scrollWidth: 1000, scrollLeft: 0 },
		{ clientWidth: 700, scrollWidth: 0, scrollLeft: 0 },
		{ clientWidth: 0, scrollWidth: 0, scrollLeft: 0 }
	])('does not schedule a zero position from hidden geometry %j', (element) => {
		const state = harness();
		state.schedule(element);
		expect(state.timers).toEqual([]);
		expect(state.saved).toEqual([]);
	});

	it('saves the valid viewport after 120ms without a later hidden measurement overwriting it', () => {
		const state = harness();
		state.schedule({ clientWidth: 1000, scrollWidth: 10_000, scrollLeft: 4000 });
		expect(state.timers).toHaveLength(1);
		expect(state.timers[0].delay).toBe(120);
		state.schedule({ clientWidth: 0, scrollWidth: 0, scrollLeft: 0 });
		state.timers[0].callback();
		expect(state.saved).toEqual([4000]);
		expect(state.timers).toHaveLength(1);
	});

	it('persists time using the true content width rather than overflowing end labels', () => {
		const startMs = timelineHandler('viewportStartMs', {
			trackCanvasEl: null,
			timelineDurationMs: 6000,
			timelineZoom: 171.5,
			timelineContentPixelWidth: timelineViewport.timelineContentPixelWidth
		});
		expect(startMs({ clientWidth: 494, scrollWidth: 84_751, scrollLeft: 56_561 })).toBe(4006);
	});
});

describe('VideoCuttingTimeline toolbar and meter contracts', () => {
	it('hides persisted media clips when the resolved asset is unavailable', () => {
		expect(source).toContain("if (!mediaAssetAvailable(mediaHealth, 'source_audio')) grouped.original = []");
		expect(source).toContain("if (!mediaAssetAvailable(mediaHealth, 'vocals')) grouped.vocals = []");
		expect(source).toContain("if (!mediaAssetAvailable(mediaHealth, 'background')) grouped.background = []");
	});

	it('renders the current and content-end timecodes as independent edge-aligned values', () => {
		expect(source).toContain('class="time-current"');
		expect(source).toContain('class="time-total"');
		expect(source).toMatch(/\.track-meter-head \.time-readout \{[\s\S]*?left: 6px;[\s\S]*?right: 6px;[\s\S]*?justify-content: space-between;/);
		expect(source).toMatch(/\.track-meter-head \.time-readout > span \{[\s\S]*?border: 0;[\s\S]*?background: rgba\(7, 12, 16, 0\.46\);/);
		expect(source).toMatch(/\.track-meter-head \.time-current \{[\s\S]*?font-weight: 800;[\s\S]*?color: rgba\(238, 248, 250, 0\.96\);/);
	});

	it('routes the generic delete button through the multi-selection action', () => {
		expect(source).toContain('aria-label="删除"');
		expect(source).toContain('onclick={() => deleteSelectedTimelineItems()}');
		expect(source).toContain('快捷键：E / Delete / Backspace');
	});

	it('uses a deletion-specific lock so an active TTS clip can stop and delete', () => {
		expect(source).toContain("import { timelineItemDeletionBlocked } from './timeline-deletion-policy'");
		expect(source).toContain('!selectionContainsDeletionBlockedItem(selectedTimelineItems)');
		expect(source).toContain('selectionContainsDeletionBlockedItem(contextMenuDeletionItems(target))');
		expect(source).toMatch(/async function deleteSelectedTimelineItems[\s\S]*?selectionContainsDeletionBlockedItem\(deletable\)/);
	});

	it('keeps parent-driven semantic groups visually synchronized with the timeline selection', () => {
		expect(source).toContain('timelineSelectionItems = []');
		expect(source).toContain(
			'const selectionSession = $derived(new TimelineSelectionSession(timelineSelectionItems))'
		);
		expect(source).toContain('const selectedTimelineItem = $derived(selectionSession.primary)');
		expect(source).toContain('const selectedTimelineItems = $derived(selectionSession.items)');
		expect(source).not.toContain('untrack');
	});

	it('leaves project selection resets to the parent owner', () => {
		const effectStart = source.indexOf('\t$effect(() => {\n\t\tprojectId;');
		const effectEnd = source.indexOf('\n\t});', effectStart);
		const projectResetEffect = source.slice(effectStart, effectEnd);
		const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
		expect(projectResetEffect).not.toContain('selectionSession');
		expect(projectResetEffect).not.toContain('onTimelineSelectionChange');
		expect(pageSource).toMatch(
			/async function loadDraft[\s\S]*?timelineSelectionItems = \[\];[\s\S]*?ttsSelectionSession = \{ \.\.\.EMPTY_TTS_SELECTION_SESSION \};/
		);
	});

	it('derives membership from the parent value and emits the next selection', () => {
		expect(source).toContain('const nextSelection = selectionSession.set(items)');
		expect(source).toContain('const nextItems = nextSelection.items');
		expect(source).toContain('onTimelineSelectionChange?.(nextItems)');
		expect(source).toContain('const nextSelection = selectionSession.toggle(item');
		expect(source).toContain('onTimelineSelectionChange?.([])');
		expect(source).not.toMatch(/^\s*selectionSession\s*=/m);
		expect(source).not.toContain('selectedTimelineItem = item');
		expect(source).not.toContain('selectedTimelineItems = [item]');
	});

	it('routes every pointer gesture through one owned session instead of parallel booleans', () => {
		expect(source).toContain('let gestureSession = $state(new TimelineGestureSession())');
		expect(source).toContain("gestureSession = gestureSession.begin({ kind: 'seek' })");
		expect(source).toContain('gestureSession = gestureSession.cancel()');
		expect(source).not.toContain('let timelineSeekDrag = $state');
		expect(source).not.toContain('let timelinePanState = $state');
		expect(source).not.toContain('let marqueeState = $state');
		expect(source).not.toContain('let rangeCreateState = $state');
	});

	it('previews playhead drags without committing media reloads or globally restarting clip resources', () => {
		const editableSource = readFileSync(new URL('./EditableAudioClip.svelte', import.meta.url), 'utf8');
		const waveformSource = readFileSync(new URL('./ClipWaveform.svelte', import.meta.url), 'utf8');

		expect(source).toContain('onSeekTimeline(pendingSeekMs, true)');
		expect(source).toContain('onSeekTimeline(pendingSeekMs, false)');
		expect(source).not.toContain('waveformsPaused');
		expect(source).not.toContain('waveformPaused=');
		expect(editableSource).not.toContain('waveformPaused');
		expect(waveformSource).not.toMatch(/\bpaused\b/);
		expect(waveformSource).toContain('const url = waveformSrc;');
		expect(waveformSource).toContain('const previewUrl = previewWaveformSrc;');
		expect(source.match(/as clip \(clip\.clip_id\)/g) ?? []).toHaveLength(5);
	});

	it('retains the source-level waveform while a cut tile refines', () => {
		const waveformSource = readFileSync(new URL('./ClipWaveform.svelte', import.meta.url), 'utf8');
		expect(source).toContain('timelineClipPreviewWaveformUrl(projectId, clip)');
		expect(waveformSource).toContain('canRetainClipWaveformDetail({');
		expect(waveformSource).toContain('waveformResourceIdentity');
		expect(waveformSource).not.toContain('else clearWaveformData();');
	});

	it('reports the actually clicked subtitle when an additive selection is removed', () => {
		expect(source).toContain('ttsClickedItem: TimelineSelectionItem | null = null');
		expect(source).toContain('const ttsAnchorItem = ttsClickedItem ?? nextPrimary;');
		expect(source).toMatch(/setTimelineSelection\([\s\S]*?true,\s*item\s*\);/);
	});

	it('updates the TTS group before opening the primary subtitle inspector', () => {
		expect(source).toMatch(/function setTimelineSelection[\s\S]*?syncTtsSelectionAnchor\(ttsAnchorItem, nextItems, ttsAdditive\);[\s\S]*?syncPrimaryTimelineSelection\(nextPrimary\)/);
	});

	it('keeps passive TTS highlights out of the generic edit selection when toggling them', () => {
		expect(source).toContain('ttsSelectionAnchorIsPassive(ttsSelectionSession, clickedAnchor)');
		expect(source).toContain('keepPassiveItemOutOfEditSelection');
		expect(source).toContain('includeWhenMissing: !keepPassiveItemOutOfEditSelection');
	});

	it('keeps the ASR reference range when add-selecting a localized TTS target', () => {
		expect(source).toContain('if (hasRangeSelection) preserveRangeThroughCueSync();');
		expect(source).toContain('if (preserveRange) preserveRangeThroughCueSync();');
		expect(source).toMatch(/function preserveRangeThroughCueSync\(\) \{[\s\S]*?preserveRangeOnCueSelection = true;[\s\S]*?void tick\(\)\.then\(\(\) => \{[\s\S]*?preserveRangeOnCueSelection = false;/);
		expect(source).toMatch(/selectedCueId;[\s\S]*?if \(preserveRangeOnCueSelection\) \{[\s\S]*?preserveRangeOnCueSelection = false;[\s\S]*?return;/);
		expect(source).not.toContain('queueMicrotask(() => (preserveRangeOnCueSelection = false))');
	});

	it('clears the parent TTS selection session when the timeline selection becomes empty', () => {
		const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
		expect(pageSource).toMatch(/function updateTimelineSelection\(items: TimelineSelectionItem\[\]\) \{[\s\S]*?timelineSelectionItems = items;[\s\S]*?if \(!items\.length\) ttsSelectionSession = \{ \.\.\.EMPTY_TTS_SELECTION_SESSION \};[\s\S]*?\}/);
		expect(source).toContain('if (ttsAnchorItem && nextItems.length)');
	});

	it('requires an explicit multi-subtitle merge callback', () => {
		expect(source).toContain('onMergeTimelineSubtitles?: (request: TimelineSubtitleMergeRequest)');
		expect(source).not.toContain('onMergeCue: () => void');
		expect(source).toContain('onclick={mergeSelectedTimelineSubtitles}');
		expect(source).toContain('disabled={!canMergeSelectedSubtitles}');
	});

	it('collapses selection to the surviving first subtitle after a successful merge', () => {
		expect(source).toContain('await onMergeTimelineSubtitles(request)');
		expect(source).toMatch(/setTimelineSelection\(\[\{[\s\S]*?itemId: request\.itemIds\[0\][\s\S]*?\}\]\)/);
	});

	it('keeps pending audio clips in lane layout instead of hiding them until a waveform exists', () => {
		expect(source).toContain('for (const clip of draft?.timeline_clips ?? [])');
		expect(source).toContain('grouped[clip.track_id as VideoLocalizationAudioTrackId].push(clip)');
		expect(source).not.toContain("clip.track_id === trackId && clip.audio_path");
	});

	it('renders persisted audio clips from the canonical frame interval without millisecond padding', () => {
		expect(source).toContain('const normalized = normalizeAudioFrameInterval({');
		expect(source).toContain('return timelineRangeWidthPercent(start, end, timelineDurationMs)');
		expect(source).not.toContain(
			'end_ms: Math.max(start + audioClipMinimumDurationMs, end)'
		);
	});

	it('keeps the horizontal move when a hovered dub lane cannot accept the clip', () => {
		expect(source).toContain('let verticalLaneChange = false');
		expect(source).toContain('verticalLaneChange = state.laneDropAllowed');
		expect(source).toContain('const dubLane = verticalLaneChange ? state.targetLane : undefined');
		expect(source).not.toMatch(/if \(!targetStillAvailable\) \{[\s\S]*?cancelClipDrag\(\)/);
	});

	it('lets the complete audio clip surface start horizontal or vertical movement', () => {
		const editableSource = readFileSync(new URL('./EditableAudioClip.svelte', import.meta.url), 'utf8');
		expect(editableSource).toContain('(event.currentTarget as HTMLElement).focus({ preventScroll: true });');
		expect(editableSource).toContain('if (event.button === 0 && !selected) onSelect(event);');
		expect(editableSource).toContain('event.ctrlKey || event.metaKey');
		expect(editableSource).toContain('onMove(event);');
		expect(editableSource).not.toContain("closest('.clip-label')) return");
	});

	it('turns a subtitle double click into an immediate playback selection', () => {
		expect(source).toContain('function playSubtitleTimelineItem(event: MouseEvent, trackKind: SubtitleTrackKind, cue: SubtitleTimelineItem)');
		expect(source).toContain("ondblclick={(event) => playSubtitleTimelineItem(event, 'asr', cue)}");
		expect(source).toContain("ondblclick={(event) => playSubtitleTimelineItem(event, 'localized', cue)}");
		expect(source).toContain('onSelectionRangeCommit?.({ startMs: rangeStartMs, endMs: rangeEndMs })');
	});

	it('keeps short subtitle hit areas within their real time range', () => {
		expect(source).toMatch(/\.row-subtitle \.cue-chip \{[\s\S]*?padding: 4px 0;/);
		expect(source).toMatch(/\.row-subtitle \.cue-chip \.cue-text \{[\s\S]*?margin: 0 9px;/);
	});

	it('shows a provisional new dub lane only when no existing lane accepts the moving group', () => {
		expect(source).toContain("clipDragState.trackId !== 'dub' || clipDragState.mode !== 'move'");
		expect(source).toContain('resolveDubClipGroupLanePlacement(');
		expect(source).toContain("placement?.targetLanes.filter((lane) => lane >= dubTrackLanes.length) ?? []");
		expect(source).toContain('{#each clipNewLaneIndexes as clipNewLaneIndex');
		expect(source).toContain('拖入片段后创建');
	});

	it('commits a shared lane offset while preserving a multi-lane selection', () => {
		expect(source).toContain('placeDubClipGroupAtPrimaryLane(');
		expect(source).toContain('targetLaneByClipId: placement ? { ...placement.laneByClipId } : state.groupLaneByClipId');
		expect(source).toContain('...(verticalLaneChange ? { dubLane: state.targetLaneByClipId[item.itemId] } : {})');
		expect(source).toContain('dragging={clipIncludedInDrag(clip.clip_id)}');
	});

	it('keeps every playback cache segment isolated to a single pixel', () => {
		expect(source).toContain('class:cache-empty={range.status === \'empty\'}');
		expect(source).not.toContain('class:empty={range.status === \'empty\'}');
		expect(source).toMatch(/\.preview-cache-strip \{[\s\S]*?height: 1px;[\s\S]*?overflow: hidden;/);
		expect(source).toMatch(/\.preview-cache-strip \.cache-range \{[\s\S]*?height: 1px;[\s\S]*?padding: 0;[\s\S]*?border: 0;/);
	});

	it('restores and reports the saved horizontal timeline viewport', () => {
		expect(source).toContain('timelineViewportStartMs = 0');
		expect(source).toContain('timelineViewportRestoreReady = true');
		expect(source).toContain('onTimelineViewportChange = undefined');
		expect(source).toContain('restoreTimelineViewport(savedStartMs)');
		expect(source).toContain('scheduleTimelineViewportChange(element)');
		expect(source).toContain('!timelineViewportRestored');
	});

	it('keeps transport endpoints separate from global edit-point navigation', () => {
		expect(source).toContain("event.shiftKey ? 'previous-boundary' : 'start'");
		expect(source).toContain("event.shiftKey ? 'next-boundary' : 'end'");
		expect(source).toContain("event.shiftKey && event.key === 'ArrowLeft'");
		expect(source).toContain("onTransportAction('previous-boundary')");
		expect(source).toContain("onTransportAction('next-boundary')");
	});

	it('keeps the toolbar on one row while the notice shrinks before the controls', () => {
		const noticeSource = readFileSync(new URL('./ActivityNotice.svelte', import.meta.url), 'utf8');
		const marqueeSource = readFileSync(new URL('./HoverMarqueeText.svelte', import.meta.url), 'utf8');
		expect(source).toContain('grid-template-columns: minmax(0, 1fr) auto auto;');
		expect(source).toMatch(/\.transport,[\s\S]*?\.timeline-actions \{[\s\S]*?min-width: max-content;[\s\S]*?flex-wrap: nowrap;/);
		expect(source).toContain('--playback-health-width: clamp(140px, 18cqi, 300px);');
		expect(source).toMatch(/\.playback-health \{[\s\S]*?width: var\(--playback-health-width\);[\s\S]*?flex: 0 0 var\(--playback-health-width\);/);
		expect(source).toContain('<HoverMarqueeText text={playbackHealthLabel} />');
		expect(source).not.toContain('.timeline-activity { grid-column: 1 / -1; }');
		expect(source).not.toContain('@container cut-timeline (max-width: 1380px)');
		expect(noticeSource).toMatch(/\.activity-slot \{[\s\S]*?min-width: 0;[\s\S]*?overflow: hidden;/);
		expect(noticeSource).toContain('<HoverMarqueeText text={displayedSummary} />');
		expect(noticeSource).toContain('<HoverMarqueeText text={taskSummary.text} />');
		expect(noticeSource).toMatch(/\.activity-summary \{[\s\S]*?flex: 1 1 auto;[\s\S]*?overflow: hidden;/);
		expect(marqueeSource).toContain('animation: hover-marquee-loop var(--hover-marquee-duration) linear infinite;');
		expect(marqueeSource).toContain('@media (prefers-reduced-motion: reduce)');
		expect(marqueeSource).toContain('aria-hidden="true"');
	});

	it('exposes mute and solo state with unambiguous pressed controls', () => {
		expect(source).toContain('aria-label="静音原音轨" aria-pressed={trackStates.original.muted}');
		expect(source).toContain('aria-label="独奏人声轨" aria-pressed={trackStates.vocals.solo}');
		expect(source).toContain('aria-label="独奏背景音乐轨" aria-pressed={trackStates.background.solo}');
		expect(source).toContain('aria-label="独奏合成配音轨 1" aria-pressed={dubLaneState(0).solo}');
	});

	it('keeps ASR generation visible after the subtitle track already has cues', () => {
		expect(source).toContain('aria-label={asrGenerateCommand.label}');
		expect(source).toContain('onclick={() => void asrGenerateCommand.onSelect()}');
		expect(source).toContain("{asrBusy ? '正在听写' : asrGenerateCommand.label}");
	});
});
