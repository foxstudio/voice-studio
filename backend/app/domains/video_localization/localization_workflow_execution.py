"""Execution strategies for formal and incremental localization runs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable

from app.domains.video_localization.development_checkpoints import (
    LocalizationDevelopmentMaterializationStore,
)
from app.domains.video_localization.localization_workflow_nodes import (
    LOCALIZATION_WORKFLOW_NODE_SPECS,
    node_behavior_fingerprint,
)
from app.domains.video_localization.workflow_contracts import (
    LOCALIZATION_WORKFLOW_V3_DEFINITION,
)
from app.domains.video_localization.workflow_graph import WorkflowGraph
from app.domains.video_localization.workflow_ledger import (
    LocalizationWorkflowLedger,
)


@dataclass(frozen=True)
class LocalizationDevelopmentExecutionConfig:
    development_session_id: str
    target_step_id: str
    snapshot_root: Path
    force_target: bool = True
    recover_verified_candidates: bool = False
    retry_unknown_batch_step_id: str | None = None


class LocalizationDevelopmentTargetReached(Exception):
    """Internal control signal emitted after the requested node is materialized."""


class LocalizationDevelopmentTargetFailed(Exception):
    """Internal signal emitted when the requested node produced a failed result."""

    def __init__(self, step_id: str, step_result: dict[str, Any]) -> None:
        super().__init__(step_id)
        self.step_id = step_id
        self.step_result = step_result


class LocalizationWorkflowExecution:
    """One execution facade shared by formal and development workflows."""

    def __init__(
        self,
        ledger: LocalizationWorkflowLedger,
        *,
        project_id: str,
        operation_id: str,
        development: LocalizationDevelopmentExecutionConfig | None = None,
        behavior_contexts: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.project_id = project_id
        self.operation_id = operation_id
        self.development = development
        self.behavior_contexts = behavior_contexts or {}
        self.graph = WorkflowGraph(LOCALIZATION_WORKFLOW_V3_DEFINITION)
        self.executed_step_ids: list[str] = []
        self.reused_step_ids: list[str] = []
        self.recomputed_reasons: dict[str, str] = {}
        self._lineage_fingerprints: dict[str, str] = {}
        self._contract_versions = {
            task.id: str(task.output_contract_version or "")
            for stage in LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
            for task in stage.atomic_tasks
        }
        if development is None:
            self.plan = None
            self.store = None
            return
        self.plan = self.graph.plan_for(development.target_step_id)
        self.store = LocalizationDevelopmentMaterializationStore(
            development.snapshot_root,
            project_id=project_id,
            development_session_id=development.development_session_id,
        )

    @property
    def is_development(self) -> bool:
        return self.development is not None

    def operation_id_for(self, step_id: str) -> str:
        if self.development is None:
            return self.ledger.operation_id_for(step_id)
        return f"{self.development.development_session_id}:{step_id}"

    def development_artifact_dir_for(self, step_id: str) -> Path | None:
        """Return the external artifact directory for a development node."""

        if self.store is None:
            return None
        return self.store.artifact_dir_for(step_id)

    def may_resume_atomic_checkpoint(
        self,
        step_id: str,
        *,
        allow_completed_target: bool = False,
    ) -> bool:
        """Allow a node to inspect its own typed internal checkpoints.

        Completed materializations normally suppress checkpoint replay. A
        development target may opt in when its checkpoint validator
        independently proves input and behavior compatibility. This lets a
        parser or projection fix replay paid raw responses even when an older
        completed node materialization also exists.
        """

        if self.development is None or self.store is None:
            return False
        if (
            allow_completed_target
            and self.development.target_step_id == step_id
        ):
            return True
        return not self.store.path_for(step_id).is_file()

    def run(
        self,
        step_id: str,
        summary: str,
        action: Callable[[], Any],
        projector: Callable[..., dict],
        **projector_kwargs: Any,
    ) -> Any:
        if self.development is None:
            return self.ledger.run(
                step_id,
                summary,
                action,
                projector,
                **projector_kwargs,
            )
        result = self._run_development_node(
            step_id,
            summary,
            action,
            projector,
            projector_kwargs,
        )
        self._raise_for_target_outcome(step_id)
        return result

    def run_side_effect(
        self,
        step_id: str,
        summary: str,
        action: Callable[[], Any],
        projector: Callable[..., dict],
        **projector_kwargs: Any,
    ) -> Any:
        """Execute a real side effect in both modes without snapshot reuse."""

        if self.development is None:
            return self.ledger.run(
                step_id,
                summary,
                action,
                projector,
                **projector_kwargs,
            )
        assert self.plan is not None
        if step_id not in self.plan.required_step_ids:
            raise ValueError(
                f"开发执行计划不需要节点 {step_id}，不能隐式执行。"
            )
        missing_dependencies = [
            dependency
            for dependency in self.graph.dependencies(step_id)
            if dependency not in self._lineage_fingerprints
        ]
        if missing_dependencies:
            raise ValueError(
                f"节点 {step_id} 缺少已验证的上游结果："
                + "、".join(missing_dependencies)
            )
        result = self.ledger.run(
            step_id,
            summary,
            action,
            projector,
            **projector_kwargs,
        )
        self.executed_step_ids.append(step_id)
        self.recomputed_reasons[step_id] = (
            "副作用节点始终执行，不复用开发快照。"
        )
        self._raise_for_target_outcome(step_id)
        return result

    def run_parallel(
        self,
        jobs: list[
            tuple[
                str,
                str,
                Callable[[], Any],
                Callable[..., dict],
                dict[str, Any],
            ]
        ],
        *,
        thread_name_prefix: str,
    ) -> list[Any]:
        if self.development is None:
            return self.ledger.run_parallel(
                jobs,
                thread_name_prefix=thread_name_prefix,
            )
        assert self.plan is not None
        assert self.store is not None
        results: list[Any] = [None] * len(jobs)
        pending: list[
            tuple[
                int,
                str,
                Callable[[], Any],
                Callable[..., dict],
                dict[str, Any],
                str,
                dict[str, str],
                str,
            ]
        ] = []
        target_in_group = False
        for index, (
            step_id,
            summary,
            action,
            projector,
            projector_kwargs,
        ) in enumerate(jobs):
            if step_id not in self.plan.required_step_ids:
                continue
            (
                behavior_fingerprint,
                dependency_fingerprints,
                lookup,
                reason,
            ) = self._prepare_development_node(step_id)
            if not (
                step_id == self.development.target_step_id
                and self.development.force_target
            ) and lookup.status == "valid":
                assert lookup.result is not None
                assert lookup.envelope is not None
                self._lineage_fingerprints[step_id] = (
                    lookup.envelope.materialization_fingerprint
                )
                self.reused_step_ids.append(step_id)
                results[index] = self.ledger.reuse(
                    step_id,
                    lookup.result,
                    projector,
                    **projector_kwargs,
                )
            else:
                self.ledger.begin(step_id, summary)
                pending.append(
                    (
                        index,
                        step_id,
                        action,
                        projector,
                        projector_kwargs,
                        behavior_fingerprint,
                        dependency_fingerprints,
                        reason,
                    )
                )
            target_in_group = (
                target_in_group
                or step_id == self.development.target_step_id
            )

        def timed(action: Callable[[], Any]) -> tuple[Any, int]:
            started_at = time.perf_counter()
            result = action()
            return (
                result,
                max(
                    0,
                    round((time.perf_counter() - started_at) * 1000),
                ),
            )

        if pending:
            with ThreadPoolExecutor(
                max_workers=len(pending),
                thread_name_prefix=thread_name_prefix,
            ) as executor:
                futures = {
                    executor.submit(timed, item[2]): item
                    for item in pending
                }
                for future in as_completed(futures):
                    (
                        index,
                        step_id,
                        _action,
                        projector,
                        projector_kwargs,
                        behavior_fingerprint,
                        dependency_fingerprints,
                        reason,
                    ) = futures[future]
                    result, duration_ms = future.result()
                    results[index] = self.ledger.finish_duration(
                        step_id,
                        result,
                        duration_ms,
                        projector,
                        **projector_kwargs,
                    )
                    envelope = self.store.save(
                        step_id,
                        result,
                        produced_by_operation_id=self.operation_id,
                        behavior_fingerprint=behavior_fingerprint,
                        dependency_fingerprints=dependency_fingerprints,
                    )
                    self._lineage_fingerprints[step_id] = (
                        envelope.materialization_fingerprint
                    )
                    self.executed_step_ids.append(step_id)
                    self.recomputed_reasons[step_id] = reason
        if target_in_group:
            self._raise_for_target_outcome(
                self.development.target_step_id
            )
        return results

    def development_summary(self) -> dict[str, Any]:
        if self.development is None:
            return {}
        return {
            "stage": "开发节点已完成",
            "stage_id": self.development.target_step_id,
            **self.ledger.summary_fields(),
            "execution_mode": "development_target",
            "development_session_id": (
                self.development.development_session_id
            ),
            "development_target_step_id": (
                self.development.target_step_id
            ),
            "executed_step_ids": list(self.executed_step_ids),
            "reused_step_ids": list(self.reused_step_ids),
            "recomputed_reasons": dict(self.recomputed_reasons),
            "formal_project_data_changed": any(
                LOCALIZATION_WORKFLOW_NODE_SPECS[step_id]
                .has_formal_side_effect
                for step_id in self.executed_step_ids
            ),
        }

    def _run_development_node(
        self,
        step_id: str,
        summary: str,
        action: Callable[[], Any],
        projector: Callable[..., dict],
        projector_kwargs: dict[str, Any],
    ) -> Any:
        assert self.development is not None
        assert self.plan is not None
        assert self.store is not None
        if step_id not in self.plan.required_step_ids:
            raise ValueError(
                f"开发执行计划不需要节点 {step_id}，不能隐式执行。"
            )
        (
            behavior_fingerprint,
            dependency_fingerprints,
            lookup,
            lookup_reason,
        ) = self._prepare_development_node(step_id)
        force = (
            step_id == self.development.target_step_id
            and self.development.force_target
        )
        if not force and lookup.status == "valid":
            assert lookup.result is not None
            assert lookup.envelope is not None
            self._lineage_fingerprints[step_id] = (
                lookup.envelope.materialization_fingerprint
            )
            self.reused_step_ids.append(step_id)
            return self.ledger.reuse(
                step_id,
                lookup.result,
                projector,
                **projector_kwargs,
            )
        reason = (
            "当前目标节点按开发要求强制重跑。"
            if force
            else lookup_reason
        )
        result = self.ledger.run(
            step_id,
            summary,
            action,
            projector,
            **projector_kwargs,
        )
        envelope = self.store.save(
            step_id,
            result,
            produced_by_operation_id=self.operation_id,
            behavior_fingerprint=behavior_fingerprint,
            dependency_fingerprints=dependency_fingerprints,
        )
        self._lineage_fingerprints[step_id] = (
            envelope.materialization_fingerprint
        )
        self.executed_step_ids.append(step_id)
        self.recomputed_reasons[step_id] = reason
        return result

    def _prepare_development_node(
        self,
        step_id: str,
    ) -> tuple[str, dict[str, str], Any, str]:
        assert self.development is not None
        assert self.plan is not None
        assert self.store is not None
        dependencies = self.graph.dependencies(step_id)
        missing_dependencies = [
            dependency
            for dependency in dependencies
            if dependency not in self._lineage_fingerprints
        ]
        if missing_dependencies:
            raise ValueError(
                f"节点 {step_id} 缺少已验证的上游结果："
                + "、".join(missing_dependencies)
            )
        dependency_fingerprints = {
            dependency: self._lineage_fingerprints[dependency]
            for dependency in dependencies
        }
        behavior_fingerprint = node_behavior_fingerprint(
            step_id,
            self.behavior_contexts.get(step_id),
        )
        lookup = self.store.load(
            step_id,
            result_model=(
                LOCALIZATION_WORKFLOW_NODE_SPECS[step_id].result_model
            ),
            expected_contract_version=self._contract_versions[step_id],
            expected_behavior_fingerprint=behavior_fingerprint,
            expected_dependency_fingerprints=dependency_fingerprints,
        )
        reason = (
            "当前目标节点按开发要求强制重跑。"
            if (
                step_id == self.development.target_step_id
                and self.development.force_target
            )
            else lookup.reason
        )
        return (
            behavior_fingerprint,
            dependency_fingerprints,
            lookup,
            reason,
        )

    def _raise_for_target_outcome(self, step_id: str) -> None:
        assert self.development is not None
        if step_id != self.development.target_step_id:
            return
        step_result = self.ledger.step_results.get(step_id, {})
        if step_result.get("status") == "failed":
            raise LocalizationDevelopmentTargetFailed(
                step_id,
                dict(step_result),
            )
        raise LocalizationDevelopmentTargetReached


__all__ = [
    "LocalizationDevelopmentExecutionConfig",
    "LocalizationDevelopmentTargetFailed",
    "LocalizationDevelopmentTargetReached",
    "LocalizationWorkflowExecution",
]
