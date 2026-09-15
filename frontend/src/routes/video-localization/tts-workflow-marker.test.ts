import { describe, expect, it } from 'vitest';
import { persistedTtsWorkflowId, ttsInitializationClientId } from './tts-workflow-marker';

describe('TTS workflow marker', () => {
	it('keeps init markers local and never exposes them as persisted workflow ids', () => {
		expect(ttsInitializationClientId('init:client-123')).toBe('client-123');
		expect(persistedTtsWorkflowId('init:client-123')).toBeNull();
	});

	it('routes real workflow ids to the backend', () => {
		expect(ttsInitializationClientId('workflow-123')).toBeNull();
		expect(persistedTtsWorkflowId('workflow-123')).toBe('workflow-123');
	});
});
