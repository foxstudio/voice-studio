from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def as_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def is_internal_locator_key(key: str) -> bool:
    normalized = key.strip().lower()
    return (
        normalized
        in {"path", "paths", "directory", "directories"}
        or normalized.endswith(
            ("_path", "_paths", "_directory", "_directories")
        )
    )


def contains_internal_locator(value: Any) -> bool:
    value = as_payload(value)
    if isinstance(value, dict):
        return any(
            is_internal_locator_key(str(key))
            or contains_internal_locator(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_internal_locator(item) for item in value)
    return False


def without_internal_locators(value: Any) -> Any:
    value = as_payload(value)
    if isinstance(value, dict):
        return {
            key: without_internal_locators(item)
            for key, item in value.items()
            if not is_internal_locator_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [without_internal_locators(item) for item in value]
    return value


__all__ = [
    "as_payload",
    "contains_internal_locator",
    "is_internal_locator_key",
    "without_internal_locators",
]
