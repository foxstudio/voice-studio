from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_research_evidence_operation_projection,
    asr_flow,
    asr_pipeline,
    document_understanding_contracts,
    research_evidence,
    visual_evidence,
    web_research,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationResearchQuery,
    VideoLocalizationResearchSource,
    VideoLocalizationResearchState,
)
from app.services import llm_runtime  # noqa: E402


def _understanding_result() -> document_understanding_contracts.AsrDocumentUnderstandingResult:
    request = document_understanding_contracts.AsrDocumentUnderstandingInput(
        upstream_operation_id="initial-analysis-operation",
        source_track_id="vocals",
        source_audio_sha256="audio-sha256",
        language="en",
        scene_context="Two-person AI investment interview.",
        profile_id="review-profile",
        segments=[
            {
                "ordinal": 1,
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 2200,
                "text": "JoAnne Feeney discusses AI chips.",
                "speaker_cluster_id": "cluster_01",
            }
        ],
    )
    return document_understanding_contracts.AsrDocumentUnderstandingResult(
        input=request,
        brief={
            "summary": "嘉宾讨论人工智能芯片的投资机会。",
            "content_logic": ["主持人提问", "嘉宾解释投资判断"],
            "speaker_style": "两人问答，表达专业。",
            "entity_candidates": [
                {
                    "name": "JoAnne Feeney",
                    "role": "受访嘉宾姓名候选",
                    "needs_research": True,
                }
            ],
            "research_candidates": [
                {
                    "query": "JoAnne Feeney AI chips interview",
                    "category": "proper_noun",
                    "reason": "核对受访者姓名拼写。",
                    "target_terms": ["JoAnne Feeney"],
                }
            ],
            "review_sections": [
                {
                    "section_id": "S1",
                    "start_ordinal": 1,
                    "end_ordinal": 1,
                    "start_segment_id": "asr_0001",
                    "end_segment_id": "asr_0001",
                    "role": "建立访谈主题",
                    "focus": ["核对嘉宾姓名"],
                }
            ],
        },
        profile_id="review-profile",
        model_id="review-model",
        prompt_version="asr-flow-v5",
        execution_strategy="full_document",
        window_count=0,
        llm_call_count=1,
        retry_count=0,
        stage_timing={"duration_ms": 100},
        quality_summary={
            "status": "passed",
            "segment_count": 1,
            "section_count": 1,
            "sections_cover_all_segments": True,
            "source_text_unchanged": True,
        },
    )


def _research_state(
    query: str,
    *,
    with_source: bool,
) -> VideoLocalizationResearchState:
    query_model = VideoLocalizationResearchQuery(
        query_id="query_01",
        query=query,
        category="proper_noun",
        reason="核对受访者姓名拼写。",
        target_terms=["JoAnne Feeney"],
    )
    sources = (
        [
            VideoLocalizationResearchSource(
                source_id="source_01",
                query_id="query_01",
                title="JoAnne Feeney discusses AI chip stocks",
                url="https://example.com/interview",
                snippet="Portfolio manager JoAnne Feeney discusses AI.",
                provider="duckduckgo",
            )
        ]
        if with_source
        else []
    )
    return VideoLocalizationResearchState(
        status="completed",
        profile_id="review-profile",
        model_id="review-model",
        provider="duckduckgo",
        queries=[query_model],
        sources=sources,
        duration_ms=10,
    )


def _visual_result() -> visual_evidence.AsrVisualEvidenceResult:
    request = visual_evidence.AsrVisualEvidenceInput(
        upstream_operation_id="understanding-operation",
        video_sha256="video-sha256",
        video_duration_ms=60_000,
        document_summary="嘉宾讨论人工智能芯片的投资机会。",
        segments=[
            {
                "ordinal": 1,
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 2200,
                "text": "JoAnne Feeney discusses AI chips.",
            }
        ],
        questions=[
            {
                "question_id": "visual_01",
                "start_ordinal": 1,
                "end_ordinal": 1,
                "start_segment_id": "asr_0001",
                "end_segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 2200,
                "kind": "visible_text",
                "reason": "核对 JoAnne Feeney 的画面姓名条。",
                "question": "姓名条显示的姓名和机构是什么？",
                "frame_strategy": "look_ahead",
            }
        ],
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
                "file_name": "frame_01.jpg",
                "sha256": "frame-sha256",
                "size_bytes": 100,
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


def test_research_input_is_a_versioned_snapshot_of_understanding():
    understanding = _understanding_result()

    request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            understanding,
            upstream_operation_id="understanding-operation",
        )

    assert request.contract_version == "asr-research-evidence-input-v1"
    assert request.upstream_contract_version == ("asr-document-understanding-v1")
    assert request.upstream_operation_id == "understanding-operation"
    assert request.source_audio_sha256 == "audio-sha256"
    assert request.candidates[0].candidate_id == "candidate_01"
    assert request.candidates[0].query == ("JoAnne Feeney AI chips interview")
    assert request.segments[0].text == ("JoAnne Feeney discusses AI chips.")


def test_visual_clue_only_narrows_query_and_never_becomes_web_evidence(
    monkeypatch,
):
    request = research_evidence.AsrResearchEvidenceInputV2.from_document_and_visual_evidence(
            _understanding_result(),
            _visual_result(),
            upstream_operation_id="understanding-operation",
            visual_evidence_operation_id="visual-operation",
        )
    assert request.contract_version == "asr-research-evidence-input-v2"
    assert len(request.visual_hints) == 1
    assert request.visual_hints[0].candidate_id == "candidate_01"

    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="review-profile",
            model_id="review-model",
        ),
    )
    captured_queries: list[str] = []

    def fake_research(_segments, **kwargs):
        query = kwargs["planned_queries"][0].query
        captured_queries.append(query)
        return _research_state(query, with_source=True)

    monkeypatch.setattr(
        web_research,
        "research_transcript",
        fake_research,
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "assessments": [
                {
                    "candidate_id": "candidate_01",
                    "decision": "sufficient",
                    "reason": "网页来源与访谈主题直接相关。",
                }
            ]
        },
    )

    result = research_evidence.ResearchEvidenceService().run(request)

    assert result.contract_version == "asr-research-evidence-v2"
    assert "Advisors Capital" in captured_queries[0]
    assert result.query_runs[0].visual_hint_ids_used == ["visual_hint_01"]
    assert len(result.evidence) == 1
    assert result.evidence[0].url == "https://example.com/interview"
    assert result.evidence[0].provider == "duckduckgo"
    dumped = result.model_dump(mode="json")
    assert "canonical_name" not in str(dumped)
    assert "replacement_text" not in str(dumped)


def test_generic_visual_words_do_not_bind_an_unrelated_candidate():
    visual_result = _visual_result()
    visual_result.input.questions.append(
        visual_evidence.AsrVisualEvidenceQuestion(
            question_id="visual_02",
            start_ordinal=1,
            end_ordinal=1,
            start_segment_id="asr_0001",
            end_segment_id="asr_0001",
            start_ms=0,
            end_ms=2200,
            kind="visible_text",
            reason="查看美光行情图。",
            question="画面显示的 Micron 涨幅是多少？",
        )
    )
    visual_result.observations.append(
        visual_evidence.AsrVisualEvidenceObservation(
            question_id="visual_02",
            status="answered",
            visible_text=["Micron", "Bloomberg Tech", "10.57%"],
            search_terms=["Micron forward earnings multiple"],
            relevant_frame_ids=["frame_02"],
            confidence=0.9,
        )
    )
    candidates = [
        research_evidence.AsrResearchEvidenceCandidate(
            candidate_id="candidate_01",
            query="JoAnne Feeney Advisors Capital",
            category="proper_noun",
            target_terms=["Duan Feeny", "Capital Management"],
        ),
        research_evidence.AsrResearchEvidenceCandidate(
            candidate_id="candidate_02",
            query="Bloomberg Technology host Ed semiconductor interview",
            category="persona",
            target_terms=["Ed"],
        ),
    ]

    hints = research_evidence._bind_visual_hints(
        candidates,
        visual_result,
    )

    assert [item.question_id for item in hints] == ["visual_01"]
    assert hints[0].candidate_id == "candidate_01"


def test_repeated_lower_third_does_not_attach_an_unrelated_chart_hint():
    visual_result = _visual_result()
    visual_result.input.questions = [
        visual_evidence.AsrVisualEvidenceQuestion(
            question_id="visual_chart",
            start_ordinal=1,
            end_ordinal=1,
            start_segment_id="asr_0001",
            end_segment_id="asr_0001",
            start_ms=200_000,
            end_ms=210_000,
            kind="chart",
            reason="查看美光估值图表。",
            question="画面中的 Micron 估值数字是什么？",
        )
    ]
    visual_result.observations = [
        visual_evidence.AsrVisualEvidenceObservation(
            question_id="visual_chart",
            status="answered",
            visible_text=[
                "JoAnne Feeney",
                "ADVISORS CAPITAL PARTNER & PORTFOLIO MANAGER",
                "Micron 10.57%",
            ],
            search_terms=["Micron forward earnings valuation Bloomberg chart"],
            relevant_frame_ids=["frame_chart"],
            confidence=0.85,
        )
    ]
    candidates = [
        research_evidence.AsrResearchEvidenceCandidate(
            candidate_id="candidate_01",
            query="JoAnne Feeney Advisors Capital",
            category="proper_noun",
            target_terms=["Duan Feeny", "Capital Management"],
        )
    ]

    hints = research_evidence._bind_visual_hints(
        candidates,
        visual_result,
    )

    assert hints == []


def test_research_uses_llm_feedback_for_a_bounded_followup(
    monkeypatch,
):
    request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            _understanding_result(),
            upstream_operation_id="understanding-operation",
        )
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="review-profile",
            model_id="review-model",
        ),
    )
    search_calls: list[str] = []

    def fake_research(_segments, **kwargs):
        assert kwargs["augment_planned_queries"] is False
        query = kwargs["planned_queries"][0].query
        search_calls.append(query)
        return _research_state(
            query,
            with_source=query.endswith("portfolio manager"),
        )

    monkeypatch.setattr(
        web_research,
        "research_transcript",
        fake_research,
    )
    assessments = iter(
        [
            {
                "assessments": [
                    {
                        "candidate_id": "candidate_01",
                        "decision": "search_more",
                        "reason": "首轮没有直接资料，补充职业身份缩小范围。",
                        "followup_query": ("JoAnne Feeney AI chips portfolio manager"),
                    }
                ]
            },
            {
                "assessments": [
                    {
                        "candidate_id": "candidate_01",
                        "decision": "sufficient",
                        "reason": "来源标题和摘要与同一访谈主题直接相关。",
                        "followup_query": None,
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: next(assessments),
    )

    result = asr_pipeline.AsrPipeline().run_research_evidence(request)

    assert search_calls == [
        "JoAnne Feeney AI chips interview",
        "JoAnne Feeney AI chips portfolio manager",
    ]
    assert result.contract_version == "asr-research-evidence-v1"
    assert result.status == "completed"
    assert result.stop_reason == "evidence_sufficient"
    assert result.supported_candidate_ids == ["candidate_01"]
    assert result.unresolved_candidate_ids == []
    assert len(result.rounds) == 2
    assert result.rounds[0].continued_candidate_ids == ["candidate_01"]
    assert result.evidence[0].candidate_id == "candidate_01"
    assert result.evidence[0].matched_target_terms == ["JoAnne Feeney"]
    assert result.quality_summary.source_text_unchanged is True
    dumped = result.model_dump(mode="json")
    assert "canonical_name" not in str(dumped)
    assert "replacement" not in str(dumped)


def test_research_stops_when_followup_makes_no_progress(
    monkeypatch,
):
    request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            _understanding_result(),
            upstream_operation_id="understanding-operation",
        )
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="review-profile",
            model_id="review-model",
        ),
    )
    monkeypatch.setattr(
        web_research,
        "research_transcript",
        lambda _segments, **kwargs: _research_state(
            kwargs["planned_queries"][0].query,
            with_source=False,
        ),
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "assessments": [
                {
                    "candidate_id": "candidate_01",
                    "decision": "search_more",
                    "reason": "建议继续使用同一个查询。",
                    "followup_query": ("JoAnne Feeney AI chips interview"),
                }
            ]
        },
    )

    result = research_evidence.ResearchEvidenceService().run(request)

    assert result.status == "partial"
    assert result.stop_reason == "no_progress"
    assert result.supported_candidate_ids == []
    assert result.unresolved_candidate_ids == ["candidate_01"]
    assert result.query_runs[0].outcome == ("unresolved_no_evidence")
    assert result.quality_summary.status == "warning"


def test_research_falls_back_to_candidate_scoped_assessment_when_batch_truncated(
    monkeypatch,
):
    request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            _understanding_result(),
            upstream_operation_id="understanding-operation",
        )
    request = request.model_copy(
        update={
            "candidates": [
                *request.candidates,
                research_evidence.AsrResearchEvidenceCandidate(
                    candidate_id="candidate_02",
                    query="Micron AI memory demand interview",
                    category="background",
                    reason="核对美光相关背景。",
                    target_terms=["Micron"],
                ),
            ]
        }
    )
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="review-profile",
            model_id="review-model",
        ),
    )
    monkeypatch.setattr(
        web_research,
        "research_transcript",
        lambda _segments, **kwargs: _research_state(
            kwargs["planned_queries"][0].query,
            with_source=True,
        ),
    )
    assessment_calls: list[tuple[list[str], int]] = []
    reasoning_efforts: list[str | None] = []

    def fake_complete_json(**kwargs):
        reasoning_efforts.append(kwargs.get("reasoning_effort"))
        candidate_ids = [item["candidate_id"] for item in kwargs["user_payload"]["candidates"]]
        assessment_calls.append((candidate_ids, kwargs["max_tokens"]))
        if len(candidate_ids) > 1:
            raise llm_runtime.LlmRuntimeError(
                "语言模型输出因长度限制而不完整",
                code="llm_output_truncated",
                status_code=502,
            )
        return {
            "assessments": [
                {
                    "candidate_id": candidate_ids[0],
                    "decision": "sufficient",
                    "reason": "该候选已有直接相关公开资料。",
                    "followup_query": None,
                }
            ]
        }

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        fake_complete_json,
    )

    result = research_evidence.ResearchEvidenceService().run(request)

    assert assessment_calls == [
        (["candidate_01", "candidate_02"], 2_400),
        (["candidate_01"], 4_096),
        (["candidate_02"], 4_096),
    ]
    assert reasoning_efforts == [None, None, None]
    assert result.status == "completed"
    assert result.stop_reason == "evidence_sufficient"
    assert result.supported_candidate_ids == [
        "candidate_01",
        "candidate_02",
    ]
    assert any("改为逐个疑点判断" in warning for warning in result.warnings)


def test_research_records_safe_llm_usage_and_exposes_it_in_debug_details(
    monkeypatch,
):
    request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            _understanding_result(),
            upstream_operation_id="understanding-operation",
        )
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="review-profile",
            model_id="moonshotai/kimi-k3",
        ),
    )
    monkeypatch.setattr(
        web_research,
        "research_transcript",
        lambda _segments, **kwargs: _research_state(
            kwargs["planned_queries"][0].query,
            with_source=True,
        ),
    )

    def fake_complete_json(**kwargs):
        kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="review-profile",
                model_id="moonshotai/kimi-k3",
                provider_host="openrouter.ai",
                request_chars=5731,
                request_body_bytes=6931,
                max_tokens=2400,
                timeout_seconds=60,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=23108,
                finish_reason="stop",
                native_finish_reason="stop",
                prompt_tokens=1815,
                cached_tokens=512,
                completion_tokens=612,
                reasoning_tokens=372,
                total_tokens=2427,
                cost_usd=0.0132426,
                content_chars=676,
                reasoning_chars=1711,
                response_id="generation-123",
            )
        )
        candidate_id = kwargs["user_payload"]["candidates"][0]["candidate_id"]
        return {
            "assessments": [
                {
                    "candidate_id": candidate_id,
                    "decision": "sufficient",
                    "reason": "已有直接相关资料。",
                    "followup_query": None,
                }
            ]
        }

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        fake_complete_json,
    )

    result = research_evidence.ResearchEvidenceService().run(request)
    debug = asr_research_evidence_operation_projection.step_result(result)["debug"]

    assert len(result.llm_calls) == 1
    call = result.llm_calls[0]
    assert call.purpose == "evidence_assessment"
    assert call.prompt_tokens == 1815
    assert call.reasoning_tokens == 372
    assert call.cost_usd == 0.0132426
    assert not hasattr(call, "reasoning")
    assert {"模型调用", "输入 Token", "输出 Token", "其中思考", "模型费用"} <= {
        item["label"] for item in debug["metrics"]
    }
    assert debug["sections"][0]["title"] == "模型调用明细"
    assert "不保存模型的完整隐藏思考" in debug["notes"][0]


def test_research_without_candidates_does_not_call_model(
    monkeypatch,
):
    request = research_evidence.AsrResearchEvidenceInput.from_document_understanding(
            _understanding_result(),
            upstream_operation_id="understanding-operation",
    ).model_copy(update={"candidates": []})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no candidates should not call a model")

    monkeypatch.setattr(llm_runtime, "resolve_profile", forbidden)

    result = research_evidence.ResearchEvidenceService().run(request)

    assert result.status == "not_needed"
    assert result.stop_reason == "no_candidates"
    assert result.query_runs == []
    assert result.quality_summary.status == "passed"


def test_formal_review_reuses_the_same_research_evidence_method(
    monkeypatch,
):
    pipeline = asr_pipeline.AsrPipeline()
    understanding = _understanding_result()
    monkeypatch.setattr(
        pipeline,
        "run_document_understanding",
        lambda _request, *, context=None: understanding,
    )
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="review-profile",
            model_id="review-model",
        ),
    )
    monkeypatch.setattr(
        web_research,
        "research_transcript",
        lambda _segments, **kwargs: _research_state(
            kwargs["planned_queries"][0].query,
            with_source=True,
        ),
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "assessments": [
                {
                    "candidate_id": "candidate_01",
                    "decision": "sufficient",
                    "reason": "来源与访谈主题直接相关。",
                }
            ]
        },
    )
    captured: dict[str, object] = {}

    def fake_review(segments, **kwargs):
        captured["brief"] = kwargs["document_understanding_runner"](
            segments,
            language="en",
            scene_context="Two-person AI investment interview.",
            profile_id="review-profile",
            is_cancelled=None,
        )
        project_research = kwargs["research_runner"](
            segments,
            language="en",
            scene_context="Two-person AI investment interview.",
            profile_id="review-profile",
            planned_queries=[],
        )
        captured["project_research"] = project_research
        return asr_flow.AsrReviewRun(
            segments=segments,
            research=project_research,
            profile_id="review-profile",
            model_id="review-model",
            report={"status": "passed"},
            stage_timings={"research": {"duration_ms": 10}},
            review_meta={"status": "completed"},
        )

    monkeypatch.setattr(asr_flow, "review_transcript", fake_review)
    review_request = asr_pipeline.AsrTranscriptReviewInput(
        audio_path="/tmp/source.wav",
        engine_id="qwen3-asr-mlx",
        upstream_operation_id="formal-operation",
        source_track_id="vocals",
        source_audio_sha256="audio-sha256",
        segments=[
            {
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 2200,
                "raw_text": "JoAnne Feeney discusses AI chips.",
            }
        ],
        language="en",
        profile_id="review-profile",
        scene_context="Two-person AI investment interview.",
    )

    result = pipeline.run_transcript_review(review_request)

    assert result.research_evidence is not None
    assert result.research_evidence.contract_version == ("asr-research-evidence-v1")
    assert result.research_evidence.input.upstream_operation_id == ("formal-operation:understand_document")
    assert result.research_evidence.status == "completed"
    assert result.research.sources[0].source_id.startswith("evidence_")
    assert captured["brief"]["summary"] == ("嘉宾讨论人工智能芯片的投资机会。")
