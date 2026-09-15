from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_initial_analysis_execution,
    asr_initial_analysis_operation_projection,
    asr_development_replay,
    asr_pipeline,
    asr_raw_execution,
    asr_raw_operation_projection,
    development_checkpoints,
    document_understanding_contracts,
    entity_normalization,
    media_assets,
    operation_detail_reader,
    operation_queue,
    operation_detail_reconciliation,
    research_evidence,
    review_decisions,
    section_review,
    service,
    source_pipeline,
    speaker_diarization,
    transcription,
    visual_evidence,
    whole_recheck,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationGlossaryEntry,
    VideoLocalizationOperation,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.api.video_localization import (  # noqa: E402
    VideoLocalizationAsrOperationRequest,
)
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, ProjectCreate  # noqa: E402
from app.schemas.video_localization_asr_raw_step import (  # noqa: E402
    asr_raw_step_input,
    asr_raw_step_output_from_payload,
)
from app.services import database, project_store, settings_store  # noqa: E402
from app.services import llm_runtime  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
    video_localization_operation_detail_core_store as detail_store,
    video_localization_operation_execution as operation_execution,
    video_localization_operation_ledger_store as ledger_store,
    video_localization_operation_step_store as step_store,
)


@pytest.fixture(autouse=True)
def _keep_development_step_tests_synchronous(monkeypatch):
    """Keep the shared queue worker from leaking across temporary databases."""

    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
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


def _project_with_existing_asr(tmp_path: Path) -> tuple[str, VideoLocalizationOperation]:
    project = project_store.create_project(ProjectCreate(name="ASR 开发断点"))
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"fake-wav")
    previous_operation = VideoLocalizationOperation(
        operation_id="formal-asr",
        project_id=project.project_id,
        kind="english_asr",
        status="success",
        parameters={"execution_mode": "full"},
        result_summary={"stage": "ASR 字幕已完成"},
    )
    draft = VideoLocalizationDraft(
        source_media={
            "filename": "demo.mp4",
            "audio_path": str(audio_path),
            "duration_ms": 3200,
            "metadata": {
                "english_asr_status": "completed",
                "english_asr_engine_id": "previous-engine",
            },
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
        operations=[previous_operation],
    )
    service.save_video_localization(project.project_id, draft)
    return project.project_id, previous_operation


def _latest_formal_asr_operation_id(draft: VideoLocalizationDraft) -> str | None:
    operations = [
        operation
        for operation in draft.operations
        if operation.kind == "english_asr"
        and operation.status == "success"
        and operation.parameters.get("execution_mode") == "full"
    ]
    if not operations:
        return None
    return max(operations, key=lambda operation: operation.created_at).operation_id


def _initial_analysis_snapshot(audio_path: Path) -> asr_pipeline.AsrInitialAnalysisSnapshot:
    raw_input = transcription.TranscribeRawInput(
        audio_path=str(audio_path),
        audio_sha256="shared-audio-sha256",
        engine_id="qwen3-asr-mlx",
        source_track_id="original",
        requested_language="auto",
        duration_ms=3200,
    )
    raw_result = transcription.build_transcribe_raw_output(
        input=raw_input,
        raw_text="Hello from the interview.",
        language="en",
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=3200,
                raw_text="Hello from the interview.",
            )
        ],
        stage_timing={"duration_ms": 1200, "segment_count": 1},
    )
    diarization_input = speaker_diarization.DiarizeSpeakersInput(
        audio_path=str(audio_path),
        audio_sha256="shared-audio-sha256",
        source_track_id="original",
        engine_id="auto",
        duration_ms=3200,
    )
    diarization_result = speaker_diarization.DiarizeSpeakersOutput(
        input=diarization_input,
        status="completed",
        engine_id="moss-transcribe-diarize-mlx",
        model_id="moss-test",
        segments=[
            speaker_diarization.SpeakerDiarizationSegment(
                start_ms=0,
                end_ms=3200,
                speaker_cluster_id="cluster_01",
                source_speaker_label="speaker_0",
            )
        ],
        clusters=[
            VideoLocalizationSpeakerCluster(
                cluster_id="cluster_01",
                source_label="speaker_0",
                source_engine_id="moss-transcribe-diarize-mlx",
                start_ms=0,
                end_ms=3200,
                duration_ms=3200,
                segment_count=1,
            )
        ],
        count_guidance=speaker_diarization.SpeakerCountGuidanceResult(
            detected_speaker_count=1,
            engine_applied=False,
            usage="automatic",
            evaluation="automatic",
        ),
        quality_summary=speaker_diarization.SpeakerDiarizationQualitySummary(
            status="passed",
            segment_count=1,
            cluster_count=1,
            overlap_segment_count=0,
            covered_duration_ms=3200,
            audio_duration_ms=3200,
            coverage_ratio=1,
        ),
        stage_timing={
            "duration_ms": 1800,
            "segment_count": 1,
            "cluster_count": 1,
            "status": "completed",
        },
    )
    analysis = asr_pipeline.AsrInitialAnalysisResult(
        raw_asr=raw_result,
        diarization=diarization_result,
    )
    return asr_pipeline.AsrPipeline().snapshot_initial_analysis(analysis)


def _stub_initial_analysis_branches(
    monkeypatch,
    snapshot: asr_pipeline.AsrInitialAnalysisSnapshot,
) -> None:
    def raw_runner(request, *, context=None):
        assert context is not None
        return snapshot.analysis.raw_asr.model_copy(
            update={"input": request},
            deep=True,
        )

    def diarization_runner(request, *, context=None):
        assert context is not None
        assert snapshot.analysis.diarization is not None
        return snapshot.analysis.diarization.model_copy(
            update={"input": request},
            deep=True,
        )

    monkeypatch.setattr(
        asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        raw_runner,
    )
    monkeypatch.setattr(
        asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_speaker_diarization",
        diarization_runner,
    )


def _document_understanding_result(
    snapshot: asr_pipeline.AsrInitialAnalysisSnapshot,
    *,
    upstream_operation_id: str,
) -> document_understanding_contracts.AsrDocumentUnderstandingResult:
    request = document_understanding_contracts.AsrDocumentUnderstandingInput.from_joined_transcript(
        snapshot.joined_transcript,
        upstream_operation_id=upstream_operation_id,
        profile_id="review-profile",
        scene_context="Two-person AI interview.",
    )
    return document_understanding_contracts.AsrDocumentUnderstandingResult(
        input=request,
        brief=document_understanding_contracts.AsrDocumentUnderstandingBrief(
            summary="这是一段围绕 AI 芯片投资判断展开的访谈。",
            content_logic=["主持人提出问题", "嘉宾解释判断依据"],
            speaker_style="两人问答，嘉宾以解释和举例为主。",
            entity_candidates=[
                {
                    "name": "JoAnne Feeney",
                    "role": "受访嘉宾姓名候选",
                    "needs_research": True,
                }
            ],
            research_candidates=[
                {
                    "query": "JoAnne Feeney AI chips interview",
                    "category": "proper_noun",
                    "reason": "核对受访者姓名拼写。",
                    "target_terms": ["JoAnne Feeney"],
                }
            ],
            review_sections=[
                {
                    "section_id": "S1",
                    "start_ordinal": 1,
                    "end_ordinal": 1,
                    "start_segment_id": request.segments[0].segment_id,
                    "end_segment_id": request.segments[0].segment_id,
                    "role": "开场与主题建立",
                    "focus": ["核对受访者姓名和访谈主题"],
                }
            ],
        ),
        profile_id="review-profile",
        model_id="review-model",
        prompt_version="asr-flow-v5",
        execution_strategy="full_document",
        window_count=0,
        llm_call_count=1,
        retry_count=0,
        stage_timing={"duration_ms": 250},
        quality_summary={
            "status": "passed",
            "segment_count": 1,
            "section_count": 1,
            "sections_cover_all_segments": True,
            "source_text_unchanged": True,
        },
        raw_responses=[
            {
                "stage": "full_document",
                "attempt": 1,
                "response_json": ('{"summary":"这是一段 AI 芯片访谈。","logic":["提出问题","解释判断"]}'),
            }
        ],
    )


def _research_evidence_result(
    understanding: (document_understanding_contracts.AsrDocumentUnderstandingResult),
    *,
    upstream_operation_id: str,
) -> research_evidence.AsrResearchEvidenceResult:
    request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            understanding,
            upstream_operation_id=upstream_operation_id,
        )
    return research_evidence.AsrResearchEvidenceResult(
        input=request,
        status="completed",
        stop_reason="evidence_sufficient",
        profile_id="review-profile",
        model_id="review-model",
        query_runs=[
            {
                "query_run_id": "round_01_candidate_01",
                "round_index": 1,
                "candidate_id": "candidate_01",
                "requested_query": ("JoAnne Feeney AI chips interview"),
                "effective_query": ("JoAnne Feeney AI chips portfolio manager"),
                "outcome": "supported",
                "provider": "duckduckgo",
                "source_count": 1,
                "duration_ms": 50,
            }
        ],
        evidence=[
            {
                "evidence_id": "evidence_01",
                "candidate_id": "candidate_01",
                "query_run_id": "round_01_candidate_01",
                "title": "JoAnne Feeney discusses AI chip stocks",
                "url": "https://example.com/interview",
                "snippet": ("Portfolio manager JoAnne Feeney discusses AI."),
                "provider": "duckduckgo",
                "retrieved_at": "2026-07-24T00:00:00Z",
                "matched_target_terms": ["JoAnne Feeney"],
            }
        ],
        rounds=[
            {
                "round_index": 1,
                "query_run_ids": ["round_01_candidate_01"],
                "evidence_ids": ["evidence_01"],
                "assessments": [
                    {
                        "candidate_id": "candidate_01",
                        "decision": "sufficient",
                        "reason": ("来源标题和摘要与访谈主题直接相关。"),
                    }
                ],
                "new_evidence_count": 1,
            }
        ],
        supported_candidate_ids=["candidate_01"],
        stage_timing={"duration_ms": 80},
        quality_summary={
            "status": "passed",
            "candidate_count": 1,
            "supported_candidate_count": 1,
            "unresolved_candidate_count": 0,
            "failed_query_count": 0,
            "total_query_count": 1,
            "evidence_count": 1,
            "round_count": 1,
            "source_text_unchanged": True,
        },
    )


def _visual_evidence_result(
    understanding: (document_understanding_contracts.AsrDocumentUnderstandingResult),
    *,
    upstream_operation_id: str,
    frame_dir: Path,
) -> visual_evidence.AsrVisualEvidenceResult:
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_bytes = b"bounded-visual-frame"
    frame_path = frame_dir / "frame_01.jpg"
    frame_path.write_bytes(frame_bytes)
    request = visual_evidence.AsrVisualEvidenceInput.from_document_understanding(
            understanding,
            upstream_operation_id=upstream_operation_id,
            video_sha256="video-sha256",
            video_duration_ms=60_000,
            profile_id="vision-profile",
        )
    segment = understanding.input.segments[0]
    request = request.model_copy(
        update={
            "questions": [
                visual_evidence.AsrVisualEvidenceQuestion(
                    question_id="visual_01",
                    start_ordinal=1,
                    end_ordinal=1,
                    start_segment_id=segment.segment_id,
                    end_segment_id=segment.segment_id,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    kind="visible_text",
                    reason="核对 JoAnne Feeney 的画面姓名条。",
                    question="姓名条显示的姓名和机构是什么？",
                    frame_strategy="look_ahead",
                )
            ]
        }
    )
    return visual_evidence.AsrVisualEvidenceResult(
        input=request,
        status="completed",
        stop_reason="completed",
        profile_id="vision-profile",
        model_id="vision-model",
        frames=[
            {
                "frame_id": "frame_01",
                "question_id": "visual_01",
                "frame_index": 1,
                "timestamp_ms": 40_000,
                "file_name": frame_path.name,
                "sha256": hashlib.sha256(frame_bytes).hexdigest(),
                "size_bytes": len(frame_bytes),
            }
        ],
        observations=[
            {
                "question_id": "visual_01",
                "status": "answered",
                "answer": "画面姓名条显示嘉宾姓名和机构。",
                "visible_text": [
                    "JoAnne Feeney",
                    "Advisors Capital",
                ],
                "search_terms": [
                    "JoAnne Feeney Advisors Capital",
                ],
                "relevant_frame_ids": ["frame_01"],
                "confidence": 0.98,
                "needs_web_search": True,
            }
        ],
        stage_timing={"duration_ms": 200},
        quality_summary={
            "status": "passed",
            "question_count": 1,
            "answered_question_count": 1,
            "unresolved_question_count": 0,
            "failed_question_count": 0,
            "frame_count": 1,
            "source_text_unchanged": True,
        },
    )


def test_research_snapshot_uses_resolved_cache_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(ProjectCreate(name="资料查询缓存路径"))
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"fake-wav")
    understanding = _document_understanding_result(
        _initial_analysis_snapshot(audio_path),
        upstream_operation_id="initial-analysis",
    )
    expected = _research_evidence_result(
        understanding,
        upstream_operation_id="document-understanding",
    )
    resolved_cache = tmp_path / "resolved-cache"
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        settings_store,
        "cache_dir",
        lambda: resolved_cache,
    )

    def fake_run(request, *, context, cache_dir):
        assert request.upstream_operation_id == "document-understanding"
        assert context.is_cancelled is not None
        captured["cache_dir"] = cache_dir
        return expected

    monkeypatch.setattr(
        asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_research_evidence",
        fake_run,
    )

    result = service.research_english_transcript_snapshot(
        project.project_id,
        understanding,
        upstream_operation_id="document-understanding",
        is_cancelled=lambda: False,
    )

    assert result == expected
    assert captured["cache_dir"] == str(resolved_cache / "video-localization" / "research")


def test_transcribe_raw_contract_normalizes_only_engine_output(tmp_path: Path, monkeypatch):
    assert AppSettings().video_localization_development_step_control_enabled is False
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"audio")

    monkeypatch.setattr(
        transcription.asr_service,
        "transcribe",
        lambda **_kwargs: {
            "text": "Hello raw ASR.",
            "segments": [{"start_ms": 0, "end_ms": 1200, "text": "Hello raw ASR.", "language": "en"}],
            "incomplete_chunk_ranges": [{"start_ms": 2000, "end_ms": 2500}],
            "usage_seconds": 1.2,
            "provider_response_id": "provider-run-1",
        },
    )

    result = transcription.transcribe_raw(
        transcription.TranscribeRawInput(
            audio_path=str(audio_path),
            audio_sha256="sha256",
            engine_id="qwen3-asr-mlx",
            source_track_id="original",
            requested_language="auto",
            duration_ms=3000,
        )
    )

    assert result.contract_version == "asr-raw-v2"
    assert result.raw_text == "Hello raw ASR."
    assert result.language == "en"
    assert [segment.raw_text for segment in result.segments] == ["Hello raw ASR."]
    assert result.incomplete_chunk_ranges == [{"start_ms": 2000, "end_ms": 2500}]
    assert result.usage_seconds == 1.2
    assert result.provider_response_id == "provider-run-1"
    assert result.stage_timing["segment_count"] == 1
    assert result.quality_summary is not None
    assert result.quality_summary.status == "warning"
    assert result.quality_summary.has_text is True
    assert result.quality_summary.has_segments is True
    assert result.quality_summary.timestamps_monotonic is True
    assert result.quality_summary.incomplete_range_count == 1
    assert "incomplete_chunk_ranges" in result.quality_summary.warning_codes


def test_partial_asr_sample_uses_stable_stratified_random_coverage():
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id=f"asr_{index:04d}",
            start_ms=(index - 1) * 1000,
            end_ms=index * 1000,
            raw_text=f"Segment {index}.",
        )
        for index in range(1, 81)
    ]
    result = transcription.build_transcribe_raw_output(
        input=transcription.TranscribeRawInput(
            audio_path="/tmp/source.wav",
            audio_sha256="sha256",
            engine_id="qwen3-asr-mlx",
            source_track_id="vocals",
            requested_language="auto",
            duration_ms=80_000,
        ),
        raw_text=" ".join(segment.raw_text for segment in segments),
        language="en",
        segments=segments,
    )

    first = asr_raw_operation_projection.partial_asr_sample(
        result,
        seed="operation-123",
    )
    repeated = asr_raw_operation_projection.partial_asr_sample(
            result,
            seed="operation-123",
        )

    sampled_numbers = [int(segment["segment_id"].split("_")[-1]) for segment in first["segments"]]
    assert first == repeated
    assert first["sampling_mode"] == "deterministic_stratified_random"
    assert first["sample_count"] == 8
    assert first["sample_ratio"] == pytest.approx(0.10)
    assert first["quality_summary"]["status"] == "passed"
    assert len(sampled_numbers) == 8
    assert sampled_numbers == sorted(sampled_numbers)
    assert sampled_numbers[0] <= 10
    assert sampled_numbers[-1] >= 71
    assert any(number > 20 for number in sampled_numbers)


def test_document_understanding_summary_keeps_all_four_result_groups():
    result = {
        "label": "理解全文并规划复查",
        "order": 30,
        "status": "success",
        "notes": [],
        "summary_section_limit": 4,
        "sections": [{"title": f"分组 {index}", "items": [{"title": "结果"}]} for index in range(1, 5)],
    }

    compact = operation_queue._compact_live_step_result(result)

    assert [section["title"] for section in compact["sections"]] == [
        "分组 1",
        "分组 2",
        "分组 3",
        "分组 4",
    ]
    assert "coverage" not in compact


def test_legacy_stop_after_is_not_a_new_asr_request_contract() -> None:
    schema = VideoLocalizationAsrOperationRequest.model_json_schema()

    assert schema["properties"]["execution_mode"]["enum"] == [
        "full",
        "development_target",
    ]
    assert "stop_after_step" not in schema["properties"]
    target_values = schema["properties"][
        "development_target_step_id"
    ]["anyOf"][0]["enum"]
    assert not any(value.endswith("_r2") for value in target_values)
    with pytest.raises(ValidationError):
        VideoLocalizationAsrOperationRequest.model_validate(
            {
                "execution_mode": "stop_after",
                "stop_after_step": "asr",
            }
        )


def test_legacy_stop_after_history_cannot_be_retried(
    tmp_path: Path,
) -> None:
    _configure(tmp_path, enabled=True)
    project_id, _ = _project_with_existing_asr(tmp_path)
    with pytest.raises(AppException) as submit_error:
        operation_queue.submit(
            project_id,
            "english_asr",
            {
                "execution_mode": "stop_after",
                "stop_after_step": "asr",
            },
        )
    assert submit_error.value.code == (
        "VIDEO_LOCALIZATION_ASR_EXECUTION_MODE_INVALID"
    )
    with pytest.raises(AppException) as mixed_error:
        operation_queue.submit(
            project_id,
            "english_asr",
            {
                "execution_mode": "full",
                "stop_after_step": "asr",
            },
        )
    assert mixed_error.value.code == (
        "VIDEO_LOCALIZATION_ASR_PARAMETERS_INVALID"
    )

    draft = service.get_video_localization(project_id)
    assert draft is not None
    legacy = VideoLocalizationOperation(
        operation_id="legacy-stop-after",
        project_id=project_id,
        kind="english_asr",
        status="failed",
        parameters={
            "execution_mode": "stop_after",
            "stop_after_step": "asr",
        },
    )
    service.save_video_localization(
        project_id,
        draft.model_copy(
            update={"operations": [*draft.operations, legacy]}
        ),
    )

    with pytest.raises(AppException) as raised:
        operation_queue.retry(project_id, legacy.operation_id)

    assert raised.value.code == (
        "VIDEO_LOCALIZATION_ASR_STOP_AFTER_RETIRED"
    )


def _managed_raw_result(request):
    return transcription.build_transcribe_raw_output(
        input=request,
        raw_text="Managed raw result.",
        language="en",
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=800,
                raw_text="Managed raw result.",
            )
        ],
        stage_timing={
            "duration_ms": 25,
            "segment_count": 1,
        },
    )


def test_managed_raw_asr_contract_rejects_inconsistent_result_metadata():
    request = transcription.TranscribeRawInput(
        audio_path="/runtime-only/source.wav",
        audio_sha256="a" * 64,
        engine_id="qwen3-asr-mlx",
        source_track_id="original",
        requested_language="auto",
        duration_ms=1_000,
    )
    step_input = asr_raw_step_input(
        audio_sha256=request.audio_sha256,
        source_track_id=request.source_track_id,
        engine_id=request.engine_id,
        requested_language=request.requested_language,
        duration_ms=request.duration_ms,
    )
    payload = _managed_raw_result(request).model_dump(mode="json")
    payload["quality_summary"]["segment_count"] = 2

    with pytest.raises(
        ValueError,
        match="quality summary does not match result",
    ):
        asr_raw_step_output_from_payload(
            step_input,
            payload,
        )

    payload = _managed_raw_result(request).model_dump(mode="json")
    payload["incomplete_chunk_ranges"] = ["not-an-object"]
    with pytest.raises(
        ValueError,
        match="incomplete ranges must be objects",
    ):
        asr_raw_step_output_from_payload(
            step_input,
            payload,
        )


def test_development_target_replays_only_target_and_never_enters_full_asr(
    tmp_path: Path,
    monkeypatch,
):
    _configure(tmp_path, enabled=True)
    project_id, _previous_operation = _project_with_existing_asr(
        tmp_path
    )
    before = service.get_video_localization(project_id)
    assert before is not None
    monkeypatch.setattr(
        operation_queue,
        "DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT",
        tmp_path / "development-snapshots",
    )
    assert before.source_media.audio_path is not None
    initial = _initial_analysis_snapshot(
        Path(before.source_media.audio_path)
    )
    workflow = asr_pipeline.AsrPipelineInput(
        operation_id="formal-asr",
        audio_path=before.source_media.audio_path,
        alignment_audio_path=before.source_media.audio_path,
        engine_id="qwen3-asr-mlx",
        source_track_id="original",
        alignment_source_track_id="original",
        language="en",
        duration_ms=3_200,
        source_audio_sha256="shared-audio-sha256",
        alignment_audio_sha256="shared-audio-sha256",
    )
    lineage_writer = (
        development_checkpoints
        .LocalizationDevelopmentCheckpointWriter(
            operation_queue.DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT,
            project_id=project_id,
            workflow_operation_id="formal-asr",
        )
    )
    lineage_writer("workflow_input", workflow)
    lineage_writer("asr_result", initial.analysis.raw_asr)
    lineage_writer(
        "initial_analysis_join_result",
        initial.joined_transcript,
    )
    replay_result = review_decisions.AsrReviewDecisionsResult(
        input=review_decisions.AsrReviewDecisionsInput(
            upstream_operation_id="section-review",
            source_track_id="original",
            source_audio_sha256="shared-audio-sha256",
            language="en",
            profile_id="review-profile",
            round_index=1,
            document_summary="One short interview.",
            segments=[
                item.model_copy(deep=True)
                for item in initial.joined_transcript.segments
            ],
            issues=[],
            upstream_status="completed",
        ),
        status="completed",
        profile_id="review-profile",
        updated_segments=[
            item.model_copy(deep=True)
            for item in initial.joined_transcript.segments
        ],
        duration_ms=1,
        quality_summary={
            "status": "passed",
            "issue_count": 0,
            "decided_issue_count": 0,
            "applied_change_count": 0,
            "unresolved_issue_count": 0,
            "upstream_review_complete": True,
            "segment_ids_unchanged": True,
            "source_timing_unchanged": True,
            "only_supplied_issues_considered": True,
            "locked_changes_preserved": True,
        },
    )
    monkeypatch.setattr(
        operation_queue,
        "_enqueue",
        lambda _operation_id: None,
    )
    replay_calls: list[dict] = []

    def replay_target(**kwargs):
        replay_calls.append(kwargs)
        return (
            asr_development_replay
            .AsrDevelopmentReplayExecution(
                target_step_id="review_decisions_r1",
                source_snapshot_step_id=(
                    "review_decisions_r1_prepared"
                ),
                result=replay_result,
                skipped_paid_preparation=True,
            )
        )

    monkeypatch.setattr(
        asr_development_replay,
        "replay_asr_target",
        replay_target,
    )

    def forbidden_full_asr(*_args, **_kwargs):
        raise AssertionError(
            "development_target must never enter full ASR"
        )

    monkeypatch.setattr(
        service,
        "transcribe_english_source_audio",
        forbidden_full_asr,
    )
    submitted = operation_queue.submit(
        project_id,
        "english_asr",
        {
            "execution_mode": "development_target",
            "development_source_operation_id": "formal-asr",
            "development_target_step_id": (
                "review_decisions_r1"
            ),
        },
    )
    assert submitted is not None

    operation_queue._process(project_id, submitted.operation_id)

    completed = operation_queue.get_operation(
        project_id,
        submitted.operation_id,
    )
    after = service.get_video_localization(project_id)
    assert completed is not None
    assert after is not None
    assert completed.status == "success"
    assert completed.label == "ASR 子流程开发"
    assert completed.result_summary[
        "development_target_step_id"
    ] == "review_decisions_r1"
    assert completed.result_summary[
        "formal_project_data_changed"
    ] is False
    assert completed.result_summary[
        "skipped_paid_preparation"
    ] is True
    assert completed.result_summary["stage"] == (
        "ASR 开发结果已保存，未写入正式字幕"
    )
    projected_tasks = [
        task
        for stage in completed.result_summary["task_stage_groups"]
        for task in stage["atomic_tasks"]
    ]
    assert [task["id"] for task in projected_tasks] == [
        "review_decisions_r1"
    ]
    assert len(replay_calls) == 1
    assert after.cues == before.cues
    assert after.transcription == before.transcription
