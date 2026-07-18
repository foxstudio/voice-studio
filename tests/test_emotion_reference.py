from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    BatchGenerateRequest,
    BatchSegmentInput,
    BatchTask,
    GenerateRequest,
    GenerationTask,
    LongformGenerateRequest,
    TaskStatus,
    VoiceFile,
)
from app.services import (  # noqa: E402
    batch_queue,
    custom_reference_store,
    database as db,
    emotion_reference,
    history_store,
    longform_queue,
    settings_store,
    task_queue,
    voice_store,
)


@pytest.fixture
def isolated_store(tmp_path: Path):
    original_db = db.DB_PATH
    previous_settings = settings_store.get().model_copy(deep=True)
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
        settings_store.update(previous_settings)
        db.set_db_path(original_db)


def _audio(path: Path, content: bytes = b"audio") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def _managed_file(file_id: str) -> VoiceFile:
    path = custom_reference_store.allocate_path(file_id, ".wav")
    path.write_bytes(b"managed-audio")
    voice_file = VoiceFile(
        file_id=file_id,
        original_name=f"{file_id}.wav",
        path=str(path),
        size_bytes=path.stat().st_size,
    )
    db.upsert("voice_files", file_id, voice_file.model_dump())
    return voice_file


def test_explicit_emotion_reference_path_wins_over_library_voice(tmp_path, monkeypatch):
    explicit = _audio(tmp_path / "explicit.wav")
    library = _audio(tmp_path / "library.wav")
    calls: list[str] = []

    def reference_path(voice_id: str | None):
        calls.append(str(voice_id))
        return library

    monkeypatch.setattr(voice_store, "reference_path", reference_path)
    request = GenerateRequest(
        text="显式路径优先。",
        engine_id="indextts-v2",
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=explicit,
        emotion_reference_voice_id="emotion-voice",
    )

    assert emotion_reference.resolve_generate_request(request) == explicit
    assert calls == []


def test_missing_explicit_path_does_not_fall_back_to_voice(tmp_path, monkeypatch):
    library = _audio(tmp_path / "library.wav")
    monkeypatch.setattr(voice_store, "reference_path", lambda _voice_id: library)
    request = GenerateRequest(
        text="坏路径不能静默切换来源。",
        engine_id="indextts-v2",
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=str(tmp_path / "missing.wav"),
        emotion_reference_voice_id="emotion-voice",
    )

    with pytest.raises(emotion_reference.EmotionReferenceError, match="EMOTION_REFERENCE_AUDIO_NOT_FOUND"):
        emotion_reference.resolve_generate_request(request)


def test_reference_fields_require_explicit_emotion_reference_mode(tmp_path):
    request = GenerateRequest(
        text="不能静默忽略独立情绪字段。",
        engine_id="indextts-v2",
        emotion_mode="follow_reference",
        emotion_reference_audio_path=_audio(tmp_path / "emotion.wav"),
    )

    with pytest.raises(emotion_reference.EmotionReferenceError, match="EMOTION_REFERENCE_MODE_REQUIRED"):
        emotion_reference.validate_generate_request(request)


@pytest.mark.parametrize(
    ("start_ms", "end_ms", "duration_ms"),
    [
        (1000, None, 5000),
        (None, 3000, 5000),
        (3000, 3000, 5000),
        (4000, 3000, 5000),
        (1000, 6000, 5000),
    ],
)
def test_emotion_reference_trim_range_is_complete_ordered_and_bounded(
    tmp_path,
    start_ms,
    end_ms,
    duration_ms,
):
    request = GenerateRequest(
        text="裁切范围校验。",
        engine_id="indextts-v2",
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=_audio(tmp_path / "emotion.wav"),
        emotion_reference_source_duration_ms=duration_ms,
        emotion_reference_trim_start_ms=start_ms,
        emotion_reference_trim_end_ms=end_ms,
    )

    with pytest.raises(emotion_reference.EmotionReferenceError, match="EMOTION_REFERENCE_RANGE_INVALID"):
        emotion_reference.validate_generate_request(request)


def test_emotion_reference_trim_range_accepts_source_boundary(tmp_path):
    request = GenerateRequest(
        text="裁切范围上界合法。",
        engine_id="indextts-v2",
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=_audio(tmp_path / "emotion.wav"),
        emotion_reference_source_duration_ms=5000,
        emotion_reference_trim_start_ms=1000,
        emotion_reference_trim_end_ms=5000,
    )

    emotion_reference.validate_generate_request(request)


@pytest.mark.parametrize("engine_id", ["omnivoice", "f5-tts"])
def test_independent_emotion_reference_is_indextts_only(tmp_path, engine_id):
    request = GenerateRequest(
        text="只允许 IndexTTS。",
        engine_id=engine_id,
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=_audio(tmp_path / f"{engine_id}.wav"),
    )

    with pytest.raises(emotion_reference.EmotionReferenceError, match="EMOTION_REFERENCE_UNSUPPORTED"):
        emotion_reference.validate_generate_request(request)


@pytest.mark.parametrize(
    ("extra_key", "extra_value"),
    [("emotion", "happy"), ("emotion_values", {"happy": 1.0})],
)
def test_independent_reference_rejects_builtin_emotion_fields(tmp_path, extra_key, extra_value):
    values = {
        "text": "不能混用。",
        "engine_id": "indextts-v2",
        "emotion_mode": "emotion_reference",
        "emotion_reference_audio_path": _audio(tmp_path / "emotion.wav"),
        extra_key: extra_value,
    }
    request = GenerateRequest(**values)

    with pytest.raises(emotion_reference.EmotionReferenceError, match="EMOTION_REFERENCE_CONFLICT"):
        emotion_reference.validate_generate_request(request)


def test_single_runtime_builder_receives_resolved_emotion_reference(tmp_path):
    speaker = _audio(tmp_path / "speaker.wav")
    emotion = _audio(tmp_path / "emotion.wav")
    request = GenerateRequest(
        text="单条参数透传。",
        engine_id="indextts-v2",
        reference_audio_path=speaker,
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=emotion,
        emo_alpha=0.65,
    )

    kwargs = task_queue._kwargs(request, str(tmp_path / "out.wav"))

    assert kwargs["reference_audio"] == speaker
    assert kwargs["emotion_reference_audio"] == emotion
    assert kwargs["emotion"] is None
    assert kwargs["emo_alpha"] == 0.65


def _batch(req: BatchGenerateRequest, output_dir: Path) -> BatchTask:
    return BatchTask(
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        output_format=req.output_format,
        output_dir=str(output_dir),
        segments=batch_queue._result_segments(req),
        parameters=req.model_dump(),
    )


def test_batch_common_reference_and_segment_follow_reference_do_not_leak(tmp_path):
    speaker = _audio(tmp_path / "speaker.wav")
    emotion = _audio(tmp_path / "emotion.wav")
    request = BatchGenerateRequest(
        engine_id="indextts-v2",
        reference_audio_path=speaker,
        parameters={
            "emotion_mode": "emotion_reference",
            "emotion_reference_audio_path": emotion,
            "emo_alpha": 0.6,
        },
        segments=[
            BatchSegmentInput(text="继承公共情绪参考。"),
            BatchSegmentInput(text="逐段恢复跟随。", parameters={"emotion_mode": "follow_reference"}),
        ],
    )

    common = batch_queue._common_kwargs(request)
    segments = batch_queue._runner_segments(request, _batch(request, tmp_path), tmp_path)

    assert common["emotion_reference_audio"] == emotion
    assert "emotion_reference_audio" not in segments[0]["parameters"]
    assert segments[1]["parameters"]["emotion_reference_audio"] is None
    assert segments[1]["parameters"]["emotion"] is None


def test_batch_segment_can_replace_common_emotion_reference(tmp_path):
    speaker = _audio(tmp_path / "speaker.wav")
    common_emotion = _audio(tmp_path / "common-emotion.wav")
    segment_emotion = _audio(tmp_path / "segment-emotion.wav")
    request = BatchGenerateRequest(
        engine_id="indextts-v2",
        reference_audio_path=speaker,
        parameters={
            "emotion_mode": "emotion_reference",
            "emotion_reference_audio_path": common_emotion,
        },
        segments=[
            BatchSegmentInput(
                text="逐段替换。",
                parameters={
                    "emotion_mode": "emotion_reference",
                    "emotion_reference_audio_path": segment_emotion,
                },
            )
        ],
    )

    segment = batch_queue._runner_segments(request, _batch(request, tmp_path), tmp_path)[0]

    assert segment["parameters"]["emotion_reference_audio"] == segment_emotion
    assert segment["parameters"]["emotion"] is None


def test_batch_segment_independent_reference_rejects_top_level_emotion(tmp_path):
    request = BatchGenerateRequest(
        engine_id="indextts-v2",
        reference_audio_path=_audio(tmp_path / "speaker.wav"),
        segments=[
            BatchSegmentInput(
                text="逐段参数也不能混用。",
                emotion="happy",
                parameters={
                    "emotion_mode": "emotion_reference",
                    "emotion_reference_audio_path": _audio(tmp_path / "emotion.wav"),
                },
            )
        ],
    )

    with pytest.raises(emotion_reference.EmotionReferenceError, match="EMOTION_REFERENCE_CONFLICT"):
        emotion_reference.validate_batch_request(request)


@pytest.mark.asyncio
async def test_longform_and_retry_preserve_emotion_reference_snapshot(isolated_store, monkeypatch):
    speaker = _audio(isolated_store / "speaker.wav")
    emotion = _managed_file("longform-emotion")
    request = LongformGenerateRequest(
        generate_request=GenerateRequest(
            text="第一句。第二句。",
            engine_id="indextts-v2",
            reference_audio_path=speaker,
            emotion_mode="emotion_reference",
            emotion_reference_audio_path=emotion.path,
            emotion_reference_source_audio_path=emotion.path,
            emotion_reference_source_duration_ms=5000,
            emotion_reference_trim_start_ms=1000,
            emotion_reference_trim_end_ms=4000,
        ),
        verify_enabled=False,
        merge_enabled=False,
    )
    monkeypatch.setattr(longform_queue, "start_worker", lambda: None)
    monkeypatch.setattr(longform_queue, "_enqueue_task_id", lambda _task_id: None)

    task = await longform_queue.submit(request)
    snapshot = task.parameters["generate_request"]
    assert snapshot["emotion_mode"] == "emotion_reference"
    assert snapshot["emotion_reference_audio_path"] == emotion.path
    assert snapshot["emotion_reference_trim_start_ms"] == 1000
    assert snapshot["emotion_reference_trim_end_ms"] == 4000

    task.status = TaskStatus.failed
    task.segments[0].status = TaskStatus.failed
    longform_queue._save(task)
    retried = await longform_queue.retry_failed(task.longform_task_id)
    assert retried.parameters["generate_request"] == snapshot


def test_history_and_task_lifecycle_track_emotion_source_and_clip(isolated_store):
    source = _managed_file("emotion-source")
    clip = _managed_file("emotion-clip")
    parameters = GenerateRequest(
        text="持久化素材引用。",
        engine_id="indextts-v2",
        reference_audio_path=_audio(isolated_store / "speaker.wav"),
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=clip.path,
        emotion_reference_source_audio_path=source.path,
        emotion_reference_source_duration_ms=6000,
        emotion_reference_trim_start_ms=1000,
        emotion_reference_trim_end_ms=4500,
    ).model_dump()
    task = GenerationTask(
        task_id="emotion-lifecycle",
        engine_id="indextts-v2",
        input_text="持久化素材引用。",
        status=TaskStatus.success,
        parameters=parameters,
    )
    db.upsert("tasks", task.task_id, task.model_dump())

    assert custom_reference_store.delete_if_unreferenced(source.path) is None
    assert custom_reference_store.delete_if_unreferenced(clip.path) is None

    output = Path(settings_store.get().output_dir) / "emotion-lifecycle.wav"
    _audio(output)
    history = task_queue._save_history(task, GenerateRequest(**parameters), output, "emotion-lifecycle", {})
    assert history.parameter_snapshot["emotion_reference_audio_path"] == clip.path
    assert history.parameter_snapshot["emotion_reference_source_audio_path"] == source.path

    task.result_id = history.result_id
    db.upsert("tasks", task.task_id, task.model_dump())
    assert task_queue.delete_task(task.task_id)["status"] == "deleted"
    for voice_file in (source, clip):
        assert not Path(voice_file.path).exists()
        assert voice_store.get_file(voice_file.file_id) is None


def test_clip_only_endpoint_does_not_start_asr(monkeypatch):
    voice_file = VoiceFile(
        file_id="emotion-clip",
        original_name="emotion-clip.wav",
        path="/tmp/emotion-clip.wav",
        size_bytes=10,
        duration_ms=3000,
        sample_rate=24000,
    )
    calls: list[tuple[str, int, int]] = []

    def create_audio_clip(file_id: str, start_ms: int, end_ms: int):
        calls.append((file_id, start_ms, end_ms))
        return {
            "file_id": voice_file.file_id,
            "filename": voice_file.original_name,
            "path": voice_file.path,
            "quality": {"passed": True, "warnings": []},
            "voice_file": voice_file,
        }

    monkeypatch.setattr(voice_store, "create_audio_clip", create_audio_clip)
    with TestClient(app) as client:
        response = client.post(
            "/api/voices/files/source-file/clip",
            json={"start_ms": 1000, "end_ms": 4000},
        )

    assert response.status_code == 200
    assert response.json()["file_id"] == voice_file.file_id
    assert "transcription" not in response.json()
    assert calls == [("source-file", 1000, 4000)]


@pytest.mark.asyncio
async def test_submit_rejects_invalid_emotion_reference_before_queueing(tmp_path, monkeypatch):
    monkeypatch.setattr(task_queue, "start_worker", lambda: None)
    request = GenerateRequest(
        text="错误配置不入队。",
        engine_id="omnivoice",
        emotion_mode="emotion_reference",
        emotion_reference_audio_path=_audio(tmp_path / "emotion.wav"),
    )

    with pytest.raises(AppException) as exc_info:
        await task_queue.submit(request)

    assert exc_info.value.code == "EMOTION_REFERENCE_UNSUPPORTED"
