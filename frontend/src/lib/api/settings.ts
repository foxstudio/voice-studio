import { api } from './client';
import type { AppSettings } from './types';

export function getSettings(): Promise<AppSettings> {
  return api.get<AppSettings>('/settings');
}

export function updateSettings(data: AppSettings): Promise<AppSettings> {
  return api.patch<AppSettings>('/settings', data);
}
