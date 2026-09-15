from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Literal

from app.schemas.video_localization_operation_summary_migration import (
    LegacyOperationSummarySource,
    LegacySummaryDecodeError,
    LegacySummaryDecoder,
    SummaryBackfillCategory,
)
from app.services import database
from app.services import video_localization_operation_ledger_store
from app.services import video_localization_operation_summary_audit
from app.services import video_localization_operation_summary_store


SummaryBackfillStatus = Literal[
    "backfilled",
    "unchanged",
    "skipped",
    "repair_required",
]
SummaryPromotionStatus = Literal[
    "verified",
    "unchanged",
    "skipped",
    "rejected",
]
SummaryAuthorityCloseStatus = Literal[
    "authoritative",
    "unchanged",
    "skipped",
    "rejected",
]


@dataclass(frozen=True)
class OperationSummaryBackfillProject:
    project_id: str
    status: SummaryBackfillStatus
    category: SummaryBackfillCategory
    operation_count: int


@dataclass(frozen=True)
class OperationSummaryBackfillReport:
    after_project_id: str | None
    scanned_project_count: int
    backfilled_project_count: int
    unchanged_project_count: int
    skipped_project_count: int
    repair_required_project_count: int
    truncated: bool
    next_cursor: str | None
    projects: tuple[OperationSummaryBackfillProject, ...]


@dataclass(frozen=True)
class OperationSummaryReconciliationReport:
    total_project_count: int
    checked_project_count: int
    total_ledger_count: int
    total_summary_count: int
    checked_legacy_count: int
    checked_ledger_count: int
    checked_summary_count: int
    matched_operation_count: int
    total_issue_count: int
    truncated: bool
    category_counts: dict[str, int]
    issues: tuple[
        video_localization_operation_summary_audit
        .SummaryReconciliationIssue,
        ...,
    ]

    @property
    def healthy(self) -> bool:
        return not self.truncated and not self.issues


@dataclass(frozen=True)
class OperationSummaryPromotionProject:
    project_id: str
    status: SummaryPromotionStatus
    issue_categories: tuple[str, ...]


@dataclass(frozen=True)
class OperationSummaryPromotionReport:
    after_project_id: str | None
    scanned_project_count: int
    verified_project_count: int
    unchanged_project_count: int
    skipped_project_count: int
    rejected_project_count: int
    truncated: bool
    next_cursor: str | None
    projects: tuple[OperationSummaryPromotionProject, ...]


@dataclass(frozen=True)
class OperationSummaryAuthorityCloseProject:
    project_id: str
    status: SummaryAuthorityCloseStatus
    issue_categories: tuple[str, ...]


@dataclass(frozen=True)
class OperationSummaryAuthorityCloseReport:
    closed: bool
    changed: bool
    closed_at: str | None
    scanned_project_count: int
    authoritative_project_count: int
    unchanged_project_count: int
    skipped_project_count: int
    rejected_project_count: int
    truncated: bool
    projects: tuple[OperationSummaryAuthorityCloseProject, ...]


def backfill_operation_summaries(
    decode_source: LegacySummaryDecoder,
    *,
    after_project_id: str | None = None,
    limit: int = 100,
) -> OperationSummaryBackfillReport:
    """Run one resumable project-keyset batch of the shadow migration."""

    if limit < 1:
        raise ValueError("summary backfill limit must be positive")
    cursor = str(after_project_id or "")
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT project_id
            FROM projects
            WHERE project_id > ?
            ORDER BY project_id
            LIMIT ?
            """,
            (cursor, limit + 1),
        ).fetchall()
    truncated = len(rows) > limit
    project_ids = [
        str(row["project_id"]) for row in rows[:limit]
    ]
    projects = tuple(
        _backfill_project_operation_summaries(
            project_id,
            decode_source,
        )
        for project_id in project_ids
    )
    counts = Counter(project.status for project in projects)
    return OperationSummaryBackfillReport(
        after_project_id=after_project_id,
        scanned_project_count=len(projects),
        backfilled_project_count=counts["backfilled"],
        unchanged_project_count=counts["unchanged"],
        skipped_project_count=counts["skipped"],
        repair_required_project_count=counts["repair_required"],
        truncated=truncated,
        next_cursor=(
            project_ids[-1]
            if truncated and project_ids
            else None
        ),
        projects=projects,
    )


def promote_operation_summaries(
    decode_source: LegacySummaryDecoder,
    *,
    after_project_id: str | None = None,
    limit: int = 100,
) -> OperationSummaryPromotionReport:
    """Promote one bounded batch after transactional reconciliation."""

    if limit < 1:
        raise ValueError("summary promotion limit must be positive")
    cursor = str(after_project_id or "")
    with database.conn() as connection:
        rows = connection.execute(
            """
            SELECT project_id
            FROM projects
            WHERE project_id > ?
            ORDER BY project_id
            LIMIT ?
            """,
            (cursor, limit + 1),
        ).fetchall()
    truncated = len(rows) > limit
    project_ids = [
        str(row["project_id"]) for row in rows[:limit]
    ]
    projects = tuple(
        _promote_project_operation_summaries(
            project_id,
            decode_source,
        )
        for project_id in project_ids
    )
    counts = Counter(project.status for project in projects)
    return OperationSummaryPromotionReport(
        after_project_id=after_project_id,
        scanned_project_count=len(projects),
        verified_project_count=counts["verified"],
        unchanged_project_count=counts["unchanged"],
        skipped_project_count=counts["skipped"],
        rejected_project_count=counts["rejected"],
        truncated=truncated,
        next_cursor=(
            project_ids[-1]
            if truncated and project_ids
            else None
        ),
        projects=projects,
    )


def reconcile_operation_summaries(
    decode_source: LegacySummaryDecoder,
    *,
    limit: int = 1_000,
) -> OperationSummaryReconciliationReport:
    """Audit legacy/new canonical projections in one SQLite snapshot."""

    if limit < 1:
        raise ValueError("summary audit limit must be positive")
    with database.read_conn() as connection:
        total_project_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT project_id FROM projects
                    UNION
                    SELECT project_id
                    FROM video_localization_operations
                    UNION
                    SELECT project_id
                    FROM video_localization_operation_summaries
                    UNION
                    SELECT project_id
                    FROM video_localization_operation_projection_state
                )
                """
            ).fetchone()[0]
        )
        project_rows = connection.execute(
            """
            SELECT project_id
            FROM (
                SELECT project_id FROM projects
                UNION
                SELECT project_id
                FROM video_localization_operations
                UNION
                SELECT project_id
                FROM video_localization_operation_summaries
                UNION
                SELECT project_id
                FROM video_localization_operation_projection_state
            )
            ORDER BY project_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        total_ledger_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM video_localization_operations
                """
            ).fetchone()[0]
        )
        total_summary_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM video_localization_operation_summaries
                """
            ).fetchone()[0]
        )
        reconciliations = [
            _reconcile_project_operation_summaries(
                connection,
                str(row["project_id"]),
                decode_source,
            )
            for row in project_rows
        ]
    category_counts: Counter[str] = Counter()
    reported_issues: list[
        video_localization_operation_summary_audit
        .SummaryReconciliationIssue
    ] = []
    total_issue_count = 0
    for reconciliation in reconciliations:
        for issue in reconciliation.issues:
            total_issue_count += 1
            category_counts[issue.category] += 1
            if len(reported_issues) < limit:
                reported_issues.append(issue)
    project_truncated = len(reconciliations) < total_project_count
    issue_truncated = total_issue_count > len(reported_issues)
    return OperationSummaryReconciliationReport(
        total_project_count=total_project_count,
        checked_project_count=len(reconciliations),
        total_ledger_count=total_ledger_count,
        total_summary_count=total_summary_count,
        checked_legacy_count=sum(
            item.legacy_count for item in reconciliations
        ),
        checked_ledger_count=sum(
            item.ledger_count for item in reconciliations
        ),
        checked_summary_count=sum(
            item.summary_count for item in reconciliations
        ),
        matched_operation_count=sum(
            item.matched_count for item in reconciliations
        ),
        total_issue_count=total_issue_count,
        truncated=project_truncated or issue_truncated,
        category_counts=dict(sorted(category_counts.items())),
        issues=tuple(reported_issues),
    )


def close_operation_summary_authority(
    decode_source: LegacySummaryDecoder,
    *,
    limit: int = 1_000,
) -> OperationSummaryAuthorityCloseReport:
    """Atomically close the runtime reader after a full-store recheck."""

    if limit < 1:
        raise ValueError("summary authority close limit must be positive")
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_authority = (
            video_localization_operation_summary_store
            .read_summary_authority(connection)
        )
        rows = connection.execute(
            """
            SELECT project_id
            FROM (
                SELECT project_id FROM projects
                UNION
                SELECT project_id
                FROM video_localization_operations
                UNION
                SELECT project_id
                FROM video_localization_operation_summaries
                UNION
                SELECT project_id
                FROM video_localization_operation_projection_state
            )
            ORDER BY project_id
            LIMIT ?
            """,
            (limit + 1,),
        ).fetchall()
        truncated = len(rows) > limit
        project_ids = [
            str(row["project_id"]) for row in rows[:limit]
        ]
        if truncated:
            return _authority_close_report(
                closed=current_authority is not None,
                changed=False,
                closed_at=(
                    current_authority.closed_at
                    if current_authority is not None
                    else None
                ),
                scanned_project_count=len(project_ids),
                truncated=True,
                projects=(),
            )

        checked_projects: list[
            OperationSummaryAuthorityCloseProject
        ] = []
        closable_project_ids: list[str] = []
        for project_id in project_ids:
            project_row = connection.execute(
                """
                SELECT data, updated_at
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            reconciliation = _reconcile_project_operation_summaries(
                connection,
                project_id,
                decode_source,
            )
            issue_categories = tuple(
                sorted(
                    {
                        issue.category
                        for issue in reconciliation.issues
                    }
                )
            )
            source_is_absent = False
            if project_row is not None and not issue_categories:
                try:
                    source_is_absent = (
                        decode_source(
                            project_id,
                            str(project_row["data"]),
                            str(project_row["updated_at"]),
                            False,
                        )
                        is None
                    )
                except LegacySummaryDecodeError:
                    issue_categories = (
                        "legacy_project_invalid",
                    )
            if source_is_absent:
                checked_projects.append(
                    OperationSummaryAuthorityCloseProject(
                        project_id=project_id,
                        status="skipped",
                        issue_categories=(),
                    )
                )
                continue
            state_row = connection.execute(
                """
                SELECT summary_status
                FROM video_localization_operation_projection_state
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if (
                not issue_categories
                and (
                    state_row is None
                    or str(state_row["summary_status"])
                    not in {"verified", "authoritative"}
                )
            ):
                issue_categories = ("projection_not_verified",)
            if issue_categories:
                checked_projects.append(
                    OperationSummaryAuthorityCloseProject(
                        project_id=project_id,
                        status="rejected",
                        issue_categories=issue_categories,
                    )
                )
                continue
            status: SummaryAuthorityCloseStatus = (
                "unchanged"
                if str(state_row["summary_status"])
                == "authoritative"
                else "authoritative"
            )
            checked_projects.append(
                OperationSummaryAuthorityCloseProject(
                    project_id=project_id,
                    status=status,
                    issue_categories=(),
                )
            )
            closable_project_ids.append(project_id)

        if any(
            project.status == "rejected"
            for project in checked_projects
        ):
            return _authority_close_report(
                closed=current_authority is not None,
                changed=False,
                closed_at=(
                    current_authority.closed_at
                    if current_authority is not None
                    else None
                ),
                scanned_project_count=len(project_ids),
                truncated=False,
                projects=tuple(checked_projects),
            )

        closed_at = (
            current_authority.closed_at
            if current_authority is not None
            else datetime.now(timezone.utc).isoformat()
        )
        projection_changed = False
        for project_id in closable_project_ids:
            projection_changed = (
                video_localization_operation_summary_store
                .close_project_projection_authority(
                    connection,
                    project_id,
                    closed_at=closed_at,
                )
                or projection_changed
            )
        marker_changed = (
            video_localization_operation_summary_store
            .mark_summary_authority_closed(
                connection,
                closed_at=closed_at,
            )
        )
        return _authority_close_report(
            closed=True,
            changed=projection_changed or marker_changed,
            closed_at=closed_at,
            scanned_project_count=len(project_ids),
            truncated=False,
            projects=tuple(checked_projects),
        )


def _authority_close_report(
    *,
    closed: bool,
    changed: bool,
    closed_at: str | None,
    scanned_project_count: int,
    truncated: bool,
    projects: tuple[OperationSummaryAuthorityCloseProject, ...],
) -> OperationSummaryAuthorityCloseReport:
    counts = Counter(project.status for project in projects)
    return OperationSummaryAuthorityCloseReport(
        closed=closed,
        changed=changed,
        closed_at=closed_at,
        scanned_project_count=scanned_project_count,
        authoritative_project_count=counts["authoritative"],
        unchanged_project_count=counts["unchanged"],
        skipped_project_count=counts["skipped"],
        rejected_project_count=counts["rejected"],
        truncated=truncated,
        projects=projects,
    )


def _promote_project_operation_summaries(
    project_id: str,
    decode_source: LegacySummaryDecoder,
) -> OperationSummaryPromotionProject:
    try:
        with database.conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT data, updated_at
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                return OperationSummaryPromotionProject(
                    project_id=project_id,
                    status="rejected",
                    issue_categories=("project_missing",),
                )
            try:
                source = decode_source(
                    project_id,
                    str(row["data"]),
                    str(row["updated_at"]),
                    False,
                )
            except LegacySummaryDecodeError:
                legacy_source_is_absent = False
            else:
                legacy_source_is_absent = source is None
            if legacy_source_is_absent:
                return OperationSummaryPromotionProject(
                    project_id=project_id,
                    status="skipped",
                    issue_categories=(),
                )
            reconciliation = (
                _reconcile_project_operation_summaries(
                    connection,
                    project_id,
                    decode_source,
                )
            )
            if reconciliation.issues:
                return OperationSummaryPromotionProject(
                    project_id=project_id,
                    status="rejected",
                    issue_categories=tuple(
                        sorted(
                            {
                                issue.category
                                for issue in reconciliation.issues
                            }
                        )
                    ),
                )
            changed = (
                video_localization_operation_summary_store
                .promote_project_projection(
                    connection,
                    project_id,
                    verified_at=datetime.now(
                        timezone.utc
                    ).isoformat(),
                )
            )
        return OperationSummaryPromotionProject(
            project_id=project_id,
            status="verified" if changed else "unchanged",
            issue_categories=(),
        )
    except (
        video_localization_operation_summary_store
        .OperationSummaryProjectionConflict
    ):
        return OperationSummaryPromotionProject(
            project_id=project_id,
            status="rejected",
            issue_categories=("projection_conflict",),
        )
    except Exception:
        return OperationSummaryPromotionProject(
            project_id=project_id,
            status="rejected",
            issue_categories=("storage_error",),
        )


def _backfill_project_operation_summaries(
    project_id: str,
    decode_source: LegacySummaryDecoder,
) -> OperationSummaryBackfillProject:
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("SAVEPOINT summary_project_backfill")
        try:
            row = connection.execute(
                """
                SELECT data, updated_at
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                result = OperationSummaryBackfillProject(
                    project_id=project_id,
                    status="skipped",
                    category="project_missing",
                    operation_count=0,
                )
            else:
                source = decode_source(
                    project_id,
                    str(row["data"]),
                    str(row["updated_at"]),
                    True,
                )
                if source is None:
                    result = OperationSummaryBackfillProject(
                        project_id=project_id,
                        status="skipped",
                        category="not_video_localization",
                        operation_count=0,
                    )
                else:
                    if source.normalized:
                        connection.execute(
                            """
                            UPDATE projects
                            SET
                                data = ?,
                                repository_revision = repository_revision + 1
                            WHERE project_id = ?
                            """,
                            (
                                json.dumps(
                                    source.project_payload,
                                    ensure_ascii=False,
                                ),
                                project_id,
                            ),
                        )
                    (
                        video_localization_operation_ledger_store
                        .backfill_project_operations(
                            connection,
                            project_id,
                            list(source.operation_payloads),
                            updated_at=source.projected_at,
                        )
                    )
                    sync = (
                        video_localization_operation_summary_store
                        .backfill_project_summaries(
                            connection,
                            project_id,
                            source.cores,
                            projected_at=source.projected_at,
                        )
                    )
                    result = OperationSummaryBackfillProject(
                        project_id=project_id,
                        status=(
                            "backfilled"
                            if sync.changed or source.normalized
                            else "unchanged"
                        ),
                        category="none",
                        operation_count=len(
                            source.operation_payloads
                        ),
                    )
        except Exception as exc:
            connection.execute(
                "ROLLBACK TO SAVEPOINT summary_project_backfill"
            )
            connection.execute(
                "RELEASE SAVEPOINT summary_project_backfill"
            )
            (
                video_localization_operation_summary_store
                .mark_project_repair_required(
                    connection,
                    project_id,
                )
            )
            return OperationSummaryBackfillProject(
                project_id=project_id,
                status="repair_required",
                category=_summary_backfill_error_category(exc),
                operation_count=0,
            )
        connection.execute(
            "RELEASE SAVEPOINT summary_project_backfill"
        )
        return result


def _reconcile_project_operation_summaries(
    connection: Connection,
    project_id: str,
    decode_source: LegacySummaryDecoder,
) -> (
    video_localization_operation_summary_audit
    .ProjectSummaryReconciliation
):
    row = connection.execute(
        """
        SELECT data, updated_at
        FROM projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return (
            video_localization_operation_summary_audit
            .reconcile_project_snapshot(
                connection,
                project_id,
                project_exists=False,
                expected_operations=None,
            )
        )
    try:
        source = decode_source(
            project_id,
            str(row["data"]),
            str(row["updated_at"]),
            False,
        )
    except LegacySummaryDecodeError as exc:
        legacy_error = (
            "legacy_project_invalid"
            if exc.category == "legacy_project_invalid"
            else "legacy_draft_invalid"
        )
        return (
            video_localization_operation_summary_audit
            .reconcile_project_snapshot(
                connection,
                project_id,
                project_exists=True,
                expected_operations=None,
                legacy_error=legacy_error,
            )
        )
    return (
        video_localization_operation_summary_audit
        .reconcile_project_snapshot(
            connection,
            project_id,
            project_exists=True,
            expected_operations=(
                None
                if source is None
                else source.expected_operations
            ),
        )
    )


def _summary_backfill_error_category(
    exc: Exception,
) -> SummaryBackfillCategory:
    if isinstance(exc, LegacySummaryDecodeError):
        return exc.category
    if isinstance(
        exc,
        (
            video_localization_operation_ledger_store
            .OperationProjectionConflict,
            video_localization_operation_summary_store
            .OperationSummaryProjectionConflict,
        ),
    ):
        return "projection_conflict"
    return "storage_error"


__all__ = [
    "LegacyOperationSummarySource",
    "LegacySummaryDecodeError",
    "OperationSummaryAuthorityCloseProject",
    "OperationSummaryAuthorityCloseReport",
    "OperationSummaryBackfillProject",
    "OperationSummaryBackfillReport",
    "OperationSummaryPromotionProject",
    "OperationSummaryPromotionReport",
    "OperationSummaryReconciliationReport",
    "SummaryAuthorityCloseStatus",
    "SummaryBackfillCategory",
    "SummaryBackfillStatus",
    "SummaryPromotionStatus",
    "backfill_operation_summaries",
    "close_operation_summary_authority",
    "promote_operation_summaries",
    "reconcile_operation_summaries",
]
