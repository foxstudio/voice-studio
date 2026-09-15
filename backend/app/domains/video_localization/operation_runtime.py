from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


OperationRuntimeKey = tuple[str, str]


class OperationCommitGate:
    """Linearizes cancellation against one operation's final content commit."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._cancel_requested = False
        self._cancel_commit_in_flight = False
        self._committed = False

    def request_cancel(self) -> bool:
        with self._lock:
            if self._committed:
                return False
            self._cancel_requested = True
            return True

    def commit_cancel(
        self,
        action: Callable[[], Any],
    ) -> tuple[bool, Any | None]:
        """Persist cancellation before exposing it to local workers."""
        with self._condition:
            while self._cancel_commit_in_flight:
                self._condition.wait()
            if self._committed:
                return False, None
            if self._cancel_requested:
                return False, None
            self._cancel_commit_in_flight = True
        try:
            result = action()
        except BaseException:
            with self._condition:
                self._cancel_commit_in_flight = False
                self._condition.notify_all()
            raise
        with self._condition:
            self._cancel_requested = True
            self._cancel_commit_in_flight = False
            self._condition.notify_all()
            return True, result

    def is_cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def commit(
        self,
        action: Callable[[], Any],
    ) -> tuple[bool, Any | None]:
        with self._condition:
            while self._cancel_commit_in_flight:
                self._condition.wait()
            if self._cancel_requested:
                return False, None
            result = action()
            self._committed = True
            return True, result


class OperationRuntime:
    """Process-local queue deduplication and cancellation synchronization.

    Durable command metadata is owned by the operation ledger while complete
    user-visible records remain in the Project compatibility mirror during
    reader migration. This object owns only resources that cannot survive a
    process restart: queue membership and the lock that orders cancellation
    against a final commit.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scheduled_operations: set[OperationRuntimeKey] = set()
        self._cancelled_operations: set[OperationRuntimeKey] = set()
        self._commit_gates: dict[
            OperationRuntimeKey,
            OperationCommitGate,
        ] = {}
        self._recovery_timers: dict[
            OperationRuntimeKey,
            tuple[int, threading.Timer],
        ] = {}

    def enqueue_once(self, operation: OperationRuntimeKey) -> bool:
        with self._lock:
            if operation in self._scheduled_operations:
                return False
            self._scheduled_operations.add(operation)
            return True

    def mark_dequeued(self, operation: OperationRuntimeKey) -> None:
        with self._lock:
            self._scheduled_operations.add(operation)

    def commit_gate(
        self,
        operation: OperationRuntimeKey,
    ) -> OperationCommitGate:
        with self._lock:
            gate = self._commit_gates.get(operation)
            if gate is None:
                gate = OperationCommitGate()
                self._commit_gates[operation] = gate
            return gate

    def mark_cancelled(self, operation: OperationRuntimeKey) -> None:
        with self._lock:
            self._cancelled_operations.add(operation)

    def cancellation_requested(
        self,
        operation: OperationRuntimeKey,
    ) -> bool:
        with self._lock:
            gate = self._commit_gates.get(operation)
            return (
                operation in self._cancelled_operations
                or bool(gate and gate.is_cancel_requested())
            )

    def complete(self, operation: OperationRuntimeKey) -> None:
        with self._lock:
            self._scheduled_operations.discard(operation)
            self._cancelled_operations.discard(operation)
            self._commit_gates.pop(operation, None)

    def schedule_recovery(
        self,
        operation: OperationRuntimeKey,
        *,
        deadline_ms: int,
        callback: Callable[[OperationRuntimeKey], None],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        """Wake once a foreign lease may have expired.

        An earlier pending wake is retained because it will re-read the
        durable lease and schedule the next deadline when heartbeat extended
        it. The callback always runs outside the runtime lock.
        """
        actual_clock = clock_ms or (
            lambda: time.time_ns() // 1_000_000
        )
        with self._lock:
            current = self._recovery_timers.get(operation)
            if (
                current is not None
                and current[1].is_alive()
                and current[0] <= deadline_ms
            ):
                return
            if current is not None:
                current[1].cancel()

            timer: threading.Timer

            def fire() -> None:
                with self._lock:
                    active = self._recovery_timers.get(operation)
                    if active is None or active[1] is not timer:
                        return
                    self._recovery_timers.pop(operation, None)
                callback(operation)

            delay_seconds = max(
                0.05,
                (deadline_ms - actual_clock()) / 1_000 + 0.05,
            )
            timer = threading.Timer(delay_seconds, fire)
            timer.daemon = True
            self._recovery_timers[operation] = (
                deadline_ms,
                timer,
            )
            timer.start()

    def cancel_recovery(self, operation: OperationRuntimeKey) -> None:
        with self._lock:
            scheduled = self._recovery_timers.pop(
                operation,
                None,
            )
        if scheduled is not None:
            scheduled[1].cancel()

    def reset(self) -> None:
        with self._lock:
            timers = [
                timer
                for _deadline, timer in self._recovery_timers.values()
            ]
            self._scheduled_operations.clear()
            self._cancelled_operations.clear()
            self._commit_gates.clear()
            self._recovery_timers.clear()
        for timer in timers:
            timer.cancel()


__all__ = [
    "OperationCommitGate",
    "OperationRuntime",
    "OperationRuntimeKey",
]
