from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class ProviderAlreadyRegisteredError(ValueError):
    pass


class ProviderNotFoundError(LookupError):
    pass


class ProviderRegistry:
    """Small instance-owned registry so tests and callers can inject providers."""

    def __init__(self, providers: Iterable[Any] = ()) -> None:
        self._providers: dict[str, Any] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: Any, *, replace: bool = False) -> Any:
        provider_id = str(getattr(provider, "provider_id", "")).strip()
        if not provider_id:
            raise ValueError("ASR provider must define a non-empty provider_id")
        if provider_id in self._providers and not replace:
            raise ProviderAlreadyRegisteredError(f"ASR provider is already registered: {provider_id}")
        self._providers[provider_id] = provider
        return provider

    def unregister(self, provider_id: str) -> Any | None:
        return self._providers.pop(provider_id, None)

    def get(self, provider_id: str) -> Any | None:
        return self._providers.get(provider_id)

    def require(self, provider_id: str) -> Any:
        provider = self.get(provider_id)
        if provider is None:
            raise ProviderNotFoundError(f"Unknown ASR provider: {provider_id}")
        return provider

    def list_provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def list_providers(self) -> tuple[Any, ...]:
        return tuple(self._providers[key] for key in self.list_provider_ids())

    def __contains__(self, provider_id: object) -> bool:
        return provider_id in self._providers


AsrProviderRegistry = ProviderRegistry
default_registry = ProviderRegistry()
