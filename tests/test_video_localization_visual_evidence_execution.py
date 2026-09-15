from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domains.video_localization import (
    asr_visual_evidence_execution as execution,
    asr_visual_evidence_managed_contracts as managed_contracts,
    asr_visual_evidence_result_reader as result_reader,
    managed_artifact_files,
    visual_evidence,
)
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.voice_studio import AppSettings
from app.services import database, llm_runtime, settings_store
from app.services import (
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


T0 = datetime.now(timezone.utc)
JPEG = b"\xff\xd8\xff\xe0managed-visual-frame\xff\xd9"


def _claim(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            voice_dir=str(tmp_path / "voices"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    with database.conn() as connection:
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
            ) VALUES (
                'project-1',
                'operation-1',
                'english_asr',
                'running',
                'parameters-v1',
                ?,
                'command',
                ?,
                ?
            )
            """,
            (
                ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
                T0.isoformat(),
                T0.isoformat(),
            ),
        )
    claimed = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(minutes=10),
    )
    assert claimed.attempt.execution_fence is not None
    return claimed.attempt.execution_fence


def _profile() -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="vision-profile",
        protocol="openai_compatible",
        base_url="http://127.0.0.1:9999/v1",
        model_id="vision-model",
        api_key=None,
    )


def _request(*, questions: bool = True):
    return visual_evidence.AsrVisualEvidenceInput(
        upstream_operation_id="document-operation",
        video_sha256="a" * 64,
        video_duration_ms=10_000,
        profile_id="vision-profile" if questions else None,
        document_summary="Summary",
        segments=[
            visual_evidence.AsrVisualEvidenceSegment(
                ordinal=1,
                segment_id="segment-1",
                start_ms=0,
                end_ms=2_000,
                text="hello",
            )
        ],
        questions=(
            [
                visual_evidence.AsrVisualEvidenceQuestion(
                    question_id="visual_01",
                    start_ordinal=1,
                    end_ordinal=1,
                    start_segment_id="segment-1",
                    end_segment_id="segment-1",
                    start_ms=0,
                    end_ms=2_000,
                    kind="visible_text",
                    reason="Read title",
                    question="What is visible?",
                )
            ]
            if questions
            else []
        ),
    )


def _prepared(*, questions: bool = True):
    profile = _profile() if questions else None
    return managed_contracts.AsrVisualEvidencePreparedInputV2(
        upstream_artifact_fingerprint="c" * 64,
        profile_configuration_fingerprint=(
            provider_execution.provider_configuration_fingerprint(
                profile
            )
            if profile is not None
            else None
        ),
        behavior_fingerprint=(
            execution.visual_evidence_behavior_fingerprint()
        ),
        request=_request(questions=questions),
    )


def test_prepared_visual_workflow_replays_frames_calls_and_final(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    frame_dir = tmp_path / "runtime"
    frame_dir.mkdir()
    extraction_calls = 0
    provider_calls = 0

    def extract(request):
        nonlocal extraction_calls
        extraction_calls += 1
        destination = request.frame_root / "frame.jpg"
        destination.write_bytes(JPEG)
        return visual_evidence.VisualEvidenceExtractionBatch(
            frames=(
                visual_evidence.AsrVisualEvidenceFrame(
                    frame_id="frame-a",
                    question_id=request.question_id,
                    frame_index=request.start_frame_index,
                    round_index=request.round_index,
                    timestamp_ms=request.timestamps[0],
                    file_name=destination.name,
                    sha256=hashlib.sha256(JPEG).hexdigest(),
                    size_bytes=len(JPEG),
                ),
            ),
            warnings=(),
            attempted_timestamps=(request.timestamps[0],),
        )

    def complete(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="vision-profile",
                model_id="vision-model",
                provider_host="127.0.0.1",
                request_chars=100,
                request_body_bytes=150,
                max_tokens=1_600,
                timeout_seconds=120,
                reasoning_effort_requested=None,
                reasoning_control_applied=True,
                duration_ms=25,
                finish_reason="stop",
                content_chars=40,
                reasoning_chars=0,
                response_id="response-1",
            )
        )
        return {
            "answer": "画面中显示标题。",
            "visible_text": ["Title"],
            "search_terms": [],
            "relevant_frame_indexes": [1],
            "confidence": 0.9,
            "needs_web_search": False,
            "needs_more_frames": False,
            "more_frames_reason": "",
            "limitations": [],
        }

    first = execution.execute_prepared_visual_evidence(
        _prepared(),
        execution_fence=fence,
        source_video_path=source,
        frame_dir=frame_dir,
        resolved_profile=_profile(),
        is_cancelled=lambda: False,
        extractor=extract,
        complete_multimodal_json=complete,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    (frame_dir / "frame.jpg").unlink(missing_ok=True)
    replay = execution.execute_prepared_visual_evidence(
        _prepared(),
        execution_fence=fence,
        source_video_path=source,
        frame_dir=frame_dir,
        resolved_profile=_profile(),
        is_cancelled=lambda: False,
        extractor=extract,
        complete_multimodal_json=complete,
        clock=lambda: T0 + timedelta(seconds=2),
    )

    assert first == replay
    assert first.status == "completed"
    assert first.stage_timing.duration_ms == 25
    assert extraction_calls == 1
    assert provider_calls == 1
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert {item.step_id for item in steps} == {
        "prepare_visual_input",
        "extract_visual_visual_01_r1",
        "visual_call_visual_01_r1",
        "finalize_visual_evidence",
    }
    assert all(item.status == "success" for item in steps)
    assert not (frame_dir / "manifest.json").exists()
    final_step = next(
        item
        for item in steps
        if item.step_id == "finalize_visual_evidence"
    )
    final_artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        final_step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert final_artifact is not None
    final = managed_contracts.parse_step_output(
        artifact_store.read_artifact(
            final_artifact.artifact_id,
            file_backend=managed_artifact_files,
        ).content
    )
    assert len(final.frames) == 1
    assert len(final.calls) == 1
    assert len(final.extractions) == 1
    with database.read_conn() as connection:
        authority = result_reader.read_success_from_connection(
            connection,
            "project-1",
            "operation-1",
            file_backend=managed_artifact_files,
        )
    assert authority is not None
    assert authority.result == first
    assert authority.final_artifact_fingerprint == (
        final_artifact.content_fingerprint
    )


def test_no_questions_uses_no_video_profile_or_provider(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)

    result = execution.execute_prepared_visual_evidence(
        _prepared(questions=False),
        execution_fence=fence,
        source_video_path=tmp_path / "missing.mp4",
        frame_dir=tmp_path / "unused",
        resolved_profile=None,
        is_cancelled=lambda: False,
        extractor=lambda _request: (_ for _ in ()).throw(
            AssertionError("extractor must not run")
        ),
        complete_multimodal_json=lambda **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("Provider must not run")
            )
        ),
    )

    assert result.status == "not_needed"
    assert result.stop_reason == "no_questions"
    assert {
        item.step_id
        for item in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
    } == {
        "prepare_visual_input",
        "finalize_visual_evidence",
    }


def test_known_provider_failure_finalizes_partial_result_without_call_ref(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    def extract(request):
        destination = request.frame_root / "frame.jpg"
        destination.write_bytes(JPEG)
        return visual_evidence.VisualEvidenceExtractionBatch(
            frames=(
                visual_evidence.AsrVisualEvidenceFrame(
                    frame_id="frame-a",
                    question_id=request.question_id,
                    frame_index=request.start_frame_index,
                    round_index=request.round_index,
                    timestamp_ms=request.timestamps[0],
                    file_name=destination.name,
                    sha256=hashlib.sha256(JPEG).hexdigest(),
                    size_bytes=len(JPEG),
                ),
            ),
            warnings=(),
            attempted_timestamps=(request.timestamps[0],),
        )

    def complete(**kwargs):
        kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="vision-profile",
                model_id="vision-model",
                provider_host="127.0.0.1",
                request_chars=100,
                request_body_bytes=150,
                max_tokens=1_600,
                timeout_seconds=120,
                reasoning_effort_requested=None,
                reasoning_control_applied=True,
                duration_ms=10,
                finish_reason=None,
                content_chars=0,
                reasoning_chars=0,
                response_id="response-rejected",
                error_code="llm_http_400",
            )
        )
        raise llm_runtime.LlmRuntimeError(
            "request rejected",
            code="llm_http_400",
            status_code=400,
        )

    result = execution.execute_prepared_visual_evidence(
        _prepared(),
        execution_fence=fence,
        source_video_path=source,
        frame_dir=tmp_path / "runtime",
        resolved_profile=_profile(),
        is_cancelled=lambda: False,
        extractor=extract,
        complete_multimodal_json=complete,
        clock=lambda: T0 + timedelta(seconds=1),
    )

    assert result.status == "partial"
    assert result.quality_summary.model_call_count == 0
    assert result.observations[0].error_code == "llm_http_400"
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert {
        item.step_id: item.status for item in steps
    } == {
        "prepare_visual_input": "success",
        "extract_visual_visual_01_r1": "success",
        "visual_call_visual_01_r1": "failed",
        "finalize_visual_evidence": "success",
    }
    final_step = next(
        item
        for item in steps
        if item.step_id == "finalize_visual_evidence"
    )
    final_artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        final_step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert final_artifact is not None
    final = managed_contracts.parse_step_output(
        artifact_store.read_artifact(
            final_artifact.artifact_id,
            file_backend=managed_artifact_files,
        ).content
    )
    assert final.calls == ()
    with database.read_conn() as connection:
        authority = result_reader.read_success_from_connection(
            connection,
            "project-1",
            "operation-1",
            file_backend=managed_artifact_files,
        )
    assert authority is not None
    assert authority.result == result
