"""External, development-only checkpoints for localization workflow replay.

Formal tasks never read these files.  A development harness may inject this
writer into the canonical workflow so each completed atomic result is saved
outside project data before the next task starts.
"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domains.video_localization.schemas import now_iso


CHECKPOINT_SCHEMA_VERSION = "localization-development-checkpoint-v2"
READABLE_CHECKPOINT_SCHEMA_VERSIONS = frozenset(
    {
        "localization-development-checkpoint-v1",
        CHECKPOINT_SCHEMA_VERSION,
    }
)
MATERIALIZATION_SCHEMA_VERSION = "localization-development-materialization-v1"


def _safe_identifier(value: str, *, label: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    if not safe_value:
        raise ValueError(f"{label}缺少有效 ID。")
    return safe_value


def _serialized_result(result: Any) -> dict[str, Any]:
    model_dump = getattr(result, "model_dump", None)
    if not callable(model_dump):
        raise TypeError("开发检查点只接受可序列化的版本化原子任务结果。")
    serialized = model_dump(mode="json")
    if not isinstance(serialized, dict):
        raise TypeError("原子任务结果必须序列化为对象。")
    return serialized


def result_fingerprint(serialized: dict[str, Any]) -> str:
    """Return the domain fingerprint or a stable digest for every result."""

    explicit = str(
        serialized.get("result_fingerprint")
        or serialized.get("source_fingerprint")
        or serialized.get("context_intent_fingerprint")
        or ""
    ).strip()
    if explicit:
        return explicit
    return hashlib.sha256(
        json.dumps(
            serialized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class LocalizationDevelopmentMaterializationEnvelope(BaseModel):
    """Versioned persisted output used only by development execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = MATERIALIZATION_SCHEMA_VERSION
    project_id: str = Field(min_length=1)
    development_session_id: str = Field(min_length=1)
    produced_by_operation_id: str = Field(min_length=1)
    atomic_operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    result_contract_version: str = Field(min_length=1)
    behavior_fingerprint: str = Field(min_length=1)
    dependency_fingerprints: dict[str, str]
    result_fingerprint: str = Field(min_length=1)
    materialization_fingerprint: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    result: dict[str, Any]


class LocalizationDevelopmentCheckpointEnvelope(BaseModel):
    """Versioned trace checkpoint for one completed atomic sub-operation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = CHECKPOINT_SCHEMA_VERSION
    project_id: str = Field(min_length=1)
    workflow_operation_id: str = Field(min_length=1)
    atomic_operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    result_contract_version: str = Field(min_length=1)
    behavior_fingerprint: str | None = None
    result_fingerprint: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    result: dict[str, Any]


@dataclass(frozen=True)
class LocalizationDevelopmentMaterializationLookup:
    status: str
    reason: str
    result: BaseModel | None = None
    envelope: LocalizationDevelopmentMaterializationEnvelope | None = None


def development_checkpoint_exists(root: Path, *, workflow_operation_id: str, step_id: str) -> bool:
    """Distinguish missing evidence from invalid evidence without reading its payload."""
    return (root / _safe_identifier(workflow_operation_id, label="工作流")
            / _safe_identifier(step_id, label="步骤") / "checkpoint.json").exists()


class LocalizationDevelopmentCheckpointWriter:
    """Atomically persist one versioned envelope per completed atomic task."""

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        workflow_operation_id: str,
        behavior_fingerprint_resolver: (
            Callable[[str], str | None] | None
        ) = None,
    ) -> None:
        self.root = Path(root)
        self.project_id = project_id
        self.workflow_operation_id = workflow_operation_id
        self.behavior_fingerprint_resolver = (
            behavior_fingerprint_resolver
        )

    def __call__(self, step_id: str, result: Any) -> None:
        safe_step_id = _safe_identifier(
            step_id,
            label="开发检查点原子任务",
        )
        serialized = _serialized_result(result)

        contract_version = str(
            serialized.get("contract_version")
            or serialized.get("schema_version")
            or ""
        ).strip()
        if not contract_version:
            raise ValueError("原子任务结果缺少版本化输出契约。")

        artifact_dir = (
            self.root
            / _safe_identifier(
                self.workflow_operation_id,
                label="开发检查点工作流",
            )
            / safe_step_id
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "checkpoint.json"
        temporary_path = artifact_dir / "checkpoint.json.tmp"
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "project_id": self.project_id,
            "workflow_operation_id": self.workflow_operation_id,
            "atomic_operation_id": (
                f"{self.workflow_operation_id}:{step_id}"
            ),
            "step_id": step_id,
            "result_contract_version": contract_version,
            "behavior_fingerprint": (
                self.behavior_fingerprint_resolver(step_id)
                if self.behavior_fingerprint_resolver is not None
                else None
            ),
            "result_fingerprint": result_fingerprint(serialized),
            "created_at": now_iso(),
            "result": serialized,
        }
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(artifact_path)


def load_development_checkpoint(
    root: Path,
    *,
    project_id: str,
    workflow_operation_id: str,
    step_id: str,
    result_model: type[BaseModel],
    expected_behavior_fingerprint: str | None = None,
) -> BaseModel | None:
    """Load one typed trace checkpoint without exposing it to formal runs."""

    safe_step_id = _safe_identifier(
        step_id,
        label="开发检查点原子任务",
    )
    path = (
        Path(root)
        / _safe_identifier(
            workflow_operation_id,
            label="开发检查点工作流",
        )
        / safe_step_id
        / "checkpoint.json"
    )
    if not path.is_file():
        return None
    try:
        envelope = LocalizationDevelopmentCheckpointEnvelope.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        if (
            envelope.schema_version
            not in READABLE_CHECKPOINT_SCHEMA_VERSIONS
            or envelope.project_id != project_id
            or envelope.workflow_operation_id != workflow_operation_id
            or envelope.step_id != step_id
            or envelope.atomic_operation_id
            != f"{workflow_operation_id}:{step_id}"
            or (
                expected_behavior_fingerprint is not None
                and envelope.behavior_fingerprint
                != expected_behavior_fingerprint
            )
        ):
            return None
        result = result_model.model_validate(envelope.result)
    except (OSError, json.JSONDecodeError, ValidationError):
        return None
    serialized = result.model_dump(mode="json")
    if result_fingerprint(serialized) != envelope.result_fingerprint:
        return None
    return result


def load_development_checkpoints_by_prefix(
    root: Path,
    *,
    project_id: str,
    workflow_operation_id: str,
    step_id_prefix: str,
    result_model: type[BaseModel],
    expected_behavior_fingerprint: str | None = None,
) -> list[BaseModel]:
    """Load every valid typed checkpoint in one bounded atomic-step family."""

    safe_workflow_id = _safe_identifier(
        workflow_operation_id,
        label="开发检查点工作流",
    )
    safe_prefix = _safe_identifier(
        step_id_prefix,
        label="开发检查点原子任务前缀",
    )
    workflow_root = Path(root) / safe_workflow_id
    if not workflow_root.is_dir():
        return []
    results: list[BaseModel] = []
    for artifact_dir in sorted(workflow_root.iterdir()):
        if (
            not artifact_dir.is_dir()
            or not artifact_dir.name.startswith(safe_prefix)
        ):
            continue
        path = artifact_dir / "checkpoint.json"
        if not path.is_file():
            continue
        try:
            envelope = (
                LocalizationDevelopmentCheckpointEnvelope.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            )
            if (
                envelope.schema_version
                not in READABLE_CHECKPOINT_SCHEMA_VERSIONS
                or envelope.project_id != project_id
                or envelope.workflow_operation_id
                != workflow_operation_id
                or not envelope.step_id.startswith(step_id_prefix)
                or envelope.atomic_operation_id
                != f"{workflow_operation_id}:{envelope.step_id}"
                or (
                    expected_behavior_fingerprint is not None
                    and envelope.behavior_fingerprint
                    != expected_behavior_fingerprint
                )
            ):
                continue
            result = result_model.model_validate(envelope.result)
        except (OSError, json.JSONDecodeError, ValidationError):
            continue
        serialized = result.model_dump(mode="json")
        if result_fingerprint(serialized) != envelope.result_fingerprint:
            continue
        results.append(result)
    return results


class LocalizationDevelopmentMaterializationStore:
    """Typed read/write boundary for reusable development node results."""

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        development_session_id: str,
    ) -> None:
        self.root = Path(root)
        self.project_id = project_id
        self.development_session_id = development_session_id
        self._safe_session_id = _safe_identifier(
            development_session_id,
            label="本土化开发会话",
        )

    def save(
        self,
        step_id: str,
        result: Any,
        *,
        produced_by_operation_id: str,
        behavior_fingerprint: str,
        dependency_fingerprints: dict[str, str],
    ) -> LocalizationDevelopmentMaterializationEnvelope:
        safe_step_id = _safe_identifier(
            step_id,
            label="本土化开发节点",
        )
        serialized = _serialized_result(result)
        contract_version = str(
            serialized.get("contract_version")
            or serialized.get("schema_version")
            or ""
        ).strip()
        if not contract_version:
            raise ValueError("原子任务结果缺少版本化输出契约。")
        serialized_fingerprint = result_fingerprint(serialized)
        materialization_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "step_id": step_id,
                    "behavior_fingerprint": behavior_fingerprint,
                    "dependency_fingerprints": dependency_fingerprints,
                    "result_fingerprint": serialized_fingerprint,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        envelope = LocalizationDevelopmentMaterializationEnvelope(
            project_id=self.project_id,
            development_session_id=self.development_session_id,
            produced_by_operation_id=produced_by_operation_id,
            atomic_operation_id=(
                f"{self.development_session_id}:{step_id}"
            ),
            step_id=step_id,
            result_contract_version=contract_version,
            behavior_fingerprint=behavior_fingerprint,
            dependency_fingerprints=dict(dependency_fingerprints),
            result_fingerprint=serialized_fingerprint,
            materialization_fingerprint=materialization_fingerprint,
            created_at=now_iso(),
            result=serialized,
        )
        artifact_dir = (
            self.root
            / self._safe_session_id
            / "nodes"
            / safe_step_id
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "materialization.json"
        temporary_path = artifact_dir / "materialization.json.tmp"
        temporary_path.write_text(
            envelope.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(artifact_path)
        return envelope

    def load(
        self,
        step_id: str,
        *,
        result_model: type[BaseModel],
        expected_contract_version: str,
        expected_behavior_fingerprint: str,
        expected_dependency_fingerprints: dict[str, str],
    ) -> LocalizationDevelopmentMaterializationLookup:
        path = self.path_for(step_id)
        if not path.is_file():
            return LocalizationDevelopmentMaterializationLookup(
                status="missing",
                reason="没有找到可复用的上游节点结果。",
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            envelope = (
                LocalizationDevelopmentMaterializationEnvelope.model_validate(
                    raw
                )
            )
        except (OSError, json.JSONDecodeError, ValidationError):
            return LocalizationDevelopmentMaterializationLookup(
                status="invalid",
                reason="节点快照损坏或不符合版本化契约。",
            )
        identity_matches = (
            envelope.schema_version == MATERIALIZATION_SCHEMA_VERSION
            and envelope.project_id == self.project_id
            and envelope.development_session_id
            == self.development_session_id
            and envelope.step_id == step_id
        )
        if not identity_matches:
            return LocalizationDevelopmentMaterializationLookup(
                status="invalid",
                reason="节点快照不属于当前项目、开发会话或节点。",
                envelope=envelope,
            )
        if envelope.result_contract_version != expected_contract_version:
            return LocalizationDevelopmentMaterializationLookup(
                status="stale",
                reason="节点输出契约已经变化。",
                envelope=envelope,
            )
        if envelope.behavior_fingerprint != expected_behavior_fingerprint:
            return LocalizationDevelopmentMaterializationLookup(
                status="stale",
                reason="节点代码、提示词、模型或关键配置已经变化。",
                envelope=envelope,
            )
        if (
            envelope.dependency_fingerprints
            != expected_dependency_fingerprints
        ):
            return LocalizationDevelopmentMaterializationLookup(
                status="stale",
                reason="上游节点结果已经变化。",
                envelope=envelope,
            )
        try:
            result = result_model.model_validate(envelope.result)
        except ValidationError:
            return LocalizationDevelopmentMaterializationLookup(
                status="invalid",
                reason="节点结果无法通过当前类型校验。",
                envelope=envelope,
            )
        serialized = result.model_dump(mode="json")
        if result_fingerprint(serialized) != envelope.result_fingerprint:
            return LocalizationDevelopmentMaterializationLookup(
                status="invalid",
                reason="节点结果内容与校验指纹不一致。",
                envelope=envelope,
            )
        return LocalizationDevelopmentMaterializationLookup(
            status="valid",
            reason="上游节点结果仍然有效。",
            result=result,
            envelope=envelope,
        )

    def path_for(self, step_id: str) -> Path:
        return self.artifact_dir_for(step_id) / "materialization.json"

    def artifact_dir_for(self, step_id: str) -> Path:
        safe_step_id = _safe_identifier(
            step_id,
            label="本土化开发节点",
        )
        return (
            self.root
            / self._safe_session_id
            / "nodes"
            / safe_step_id
        )


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "MATERIALIZATION_SCHEMA_VERSION",
    "LocalizationDevelopmentCheckpointEnvelope",
    "LocalizationDevelopmentCheckpointWriter",
    "LocalizationDevelopmentMaterializationEnvelope",
    "LocalizationDevelopmentMaterializationLookup",
    "LocalizationDevelopmentMaterializationStore",
    "load_development_checkpoint",
    "result_fingerprint",
]
