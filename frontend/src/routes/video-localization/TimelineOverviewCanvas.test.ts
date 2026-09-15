import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import TimelineOverviewCanvas from './TimelineOverviewCanvas.svelte';

describe('timeline overview canvas', () => {
	it('keeps a low-resolution waveform inside a full-timeline audio overview', () => {
		const { body } = render(TimelineOverviewCanvas, {
			props: {
				items: [{ start_ms: 0, end_ms: 665_088 }],
				durationMs: 665_088,
				tone: 'music',
				gain: 2,
				waveformSrc: '/api/projects/project-1/timeline-clips/background/waveform?bins=4800',
				waveformItem: {
					start_ms: 0,
					end_ms: 665_088,
					source_start_ms: 0,
					source_end_ms: 665_088
				}
			}
		});

		expect(body).toContain('data-overview-waveform="music"');
		expect(body).toContain('clip-waveform');
		expect(body).toContain('data-waveform-gain="2"');
	});

	it('exposes one lightweight keyboard focus target when overview items are selectable', () => {
		const { body } = render(TimelineOverviewCanvas, {
			props: {
				items: [{ clip_id: 'clip-1', start_ms: 100, end_ms: 200 }],
				durationMs: 1_000,
				tone: 'dub',
				ariaLabel: '配音概览，可点击选择片段',
				selectedItemIds: ['clip-1'],
				onSelect: () => undefined
			}
		});

		expect(body).toContain('<button');
		expect(body).toContain('aria-label="配音概览，可点击选择片段"');
		expect(body).toContain('data-audio-clip-id="clip-1"');
		expect(body).toContain('aria-pressed="true"');
		expect(body.match(/<button/g)).toHaveLength(1);
	});
});
