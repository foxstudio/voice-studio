from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    media_assets,
    operation_queue,
    service as video_localization_service,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import video_localization_operations  # noqa: E402
from app.domains.video_localization.draft_store import (  # noqa: E402
    DRAFT_WRITE_LOCK,
)


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    return TestClient(app)


def test_operation_application_port_owns_submission_lock(monkeypatch):
    attempted = threading.Event()
    submitted = threading.Event()

    def fake_submit(_project_id: str, _kind: str, _parameters: dict | None):
        submitted.set()
        return None

    monkeypatch.setattr(operation_queue, "submit", fake_submit)

    def invoke() -> None:
        attempted.set()
        video_localization_operations.submit_operation(
            "project_1",
            "source_audio",
            {},
        )

    with DRAFT_WRITE_LOCK:
        thread = threading.Thread(target=invoke)
        thread.start()
        assert attempted.wait(timeout=1)
        assert not submitted.wait(timeout=0.05)

    thread.join(timeout=1)
    assert not thread.is_alive()
    assert submitted.is_set()


def test_operation_routes_use_application_port(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    calls: list[tuple[str, str, dict | None]] = []

    def fake_submit(
        project_id: str,
        kind: str,
        parameters: dict | None,
    ) -> VideoLocalizationOperation:
        calls.append((project_id, kind, parameters))
        return VideoLocalizationOperation(
            project_id=project_id,
            kind=kind,
            parameters=parameters or {},
        )

    monkeypatch.setattr(
        video_localization_operations,
        "submit_operation",
        fake_submit,
    )

    response = client.post(
        "/api/projects/project_1/video-localization/operations",
        json={
            "kind": "source_audio",
            "parameters": {"source_track_id": "source"},
        },
    )

    assert response.status_code == 200
    assert calls == [
        (
            "project_1",
            "source_audio",
            {"source_track_id": "source"},
        )
    ]


def test_asr_uses_only_the_typed_public_entrypoint(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    calls: list[tuple[str, str, dict | None]] = []

    def fake_submit(
        project_id: str,
        kind: str,
        parameters: dict | None,
    ) -> VideoLocalizationOperation:
        calls.append((project_id, kind, parameters))
        return VideoLocalizationOperation(
            project_id=project_id,
            kind=kind,
            parameters=parameters or {},
        )

    monkeypatch.setattr(
        video_localization_operations,
        "submit_operation",
        fake_submit,
    )

    generic = client.post(
        "/api/projects/project_1/video-localization/operations",
        json={"kind": "english_asr", "parameters": {}},
    )
    typed = client.post(
        (
            "/api/projects/project_1/video-localization/"
            "operations/english-asr"
        ),
        json={
            "engine_id": "auto",
            "source_track_id": "vocals",
        },
    )

    assert generic.status_code == 400
    assert typed.status_code == 200
    assert calls == [
        (
            "project_1",
            "english_asr",
            {
                "execution_mode": "full",
                "max_research_rounds": 3,
                "max_research_queries": 9,
                "engine_id": "auto",
                "diarization_engine_id": "auto",
                "source_track_id": "vocals",
                "source_language": "auto",
                "segmentation_profile_id": "generic_zh",
            },
        )
    ]


def test_localization_retry_projects_persisted_parameters_to_current_request(
    monkeypatch,
):
    operation = VideoLocalizationOperation(
        operation_id="localization_retry_source",
        project_id="project_retry",
        kind="localization_draft",
        status="failed",
        parameters={
            "source_language": "en",
            "target_language": "zh-Hans",
            "profile_id": "profile_quality",
            "localization_requirements_id": "zh_cn_native_creator_v1",
            "workflow_id": "localization-v3",
            "execution_mode": "full",
            "scope": {"area": "subtitle"},
        },
    )
    draft = VideoLocalizationDraft(operations=[operation])
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        video_localization_service,
        "get_video_localization",
        lambda _project_id: draft,
    )

    def fake_submit(
        project_id,
        kind,
        parameters,
        *,
        command_type,
        source_operation_id,
    ):
        captured.update(
            project_id=project_id,
            kind=kind,
            parameters=parameters,
            command_type=command_type,
            source_operation_id=source_operation_id,
        )
        return operation

    monkeypatch.setattr(operation_queue, "submit", fake_submit)

    retried = operation_queue.retry("project_retry", operation.operation_id)

    assert retried is operation
    assert captured == {
        "project_id": "project_retry",
        "kind": "localization_draft",
        "command_type": "retry",
        "source_operation_id": operation.operation_id,
        "parameters": {
            "source_language": "en",
            "target_language": "zh-Hans",
            "profile_id": "profile_quality",
            "localization_requirements_id": "zh_cn_native_creator_v1",
        },
    }


def test_sync_operation_reader_runs_in_fastapi_threadpool(
    tmp_path: Path,
    monkeypatch,
):
    _client(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def slow_list(_project_id: str):
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(operation_queue, "list_operations", slow_list)

    async def exercise() -> tuple[int, int, list]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            operations = asyncio.create_task(
                client.get(
                    "/api/projects/project_123/"
                    "video-localization/operations"
                )
            )
            assert await asyncio.to_thread(started.wait, 1)
            health = await asyncio.wait_for(
                client.get("/api/health"),
                timeout=0.25,
            )
            release.set()
            response = await operations
            return health.status_code, response.status_code, response.json()

    health_status, operations_status, payload = asyncio.run(exercise())

    assert health_status == 200
    assert operations_status == 200
    assert payload == []


def test_old_tts_batch_entry_is_not_exposed(
    tmp_path: Path,
    monkeypatch,
):
    _client(tmp_path)
    called = False

    def forbidden_service_call(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("removed endpoint must not call a domain service")

    monkeypatch.setattr(
        video_localization_service,
        "build_tts_batch_request",
        forbidden_service_call,
        raising=False,
    )

    response = TestClient(app).post(
        "/api/projects/project_123/video-localization/tts/batch"
    )

    assert response.status_code == 404
    assert called is False


def test_retired_reference_selection_command_is_not_exposed(
    tmp_path: Path,
):
    client = _client(tmp_path)

    response = client.post(
        "/api/projects/project_123/"
        "video-localization/reference-clips/from-selection",
        json={},
    )

    assert response.status_code == 404


def test_source_media_probe_runs_off_event_loop(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "异步媒体探测", "description": ""},
    ).json()["project_id"]
    started = threading.Event()
    release = threading.Event()

    def slow_probe(_path: Path):
        started.set()
        release.wait(timeout=2)
        return {
            "duration_ms": 1000,
            "width": 1280,
            "height": 720,
            "frame_rate": 25.0,
        }

    monkeypatch.setattr(media_assets, "probe_video", slow_probe)
    async def exercise() -> tuple[int, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as async_client:
            upload = asyncio.create_task(
                async_client.post(
                    f"/api/projects/{project_id}/video-localization/source-media",
                    files={"file": ("sample.mp4", b"not-a-real-video", "video/mp4")},
                )
            )
            assert await asyncio.to_thread(started.wait, 1)
            health = await asyncio.wait_for(
                async_client.get("/api/health"),
                timeout=0.25,
            )
            release.set()
            response = await upload
            return health.status_code, response.status_code

    health_status, upload_status = asyncio.run(exercise())

    assert health_status == 200
    assert upload_status == 200
