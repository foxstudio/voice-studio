from __future__ import annotations

import sys
import shutil
import subprocess
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from starlette.datastructures import Headers, UploadFile
from fastapi.testclient import TestClient

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
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
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


def test_long_plain_text_is_not_treated_as_a_managed_path(isolated_store):
    long_text = "这是一段正文，不是文件路径。" * 200

    assert custom_reference_store.is_managed_custom_path(long_text) is False
    assert custom_reference_store.managed_paths_in({"input_text": long_text}) == set()


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


def _create_video(path: Path, *, with_audio: bool) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=160x90:r=24:d=3",
    ]
    if with_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3", "-shortest"]
    command += ["-c:v", "mpeg4"]
    if with_audio:
        command += ["-c:a", "aac"]
    command.append(str(path))
    subprocess.run(command, check=True)


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for reference-video tests")
async def test_upload_video_extracts_managed_wav_and_cleans_the_pair(isolated_store, tmp_path: Path):
    video = tmp_path / "reference.mp4"
    _create_video(video, with_audio=True)
    upload = UploadFile(
        BytesIO(video.read_bytes()),
        filename=video.name,
        headers=Headers({"content-type": "video/mp4"}),
    )

    result = await voice_store.upload_audio(upload)
    stored = voice_store.get_file(result["file_id"])

    assert result["source_kind"] == "video"
    assert result["filename"].endswith("_extracted.wav")
    assert stored is not None
    assert stored.source_media_name == video.name
    assert stored.source_media_path
    assert Path(stored.path).suffix == ".wav"
    assert Path(stored.path).parent == Path(stored.source_media_path).parent
    assert Path(stored.path).exists()
    assert Path(stored.source_media_path).exists()
    assert voice_store.audio_tools.probe_audio(stored.path)["sample_rate"] == 24000

    deleted = custom_reference_store.delete_if_unreferenced(stored.path, task_rows=[], history_rows=[], voice_rows=[])
    assert deleted == stored.file_id
    assert not Path(stored.path).exists()
    assert not Path(stored.source_media_path).exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for reference-video tests")
def test_upload_video_endpoint_serves_the_extracted_audio(isolated_store, tmp_path: Path):
    video = tmp_path / "endpoint-reference.mp4"
    _create_video(video, with_audio=True)

    with TestClient(app) as client:
        uploaded = client.post(
            "/api/voices/upload",
            files={"file": (video.name, video.read_bytes(), "video/mp4")},
        )
        assert uploaded.status_code == 200
        result = uploaded.json()
        assert result["source_kind"] == "video"
        served_audio = client.get(f"/api/voices/files/{result['file_id']}/audio")

    assert served_audio.status_code == 200
    assert served_audio.content.startswith(b"RIFF")


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for reference-video tests")
async def test_upload_video_without_audio_is_rejected_without_leaving_a_file(isolated_store, tmp_path: Path):
    video = tmp_path / "silent.mp4"
    _create_video(video, with_audio=False)
    upload = UploadFile(
        BytesIO(video.read_bytes()),
        filename=video.name,
        headers=Headers({"content-type": "video/mp4"}),
    )

    with pytest.raises(AppException) as error:
        await voice_store.upload_audio(upload)

    assert error.value.code == "REFERENCE_VIDEO_NO_AUDIO"
    assert list(custom_reference_store.custom_reference_dir().glob("*")) == []


@pytest.mark.asyncio
async def test_failed_video_extraction_leaves_no_invalid_voice_file(isolated_store, monkeypatch):
    def fail_extraction(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 120)

    monkeypatch.setattr(voice_store.audio_tools, "extract_reference_audio", fail_extraction)
    upload = UploadFile(
        BytesIO(b"not-a-real-video"),
        filename="timeout.mp4",
        headers=Headers({"content-type": "video/mp4"}),
    )

    with pytest.raises(AppException) as error:
        await voice_store.upload_audio(upload)

    assert error.value.code == "REFERENCE_VIDEO_AUDIO_EXTRACT_FAILED"
    assert db.list_all("voice_files", "created_at", limit=-1) == []
    assert list(custom_reference_store.custom_reference_dir().glob("*")) == []


@pytest.mark.asyncio
async def test_unreadable_audio_is_rejected_without_a_dangling_record(isolated_store, monkeypatch):
    monkeypatch.setattr(voice_store.audio_tools, "probe_audio", lambda *_: (_ for _ in ()).throw(ValueError("bad audio")))
    upload = UploadFile(
        BytesIO(b"not-a-real-wave"),
        filename="broken.wav",
        headers=Headers({"content-type": "audio/wav"}),
    )

    with pytest.raises(AppException) as error:
        await voice_store.upload_audio(upload)

    assert error.value.code == "REFERENCE_AUDIO_UNREADABLE"
    assert db.list_all("voice_files", "created_at", limit=-1) == []
    assert list(custom_reference_store.custom_reference_dir().glob("*")) == []


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for reference-video tests")
async def test_registered_video_pair_moves_together_then_deletes_together(isolated_store, tmp_path: Path):
    video = tmp_path / "register-me.mp4"
    _create_video(video, with_audio=True)
    result = await voice_store.upload_audio(
        UploadFile(BytesIO(video.read_bytes()), filename=video.name, headers=Headers({"content-type": "video/mp4"}))
    )
    uploaded = voice_store.get_file(result["file_id"])
    assert uploaded and uploaded.source_media_path
    custom_audio_path = Path(uploaded.path)
    custom_video_path = Path(uploaded.source_media_path)

    voice = voice_store.create_voice(VoiceAssetCreate(name="Video reference", reference_audio_ids=[uploaded.file_id]))
    stored = voice_store.get_file(uploaded.file_id)
    assert stored and stored.source_media_path
    assert Path(stored.path).parent == settings_store.voice_dir()
    assert Path(stored.source_media_path).parent == settings_store.voice_dir()
    assert Path(stored.path).exists() and Path(stored.source_media_path).exists()
    assert not custom_audio_path.exists() and not custom_video_path.exists()

    voice_store.delete_voice(voice.voice_id)
    assert not Path(stored.path).exists()
    assert not Path(stored.source_media_path).exists()
    assert voice_store.get_file(uploaded.file_id) is None


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for reference-video tests")
async def test_referenced_video_pair_is_kept_until_all_references_are_gone(isolated_store, tmp_path: Path):
    video = tmp_path / "keep-me.mp4"
    _create_video(video, with_audio=True)
    result = await voice_store.upload_audio(
        UploadFile(BytesIO(video.read_bytes()), filename=video.name, headers=Headers({"content-type": "video/mp4"}))
    )
    stored = voice_store.get_file(result["file_id"])
    assert stored and stored.source_media_path
    task_rows = [{"parameters": {"reference_audio_path": stored.path}}]

    assert custom_reference_store.delete_if_unreferenced(stored.path, task_rows=task_rows, history_rows=[], voice_rows=[]) is None
    assert Path(stored.path).exists() and Path(stored.source_media_path).exists()

    assert custom_reference_store.delete_if_unreferenced(stored.path, task_rows=[], history_rows=[], voice_rows=[]) == stored.file_id
    assert not Path(stored.path).exists() and not Path(stored.source_media_path).exists()


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
