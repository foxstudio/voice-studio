from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.video_localization_operation_detail import (  # noqa: E402
    OperationDetailCoreV1,
    SemanticTtsGroupingDetailParametersV1,
    operation_detail_core_fingerprint,
    operation_detail_core_json,
)
from app.domains.video_localization import (  # noqa: E402
    operation_detail_projection,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationOperation,
)
from app.services import database  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_store,
)


PROJECT_ID = "project-detail"
OPERATION_ID = "semantic-operation"
WORKFLOW_VERSION = "semantic-tts-grouping-workflow-v2"
WRITTEN_AT = "2026-07-31T05:00:00+00:00"
PROFILE_FINGERPRINT = "a" * 64


@pytest.fixture
def isolated_database(tmp_path: Path):
    original_path = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    try:
        yield tmp_path
    finally:
        database.set_db_path(original_path)


def _core(
    *,
    project_id: str = PROJECT_ID,
    operation_id: str = OPERATION_ID,
    target_chars: int = 120,
) -> OperationDetailCoreV1:
    return OperationDetailCoreV1(
        project_id=project_id,
        operation_id=operation_id,
        kind="semantic_tts_grouping",
        workflow_version=WORKFLOW_VERSION,
        parameters=SemanticTtsGroupingDetailParametersV1(
            profile_id="profile-local",
            profile_configuration_fingerprint=PROFILE_FINGERPRINT,
            target_chars=target_chars,
            max_chars=180,
        ),
    )


def _insert_ledger(
    connection,
    *,
    project_id: str = PROJECT_ID,
    operation_id: str = OPERATION_ID,
    kind: str = "semantic_tts_grouping",
    workflow_version: str = WORKFLOW_VERSION,
) -> None:
    connection.execute(
        """
        INSERT INTO video_localization_operations (
            project_id,
            operation_id,
            kind,
            status,
            parameters_fingerprint,
            workflow_version,
            origin,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, 'queued', ?, ?, 'command', ?, ?)
        """,
        (
            project_id,
            operation_id,
            kind,
            "b" * 64,
            workflow_version,
            WRITTEN_AT,
            WRITTEN_AT,
        ),
    )


def test_detail_core_is_typed_path_free_and_canonical():
    core = _core()

    payload = json.loads(operation_detail_core_json(core))

    assert payload == {
        "detail_schema_version": "operation-detail-core-v1",
        "kind": "semantic_tts_grouping",
        "operation_id": OPERATION_ID,
        "parameters": {
            "max_chars": 180,
            "parameters_schema_version": (
                "semantic-tts-grouping-detail-parameters-v1"
            ),
            "profile_configuration_fingerprint": PROFILE_FINGERPRINT,
            "profile_id": "profile-local",
            "target_chars": 120,
        },
        "project_id": PROJECT_ID,
        "workflow_version": WORKFLOW_VERSION,
    }
    encoded = operation_detail_core_json(core).encode("utf-8")
    assert len(encoded) < 512
    assert operation_detail_core_fingerprint(core) == hashlib.sha256(
        encoded
    ).hexdigest()
    assert "task_step_results" not in encoded.decode()
    assert "artifact" not in encoded.decode()
    assert "/private/" not in encoded.decode()


@pytest.mark.parametrize(
    "payload",
    [
        {
            **_core().model_dump(mode="json"),
            "detail_schema_version": "operation-detail-core-v2",
        },
        {
            **_core().model_dump(mode="json"),
            "task_step_results": {},
        },
        {
            **_core().model_dump(mode="json"),
            "parameters": {
                **_core().parameters.model_dump(mode="json"),
                "artifact_path": "/private/result.json",
            },
        },
        {
            **_core().model_dump(mode="json"),
            "parameters": {
                **_core().parameters.model_dump(mode="json"),
                "profile_configuration_fingerprint": "short",
            },
        },
        {
            **_core().model_dump(mode="json"),
            "parameters": {
                **_core().parameters.model_dump(mode="json"),
                "target_chars": 200,
                "max_chars": 180,
            },
        },
    ],
)
def test_detail_core_rejects_future_extra_or_invalid_fields(payload):
    with pytest.raises(ValidationError):
        OperationDetailCoreV1.model_validate(payload)


def test_detail_projection_whitelists_only_non_derivable_semantic_input():
    operation = VideoLocalizationOperation(
        operation_id=OPERATION_ID,
        project_id=PROJECT_ID,
        kind="semantic_tts_grouping",
        parameters={
            "profile_id": "profile-local",
            "profile_configuration_fingerprint": PROFILE_FINGERPRINT,
            "workflow_id": "semantic-tts-grouping",
            "target_chars": 120,
            "max_chars": 180,
            "scope": {
                "area": "subtitle",
                "exclusive": False,
            },
            "artifact_path": "/private/should-not-be-copied.json",
        },
        result_summary={
            "task_step_results": {
                "group": {"debug": {"provider_response": "hidden"}}
            }
        },
    )

    core = operation_detail_projection.detail_core_from_operation(
        operation,
        workflow_version=WORKFLOW_VERSION,
    )

    assert core == _core()
    encoded = operation_detail_core_json(core)
    assert "scope" not in encoded
    assert "artifact_path" not in encoded
    assert "task_step_results" not in encoded
    assert "provider_response" not in encoded


def test_detail_projection_keeps_unsupported_workflows_on_legacy_adapter():
    source_audio = VideoLocalizationOperation(
        operation_id="source-audio-operation",
        project_id=PROJECT_ID,
        kind="source_audio",
        parameters={"scope": {"area": "timeline"}},
    )

    assert (
        operation_detail_projection.detail_core_from_operation(
            source_audio,
            workflow_version="operation-v1",
        )
        is None
    )


def test_detail_projection_fails_closed_when_required_input_is_missing():
    operation = VideoLocalizationOperation(
        operation_id=OPERATION_ID,
        project_id=PROJECT_ID,
        kind="semantic_tts_grouping",
        parameters={
            "profile_id": "profile-local",
            "target_chars": 120,
            "max_chars": 180,
        },
    )

    with pytest.raises(
        operation_detail_projection.OperationDetailProjectionError
    ):
        operation_detail_projection.detail_core_from_operation(
            operation,
            workflow_version=WORKFLOW_VERSION,
        )


def test_detail_core_schema_is_additive_for_existing_databases(
    isolated_database: Path,
):
    database_path = isolated_database / "existing.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE existing_data (value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO existing_data (value) VALUES ('preserved')"
        )
    database.set_db_path(database_path)

    with database.conn() as connection:
        value = connection.execute(
            "SELECT value FROM existing_data"
        ).fetchone()[0]
        columns = {
            row["name"]
            for row in connection.execute(
                """
                PRAGMA table_info(
                    video_localization_operation_detail_cores
                )
                """
            )
        }

    assert value == "preserved"
    assert columns == {
        "project_id",
        "operation_id",
        "detail_schema_version",
        "workflow_version",
        "content_fingerprint",
        "core_json",
        "written_at",
    }


def test_detail_core_write_is_exactly_idempotent_and_project_scoped(
    isolated_database,
):
    with database.conn() as connection:
        _insert_ledger(connection)
        _insert_ledger(
            connection,
            project_id="project-other",
        )

    assert detail_store.put_detail_core(
        _core(),
        written_at=WRITTEN_AT,
    ) == "created"
    assert detail_store.put_detail_core(
        _core(),
        written_at="2026-07-31T05:01:00+00:00",
    ) == "reused"
    assert detail_store.put_detail_core(
        _core(project_id="project-other"),
        written_at=WRITTEN_AT,
    ) == "created"

    record = detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    )
    other = detail_store.get_detail_core(
        "project-other",
        OPERATION_ID,
    )
    assert record is not None
    assert other is not None
    assert record.core == _core()
    assert record.written_at == WRITTEN_AT
    assert record.content_fingerprint == (
        operation_detail_core_fingerprint(_core())
    )
    assert other.core.project_id == "project-other"


def test_detail_core_rejects_changed_content_for_same_operation(
    isolated_database,
):
    with database.conn() as connection:
        _insert_ledger(connection)
    detail_store.put_detail_core(_core(), written_at=WRITTEN_AT)

    with pytest.raises(detail_store.OperationDetailCoreConflict):
        detail_store.put_detail_core(
            _core(target_chars=140),
            written_at=WRITTEN_AT,
        )

    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ).core == _core()


@pytest.mark.parametrize(
    ("kind", "workflow_version"),
    [
        ("english_asr", WORKFLOW_VERSION),
        ("semantic_tts_grouping", "operation-v1"),
    ],
)
def test_detail_core_requires_matching_ledger_identity(
    isolated_database,
    kind: str,
    workflow_version: str,
):
    with database.conn() as connection:
        _insert_ledger(
            connection,
            kind=kind,
            workflow_version=workflow_version,
        )

    with pytest.raises(detail_store.OperationDetailCoreIdentityConflict):
        detail_store.put_detail_core(
            _core(),
            written_at=WRITTEN_AT,
        )


def test_detail_core_requires_an_existing_ledger_operation(
    isolated_database,
):
    with pytest.raises(detail_store.OperationDetailCoreIdentityConflict):
        detail_store.put_detail_core(
            _core(),
            written_at=WRITTEN_AT,
        )


@pytest.mark.parametrize(
    "mutation",
    ["fingerprint", "json", "schema", "workflow"],
)
def test_detail_core_corruption_fails_closed(
    isolated_database,
    mutation: str,
):
    with database.conn() as connection:
        _insert_ledger(connection)
    detail_store.put_detail_core(_core(), written_at=WRITTEN_AT)
    with database.conn() as connection:
        if mutation == "fingerprint":
            connection.execute(
                """
                UPDATE video_localization_operation_detail_cores
                SET content_fingerprint = ?
                """,
                ("c" * 64,),
            )
        elif mutation == "json":
            connection.execute(
                """
                UPDATE video_localization_operation_detail_cores
                SET core_json = '{"broken":'
                """
            )
        elif mutation == "schema":
            connection.execute(
                """
                UPDATE video_localization_operation_detail_cores
                SET detail_schema_version = 'operation-detail-core-v2'
                """
            )
        else:
            connection.execute(
                """
                UPDATE video_localization_operation_detail_cores
                SET workflow_version = 'operation-v1'
                """
            )

    with pytest.raises(
        (
            detail_store.OperationDetailCoreIdentityConflict,
            detail_store.OperationDetailCoreIntegrityError,
            detail_store.OperationDetailCoreSchemaError,
        )
    ):
        detail_store.get_detail_core(PROJECT_ID, OPERATION_ID)


def test_detail_core_participates_in_caller_transaction(
    isolated_database,
):
    with pytest.raises(RuntimeError):
        with database.conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _insert_ledger(connection)
            detail_store.put_detail_core_from_connection(
                connection,
                _core(),
                written_at=WRITTEN_AT,
            )
            raise RuntimeError("rollback")

    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ) is None


def test_project_delete_removes_detail_core(
    isolated_database,
):
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES (?, '{"parameters":{"video_localization":{}}}', ?)
            """,
            (PROJECT_ID, WRITTEN_AT),
        )
        _insert_ledger(connection)
    detail_store.put_detail_core(_core(), written_at=WRITTEN_AT)

    video_localization_operation_store.delete_project_with_projection(
        PROJECT_ID
    )

    assert detail_store.get_detail_core(
        PROJECT_ID,
        OPERATION_ID,
    ) is None


def test_detail_core_reader_never_queries_project_blob(
    isolated_database,
):
    with database.conn() as connection:
        _insert_ledger(connection)
    detail_store.put_detail_core(_core(), written_at=WRITTEN_AT)
    statements: list[str] = []

    with database.conn() as connection:
        connection.set_trace_callback(statements.append)
        record = detail_store.get_detail_core_from_connection(
            connection,
            PROJECT_ID,
            OPERATION_ID,
        )
        connection.set_trace_callback(None)

    assert record is not None
    normalized = " ".join("\n".join(statements).lower().split())
    assert "projects.data" not in normalized
    assert " from projects " not in f" {normalized} "
