import { describe, expect, it } from 'vitest';
import { timelineItemDeletionBlocked } from './timeline-deletion-policy';

describe('timeline deletion policy', () => {
	it('allows an active TTS workflow clip to be stopped and deleted', () => {
		expect(timelineItemDeletionBlocked({
			kind: 'audio',
			trackId: 'dub',
			trackLocked: false,
			laneLocked: false,
			runtimeBusy: true,
			ttsWorkflowMarker: 'workflow-running'
		})).toBe(false);
		expect(timelineItemDeletionBlocked({
			kind: 'audio',
			trackId: 'dub',
			trackLocked: false,
			laneLocked: false,
			runtimeBusy: true,
			ttsWorkflowMarker: 'init:client-initializing'
		})).toBe(false);
	});

	it('keeps unrelated processing items protected from deletion', () => {
		expect(timelineItemDeletionBlocked({
			kind: 'audio',
			trackId: 'dub',
			trackLocked: false,
			laneLocked: false,
			runtimeBusy: true,
			ttsWorkflowMarker: null
		})).toBe(true);
		expect(timelineItemDeletionBlocked({
			kind: 'subtitle',
			trackId: 'localizedSubtitles',
			trackLocked: false,
			laneLocked: false,
			runtimeBusy: true,
			ttsWorkflowMarker: 'workflow-running'
		})).toBe(true);
	});

	it('honors explicit track and dub-lane locks even for active TTS clips', () => {
		expect(timelineItemDeletionBlocked({
			kind: 'audio',
			trackId: 'dub',
			trackLocked: true,
			laneLocked: false,
			runtimeBusy: true,
			ttsWorkflowMarker: 'workflow-running'
		})).toBe(true);
		expect(timelineItemDeletionBlocked({
			kind: 'audio',
			trackId: 'dub',
			trackLocked: false,
			laneLocked: true,
			runtimeBusy: true,
			ttsWorkflowMarker: 'workflow-running'
		})).toBe(true);
	});
});
