from __future__ import annotations

from .base import EngineAdapter


class AdapterRegistry:
    """Explicit adapter registry with no import-time global mutations."""

    def __init__(self) -> None:
        self._adapters: dict[str, EngineAdapter] = {}

    def register(self, adapter: EngineAdapter) -> None:
        engine_id = adapter.engine_id.strip()
        if not engine_id:
            raise ValueError("engine_id must not be empty")
        if engine_id in self._adapters:
            raise ValueError(f"adapter already registered: {engine_id}")
        self._adapters[engine_id] = adapter

    def get(self, engine_id: str) -> EngineAdapter | None:
        return self._adapters.get(engine_id)

    def require(self, engine_id: str) -> EngineAdapter:
        adapter = self.get(engine_id)
        if adapter is None:
            raise KeyError(f"adapter is not registered: {engine_id}")
        return adapter


def build_default_registry() -> AdapterRegistry:
    """Build the opt-in registry without mutating process-global state."""

    from .seed_audio.adapter import SeedAudioAdapter

    registry = AdapterRegistry()
    registry.register(SeedAudioAdapter())
    return registry
