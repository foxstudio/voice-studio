import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api } from './index';

afterEach(() => vi.unstubAllGlobals());

describe('Seed Audio asset API', () => {
	it('uploads image as multipart with explicit license status', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			expect(url).toBe('/api/seed-audio/assets/image');
			expect(init?.method).toBe('POST');
			expect(init?.body).toBeInstanceOf(FormData);
			const form = init?.body as FormData;
			expect(form.get('file')).toBeInstanceOf(File);
			expect(form.get('license_status')).toBe('self_voice');
			return new Response(JSON.stringify({
				file_id: 'image-1', asset_type: 'seed_audio_image', source: 'upload', license_status: 'self_voice',
				original_name: 'scene.png', mime_type: 'image/png', media_format: 'png', size_bytes: 8, created_at: '2026-07-11T00:00:00Z'
			}), { status: 201, headers: { 'Content-Type': 'application/json' } });
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await Api.uploadSeedAudioImage(new File([new Uint8Array(8)], 'scene.png', { type: 'image/png' }), 'self_voice');

		expect(result.file_id).toBe('image-1');
		expect(fetchMock).toHaveBeenCalledOnce();
	});
});
