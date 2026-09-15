from __future__ import annotations

from dataclasses import dataclass

from app.schemas.voice_studio import EngineCompatibility, EngineDetail, EngineManifest
from app.services import engine_compatibility, engine_health, engine_manifests, engine_policy, qwen3_tts_paths


@dataclass(frozen=True)
class EngineProvider:
    engine_id: str

    @property
    def detail(self) -> EngineDetail:
        detail = engine_manifests.ENGINES[self.engine_id].model_copy(deep=True)
        if self.engine_id == qwen3_tts_paths.ENGINE_ID and not qwen3_tts_paths.voice_design_available():
            detail.manifest.capabilities = [
                item for item in detail.manifest.capabilities if item != "voice_design"
            ]
            detail.manifest.parameter_schema = [
                param for param in detail.manifest.parameter_schema if param.key != "voice_design_prompt"
            ]
            detail.manifest.description = detail.manifest.description.replace("、声音设计", "")
            detail.manifest.default_use_case = detail.manifest.default_use_case.replace("、声音设计", "")
        detail.compatibility = self.compatibility
        return detail

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

    @property
    def compatibility(self) -> EngineCompatibility:
        return engine_compatibility.evaluate(self.engine_id)

    def health_check(self) -> dict:
        compatibility = self.compatibility
        if compatibility.compatible is False:
            return {
                "healthy": False,
                "status": compatibility.reason_code or "platform_unsupported",
                "detail": compatibility.message,
                "compatibility": compatibility.model_dump(mode="json"),
            }
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
