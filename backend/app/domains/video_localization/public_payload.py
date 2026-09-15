from __future__ import annotations

from typing import Any

from app.schemas.public_payload import (
    as_payload,
    contains_internal_locator,
    is_internal_locator_key,
    without_internal_locators,
)


def public_operation_payload(value: Any) -> dict[str, Any]:
    payload = dict(as_payload(value))
    payload["result_summary"] = without_internal_locators(
        payload.get("result_summary", {})
    )
    payload["parameters"] = without_internal_locators(
        payload.get("parameters", {})
    )
    return payload


__all__ = [
    "as_payload",
    "contains_internal_locator",
    "is_internal_locator_key",
    "public_operation_payload",
    "without_internal_locators",
]
