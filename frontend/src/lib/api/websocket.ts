/**
 * Typed WebSocket client for backend task progress channel.
 *
 * Backend endpoint: /api/tasks/ws (broadcast — receives EVERY task update,
 * not per-task). Each frame is JSON of a full GenerationTask object.
 *
 * Usage:
 *   const sub = subscribeTaskUpdates(
 *     taskId,
 *     (event) => {
 *       if (event.type === 'done') { ... }
 *     },
 *     {
 *       onStatusChange: (s) => console.log('ws status', s),
 *       onFallback: () => startPolling(),
 *     }
 *   );
 *   // later:
 *   sub.close();
 *
 * Retry policy: 500ms, 1s, 2s exponential backoff. After 3 failed attempts
 * (initial connect or reconnect), invokes `onFallback` and stops.
 */

import type { GenerationTask } from './types';

// ── Public types ──────────────────────────────────────

export type WsConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'fallback'
  | 'closed';

export interface TaskProgressEvent {
  type: 'progress' | 'done' | 'error';
  data: GenerationTask;
}

export interface SubscribeOptions {
  /** Called whenever connection status transitions. */
  onStatusChange?: (status: WsConnectionStatus) => void;
  /** Called once after all retries exhausted — caller should start polling. */
  onFallback?: () => void;
  /** Override the WS URL (mainly for testing). Defaults to `/api/tasks/ws`. */
  url?: string;
  /** Max retry attempts before fallback. Defaults to 3. */
  maxRetries?: number;
}

export interface Subscription {
  /** Close the WS connection and stop any pending retries. */
  close(): void;
  /** Current status (read-only snapshot). */
  readonly status: WsConnectionStatus;
}

// ── Helpers ───────────────────────────────────────────

function buildWsUrl(path: string): string {
  if (path.startsWith('ws://') || path.startsWith('wss://')) return path;
  // Browser only — SSR-safe guard
  if (typeof window === 'undefined') return path;
  // Connect directly to backend (Vite proxy does not reliably forward WS upgrades)
  const host = window.location.hostname + ':8765';
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${host}${path}`;
}

function classifyEvent(task: GenerationTask): TaskProgressEvent['type'] {
  if (task.status === 'success') return 'done';
  if (task.status === 'failed') return 'error';
  return 'progress';
}

// Retry backoff in ms: 500, 1000, 2000
const BACKOFF_MS = [500, 1000, 2000];

// ── Public API ────────────────────────────────────────

/**
 * Subscribe to task progress updates for a single task ID over WebSocket.
 *
 * Backend broadcasts every task update on a single channel; this helper
 * filters by `task_id` so callers only see events for the task they care about.
 */
export function subscribeTaskUpdates(
  taskId: string,
  onEvent: (event: TaskProgressEvent) => void,
  options: SubscribeOptions = {}
): Subscription {
  const maxRetries = options.maxRetries ?? 3;
  const url = buildWsUrl(options.url ?? '/api/tasks/ws');

  let ws: WebSocket | null = null;
  let attempt = 0;
  let closed = false;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let status: WsConnectionStatus = 'connecting';

  const setStatus = (next: WsConnectionStatus): void => {
    if (status === next) return;
    status = next;
    options.onStatusChange?.(next);
  };

  const connect = (): void => {
    if (closed) return;

    try {
      ws = new WebSocket(url);
    } catch {
      scheduleRetry();
      return;
    }

    ws.onopen = () => {
      if (closed) {
        ws?.close();
        return;
      }
      attempt = 0;
      setStatus('connected');
    };

    ws.onmessage = (msg: MessageEvent) => {
      if (closed) return;
      let task: GenerationTask;
      try {
        task = JSON.parse(msg.data as string) as GenerationTask;
      } catch {
        return; // Ignore malformed frames silently — not our task.
      }
      if (task.task_id !== taskId) return;
      onEvent({ type: classifyEvent(task), data: task });
    };

    ws.onerror = () => {
      // Errors trigger onclose; handle retry there.
    };

    ws.onclose = () => {
      if (closed) return;
      scheduleRetry();
    };
  };

  const scheduleRetry = (): void => {
    if (closed) return;
    if (attempt >= maxRetries) {
      setStatus('fallback');
      closed = true;
      options.onFallback?.();
      return;
    }
    const delay = BACKOFF_MS[attempt] ?? BACKOFF_MS[BACKOFF_MS.length - 1];
    attempt += 1;
    setStatus('reconnecting');
    retryTimer = setTimeout(() => {
      retryTimer = null;
      connect();
    }, delay);
  };

  // Kick off first connection
  connect();

  return {
    close(): void {
      if (closed) return;
      closed = true;
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      if (ws !== null) {
        try {
          ws.close();
        } catch {
          // ignore
        }
        ws = null;
      }
      setStatus('closed');
    },
    get status(): WsConnectionStatus {
      return status;
    },
  };
}
