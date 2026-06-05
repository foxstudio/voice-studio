import { api } from './client';
import type { GenerationTask, CancelResponse, RetryResponse } from './types';

export function listTasks(): Promise<GenerationTask[]> {
  return api.get<GenerationTask[]>('/tasks');
}

export function getTask(taskId: string): Promise<GenerationTask> {
  return api.get<GenerationTask>(`/tasks/${taskId}`);
}

export function cancelTask(taskId: string): Promise<CancelResponse> {
  return api.post<CancelResponse>(`/tasks/${taskId}/cancel`, {});
}

export function retryTask(taskId: string): Promise<RetryResponse> {
  return api.post<RetryResponse>(`/tasks/${taskId}/retry`, {});
}
