#!/usr/bin/env python3
"""Benchmark the current operation feed with a fixed fixture or live project."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import psutil


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    operation_summary_projection,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationOperation,
)
from app.schemas.voice_studio import Project  # noqa: E402
from app.services import database  # noqa: E402
from app.services import project_store  # noqa: E402
from app.services import video_localization_operations  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_summary_store,
)


SCHEMA_VERSION = "video-localization-operation-read-benchmark-v7"
FIXTURE_VERSION = "operation-feed-fixed-v6"
FIXTURE_PROJECT_ID = "benchmark-operation-summaries"


def _request(url: str) -> tuple[float, int]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        body = response.read()
    return (time.perf_counter() - started) * 1_000, len(body)


def _nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one sample is required")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(_nearest_rank(values, 0.95), 3),
        "max_ms": round(max(values), 3),
    }


def _process_io(
    process: psutil.Process | None = None,
) -> dict[str, int | bool]:
    process = process or psutil.Process()
    reader = getattr(process, "io_counters", None)
    if reader is None:
        return {
            "supported": False,
            "read_bytes": 0,
            "write_bytes": 0,
        }
    counters = reader()
    return {
        "supported": True,
        "read_bytes": int(getattr(counters, "read_bytes", 0)),
        "write_bytes": int(getattr(counters, "write_bytes", 0)),
    }


def _database_snapshot(path: Path) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for candidate in (path, Path(f"{path}-wal")):
        if not candidate.exists():
            continue
        payload = candidate.read_bytes()
        if candidate.name.endswith("-wal") and not payload:
            # SQLite may create and later remove an empty WAL sidecar while
            # read connections are opened or closed.  A zero-byte sidecar
            # contains no database change and must not make the read-only
            # benchmark depend on connection-cleanup timing.
            continue
        files[candidate.name] = {
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    return {
        "files": files,
        "total_size_bytes": sum(
            int(item["size_bytes"]) for item in files.values()
        ),
    }


def _fixed_step_result(operation_index: int, step_index: int) -> dict:
    return {
        "label": f"固定步骤 {step_index + 1}",
        "order": (step_index + 1) * 10,
        "status": "success",
        "purpose": "固定无付费性能夹具",
        "summary": f"任务 {operation_index + 1} 的步骤 {step_index + 1} 已完成。",
        "metrics": [
            {"label": "条目", "value": "40"},
            {"label": "批次", "value": str(step_index + 1)},
        ],
        "sections": [
            {
                "title": f"结果组 {section_index + 1}",
                "items": [
                    {
                        "title": f"固定结果 {item_index + 1}",
                        "text": "用于验证终态详情不会进入高频轮询摘要。" * 4,
                    }
                    for item_index in range(20)
                ],
            }
            for section_index in range(3)
        ],
        "notes": ["固定夹具不调用任何模型、搜索或媒体引擎。"],
    }


def _seed_fixed_fixture(
    db_path: Path,
    *,
    terminal_operations: int,
    include_active: bool = True,
) -> None:
    database.set_db_path(db_path)
    operations = []
    heavy_detail_payload = terminal_operations <= 100
    for operation_index in range(terminal_operations):
        steps = (
            {
                f"step_{step_index + 1}": _fixed_step_result(
                    operation_index,
                    step_index,
                )
                for step_index in range(12)
            }
            if heavy_detail_payload
            else {}
        )
        operation = VideoLocalizationOperation(
            operation_id=f"terminal_{operation_index + 1:04d}",
            project_id=FIXTURE_PROJECT_ID,
            kind=(
                "english_asr"
                if operation_index % 2 == 0
                else "localization_draft"
            ),
            status="success",
            label="固定终态任务",
            progress=1,
            result_summary={
                "stage": (
                    "固定 revision 00000000"
                    if operation_index == 0
                    else "已完成"
                ),
                "task_duration_ms": 12_000 + operation_index,
                "cue_count": 160,
                "localized_subtitle_count": 160,
                "task_stage_groups": [
                    {
                        "id": "fixed",
                        "label": "固定流程",
                        "atomic_tasks": [
                            {"id": step_id, "execution": "serial"}
                            for step_id in steps
                        ],
                    }
                ],
                "task_step_results": steps,
                "task_final_result": {
                    "status": "success",
                    "summary": "固定任务已完成。",
                    "sections": [
                        {
                            "title": "最终结果",
                            "items": [
                                {"title": f"步骤 {index + 1}"}
                                for index in range(12)
                            ],
                        }
                    ],
                },
            },
            parameters={
                "execution_mode": "formal",
                **(
                    {
                        "large_private_payload":
                            "不会进入轮询摘要" * 200,
                    }
                    if heavy_detail_payload
                    else {}
                ),
            },
            created_at=f"2026-07-30T00:{operation_index // 60:02d}:{operation_index % 60:02d}",
            completed_at=f"2026-07-30T01:{operation_index // 60:02d}:{operation_index % 60:02d}",
        )
        operations.append(operation.model_dump(mode="json"))
    if include_active:
        active = VideoLocalizationOperation(
            operation_id="active_0001",
            project_id=FIXTURE_PROJECT_ID,
            kind="localization_draft",
            status="running",
            label="固定活动任务",
            progress=0.5,
            result_summary={
                "stage": "固定步骤执行中",
                "stage_id": "active_step",
                "task_step_results": {
                    "active_step": {
                        "label": "固定活动步骤",
                        "status": "running",
                        "summary": "正在执行固定无付费夹具。",
                        "sections": [],
                    }
                },
            },
            created_at="2026-07-30T02:00:00",
        )
        operations.append(active.model_dump(mode="json"))
    project_store.save_project(
        Project(
            project_id=FIXTURE_PROJECT_ID,
            name="Operation summary benchmark",
            default_engine_id=None,
            parameters={
                "video_localization": {
                    "operations": operations,
                    "source_media": {},
                    "stems": {},
                }
            },
            created_at="2026-07-30T00:00:00",
            updated_at="2026-07-30T02:00:00",
        ),
        touch_updated_at=False,
    )
    backfill = (
        video_localization_operations.backfill_operation_summaries(
            limit=10
        )
    )
    if backfill.repair_required_project_count:
        raise RuntimeError(
            "fixed benchmark summary backfill requires repair"
        )
    promotion = (
        video_localization_operations.promote_operation_summaries(
            limit=10
        )
    )
    if promotion.rejected_project_count:
        raise RuntimeError(
            "fixed benchmark summary promotion was rejected"
        )
    state = (
        video_localization_operation_summary_store
        .read_projection_state(FIXTURE_PROJECT_ID)
    )
    if (
        state is None
        or state.summary_status
        not in {"verified", "authoritative"}
    ):
        raise RuntimeError(
            "fixed benchmark did not select the summary repository"
        )


def _advance_fixed_fixture_revision(
    db_path: Path,
    *,
    marker: int,
) -> int:
    """Advance one fixed summary and its durable projection revision."""

    database.set_db_path(db_path)
    with database.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT
                projects.data,
                COALESCE(
                    projection.projection_revision,
                    0
                ) AS projection_revision
            FROM projects
            LEFT JOIN video_localization_operation_projection_state
                AS projection
              ON projection.project_id = projects.project_id
            WHERE projects.project_id = ?
            """,
            (FIXTURE_PROJECT_ID,),
        ).fetchone()
        if row is None:
            raise RuntimeError("fixed HTTP fixture project is missing")
        payload = json.loads(row["data"])
        operations = (
            payload.get("parameters", {})
            .get("video_localization", {})
            .get("operations")
        )
        if not isinstance(operations, list) or not operations:
            raise RuntimeError("fixed HTTP fixture operations are missing")
        first = operations[0]
        if not isinstance(first, dict):
            raise RuntimeError("fixed HTTP fixture operation is invalid")
        summary = first.setdefault("result_summary", {})
        if not isinstance(summary, dict):
            raise RuntimeError("fixed HTTP fixture summary is invalid")
        summary["stage"] = f"固定 revision {marker:08d}"
        typed_operations = [
            VideoLocalizationOperation.model_validate(operation)
            for operation in operations
        ]
        cores = [
            operation_summary_projection
            .operation_summary_core_from_operation(operation)
            for operation in typed_operations
        ]
        next_revision = int(row["projection_revision"]) + 1
        connection.execute(
            """
            UPDATE projects
            SET data = ?
            WHERE project_id = ?
            """,
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
                FIXTURE_PROJECT_ID,
            ),
        )
        connection.execute(
            """
            INSERT INTO video_localization_operation_projection_state(
                project_id,
                projection_revision
            ) VALUES (?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                projection_revision = excluded.projection_revision
            """,
            (FIXTURE_PROJECT_ID, next_revision),
        )
        (
            video_localization_operation_summary_store
            .sync_project_summaries(
                connection,
                FIXTURE_PROJECT_ID,
                cores,
                projected_at=(
                    "2026-07-30T02:00:00+00:00"
                ),
            )
        )
    return next_revision


def _sqlite_data_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA data_version").fetchone()
    if row is None:
        raise RuntimeError("SQLite data_version is unavailable")
    return int(row[0])


def _measure_call(call: Callable[[], object]) -> tuple[float, float, int]:
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    result = call()
    wall_ms = (time.perf_counter() - wall_started) * 1_000
    cpu_ms = (time.process_time() - cpu_started) * 1_000
    if hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
    else:
        payload = [
            item.model_dump(mode="json")
            for item in (result or [])
        ]
    response_bytes = len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return wall_ms, cpu_ms, response_bytes


def run_fixed_feed_v2_benchmark(
    *,
    rounds: int,
    samples_per_round: int,
    terminal_operations: int,
    history_page_size: int = 50,
    workspace: Path | None = None,
) -> dict:
    """Measure bounded v2 head reads and a complete keyset traversal."""

    if not 1 <= history_page_size <= 100:
        raise ValueError("history_page_size must be between 1 and 100")
    original_db_path = database.DB_PATH
    temporary = None
    if workspace is None:
        temporary = tempfile.TemporaryDirectory(
            prefix="voice-studio-operation-feed-v2-benchmark-"
        )
        workspace = Path(temporary.name)
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "voice_studio.db"
    try:
        _seed_fixed_fixture(
            db_path,
            terminal_operations=terminal_operations,
        )

        def call_head():
            return video_localization_operations.read_operation_feed_v2(
                FIXTURE_PROJECT_ID,
                history_limit=history_page_size,
            )

        before_db = _database_snapshot(db_path)
        before_io = _process_io()
        process = psutil.Process()
        rss_before = process.memory_info().rss
        peak_rss = rss_before

        cold_wall, cold_cpu, head_response_bytes = _measure_call(
            call_head
        )
        head = call_head()
        if head is None:
            raise RuntimeError("fixed v2 fixture operation feed is missing")
        if len(head.active_operations) != 1:
            raise RuntimeError(
                "fixed v2 head did not include every active operation"
            )
        if (
            len(head.history)
            != min(history_page_size, terminal_operations)
            or head.history_total != terminal_operations
        ):
            raise RuntimeError(
                "fixed v2 head did not return the expected bounded history"
            )

        head_wall_samples: list[float] = []
        head_cpu_samples: list[float] = []
        for _sample_index in range(rounds * samples_per_round):
            wall_ms, cpu_ms, current_bytes = _measure_call(call_head)
            if current_bytes != head_response_bytes:
                raise RuntimeError(
                    "fixed v2 head response changed during benchmark"
                )
            head_wall_samples.append(wall_ms)
            head_cpu_samples.append(cpu_ms)
            peak_rss = max(peak_rss, process.memory_info().rss)

        def unchanged_call():
            return video_localization_operations.read_operation_feed_v2(
                FIXTURE_PROJECT_ID,
                after_revision=head.revision,
                history_limit=history_page_size,
            )

        unchanged_probe = unchanged_call()
        if (
            unchanged_probe is None
            or unchanged_probe.changed
            or unchanged_probe.active_operations
            or unchanged_probe.history
        ):
            raise RuntimeError(
                "fixed v2 unchanged feed repeated summaries"
            )
        unchanged_wall_samples: list[float] = []
        unchanged_cpu_samples: list[float] = []
        unchanged_response_bytes = 0
        for _sample_index in range(rounds * samples_per_round):
            wall_ms, cpu_ms, current_bytes = _measure_call(
                unchanged_call
            )
            unchanged_wall_samples.append(wall_ms)
            unchanged_cpu_samples.append(cpu_ms)
            if (
                unchanged_response_bytes
                and current_bytes != unchanged_response_bytes
            ):
                raise RuntimeError(
                    "fixed v2 unchanged response changed during benchmark"
                )
            unchanged_response_bytes = current_bytes

        traversal_started = time.perf_counter()
        cursor: str | None = None
        history_revision = head.history_revision
        traversed_ids: list[str] = []
        page_response_bytes: list[int] = []
        page_count = 0
        while True:
            page = (
                head
                if cursor is None
                else video_localization_operations.read_operation_feed_v2(
                    FIXTURE_PROJECT_ID,
                    cursor=cursor,
                    history_limit=history_page_size,
                )
            )
            if page is None:
                raise RuntimeError(
                    "fixed v2 traversal lost the fixture project"
                )
            if page.history_revision != history_revision:
                raise RuntimeError(
                    "fixed v2 traversal changed history revision"
                )
            if page_count and page.active_operations:
                raise RuntimeError(
                    "fixed v2 history page repeated active operations"
                )
            if len(page.history) > history_page_size:
                raise RuntimeError(
                    "fixed v2 history page exceeded its bound"
                )
            payload = page.model_dump(mode="json")
            page_response_bytes.append(
                len(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
            )
            traversed_ids.extend(
                operation.operation_id
                for operation in page.history
            )
            page_count += 1
            cursor = page.next_cursor
            if cursor is None:
                break
        traversal_ms = (
            time.perf_counter() - traversal_started
        ) * 1_000
        if (
            len(traversed_ids) != terminal_operations
            or len(set(traversed_ids)) != terminal_operations
        ):
            raise RuntimeError(
                "fixed v2 traversal did not cover history exactly once"
            )

        after_io = _process_io()
        after_db = _database_snapshot(db_path)
        return {
            "schema_version": SCHEMA_VERSION,
            "environment": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "fixture": {
                "version": FIXTURE_VERSION,
                "mode": "repository_feed_v2",
                "reader_source": "verified_repository",
                "terminal_operations": terminal_operations,
                "active_operations": 1,
                "history_page_size": history_page_size,
                "history_page_count": page_count,
                "rounds": rounds,
                "samples_per_round": samples_per_round,
                "paid_provider_calls": 0,
            },
            "cold_operation_feed_v2_head": {
                "samples": 1,
                "p50_ms": round(cold_wall, 3),
                "p95_ms": round(cold_wall, 3),
                "max_ms": round(cold_wall, 3),
                "cpu_ms": round(cold_cpu, 3),
            },
            "warm_operation_feed_v2_head": {
                **_summary(head_wall_samples),
                "cpu_p50_ms": round(
                    statistics.median(head_cpu_samples),
                    3,
                ),
                "cpu_p95_ms": round(
                    _nearest_rank(head_cpu_samples, 0.95),
                    3,
                ),
            },
            "unchanged_operation_feed_v2": {
                **_summary(unchanged_wall_samples),
                "cpu_p50_ms": round(
                    statistics.median(unchanged_cpu_samples),
                    3,
                ),
                "cpu_p95_ms": round(
                    _nearest_rank(unchanged_cpu_samples, 0.95),
                    3,
                ),
                "response_bytes": unchanged_response_bytes,
            },
            "full_history_traversal": {
                "duration_ms": round(traversal_ms, 3),
                "page_count": page_count,
                "unique_operations": len(set(traversed_ids)),
                "exact_coverage": (
                    len(traversed_ids) == terminal_operations
                    and len(set(traversed_ids)) == terminal_operations
                ),
            },
            "response": {
                "head_bytes": head_response_bytes,
                "max_page_bytes": max(page_response_bytes),
                "total_traversal_bytes": sum(page_response_bytes),
                "unchanged_bytes": unchanged_response_bytes,
            },
            "process": {
                "rss_before_bytes": rss_before,
                "peak_rss_bytes": peak_rss,
                "peak_rss_delta_bytes": max(
                    0,
                    peak_rss - rss_before,
                ),
                "read_bytes_delta": max(
                    0,
                    after_io["read_bytes"] - before_io["read_bytes"],
                ),
                "write_bytes_delta": max(
                    0,
                    after_io["write_bytes"] - before_io["write_bytes"],
                ),
                "io_counters_supported": bool(
                    before_io["supported"] and after_io["supported"]
                ),
            },
            "database": {
                "before": before_db,
                "after": after_db,
                "files_changed": before_db["files"] != after_db["files"],
                "size_delta_bytes": (
                    int(after_db["total_size_bytes"])
                    - int(before_db["total_size_bytes"])
                ),
            },
        }
    finally:
        database.set_db_path(original_db_path)
        if temporary is not None:
            temporary.cleanup()


def _request_json(url: str) -> tuple[float, int, dict]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        body = response.read()
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return (time.perf_counter() - started) * 1_000, len(body), payload


def _available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_service(
    process: subprocess.Popen,
    health_url: str,
    *,
    timeout_seconds: float = 30,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(
                "fixed benchmark service exited during startup"
                + (f": {output.strip()}" if output.strip() else "")
            )
        try:
            _request(health_url)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(
        "fixed benchmark service did not become ready"
    ) from last_error


def _stop_service(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_http_wave(
    *,
    feed_url: str,
    health_url: str,
    concurrency: int,
    expected_revision: int,
    expected_changed: bool,
) -> tuple[list[float], list[float], int]:
    with ThreadPoolExecutor(max_workers=concurrency * 2) as executor:
        feed_futures = [
            executor.submit(_request_json, feed_url)
            for _sample in range(concurrency)
        ]
        health_futures = [
            executor.submit(_request, health_url)
            for _sample in range(concurrency)
        ]
        feed_results = [
            future.result()
            for future in feed_futures
        ]
        health_results = [
            future.result() for future in health_futures
        ]
    for _duration, _size, payload in feed_results:
        changed_payload_is_valid = (
            isinstance(payload.get("active_operations"), list)
            and isinstance(payload.get("history"), list)
            and bool(
                payload["active_operations"]
                or payload["history"]
            )
        )
        unchanged_payload_is_valid = (
            payload.get("active_operations") == []
            and payload.get("history") == []
        )
        if (
            int(payload.get("revision", -1)) != expected_revision
            or bool(payload.get("changed")) is not expected_changed
            or (expected_changed and not changed_payload_is_valid)
            or (not expected_changed and not unchanged_payload_is_valid)
        ):
            raise RuntimeError(
                "operation feed wave returned an unexpected revision payload"
            )
    response_sizes = {
        size
        for _duration, size, _payload in feed_results
    }
    if len(response_sizes) != 1:
        raise RuntimeError(
            "operation feed response changed during one HTTP wave"
        )
    return (
        [
            duration
            for duration, _size, _payload in feed_results
        ],
        [duration for duration, _size in health_results],
        response_sizes.pop(),
    )


def run_fixed_http_benchmark(
    *,
    rounds: int,
    concurrency: int,
    terminal_operations: int,
    advance_revisions: bool = False,
    workspace: Path | None = None,
) -> dict:
    """Run repeatable changed/unchanged waves against an isolated API process."""

    original_db_path = database.DB_PATH
    temporary = None
    if workspace is None:
        temporary = tempfile.TemporaryDirectory(
            prefix="voice-studio-operation-http-benchmark-"
        )
        workspace = Path(temporary.name)
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "voice_studio.db"
    service_process: subprocess.Popen | None = None
    monitor_connection: sqlite3.Connection | None = None
    try:
        _seed_fixed_fixture(
            db_path,
            terminal_operations=terminal_operations,
            include_active=False,
        )
        database.set_db_path(original_db_path)
        port = _available_local_port()
        root = f"http://127.0.0.1:{port}"
        encoded_project = urllib.parse.quote(
            FIXTURE_PROJECT_ID,
            safe="",
        )
        feed_url = (
            f"{root}/api/projects/{encoded_project}/"
            "video-localization/operations/feed-v2?history_limit=50"
        )
        health_url = f"{root}/api/health"
        environment = os.environ.copy()
        existing_python_path = environment.get("PYTHONPATH", "")
        environment.update(
            {
                "PYTHONPATH": (
                    str(BACKEND)
                    if not existing_python_path
                    else f"{BACKEND}{os.pathsep}{existing_python_path}"
                ),
                "VOICE_STUDIO_DB_PATH": str(db_path),
                "VOICE_STUDIO_DATA_DIR": str(workspace / "data"),
                "VOICE_STUDIO_VIDEO_LOCALIZATION_WORKERS": "1",
            }
        )
        service_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "error",
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _wait_for_service(service_process, health_url)
        _request(health_url)
        (
            _initial_duration,
            _initial_changed_response_bytes,
            initial_feed,
        ) = _request_json(feed_url)
        revision = int(initial_feed["revision"])
        initial_revision = revision
        if not initial_feed.get("changed"):
            raise RuntimeError(
                "initial fixed HTTP feed did not include summaries"
            )
        query_separator = "&" if "?" in feed_url else "?"
        unchanged_url = (
            f"{feed_url}{query_separator}after_revision={revision}"
        )
        (
            _unchanged_duration,
            _initial_unchanged_response_bytes,
            unchanged_feed,
        ) = _request_json(unchanged_url)
        if (
            unchanged_feed.get("changed")
            or unchanged_feed.get("active_operations")
            or unchanged_feed.get("history")
        ):
            raise RuntimeError(
                "fixed HTTP unchanged feed repeated summaries"
            )

        process = psutil.Process(service_process.pid)
        monitor_connection = sqlite3.connect(
            db_path,
            timeout=database.SQLITE_BUSY_TIMEOUT_MS / 1_000,
        )
        _sqlite_data_version(monitor_connection)
        before_database = _database_snapshot(db_path)
        before_io = _process_io(process)
        cpu_before = process.cpu_times()
        rss_before = process.memory_info().rss
        peak_rss = rss_before
        changed_samples: list[float] = []
        unchanged_samples: list[float] = []
        changed_health_samples: list[float] = []
        unchanged_health_samples: list[float] = []
        changed_rounds: list[dict] = []
        unchanged_rounds: list[dict] = []
        changed_response_sizes: list[int] = []
        unchanged_response_sizes: list[int] = []
        unexpected_read_window_changes = 0

        for round_index in range(rounds):
            if advance_revisions:
                previous_revision = revision
                revision = _advance_fixed_fixture_revision(
                    db_path,
                    marker=round_index + 1,
                )
                changed_url = (
                    f"{feed_url}{query_separator}"
                    f"after_revision={previous_revision}"
                )
                unchanged_url = (
                    f"{feed_url}{query_separator}"
                    f"after_revision={revision}"
                )
                order = (
                    (
                        "changed",
                        changed_url,
                        revision,
                        True,
                    ),
                    (
                        "unchanged",
                        unchanged_url,
                        revision,
                        False,
                    ),
                )
                if round_index % 2:
                    order = tuple(reversed(order))
            else:
                order = (
                    (
                        "changed",
                        feed_url,
                        revision,
                        True,
                    ),
                    (
                        "unchanged",
                        unchanged_url,
                        revision,
                        False,
                    ),
                )
                if round_index % 2:
                    order = tuple(reversed(order))
            round_results: dict[
                str,
                tuple[list[float], list[float], int],
            ] = {}
            for (
                label,
                url,
                expected_revision,
                expected_changed,
            ) in order:
                data_version_before = _sqlite_data_version(
                    monitor_connection
                )
                round_results[label] = _run_http_wave(
                    feed_url=url,
                    health_url=health_url,
                    concurrency=concurrency,
                    expected_revision=expected_revision,
                    expected_changed=expected_changed,
                )
                data_version_after = _sqlite_data_version(
                    monitor_connection
                )
                if data_version_after != data_version_before:
                    unexpected_read_window_changes += 1
                peak_rss = max(
                    peak_rss,
                    process.memory_info().rss,
                )
            changed, changed_health, current_changed_bytes = (
                round_results["changed"]
            )
            unchanged, unchanged_health, current_unchanged_bytes = (
                round_results["unchanged"]
            )
            changed_response_sizes.append(current_changed_bytes)
            unchanged_response_sizes.append(current_unchanged_bytes)
            changed_samples.extend(changed)
            unchanged_samples.extend(unchanged)
            changed_health_samples.extend(changed_health)
            unchanged_health_samples.extend(unchanged_health)
            changed_rounds.append(
                {"round": round_index + 1, **_summary(changed)}
            )
            unchanged_rounds.append(
                {"round": round_index + 1, **_summary(unchanged)}
            )

        cpu_after = process.cpu_times()
        after_io = _process_io(process)
        after_database = _database_snapshot(db_path)
        cpu_seconds_delta = max(
            0.0,
            (cpu_after.user + cpu_after.system)
            - (cpu_before.user + cpu_before.system),
        )
        measured_requests = rounds * concurrency * 4
        return {
            "schema_version": SCHEMA_VERSION,
            "environment": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "uvicorn_workers": 1,
            },
            "fixture": {
                "version": FIXTURE_VERSION,
                "mode": (
                    "fixed_http_advancing"
                    if advance_revisions
                    else "fixed_http"
                ),
                "reader_source": "verified_repository",
                "terminal_operations": terminal_operations,
                "active_operations": 0,
                "rounds": rounds,
                "concurrency": concurrency,
                "controlled_revision_updates": (
                    rounds if advance_revisions else 0
                ),
                "revision_start": initial_revision,
                "revision_end": revision,
                "repeatable": True,
                "startup_writes_excluded": True,
                "paid_provider_calls": 0,
            },
            "changed_operation_feed": {
                **_summary(changed_samples),
                "rounds": changed_rounds,
            },
            "unchanged_operation_feed": {
                **_summary(unchanged_samples),
                "rounds": unchanged_rounds,
            },
            "health_during_changed_feed": _summary(
                changed_health_samples
            ),
            "health_during_unchanged_feed": _summary(
                unchanged_health_samples
            ),
            "response": {
                "changed_bytes": changed_response_sizes[-1],
                "changed_bytes_min": min(changed_response_sizes),
                "changed_bytes_max": max(changed_response_sizes),
                "unchanged_bytes": unchanged_response_sizes[-1],
                "unchanged_bytes_min": min(unchanged_response_sizes),
                "unchanged_bytes_max": max(unchanged_response_sizes),
            },
            "process": {
                "cpu_seconds_delta": round(cpu_seconds_delta, 6),
                "cpu_ms_per_measured_request": round(
                    cpu_seconds_delta * 1_000 / measured_requests,
                    3,
                ),
                "rss_before_bytes": rss_before,
                "peak_rss_bytes": peak_rss,
                "peak_rss_delta_bytes": max(
                    0,
                    peak_rss - rss_before,
                ),
                "read_bytes_delta": max(
                    0,
                    after_io["read_bytes"]
                    - before_io["read_bytes"],
                ),
                "write_bytes_delta": max(
                    0,
                    after_io["write_bytes"]
                    - before_io["write_bytes"],
                ),
                "io_counters_supported": bool(
                    before_io["supported"]
                    and after_io["supported"]
                ),
            },
            "database": {
                "before": before_database,
                "after": after_database,
                "files_changed": (
                    before_database["files"]
                    != after_database["files"]
                ),
                "expected_files_changed": advance_revisions,
                "controlled_revision_updates": (
                    rounds if advance_revisions else 0
                ),
                "unexpected_read_window_changes": (
                    unexpected_read_window_changes
                ),
                "read_windows_clean": (
                    unexpected_read_window_changes == 0
                ),
                "size_delta_bytes": (
                    int(after_database["total_size_bytes"])
                    - int(before_database["total_size_bytes"])
                ),
            },
        }
    finally:
        database.set_db_path(original_db_path)
        if monitor_connection is not None:
            monitor_connection.close()
        if service_process is not None:
            _stop_service(service_process)
        if temporary is not None:
            temporary.cleanup()


def run_live_benchmark(
    *,
    base_url: str,
    project_id: str,
    sequential_samples: int,
    concurrency: int,
) -> dict:
    root = base_url.rstrip("/")
    encoded_project = urllib.parse.quote(project_id, safe="")
    feed_url = (
        f"{root}/api/projects/{encoded_project}/"
        "video-localization/operations/feed-v2?history_limit=50"
    )
    health_url = f"{root}/api/health"
    _request(feed_url)
    sequential_results = [
        _request(feed_url) for _sample in range(sequential_samples)
    ]
    with ThreadPoolExecutor(max_workers=concurrency * 2) as executor:
        feed_futures = [
            executor.submit(_request, feed_url)
            for _sample in range(concurrency)
        ]
        health_futures = [
            executor.submit(_request, health_url)
            for _sample in range(concurrency)
        ]
        concurrent_feed = [
            future.result()[0] for future in feed_futures
        ]
        concurrent_health = [future.result()[0] for future in health_futures]
    sequential = [result[0] for result in sequential_results]
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "base_url": root,
        },
        "fixture": {
            "project_id": project_id,
            "cache_groups": ["warm"],
            "sequential_samples": sequential_samples,
            "concurrency": concurrency,
            "repeatable": False,
        },
        "sequential_operation_feed": _summary(sequential),
        "concurrent_operation_feed": _summary(concurrent_feed),
        "concurrent_health": _summary(concurrent_health),
        "response": {
            "bytes": sequential_results[-1][1],
        },
    }


def _format_text(report: dict) -> str:
    fixture = report["fixture"]
    lines = [
        f"Schema: {report['schema_version']}",
    ]
    if fixture.get("mode") == "repository_feed_v2":
        cold = report["cold_operation_feed_v2_head"]
        warm = report["warm_operation_feed_v2_head"]
        unchanged = report["unchanged_operation_feed_v2"]
        traversal = report["full_history_traversal"]
        lines.extend(
            [
                (
                    f"Fixture: {fixture['version']} "
                    f"terminal={fixture['terminal_operations']} "
                    f"page={fixture['history_page_size']} "
                    f"pages={fixture['history_page_count']}"
                ),
                f"Cold v2 head: {cold['p50_ms']:.3f}ms",
                (
                    f"Warm v2 head: n={warm['samples']} "
                    f"p50={warm['p50_ms']:.3f}ms "
                    f"p95={warm['p95_ms']:.3f}ms "
                    f"max={warm['max_ms']:.3f}ms"
                ),
                (
                    f"Unchanged v2 head: n={unchanged['samples']} "
                    f"p50={unchanged['p50_ms']:.3f}ms "
                    f"p95={unchanged['p95_ms']:.3f}ms "
                    f"response={unchanged['response_bytes']} bytes"
                ),
                (
                    f"Traversal: {traversal['page_count']} pages "
                    f"{traversal['unique_operations']} unique operations "
                    f"in {traversal['duration_ms']:.3f}ms"
                ),
                (
                    f"Responses: head={report['response']['head_bytes']} "
                    f"max page={report['response']['max_page_bytes']} "
                    f"unchanged={report['response']['unchanged_bytes']} bytes"
                ),
                (
                    f"Read-only: database files changed="
                    f"{report['database']['files_changed']} "
                    f"size delta={report['database']['size_delta_bytes']} bytes"
                ),
            ]
        )
        return "\n".join(lines)
    if fixture.get("mode") in {
        "fixed_http",
        "fixed_http_advancing",
    }:
        changed = report["changed_operation_feed"]
        unchanged = report["unchanged_operation_feed"]
        health_changed = report["health_during_changed_feed"]
        health_unchanged = report[
            "health_during_unchanged_feed"
        ]
        lines.extend(
            [
                (
                    f"Fixture: {fixture['version']} "
                    f"{fixture['mode']} "
                    f"terminal={fixture['terminal_operations']} "
                    f"rounds={fixture['rounds']} "
                    f"concurrency={fixture['concurrency']} "
                    f"revision updates="
                    f"{fixture['controlled_revision_updates']} "
                    f"revision={fixture['revision_start']}"
                    f"->{fixture['revision_end']}"
                ),
                (
                    f"Changed feed: n={changed['samples']} "
                    f"p50={changed['p50_ms']:.3f}ms "
                    f"p95={changed['p95_ms']:.3f}ms"
                ),
                (
                    f"Unchanged feed: n={unchanged['samples']} "
                    f"p50={unchanged['p50_ms']:.3f}ms "
                    f"p95={unchanged['p95_ms']:.3f}ms"
                ),
                (
                    f"Health with changed feed: "
                    f"p95={health_changed['p95_ms']:.3f}ms; "
                    f"with unchanged feed: "
                    f"p95={health_unchanged['p95_ms']:.3f}ms"
                ),
                (
                    f"Responses: changed="
                    f"{report['response']['changed_bytes']} bytes "
                    f"unchanged="
                    f"{report['response']['unchanged_bytes']} bytes"
                ),
                (
                    f"Database files changed="
                    f"{report['database']['files_changed']} "
                    f"(expected="
                    f"{report['database']['expected_files_changed']}); "
                    f"read windows clean="
                    f"{report['database']['read_windows_clean']} "
                    f"size delta="
                    f"{report['database']['size_delta_bytes']} bytes"
                ),
            ]
        )
        return "\n".join(lines)
    lines.append(
        f"Fixture: project={fixture['project_id']} "
        f"cache=warm concurrency={fixture['concurrency']}"
    )
    for label, key in (
        ("Sequential feed", "sequential_operation_feed"),
        ("Concurrent feed", "concurrent_operation_feed"),
        ("Concurrent health", "concurrent_health"),
    ):
        result = report[key]
        lines.append(
            f"{label}: n={result['samples']} "
            f"p50={result['p50_ms']:.3f}ms "
            f"p95={result['p95_ms']:.3f}ms "
            f"max={result['max_ms']:.3f}ms"
        )
    lines.append(f"Response: {report['response']['bytes']} bytes")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id",
        help="Run the exploratory live HTTP mode instead of the fixed fixture.",
    )
    parser.add_argument(
        "--fixed-http",
        action="store_true",
        help=(
            "Launch an isolated API process and run repeatable changed/"
            "unchanged feed concurrency waves."
        ),
    )
    parser.add_argument(
        "--fixed-http-advancing",
        action="store_true",
        help=(
            "Launch an isolated API process and advance the durable "
            "projection revision before every changed-feed wave."
        ),
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--sequential-samples",
        type=int,
        default=20,
    )
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--samples-per-round", type=int, default=20)
    parser.add_argument("--terminal-operations", type=int, default=50)
    parser.add_argument("--history-page-size", type=int, default=50)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    args = parser.parse_args()
    if min(
        args.sequential_samples,
        args.concurrency,
        args.rounds,
        args.samples_per_round,
        args.terminal_operations,
        args.history_page_size,
    ) < 1:
        parser.error("sample, round, concurrency, and fixture counts must be positive")
    selected_modes = sum(
        bool(value)
        for value in (
            args.project_id,
            args.fixed_http,
            args.fixed_http_advancing,
        )
    )
    if selected_modes > 1:
        parser.error(
            "--project-id, --fixed-http, and "
            "--fixed-http-advancing are mutually exclusive"
        )
    if args.history_page_size > 100:
        parser.error("--history-page-size cannot exceed 100")
    return args


def main() -> int:
    args = _parse_args()
    if (
        args.fixed_http
        or args.fixed_http_advancing
    ):
        report = run_fixed_http_benchmark(
            rounds=args.rounds,
            concurrency=args.concurrency,
            terminal_operations=args.terminal_operations,
            advance_revisions=args.fixed_http_advancing,
        )
    elif args.project_id:
        report = run_live_benchmark(
            base_url=args.base_url,
            project_id=args.project_id,
            sequential_samples=args.sequential_samples,
            concurrency=args.concurrency,
        )
    else:
        report = run_fixed_feed_v2_benchmark(
            rounds=args.rounds,
            samples_per_round=args.samples_per_round,
            terminal_operations=args.terminal_operations,
            history_page_size=args.history_page_size,
        )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
