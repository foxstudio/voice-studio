from __future__ import annotations

from dataclasses import dataclass

from app.schemas.voice_studio import EngineDetail, EngineManifest
from app.services import engine_health, engine_manifests, engine_policy, qwen3_tts_paths


@dataclass(frozen=True)
class EngineProvider:
    engine_id: str

    @property
    def detail(self) -> EngineDetail:
        detail = engine_manifests.ENGINES[self.engine_id]
        if self.engine_id != qwen3_tts_paths.ENGINE_ID or qwen3_tts_paths.voice_design_available():
            return detail
        patched = detail.model_copy(deep=True)
        patched.manifest.capabilities = [item for item in patched.manifest.capabilities if item != "voice_design"]
        patched.manifest.parameter_schema = [param for param in patched.manifest.parameter_schema if param.key != "voice_design_prompt"]
        patched.manifest.description = patched.manifest.description.replace("、声音设计", "")
        patched.manifest.default_use_case = patched.manifest.default_use_case.replace("、声音设计", "")
        return patched

    @property
    def manifest(self) -> EngineManifest:
        return self.detail.manifest

    @property
    def runner_kind(self) -> engine_policy.RunnerKind:
        return engine_policy.runner_kind_for(self.engine_id)

    @property
    def timeout_seconds(self) -> int:
        return engine_policy.timeout_seconds_for(self.engine_id)

    @property
    def is_cloud(self) -> bool:
        return engine_policy.is_cloud_engine(self.engine_id)

    @property
    def requires_idempotency_marker(self) -> bool:
        return engine_policy.requires_idempotency_marker(self.engine_id)

    def health_check(self) -> dict:
        return engine_health.health_check(self.engine_id)


def resolve_engine_id(engine_id: str) -> str:
    return engine_policy.resolve_engine_id(engine_id)


def list_providers() -> list[EngineProvider]:
    return [EngineProvider(engine_id) for engine_id in engine_manifests.ENGINES]


def get_provider(engine_id: str) -> EngineProvider | None:
    resolved = resolve_engine_id(engine_id)
    if resolved not in engine_manifests.ENGINES:
        return None
    return EngineProvider(resolved)


def list_engine_details() -> list[EngineDetail]:
    return [provider.detail for provider in list_providers()]


def get_engine_detail(engine_id: str) -> EngineDetail | None:
    provider = get_provider(engine_id)
    return provider.detail if provider else None
