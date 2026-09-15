from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from app.domains.video_localization import workflow_contracts
from app.domains.video_localization.llm_observability import (
    enrich_task_step_with_llm_calls,
)
from app.errors import AppException


class LocalizationWorkflowLedger:
    """Progress, timing, and result ledger for one localization workflow."""

    def __init__(
        self,
        operation_id: str,
        *,
        definition: workflow_contracts.WorkflowDefinition,
        is_cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_report: Callable[[str, dict], None] | None = None,
        on_atomic_result: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.operation_id = operation_id
        self.is_cancelled = is_cancelled
        self.on_progress = on_progress
        self.on_report = on_report
        self.on_atomic_result = on_atomic_result
        self.started_at = time.perf_counter()
        self._progress_lock = threading.Lock()
        self._last_progress = 0.0
        self.step_results: dict[str, dict] = {}
        self.stage_timings: dict[str, dict[str, int | bool]] = {}
        self.definition = definition
        self.tasks = {
            task.id: task
            for stage in self.definition.stages
            for task in stage.atomic_tasks
        }

    def operation_id_for(self, step_id: str) -> str:
        return f"{self.operation_id}:{step_id}"

    def ensure_active(self) -> None:
        if self.is_cancelled is not None and self.is_cancelled():
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_OPERATION_CANCELLED",
                "本土化字幕任务已取消。",
            )

    def begin(self, step_id: str, summary: str) -> float:
        self.ensure_active()
        started_at = time.perf_counter()
        started_elapsed_ms = max(
            0,
            round((started_at - self.started_at) * 1000),
        )
        task = self.tasks[step_id]
        self.stage_timings[step_id] = {
            "duration_ms": 0,
            "started_elapsed_ms": started_elapsed_ms,
            "running": True,
            "atomic": True,
        }
        self._report_progress(
            self._progress(step_id, 0.0),
            f"flow:{step_id}|{summary}",
        )
        if self.on_report is not None:
            self.on_report(
                step_id,
                {
                    "label": task.label,
                    "order": task.order,
                    "status": "running",
                    "purpose": task.description,
                    "summary": summary,
                    "metrics": [],
                    "sections": [],
                    "notes": [],
                    "_task_timing": dict(
                        self.stage_timings[step_id]
                    ),
                },
            )
        return started_at

    def finish(
        self,
        step_id: str,
        started_at: float,
        result: Any,
        projector: Callable[..., dict],
        **projector_kwargs: Any,
    ) -> Any:
        return self.finish_duration(
            step_id,
            result,
            max(0, round((time.perf_counter() - started_at) * 1000)),
            projector,
            **projector_kwargs,
        )

    def finish_duration(
        self,
        step_id: str,
        result: Any,
        duration_ms: int,
        projector: Callable[..., dict],
        **projector_kwargs: Any,
    ) -> Any:
        if self.on_atomic_result is not None:
            self.on_atomic_result(step_id, result)
        projected = enrich_task_step_with_llm_calls(
            projector(result, **projector_kwargs),
            result,
        )
        self.step_results[step_id] = projected
        started_elapsed_ms = int(
            self.stage_timings.get(step_id, {}).get(
                "started_elapsed_ms",
                max(
                    0,
                    round(
                        (time.perf_counter() - self.started_at) * 1000
                    )
                    - max(0, int(duration_ms)),
                ),
            )
        )
        self.stage_timings[step_id] = {
            "duration_ms": max(0, int(duration_ms)),
            "started_elapsed_ms": started_elapsed_ms,
            "atomic": True,
        }
        self._report_progress(
            self._progress(step_id, 1.0),
            (
                f"flow:{step_id}|"
                f"{projected.get('summary') or self.tasks[step_id].label + '已完成。'}"
            ),
        )
        if self.on_report is not None:
            self.on_report(
                step_id,
                {
                    **projected,
                    "_task_timing": dict(
                        self.stage_timings[step_id]
                    ),
                },
            )
        return result

    def run(
        self,
        step_id: str,
        summary: str,
        action: Callable[[], Any],
        projector: Callable[..., dict],
        **projector_kwargs: Any,
    ) -> Any:
        started_at = self.begin(step_id, summary)
        result = action()
        return self.finish(
            step_id,
            started_at,
            result,
            projector,
            **projector_kwargs,
        )

    def reuse(
        self,
        step_id: str,
        result: Any,
        projector: Callable[..., dict],
        **projector_kwargs: Any,
    ) -> Any:
        """Project one validated development result without re-executing it."""

        self.ensure_active()
        projected = enrich_task_step_with_llm_calls(
            projector(result, **projector_kwargs),
            result,
        )
        projected = {
            **projected,
            "summary": (
                f"已复用开发快照。"
                f"{projected.get('summary') or ''}"
            ),
            "notes": [
                *(projected.get("notes") or []),
                "本节点未重新调用模型或媒体处理能力。",
            ],
        }
        self.step_results[step_id] = projected
        self.stage_timings[step_id] = {
            "duration_ms": 0,
            "started_elapsed_ms": max(
                0,
                round(
                    (time.perf_counter() - self.started_at) * 1000
                ),
            ),
            "atomic": True,
            "reused": True,
        }
        self._report_progress(
            self._progress(step_id, 1.0),
            f"flow:{step_id}|{self.tasks[step_id].label}：已复用开发快照",
        )
        if self.on_report is not None:
            self.on_report(
                step_id,
                {
                    **projected,
                    "_task_timing": dict(
                        self.stage_timings[step_id]
                    ),
                },
            )
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
        for step_id, summary, _action, _projector, _kwargs in jobs:
            self.begin(step_id, summary)

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

        with ThreadPoolExecutor(
            max_workers=len(jobs),
            thread_name_prefix=thread_name_prefix,
        ) as executor:
            future_jobs = {
                executor.submit(timed, action): (
                    index,
                    step_id,
                    projector,
                    kwargs,
                )
                for index, (
                    step_id,
                    _summary,
                    action,
                    projector,
                    kwargs,
                ) in enumerate(jobs)
            }
            completed_results: dict[int, Any] = {}
            for future in as_completed(future_jobs):
                index, step_id, projector, kwargs = future_jobs[future]
                result, atomic_duration_ms = future.result()
                completed_results[index] = self.finish_duration(
                    step_id,
                    result,
                    atomic_duration_ms,
                    projector,
                    **kwargs,
                )
        return [
            completed_results[index]
            for index in range(len(jobs))
        ]

    def batch_callback(
        self,
        step_id: str,
    ) -> Callable[[int, int, str], None]:
        def report(completed: int, total: int, batch_id: str) -> None:
            total_count = max(1, int(total))
            completed_count = max(
                0,
                min(int(completed), total_count),
            )
            task = self.tasks[step_id]
            summary = (
                f"已完成 {completed_count}/{total_count} 批，"
                f"最近完成 {batch_id}。"
            )
            self._report_progress(
                self._progress(
                    step_id,
                    completed_count / total_count,
                ),
                (
                    f"flow:{step_id}|{task.label}："
                    f"{completed_count}/{total_count} 批"
                ),
            )
            if self.on_report is not None:
                self.on_report(
                    step_id,
                    {
                        "label": task.label,
                        "order": task.order,
                        "status": "running",
                        "purpose": task.description,
                        "summary": summary,
                        "metrics": [
                            {
                                "label": "批次进度",
                                "value": (
                                    f"{completed_count}/{total_count}"
                                ),
                            }
                        ],
                        "sections": [],
                        "notes": [],
                    },
                )

        return report

    def summary_fields(self) -> dict:
        definition = self.definition.model_dump(mode="json")
        return {
            "workflow_schema_version": definition["schema_version"],
            "workflow_id": definition["workflow_id"],
            "task_stage_groups": definition["stages"],
            "task_step_results": self.step_results,
            "stage_timings": self.stage_timings,
            "duration_ms": max(
                0,
                round((time.perf_counter() - self.started_at) * 1000),
            ),
        }

    def _progress(self, step_id: str, fraction: float) -> float:
        task = self.tasks[step_id]
        previous_order = max(
            [
                item.order
                for item in self.tasks.values()
                if item.order < task.order
            ]
            or [0]
        )
        interpolated = previous_order + (
            task.order - previous_order
        ) * max(0.0, min(1.0, fraction))
        maximum_order = max(
            (item.order for item in self.tasks.values()),
            default=100,
        )
        return min(
            0.99,
            max(0.01, interpolated / max(float(maximum_order), 1.0)),
        )

    def _report_progress(self, progress: float, stage: str) -> None:
        if self.on_progress is None:
            return
        with self._progress_lock:
            monotonic_progress = max(self._last_progress, progress)
            self._last_progress = monotonic_progress
        self.on_progress(monotonic_progress, stage)


__all__ = ["LocalizationWorkflowLedger"]
