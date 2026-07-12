from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EngineAdapter(Protocol):
    """Minimal boundary implemented by model-specific request adapters."""

    engine_id: str

    def build_payload(self, request: Any) -> dict[str, Any]: ...

    def execute(self, request: Any, **context: Any) -> dict[str, Any]: ...
