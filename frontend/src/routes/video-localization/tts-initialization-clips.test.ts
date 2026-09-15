import { describe, expect, it } from 'vitest';
import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import {
	cleanupTtsInitializationClips,
	createTtsInitializationPlaceholder,
	promoteTtsInitializationPlaceholder,
	ttsInitializationFailureMessage
} from './tts-initialization-clips';

function clip(
	clipId: string,
	overrides: Partial<VideoLocalizationTimelineClip> = {}
): VideoLocalizationTimelineClip {
	return {
		clip_id: clipId,
		track_id: 'dub',
		start_ms: 1_000,
		end_ms: 2_000,
		...overrides
	};
}

describe('TTS initialization clip cleanup', () => {
	it('keeps an existing take and places a new initialization on a free lane', () => {
		const original = clip('clip-original', {
			audio_path: '/tts/original.wav',
			dub_lane: 0
		});
		const sibling = clip('clip-sibling', {
			start_ms: 2_100,
			end_ms: 2_800,
			audio_path: '/tts/sibling.wav',
			dub_lane: 0
		});

		const placeholder = createTtsInitializationPlaceholder(
			[original, sibling],
			{
				clientId: 'client-new',
				segmentId: 'localized-1',
				primaryCueId: 'cue-1',
				sourceCueIds: ['cue-1'],
				startMs: 1_000,
				endMs: 2_000,
				targetClip: original
			}
		);

		expect(placeholder).toEqual(expect.objectContaining({
			clip_id: 'pending_tts_init_client-new',
			audio_path: null,
			dub_lane: 1,
			optimistic_tts_workflow_id: 'init:client-new'
		}));
		expect([original, sibling, placeholder]).toEqual(expect.arrayContaining([original, placeholder]));
	});

	it('promotes only the accepted initialization while every existing take remains', () => {
		const original = clip('clip-original', { audio_path: '/tts/original.wav' });
		const first = createTtsInitializationPlaceholder([original], {
			clientId: 'client-1',
			segmentId: 'localized-1',
			primaryCueId: 'cue-1',
			sourceCueIds: ['cue-1'],
			startMs: 1_000,
			endMs: 2_000,
			targetClip: original
		});
		const second = createTtsInitializationPlaceholder([original, first], {
			clientId: 'client-2',
			segmentId: 'localized-1',
			primaryCueId: 'cue-1',
			sourceCueIds: ['cue-1'],
			startMs: 1_000,
			endMs: 2_000,
			targetClip: original
		});

		const promoted = promoteTtsInitializationPlaceholder(
			[original, first, second],
			'client-1',
			'workflow-1'
		);

		expect(promoted).toEqual(expect.arrayContaining([
			original,
			expect.objectContaining({
				clip_id: first.clip_id,
				optimistic_tts_workflow_id: 'workflow-1'
			}),
			expect.objectContaining({
				clip_id: second.clip_id,
				optimistic_tts_workflow_id: 'init:client-2'
			})
		]));
	});

	it('keeps a plain-language submission error visible on the failed timeline clip', () => {
		expect(ttsInitializationFailureMessage(new Error('字幕修改尚未保存，请重试'))).toBe(
			'字幕修改尚未保存，请重试'
		);
		expect(ttsInitializationFailureMessage(new TypeError('Failed to fetch'))).toBe(
			'接口当前无法连接，请等待服务恢复后重试'
		);
	});

	it('removes an initialization placeholder without touching durable siblings', () => {
		const placeholder = clip('pending_tts_init_client-1', {
			optimistic_tts_workflow_id: 'init:client-1'
		});
		const sibling = clip('clip-sibling');

		expect(cleanupTtsInitializationClips([placeholder, sibling], 'client-1')).toEqual({
			clips: [sibling],
			removedClipIds: ['pending_tts_init_client-1']
		});
	});
});
