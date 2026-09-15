from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_entity_normalization_operation_projection,
    asr_flow,
    asr_pipeline,
    entity_normalization,
    research_evidence,
    visual_name_evidence,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime  # noqa: E402


def _request() -> entity_normalization.AsrEntityNormalizationInput:
    return entity_normalization.AsrEntityNormalizationInput(
        upstream_contract_version="asr-research-evidence-v2",
        upstream_operation_id="research-op",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        language="en",
        profile_id="review-profile",
        document_summary="一段财经访谈。",
        segments=[
            research_evidence.AsrResearchEvidenceSegment(
                ordinal=1,
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1_000,
                text="Duan Feeny joins the program.",
                speaker_cluster_id="cluster_01",
            )
        ],
        candidates=[
            research_evidence.AsrResearchEvidenceCandidate(
                candidate_id="candidate_01",
                query="JoAnne Feeney Advisors Capital",
                category="proper_noun",
                reason="核对嘉宾姓名。",
                target_terms=["Duan Feeny"],
            )
        ],
        evidence=[
            research_evidence.AsrResearchEvidenceItem(
                evidence_id="evidence_01",
                candidate_id="candidate_01",
                query_run_id="query_01",
                title="JoAnne Feeney | Advisors Capital",
                url="https://example.com/joanne",
                snippet="JoAnne Feeney is a partner and portfolio manager.",
                provider="example",
                retrieved_at="2026-07-25T00:00:00Z",
                matched_target_terms=["JoAnne Feeney"],
            )
        ],
    )


def _visual_research_result(
    *,
    confidence: float = 0.95,
    frame_ids: list[str] | None = None,
) -> research_evidence.AsrResearchEvidenceResult:
    request = research_evidence.AsrResearchEvidenceInputV2(
        upstream_operation_id="understanding-op",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        language="en",
        profile_id="review-profile",
        document_summary="一段财经访谈。",
        segments=[
            research_evidence.AsrResearchEvidenceSegment(
                ordinal=1,
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1_000,
                text="Duan Feeny joins the program.",
                speaker_cluster_id="cluster_01",
            )
        ],
        candidates=[
            research_evidence.AsrResearchEvidenceCandidate(
                candidate_id="candidate_01",
                query="Duan Feeny Advisors Capital",
                category="proper_noun",
                reason="核对嘉宾姓名。",
                target_terms=["Duan Feeny"],
            )
        ],
        visual_evidence_operation_id="visual-op",
        visual_hints=[
            research_evidence.AsrResearchVisualHint(
                hint_id="visual_hint_01",
                candidate_id="candidate_01",
                question_id="visual_01",
                search_terms=[
                    "JoAnne Feeney",
                    "Advisors Capital",
                ],
                visible_text=[
                    "JoAnne Feeney",
                    "ADVISORS CAPITAL",
                    "PARTNER & PORTFOLIO MANAGER",
                ],
                frame_ids=(
                    ["frame_01"]
                    if frame_ids is None
                    else frame_ids
                ),
                confidence=confidence,
            )
        ],
    )
    return research_evidence.AsrResearchEvidenceResult(
        contract_version="asr-research-evidence-v2",
        input=request,
        status="partial",
        stop_reason="no_progress",
        profile_id="review-profile",
        model_id="review-model",
        unresolved_candidate_ids=["candidate_01"],
        stage_timing={"duration_ms": 80},
        quality_summary={
            "status": "warning",
            "candidate_count": 1,
            "supported_candidate_count": 0,
            "unresolved_candidate_count": 1,
            "failed_query_count": 0,
            "total_query_count": 1,
            "evidence_count": 0,
            "round_count": 1,
            "source_text_unchanged": True,
        },
    )


def test_entity_normalization_has_versioned_atomic_contract(monkeypatch):
    request = _request()
    captured: dict[str, object] = {}

    def fake_normalize(**kwargs):
        captured.update(kwargs)
        updated = kwargs["segments"][0].model_copy(
            update={"corrected_text": "JoAnne Feeney joins the program."}
        )
        return (
            [updated],
            [
                {
                    "canonical_name": "JoAnne Feeney",
                    "variants": ["Duan Feeny"],
                    "role": "嘉宾",
                    "confidence": 0.96,
                    "evidence_source_ids": ["evidence_01"],
                }
            ],
            [
                {
                    "issue_id": "",
                    "segment_id": "asr_0001",
                    "before": "Duan Feeny joins the program.",
                    "after": "JoAnne Feeney joins the program.",
                    "reason": "资料与画面均确认规范姓名。",
                    "confidence": 0.96,
                    "evidence_source_ids": ["evidence_01"],
                    "replacement": "JoAnne Feeney",
                }
            ],
            [],
        )

    monkeypatch.setattr(
        asr_flow,
        "normalize_researched_entities",
        fake_normalize,
    )

    result = entity_normalization.DEFAULT_ENTITY_NORMALIZATION_SERVICE.run(
        request
    )

    assert request.contract_version == "asr-entity-normalization-input-v1"
    assert result.contract_version == "asr-entity-normalization-v1"
    assert result.status == "completed"
    assert result.updated_segments[0].corrected_text == (
        "JoAnne Feeney joins the program."
    )
    assert result.resolutions[0].canonical_name == "JoAnne Feeney"
    assert result.changes[0].evidence_source_ids == ["evidence_01"]
    assert "issue_id" not in result.changes[0].model_dump()
    assert captured["document_summary"] == "一段财经访谈。"
    assert captured["candidates"][0]["target_terms"] == ["Duan Feeny"]
    assert captured["evidence"][0]["source_id"] == "evidence_01"


def test_entity_normalization_preserves_candidate_bound_visual_evidence():
    request = (
        entity_normalization.AsrEntityNormalizationInput
        .from_research_evidence(
            _visual_research_result(),
            upstream_operation_id="research-op",
        )
    )

    assert request.contract_version == (
        "asr-entity-normalization-input-v2"
    )
    assert request.visual_evidence_operation_id == "visual-op"
    assert len(request.visual_name_evidence) == 1
    claim = request.visual_name_evidence[0]
    assert claim.candidate_id == "candidate_01"
    assert claim.transcript_variant == "Duan Feeny"
    assert claim.visible_name == "JoAnne Feeney"
    assert claim.frame_ids == ["frame_01"]


def test_high_confidence_visual_name_can_drive_normalization_without_web_evidence(
    monkeypatch,
):
    request = (
        entity_normalization.AsrEntityNormalizationInput
        .from_research_evidence(
            _visual_research_result(),
            upstream_operation_id="research-op",
        )
    )
    captured: dict[str, object] = {}

    def fake_normalize(**kwargs):
        captured.update(kwargs)
        return kwargs["segments"], [], [], []

    monkeypatch.setattr(
        asr_flow,
        "normalize_researched_entities",
        fake_normalize,
    )

    result = entity_normalization.EntityNormalizationService().run(
        request
    )

    assert result.status == "completed"
    assert len(captured["evidence"]) == 1
    visual_item = captured["evidence"][0]
    assert visual_item["source_id"].startswith(
        "visual_name:visual_hint_01:"
    )
    assert visual_item["source_type"] == "visual_text"
    assert visual_item["canonical_text"] == "JoAnne Feeney"
    assert visual_item["candidate_target_terms"] == ["Duan Feeny"]
    assert visual_item["frame_ids"] == ["frame_01"]


def test_unqualified_visual_hint_does_not_trigger_name_normalization(
    monkeypatch,
):
    request = (
        entity_normalization.AsrEntityNormalizationInput
        .from_research_evidence(
            _visual_research_result(
                confidence=0.70,
                frame_ids=[],
            ),
            upstream_operation_id="research-op",
        )
    )
    called = False

    def fake_normalize(**_kwargs):
        nonlocal called
        called = True
        return [], [], [], []

    monkeypatch.setattr(
        asr_flow,
        "normalize_researched_entities",
        fake_normalize,
    )

    result = entity_normalization.EntityNormalizationService().run(
        request
    )

    assert result.status == "not_needed"
    assert called is False


def test_visual_name_evidence_rejects_unrelated_screen_text():
    result = _visual_research_result()
    visual_input = result.input
    assert isinstance(
        visual_input,
        research_evidence.AsrResearchEvidenceInputV2,
    )
    visual_input.visual_hints[0].visible_text = [
        "Bloomberg Tech",
        "PARTNER & PORTFOLIO MANAGER",
        "NASDAQ 100",
        "10.50%",
    ]

    request = (
        entity_normalization.AsrEntityNormalizationInput
        .from_research_evidence(
            result,
            upstream_operation_id="research-op",
        )
    )

    assert request.visual_name_evidence == []


def test_visual_name_evidence_rejects_added_or_changed_numbers():
    assert (
        visual_name_evidence._name_match_basis(
            "Nasdaq",
            "Nasdaq 100",
        )
        is None
    )
    assert (
        visual_name_evidence._name_match_basis(
            "Capital Management",
            "ADVISORS CAPITAL",
        )
        is None
    )
    assert (
        visual_name_evidence._name_match_basis(
            "Kimi K2",
            "Kimi K3",
        )
        is None
    )


def test_visual_resolution_requires_exact_screen_name_and_bound_variant():
    evidence = [
        {
            "source_id": "visual_name:01",
            "source_type": "visual_text",
            "canonical_text": "JoAnne Feeney",
            "candidate_target_terms": ["Duan Feeny"],
        }
    ]

    assert asr_flow._visual_evidence_supports_resolution(
        "JoAnne Feeney",
        ["Duan Feeny"],
        ["visual_name:01"],
        evidence,
    )
    assert not asr_flow._visual_evidence_supports_resolution(
        "JoAnne",
        ["Duan Feeny"],
        ["visual_name:01"],
        evidence,
    )
    assert not asr_flow._visual_evidence_supports_resolution(
        "JoAnne Feeney",
        ["Capital Management"],
        ["visual_name:01"],
        evidence,
    )


def test_entity_normalization_summary_links_cited_visual_frame():
    request = (
        entity_normalization.AsrEntityNormalizationInput
        .from_research_evidence(
            _visual_research_result(),
            upstream_operation_id="research-op",
        )
    )
    claim = request.visual_name_evidence[0]
    result = entity_normalization.AsrEntityNormalizationResult(
        input=request,
        status="completed",
        profile_id="review-profile",
        resolutions=[
            entity_normalization.AsrEntityResolution(
                canonical_name="JoAnne Feeney",
                variants=["Duan Feeny"],
                confidence=0.95,
                evidence_source_ids=[claim.evidence_id],
            )
        ],
        updated_segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1_000,
                raw_text="Duan Feeny joins the program.",
                corrected_text="JoAnne Feeney joins the program.",
            )
        ],
        duration_ms=10,
    )

    step_result = (
        asr_entity_normalization_operation_projection.step_result(
        result,
        project_id="project-01",
        )
    )
    links = step_result["sections"][0]["items"][0]["links"]

    assert links == [
        {
            "title": "查看姓名画面证据",
            "url": (
                "/api/projects/project-01/video-localization/"
                "operations/visual-op/"
                "development-visual-evidence-frames/frame_01"
            ),
            "meta": "可信度 95%",
            "text": "Duan Feeny → JoAnne Feeney",
        }
    ]


def test_pipeline_entity_normalization_publishes_the_same_complete_cues(
    monkeypatch,
):
    pipeline = asr_pipeline.AsrPipeline()
    request = _request()
    previews: list[tuple[str, list[dict]]] = []
    result = entity_normalization.AsrEntityNormalizationResult(
        input=request,
        status="completed",
        profile_id=request.profile_id,
        updated_segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=120,
                end_ms=980,
                raw_text="Duan Feeny joins the program.",
                corrected_text="JoAnne Feeney joins the program.",
            )
        ],
        duration_ms=10,
    )
    monkeypatch.setattr(
        entity_normalization.DEFAULT_ENTITY_NORMALIZATION_SERVICE,
        "run",
        lambda _request, **_kwargs: result,
    )

    actual = pipeline.run_entity_normalization(
        request,
        context=asr_pipeline.AsrRunContext(
            on_preview=lambda phase, cues: previews.append((phase, cues))
        ),
    )

    assert actual is result
    assert previews == [
        (
            "text_review",
            [
                {
                    "cue_id": "asr_0001",
                    "start_ms": 120,
                    "end_ms": 980,
                    "text": "JoAnne Feeney joins the program.",
                }
            ],
        )
    ]


def test_entity_normalization_records_each_model_call_without_prompt_text(
    monkeypatch,
):
    request = _request()

    def emit_trace(factory, *, model_id, tokens, cost):
        factory(1)(
            llm_runtime.LlmCompletionTrace(
                profile_id="review-profile",
                model_id=model_id,
                provider_host="openrouter.ai",
                request_chars=4_200,
                request_body_bytes=4_800,
                max_tokens=3_000,
                timeout_seconds=120,
                reasoning_effort_requested=None,
                reasoning_control_applied=False,
                duration_ms=1_200,
                finish_reason="stop",
                native_finish_reason="stop",
                prompt_tokens=tokens,
                cached_tokens=0,
                completion_tokens=120,
                reasoning_tokens=20,
                total_tokens=tokens + 120,
                cost_usd=cost,
                content_chars=300,
                reasoning_chars=80,
                response_id=f"response-{model_id}",
            )
        )

    def fake_normalize(**kwargs):
        emit_trace(
            kwargs["resolution_trace_sink_factory"],
            model_id="moonshotai/kimi-k3",
            tokens=1_000,
            cost=0.01,
        )
        emit_trace(
            kwargs["variant_trace_sink_factory"],
            model_id="moonshotai/kimi-k3",
            tokens=1_500,
            cost=0.02,
        )
        return kwargs["segments"], [], [], []

    monkeypatch.setattr(
        asr_flow,
        "normalize_researched_entities",
        fake_normalize,
    )

    result = entity_normalization.EntityNormalizationService().run(request)

    assert result.model_id == "moonshotai/kimi-k3"
    assert [item.purpose for item in result.llm_calls] == [
        "entity_resolution",
        "entity_variant_mapping",
    ]
    assert [item.prompt_tokens for item in result.llm_calls] == [1_000, 1_500]
    assert [item.cost_usd for item in result.llm_calls] == [0.01, 0.02]
    serialized = result.model_dump(mode="json")
    assert all(
        key not in serialized["llm_calls"][0]
        for key in {"prompt", "output", "content", "reasoning"}
    )


def test_entity_normalization_never_replaces_a_price_with_a_model_name(
    monkeypatch,
):
    segment = VideoLocalizationTranscriptSegment(
        segment_id="asr_0018",
        start_ms=113_569,
        end_ms=121_795,
        raw_text=(
            "trillion parameter model that's priced $3 per million tokens "
            "on the input side, $15 per million on the output side."
        ),
    )
    monkeypatch.setattr(
        asr_flow,
        "_resolve_researched_entities",
        lambda *_args, **_kwargs: [
            {
                "canonical_name": "Kimi K3",
                "variants": ["$3 per million tokens"],
                "role": "模型名称",
                "confidence": 0.95,
                "evidence_source_ids": ["evidence_01"],
            }
        ],
    )

    updated, _resolutions, changes, warnings = (
        asr_flow.normalize_researched_entities(
            segments=[segment],
            document_summary="讨论 Kimi K3 的价格。",
            candidates=[
                {
                    "category": "proper_noun",
                    "reason": "核对模型名称与价格。",
                    "target_terms": [
                        "Kimi K3",
                        "$3 per million tokens",
                    ],
                }
            ],
            evidence=[
                {
                    "source_id": "evidence_01",
                    "title": "Kimi K3 pricing",
                    "snippet": "Kimi K3 costs $3 per million input tokens.",
                }
            ],
            profile_id="review-profile",
        )
    )

    assert updated[0].corrected_text is None
    assert changes == []
    assert warnings == []
