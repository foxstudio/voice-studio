import type { ErrorResponse } from './types';

const BASE = '/api';

// ── Error classes ────────────────────────────────────────

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: unknown;

  constructor(status: number, message: string, code?: string, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}

// ── Internal helpers ───────────────────────────────────

async function parseErrorBody(res: Response) {
  try {
    const body = await res.json() as ErrorResponse;
    return {
      message: body.error?.message ?? res.statusText,
      code: body.error?.code,
      details: body.error?.detail,
    };
  } catch {
    return { message: res.statusText };
  }
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
  } catch (err) {
    throw new NetworkError((err as Error)?.message ?? 'Network request failed');
  }

  if (!res.ok) {
    const { message, code, details } = await parseErrorBody(res);
    throw new ApiError(res.status, message, code, details);
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;

  return res.json();
}

// ── Exported client ────────────────────────────────────

export const api = {
  get: <T>(path: string) => request<T>(path),

  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),

  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),

  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),

  upload: async <T>(path: string, file: File): Promise<T> => {
    const form = new FormData();
    form.append('file', file);
    let res: Response;
    try {
      res = await fetch(`${BASE}${path}`, { method: 'POST', body: form });
    } catch (err) {
      throw new NetworkError((err as Error)?.message ?? 'Upload request failed');
    }
    if (!res.ok) {
      const { message, code, details } = await parseErrorBody(res);
      throw new ApiError(res.status, message, code, details);
    }
    return res.json();
  },
};
