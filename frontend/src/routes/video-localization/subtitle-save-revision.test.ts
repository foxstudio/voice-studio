import { describe, expect, it } from 'vitest';
import { nextSubtitleSaveRevision, subtitleSaveRevisionIsCurrent } from './subtitle-save-revision';

describe('localized subtitle save revisions', () => {
	it('tracks concurrent saves independently for each subtitle', () => {
		const revisions = new Map<string, number>();
		const first = nextSubtitleSaveRevision(revisions, 'localized_0001');
		nextSubtitleSaveRevision(revisions, 'localized_0002');

		expect(subtitleSaveRevisionIsCurrent(revisions, 'localized_0001', first, 3, 3)).toBe(true);
	});

	it('invalidates only an older save for the same subtitle or a previous project epoch', () => {
		const revisions = new Map<string, number>();
		const older = nextSubtitleSaveRevision(revisions, 'localized_0001');
		const newer = nextSubtitleSaveRevision(revisions, 'localized_0001');

		expect(subtitleSaveRevisionIsCurrent(revisions, 'localized_0001', older, 2, 2)).toBe(false);
		expect(subtitleSaveRevisionIsCurrent(revisions, 'localized_0001', newer, 2, 3)).toBe(false);
	});
});
