import { api } from './client';
import type { EngineDetail } from './types';

export function listEngines(): Promise<EngineDetail[]> {
  return api.get<EngineDetail[]>('/engines');
}

export function getEngine(engineId: string): Promise<EngineDetail> {
  return api.get<EngineDetail>(`/engines/${engineId}`);
}

export function startEngine(engineId: string): Promise<EngineDetail> {
  return api.post<EngineDetail>(`/engines/${engineId}/start`, {});
}

export function stopEngine(engineId: string): Promise<EngineDetail> {
  return api.post<EngineDetail>(`/engines/${engineId}/stop`, {});
}

export function healthCheckEngine(engineId: string): Promise<Record<string, unknown>> {
  return api.post<Record<string, unknown>>(`/engines/${engineId}/health-check`, {});
}
