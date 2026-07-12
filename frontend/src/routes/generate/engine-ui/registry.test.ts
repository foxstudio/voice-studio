import { describe, expect, it } from 'vitest';
import type { EngineDetail, ParameterSchema } from '$lib/api/types';
import { genericEngineUiProfile, type GenericEngineState } from './generic/profile';
import { createEngineStateById, EngineUiRegistry, EngineUiRegistryError } from './registry';
import type { EngineUiProfile } from './types';

function parameter(overrides: Partial<ParameterSchema> & Pick<ParameterSchema, 'key' | 'label'>): ParameterSchema {
	return {
		description: null,
		type: 'text',
		level: 'basic',
		default: '',
		min: null,
		max: null,
		step: null,
		options: [],
		required: false,
		capability: null,
		...overrides
	};
}

function engine(engineId: string, parameterSchema: ParameterSchema[] = []): EngineDetail {
	return {
		manifest: {
			engine_id: engineId,
			display_name: engineId,
			engine_type: 'local',
			provider: 'test',
			version: 'test',
			description: '',
			supported_languages: ['zh'],
			capabilities: ['text_to_speech'],
			sample_rate: 24000,
			max_tokens: null,
			privacy_level: 'local',
			default_use_case: '',
			parameter_schema: parameterSchema
		},
		state: {
			engine_id: engineId,
			status: 'loaded',
			model_path: null,
			error_message: null,
			loaded_at: null
		}
	};
}

interface CustomState {
	mode: 'text';
	prompt: string;
}

function customProfile(engineId: string): EngineUiProfile<CustomState> {
	return {
		engineId,
		inputModes: ['text'],
		panel: { kind: 'custom', componentId: `${engineId}-panel` },
		createDefaultState: () => ({ mode: 'text', prompt: '' }),
		parameterSchema: () => [],
		validate: (state) => ({
			errors: state.prompt ? [] : [{ code: 'prompt_required', message: '请输入提示词', path: 'prompt' }],
			warnings: []
		}),
		toRequest: (state, context) => ({ engine_id: context.engineId, input_mode: state.mode, text: state.prompt }),
		fromRequest: (request) => ({ mode: 'text', prompt: String(request.text ?? '') })
	};
}

describe('EngineUiRegistry', () => {
	it('selects an exactly registered profile and exposes its full contract', () => {
		const registry = new EngineUiRegistry();
		const profile = customProfile('custom-engine');
		registry.register(profile);

		const resolved = registry.resolve<CustomState>('custom-engine', engine('custom-engine').manifest);
		const state = resolved.profile.fromRequest({ text: '测试描述' }, resolved.context);

		expect(resolved.profile).toBe(profile);
		expect(resolved.isFallback).toBe(false);
		expect(resolved.profile.validate(state, resolved.context).errors).toEqual([]);
		expect(resolved.profile.toRequest(state, resolved.context)).toEqual({
			engine_id: 'custom-engine',
			input_mode: 'text',
			text: '测试描述'
		});
	});

	it('falls back to the generic manifest profile for an unknown engine', () => {
		const registry = new EngineUiRegistry();
		const unknown = engine('future-engine', [
			parameter({ key: 'temperature', label: '随机性', type: 'number', default: 0.7 }),
			parameter({ key: 'required_text', label: '必填描述', required: true })
		]);

		const resolved = registry.resolve<GenericEngineState>('future-engine', unknown.manifest);
		const state = resolved.profile.createDefaultState(resolved.context);

		expect(resolved.profile).toBe(genericEngineUiProfile);
		expect(resolved.isFallback).toBe(true);
		expect(resolved.profile.parameterSchema(state, resolved.context)).toBe(unknown.manifest.parameter_schema);
		expect(resolved.profile.validate(state, resolved.context).errors).toEqual([
			expect.objectContaining({ code: 'required_parameter_missing', path: 'parameters.required_text' })
		]);
		expect(resolved.profile.toRequest({ values: { temperature: 0.9, ignored: 'no' } }, resolved.context)).toEqual({
			engine_id: 'future-engine',
			temperature: 0.9
		});
	});

	it('rejects duplicate and reserved registrations with actionable errors', () => {
		const registry = new EngineUiRegistry();
		registry.register(customProfile('same-engine'));

		expect(() => registry.register(customProfile('same-engine'))).toThrowError(
			expect.objectContaining<Partial<EngineUiRegistryError>>({ code: 'duplicate_profile' })
		);
		expect(() => registry.register(genericEngineUiProfile)).toThrowError(
			expect.objectContaining<Partial<EngineUiRegistryError>>({ code: 'reserved_profile_id' })
		);
	});

	it('rejects missing, mismatched, or malformed manifests before resolving a panel', () => {
		const registry = new EngineUiRegistry();

		expect(() => registry.resolve('future-engine', undefined)).toThrowError(
			expect.objectContaining<Partial<EngineUiRegistryError>>({ code: 'manifest_missing' })
		);
		expect(() => registry.resolve('future-engine', engine('other-engine').manifest)).toThrowError(
			expect.objectContaining<Partial<EngineUiRegistryError>>({ code: 'manifest_engine_mismatch' })
		);
		const malformed = { ...engine('future-engine').manifest, parameter_schema: undefined };
		expect(() => registry.resolve('future-engine', malformed as never)).toThrowError(
			expect.objectContaining<Partial<EngineUiRegistryError>>({ code: 'manifest_parameter_schema_missing' })
		);
	});
});

describe('engine state isolation', () => {
	it('keeps independent drafts for registered and fallback engines', () => {
		const registry = new EngineUiRegistry();
		registry.register(customProfile('custom-engine'));
		const stateById = createEngineStateById();
		const custom = registry.resolve<CustomState>('custom-engine', engine('custom-engine').manifest);
		const generic = registry.resolve<GenericEngineState>(
			'generic-engine',
			engine('generic-engine', [parameter({ key: 'speed', label: '语速', default: 1 })]).manifest
		);

		const customState = stateById.getOrCreate(custom);
		customState.prompt = '只属于 custom-engine';
		const genericState = stateById.getOrCreate(generic);
		genericState.values.speed = 1.25;

		expect(stateById.get<CustomState>('custom-engine')).toEqual({ mode: 'text', prompt: '只属于 custom-engine' });
		expect(stateById.get<GenericEngineState>('generic-engine')).toEqual({ values: { speed: 1.25 } });
		expect(stateById.getOrCreate(custom)).toBe(customState);

		const resetCustom = stateById.reset(custom);
		expect(resetCustom).toEqual({ mode: 'text', prompt: '' });
		expect(stateById.get<GenericEngineState>('generic-engine')).toEqual({ values: { speed: 1.25 } });
	});
});
