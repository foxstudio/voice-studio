import { api } from './client';
import type { HistoryItem, DeleteResponse } from './types';

export function listHistory(limit = 50, offset = 0): Promise<HistoryItem[]> {
  return api.get<HistoryItem[]>(`/history?limit=${limit}&offset=${offset}`);
}

export function deleteHistory(resultId: string): Promise<DeleteResponse> {
  return api.delete<DeleteResponse>(`/history/${resultId}`);
}
