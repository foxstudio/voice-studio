export class ApiError extends Error {
	constructor(
		message: string,
		public status: number,
		public code = 'API_ERROR',
		public details: unknown = null
	) {
		super(message);
	}
}

const DEFAULT_TIMEOUT_MS = 30_000;

export type ApiRequestOptions = {
	timeoutMs?: number;
};

async function fetchWithTimeout<T>(url: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		const response = await fetch(url, { ...init, signal: controller.signal });
		return await parse<T>(response);
	} catch (e: unknown) {
		if (e instanceof DOMException && e.name === 'AbortError') {
			const method = init?.method ?? 'GET';
			throw new ApiError(`请求超时：${method} ${url}（${Math.round(timeoutMs / 1000)} 秒）`, 0, 'TIMEOUT');
		}
		throw e;
	} finally {
		clearTimeout(timer);
	}
}

async function parse<T>(res: Response): Promise<T> {
	const text = await res.text();
	let data: unknown;
	try {
		data = text ? JSON.parse(text) : {};
	} catch {
		throw new ApiError(`服务器返回了非 JSON 响应（HTTP ${res.status}）`, res.status, 'INVALID_RESPONSE');
	}
	if (!res.ok) {
		const err = (data as Record<string, unknown>).error ?? {};
		const msg = (err as Record<string, unknown>).message ?? res.statusText;
		throw new ApiError(
			String(msg),
			res.status,
			String((err as Record<string, unknown>).code ?? 'API_ERROR'),
			(err as Record<string, unknown>).detail
				?? (err as Record<string, unknown>).details
				?? null
		);
	}
	return data as T;
}

export const api = {
	// Local project state is mutable and may be changed by background jobs. Never
	// let the browser reuse an earlier JSON snapshot across project switches.
	get: <T>(path: string, options: ApiRequestOptions = {}) => fetchWithTimeout<T>(`/api${path}`, { cache: 'no-store' }, options.timeoutMs),
	post: <T>(path: string, body?: unknown, options: ApiRequestOptions = {}) =>
		fetchWithTimeout<T>(`/api${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }, options.timeoutMs),
	patch: <T>(path: string, body: unknown, options: ApiRequestOptions = {}) =>
		fetchWithTimeout<T>(`/api${path}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, options.timeoutMs),
	put: <T>(path: string, body: unknown, options: ApiRequestOptions = {}) =>
		fetchWithTimeout<T>(`/api${path}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, options.timeoutMs),
	delete: <T>(path: string, options: ApiRequestOptions = {}) => fetchWithTimeout<T>(`/api${path}`, { method: 'DELETE' }, options.timeoutMs),
	upload: <T>(path: string, file: File, fields: Record<string, string> = {}) => {
		const form = new FormData();
		form.append('file', file);
		for (const [key, value] of Object.entries(fields)) form.append(key, value);
		return fetchWithTimeout<T>(`/api${path}`, { method: 'POST', body: form }, 10 * 60_000);
	},
	postForm: <T>(path: string, form: FormData) => fetchWithTimeout<T>(`/api${path}`, { method: 'POST', body: form }, 10 * 60_000)
};
