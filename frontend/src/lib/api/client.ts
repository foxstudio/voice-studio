export class ApiError extends Error {
	constructor(
		message: string,
		public status: number,
		public code = 'API_ERROR'
	) {
		super(message);
	}
}

async function parse<T>(res: Response): Promise<T> {
	const text = await res.text();
	const data = text ? JSON.parse(text) : {};
	if (!res.ok) {
		const err = data.error ?? {};
		throw new ApiError(err.message ?? res.statusText, res.status, err.code);
	}
	return data as T;
}

export const api = {
	get: <T>(path: string) => fetch(`/api${path}`).then(parse<T>),
	post: <T>(path: string, body?: unknown) =>
		fetch(`/api${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }).then(parse<T>),
	patch: <T>(path: string, body: unknown) =>
		fetch(`/api${path}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(parse<T>),
	put: <T>(path: string, body: unknown) =>
		fetch(`/api${path}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(parse<T>),
	delete: <T>(path: string) => fetch(`/api${path}`, { method: 'DELETE' }).then(parse<T>),
	upload: <T>(path: string, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return fetch(`/api${path}`, { method: 'POST', body: form }).then(parse<T>);
	},
	postForm: <T>(path: string, form: FormData) => fetch(`/api${path}`, { method: 'POST', body: form }).then(parse<T>)
};
