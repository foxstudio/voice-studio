from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.services import database
from app.services import video_localization_operation_attempt_store
from app.services.video_localization_execution_fence import ExecutionFence


DEFAULT_LEASE_DURATION = timedelta(seconds=60)
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 10.0
AcquireOutcome = Literal["acquired", "active_lease"]
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionClaimDecision:
    outcome: AcquireOutcome
    claim: OperationExecutionClaim | None
    retry_at_ms: int | None

    @property
    def acquired(self) -> bool:
        return self.claim is not None


class OperationExecutionClaim:
    """Process-local controller for one durable operation attempt."""

    def __init__(
        self,
        attempt: (
            video_localization_operation_attempt_store.OperationAttempt
        ),
        *,
        lease_duration: timedelta,
        heartbeat_interval_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        execution_fence = attempt.execution_fence
        if execution_fence is None:
            raise ValueError("execution claim requires a durable attempt")
        self.attempt = attempt
        self.execution_fence: ExecutionFence = execution_fence
        self._lease_duration = lease_duration
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._clock = clock
        self._stop_event = threading.Event()
        self._lost_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

    @property
    def lost(self) -> bool:
        return self._lost_event.is_set()

    def mark_lost(self) -> None:
        self._lost_event.set()
        self._stop_event.set()

    def start_heartbeat(self) -> None:
        with self._thread_lock:
            if self._heartbeat_thread is not None:
                return
            thread = threading.Thread(
                target=self._heartbeat_loop,
                daemon=True,
                name=(
                    "video-localization-lease-"
                    f"{self.attempt.operation_id[:8]}"
                ),
            )
            self._heartbeat_thread = thread
            thread.start()

    def stop_heartbeat(self) -> None:
        self._stop_event.set()
        with self._thread_lock:
            thread = self._heartbeat_thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(
                timeout=max(1.0, self._heartbeat_interval_seconds * 2)
            )

    def finish(
        self,
        *,
        status: (
            video_localization_operation_attempt_store
            .TerminalAttemptStatus
        ),
        error_code: str | None = None,
    ) -> bool:
        self.stop_heartbeat()
        if self.lost:
            return False
        finished = (
            video_localization_operation_attempt_store.finish_claim(
                self.attempt.attempt_id,
                self.attempt.project_id,
                self.attempt.operation_id,
                runner_id=self.attempt.runner_id,
                fencing_token=self.attempt.fencing_token,
                status=status,
                observed_at=self._clock(),
                error_code=error_code,
            )
        )
        if not finished:
            self.mark_lost()
        return finished

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(
            self._heartbeat_interval_seconds
        ):
            try:
                renewed = (
                    video_localization_operation_attempt_store
                    .heartbeat_claim(
                        self.attempt.attempt_id,
                        self.attempt.project_id,
                        self.attempt.operation_id,
                        runner_id=self.attempt.runner_id,
                        fencing_token=self.attempt.fencing_token,
                        observed_at=self._clock(),
                        lease_duration=self._lease_duration,
                    )
                )
            except Exception as exc:
                _logger.warning(
                    "operation lease heartbeat failed (%s)",
                    type(exc).__name__,
                )
                self.mark_lost()
                return
            if not renewed:
                self.mark_lost()
                return


def acquire_execution_claim(
    project_id: str,
    operation_id: str,
    *,
    runner_id: str,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    heartbeat_interval_seconds: float = (
        DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    ),
    clock: Callable[[], datetime] | None = None,
    busy_timeout_ms: int = database.SQLITE_BUSY_TIMEOUT_MS,
) -> ExecutionClaimDecision:
    _validate_timing(
        lease_duration=lease_duration,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        busy_timeout_ms=busy_timeout_ms,
    )
    actual_clock = clock or _utc_now
    decision = (
        video_localization_operation_attempt_store.claim_attempt(
            project_id,
            operation_id,
            runner_id=runner_id,
            observed_at=actual_clock(),
            lease_duration=lease_duration,
        )
    )
    if not decision.acquired:
        return ExecutionClaimDecision(
            outcome="active_lease",
            claim=None,
            retry_at_ms=decision.attempt.lease_expires_at_ms,
        )
    return ExecutionClaimDecision(
        outcome="acquired",
        claim=OperationExecutionClaim(
            decision.attempt,
            lease_duration=lease_duration,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            clock=actual_clock,
        ),
        retry_at_ms=None,
    )


def _validate_timing(
    *,
    lease_duration: timedelta,
    heartbeat_interval_seconds: float,
    busy_timeout_ms: int,
) -> None:
    heartbeat_interval_ms = int(
        heartbeat_interval_seconds * 1_000
    )
    lease_duration_ms = int(lease_duration.total_seconds() * 1_000)
    if heartbeat_interval_ms < 1:
        raise ValueError(
            "heartbeat interval must be at least 1 millisecond"
        )
    if busy_timeout_ms < 0:
        raise ValueError("busy timeout must not be negative")
    minimum_lease_ms = 3 * heartbeat_interval_ms + busy_timeout_ms
    if lease_duration_ms <= minimum_lease_ms:
        raise ValueError(
            "lease duration must exceed three heartbeat intervals "
            "plus the SQLite busy timeout"
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "AcquireOutcome",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_LEASE_DURATION",
    "ExecutionClaimDecision",
    "OperationExecutionClaim",
    "acquire_execution_claim",
]
