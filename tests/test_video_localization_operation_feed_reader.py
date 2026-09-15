from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationOperation,
)
from app.domains.video_localization import (  # noqa: E402
    operation_summary_projection,
)
from app.services import database  # noqa: E402
from app.services import video_localization_operations  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_summary_store,
)


def _descriptor(
    project_id: str,
    revision: int,
    *,
    status: str = "verified",
    history_revision: int = 0,
):
    return (
        video_localization_operation_summary_store
        .OperationSummaryFeedDescriptor(
            project_id=project_id,
            projection_revision=revision,
            summary_status=status,
            history_revision=history_revision,
        )
    )


def test_v2_repository_head_single_flights_concurrent_callers(
    tmp_path: Path,
    monkeypatch,
):
    original_db_path = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    reader = video_localization_operations.OperationFeedReader()
    entered = threading.Event()
    release = threading.Event()
    read_count = 0
    read_lock = threading.Lock()
    project_id = "project-v2-repository"
    operation = VideoLocalizationOperation(
        operation_id="operation-v2-repository",
        project_id=project_id,
        kind="source_audio",
        status="success",
        completed_at="2026-07-30T01:00:00+00:00",
    )
    core = (
        operation_summary_projection
        .operation_summary_core_from_operation(operation)
    )
    descriptor = _descriptor(
        project_id,
        7,
        status="verified",
        history_revision=3,
    )
    record = (
        video_localization_operation_summary_store
        .OperationSummaryProjectionRecord(
            core=core,
            kind=operation.kind,
            status=operation.status,
            cancel_requested=operation.cancel_requested,
            created_at=operation.created_at,
            completed_at=operation.completed_at,
        )
    )
    page = (
        video_localization_operation_summary_store
        .OperationSummaryProjectionPage(
            descriptor=descriptor,
            active_records=(),
            history_records=(record,),
            history_total=1,
            next_cursor=None,
        )
    )

    def read_page(
        _project_id: str,
        *,
        history_limit: int,
        cursor,
    ):
        nonlocal read_count
        assert history_limit == 50
        assert cursor is None
        with read_lock:
            read_count += 1
        entered.set()
        assert release.wait(timeout=2)
        return page

    monkeypatch.setattr(
        video_localization_operations
        .video_localization_operation_summary_store,
        "read_verified_project_summary_page",
        read_page,
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(
                    reader.read_v2_page,
                    project_id,
                    descriptor=descriptor,
                    cursor=None,
                    cursor_token=None,
                    history_limit=50,
                )
                for _index in range(8)
            ]
            assert entered.wait(timeout=2)
            time.sleep(0.05)
            release.set()
            results = [future.result(timeout=2) for future in futures]

        assert read_count == 1
        assert all(result is not None for result in results)
        assert {
            result.history[0].operation_id
            for result in results
            if result is not None
        } == {"operation-v2-repository"}
        assert results[0] is not results[1]
        assert results[0] is not None
        results[0].history.clear()
        cached = reader.read_v2_page(
            project_id,
            descriptor=descriptor,
            cursor=None,
            cursor_token=None,
            history_limit=50,
        )
        assert cached is not None
        assert [item.operation_id for item in cached.history] == [
            "operation-v2-repository"
        ]
    finally:
        database.set_db_path(original_db_path)
