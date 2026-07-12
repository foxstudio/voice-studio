import type { EngineRequest, EngineUiContext, EngineUiProfile, EngineValidationIssue } from '../types';

export const GENERIC_ENGINE_PROFILE_ID = '__generic__';

export interface GenericEngineState {
	values: Record<string, unknown>;
}

function defaults(context: EngineUiContext): Record<string, unknown> {
	return Object.fromEntries(context.manifest.parameter_schema.map((parameter) => [parameter.key, parameter.default]));
}

function isMissingRequiredValue(value: unknown): boolean {
	return value === undefined || value === null || value === '';
}

/**
 * Compatibility profile for engines without a custom panel. It reflects the
 * manifest's scalar parameter schema and deliberately contains no model rules.
 */
export const genericEngineUiProfile: EngineUiProfile<GenericEngineState> = {
	engineId: GENERIC_ENGINE_PROFILE_ID,
	inputModes: ['text'],
	panel: {
		kind: 'generic',
		componentId: 'generic-manifest-panel'
	},
	createDefaultState(context) {
		return { values: defaults(context) };
	},
	parameterSchema(_state, context) {
		return context.manifest.parameter_schema;
	},
	validate(state, context) {
		const errors: EngineValidationIssue[] = [];
		for (const parameter of context.manifest.parameter_schema) {
			if (parameter.required && isMissingRequiredValue(state.values[parameter.key])) {
				errors.push({
					code: 'required_parameter_missing',
					path: `parameters.${parameter.key}`,
					message: `${parameter.label}为必填项`
				});
			}
		}
		return { errors, warnings: [] };
	},
	toRequest(state, context) {
		const allowed = new Set(context.manifest.parameter_schema.map((parameter) => parameter.key));
		const parameters = Object.fromEntries(
			Object.entries(state.values).filter(([key, value]) => allowed.has(key) && value !== undefined)
		);
		return { engine_id: context.engineId, ...parameters };
	},
	fromRequest(request: Readonly<EngineRequest>, context) {
		const values = defaults(context);
		for (const parameter of context.manifest.parameter_schema) {
			if (request[parameter.key] !== undefined) values[parameter.key] = request[parameter.key];
		}
		return { values };
	}
};
