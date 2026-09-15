"""Typed recovery of completed formal-localization checkpoints on retry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from app.domains.video_localization import development_checkpoints
from app.domains.video_localization.localization_workflow_nodes import (
    LOCALIZATION_WORKFLOW_NODE_SPECS,
    checkpoint_behavior_fingerprint,
)
from app.domains.video_localization.workflow_contracts import (
    LOCALIZATION_WORKFLOW_V3_DEFINITION,
)


class LocalizationFormalRetryRecovery:
    """Reuse only a contiguous, typed, lineage-compatible formal prefix.

    Formal first runs never instantiate this class. A retry may read only the
    checkpoint directory owned by its immutable predecessor operation.
    """

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        source_operation_ids: list[str],
    ) -> None:
        self.root = Path(root)
        self.project_id = project_id
        self.source_operation_ids = list(dict.fromkeys(source_operation_ids))
        self.reused_step_ids: list[str] = []
        self._contract_versions = {
            task.id: str(task.output_contract_version or "")
            for stage in LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
            for task in stage.atomic_tasks
        }

    def recover(
        self,
        step_id: str,
        *,
        expected_lineage: dict[str, str] | None = None,
        expected_route: dict[str, Any] | None = None,
        current_result: BaseModel | None = None,
        is_reusable: Callable[[BaseModel], bool] | None = None,
    ) -> BaseModel | None:
        spec = LOCALIZATION_WORKFLOW_NODE_SPECS[step_id]
        candidate = self._load_first_checkpoint(
            step_id,
            result_model=spec.result_model,
            expected_behavior_fingerprint=(
                self._required_behavior_fingerprint(step_id)
            ),
        )
        if candidate is None:
            return None
        serialized = candidate.model_dump(mode="json")
        if (
            str(serialized.get("contract_version") or serialized.get("schema_version") or "")
            != self._contract_versions[step_id]
        ):
            return None
        if current_result is not None and (serialized != current_result.model_dump(mode="json")):
            return None
        for field, expected in (expected_lineage or {}).items():
            if str(serialized.get(field) or "") != str(expected):
                return None
        if expected_route is not None:
            route = serialized.get("route")
            if not isinstance(route, dict) or route != expected_route:
                return None
        if is_reusable is not None and not is_reusable(candidate):
            return None
        self.reused_step_ids.append(step_id)
        return candidate

    def load_generation_checkpoint(
        self,
        result_model: type[BaseModel],
    ) -> BaseModel | None:
        return self._load_first_checkpoint(
            "generate_localization_spoken_script.chunk_responses",
            result_model=result_model,
            expected_behavior_fingerprint=(
                self._required_behavior_fingerprint(
                    "generate_localization_spoken_script"
                )
            ),
        )

    def load_checkpoints_by_prefix(
        self,
        step_id_prefix: str,
        *,
        result_model: type[BaseModel],
    ) -> list[BaseModel]:
        """Load the newest predecessor's compatible atomic batch family."""

        for source_operation_id in self.source_operation_ids:
            candidates = development_checkpoints.load_development_checkpoints_by_prefix(
                self.root,
                project_id=self.project_id,
                workflow_operation_id=source_operation_id,
                step_id_prefix=step_id_prefix,
                result_model=result_model,
                expected_behavior_fingerprint=(
                    self._required_behavior_fingerprint(
                        step_id_prefix
                    )
                ),
            )
            if candidates:
                return candidates
        return []

    def _load_first_checkpoint(
        self,
        step_id: str,
        *,
        result_model: type[BaseModel],
        expected_behavior_fingerprint: str,
    ) -> BaseModel | None:
        for source_operation_id in self.source_operation_ids:
            candidate = development_checkpoints.load_development_checkpoint(
                self.root,
                project_id=self.project_id,
                workflow_operation_id=source_operation_id,
                step_id=step_id,
                result_model=result_model,
                expected_behavior_fingerprint=(
                    expected_behavior_fingerprint
                ),
            )
            if candidate is not None:
                return candidate
        return None

    @staticmethod
    def _required_behavior_fingerprint(
        step_id_prefix: str,
    ) -> str:
        fingerprint = checkpoint_behavior_fingerprint(step_id_prefix)
        if fingerprint is None:
            raise ValueError(
                "正式重试检查点不属于已注册的本土化节点："
                f"{step_id_prefix}"
            )
        return fingerprint


__all__ = ["LocalizationFormalRetryRecovery"]
