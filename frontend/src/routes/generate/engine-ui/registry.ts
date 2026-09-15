import type { EngineDetail } from '$lib/api/types';
import { GENERIC_ENGINE_PROFILE_ID, genericEngineUiProfile } from './generic/profile';
import type {
	EngineManifest,
	EngineRequest,
	EngineState,
	EngineStateById,
	EngineUiProfile,
	ResolvedEngineUiProfile
} from './types';

export type EngineUiRegistryErrorCode =
	| 'invalid_engine_id'
	| 'duplicate_profile'
	| 'reserved_profile_id'
	| 'manifest_missing'
	| 'manifest_engine_mismatch'
	| 'manifest_parameter_schema_missing';

export class EngineUiRegistryError extends Error {
	constructor(
		public readonly code: EngineUiRegistryErrorCode,
		message: string
	) {
		super(message);
		this.name = 'EngineUiRegistryError';
	}
}

function requireEngineId(engineId: string): string {
	const normalized = engineId.trim();
	if (!normalized) {
		throw new EngineUiRegistryError('invalid_engine_id', '引擎 ID 不能为空');
	}
	return normalized;
}

function requireManifest(engineId: string, manifest: EngineManifest | null | undefined): EngineManifest {
	if (!manifest) {
		throw new EngineUiRegistryError('manifest_missing', `引擎 ${engineId} 缺少 manifest，无法选择参数面板`);
	}
	if (manifest.engine_id !== engineId) {
		throw new EngineUiRegistryError(
			'manifest_engine_mismatch',
			`请求的引擎 ${engineId} 与 manifest.engine_id ${manifest.engine_id} 不一致`
		);
	}
	if (!Array.isArray(manifest.parameter_schema)) {
		throw new EngineUiRegistryError(
			'manifest_parameter_schema_missing',
			`引擎 ${engineId} 的 manifest 缺少 parameter_schema`
		);
	}
	return manifest;
}

export class EngineUiRegistry {
	private readonly profiles = new Map<string, EngineUiProfile>();

	constructor(private readonly fallbackProfile: EngineUiProfile = genericEngineUiProfile) {}

	register<State = EngineState, Request extends EngineRequest = EngineRequest>(
		profile: EngineUiProfile<State, Request>
	): void {
		const engineId = requireEngineId(profile.engineId);
		if (engineId === GENERIC_ENGINE_PROFILE_ID) {
			throw new EngineUiRegistryError(
				'reserved_profile_id',
				`${GENERIC_ENGINE_PROFILE_ID} 只用于通用回退，不能注册为具体引擎`
			);
		}
		if (this.profiles.has(engineId)) {
			throw new EngineUiRegistryError('duplicate_profile', `引擎 ${engineId} 已注册 UI Profile`);
		}
		this.profiles.set(engineId, profile as EngineUiProfile);
	}

	has(engineId: string): boolean {
		return this.profiles.has(engineId);
	}

	resolve<State = EngineState>(
		engineId: string,
		manifest: EngineManifest | null | undefined
	): ResolvedEngineUiProfile<State> {
		const normalizedEngineId = requireEngineId(engineId);
		const checkedManifest = requireManifest(normalizedEngineId, manifest);
		const registered = this.profiles.get(normalizedEngineId);
		return {
			profile: (registered ?? this.fallbackProfile) as EngineUiProfile<State>,
			context: { engineId: normalizedEngineId, manifest: checkedManifest },
			isFallback: registered === undefined
		};
	}

	resolveEngine<State = EngineState>(engine: EngineDetail): ResolvedEngineUiProfile<State> {
		return this.resolve(engine.manifest.engine_id, engine.manifest);
	}
}

class IsolatedEngineStateById implements EngineStateById {
	private readonly state = new Map<string, EngineState>();

	has(engineId: string): boolean {
		return this.state.has(engineId);
	}

	get<State = EngineState>(engineId: string): State | undefined {
		return this.state.get(engineId) as State | undefined;
	}

	getOrCreate<State = EngineState>(resolved: ResolvedEngineUiProfile<State>): State {
		const { engineId } = resolved.context;
		if (!this.state.has(engineId)) {
			this.state.set(engineId, resolved.profile.createDefaultState(resolved.context));
		}
		return this.state.get(engineId) as State;
	}

	set<State = EngineState>(engineId: string, state: State): void {
		this.state.set(requireEngineId(engineId), state);
	}

	reset<State = EngineState>(resolved: ResolvedEngineUiProfile<State>): State {
		const next = resolved.profile.createDefaultState(resolved.context);
		this.state.set(resolved.context.engineId, next);
		return next;
	}

	delete(engineId: string): boolean {
		return this.state.delete(engineId);
	}

	clear(): void {
		this.state.clear();
	}
}

export function createEngineStateById(): EngineStateById {
	return new IsolatedEngineStateById();
}

export const engineUiRegistry = new EngineUiRegistry();
