import { api } from './client';
import type { VoiceAsset, VoiceAssetCreate, UploadResult, TestGenerateResponse, DeleteResponse } from './types';

export function listVoices(): Promise<VoiceAsset[]> {
  return api.get<VoiceAsset[]>('/voices');
}

export function getVoice(voiceId: string): Promise<VoiceAsset> {
  return api.get<VoiceAsset>(`/voices/${voiceId}`);
}

export function createVoice(data: VoiceAssetCreate): Promise<VoiceAsset> {
  return api.post<VoiceAsset>('/voices', data);
}

export function updateVoice(voiceId: string, data: VoiceAssetCreate): Promise<VoiceAsset> {
  return api.patch<VoiceAsset>(`/voices/${voiceId}`, data);
}

export function deleteVoice(voiceId: string): Promise<DeleteResponse> {
  return api.delete<DeleteResponse>(`/voices/${voiceId}`);
}

export function uploadVoice(file: File): Promise<UploadResult> {
  return api.upload<UploadResult>('/voices/upload', file);
}

export function testGenerateVoice(voiceId: string): Promise<TestGenerateResponse> {
  return api.post<TestGenerateResponse>(`/voices/${voiceId}/test-generate`, {});
}
