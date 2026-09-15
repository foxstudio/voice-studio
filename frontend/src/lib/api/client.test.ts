import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './client';

describe('API request timeout', () => {
	afterEach(() => {
		vi.useRealTimers();
		vi.restoreAllMocks();
	});

	it('covers reading the response body, not only receiving the response headers', async () => {
		vi.useFakeTimers();
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => ({
			ok: true,
			status: 200,
			statusText: 'OK',
			text: () => new Promise<string>((_resolve, reject) => {
				init?.signal?.addEventListener('abort', () => {
					reject(new DOMException('The operation was aborted', 'AbortError'));
				}, { once: true });
			})
		} as Response));

		let outcome = 'pending';
		void api.get('/health', { timeoutMs: 25 })
			.then(() => { outcome = 'resolved'; })
			.catch((error: unknown) => {
				outcome = error instanceof Error ? error.message : String(error);
			});

		await vi.advanceTimersByTimeAsync(25);
		await Promise.resolve();

		expect(outcome).toContain('请求超时');
	});
});
