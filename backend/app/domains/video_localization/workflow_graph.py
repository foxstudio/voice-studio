"""Pure dependency-graph helpers shared by workflow execution strategies."""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.video_localization.workflow_contracts import WorkflowDefinition


@dataclass(frozen=True)
class WorkflowExecutionPlan:
    """The minimal topological slice required to produce one target node."""

    target_step_id: str
    ordered_step_ids: tuple[str, ...]
    required_step_ids: frozenset[str]


class WorkflowGraph:
    """Validated, immutable view of a versioned workflow definition."""

    def __init__(self, definition: WorkflowDefinition) -> None:
        self.definition = definition
        ordered_tasks = sorted(
            (
                task
                for stage in definition.stages
                for task in stage.atomic_tasks
            ),
            key=lambda item: item.order,
        )
        self._task_by_id = {task.id: task for task in ordered_tasks}
        if len(self._task_by_id) != len(ordered_tasks):
            raise ValueError("工作流包含重复的原子任务 ID。")
        for task in ordered_tasks:
            missing = [
                dependency
                for dependency in task.depends_on
                if dependency not in self._task_by_id
            ]
            if missing:
                raise ValueError(
                    f"原子任务 {task.id} 引用了不存在的依赖："
                    + "、".join(missing)
                )
        self._ordered_step_ids = tuple(self._topological_order())

    @property
    def ordered_step_ids(self) -> tuple[str, ...]:
        return self._ordered_step_ids

    def dependencies(self, step_id: str) -> tuple[str, ...]:
        return tuple(self._task(step_id).depends_on)

    def descendants(self, step_id: str) -> frozenset[str]:
        self._task(step_id)
        discovered: set[str] = set()
        pending = [step_id]
        while pending:
            current = pending.pop()
            for candidate in self._task_by_id.values():
                if (
                    current in candidate.depends_on
                    and candidate.id not in discovered
                ):
                    discovered.add(candidate.id)
                    pending.append(candidate.id)
        return frozenset(discovered)

    def plan_for(self, target_step_id: str) -> WorkflowExecutionPlan:
        self._task(target_step_id)
        required: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in required:
                return
            for dependency in self.dependencies(step_id):
                visit(dependency)
            required.add(step_id)

        visit(target_step_id)
        return WorkflowExecutionPlan(
            target_step_id=target_step_id,
            ordered_step_ids=tuple(
                step_id
                for step_id in self._ordered_step_ids
                if step_id in required
            ),
            required_step_ids=frozenset(required),
        )

    def _task(self, step_id: str):
        try:
            return self._task_by_id[step_id]
        except KeyError as exc:
            raise ValueError(f"工作流不存在原子任务：{step_id}") from exc

    def _topological_order(self) -> list[str]:
        remaining = {
            task_id: set(task.depends_on)
            for task_id, task in self._task_by_id.items()
        }
        ordered: list[str] = []
        while remaining:
            ready = sorted(
                (
                    task_id
                    for task_id, dependencies in remaining.items()
                    if not dependencies
                ),
                key=lambda task_id: self._task_by_id[task_id].order,
            )
            if not ready:
                raise ValueError("工作流依赖形成了循环，无法执行。")
            for task_id in ready:
                ordered.append(task_id)
                remaining.pop(task_id)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return ordered


__all__ = [
    "WorkflowExecutionPlan",
    "WorkflowGraph",
]
