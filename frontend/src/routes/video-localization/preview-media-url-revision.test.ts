import { describe, expect, it } from 'vitest';
import {
	appendPlaybackReloadRevision,
	stablePlaybackRevision
} from './preview-media-url-revision';

describe('preview media URL revisions', () => {
	it('keeps content-addressed media URLs stable across ordinary remounts', () => {
		expect(stablePlaybackRevision('proxy-r1', 0)).toBe('proxy-r1');
		expect(stablePlaybackRevision('proxy-r1', 0)).toBe('proxy-r1');
		expect(appendPlaybackReloadRevision('/clip.wav?v=result-1', 0)).toBe(
			'/clip.wav?v=result-1'
		);
	});

	it('changes URLs only for an explicit media reload', () => {
		expect(stablePlaybackRevision('proxy-r1', 42)).toBe('proxy-r1:reload:42');
		expect(appendPlaybackReloadRevision('/clip.wav?v=result-1', 42)).toBe(
			'/clip.wav?v=result-1&playback_reload=42'
		);
	});

});
