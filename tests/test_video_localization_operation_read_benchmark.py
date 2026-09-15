from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    ROOT / "scripts" / "benchmark_video_localization_operation_reads.py"
)
SPEC = importlib.util.spec_from_file_location(
    "video_localization_operation_read_benchmark",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_operation_read_benchmark_uses_nearest_rank_p95():
    assert benchmark._summary([1, 2, 3, 4, 5]) == {
        "samples": 5,
        "p50_ms": 3,
        "p95_ms": 5,
        "max_ms": 5,
    }


def test_database_snapshot_ignores_empty_wal_sidecar(tmp_path: Path):
    database_path = tmp_path / "voice_studio.db"
    database_path.write_bytes(b"database")
    wal_path = Path(f"{database_path}-wal")
    wal_path.write_bytes(b"")

    without_wal_payload = benchmark._database_snapshot(database_path)
    wal_path.write_bytes(b"wal payload")
    with_wal_payload = benchmark._database_snapshot(database_path)

    assert set(without_wal_payload["files"]) == {"voice_studio.db"}
    assert set(with_wal_payload["files"]) == {
        "voice_studio.db",
        "voice_studio.db-wal",
    }


def test_fixed_v2_feed_benchmark_is_bounded_exact_and_read_only(
    tmp_path: Path,
):
    original_db_path = benchmark.database.DB_PATH

    report = benchmark.run_fixed_feed_v2_benchmark(
        rounds=2,
        samples_per_round=3,
        terminal_operations=103,
        history_page_size=20,
        workspace=tmp_path,
    )

    assert report["schema_version"] == (
        "video-localization-operation-read-benchmark-v7"
    )
    assert report["fixture"] == {
        "version": "operation-feed-fixed-v6",
        "mode": "repository_feed_v2",
        "reader_source": "verified_repository",
        "terminal_operations": 103,
        "active_operations": 1,
        "history_page_size": 20,
        "history_page_count": 6,
        "rounds": 2,
        "samples_per_round": 3,
        "paid_provider_calls": 0,
    }
    assert report["warm_operation_feed_v2_head"]["samples"] == 6
    assert report["unchanged_operation_feed_v2"]["samples"] == 6
    assert report["unchanged_operation_feed_v2"]["response_bytes"] < 200
    assert report["full_history_traversal"] == {
        "duration_ms": report["full_history_traversal"]["duration_ms"],
        "page_count": 6,
        "unique_operations": 103,
        "exact_coverage": True,
    }
    assert report["response"]["head_bytes"] < 30_000
    assert report["response"]["max_page_bytes"] < 30_000
    assert report["database"]["files_changed"] is False
    assert report["database"]["size_delta_bytes"] == 0
    assert benchmark.database.DB_PATH == original_db_path


def test_fixed_http_operation_feed_benchmark_is_repeatable_and_read_only(
    tmp_path: Path,
):
    original_db_path = benchmark.database.DB_PATH

    report = benchmark.run_fixed_http_benchmark(
        rounds=1,
        concurrency=2,
        terminal_operations=3,
        workspace=tmp_path,
    )
    revision_start = report["fixture"]["revision_start"]
    assert isinstance(revision_start, int)
    assert revision_start >= 0

    assert report["schema_version"] == (
        "video-localization-operation-read-benchmark-v7"
    )
    assert report["fixture"] == {
        "version": "operation-feed-fixed-v6",
        "mode": "fixed_http",
        "reader_source": "verified_repository",
        "terminal_operations": 3,
        "active_operations": 0,
        "rounds": 1,
        "concurrency": 2,
        "controlled_revision_updates": 0,
        "revision_start": revision_start,
        "revision_end": revision_start,
        "repeatable": True,
        "startup_writes_excluded": True,
        "paid_provider_calls": 0,
    }
    assert report["changed_operation_feed"]["samples"] == 2
    assert report["unchanged_operation_feed"]["samples"] == 2
    assert report["health_during_changed_feed"]["samples"] == 2
    assert report["health_during_unchanged_feed"]["samples"] == 2
    assert (
        report["response"]["changed_bytes"]
        > report["response"]["unchanged_bytes"]
    )
    assert report["response"]["unchanged_bytes"] < 200
    assert report["database"]["files_changed"] is False
    assert report["database"]["expected_files_changed"] is False
    assert report["database"]["controlled_revision_updates"] == 0
    assert report["database"]["unexpected_read_window_changes"] == 0
    assert report["database"]["read_windows_clean"] is True
    assert report["database"]["size_delta_bytes"] == 0
    assert report["process"]["cpu_seconds_delta"] >= 0
    assert benchmark.database.DB_PATH == original_db_path


def test_fixed_http_advancing_revision_benchmark_is_controlled(
    tmp_path: Path,
):
    original_db_path = benchmark.database.DB_PATH

    report = benchmark.run_fixed_http_benchmark(
        rounds=2,
        concurrency=2,
        terminal_operations=3,
        advance_revisions=True,
        workspace=tmp_path,
    )
    revision_start = report["fixture"]["revision_start"]
    assert isinstance(revision_start, int)
    assert revision_start >= 0

    assert report["fixture"] == {
        "version": "operation-feed-fixed-v6",
        "mode": "fixed_http_advancing",
        "reader_source": "verified_repository",
        "terminal_operations": 3,
        "active_operations": 0,
        "rounds": 2,
        "concurrency": 2,
        "controlled_revision_updates": 2,
        "revision_start": revision_start,
        "revision_end": revision_start + 2,
        "repeatable": True,
        "startup_writes_excluded": True,
        "paid_provider_calls": 0,
    }
    assert report["changed_operation_feed"]["samples"] == 4
    assert report["unchanged_operation_feed"]["samples"] == 4
    assert report["database"]["files_changed"] is True
    assert report["database"]["expected_files_changed"] is True
    assert report["database"]["controlled_revision_updates"] == 2
    assert report["database"]["unexpected_read_window_changes"] == 0
    assert report["database"]["read_windows_clean"] is True
    assert report["response"]["unchanged_bytes_max"] < 200
    assert benchmark.database.DB_PATH == original_db_path
