"""Reader-facing policy for transcript-review warnings."""

from __future__ import annotations


def warning_needs_reader_review(
    *,
    code: str = "",
    message: str = "",
) -> bool:
    """Keep safely rejected model suggestions in debug data, not callouts."""

    if str(code or "").strip() == "invalid_issue":
        return False
    return "返回了一条无法定位到原文的建议，已忽略。" not in str(
        message or ""
    )
