import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import AudioGainEditor from './AudioGainEditor.svelte';

describe('audio gain editor', () => {
	it('uses a signed decimal text draft so a leading minus sign remains editable', () => {
		const { body } = render(AudioGainEditor, {
			props: {
				valueDb: 0,
				ariaLabel: '人声轨音量 dB',
				onChange: () => undefined,
				onCommit: () => undefined,
				onCancel: () => undefined
			}
		});

		expect(body).toContain('type="text"');
		expect(body).toContain('inputmode="decimal"');
		expect(body).toContain('role="spinbutton"');
		expect(body).toContain('aria-label="人声轨音量 dB"');
	});
});
