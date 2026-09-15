import type { EngineDetail, ParameterSchema } from '$lib/api/types';

export type EngineManifest = EngineDetail['manifest'];
export type EngineInputMode = string;
export type EngineState = unknown;
export type EngineRequest = Record<string, unknown>;

export interface EngineUiContext {
	engineId: string;
	manifest: EngineManifest;
}

export interface EngineValidationIssue {
	code: string;
	message: string;
	path?: string;
}

export interface EngineValidationResult {
	errors: EngineValidationIssue[];
	warnings: EngineValidationIssue[];
}

/**
 * A descriptor is intentionally used before wiring Svelte components into the
 * workbench. It gives the shell a stable panel identity without coupling the
 * registry to a component implementation or a global store.
 */
export interface EnginePanelDescriptor {
	kind: 'generic' | 'custom';
	componentId: string;
}

/**
 * The complete UI contract owned by one engine.
 *
 * Implementations must not read or write another engine's state. All state is
 * supplied explicitly, which lets the workbench keep one isolated draft per
 * engine id.
 */
export interface EngineUiProfile<State = EngineState, Request extends EngineRequest = EngineRequest> {
	engineId: string;
	inputModes: readonly EngineInputMode[];
	panel: EnginePanelDescriptor;
	createDefaultState(context: EngineUiContext): State;
	parameterSchema(state: State, context: EngineUiContext): readonly ParameterSchema[];
	validate(state: State, context: EngineUiContext): EngineValidationResult;
	toRequest(state: State, context: EngineUiContext): Request;
	fromRequest(request: Readonly<EngineRequest>, context: EngineUiContext): State;
}

export interface ResolvedEngineUiProfile<State = EngineState, Request extends EngineRequest = EngineRequest> {
	profile: EngineUiProfile<State, Request>;
	context: EngineUiContext;
	isFallback: boolean;
}

/**
 * Per-engine draft storage. Implementations must key state by the requested
 * engine id, including engines that share the generic fallback profile.
 */
export interface EngineStateById {
	has(engineId: string): boolean;
	get<State = EngineState>(engineId: string): State | undefined;
	getOrCreate<State = EngineState>(resolved: ResolvedEngineUiProfile<State>): State;
	set<State = EngineState>(engineId: string, state: State): void;
	reset<State = EngineState>(resolved: ResolvedEngineUiProfile<State>): State;
	delete(engineId: string): boolean;
	clear(): void;
}
