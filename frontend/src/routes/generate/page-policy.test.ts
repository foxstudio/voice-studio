import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

describe('generate task polling policy', () => {
	it('refreshes task totals immediately after a polled task reaches a terminal state', () => {
		expect(pageSource).toMatch(
			/if \(\['success', 'failed', 'cancelled'\]\.includes\(task\.status\)\) \{[\s\S]*?locallySubmittedTaskIds\.delete\(taskId\);[\s\S]*?scheduleTaskPageRefresh\(0\);[\s\S]*?return;/
		);
	});

	it('backs off a failed task socket instead of creating a reconnect storm', () => {
		expect(pageSource).toContain('taskSocketRetryCount = 0');
		expect(pageSource).toContain('Math.min(60_000, 1_500 * 2 ** Math.min(taskSocketRetryCount, 6))');
		expect(pageSource).not.toContain('setTimeout(connectTaskSocket, 1500)');
	});
});

describe('video-localization handoff policy', () => {
	it('opens the composer first and materializes the reference clip inside the generate page', () => {
		expect(pageSource).toContain('parseVideoLocalizationTtsHandoffIntent(intentRaw)');
		expect(pageSource).toContain('Api.previewVideoLocalizationTtsHandoff(');
		expect(pageSource).toContain('videoLocalizationHandoffPreparing = Boolean(preparedHandoffPromise)');
		expect(pageSource).toContain('正在准备视频片段的参考音频，页面其他设置仍可操作');
	});
});
