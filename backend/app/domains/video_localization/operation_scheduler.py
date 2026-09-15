from __future__ import annotations

import threading
from collections import deque

from app.domains.video_localization.operation_runtime import (
    OperationRuntimeKey,
)


DEFAULT_WORKER_COUNT = 2
MAX_WORKER_COUNT = 4


def parse_worker_count(value: str | None) -> int:
    """Parse the bounded process-local operation worker capacity."""

    if value is None or not value.strip():
        return DEFAULT_WORKER_COUNT
    try:
        count = int(value)
    except ValueError as exc:
        raise ValueError(
            "VOICE_STUDIO_VIDEO_LOCALIZATION_WORKERS must be an integer"
        ) from exc
    if count < 1 or count > MAX_WORKER_COUNT:
        raise ValueError(
            "VOICE_STUDIO_VIDEO_LOCALIZATION_WORKERS must be between "
            f"1 and {MAX_WORKER_COUNT}"
        )
    return count


class ProjectFairOperationScheduler:
    """Bounded-worker scheduler with FIFO ordering and project isolation.

    A project can own at most one worker at a time. Pending projects are kept
    in round-robin order, so a second operation from one project does not
    occupy a worker while waiting for that project's first operation.
    """

    def __init__(self, *, worker_count: int) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        self.worker_count = worker_count
        self._condition = threading.Condition()
        self._pending_by_project: dict[
            str,
            deque[OperationRuntimeKey],
        ] = {}
        self._ready_projects: deque[str] = deque()
        self._ready_project_ids: set[str] = set()
        self._active_by_project: dict[
            str,
            OperationRuntimeKey,
        ] = {}
        self._unfinished_tasks = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def put(self, operation: OperationRuntimeKey) -> bool:
        project_id, _operation_id = operation
        with self._condition:
            if self._closed:
                return False
            pending = self._pending_by_project.setdefault(
                project_id,
                deque(),
            )
            pending.append(operation)
            self._unfinished_tasks += 1
            if (
                project_id not in self._active_by_project
                and project_id not in self._ready_project_ids
            ):
                self._ready_projects.append(project_id)
                self._ready_project_ids.add(project_id)
                self._condition.notify()
            return True

    def get(self) -> OperationRuntimeKey | None:
        with self._condition:
            while True:
                if self._ready_projects:
                    project_id = self._ready_projects.popleft()
                    self._ready_project_ids.remove(project_id)
                    pending = self._pending_by_project[project_id]
                    operation = pending.popleft()
                    self._active_by_project[project_id] = operation
                    return operation
                if (
                    self._closed
                    and self._unfinished_tasks == 0
                ):
                    return None
                self._condition.wait()

    def complete(self, operation: OperationRuntimeKey) -> None:
        project_id, _operation_id = operation
        with self._condition:
            active = self._active_by_project.get(project_id)
            if active != operation:
                raise ValueError(
                    "completed operation does not own the project slot"
                )
            self._active_by_project.pop(project_id)
            self._unfinished_tasks -= 1
            pending = self._pending_by_project.get(project_id)
            if pending:
                self._ready_projects.append(project_id)
                self._ready_project_ids.add(project_id)
                self._condition.notify()
            else:
                self._pending_by_project.pop(project_id, None)
            if self._unfinished_tasks == 0:
                self._condition.notify_all()

    def join(self) -> None:
        with self._condition:
            while self._unfinished_tasks:
                self._condition.wait()

    def close(self) -> None:
        """Stop accepting new work and wake workers after queued work drains."""

        with self._condition:
            self._closed = True
            self._condition.notify_all()


__all__ = [
    "DEFAULT_WORKER_COUNT",
    "MAX_WORKER_COUNT",
    "ProjectFairOperationScheduler",
    "parse_worker_count",
]
