"""Shared behavior fingerprints for model-driven workflow snapshots."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Iterable


def source_behavior_fingerprint(
    *,
    workflow_id: str,
    workflow_schema_version: str,
    step_id: str,
    output_contract_version: str,
    implementation_modules: Iterable[object] = (),
    implementation_objects: Iterable[object] = (),
    behavior_context: dict[str, Any] | None = None,
) -> str:
    """Hash the code and non-secret configuration that define one node.

    A development snapshot may be reused only while this value remains
    unchanged. Hashing source boundaries catches fixed-prompt and request
    whitelist changes without persisting prompt bodies in snapshot metadata.
    """

    module_hashes: dict[str, str] = {}
    for module in implementation_modules:
        source_file = inspect.getsourcefile(module)
        if not source_file:
            raise ValueError(f"无法定位节点实现源码：{step_id}")
        path = Path(source_file)
        module_hashes[str(getattr(module, "__name__", path.name))] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
        )

    object_hashes: dict[str, str] = {}
    for implementation in implementation_objects:
        label = (
            f"{getattr(implementation, '__module__', '')}."
            f"{getattr(implementation, '__qualname__', repr(implementation))}"
        )
        object_hashes[label] = hashlib.sha256(
            inspect.getsource(implementation).encode("utf-8")
        ).hexdigest()

    payload = {
        "workflow_schema_version": workflow_schema_version,
        "workflow_id": workflow_id,
        "step_id": step_id,
        "output_contract_version": output_contract_version,
        "implementation_modules": module_hashes,
        "implementation_objects": object_hashes,
        "behavior_context": behavior_context or {},
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = ["source_behavior_fingerprint"]
