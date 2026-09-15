from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable


class GenerationPriority(IntEnum):
    FOREGROUND_RESUME = 0
    NORMAL = 1
    BACKGROUND = 2


def parse_priority(value: object) -> GenerationPriority:
    if isinstance(value, GenerationPriority):
        return value
    try:
        return GenerationPriority[str(value or "normal").strip().upper()]
    except KeyError as exc:
        raise ValueError("resource priority must be foreground_resume, normal, or background") from exc


@dataclass(frozen=True)
class GenerationTaskQueueDescriptor:
    task_id: str
    project_id: str
    priority: GenerationPriority


@dataclass(frozen=True)
class _PendingTask:
    task_id: str
    sequence: int
    queued_at: float


class ProjectFairGenerationQueue:
    """Priority queue for the one canonical TTS worker.

    A running generation is never interrupted. At the next task boundary,
    foreground resumes outrank normal work; equal priorities rotate projects.
    Aging eventually promotes work that has waited for a long time.
    """

    AGING_SECONDS = 120.0

    def __init__(
        self,
        describe_task: Callable[
            [str], GenerationTaskQueueDescriptor | None
        ],
        *,
        clock: Callable[[], float] = time.monotonic,
        on_drop: Callable[[str], None] | None = None,
    ) -> None:
        self._describe_task = describe_task
        self._clock = clock
        self._on_drop = on_drop
        self._lock = threading.Lock()
        self._wake = asyncio.Event()
        self._pending: dict[str, _PendingTask] = {}
        self._sequence = 0
        self._last_served: dict[str, int] = {}
        self._dispatch_sequence = 0

    def put_nowait(self, task_id: str) -> bool:
        # Admission stores identity only. Project/storage reads belong to the
        # off-loop dispatch phase, not the caller's event loop.
        with self._lock:
            if task_id in self._pending:
                return False
            self._pending[task_id] = _PendingTask(
                task_id=task_id,
                sequence=self._sequence,
                queued_at=self._clock(),
            )
            self._sequence += 1
        self._wake.set()
        return True

    def _effective_priority(
        self, pending: _PendingTask, priority: GenerationPriority, now: float
    ) -> int:
        aged_levels = int(
            max(0.0, now - pending.queued_at) / self.AGING_SECONDS
        )
        return max(0, int(priority) - aged_levels)

    def _pop_next(self) -> str | None:
        with self._lock:
            if not self._pending:
                self._last_served.clear()
                return None
            pending_snapshot = list(self._pending.values())
        # Never hold the admission lock during potentially slow storage reads.
        descriptions = [(pending, self._describe_task(pending.task_id))
                        for pending in pending_snapshot]
        with self._lock:
            now = self._clock()
            # Re-read persisted scheduling metadata at dispatch. Only the FIFO
            # head of each project competes; priority never reorders that project.
            heads: dict[str, tuple[_PendingTask, GenerationTaskQueueDescriptor]] = {}
            for pending, current in descriptions:
                if self._pending.get(pending.task_id) is not pending:
                    continue
                if current is None:
                    self._pending.pop(pending.task_id, None)
                    if self._on_drop is not None:
                        self._on_drop(pending.task_id)
                    continue
                heads.setdefault(current.project_id, (pending, current))
            if not heads:
                self._last_served.clear()
                return None
            self._last_served = {project: turn for project, turn in self._last_served.items()
                                 if project in heads}
            selected, current = min(
                heads.values(),
                key=lambda item: (
                    self._effective_priority(item[0], item[1].priority, now),
                    self._last_served.get(item[1].project_id, -1),
                    item[0].sequence,
                ),
            )
            task_id = selected.task_id
            self._pending.pop(task_id, None)
            self._last_served[current.project_id] = self._dispatch_sequence
            self._dispatch_sequence += 1
            return task_id

    async def get(self) -> str:
        while True:
            self._wake.clear()
            task_id = await asyncio.to_thread(self._pop_next)
            if task_id is not None:
                return task_id
            await self._wake.wait()


def descriptor(
    task_id: str,
    *,
    project_id: str | None,
    priority: object,
) -> GenerationTaskQueueDescriptor:
    return GenerationTaskQueueDescriptor(
        task_id=task_id,
        project_id=project_id or f"task:{task_id}",
        priority=parse_priority(priority),
    )


__all__ = [
    "GenerationTaskQueueDescriptor",
    "GenerationPriority",
    "ProjectFairGenerationQueue",
    "descriptor",
    "parse_priority",
]
