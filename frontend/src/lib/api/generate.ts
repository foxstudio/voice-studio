import { api } from './client';
import type { GenerateRequest, GenerateResponse } from './types';

export function generateAudio(body: GenerateRequest): Promise<GenerateResponse> {
  return api.post<GenerateResponse>('/generate', body);
}
