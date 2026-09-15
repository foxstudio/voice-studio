import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';


describe('application HTML template', () => {
	it('uses the bundled brand mark as the browser icon', () => {
		const template = readFileSync(new URL('./app.html', import.meta.url), 'utf8');

		expect(template).toContain(
			'<link rel="icon" type="image/png" href="/voice-studio-mark.png" />'
		);
	});
});
