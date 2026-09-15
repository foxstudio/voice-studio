"""Deterministic elapsed-time calculation for operation projections."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone


def duration_ms(
    started_at: str | None,
    completed_at: str | None,
) -> int | None:
    """Return elapsed milliseconds across legacy-naive and current UTC values."""

    if not started_at or not completed_at:
        return None
    try:
        started = _as_utc(datetime.fromisoformat(started_at))
        completed = _as_utc(datetime.fromisoformat(completed_at))
    except ValueError:
        return None
    return max(
        0,
        int((completed - started).total_seconds() * 1_000),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def span_duration_ms(
    started_values: Iterable[str | None],
    completed_values: Iterable[str | None],
) -> int | None:
    """Return the wall-clock span of timestamps from one clock source."""

    starts = [_parse(value) for value in started_values if value]
    completions = [_parse(value) for value in completed_values if value]
    valid_starts = [value for value in starts if value is not None]
    valid_completions = [value for value in completions if value is not None]
    if not valid_starts or not valid_completions:
        return None
    return max(
        0,
        int(
            (max(valid_completions) - min(valid_starts)).total_seconds()
            * 1_000
        ),
    )


def _parse(value: str) -> datetime | None:
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


__all__ = ["duration_ms", "span_duration_ms"]
