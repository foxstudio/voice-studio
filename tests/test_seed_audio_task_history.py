from __future__ import annotations

import base64
import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engines.seed_audio.client import SeedAudioHTTPResponse  # noqa: E402
from app.engines.seed_audio.adapter import SeedAudioAdapter  # noqa: E402
from app.engines.seed_audio.assets import SeedAudioAssetError, SeedAudioAssetResolver  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    BatchGenerateRequest,
    BatchSegmentInput,
    EngineInputAsset,
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    LongformGenerateRequest,
    TaskStatus,
)
from app.services import (  # noqa: E402
    database,
    engine_health,
    engine_manifests,
    engine_policy,
    history_store,
    settings_store,
    task_queue,
    text_verifier,
)


SEED_ENGINE_ID = "doubao-seed-audio-1.0"
VALID_WAV = b"RIFF\x04\x00\x00\x00WAVEdata"
VALID_MP3 = b"ID3\x04\x00\x00mock-mp3"


def _configure_test_storage(tmp_path: Path) -> None:
    task_queue._shutting_down = False
    database.set_db_path(tmp_path / "voice-studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
            cloud_enabled=True,
            doubao_base_url="https://openspeech.bytedance.com",
        )
    )
    settings_store.update_doubao_api_key("mock-seed-key")


def test_generate_request_adds_engine_envelope_without_changing_legacy_defaults():
    legacy = GenerateRequest(text="旧引擎请求")
    assert legacy.input_mode is None
    assert legacy.input_assets == []
    assert legacy.engine_parameters == {}

    seed = GenerateRequest(
        text="雨夜里传来一声钟响。",
        engine_id=SEED_ENGINE_ID,
        input_mode="text",
        input_assets=[],
        engine_parameters={"format": "mp3", "sample_rate": 48000},
    )
    assert seed.input_mode == "text"
    assert seed.engine_parameters["format"] == "mp3"

    asset = EngineInputAsset(
        asset_id="speaker-asset-1",
        type="speaker",
        source="cloud_speaker",
        speaker_id="zh_female_vv_uranus_bigtts",
        display_name="Vivi",
    )
    assert asset.model_dump(exclude_none=True)["speaker_id"] == "zh_female_vv_uranus_bigtts"
    with pytest.raises(ValidationError):
        EngineInputAsset(asset_id="bad", type="audio", source="upload", local_path="/tmp/private.wav")
    with pytest.raises(ValidationError, match="官方 HTTPS"):
        AppSettings(doubao_base_url="https://evil.example.com")


def test_existing_seed_audio_verification_is_repaired_without_another_asr_call(tmp_path: Path):
    _configure_test_storage(tmp_path)
    prompt = """雨声持续铺底。

男子（压低声音）问：“你听见了吗？”

音效（近景）金属门发出一声“咔哒”。

女子回答：“听见了。”"""
    legacy_report = text_verifier.verify_transcript(
        expected_text=prompt,
        transcript_text="你听见了吗？听见了。",
        result_id="seed-result-existing",
        transcription_id="existing-transcript",
        asr_engine_id="qwen3-asr-mlx",
    )
    assert legacy_report.status == "failed"

    task = GenerationTask(
        task_id="seed-existing-coverage",
        engine_id=SEED_ENGINE_ID,
        input_text=prompt,
        status=TaskStatus.success,
        result_id="seed-result-existing",
        verification=legacy_report,
        parameters=GenerateRequest(text=prompt, engine_id=SEED_ENGINE_ID, input_mode="text").model_dump(),
    )
    database.upsert("tasks", task.task_id, task.model_dump())

    repaired = task_queue.get_task(task.task_id)
    assert repaired is not None
    assert repaired.verification is not None
    assert repaired.verification.status == "passed"
    assert repaired.verification.coverage == 1.0
    assert repaired.verification.expected_text == "你听见了吗？\n听见了。"
    assert repaired.input_text == prompt


def test_seed_audio_manual_verification_derives_dialogue_and_skips_pure_sound(tmp_path: Path):
    _configure_test_storage(tmp_path)
    dialogue_prompt = "雨声铺底。\n\n男子问：“你听见了吗？”\n\n女子回答：“听见了。”"
    dialogue = HistoryItem(
        result_id="seed-manual-dialogue",
        task_id="seed-manual-task",
        engine_id=SEED_ENGINE_ID,
        input_text=dialogue_prompt,
    )
    history_store.add(dialogue)

    response = TestClient(app).post(
        "/api/evaluations/tts-verification",
        json={
            "result_id": dialogue.result_id,
            "transcript_text": "你听见了吗？听见了。",
            "asr_engine_id": "qwen3-asr-mlx",
            "language": "zh",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "passed"
    assert response.json()["coverage"] == 1.0
    assert response.json()["expected_text"] == "你听见了吗？\n听见了。"

    pure_sound = HistoryItem(
        result_id="seed-manual-sound",
        task_id="seed-manual-sound-task",
        engine_id=SEED_ENGINE_ID,
        input_text="远处雷声滚过，木门吱呀作响，最后归于安静。",
    )
    history_store.add(pure_sound)
    skipped = TestClient(app).post(
        "/api/evaluations/tts-verification",
        json={"result_id": pure_sound.result_id, "asr_engine_id": "qwen3-asr-mlx", "language": "zh"},
    )
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert skipped.json()["warnings"] == ["没有检测到明确需要发音的对白；纯音乐、环境音和音效内容不适用 ASR 覆盖率。"]


def test_seed_audio_manifest_policy_and_health_use_existing_doubao_settings(monkeypatch):
    detail = engine_manifests.ENGINES[SEED_ENGINE_ID]
    manifest = detail.manifest
    assert manifest.engine_type.value == "cloud"
    assert manifest.input_modes == ["text", "audio", "image"]
    assert manifest.max_reference_audio == 3
    assert manifest.max_reference_image == 1
    assert manifest.max_prompt_chars == 3000
    assert manifest.max_output_seconds == 120
    assert manifest.supported_output_formats == ["wav", "mp3", "pcm", "ogg_opus"]
    assert manifest.supported_sample_rates == [8000, 16000, 24000, 32000, 44100, 48000]
    parameters = {parameter.key: parameter for parameter in manifest.parameter_schema}
    assert parameters["format"].level == "basic"
    assert [option["value"] for option in parameters["format"].options] == manifest.supported_output_formats
    assert parameters["sample_rate"].level == "advanced"
    assert parameters["speech_rate"].level == "basic"
    assert [option["value"] for option in parameters["sample_rate"].options] == manifest.supported_sample_rates
    assert parameters["enable_subtitle"].level == "advanced"
    assert all(parameters[key].level == "advanced" for key in (
        "loudness_rate", "pitch_rate", "aigc_watermark", "aigc_metadata_enable",
        "content_producer", "produce_id", "content_propagator", "propagate_id",
    ))

    assert engine_policy.is_cloud_engine(SEED_ENGINE_ID) is True
    assert engine_policy.is_doubao_engine(SEED_ENGINE_ID) is True
    assert engine_policy.is_doubao_tts(SEED_ENGINE_ID) is False
    assert engine_policy.runner_kind_for(SEED_ENGINE_ID) == "cloud"
    assert engine_policy.timeout_seconds_for(SEED_ENGINE_ID) == 300
    assert engine_policy.supports_longform(SEED_ENGINE_ID) is False
    assert engine_policy.supports_batch(SEED_ENGINE_ID) is False
    assert engine_policy.is_single_generation_only(SEED_ENGINE_ID) is True
    assert engine_policy.requires_manual_replay_after_start(SEED_ENGINE_ID) is True

    monkeypatch.setattr(
        engine_health.settings_store,
        "get",
        lambda: SimpleNamespace(
            cloud_enabled=True,
            doubao_api_key_configured=True,
            doubao_base_url="https://openspeech.example.test",
            mimo_api_key_configured=False,
            mimo_base_url="https://mimo.invalid",
        ),
    )
    health = engine_health._health_cloud_engine(SEED_ENGINE_ID)
    assert health == {
        "healthy": True,
        "status": "configured",
        "base_url": "https://openspeech.example.test",
    }

    response = TestClient(app).get("/api/engines")
    assert response.status_code == 200
    serialized = next(item for item in response.json() if item["manifest"]["engine_id"] == SEED_ENGINE_ID)
    sample_rate = next(
        parameter for parameter in serialized["manifest"]["parameter_schema"] if parameter["key"] == "sample_rate"
    )
    assert sample_rate["options"][2]["value"] == 24000


def test_seed_audio_is_explicitly_rejected_by_longform_and_batch_contracts():
    request = GenerateRequest(text="一段文字", engine_id=SEED_ENGINE_ID, input_mode="text")
    with pytest.raises(ValidationError, match="Seed Audio 1.0 暂只支持单次生成"):
        LongformGenerateRequest(generate_request=request)
    with pytest.raises(ValidationError, match="Seed Audio 1.0 暂只支持单次生成"):
        BatchGenerateRequest(
            engine_id=SEED_ENGINE_ID,
            segments=[BatchSegmentInput(text="一段文字")],
        )


@pytest.mark.asyncio
async def test_seed_audio_single_task_prefers_adapter_and_persists_provider_metadata(
    tmp_path: Path,
    monkeypatch,
):
    _configure_test_storage(tmp_path)
    calls: list[dict] = []

    def mock_transport(**kwargs):
        calls.append(kwargs)
        persisted_before_request = task_queue.get_task("seed-task-1")
        assert persisted_before_request is not None
        assert persisted_before_request.provider_request_id == kwargs["headers"]["X-Api-Request-Id"]
        assert persisted_before_request.provider_state_uncertain is True
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={"X-Tt-Logid": "seed-log-1"},
            body={
                "code": 0,
                "message": "ok",
                "audio": base64.b64encode(VALID_MP3).decode(),
                "duration": 1.25,
                "original_duration": 1.1,
                "subtitle": {"text": "雨夜", "sentences": []},
            },
        )

    monkeypatch.setattr(task_queue, "_seed_audio_transport", mock_transport)
    monkeypatch.setattr(
        task_queue.engine_registry,
        "ensure_loaded",
        lambda _engine_id: (_ for _ in ()).throw(AssertionError("adapter must run before the legacy loader")),
    )
    monkeypatch.setattr(
        task_queue.engine_registry,
        "run_isolated",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("adapter must run before legacy runner")),
    )

    request = GenerateRequest(
        text="雨夜里先响起一声钟，然后逐渐安静。",
        engine_id=SEED_ENGINE_ID,
        input_mode="text",
        engine_parameters={
            "format": "mp3",
            "sample_rate": 48000,
            "enable_subtitle": True,
            "speech_rate": 10,
            "loudness_rate": 5,
            "pitch_rate": -1,
        },
    )
    task = GenerationTask(
        task_id="seed-task-1",
        engine_id=SEED_ENGINE_ID,
        input_text=request.text,
        status=TaskStatus.queued,
        parameters=request.model_dump(),
    )

    await task_queue._process(task)

    assert task.status == TaskStatus.success
    assert task.provider_request_id
    assert task.provider_log_id == "seed-log-1"
    assert task.result_duration_ms == 1250
    assert task.original_duration_ms == 1100
    assert task.subtitle == {"text": "雨夜", "sentences": []}
    assert task.response_source == "base64"
    assert task.provider_state_uncertain is False
    assert task.result_id
    history = history_store.get(task.result_id)
    assert history is not None
    assert history.provider_request_id == task.provider_request_id
    assert history.provider_log_id == "seed-log-1"
    assert history.original_duration_ms == 1100
    assert history.subtitle == task.subtitle
    assert history.response_source == "base64"
    assert history.parameter_snapshot["input_mode"] == "text"
    assert history.parameter_snapshot["input_assets"] == []
    assert history.parameter_snapshot["engine_parameters"]["format"] == "mp3"
    persisted_task = task_queue.get_task(task.task_id)
    assert persisted_task is not None
    assert persisted_task.provider_request_id == task.provider_request_id
    assert persisted_task.provider_log_id == "seed-log-1"
    output = Path(history.output_path or "")
    assert output == tmp_path / "outputs" / "seed-task-1.mp3"
    assert output.read_bytes() == VALID_MP3

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://openspeech.bytedance.com/api/v3/tts/create"
    assert call["headers"]["X-Api-Key"] == "mock-seed-key"
    assert call["headers"]["X-Api-Request-Id"] == task.provider_request_id
    assert call["json_body"] == {
        "model": "seed-audio-1.0",
        "text_prompt": request.text,
        "audio_config": {
            "format": "mp3",
            "sample_rate": 48000,
            "speech_rate": 10,
            "loudness_rate": 5,
            "pitch_rate": -1,
            "enable_subtitle": True,
        },
    }


@pytest.mark.asyncio
async def test_seed_audio_non_single_submit_is_rejected_before_queue_start(monkeypatch):
    monkeypatch.setattr(
        task_queue,
        "start_worker",
        lambda: (_ for _ in ()).throw(AssertionError("worker must not start")),
    )
    request = GenerateRequest(text="测试", engine_id=SEED_ENGINE_ID, input_mode="text")
    with pytest.raises(Exception, match="Seed Audio 1.0 暂只支持单次生成"):
        await task_queue.submit(request, task_type="segment")


def test_started_seed_audio_task_is_not_replayed_after_restart(tmp_path: Path):
    _configure_test_storage(tmp_path)
    task = GenerationTask(
        task_id="seed-inflight-1",
        engine_id=SEED_ENGINE_ID,
        input_text="测试",
        status=TaskStatus.running,
        progress=0.5,
        parameters=GenerateRequest(text="测试", engine_id=SEED_ENGINE_ID, input_mode="text").model_dump(),
    )
    database.upsert("tasks", task.task_id, task.model_dump())

    recovered = task_queue._recover_incomplete_tasks()

    assert recovered == []
    persisted = task_queue.get_task(task.task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.failed
    assert "不会自动重放" in (persisted.error_message or "")


def _managed_file(file_id: str, path: Path):
    return SimpleNamespace(file_id=file_id, path=str(path), original_name=path.name)


@pytest.mark.asyncio
async def test_managed_audio_assets_preserve_order_and_history_contains_only_summaries(
    tmp_path: Path,
    monkeypatch,
):
    _configure_test_storage(tmp_path)
    managed = tmp_path / "voices"
    managed.mkdir(exist_ok=True)
    library_audio = managed / "library.wav"
    upload_audio = managed / "upload.mp3"
    library_audio.write_bytes(b"private-library-audio")
    upload_audio.write_bytes(b"private-upload-audio")
    files = {
        "file-library": _managed_file("file-library", library_audio),
        "file-upload": _managed_file("file-upload", upload_audio),
    }
    voice = SimpleNamespace(
        voice_id="voice-1",
        license_status="self_voice",
        reference_audio_ids=["file-library"],
    )
    resolver = SeedAudioAssetResolver(
        get_voice={"voice-1": voice}.get,
        get_file=files.get,
        managed_roots=lambda: (managed,),
        audio_duration_probe=lambda _path: 2.0,
    )
    monkeypatch.setattr(task_queue, "_seed_audio_asset_resolver", resolver)
    calls: list[dict] = []

    def mock_transport(**kwargs):
        calls.append(kwargs)
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={"X-Tt-Logid": "asset-log"},
            body={"code": 0, "audio": base64.b64encode(VALID_WAV).decode(), "duration": 1.0},
        )

    monkeypatch.setattr(task_queue, "_seed_audio_transport", mock_transport)
    request = GenerateRequest(
        text="让@音频1、@音频2和@音频3依次说话。",
        engine_id=SEED_ENGINE_ID,
        input_mode="audio",
        input_assets=[
            EngineInputAsset(
                asset_id="asset-library",
                type="audio",
                source="voice_library",
                voice_id="voice-1",
                file_id="file-library",
            ),
            EngineInputAsset(
                asset_id="asset-upload",
                type="audio",
                source="upload",
                file_id="file-upload",
                license_status="self_voice",
            ),
            EngineInputAsset(
                asset_id="asset-speaker",
                type="speaker",
                source="cloud_speaker",
                speaker_id="speaker-cloud-1",
                display_name="云端声音",
            ),
        ],
        engine_parameters={"confirm_upload": True},
    )
    task = GenerationTask(
        task_id="seed-assets-1",
        engine_id=SEED_ENGINE_ID,
        input_text=request.text,
        status=TaskStatus.queued,
        parameters=request.model_dump(),
    )

    await task_queue._process(task)

    assert task.status == TaskStatus.success
    references = calls[0]["json_body"]["references"]
    assert list(references[0]) == ["audio_data"]
    assert list(references[1]) == ["audio_data"]
    assert references[2] == {"speaker": "speaker-cloud-1"}
    assert base64.b64decode(references[0]["audio_data"]) == b"private-library-audio"
    assert base64.b64decode(references[1]["audio_data"]) == b"private-upload-audio"

    history = history_store.get(task.result_id or "")
    assert history is not None
    summaries = history.parameter_snapshot["asset_summaries"]
    assert [summary["asset_id"] for summary in summaries] == [
        "asset-library",
        "asset-upload",
        "asset-speaker",
    ]
    assert [summary["reference_token"] for summary in summaries] == ["@音频1", "@音频2", "@音频3"]
    serialized = json.dumps(summaries, ensure_ascii=False)
    assert "private-library-audio" not in serialized
    assert "private-upload-audio" not in serialized
    assert str(tmp_path) not in serialized
    assert "audio_data" not in serialized
    assert "image_data" not in serialized
    persisted_task = task_queue.get_task(task.task_id)
    assert persisted_task is not None
    assert persisted_task.parameters["asset_summaries"] == summaries
    persisted_serialized = json.dumps(persisted_task.model_dump(mode="json"), ensure_ascii=False)
    assert "private-library-audio" not in persisted_serialized
    assert "private-upload-audio" not in persisted_serialized
    assert str(tmp_path) not in persisted_serialized


def test_preset_image_is_resolved_by_managed_id_and_mode_mismatch_is_rejected(tmp_path: Path):
    managed = tmp_path / "seed-assets"
    managed.mkdir()
    image = managed / "preset.webp"
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(10, 20, 30)).save(output, format="WEBP")
    preset_image = output.getvalue()
    image.write_bytes(preset_image)
    resolver = SeedAudioAssetResolver(
        get_voice=lambda _voice_id: None,
        get_file={"image-1": _managed_file("image-1", image)}.get,
        managed_roots=lambda: (managed,),
        audio_duration_probe=lambda _path: 1.0,
    )
    adapter = SeedAudioAdapter()
    request = GenerateRequest(
        text="画面中的人物轻声说：你好。",
        engine_id=SEED_ENGINE_ID,
        input_mode="image",
        input_assets=[
            EngineInputAsset(
                asset_id="preset-image",
                type="image",
                source="preset",
                file_id="image-1",
                license_status="authorized",
            )
        ],
        engine_parameters={"confirm_upload": True},
    )

    seed_request, summaries = adapter.resolve_generate_request(
        request,
        asset_resolver=resolver,
        upload_confirmation_required=True,
    )
    payload = adapter.build_payload(seed_request)
    assert base64.b64decode(payload["references"][0]["image_data"]) == preset_image
    assert summaries[0]["source"] == "preset"
    assert summaries[0]["reference_token"] is None
    assert "path" not in summaries[0]

    mismatch = request.model_copy(update={"input_mode": "audio"})
    with pytest.raises(Exception, match="参考声音模式只能包含音频参考"):
        adapter.resolve_generate_request(
            mismatch,
            asset_resolver=resolver,
            upload_confirmation_required=True,
        )


@pytest.mark.parametrize(
    ("confirm_upload", "license_status", "message"),
    [
        (False, "self_voice", "确认上传"),
        (True, "unknown", "未授权上传"),
    ],
)
def test_upload_assets_require_confirmation_and_allowed_license(
    tmp_path: Path,
    confirm_upload: bool,
    license_status: str,
    message: str,
):
    managed = tmp_path / "uploads"
    managed.mkdir()
    audio = managed / "upload.wav"
    audio.write_bytes(b"private")
    resolver = SeedAudioAssetResolver(
        get_voice=lambda _voice_id: None,
        get_file={"audio-1": _managed_file("audio-1", audio)}.get,
        managed_roots=lambda: (managed,),
        audio_duration_probe=lambda _path: 1.0,
    )
    request = GenerateRequest(
        text="让@音频1说话。",
        engine_id=SEED_ENGINE_ID,
        input_mode="audio",
        input_assets=[
            EngineInputAsset(
                asset_id="upload-audio",
                type="audio",
                source="upload",
                file_id="audio-1",
                license_status=license_status,
            )
        ],
        engine_parameters={"confirm_upload": confirm_upload},
    )

    with pytest.raises(SeedAudioAssetError, match=message):
        SeedAudioAdapter().resolve_generate_request(
            request,
            asset_resolver=resolver,
            upload_confirmation_required=True,
        )


def test_upload_confirmation_can_be_disabled_by_existing_doubao_setting(tmp_path: Path):
    managed = tmp_path / "uploads"
    managed.mkdir()
    audio = managed / "upload.wav"
    audio.write_bytes(b"private")
    resolver = SeedAudioAssetResolver(
        get_voice=lambda _voice_id: None,
        get_file={"audio-1": _managed_file("audio-1", audio)}.get,
        managed_roots=lambda: (managed,),
        audio_duration_probe=lambda _path: 1.0,
    )
    request = GenerateRequest(
        text="让@音频1说话。",
        engine_id=SEED_ENGINE_ID,
        input_mode="audio",
        input_assets=[
            EngineInputAsset(
                asset_id="upload-audio",
                type="audio",
                source="upload",
                file_id="audio-1",
                license_status="self_voice",
            )
        ],
        engine_parameters={"confirm_upload": False},
    )

    seed_request, summaries = SeedAudioAdapter().resolve_generate_request(
        request,
        asset_resolver=resolver,
        upload_confirmation_required=False,
    )

    assert len(seed_request.references) == 1
    assert summaries[0]["file_id"] == "audio-1"


@pytest.mark.asyncio
async def test_seed_retry_requires_confirmation_for_active_cancelled_or_uncertain_tasks(
    tmp_path: Path,
    monkeypatch,
):
    _configure_test_storage(tmp_path)
    parameters = GenerateRequest(text="测试", engine_id=SEED_ENGINE_ID, input_mode="text").model_dump()
    running = GenerationTask(
        task_id="seed-running",
        engine_id=SEED_ENGINE_ID,
        input_text="测试",
        status=TaskStatus.running,
        parameters=parameters,
    )
    database.upsert("tasks", running.task_id, running.model_dump())
    with pytest.raises(AppException) as active_exc:
        await task_queue.retry_task(running.task_id, confirm_cloud_replay=True)
    assert active_exc.value.code == "TASK_ACTIVE"

    cancelled = GenerationTask(
        task_id="seed-cancelled",
        engine_id=SEED_ENGINE_ID,
        input_text="测试",
        status=TaskStatus.cancelled,
        provider_request_id="request-old",
        provider_state_uncertain=True,
        parameters=parameters,
    )
    database.upsert("tasks", cancelled.task_id, cancelled.model_dump())
    with pytest.raises(AppException) as replay_exc:
        await task_queue.retry_task(cancelled.task_id)
    assert replay_exc.value.code == "CLOUD_REPLAY_CONFIRM_REQUIRED"

    async def fake_submit(req, *_args, **_kwargs):
        assert req.engine_id == SEED_ENGINE_ID
        return "seed-confirmed-retry"

    monkeypatch.setattr(task_queue, "submit", fake_submit)
    assert (
        await task_queue.retry_task(cancelled.task_id, confirm_cloud_replay=True)
        == "seed-confirmed-retry"
    )


def test_retry_api_returns_understandable_cloud_confirmation_error(tmp_path: Path):
    _configure_test_storage(tmp_path)
    task = GenerationTask(
        task_id="seed-api-retry",
        engine_id=SEED_ENGINE_ID,
        input_text="测试",
        status=TaskStatus.cancelled,
        provider_request_id="request-old",
        provider_state_uncertain=True,
        parameters=GenerateRequest(text="测试", engine_id=SEED_ENGINE_ID, input_mode="text").model_dump(),
    )
    database.upsert("tasks", task.task_id, task.model_dump())

    response = TestClient(app).post(f"/api/tasks/{task.task_id}/retry")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLOUD_REPLAY_CONFIRM_REQUIRED"


@pytest.mark.asyncio
async def test_cancelled_seed_request_cleans_downloaded_orphan(tmp_path: Path, monkeypatch):
    _configure_test_storage(tmp_path)
    request = GenerateRequest(text="测试", engine_id=SEED_ENGINE_ID, input_mode="text")
    task = GenerationTask(
        task_id="seed-cancel-output",
        engine_id=SEED_ENGINE_ID,
        input_text=request.text,
        status=TaskStatus.queued,
        parameters=request.model_dump(),
    )

    def transport(**_):
        task_queue.cancel_task(task.task_id)
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={"code": 0, "audio": base64.b64encode(VALID_WAV).decode()},
        )

    monkeypatch.setattr(task_queue, "_seed_audio_transport", transport)
    await task_queue._process(task)

    persisted = task_queue.get_task(task.task_id)
    assert persisted is not None and persisted.status == TaskStatus.cancelled
    assert persisted.provider_state_uncertain is True
    assert not (tmp_path / "outputs" / "seed-cancel-output.wav").exists()
    task_queue._cancelled.discard(task.task_id)


@pytest.mark.asyncio
async def test_history_failure_never_leaves_success_or_output(tmp_path: Path, monkeypatch):
    _configure_test_storage(tmp_path)
    request = GenerateRequest(text="测试", engine_id=SEED_ENGINE_ID, input_mode="text")
    task = GenerationTask(
        task_id="seed-history-fail",
        engine_id=SEED_ENGINE_ID,
        input_text=request.text,
        status=TaskStatus.queued,
        parameters=request.model_dump(),
    )
    monkeypatch.setattr(
        task_queue,
        "_seed_audio_transport",
        lambda **_: SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={"code": 0, "audio": base64.b64encode(VALID_WAV).decode()},
        ),
    )
    monkeypatch.setattr(history_store, "add", lambda _item: (_ for _ in ()).throw(RuntimeError("history down")))

    await task_queue._process(task)

    persisted = task_queue.get_task(task.task_id)
    assert persisted is not None and persisted.status == TaskStatus.failed
    assert persisted.result_id is None
    assert not (tmp_path / "outputs" / "seed-history-fail.wav").exists()


@pytest.mark.asyncio
async def test_success_persist_failure_removes_history_and_output(tmp_path: Path, monkeypatch):
    _configure_test_storage(tmp_path)
    request = GenerateRequest(text="测试", engine_id=SEED_ENGINE_ID, input_mode="text")
    task = GenerationTask(
        task_id="seed-success-persist-fail",
        engine_id=SEED_ENGINE_ID,
        input_text=request.text,
        status=TaskStatus.queued,
        parameters=request.model_dump(),
    )
    monkeypatch.setattr(
        task_queue,
        "_seed_audio_transport",
        lambda **_: SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={"code": 0, "audio": base64.b64encode(VALID_WAV).decode()},
        ),
    )
    original_update = task_queue._update_status
    failed_once = False

    async def fail_success_update(current, **kwargs):
        nonlocal failed_once
        if kwargs.get("status") == TaskStatus.success and not failed_once:
            failed_once = True
            raise RuntimeError("task save down")
        return await original_update(current, **kwargs)

    monkeypatch.setattr(task_queue, "_update_status", fail_success_update)
    await task_queue._process(task)

    persisted = task_queue.get_task(task.task_id)
    assert persisted is not None and persisted.status == TaskStatus.failed
    assert persisted.result_id is None
    assert history_store.list_history() == []
    assert not (tmp_path / "outputs" / "seed-success-persist-fail.wav").exists()
