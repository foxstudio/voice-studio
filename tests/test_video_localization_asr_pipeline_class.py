from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_flow,
    asr_pipeline,
    asr_targeted_relisten,
    document_understanding_contracts,
    speaker_diarization,
    transcription,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationResearchState,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.services import llm_runtime  # noqa: E402


def _raw_input() -> transcription.TranscribeRawInput:
    return transcription.TranscribeRawInput(
        audio_path="/tmp/source.wav",
        audio_sha256="source-sha256",
        engine_id="qwen3-asr-mlx",
        source_track_id="vocals",
        requested_language="auto",
        duration_ms=4000,
    )


def _raw_output() -> transcription.TranscribeRawOutput:
    return transcription.build_transcribe_raw_output(
        input=_raw_input(),
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


def _full_input(**patch) -> asr_pipeline.AsrPipelineInput:
    values = {
        "audio_path": "/tmp/source.wav",
        "alignment_audio_path": "/tmp/alignment.wav",
        "engine_id": "qwen3-asr-mlx",
        "source_track_id": "vocals",
        "alignment_source_track_id": "original",
        "language": "auto",
        "duration_ms": 4000,
        "source_filename": "Demo (Seedance_2.0).webm",
        "source_audio_sha256": "source-sha256",
        "alignment_audio_sha256": "alignment-sha256",
        "diarization_engine_id": "auto",
    }
    values.update(patch)
    return asr_pipeline.AsrPipelineInput(**values)


def _review_input() -> asr_pipeline.AsrTranscriptReviewInput:
    return asr_pipeline.AsrTranscriptReviewInput(
        audio_path="/tmp/source.wav",
        engine_id="qwen3-asr-mlx",
        segments=_raw_output().segments,
        language="en",
        profile_id="review-profile",
        scene_context="Two-person AI interview.",
        research_cache_dir="/tmp/asr-review-cache",
    )


def _review_run(
    request: asr_pipeline.AsrTranscriptReviewInput,
) -> asr_flow.AsrReviewRun:
    return asr_flow.AsrReviewRun(
        segments=request.segments,
        research=VideoLocalizationResearchState(status="not_needed"),
        profile_id=request.profile_id,
        model_id="review-model",
        report={"status": "passed", "changes": [], "warnings": []},
        stage_timings={"understand_document": {"duration_ms": 12}},
        review_meta={"status": "completed"},
    )


def _review_result(
    request: asr_pipeline.AsrTranscriptReviewInput,
) -> asr_pipeline.AsrTranscriptReviewResult:
    run = _review_run(request)
    return asr_pipeline.AsrTranscriptReviewResult(
        input=request,
        segments=run.segments,
        research=run.research,
        profile_id=run.profile_id,
        model_id=run.model_id,
        report=run.report,
        stage_timings=run.stage_timings,
        review_meta=run.review_meta,
    )


def test_pipeline_input_is_versioned_and_rejects_invalid_speaker_range():
    request = _full_input(min_speakers=1, max_speakers=3)

    assert request.contract_version == "asr-pipeline-v2"
    assert request.source_filename == "Demo (Seedance_2.0).webm"
    assert request.source_audio_sha256 == "source-sha256"
    assert request.alignment_audio_sha256 == "alignment-sha256"

    with pytest.raises(ValidationError):
        _full_input(min_speakers=4, max_speakers=2)


def test_section_review_flow_ranges_reject_out_of_bounds_before_indexing():
    with pytest.raises(ValueError, match="ordered, non-overlapping"):
        asr_pipeline._section_review_sections_from_flow(
            _raw_output().segments,
            [
                {
                    "id": "S1",
                    "start_segment": 1,
                    "end_segment": 2,
                    "role": "Opening",
                    "focus": ["Check topic"],
                }
            ],
        )


def test_targeted_section_review_accepts_sparse_real_failure_ranges():
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id=f"asr_{ordinal:04d}",
            start_ms=(ordinal - 1) * 1000,
            end_ms=ordinal * 1000,
            raw_text=f"Segment {ordinal}.",
        )
        for ordinal in range(1, 174)
    ]
    sections = [
        {"id": "T1", "start_segment": 67, "end_segment": 67},
        {"id": "T2", "start_segment": 87, "end_segment": 87},
        {"id": "T3", "start_segment": 109, "end_segment": 109},
        {"id": "T4", "start_segment": 141, "end_segment": 141},
        {"id": "T5", "start_segment": 160, "end_segment": 160},
    ]

    result = asr_pipeline._section_review_sections_from_flow(
        segments,
        sections,
        require_full_coverage=False,
    )

    assert [item.section_id for item in result] == [
        "T1",
        "T2",
        "T3",
        "T4",
        "T5",
    ]
    assert result[0].start_segment_id == "asr_0067"
    assert result[-1].end_segment_id == "asr_0160"


def test_targeted_relisten_crops_only_planned_sections_and_keeps_context_terms(
    monkeypatch,
):
    import numpy as np

    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0001",
            start_ms=1_000,
            end_ms=2_000,
            raw_text="But that was slow.",
        ),
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0002",
            start_ms=2_000,
            end_ms=3_000,
            raw_text="Instead of camera movement.",
        ),
    ]
    section = asr_pipeline._section_review_sections_from_flow(
        segments,
        [
            {
                "id": "target-1",
                "start_segment": 1,
                "end_segment": 2,
                "role": "重听疑点",
                "focus": ["核对相邻句意"],
            }
        ],
        require_full_coverage=False,
    )[0]
    calls: list[dict] = []

    monkeypatch.setattr(
        asr_targeted_relisten.audio_tools,
        "read_audio",
        lambda _path: (np.zeros(64_000, dtype=np.float32), 16_000),
    )
    monkeypatch.setattr(
        asr_targeted_relisten.audio_tools,
        "write_audio",
        lambda *_args, **_kwargs: None,
    )

    def fake_transcribe(**kwargs):
        calls.append(kwargs)
        return {
            "text": (
                "But that was a slow and steady camera movement. "
                "Now, let's speed it up."
            ),
            "segments": [],
        }

    monkeypatch.setattr(
        asr_targeted_relisten.asr_service,
        "transcribe",
        fake_transcribe,
    )

    result = asr_targeted_relisten.relisten_targeted_sections(
        audio_path="/tmp/source.wav",
        engine_id="qwen3-asr-mlx",
        language="en",
        sections=[section],
        segments=segments,
        context_terms=["Seedance 2.0"],
    )

    assert len(result) == 1
    assert result[0].section_id == "target-1"
    assert result[0].start_ms == 200
    assert result[0].end_ms == 3_800
    assert "slow and steady camera movement" in result[0].text
    assert calls[0]["hotwords"] == ("Seedance 2.0",)


def test_issue_relisten_uses_exact_segment_window_in_first_review_round(
    monkeypatch,
):
    import numpy as np

    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0001",
            start_ms=1_000,
            end_ms=2_000,
            raw_text="The artifact has no smell.",
        ),
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0002",
            start_ms=2_000,
            end_ms=3_000,
            raw_text="Grab your boards.",
        ),
    ]
    issue = SimpleNamespace(
        issue_id="issue-01",
        section_id="S1",
        segment_id="asr_0001",
        target_segment_ids=["asr_0001"],
        confidence=0.52,
        needs_confirmation=True,
        evidence_source_ids=[],
    )
    monkeypatch.setattr(
        asr_targeted_relisten.audio_tools,
        "read_audio",
        lambda _path: (np.zeros(64_000, dtype=np.float32), 16_000),
    )
    monkeypatch.setattr(
        asr_targeted_relisten.audio_tools,
        "write_audio",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        asr_targeted_relisten.asr_service,
        "transcribe",
        lambda **_kwargs: {
            "text": "The artifact has no smell.",
            "segments": [],
        },
    )

    result = asr_targeted_relisten.relisten_review_issues(
        audio_path="/tmp/source.wav",
        engine_id="qwen3-asr-mlx",
        language="en",
        issues=[issue],
        segments=segments,
    )

    assert len(result) == 1
    assert result[0].issue_ids == ["issue-01"]
    assert result[0].start_ms == 200
    assert result[0].end_ms == 2_800


def test_targeted_section_review_rejects_overlapping_ranges():
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id=f"asr_{ordinal:04d}",
            start_ms=(ordinal - 1) * 1000,
            end_ms=ordinal * 1000,
            raw_text=f"Segment {ordinal}.",
        )
        for ordinal in range(1, 6)
    ]

    with pytest.raises(ValueError, match="ordered, non-overlapping"):
        asr_pipeline._section_review_sections_from_flow(
            segments,
            [
                {"id": "T1", "start_segment": 2, "end_segment": 4},
                {"id": "T2", "start_segment": 4, "end_segment": 5},
            ],
            require_full_coverage=False,
        )


def test_pipeline_reuses_existing_raw_and_diarization_contracts(monkeypatch):
    pipeline = asr_pipeline.AsrPipeline()
    raw_request = _raw_input()
    raw_result = _raw_output()
    diarization_request = speaker_diarization.DiarizeSpeakersInput(
        audio_path=raw_request.audio_path,
        audio_sha256=raw_request.audio_sha256,
        source_track_id=raw_request.source_track_id,
        engine_id="auto",
        duration_ms=raw_request.duration_ms,
    )
    calls: list[tuple[str, object, object]] = []

    def cancelled() -> bool:
        return False

    monkeypatch.setattr(
        transcription,
        "transcribe_raw",
        lambda request, *, is_cancelled=None: (
            calls.append(("raw", request, is_cancelled)),
            raw_result,
        )[1],
    )
    diarization_result = object()
    monkeypatch.setattr(
        speaker_diarization,
        "diarize_speakers",
        lambda request, *, is_cancelled=None: (
            calls.append(("diarization", request, is_cancelled)),
            diarization_result,
        )[1],
    )
    context = asr_pipeline.AsrRunContext(is_cancelled=cancelled)

    assert pipeline.run_raw_asr(raw_request, context=context) is raw_result
    assert pipeline.run_speaker_diarization(diarization_request, context=context) is diarization_result
    assert calls == [
        ("raw", raw_request, cancelled),
        ("diarization", diarization_request, cancelled),
    ]


def test_initial_analysis_reuses_pipeline_step_methods(monkeypatch):
    pipeline = asr_pipeline.AsrPipeline()
    raw_request = _raw_input()
    raw_result = _raw_output()
    calls: list[transcription.TranscribeRawInput] = []

    def fake_run_raw_asr(request, *, context=None):
        calls.append(request)
        assert isinstance(context, asr_pipeline.AsrRunContext)
        return raw_result

    monkeypatch.setattr(pipeline, "run_raw_asr", fake_run_raw_asr)

    result = pipeline.run_initial_analysis(raw_asr=raw_request)

    assert result.contract_version == "asr-initial-analysis-v1"
    assert result.raw_asr is raw_result
    assert result.diarization is None
    assert calls == [raw_request]


def test_initial_analysis_starts_independent_branches_in_parallel():
    raw_request = _raw_input()
    diarization_request = speaker_diarization.DiarizeSpeakersInput(
            audio_path=raw_request.audio_path,
            audio_sha256=raw_request.audio_sha256,
            source_track_id=raw_request.source_track_id,
            engine_id="auto",
            duration_ms=raw_request.duration_ms,
        )
    barrier = threading.Barrier(2, timeout=1)

    def run_raw(_request):
        barrier.wait()
        return _raw_output()

    diarization_result = object()

    def run_diarization(_request):
        barrier.wait()
        return diarization_result

    result = transcription.run_initial_speech_analysis(
        asr_request=raw_request,
        diarization_request=diarization_request,
        raw_runner=run_raw,
        diarization_runner=run_diarization,
    )

    assert result.raw_asr.raw_text == "Hello."
    assert result.diarization is diarization_result


def test_initial_analysis_can_propagate_non_degradable_branch_failure():
    raw_request = _raw_input()
    diarization_request = speaker_diarization.DiarizeSpeakersInput(
            audio_path=raw_request.audio_path,
            audio_sha256=raw_request.audio_sha256,
            source_track_id=raw_request.source_track_id,
            engine_id="auto",
            duration_ms=raw_request.duration_ms,
        )

    class FatalBranchError(RuntimeError):
        pass

    with pytest.raises(
        FatalBranchError,
        match="fence lost",
    ):
        transcription.run_initial_speech_analysis(
            asr_request=raw_request,
            diarization_request=diarization_request,
            raw_runner=lambda _request: _raw_output(),
            diarization_runner=lambda _request: _raise(FatalBranchError("fence lost")),
            is_fatal_diarization_error=lambda exc: isinstance(
                exc,
                FatalBranchError,
            ),
        )


def _raise(exc: Exception):
    raise exc


def test_document_understanding_is_one_versioned_atomic_method(monkeypatch):
    pipeline = asr_pipeline.AsrPipeline()
    joined = asr_pipeline.AsrJoinedTranscript(
        raw_asr=_raw_output(),
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1000,
                raw_text="Hello.",
                speaker_cluster_id="cluster_01",
                speaker_confidence=0.9,
                has_speaker_overlap=True,
            )
        ],
    )
    request = document_understanding_contracts.AsrDocumentUnderstandingInput.from_joined_transcript(
        joined,
        upstream_operation_id="initial-analysis-operation",
        profile_id="review-profile",
        scene_context="Two-person AI interview.",
    )
    captured: dict[str, object] = {}

    def fake_understand(segments, **kwargs):
        captured["segments"] = segments
        captured.update(kwargs)
        return asr_flow.AsrDocumentUnderstandingRun(
            brief={
                "summary": "AI interview.",
                "logic": ["Question", "Answer"],
                "speaker_style": "Interview.",
                "entities": [],
                "search_queries": [],
                "sections": [
                    {
                        "id": "S1",
                        "start_segment": 1,
                        "end_segment": 1,
                        "role": "Opening",
                        "focus": ["Check topic"],
                    }
                ],
            },
            profile_id="review-profile",
            model_id="review-model",
            raw_responses=[
                {
                    "stage": "full_document",
                    "attempt": 1,
                    "response": {"summary": "AI interview."},
                }
            ],
            duration_ms=25,
            execution_strategy="full_document",
            llm_call_count=1,
            retry_count=0,
        )

    monkeypatch.setattr(asr_flow, "understand_document", fake_understand)

    result = pipeline.run_document_understanding(request)

    assert request.contract_version == "asr-document-understanding-input-v1"
    assert result.contract_version == "asr-document-understanding-v1"
    assert result.input == request
    assert result.input is not request
    assert result.quality_summary.source_text_unchanged is True
    assert result.quality_summary.status == "passed"
    assert result.stage_timing.duration_ms == 25
    assert result.raw_responses[0].response_json == ('{"summary":"AI interview."}')
    assert result.brief.review_sections[0].start_segment_id == "asr_0001"
    segment = captured["segments"][0]
    assert segment.start_ms == 0
    assert segment.speaker_cluster_id == "cluster_01"
    assert segment.speaker_confidence == 0.9
    assert segment.has_speaker_overlap is True
    assert captured["language"] == "en"
    assert captured["scene_context"] == "Two-person AI interview."
    assert captured["profile_id"] == "review-profile"


def test_document_understanding_quality_status_fails_real_invariants(monkeypatch):
    pipeline = asr_pipeline.AsrPipeline()
    request = document_understanding_contracts.AsrDocumentUnderstandingInput(
        upstream_operation_id="initial-analysis-operation",
        source_track_id="vocals",
        source_audio_sha256="source-sha256",
        language="en",
        segments=[
            {
                "ordinal": 1,
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "Hello.",
            },
            {
                "ordinal": 2,
                "segment_id": "asr_0002",
                "start_ms": 1000,
                "end_ms": 2000,
                "text": "World.",
            },
        ],
    )

    def fake_understand(segments, **_kwargs):
        segments[0].raw_text = "Changed."
        return asr_flow.AsrDocumentUnderstandingRun(
            brief={
                "summary": "Incomplete.",
                "logic": ["Opening"],
                "speaker_style": "Interview.",
                "entities": [],
                "search_queries": [],
                "sections": [
                    {
                        "id": "S1",
                        "start_segment": 1,
                        "end_segment": 1,
                        "role": "Opening",
                        "focus": ["Check topic"],
                    }
                ],
            },
            profile_id="review-profile",
            model_id="review-model",
            raw_responses=[],
            duration_ms=25,
            execution_strategy="full_document",
            llm_call_count=1,
            retry_count=0,
        )

    monkeypatch.setattr(asr_flow, "understand_document", fake_understand)

    result = pipeline.run_document_understanding(request)

    assert request.segments[0].text == "Hello."
    assert result.quality_summary.status == "failed"
    assert result.quality_summary.sections_cover_all_segments is False
    assert result.quality_summary.source_text_unchanged is False
    assert result.warnings == [
        "复查区块没有连续覆盖全部讲话片段。",
        "全文理解过程中检测到输入文字发生变化。",
    ]


def test_document_understanding_counts_internal_json_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: type(
            "Profile",
            (),
            {"profile_id": "review-profile", "model_id": "review-model"},
        )(),
    )

    def fake_complete_json(**kwargs):
        calls.append(kwargs)
        kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="review-profile",
                model_id="review-model",
                provider_host="openrouter.ai",
                request_chars=1200,
                request_body_bytes=2400,
                max_tokens=kwargs["max_tokens"],
                timeout_seconds=kwargs["timeout"],
                reasoning_effort_requested=None,
                reasoning_control_applied=bool(kwargs.get("disable_reasoning")),
                duration_ms=1500,
                finish_reason=("length" if len(calls) == 1 else "stop"),
                prompt_tokens=900,
                completion_tokens=120,
                reasoning_tokens=40,
                total_tokens=1020,
                cost_usd=0.0045,
                content_chars=240,
                reasoning_chars=80,
            )
        )
        if len(calls) == 1:
            raise llm_runtime.LlmRuntimeError(
                "invalid JSON",
                code="llm_json_invalid",
                status_code=502,
            )
        return {
            "summary": "这是一段人工智能访谈。",
            "logic": ["主持人提问", "嘉宾回答"],
            "speaker_style": "两人问答访谈。",
            "entities": [],
            "search_queries": [],
            "sections": [
                {
                    "id": "S1",
                    "start_segment": 1,
                    "end_segment": 1,
                    "role": "开场",
                    "focus": ["核对主题"],
                }
            ],
        }

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)

    result = asr_flow.understand_document(
        _raw_output().segments,
        language="en",
        scene_context="",
        profile_id="review-profile",
        is_cancelled=None,
    )

    assert result.llm_call_count == 2
    assert result.retry_count == 1
    assert len(result.llm_calls) == 2
    assert {item.finish_reason for item in result.llm_calls} == {"length", "stop"}
    assert sum(item.prompt_tokens or 0 for item in result.llm_calls) == 1800
    assert calls[1]["disable_reasoning"] is True


def test_transcript_review_has_versioned_standalone_contract(monkeypatch):
    pipeline = asr_pipeline.AsrPipeline()
    request = _review_input()
    captured: dict[str, object] = {}

    def is_cancelled() -> bool:
        return False

    def on_progress(_progress: float, _stage: str) -> None:
        return None

    def on_report(_step: str, _result: dict) -> None:
        return None

    previews: list[tuple[str, list[dict]]] = []

    def on_preview(phase: str, cues: list[dict]) -> None:
        previews.append((phase, cues))

    def fake_review(segments, **kwargs):
        captured["segments"] = segments
        captured.update(kwargs)
        kwargs["on_segments_changed"](
            "review_decisions_r1",
            [request.segments[0].model_copy(update={"corrected_text": "Corrected hello."})],
        )
        return _review_run(request)

    monkeypatch.setattr(asr_flow, "review_transcript", fake_review)

    result = pipeline.run_transcript_review(
        request,
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            on_preview=on_preview,
            on_report=on_report,
        ),
    )

    assert request.contract_version == "asr-transcript-review-v2"
    assert result.contract_version == "asr-transcript-review-v2"
    assert result.input is request
    assert result.segments == request.segments
    assert result.profile_id == "review-profile"
    assert result.model_id == "review-model"
    assert result.report["status"] == "passed"
    assert captured["segments"] == request.segments
    assert captured["language"] == "en"
    assert captured["profile_id"] == "review-profile"
    assert captured["scene_context"] == "Two-person AI interview."
    assert captured["research_cache_dir"] == "/tmp/asr-review-cache"
    assert captured["is_cancelled"] is is_cancelled
    assert captured["on_progress"] is on_progress
    assert callable(captured["on_report"])
    assert callable(captured["on_segments_changed"])
    assert previews == [
        (
            "text_review",
            [
                {
                    "cue_id": "asr_0001",
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "text": "Corrected hello.",
                }
            ],
        )
    ]
    assert callable(captured["document_understanding_runner"])
    assert callable(captured["research_runner"])
    assert callable(captured["section_review_runner"])
    assert callable(captured["review_decisions_runner"])


def test_pipeline_full_run_forwards_resolved_input_and_operation_context(monkeypatch):
    pipeline = asr_pipeline.AsrPipeline()
    request = _full_input(source_video_frame_rate=24.0)
    result = VideoLocalizationTranscriptionState(
        raw_text="Hello.",
        corrected_text="Hello.",
    )
    captured: dict[str, object] = {}

    def on_progress(_progress: float, _stage: str) -> None:
        return None

    def on_preview(_phase: str, _cues: list[dict]) -> None:
        return None

    def on_report(_step: str, _result: dict) -> None:
        return None

    def is_cancelled() -> bool:
        return False

    def fake_transcribe_and_process(**kwargs):
        captured.update(kwargs)
        return result

    monkeypatch.setattr(transcription, "transcribe_and_process", fake_transcribe_and_process)

    actual = pipeline.run_full(
        request,
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            on_preview=on_preview,
            on_report=on_report,
        ),
    )

    assert actual is result
    assert captured["audio_path"] == request.audio_path
    assert captured["alignment_audio_path"] == request.alignment_audio_path
    assert captured["source_audio_sha256"] == request.source_audio_sha256
    assert captured["alignment_audio_sha256"] == request.alignment_audio_sha256
    assert captured["source_filename"] == request.source_filename
    assert captured["source_video_frame_rate"] == 24.0
    assert captured["diarization_engine_id"] == "auto"
    assert captured["progress_callback"] is on_progress
    assert captured["preview_callback"] is on_preview
    assert captured["report_callback"] is on_report
    assert captured["is_cancelled"] is is_cancelled
    assert callable(captured["initial_analysis_runner"])
    assert callable(captured["initial_analysis_joiner"])
    assert callable(captured["transcript_review_runner"])

    captured.clear()
    request_without_speaker_analysis = _full_input(
        diarization_engine_id=None,
    )
    pipeline.run_full(
        request_without_speaker_analysis,
        context=asr_pipeline.AsrRunContext(
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            on_preview=on_preview,
            on_report=on_report,
        ),
    )

    assert captured["diarization_engine_id"] is None
    assert captured["initial_analysis_joiner"] is None


def _diarization_output(
    raw: transcription.TranscribeRawOutput,
    *,
    speaker_count: int,
) -> speaker_diarization.DiarizeSpeakersOutput:
    diarization_segments = [
        speaker_diarization.SpeakerDiarizationSegment(
            start_ms=(index - 1) * 500,
            end_ms=index * 500,
            speaker_cluster_id=f"cluster_{index:02d}",
            source_speaker_label=f"SPEAKER_{index - 1:02d}",
        )
        for index in range(1, speaker_count + 1)
    ]
    return speaker_diarization.DiarizeSpeakersOutput(
        input=speaker_diarization.DiarizeSpeakersInput(
            audio_path=raw.input.audio_path,
            audio_sha256=raw.input.audio_sha256,
            source_track_id=raw.input.source_track_id,
            engine_id="auto",
        ),
        status="completed",
        engine_id="moss-transcribe-diarize-mlx",
        segments=diarization_segments,
        clusters=[
            VideoLocalizationSpeakerCluster(
                cluster_id=f"cluster_{index:02d}",
                source_label=f"SPEAKER_{index - 1:02d}",
                source_engine_id="moss-transcribe-diarize-mlx",
                start_ms=(index - 1) * 500,
                end_ms=index * 500,
                duration_ms=500,
                segment_count=1,
            )
            for index in range(1, speaker_count + 1)
        ],
        count_guidance=speaker_diarization.SpeakerCountGuidanceResult(
            detected_speaker_count=speaker_count,
            engine_applied=False,
            usage="automatic",
            evaluation="automatic",
        ),
        quality_summary=speaker_diarization.SpeakerDiarizationQualitySummary(
            status="passed",
            segment_count=speaker_count,
            cluster_count=speaker_count,
            overlap_segment_count=0,
            covered_duration_ms=speaker_count * 500,
        ),
    )


def test_initial_analysis_keeps_single_speaker_evidence_without_labels():
    raw = _raw_output()
    joined = asr_pipeline.AsrPipeline().join_initial_analysis(
        asr_pipeline.AsrInitialAnalysisResult(
            raw_asr=raw,
            diarization=_diarization_output(raw, speaker_count=1),
        )
    )

    assert joined.speaker_grouping_applied is False
    assert joined.diarization is not None
    assert all(
        segment.speaker_cluster_id is None
        for segment in joined.segments
    )


def test_initial_analysis_applies_multi_speaker_labels():
    raw = _raw_output()
    joined = asr_pipeline.AsrPipeline().join_initial_analysis(
        asr_pipeline.AsrInitialAnalysisResult(
            raw_asr=raw,
            diarization=_diarization_output(raw, speaker_count=2),
        )
    )

    assert joined.speaker_grouping_applied is True
    assert any(
        segment.speaker_cluster_id is not None
        for segment in joined.segments
    )


def test_pipeline_full_run_reuses_transcript_review_method(monkeypatch):
    pipeline = asr_pipeline.AsrPipeline()
    request = _full_input(
        llm_profile_id="review-profile",
        scene_context="Two-person AI interview.",
        research_cache_dir="/tmp/asr-review-cache",
    )
    expected = VideoLocalizationTranscriptionState(
        raw_text="Hello.",
        corrected_text="Hello.",
    )
    captured: dict[str, object] = {}

    def fake_run_transcript_review(review_request, *, context=None):
        captured["request"] = review_request
        captured["context"] = context
        return _review_result(review_request)

    def fake_transcribe_and_process(**kwargs):
        captured["runner_result"] = kwargs["transcript_review_runner"](
            _raw_output().segments,
            language="en",
            profile_id="review-profile",
            glossary=[],
            scene_context="Two-person AI interview.",
            research_cache_dir="/tmp/asr-review-cache",
        )
        return expected

    monkeypatch.setattr(pipeline, "run_transcript_review", fake_run_transcript_review)
    monkeypatch.setattr(transcription, "transcribe_and_process", fake_transcribe_and_process)
    context = asr_pipeline.AsrRunContext()

    result = pipeline.run_full(request, context=context)

    assert result is expected
    review_request = captured["request"]
    assert isinstance(review_request, asr_pipeline.AsrTranscriptReviewInput)
    assert review_request.contract_version == "asr-transcript-review-v2"
    assert review_request.language == "en"
    assert review_request.profile_id == "review-profile"
    assert review_request.scene_context == "Two-person AI interview."
    assert review_request.research_cache_dir == "/tmp/asr-review-cache"
    assert captured["context"] is context
    assert isinstance(
        captured["runner_result"],
        asr_pipeline.AsrTranscriptReviewResult,
    )


def test_pipeline_degraded_review_keeps_original_text_with_advisory_gate(
    monkeypatch,
):
    pipeline = asr_pipeline.AsrPipeline()
    request = _review_input()
    original = [item.model_copy(deep=True) for item in request.segments]

    def degraded_review(segments, **_kwargs):
        return asr_flow.AsrReviewRun(
            segments=[item.model_copy(deep=True) for item in segments],
            research=VideoLocalizationResearchState(
                status="failed",
                error="模型响应中途断开",
            ),
            profile_id=None,
            model_id=None,
            report={
                "status": "degraded",
                "changes": [],
                "warnings": [],
                "error_detail": {
                    "code": "llm_result_unknown",
                    "message": "模型响应中途断开",
                    "action": "continue_with_original_asr",
                },
                "task_step_results": {},
            },
            stage_timings={},
            review_meta={"status": "partial"},
        )

    monkeypatch.setattr(asr_flow, "review_transcript", degraded_review)

    result = pipeline.run_transcript_review(request)

    assert [item.model_dump(mode="json") for item in result.segments] == [
        item.model_dump(mode="json") for item in original
    ]
    assert result.whole_rechecks == []
    assert result.quality_gate is not None
    assert result.quality_gate.input.upstream_contract_version == (
        "asr-transcript-review-v2"
    )
    assert result.quality_gate.decision == "ready_for_alignment"
    assert result.quality_gate.can_start_alignment is True
    assert result.quality_gate.blockers == []
    assert any(
        item.code == "upstream_warning"
        and "模型响应中途断开" in item.message
        for item in result.quality_gate.warnings
    )


def test_pipeline_has_no_project_persistence_dependency():
    source = Path(asr_pipeline.__file__).read_text(encoding="utf-8")

    assert "project_store" not in source
    assert "draft_store" not in source
    assert "operation_queue" not in source
    assert "VideoLocalizationDraft" not in source


def test_project_adapter_routes_all_three_asr_entry_points_through_one_pipeline():
    source_path = ROOT / "backend" / "app" / "domains" / "video_localization" / "source_pipeline.py"
    source = source_path.read_text(encoding="utf-8")

    assert "DEFAULT_ASR_PIPELINE.run_raw_asr(" in source
    assert "DEFAULT_ASR_PIPELINE.run_speaker_diarization(" in source
    assert "DEFAULT_ASR_PIPELINE.run_full(" in source
