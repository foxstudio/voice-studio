from __future__ import annotations

import sys
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from starlette.datastructures import Headers, UploadFile

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    GenerationTask,
    HistoryItem,
    TaskStatus,
    VoiceAsset,
    VoiceAssetCreate,
    VoiceFile,
)
from app.services import custom_reference_store, database as db, history_store, preset_store, settings_store, task_queue, voice_store  # noqa: E402


@pytest.fixture
def isolated_store(tmp_path: Path):
    original_db = db.DB_PATH
    db.set_db_path(tmp_path / "config" / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path / "data"),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    try:
        yield tmp_path
    finally:
        db.set_db_path(original_db)


def _custom_file(file_id: str, *, created_at: str | None = None) -> VoiceFile:
    path = custom_reference_store.allocate_path(file_id, ".wav")
    path.write_bytes(b"reference-audio")
    voice_file = VoiceFile(
        file_id=file_id,
        original_name=f"{file_id}.wav",
        path=str(path),
        size_bytes=path.stat().st_size,
        created_at=created_at or datetime.now().isoformat(timespec="seconds"),
    )
    db.upsert("voice_files", file_id, voice_file.model_dump())
    return voice_file


def _task(
    task_id: str,
    path: str,
    *,
    status: TaskStatus = TaskStatus.success,
    result_id: str | None = None,
) -> GenerationTask:
    task = GenerationTask(
        task_id=task_id,
        engine_id="indextts-v2",
        input_text="test",
        status=status,
        result_id=result_id,
        parameters={"reference_audio_path": path},
    )
    db.upsert("tasks", task.task_id, task.model_dump())
    return task


@pytest.mark.asyncio
async def test_upload_audio_uses_managed_custom_root(isolated_store, monkeypatch):
    monkeypatch.setattr(
        voice_store.audio_tools,
        "probe_audio",
        lambda path: {"duration_ms": 3000, "sample_rate": 24000, "size_bytes": Path(path).stat().st_size},
    )
    upload = UploadFile(
        BytesIO(b"fake-wave"),
        filename="sample.WAV",
        headers=Headers({"content-type": "audio/wav"}),
    )

    result = await voice_store.upload_audio(upload)

    assert custom_reference_store.is_managed_custom_path(result["path"])
    assert Path(result["path"]).parent == custom_reference_store.custom_reference_dir()
    assert voice_store.get_file(result["file_id"]).path == result["path"]


def test_create_voice_promotes_custom_file_and_updates_record(isolated_store):
    uploaded = _custom_file("custom-promote")
    task = _task("promotion-task", uploaded.path)
    history_store.add(
        HistoryItem(
            result_id="promotion-history",
            task_id=task.task_id,
            engine_id=task.engine_id,
            input_text=task.input_text,
            parameter_snapshot={"reference_audio_path": uploaded.path},
        )
    )

    voice = voice_store.create_voice(
        VoiceAssetCreate(name="Durable voice", reference_audio_ids=[uploaded.file_id])
    )

    stored = voice_store.get_file(uploaded.file_id)
    assert stored is not None
    assert Path(stored.path).parent == settings_store.voice_dir()
    assert Path(stored.path).exists()
    assert not Path(uploaded.path).exists()
    assert voice.reference_audio_ids == [uploaded.file_id]
    assert db.get_one("tasks", "task_id", task.task_id)["parameters"]["reference_audio_path"] == stored.path
    assert history_store.get("promotion-history").parameter_snapshot["reference_audio_path"] == stored.path


def test_create_voice_keeps_legacy_voice_file_compatible(isolated_store):
    path = settings_store.voice_dir() / "legacy.wav"
    path.write_bytes(b"legacy")
    legacy = VoiceFile(file_id="legacy", original_name="legacy.wav", path=str(path), size_bytes=6)
    db.upsert("voice_files", legacy.file_id, legacy.model_dump())

    voice_store.create_voice(VoiceAssetCreate(name="Legacy", reference_audio_ids=[legacy.file_id]))

    assert voice_store.get_file(legacy.file_id).path == str(path)
    assert path.exists()


def test_delete_task_removes_unreferenced_custom_files_after_history(isolated_store):
    source = _custom_file("source-file")
    clip = _custom_file("clip-file")
    task = GenerationTask(
        task_id="delete-me",
        engine_id="indextts-v2",
        input_text="test",
        status=TaskStatus.success,
        result_id="history-delete-me",
        parameters={
            "reference_audio_path": clip.path,
            "custom_reference_source_audio_path": source.path,
        },
    )
    db.upsert("tasks", task.task_id, task.model_dump())
    history_store.add(
        HistoryItem(
            result_id=task.result_id,
            task_id=task.task_id,
            engine_id=task.engine_id,
            input_text=task.input_text,
            parameter_snapshot=task.parameters,
        )
    )

    result = task_queue.delete_task(task.task_id)

    assert result == {"task_id": task.task_id, "status": "deleted"}
    assert db.get_one("tasks", "task_id", task.task_id) is None
    assert history_store.get(task.result_id) is None
    for voice_file in (source, clip):
        assert not Path(voice_file.path).exists()
        assert voice_store.get_file(voice_file.file_id) is None


def test_remaining_active_task_and_history_protect_custom_file(isolated_store):
    active_file = _custom_file("active-protected")
    _task("delete-active-peer", active_file.path)
    _task("active-peer", active_file.path, status=TaskStatus.running)

    assert task_queue.delete_task("delete-active-peer")["status"] == "deleted"
    assert Path(active_file.path).exists()
    assert voice_store.get_file(active_file.file_id) is not None

    history_file = _custom_file("history-protected")
    _task("delete-history-peer", history_file.path)
    history_store.add(
        HistoryItem(
            result_id="other-history",
            task_id="already-gone",
            engine_id="indextts-v2",
            input_text="test",
            parameter_snapshot={"reference_audio_path": history_file.path},
        )
    )

    assert task_queue.delete_task("delete-history-peer")["status"] == "deleted"
    assert Path(history_file.path).exists()
    assert voice_store.get_file(history_file.file_id) is not None


def test_batch_request_protects_custom_file(isolated_store):
    uploaded = _custom_file("batch-protected")
    _task("delete-batch-peer", uploaded.path)
    db.upsert(
        "batches",
        "batch-protector",
        {
            "batch_task_id": "batch-protector",
            "status": "success",
            "parameters": {"reference_audio_path": uploaded.path},
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    assert task_queue.delete_task("delete-batch-peer")["status"] == "deleted"
    assert Path(uploaded.path).exists()
    assert voice_store.get_file(uploaded.file_id) is not None


def test_transcription_record_protects_custom_file(isolated_store):
    uploaded = _custom_file("transcription-protected")
    _task("delete-transcription-peer", uploaded.path)
    db.upsert(
        "transcriptions",
        "transcription-protector",
        {
            "transcription_id": "transcription-protector",
            "filename": uploaded.original_name,
            "source_audio_path": uploaded.path,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
        "created_at",
    )

    assert task_queue.delete_task("delete-transcription-peer")["status"] == "deleted"
    assert Path(uploaded.path).exists()
    assert voice_store.get_file(uploaded.file_id) is not None


def test_voice_library_reference_protects_managed_custom_record(isolated_store):
    uploaded = _custom_file("library-protected")
    voice = VoiceAsset(name="Existing library row", reference_audio_ids=[uploaded.file_id])
    db.upsert("voices", voice.voice_id, voice.model_dump(exclude={"engine_bindings"}))
    _task("delete-library-peer", uploaded.path)

    assert task_queue.delete_task("delete-library-peer")["status"] == "deleted"
    assert Path(uploaded.path).exists()
    assert voice_store.get_file(uploaded.file_id) is not None


def test_delete_task_never_deletes_external_reference_path(isolated_store):
    external_path = isolated_store / "external-reference.wav"
    external_path.write_bytes(b"external")
    external = VoiceFile(
        file_id="external-file",
        original_name=external_path.name,
        path=str(external_path),
        size_bytes=external_path.stat().st_size,
    )
    db.upsert("voice_files", external.file_id, external.model_dump())
    _task("external-path-task", str(external_path))

    assert task_queue.delete_task("external-path-task")["status"] == "deleted"
    assert external_path.exists()
    assert voice_store.get_file(external.file_id) is not None


def test_delete_orphan_history_reclaims_its_custom_reference(isolated_store):
    uploaded = _custom_file("history-orphan")
    history_store.add(
        HistoryItem(
            result_id="orphan-result",
            task_id="already-deleted",
            engine_id="indextts-v2",
            input_text="test",
            parameter_snapshot={"reference_audio_path": uploaded.path},
        )
    )

    history_store.delete("orphan-result")

    assert not Path(uploaded.path).exists()
    assert voice_store.get_file(uploaded.file_id) is None


def test_delete_last_preset_reference_reclaims_custom_audio(isolated_store):
    uploaded = _custom_file("preset-orphan")
    db.upsert(
        "presets",
        "custom-preset",
        {
            "preset_id": "custom-preset",
            "name": "Custom",
            "engine_id": "indextts-v2",
            "parameters": {"reference_audio_path": uploaded.path},
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    assert preset_store.delete_preset("custom-preset") is True
    assert not Path(uploaded.path).exists()
    assert voice_store.get_file(uploaded.file_id) is None


def test_symlinked_custom_file_is_never_deleted(isolated_store):
    external = isolated_store / "outside.wav"
    external.write_bytes(b"external")
    path = custom_reference_store.custom_reference_dir() / "linked.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(external)
    record = VoiceFile(file_id="linked", original_name="linked.wav", path=str(path), size_bytes=8)
    db.upsert("voice_files", record.file_id, record.model_dump())

    assert custom_reference_store.delete_if_unreferenced(path) is None
    assert external.exists()


def test_ttl_cleanup_only_removes_old_unreferenced_custom_uploads(isolated_store):
    now = datetime(2026, 7, 13, 12, 0, 0)
    old = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    recent = (now - timedelta(minutes=10)).isoformat(timespec="seconds")
    orphan = _custom_file("ttl-orphan", created_at=old)
    recent_file = _custom_file("ttl-recent", created_at=recent)
    task_file = _custom_file("ttl-task", created_at=old)
    voice_file = _custom_file("ttl-voice", created_at=old)
    _task("ttl-protector", task_file.path, status=TaskStatus.running)
    voice = VoiceAsset(name="TTL protected", reference_audio_ids=[voice_file.file_id])
    db.upsert("voices", voice.voice_id, voice.model_dump(exclude={"engine_bindings"}))

    deleted = custom_reference_store.cleanup_orphaned_uploads(ttl_seconds=3600, now=now)

    assert deleted == [orphan.file_id]
    assert not Path(orphan.path).exists()
    for protected in (recent_file, task_file, voice_file):
        assert Path(protected.path).exists()
        assert voice_store.get_file(protected.file_id) is not None
