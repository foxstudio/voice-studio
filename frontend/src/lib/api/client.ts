export class ApiError extends Error {
	constructor(
		message: string,
		public status: number,
		public code = 'API_ERROR'
	) {
		super(message);
	}
}

const DEFAULT_TIMEOUT_MS = 30_000;

async function fetchWithTimeout(url: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Response> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		return await fetch(url, { ...init, signal: controller.signal });
	} catch (e: unknown) {
		if (e instanceof DOMException && e.name === 'AbortError') {
			throw new ApiError('请求超时，请检查网络或服务状态', 0, 'TIMEOUT');
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
		throw new ApiError(String(msg), res.status, String((err as Record<string, unknown>).code ?? 'API_ERROR'));
	}
	return data as T;
}

export const api = {
	get: <T>(path: string) => fetchWithTimeout(`/api${path}`).then(parse<T>),
	post: <T>(path: string, body?: unknown) =>
		fetchWithTimeout(`/api${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }).then(parse<T>),
	patch: <T>(path: string, body: unknown) =>
		fetchWithTimeout(`/api${path}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(parse<T>),
	put: <T>(path: string, body: unknown) =>
		fetchWithTimeout(`/api${path}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(parse<T>),
	delete: <T>(path: string) => fetchWithTimeout(`/api${path}`, { method: 'DELETE' }).then(parse<T>),
	upload: <T>(path: string, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return fetchWithTimeout(`/api${path}`, { method: 'POST', body: form }).then(parse<T>);
	},
	postForm: <T>(path: string, form: FormData) => fetchWithTimeout(`/api${path}`, { method: 'POST', body: form }).then(parse<T>)
};
