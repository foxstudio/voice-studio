from __future__ import annotations

import json
import os
import stat
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.schemas.video_localization_operation_summary import (
    SUMMARY_CORE_SCHEMA_VERSION,
)
from app.schemas.history_scope import history_scope_values
from app.services.paths import expand_path

def _default_db_path() -> Path:
    explicit = os.environ.get("VOICE_STUDIO_DB_PATH")
    if explicit:
        return expand_path(explicit)
    data_dir = expand_path(os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio"))
    return data_dir / "config" / "voice_studio.db"


DB_PATH = _default_db_path()
_DB_RUNTIME_GENERATION = 0
SQLITE_BUSY_TIMEOUT_MS = 5_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voices (
    voice_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_files (
    file_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    result_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history_metadata (
    result_id TEXT PRIMARY KEY,
    project_id TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(result_id) REFERENCES history(result_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS history_scopes (
    result_id TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    PRIMARY KEY (result_id, scope_value),
    FOREIGN KEY(result_id) REFERENCES history(result_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    repository_revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS video_localization_workspace_projections (
    project_id TEXT PRIMARY KEY,
    repository_revision INTEGER NOT NULL,
    catalog_json TEXT NOT NULL,
    workspace_json TEXT,
    timeline_json TEXT NOT NULL DEFAULT '[]',
    semantic_group_summaries_json TEXT NOT NULL DEFAULT '[]',
    projected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
    export_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS download_counters (
    counter_key TEXT PRIMARY KEY,
    value INTEGER NOT NULL CHECK (value >= 0)
);
CREATE TABLE IF NOT EXISTS transcriptions (
    transcription_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asr_tasks (
    task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    batch_task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_segment_attempts (
    attempt_id TEXT PRIMARY KEY,
    batch_task_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    engine_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    provider_request_id TEXT,
    provider_log_id TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('prepared', 'success', 'failed', 'uncertain')),
    prepared_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT,
    UNIQUE (batch_task_id, segment_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS longform_tasks (
    longform_task_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
    preset_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_localization_operation_projection_state (
    project_id TEXT PRIMARY KEY,
    projection_revision INTEGER NOT NULL DEFAULT 0,
    summary_schema_version TEXT,
    summary_status TEXT NOT NULL DEFAULT 'missing',
    summary_row_count INTEGER NOT NULL DEFAULT 0,
    summary_fingerprint TEXT,
    history_revision INTEGER NOT NULL DEFAULT 0,
    history_fingerprint TEXT,
    last_verified_at TEXT
);
CREATE TABLE IF NOT EXISTS video_localization_project_snapshot_projection (
    project_id TEXT PRIMARY KEY,
    target_repository_revision INTEGER NOT NULL,
    create_autosave INTEGER NOT NULL DEFAULT 0,
    requested_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS video_localization_project_cleanup_jobs (
    job_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('reset', 'delete')),
    directory_name TEXT NOT NULL,
    cleanup_payload TEXT NOT NULL DEFAULT '{}',
    package_staged INTEGER NOT NULL DEFAULT 0,
    requested_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS video_localization_tts_handoff_outbox (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    event_kind TEXT NOT NULL
        CHECK (
            event_kind IN (
                'task_registration',
                'result_placement',
                'workflow_terminal'
            )
        ),
    payload_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'applied', 'abandoned')),
    created_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT,
    applied_at TEXT,
    abandoned_at TEXT,
    claim_owner TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    claimed_at_ms INTEGER,
    lease_expires_at_ms INTEGER
);
CREATE TABLE IF NOT EXISTS video_localization_tts_workflow_projection_state (
    project_id TEXT PRIMARY KEY,
    projection_revision INTEGER NOT NULL DEFAULT 0,
    projected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_localization_tts_workflows (
    project_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    generation_task_id TEXT,
    segment_id TEXT NOT NULL,
    status TEXT NOT NULL,
    workflow_json TEXT NOT NULL,
    projected_at TEXT NOT NULL,
    row_revision INTEGER NOT NULL DEFAULT 0,
    generation_status TEXT,
    generation_progress REAL,
    generation_error_message TEXT,
    generation_started_at TEXT,
    generation_completed_at TEXT,
    generation_result_id TEXT,
    PRIMARY KEY (project_id, workflow_id)
);
CREATE TABLE IF NOT EXISTS video_localization_operation_summaries (
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    summary_schema_version TEXT NOT NULL,
    ledger_state_revision INTEGER NOT NULL,
    core_revision INTEGER NOT NULL,
    content_fingerprint TEXT NOT NULL,
    core_json TEXT NOT NULL,
    projected_at TEXT NOT NULL,
    PRIMARY KEY (project_id, operation_id)
);
CREATE TABLE IF NOT EXISTS video_localization_operation_summary_authority (
    authority_key TEXT PRIMARY KEY
        CHECK (authority_key = 'operation-summary'),
    summary_schema_version TEXT NOT NULL,
    closed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_localization_operation_detail_cores (
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    detail_schema_version TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL
        CHECK (length(content_fingerprint) = 64),
    core_json TEXT NOT NULL,
    written_at TEXT NOT NULL,
    PRIMARY KEY (project_id, operation_id)
);
CREATE TABLE IF NOT EXISTS video_localization_operation_attempts (
    attempt_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    runner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    heartbeat_at_ms INTEGER,
    lease_expires_at_ms INTEGER,
    completed_at TEXT,
    error_code TEXT,
    UNIQUE (project_id, operation_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS video_localization_operations (
    operation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    command_revision INTEGER NOT NULL DEFAULT 0,
    state_revision INTEGER NOT NULL DEFAULT 0,
    parameters_fingerprint TEXT NOT NULL,
    workflow_version TEXT NOT NULL DEFAULT 'operation-v1',
    origin TEXT NOT NULL DEFAULT 'legacy_project',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    last_command_id TEXT,
    PRIMARY KEY (project_id, operation_id)
);
CREATE TABLE IF NOT EXISTS video_localization_operation_outbox (
    event_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    source_operation_id TEXT,
    command_type TEXT NOT NULL,
    command_revision INTEGER NOT NULL,
    payload_version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    UNIQUE (project_id, operation_id, command_revision)
);
CREATE TABLE IF NOT EXISTS video_localization_operation_step_attempts (
    step_attempt_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation_attempt_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_schema_version TEXT NOT NULL,
    step_attempt_number INTEGER NOT NULL
        CHECK (step_attempt_number > 0),
    fencing_token INTEGER NOT NULL
        CHECK (fencing_token > 0),
    workflow_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    cost_class TEXT NOT NULL
        CHECK (
            cost_class IN (
                'local_free',
                'external_free',
                'external_paid'
            )
        ),
    provider_name TEXT,
    provider_idempotency_key TEXT,
    provider_request_id TEXT,
    status TEXT NOT NULL
        CHECK (
            status IN (
                'prepared',
                'submitted',
                'result_unknown',
                'success',
                'failed',
                'cancelled'
            )
        ),
    status_revision INTEGER NOT NULL DEFAULT 1
        CHECK (status_revision > 0),
    prepared_at TEXT NOT NULL,
    submitted_at TEXT,
    result_unknown_at TEXT,
    completed_at TEXT,
    output_fingerprint TEXT,
    error_code TEXT,
    CHECK (
        (
            cost_class = 'local_free'
            AND provider_name IS NULL
            AND provider_idempotency_key IS NULL
        )
        OR
        (
            cost_class IN ('external_free', 'external_paid')
            AND provider_name IS NOT NULL
            AND provider_idempotency_key IS NOT NULL
        )
    ),
    UNIQUE (
        project_id,
        operation_id,
        step_id,
        step_attempt_number
    ),
    UNIQUE (
        project_id,
        operation_id,
        operation_attempt_id,
        step_id,
        input_fingerprint
    ),
    UNIQUE (provider_name, provider_idempotency_key)
);
CREATE TABLE IF NOT EXISTS video_localization_operation_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_schema_version TEXT NOT NULL,
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    step_attempt_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    payload_schema_version TEXT NOT NULL,
    media_type TEXT NOT NULL
        CHECK (
            media_type IN (
                'application/json',
                'text/plain',
                'application/octet-stream',
                'image/jpeg'
            )
        ),
    storage_backend TEXT NOT NULL
        CHECK (storage_backend = 'project_package'),
    storage_key TEXT NOT NULL,
    staging_key TEXT,
    content_fingerprint TEXT NOT NULL,
    size_bytes INTEGER NOT NULL
        CHECK (size_bytes > 0 AND size_bytes <= 16777216),
    status TEXT NOT NULL
        CHECK (status IN ('staged', 'committed')),
    status_revision INTEGER NOT NULL DEFAULT 1
        CHECK (status_revision > 0),
    created_at TEXT NOT NULL,
    committed_at TEXT,
    CHECK (length(content_fingerprint) = 64),
    CHECK (
        (
            status = 'staged'
            AND staging_key IS NOT NULL
            AND committed_at IS NULL
        )
        OR
        (
            status = 'committed'
            AND staging_key IS NULL
            AND committed_at IS NOT NULL
        )
    ),
    UNIQUE (
        project_id,
        operation_id,
        step_attempt_id,
        artifact_kind,
        artifact_key
    ),
    UNIQUE (project_id, storage_backend, storage_key)
);
CREATE TABLE IF NOT EXISTS
video_localization_operation_step_adjudications (
    adjudication_id TEXT PRIMARY KEY,
    adjudication_schema_version TEXT NOT NULL,
    step_attempt_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('success', 'failed')),
    source TEXT NOT NULL
        CHECK (source IN ('provider_query', 'human_review')),
    reason_code TEXT NOT NULL
        CHECK (
            length(reason_code) >= 3
            AND length(reason_code) <= 128
        ),
    expected_status_revision INTEGER NOT NULL
        CHECK (expected_status_revision > 0),
    resulting_status_revision INTEGER NOT NULL
        CHECK (
            resulting_status_revision
            = expected_status_revision + 1
        ),
    provider_request_id TEXT,
    output_fingerprint TEXT,
    error_code TEXT,
    decided_at TEXT NOT NULL,
    CHECK (
        source = 'human_review'
        OR provider_request_id IS NOT NULL
    ),
    CHECK (
        (
            decision = 'success'
            AND output_fingerprint IS NOT NULL
            AND length(output_fingerprint) = 64
            AND error_code IS NULL
        )
        OR
        (
            decision = 'failed'
            AND output_fingerprint IS NULL
            AND error_code IS NOT NULL
        )
    )
);
"""

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_engine ON tasks(json_extract(data, '$.engine_id'));
CREATE INDEX IF NOT EXISTS idx_tasks_voice ON tasks(json_extract(data, '$.voice_id'));
CREATE INDEX IF NOT EXISTS idx_tasks_duration ON tasks(json_extract(data, '$.result_duration_ms'));
CREATE INDEX IF NOT EXISTS idx_tasks_longform ON tasks(json_extract(data, '$.longform_task_id'));
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(json_extract(data, '$.task_type'));
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(json_extract(data, '$.project_id'));
CREATE INDEX IF NOT EXISTS idx_history_project ON history(json_extract(data, '$.project_id'));
CREATE INDEX IF NOT EXISTS idx_history_metadata_filter
    ON history_metadata(project_id, source, created_at DESC, result_id);
CREATE INDEX IF NOT EXISTS idx_history_scopes_lookup
    ON history_scopes(scope_value, result_id);
CREATE INDEX IF NOT EXISTS idx_batches_project ON batches(json_extract(data, '$.parameters.project_id'));
CREATE INDEX IF NOT EXISTS idx_longform_tasks_status ON longform_tasks(status);
CREATE INDEX IF NOT EXISTS idx_longform_tasks_created_at ON longform_tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_video_localization_operation_attempts_operation
    ON video_localization_operation_attempts(
        project_id,
        operation_id,
        attempt_number DESC
    );
CREATE INDEX IF NOT EXISTS idx_video_localization_operation_attempts_running
    ON video_localization_operation_attempts(status, heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_video_localization_operation_attempts_claim
    ON video_localization_operation_attempts(
        project_id,
        operation_id,
        status,
        lease_expires_at_ms
    );
CREATE INDEX IF NOT EXISTS idx_video_localization_ledger_project_created
    ON video_localization_operations(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_video_localization_ledger_recovery
    ON video_localization_operations(status, cancel_requested);
DROP INDEX IF EXISTS idx_video_localization_ledger_active_kind;
CREATE UNIQUE INDEX idx_video_localization_ledger_active_kind
    ON video_localization_operations(project_id, kind)
    WHERE origin = 'command' AND status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_video_localization_operation_outbox_pending
    ON video_localization_operation_outbox(status, created_at);
CREATE INDEX IF NOT EXISTS idx_video_localization_tts_handoff_outbox_pending
    ON video_localization_tts_handoff_outbox(
        status,
        created_at,
        event_id
    );
CREATE INDEX IF NOT EXISTS idx_video_localization_tts_handoff_outbox_project
    ON video_localization_tts_handoff_outbox(project_id, event_kind);
CREATE INDEX IF NOT EXISTS idx_video_localization_tts_workflows_project_order
    ON video_localization_tts_workflows(project_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_video_localization_tts_workflows_generation_task
    ON video_localization_tts_workflows(generation_task_id)
    WHERE generation_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_batch_segment_attempts_batch_segment
    ON batch_segment_attempts(batch_task_id, segment_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_video_localization_step_attempts_worker
    ON video_localization_operation_step_attempts(
        operation_attempt_id,
        fencing_token
    );
CREATE INDEX IF NOT EXISTS idx_video_localization_step_attempts_status
    ON video_localization_operation_step_attempts(
        status,
        prepared_at
    );
CREATE INDEX IF NOT EXISTS idx_video_localization_step_adjudications_operation
    ON video_localization_operation_step_adjudications(
        project_id,
        operation_id,
        decided_at,
        adjudication_id
    );
"""


def set_db_path(path: str | Path) -> None:
    global DB_PATH, _DB_RUNTIME_GENERATION
    DB_PATH = expand_path(str(path))
    _DB_RUNTIME_GENERATION += 1


def runtime_identity() -> tuple[str, int]:
    """Identify the configured database instance for process-local caches."""

    return (str(DB_PATH.resolve()), _DB_RUNTIME_GENERATION)


def ensure_db(path: Path | None = None) -> None:
    (path or DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def _ensure_private_database_file(path: Path) -> None:
    """Create or tighten a SQLite file without following user-controlled links."""
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        os.close(descriptor)
    _tighten_private_file(path)


def _tighten_private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            return
        os.chmod(path, 0o600)
    except FileNotFoundError:
        return


def _tighten_database_sidecars(path: Path) -> None:
    _tighten_private_file(path)
    _tighten_private_file(Path(f"{path}-wal"))
    _tighten_private_file(Path(f"{path}-shm"))


_schema_applied_paths: set[Path] = set()
_schema_lock = threading.RLock()


@contextmanager
def conn():
    db_path = DB_PATH
    ensure_db(db_path)
    existed_before_connect = db_path.exists()
    _ensure_private_database_file(db_path)
    db = sqlite3.connect(
        db_path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
    )
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    with _schema_lock:
        if (
            not existed_before_connect
            or db_path not in _schema_applied_paths
        ):
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            _apply_compatible_migrations(db)
            db.executescript(INDEX_DDL)
            _initialize_empty_operation_summary_authority(db)
            db.commit()
            _schema_applied_paths.add(db_path)
    _tighten_database_sidecars(db_path)
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()
    finally:
        db.close()
        _tighten_database_sidecars(db_path)


@contextmanager
def read_conn():
    """Open an existing database snapshot with writes prohibited."""

    db_path = DB_PATH.resolve()
    if not db_path.exists():
        raise FileNotFoundError(
            f"database does not exist: {db_path.name}"
        )
    db = sqlite3.connect(
        f"{db_path.as_uri()}?mode=ro",
        uri=True,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
    )
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    db.execute("PRAGMA query_only = ON")
    db.execute("BEGIN")
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _apply_compatible_migrations(db: sqlite3.Connection) -> None:
    _migrate_history_read_index(db)
    _migrate_project_repository_revision(db)
    _migrate_tts_handoff_claims(db)
    _migrate_operation_projection_state(db)
    _migrate_operation_summary_projection(db)
    _migrate_operation_artifact_media_types(db)
    _migrate_tts_workflow_runtime_projection(db)
    attempt_columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(video_localization_operation_attempts)"
        ).fetchall()
    }
    if "fencing_token" not in attempt_columns:
        db.execute(
            """
            ALTER TABLE video_localization_operation_attempts
            ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0
            """
        )
    if "heartbeat_at_ms" not in attempt_columns:
        db.execute(
            """
            ALTER TABLE video_localization_operation_attempts
            ADD COLUMN heartbeat_at_ms INTEGER
            """
        )
    if "lease_expires_at_ms" not in attempt_columns:
        db.execute(
            """
            ALTER TABLE video_localization_operation_attempts
            ADD COLUMN lease_expires_at_ms INTEGER
            """
        )
    _migrate_operation_attempt_identity_scope(db)
    _migrate_operation_ledger_identity_scope(db)
    _migrate_operation_outbox_identity_scope(db)


def _migrate_tts_workflow_runtime_projection(
    db: sqlite3.Connection,
) -> None:
    """Add the incremental TTS read-model fields to existing databases."""

    columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(video_localization_tts_workflows)"
        ).fetchall()
    }
    definitions = {
        "row_revision": "INTEGER NOT NULL DEFAULT 0",
        "generation_status": "TEXT",
        "generation_progress": "REAL",
        "generation_error_message": "TEXT",
        "generation_started_at": "TEXT",
        "generation_completed_at": "TEXT",
        "generation_result_id": "TEXT",
    }
    for column, definition in definitions.items():
        if column not in columns:
            db.execute(
                f"ALTER TABLE video_localization_tts_workflows "
                f"ADD COLUMN {column} {definition}"
            )


def _migrate_history_read_index(db: sqlite3.Connection) -> None:
    """Backfill the bounded history query model once for legacy rows."""

    rows = db.execute(
        """
        SELECT history.result_id, history.data, history.created_at
        FROM history
        LEFT JOIN history_metadata
          ON history_metadata.result_id = history.result_id
        WHERE history_metadata.result_id IS NULL
        """
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["data"]))
        except (TypeError, ValueError):
            payload = {}
        parameters = payload.get("parameter_snapshot") or {}
        source = (
            parameters.get("source")
            if isinstance(parameters, dict)
            else None
        )
        db.execute(
            """
            INSERT INTO history_metadata (
                result_id,
                project_id,
                source,
                created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                str(row["result_id"]),
                payload.get("project_id"),
                source,
                str(row["created_at"]),
            ),
        )
        db.executemany(
            """
            INSERT OR IGNORE INTO history_scopes (
                result_id,
                scope_value
            ) VALUES (?, ?)
            """,
            [
                (str(row["result_id"]), scope_value)
                for scope_value in history_scope_values(payload)
            ],
        )


def _migrate_tts_handoff_claims(
    db: sqlite3.Connection,
) -> None:
    columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(video_localization_tts_handoff_outbox)"
        ).fetchall()
    }
    additions = {
        "claim_owner": "TEXT",
        "fencing_token": "INTEGER NOT NULL DEFAULT 0",
        "claimed_at_ms": "INTEGER",
        "lease_expires_at_ms": "INTEGER",
    }
    for column_name, declaration in additions.items():
        if column_name in columns:
            continue
        db.execute(
            "ALTER TABLE "
            "video_localization_tts_handoff_outbox "
            f"ADD COLUMN {column_name} {declaration}"
        )


def _migrate_project_repository_revision(
    db: sqlite3.Connection,
) -> None:
    columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(projects)"
        ).fetchall()
    }
    if "repository_revision" not in columns:
        db.execute(
            """
            ALTER TABLE projects
            ADD COLUMN repository_revision INTEGER NOT NULL DEFAULT 0
            """
        )


def _migrate_operation_artifact_media_types(
    db: sqlite3.Connection,
) -> None:
    """Add JPEG without weakening the durable artifact constraints."""

    row = db.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'video_localization_operation_artifacts'
        """
    ).fetchone()
    normalized_sql = "".join(
        str(row["sql"] or "").lower().split()
    )
    if "'image/jpeg'" in normalized_sql:
        return
    db.execute(
        """
        ALTER TABLE video_localization_operation_artifacts
        RENAME TO video_localization_operation_artifacts_without_jpeg
        """
    )
    db.execute(
        """
        CREATE TABLE video_localization_operation_artifacts (
            artifact_id TEXT PRIMARY KEY,
            artifact_schema_version TEXT NOT NULL,
            project_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            step_attempt_id TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            artifact_key TEXT NOT NULL,
            payload_schema_version TEXT NOT NULL,
            media_type TEXT NOT NULL
                CHECK (
                    media_type IN (
                        'application/json',
                        'text/plain',
                        'application/octet-stream',
                        'image/jpeg'
                    )
                ),
            storage_backend TEXT NOT NULL
                CHECK (storage_backend = 'project_package'),
            storage_key TEXT NOT NULL,
            staging_key TEXT,
            content_fingerprint TEXT NOT NULL,
            size_bytes INTEGER NOT NULL
                CHECK (
                    size_bytes > 0
                    AND size_bytes <= 16777216
                ),
            status TEXT NOT NULL
                CHECK (status IN ('staged', 'committed')),
            status_revision INTEGER NOT NULL DEFAULT 1
                CHECK (status_revision > 0),
            created_at TEXT NOT NULL,
            committed_at TEXT,
            CHECK (length(content_fingerprint) = 64),
            CHECK (
                (
                    status = 'staged'
                    AND staging_key IS NOT NULL
                    AND committed_at IS NULL
                )
                OR
                (
                    status = 'committed'
                    AND staging_key IS NULL
                    AND committed_at IS NOT NULL
                )
            ),
            UNIQUE (
                project_id,
                operation_id,
                step_attempt_id,
                artifact_kind,
                artifact_key
            ),
            UNIQUE (
                project_id,
                storage_backend,
                storage_key
            )
        )
        """
    )
    db.execute(
        """
        INSERT INTO video_localization_operation_artifacts (
            artifact_id,
            artifact_schema_version,
            project_id,
            operation_id,
            step_attempt_id,
            artifact_kind,
            artifact_key,
            payload_schema_version,
            media_type,
            storage_backend,
            storage_key,
            staging_key,
            content_fingerprint,
            size_bytes,
            status,
            status_revision,
            created_at,
            committed_at
        )
        SELECT
            artifact_id,
            artifact_schema_version,
            project_id,
            operation_id,
            step_attempt_id,
            artifact_kind,
            artifact_key,
            payload_schema_version,
            media_type,
            storage_backend,
            storage_key,
            staging_key,
            content_fingerprint,
            size_bytes,
            status,
            status_revision,
            created_at,
            committed_at
        FROM video_localization_operation_artifacts_without_jpeg
        """
    )
    db.execute(
        """
        DROP TABLE video_localization_operation_artifacts_without_jpeg
        """
    )


def _initialize_empty_operation_summary_authority(
    db: sqlite3.Connection,
) -> None:
    """Close the summary reader immediately for a brand-new empty store."""

    db.execute(
        """
        INSERT INTO video_localization_operation_summary_authority (
            authority_key,
            summary_schema_version,
            closed_at
        )
        SELECT
            'operation-summary',
            ?,
            STRFTIME('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE NOT EXISTS(SELECT 1 FROM projects)
          AND NOT EXISTS(
              SELECT 1 FROM video_localization_operations
          )
          AND NOT EXISTS(
              SELECT 1
              FROM video_localization_operation_summaries
          )
          AND NOT EXISTS(
              SELECT 1
              FROM video_localization_operation_projection_state
          )
        ON CONFLICT(authority_key) DO NOTHING
        """,
        (SUMMARY_CORE_SCHEMA_VERSION,),
    )


def _migrate_operation_summary_projection(
    db: sqlite3.Connection,
) -> None:
    columns = {
        str(row["name"])
        for row in db.execute(
            """
            PRAGMA table_info(
                video_localization_operation_projection_state
            )
            """
        ).fetchall()
    }
    additions = {
        "summary_schema_version": "TEXT",
        "summary_status": (
            "TEXT NOT NULL DEFAULT 'missing'"
        ),
        "summary_row_count": "INTEGER NOT NULL DEFAULT 0",
        "summary_fingerprint": "TEXT",
        "history_revision": "INTEGER NOT NULL DEFAULT 0",
        "history_fingerprint": "TEXT",
        "last_verified_at": "TEXT",
    }
    for column_name, declaration in additions.items():
        if column_name in columns:
            continue
        db.execute(
            "ALTER TABLE "
            "video_localization_operation_projection_state "
            f"ADD COLUMN {column_name} {declaration}"
        )


def _migrate_operation_projection_state(
    db: sqlite3.Connection,
) -> None:
    legacy_state_exists = db.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'video_localization_operation_index_state'
        """
    ).fetchone()
    if legacy_state_exists is not None:
        legacy_columns = {
            str(row["name"])
            for row in db.execute(
                """
                PRAGMA table_info(
                    video_localization_operation_index_state
                )
                """
            ).fetchall()
        }
        revision_expression = (
            "COALESCE(projection_revision, 0)"
            if "projection_revision" in legacy_columns
            else "0"
        )
        db.execute(
            f"""
            INSERT INTO video_localization_operation_projection_state (
                project_id,
                projection_revision
            )
            SELECT project_id, {revision_expression}
            FROM video_localization_operation_index_state
            WHERE true
            ON CONFLICT(project_id) DO UPDATE SET
                projection_revision = MAX(
                    projection_revision,
                    excluded.projection_revision
                )
            """
        )
    db.execute(
        "DROP TABLE IF EXISTS video_localization_operation_index"
    )
    db.execute(
        """
        DROP TABLE IF EXISTS
        video_localization_operation_index_state
        """
    )


def _migrate_operation_attempt_identity_scope(
    db: sqlite3.Connection,
) -> None:
    row = db.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'video_localization_operation_attempts'
        """
    ).fetchone()
    normalized_sql = "".join(str(row["sql"] or "").lower().split())
    if (
        "unique(project_id,operation_id,attempt_number)"
        in normalized_sql
    ):
        return
    for index_name in (
        "idx_video_localization_operation_attempts_operation",
        "idx_video_localization_operation_attempts_running",
        "idx_video_localization_operation_attempts_claim",
    ):
        db.execute(f"DROP INDEX IF EXISTS {index_name}")
    db.execute(
        """
        ALTER TABLE video_localization_operation_attempts
        RENAME TO video_localization_operation_attempts_global_id
        """
    )
    db.execute(
        """
        CREATE TABLE video_localization_operation_attempts (
            attempt_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            fencing_token INTEGER NOT NULL DEFAULT 0,
            runner_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            heartbeat_at_ms INTEGER,
            lease_expires_at_ms INTEGER,
            completed_at TEXT,
            error_code TEXT,
            UNIQUE (project_id, operation_id, attempt_number)
        )
        """
    )
    db.execute(
        """
        INSERT INTO video_localization_operation_attempts (
            attempt_id,
            project_id,
            operation_id,
            attempt_number,
            fencing_token,
            runner_id,
            status,
            started_at,
            heartbeat_at,
            heartbeat_at_ms,
            lease_expires_at_ms,
            completed_at,
            error_code
        )
        SELECT
            attempt_id,
            project_id,
            operation_id,
            attempt_number,
            fencing_token,
            runner_id,
            status,
            started_at,
            heartbeat_at,
            heartbeat_at_ms,
            lease_expires_at_ms,
            completed_at,
            error_code
        FROM video_localization_operation_attempts_global_id
        """
    )
    db.execute(
        "DROP TABLE video_localization_operation_attempts_global_id"
    )


def _migrate_operation_ledger_identity_scope(
    db: sqlite3.Connection,
) -> None:
    columns = db.execute(
        "PRAGMA table_info(video_localization_operations)"
    ).fetchall()
    primary_key = {
        str(row["name"]): int(row["pk"])
        for row in columns
        if int(row["pk"])
    }
    if primary_key == {"project_id": 1, "operation_id": 2}:
        return
    for index_name in (
        "idx_video_localization_ledger_project_created",
        "idx_video_localization_ledger_recovery",
        "idx_video_localization_ledger_active_kind",
    ):
        db.execute(f"DROP INDEX IF EXISTS {index_name}")
    db.execute(
        """
        ALTER TABLE video_localization_operations
        RENAME TO video_localization_operations_global_id
        """
    )
    db.execute(
        """
        CREATE TABLE video_localization_operations (
            operation_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            command_revision INTEGER NOT NULL DEFAULT 0,
            state_revision INTEGER NOT NULL DEFAULT 0,
            parameters_fingerprint TEXT NOT NULL,
            workflow_version TEXT NOT NULL DEFAULT 'operation-v1',
            origin TEXT NOT NULL DEFAULT 'legacy_project',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            last_command_id TEXT,
            PRIMARY KEY (project_id, operation_id)
        )
        """
    )
    db.execute(
        """
        INSERT INTO video_localization_operations (
            operation_id,
            project_id,
            kind,
            status,
            cancel_requested,
            command_revision,
            state_revision,
            parameters_fingerprint,
            workflow_version,
            origin,
            created_at,
            updated_at,
            completed_at,
            last_command_id
        )
        SELECT
            operation_id,
            project_id,
            kind,
            status,
            cancel_requested,
            command_revision,
            state_revision,
            parameters_fingerprint,
            workflow_version,
            origin,
            created_at,
            updated_at,
            completed_at,
            last_command_id
        FROM video_localization_operations_global_id
        """
    )
    db.execute(
        "DROP TABLE video_localization_operations_global_id"
    )


def _migrate_operation_outbox_identity_scope(
    db: sqlite3.Connection,
) -> None:
    row = db.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'video_localization_operation_outbox'
        """
    ).fetchone()
    normalized_sql = "".join(str(row["sql"] or "").lower().split())
    if (
        "unique(project_id,operation_id,command_revision)"
        in normalized_sql
    ):
        return
    db.execute(
        """
        DROP INDEX IF EXISTS
        idx_video_localization_operation_outbox_pending
        """
    )
    db.execute(
        """
        ALTER TABLE video_localization_operation_outbox
        RENAME TO video_localization_operation_outbox_global_id
        """
    )
    db.execute(
        """
        CREATE TABLE video_localization_operation_outbox (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            source_operation_id TEXT,
            command_type TEXT NOT NULL,
            command_revision INTEGER NOT NULL,
            payload_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            applied_at TEXT,
            UNIQUE (
                project_id,
                operation_id,
                command_revision
            )
        )
        """
    )
    db.execute(
        """
        INSERT INTO video_localization_operation_outbox (
            event_id,
            command_id,
            project_id,
            operation_id,
            source_operation_id,
            command_type,
            command_revision,
            payload_version,
            status,
            created_at,
            applied_at
        )
        SELECT
            event_id,
            command_id,
            project_id,
            operation_id,
            source_operation_id,
            command_type,
            command_revision,
            payload_version,
            status,
            created_at,
            applied_at
        FROM video_localization_operation_outbox_global_id
        """
    )
    db.execute(
        "DROP TABLE video_localization_operation_outbox_global_id"
    )


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _load(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return json.loads(row["data"]) if row else None


def upsert(
    table: str,
    key: str,
    data: dict[str, Any],
    time_field: str = "updated_at",
    transaction_hook: Callable[[sqlite3.Connection], None] | None = None,
) -> None:
    """Write one row and an optional same-transaction application projection."""

    with conn() as db:
        upsert_from_connection(
            db,
            table,
            key,
            data,
            time_field=time_field,
        )
        if transaction_hook is not None:
            transaction_hook(db)


def upsert_from_connection(
    db: sqlite3.Connection,
    table: str,
    key: str,
    data: dict[str, Any],
    *,
    time_field: str = "updated_at",
) -> None:
    """Write one repository row inside a caller-owned transaction."""

    if table == "projects":
        raise ValueError(
            "Project writes must use project_store repository CAS"
        )
    timestamp = data.get(time_field) or data.get("created_at") or ""
    id_field = f"{table[:-1]}_id"
    if table == "history":
        id_field = "result_id"
        time_field = "created_at"
    elif table == "tasks":
        id_field = "task_id"
    elif table == "voice_files":
        id_field = "file_id"
        time_field = "created_at"
    elif table == "exports":
        id_field = "export_id"
        time_field = "created_at"
    elif table == "asr_tasks":
        id_field = "task_id"
        time_field = "created_at"
    elif table == "batches":
        id_field = "batch_task_id"
        time_field = "created_at"
    elif table == "longform_tasks":
        id_field = "longform_task_id"
        time_field = "created_at"
    if table == "tasks":
        db.execute(
            "INSERT OR REPLACE INTO tasks (task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
            (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
        )
    elif table == "batches":
        db.execute(
            "INSERT OR REPLACE INTO batches (batch_task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
            (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
        )
    elif table == "asr_tasks":
        db.execute(
            "INSERT OR REPLACE INTO asr_tasks (task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
            (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
        )
    elif table == "longform_tasks":
        db.execute(
            "INSERT OR REPLACE INTO longform_tasks (longform_task_id, data, created_at, status) VALUES (?, ?, ?, ?)",
            (key, _dump(data), data.get("created_at", ""), data.get("status", "")),
        )
    else:
        db.execute(
            f"INSERT OR REPLACE INTO {table} ({id_field}, data, {time_field}) VALUES (?, ?, ?)",
            (key, _dump(data), timestamp),
        )


def get_one(table: str, key_field: str, key: str) -> dict[str, Any] | None:
    with conn() as db:
        row = db.execute(f"SELECT data FROM {table} WHERE {key_field} = ?", (key,)).fetchone()
    return _load(row)


def list_all(table: str, order_field: str = "created_at", desc: bool = True, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    direction = "DESC" if desc else "ASC"
    with conn() as db:
        if limit < 0:
            rows = db.execute(f"SELECT data FROM {table} ORDER BY {order_field} {direction}").fetchall()
        else:
            rows = db.execute(f"SELECT data FROM {table} ORDER BY {order_field} {direction} LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def delete_one(table: str, key_field: str, key: str) -> None:
    with conn() as db:
        db.execute(f"DELETE FROM {table} WHERE {key_field} = ?", (key,))


def get_settings_rows() -> dict[str, str]:
    with conn() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def save_setting(key: str, value: str) -> None:
    apply_settings_changes({key: value})


def apply_settings_changes(
    upserts: Mapping[str, str],
    deletes: Iterable[str] = (),
) -> None:
    with conn() as db:
        db.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            list(upserts.items()),
        )
        db.executemany(
            "DELETE FROM settings WHERE key = ?",
            [(key,) for key in deletes],
        )
