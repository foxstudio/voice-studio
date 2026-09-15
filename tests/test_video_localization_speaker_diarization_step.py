from __future__ import annotations

import hashlib
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    operation_queue,
    operation_detail_reconciliation,
    media_assets,
    service,
    source_pipeline,
    speaker_diarization,
    speaker_diarization_execution,
    speaker_diarization_operation_projection,
    transcription,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, ProjectCreate  # noqa: E402
from app.schemas.video_localization_speaker_diarization_step import (  # noqa: E402
    speaker_diarization_step_output_bytes,
    speaker_diarization_step_output_from_payload,
)
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
    video_localization_operation_execution as operation_execution,
    video_localization_operation_artifact_store as artifact_store,
    video_localization_operation_step_store as step_store,
)


def _configure(tmp_path: Path, *, enabled: bool) -> None:
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
            video_localization_development_step_control_enabled=enabled,
        )
    )


def _project_with_existing_asr(tmp_path: Path) -> str:
    project = project_store.create_project(ProjectCreate(name="说话人区分开发单步"))
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"fake-wav")
    draft = VideoLocalizationDraft(
        source_media={
            "filename": "demo.mp4",
            "audio_path": str(audio_path),
            "audio_sha256": "audio-sha256",
            "duration_ms": 5000,
            "metadata": {"english_asr_status": "completed"},
        },
        transcription=VideoLocalizationTranscriptionState(
            raw_text="Existing transcript",
            corrected_text="Existing transcript",
        ),
        cues=[
            VideoLocalizationCue(
                cue_id="existing-cue",
                start_ms=0,
                end_ms=1000,
                en_subtitle_text="Existing subtitle",
            )
        ],
    )
    service.save_video_localization(project.project_id, draft)
    return project.project_id


def _diarization_output(
    audio_path: Path,
    *,
    request: speaker_diarization.DiarizeSpeakersInput | None = None,
) -> speaker_diarization.DiarizeSpeakersOutput:
    cluster = VideoLocalizationSpeakerCluster(
        cluster_id="cluster_01",
        source_label="S01",
        source_engine_id="moss-transcribe-diarize-mlx",
        start_ms=0,
        end_ms=5000,
        duration_ms=5000,
        segment_count=1,
    )
    return speaker_diarization.DiarizeSpeakersOutput(
        input=request
        or speaker_diarization.DiarizeSpeakersInput(
            audio_path=str(audio_path),
            audio_sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest(),
            source_track_id="original",
            engine_id="moss-transcribe-diarize-mlx",
            duration_ms=5000,
            max_speakers=2,
        ),
        status="completed",
        engine_id="moss-transcribe-diarize-mlx",
        model_id="vanch007/mlx-MOSS-Transcribe-Diarize-8bit",
        segments=[
            speaker_diarization.SpeakerDiarizationSegment(
                start_ms=0,
                end_ms=5000,
                speaker_cluster_id="cluster_01",
                source_speaker_label="S01",
            )
        ],
        clusters=[cluster],
        count_guidance=speaker_diarization.SpeakerCountGuidanceResult(
            requested_min_speakers=None,
            requested_max_speakers=2,
            detected_speaker_count=1,
            engine_applied=False,
            usage="quality_check_only",
            evaluation="within_range",
        ),
        quality_summary=speaker_diarization.SpeakerDiarizationQualitySummary(
            status="passed",
            segment_count=1,
            cluster_count=1,
            overlap_segment_count=0,
            covered_duration_ms=5000,
            audio_duration_ms=5000,
            coverage_ratio=1.0,
        ),
        stage_timing={
            "duration_ms": 100,
            "segment_count": 1,
            "cluster_count": 1,
            "status": "completed",
        },
    )


def test_diarization_contract_treats_speaker_limit_as_quality_guidance(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"audio")
    engine_kwargs: dict[str, object] = {}

    def fake_engine(*_args, **kwargs):
        engine_kwargs.update(kwargs)
        return {
            "status": "completed",
            "engine_id": "moss-transcribe-diarize-mlx",
            "model_id": "model",
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 1200,
                    "speaker": "cluster_01",
                    "source_speaker": "S01",
                    "confidence": 0.9,
                },
                {
                    "start_ms": 1200,
                    "end_ms": 2500,
                    "speaker": "cluster_02",
                    "source_speaker": "S02",
                    "confidence": 0.8,
                },
            ],
            "clusters": [
                VideoLocalizationSpeakerCluster(
                    cluster_id=f"cluster_0{index}",
                    source_label=f"S0{index}",
                    source_engine_id="moss-transcribe-diarize-mlx",
                    start_ms=(index - 1) * 1200,
                    end_ms=index * 1200,
                    duration_ms=1200,
                    segment_count=1,
                )
                for index in (1, 2)
            ],
            "verification": {"status": "completed"},
            "quality_flags": [],
            "error": None,
        }

    monkeypatch.setattr(
        speaker_diarization.speaker_diarization_service,
        "diarize",
        fake_engine,
    )

    result = speaker_diarization.diarize_speakers(
        speaker_diarization.DiarizeSpeakersInput(
            audio_path=str(audio_path),
            audio_sha256="sha256",
            source_track_id="vocals",
            engine_id="auto",
            duration_ms=3000,
            max_speakers=1,
        )
    )

    assert result.contract_version == "speaker-diarization-v1"
    assert result.count_guidance.engine_applied is False
    assert result.count_guidance.usage == "quality_check_only"
    assert "min_speakers" not in engine_kwargs
    assert "max_speakers" not in engine_kwargs
    assert result.count_guidance.detected_speaker_count == 2
    assert result.count_guidance.evaluation == "above_maximum"
    assert result.quality_summary.status == "warning"
    assert "speaker_count_above_requested_maximum" in result.quality_summary.warning_codes


def test_diarization_contract_rejects_invalid_speaker_range():
    with pytest.raises(ValidationError):
        speaker_diarization.DiarizeSpeakersInput(
            audio_path="/tmp/source.wav",
            audio_sha256="sha256",
            source_track_id="original",
            engine_id="auto",
            min_speakers=3,
            max_speakers=2,
        )


def test_managed_diarization_contract_removes_runtime_paths(
    tmp_path: Path,
):
    audio_path = tmp_path / "private-source.wav"
    audio_path.write_bytes(b"audio")
    payload = _diarization_output(audio_path).model_dump(mode="json")
    payload["verification"] = {
        "status": "failed",
        "reason": "campplus_unavailable",
        "mapping": {"S01": "cluster_01"},
        "error": str(tmp_path / "private-model.bin"),
        "worker_metadata": {"runtime_path": str(tmp_path / "runtime")},
    }
    payload["error"] = str(tmp_path / "private-worker")

    output = speaker_diarization_step_output_from_payload(payload)
    encoded = speaker_diarization_step_output_bytes(output)

    assert str(tmp_path).encode() not in encoded
    assert not hasattr(output.result.input, "audio_path")
    assert output.result.error == "speaker_verification_failed"
    assert output.result.verification.reason == "campplus_unavailable"


def test_diarization_join_requires_the_same_audio_fingerprint(tmp_path: Path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"audio")
    result = _diarization_output(audio_path)
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0001",
            start_ms=0,
            end_ms=1000,
            raw_text="Hello.",
        )
    ]

    attached = speaker_diarization.attach_to_transcript_segments(
        segments,
        result,
        audio_sha256=result.input.audio_sha256,
        source_track_id="original",
    )
    assert attached[0].speaker_cluster_id == "cluster_01"

    with pytest.raises(AppException) as captured:
        speaker_diarization.attach_to_transcript_segments(
            segments,
            result,
            audio_sha256="different-audio",
            source_track_id="original",
        )
    assert captured.value.code == "VIDEO_LOCALIZATION_DIARIZATION_SOURCE_MISMATCH"


def test_full_initial_analysis_runs_raw_asr_and_diarization_concurrently(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"audio")
    started = threading.Barrier(2, timeout=2)
    raw_preview_published = threading.Event()
    calls: list[str] = []

    def fake_asr(request, **_kwargs):
        calls.append("asr")
        started.wait()
        return transcription.build_transcribe_raw_output(
            input=request,
            raw_text="Hello.",
            language="en",
            segments=[
                VideoLocalizationTranscriptSegment(
                    segment_id="asr_0001",
                    start_ms=0,
                    end_ms=1000,
                    raw_text="Hello.",
                )
            ],
        )

    def fake_diarization(request, **_kwargs):
        calls.append("diarization")
        started.wait()
        assert raw_preview_published.wait(timeout=2)
        return _diarization_output(Path(request.audio_path))

    monkeypatch.setattr(transcription, "transcribe_raw", fake_asr)
    monkeypatch.setattr(speaker_diarization, "diarize_speakers", fake_diarization)

    analysis = transcription.run_initial_speech_analysis(
        asr_request=transcription.TranscribeRawInput(
            audio_path=str(audio_path),
            audio_sha256="audio-sha256",
            engine_id="qwen3-asr-mlx",
            source_track_id="original",
            requested_language="auto",
            duration_ms=5000,
        ),
        diarization_request=speaker_diarization.DiarizeSpeakersInput(
            audio_path=str(audio_path),
            audio_sha256="audio-sha256",
            source_track_id="original",
            engine_id="auto",
            duration_ms=5000,
        ),
        on_raw_asr_complete=lambda result: (
            calls.append(f"preview:{result.raw_text}"),
            raw_preview_published.set(),
        ),
    )

    assert set(calls) == {"asr", "diarization", "preview:Hello."}
    assert analysis.raw_asr.raw_text == "Hello."
    assert analysis.diarization is not None
    assert analysis.diarization.count_guidance.detected_speaker_count == 1
    assert analysis.diarization_error is None


def test_standalone_diarization_is_gated_and_never_changes_formal_asr(tmp_path: Path, monkeypatch):
    _configure(tmp_path, enabled=False)
    project_id = _project_with_existing_asr(tmp_path)

    with pytest.raises(AppException) as captured:
        operation_queue.submit(
            project_id,
            "speaker_diarization",
            {"source_track_id": "original", "max_speakers": 2},
        )
    assert captured.value.code == "VIDEO_LOCALIZATION_DEVELOPMENT_STEP_CONTROL_DISABLED"

    _configure(tmp_path, enabled=True)
    before = service.get_video_localization(project_id)
    assert before is not None
    audio_path = Path(before.source_media.audio_path or "")
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _operation_id: None)
    monkeypatch.setattr(
        operation_queue.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_speaker_diarization",
        lambda request, **_kwargs: _diarization_output(
            Path(request.audio_path),
            request=request,
        ),
    )

    submitted = operation_queue.submit(
        project_id,
        "speaker_diarization",
        {"source_track_id": "original", "max_speakers": 2},
    )
    assert submitted is not None
    operation_queue._process(project_id, submitted.operation_id)

    completed = operation_queue.get_operation(project_id, submitted.operation_id)
    assert completed is not None
    assert completed.status == "success"
    assert completed.result_summary["execution_scope"] == "partial"
    assert completed.result_summary["speaker_count"] == 1
    assert completed.result_summary["workflow_schema_version"] == "speaker-diarization-workflow-v1"
    assert "artifact_path" not in completed.result_summary
    assert str(tmp_path) not in str(completed.model_dump(mode="json"))

    after = service.get_video_localization(project_id)
    assert after is not None
    assert after.transcription == before.transcription
    assert after.cues == before.cues
    assert after.speakers == before.speakers
    assert after.source_media.metadata == before.source_media.metadata

    response = TestClient(app).get(
        f"/api/projects/{project_id}/video-localization/operations/"
        f"{submitted.operation_id}/development-diarization-result"
    )
    assert response.status_code == 200
    assert response.json()["count_guidance"]["detected_speaker_count"] == 1
    assert "audio_path" not in response.json()["input"]
    detail = TestClient(app).get(f"/api/projects/{project_id}/video-localization/operations/{submitted.operation_id}")
    assert detail.status_code == 200
    assert detail.json()["parameters"]["max_speakers"] == 2
    assert detail.json()["result_summary"]["workflow_schema_version"] == "speaker-diarization-workflow-v1"
    audit = operation_detail_reconciliation.reconcile_operation_detail(
            project_id,
            submitted.operation_id,
        )
    assert audit.status == "matched"
    assert audit.checked_artifact_count == 1


def test_standalone_diarization_rejects_unknown_parameters(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path, enabled=True)
    project_id = _project_with_existing_asr(tmp_path)
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )

    with pytest.raises(AppException) as captured:
        operation_queue.submit(
            project_id,
            "speaker_diarization",
            {
                "source_track_id": "original",
                "unexpected": "silently ignored before",
            },
        )

    assert captured.value.code == "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_PARAMETERS_INVALID"


def test_legacy_diarization_retry_creates_clean_managed_workflow(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path, enabled=True)
    project_id = _project_with_existing_asr(tmp_path)
    draft = service.get_video_localization(project_id)
    assert draft is not None
    legacy = VideoLocalizationOperation(
        project_id=project_id,
        kind="speaker_diarization",
        status="failed",
        parameters={
            "engine_id": "auto",
            "source_track_id": "original",
            "max_speakers": 2,
            "obsolete_snapshot_option": True,
            "scope": {"legacy": True},
        },
        result_summary={
            "stage": "旧版说话人区分失败",
        },
        completed_at="2026-07-31T08:00:00+00:00",
    )
    service.save_video_localization(
        project_id,
        draft.model_copy(
            update={
                "operations": [
                    *draft.operations,
                    legacy,
                ]
            }
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )

    retried = operation_queue.retry(
        project_id,
        legacy.operation_id,
    )

    assert retried is not None
    assert retried.operation_id != legacy.operation_id
    assert retried.result_summary["workflow_schema_version"] == "speaker-diarization-workflow-v1"
    assert "obsolete_snapshot_option" not in retried.parameters
    assert retried.parameters["max_speakers"] == 2


def test_standalone_diarization_recovers_committed_artifact_without_rerun(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path, enabled=True)
    project_id = _project_with_existing_asr(tmp_path)
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )
    submitted = operation_queue.submit(
        project_id,
        "speaker_diarization",
        {
            "source_track_id": "original",
            "max_speakers": 2,
        },
    )
    assert submitted is not None
    operation_queue._mark_operation(
        project_id,
        submitted.operation_id,
        kind=None,
        status="running",
        started_at="2026-07-31T08:00:00+00:00",
        result_summary=(speaker_diarization_operation_projection.running_summary()),
    )
    decision = operation_execution.acquire_execution_claim(
        project_id,
        submitted.operation_id,
        runner_id="crashed-diarization-worker",
    )
    assert decision.acquired and decision.claim is not None
    claim = decision.claim
    draft = service.get_video_localization(project_id)
    assert draft is not None
    with source_pipeline.prepared_speaker_diarization_request(
        draft,
        engine_id="auto",
        source_track_id="original",
        max_speakers=2,
    ) as request:
        handle = speaker_diarization_execution.prepare_speaker_diarization_step(
                claim.execution_fence,
                request,
            )
        (
            speaker_diarization_execution.complete_speaker_diarization_step(
                handle,
                _diarization_output(
                    Path(request.audio_path),
                    request=request,
                ).model_dump(mode="json"),
            )
        )
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET lease_expires_at_ms = 0
            WHERE attempt_id = ?
            """,
            (claim.attempt.attempt_id,),
        )
    monkeypatch.setattr(
        operation_queue.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_speaker_diarization",
        lambda *_args, **_kwargs: pytest.fail("committed diarization must not rerun inference"),
    )

    operation_queue._recover_project_operations(project_id)

    completed = operation_queue.get_operation(
        project_id,
        submitted.operation_id,
    )
    assert completed is not None
    assert completed.status == "success"
    result = operation_queue.get_development_diarization_result(
        project_id,
        submitted.operation_id,
    )
    assert result is not None
    assert result.count_guidance.detected_speaker_count == 1


def test_standalone_diarization_corruption_fails_closed(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path, enabled=True)
    project_id = _project_with_existing_asr(tmp_path)
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )
    monkeypatch.setattr(
        operation_queue.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_speaker_diarization",
        lambda request, **_kwargs: _diarization_output(
            Path(request.audio_path),
            request=request,
        ),
    )
    submitted = operation_queue.submit(
        project_id,
        "speaker_diarization",
        {"source_track_id": "original"},
    )
    assert submitted is not None
    operation_queue._process(
        project_id,
        submitted.operation_id,
    )
    step = step_store.list_step_attempts(
        project_id,
        submitted.operation_id,
    )[0]
    artifact = artifact_store.get_step_artifact(
        project_id,
        submitted.operation_id,
        step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    path = media_assets.project_video_localization_dir(project_id) / artifact.storage_key
    path.write_bytes(b'{"tampered":true}')

    client = TestClient(app)
    detail = client.get(f"/api/projects/{project_id}/video-localization/operations/{submitted.operation_id}")
    assert detail.status_code == 409
    assert detail.json()["error"]["code"] == "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED"
    result = client.get(
        f"/api/projects/{project_id}/video-localization/operations/"
        f"{submitted.operation_id}/development-diarization-result"
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED"
    audit = operation_detail_reconciliation.reconcile_operation_detail(
            project_id,
            submitted.operation_id,
        )
    assert not audit.matched
    assert audit.issues


def test_standalone_diarization_recovery_rejects_changed_audio(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path, enabled=True)
    project_id = _project_with_existing_asr(tmp_path)
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )
    submitted = operation_queue.submit(
        project_id,
        "speaker_diarization",
        {"source_track_id": "original"},
    )
    assert submitted is not None
    operation_queue._mark_operation(
        project_id,
        submitted.operation_id,
        kind=None,
        status="running",
        started_at="2026-07-31T08:00:00+00:00",
        result_summary=(speaker_diarization_operation_projection.running_summary()),
    )
    decision = operation_execution.acquire_execution_claim(
        project_id,
        submitted.operation_id,
        runner_id="changed-input-worker",
    )
    assert decision.acquired and decision.claim is not None
    claim = decision.claim
    draft = service.get_video_localization(project_id)
    assert draft is not None
    with source_pipeline.prepared_speaker_diarization_request(
        draft,
        source_track_id="original",
    ) as request:
        (
            speaker_diarization_execution.prepare_speaker_diarization_step(
                claim.execution_fence,
                request,
            )
        )
        Path(request.audio_path).write_bytes(b"changed-source-audio")
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET lease_expires_at_ms = 0
            WHERE attempt_id = ?
            """,
            (claim.attempt.attempt_id,),
        )
    monkeypatch.setattr(
        operation_queue.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_speaker_diarization",
        lambda *_args, **_kwargs: pytest.fail("changed input must fail before inference"),
    )

    operation_queue._recover_project_operations(project_id)

    failed = operation_queue.get_operation(
        project_id,
        submitted.operation_id,
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_INPUT_CHANGED"


def test_standalone_diarization_artifact_failure_never_marks_success(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path, enabled=True)
    project_id = _project_with_existing_asr(tmp_path)
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )
    monkeypatch.setattr(
        operation_queue.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_speaker_diarization",
        lambda request, **_kwargs: _diarization_output(
            Path(request.audio_path),
            request=request,
        ),
    )

    def fail_artifact_write(*_args, **_kwargs):
        raise RuntimeError("injected artifact failure")

    monkeypatch.setattr(
        artifact_store,
        "stage_artifact",
        fail_artifact_write,
    )
    submitted = operation_queue.submit(
        project_id,
        "speaker_diarization",
        {"source_track_id": "original"},
    )
    assert submitted is not None

    operation_queue._process(
        project_id,
        submitted.operation_id,
    )

    failed = operation_queue.get_operation(
        project_id,
        submitted.operation_id,
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == ("VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_RESULT_WRITE_FAILED")
    failed_step = step_store.list_step_attempts(
        project_id,
        submitted.operation_id,
    )[0]
    assert (
        artifact_store.get_step_artifact(
            project_id,
            submitted.operation_id,
            failed_step.step_attempt_id,
            artifact_kind="step-result",
            artifact_key="primary",
        )
        is None
    )
