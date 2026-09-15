import type { EngineUiProfile } from '../types';
import { seedAudioStateFromRequest, seedAudioStateToRequest, type SeedAudioRequestEnvelope } from './request';
import { SEED_AUDIO_ENGINE_ID, createDefaultSeedAudioState, type SeedAudioState } from './state';
import { validateSeedAudioState } from './validation';

export const seedAudioProfile: EngineUiProfile<SeedAudioState, SeedAudioRequestEnvelope> = {
	engineId: SEED_AUDIO_ENGINE_ID,
	inputModes: ['text', 'audio', 'image'],
	panel: { kind: 'custom', componentId: 'seed-audio-panel' },
	createDefaultState: () => createDefaultSeedAudioState(),
	parameterSchema: (_state, context) => context.manifest.parameter_schema,
	validate: (state) => validateSeedAudioState(state),
	toRequest: (state) => seedAudioStateToRequest(state),
	fromRequest: (request) => seedAudioStateFromRequest(request)
};
