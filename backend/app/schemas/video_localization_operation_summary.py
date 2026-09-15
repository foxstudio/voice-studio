from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SUMMARY_CORE_SCHEMA_VERSION = "operation-summary-core-v1"


class OperationSummaryCoreV1(BaseModel):
    """Path-free, rebuildable operation list payload contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary_schema_version: Literal[
        "operation-summary-core-v1"
    ] = SUMMARY_CORE_SCHEMA_VERSION
    operation_id: str
    project_id: str
    label: str | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    error_code: str | None = None
    error_message: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    detail_available: Literal[True] = True


def operation_summary_core_json(
    core: OperationSummaryCoreV1,
) -> str:
    return json.dumps(
        core.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def operation_summary_core_fingerprint(
    core: OperationSummaryCoreV1,
) -> str:
    encoded = operation_summary_core_json(core).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "OperationSummaryCoreV1",
    "SUMMARY_CORE_SCHEMA_VERSION",
    "operation_summary_core_fingerprint",
    "operation_summary_core_json",
]
