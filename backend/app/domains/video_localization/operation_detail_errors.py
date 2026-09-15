"""Shared fail-closed errors for managed operation detail readers."""

from __future__ import annotations


_REPAIR_MESSAGE = (
    "任务详情的新权威数据缺失或损坏，请先运行本土化任务详情审计和显式迁移。"
)


class OperationDetailRepairRequired(RuntimeError):
    """A managed detail cannot be reconstructed from typed authorities."""

    def __init__(self, *issue_codes: str):
        normalized = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in issue_codes
                if str(value or "").strip()
            )
        )
        super().__init__(_REPAIR_MESSAGE)
        self.issue_codes = normalized or ("detail_invalid",)


__all__ = ["OperationDetailRepairRequired"]
