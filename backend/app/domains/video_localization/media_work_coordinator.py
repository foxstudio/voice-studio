from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from enum import IntEnum
from typing import Iterator


class MediaWorkPriority(IntEnum):
    """Priority for bounded media work competing for the shared encoder."""

    INTERACTIVE = 0
    PREVIEW = 1
    BACKGROUND = 2


class MediaWorkCoordinator:
    """Serialize expensive media work while letting user-visible work go first.

    Running FFmpeg processes are never interrupted. Priority is applied between
    bounded units (one playback segment or one thumbnail chunk), which keeps the
    coordinator predictable and avoids two independent semaphores saturating the
    same machine.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = False
        self._sequence = 0
        self._waiters: dict[str, tuple[int, int]] = {}

    @contextmanager
    def slot(
        self,
        priority: MediaWorkPriority,
        cancel_event: threading.Event,
        *,
        poll_seconds: float = 0.05,
    ) -> Iterator[bool]:
        token = uuid.uuid4().hex
        acquired = False
        with self._condition:
            sequence = self._sequence
            self._sequence += 1
            self._waiters[token] = (int(priority), sequence)
            try:
                while True:
                    if cancel_event.is_set():
                        break
                    next_token = min(
                        self._waiters,
                        key=lambda item: self._waiters[item],
                    )
                    if not self._active and next_token == token:
                        self._active = True
                        acquired = True
                        break
                    self._condition.wait(timeout=poll_seconds)
            finally:
                self._waiters.pop(token, None)
                self._condition.notify_all()
        try:
            yield acquired
        finally:
            if acquired:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()


MEDIA_WORK_COORDINATOR = MediaWorkCoordinator()
