from __future__ import annotations

from pathlib import Path
import sys

from pydantic import ValidationError
import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_context_intent,
    localization_requirements,
    localization_source,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationTranscriptionState,
)


def _draft(*, include_brief: bool = True) -> VideoLocalizationDraft:
    quality_cycle = (
        {
            "document_brief": {
                "summary": "分析师讨论 AI 投资机会。",
                "logic": ["先说明市场回调", "再比较公司竞争优势"],
                "speaker_style": "专业、审慎、对话式",
                "entities": [
                    {
                        "name": "world",
                        "role": "上游识别出的罕见待核实词",
                        "needs_research": True,
                    }
                ],
            }
        }
        if include_brief
        else {}
    )
    return VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=900,
                en_subtitle_text="Hello world.",
                source_word_ids=["word_0001", "word_0002"],
                transcription_revision_id="revision_01",
            )
        ],
        transcription=VideoLocalizationTranscriptionState(
            revision_id="revision_01",
            language="en",
            source_track_id="original",
            source_audio_sha256="a" * 64,
            corrected_text="Hello world.",
            words=[
                VideoLocalizationAlignedWord(
                    word_id="word_0001",
                    segment_id="segment_0001",
                    text="Hello",
                    start_ms=0,
                    end_ms=400,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                ),
                VideoLocalizationAlignedWord(
                    word_id="word_0002",
                    segment_id="segment_0001",
                    text="world.",
                    start_ms=450,
                    end_ms=900,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                ),
            ],
            transcript_quality_cycle=quality_cycle,
        ),
        scene_context="两人科技访谈。",
    )


def _source(draft: VideoLocalizationDraft):
    return localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(draft)
    )


def _requirements():
    return localization_requirements.resolve_localization_requirements()


def test_context_intent_reuses_narrow_verified_document_context():
    draft = _draft()
    source = _source(draft)
    request = (
        localization_context_intent
        .build_localization_context_intent_input(
            draft,
            source_lock=source,
            upstream_operation_id="source_operation_01",
            requirements_profile=_requirements(),
            selection_source="project_default",
        )
    )
    result = (
        localization_context_intent
        .DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE.lock(request)
    )

    assert result.contract_version == "localization-context-intent-v2"
    assert result.source_fingerprint == source.source_fingerprint
    assert request.document_context.summary == "分析师讨论 AI 投资机会。"
    assert request.document_context.content_logic == [
        "先说明市场回调",
        "再比较公司竞争优势",
    ]
    assert request.document_context.speaker_style == "专业、审慎、对话式"
    assert [
        item.model_dump(mode="json")
        for item in request.document_context.source_uncertainties
    ] == [
        {
            "term": "world",
            "reason": "上游识别出的罕见待核实词",
            "source_cue_ids": ["cue_0001"],
        }
    ]
    assert request.document_context.provenance == "asr_document_brief"
    assert request.delivery_intent.target_language == "zh-Hans"
    assert request.delivery_intent.target_locale == "zh-CN"
    serialized = result.model_dump_json()
    assert "上游识别出的罕见待核实词" in serialized
    assert result.quality_summary.model_call_count == 0


def test_context_intent_falls_back_to_scene_context_without_model():
    draft = _draft(include_brief=False)
    source = _source(draft)
    request = (
        localization_context_intent
        .build_localization_context_intent_input(
            draft,
            source_lock=source,
            upstream_operation_id="source_operation_01",
            requirements_profile=_requirements(),
            selection_source="project_default",
        )
    )
    result = (
        localization_context_intent
        .DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE.lock(request)
    )

    assert request.document_context.summary == "两人科技访谈。"
    assert request.document_context.provenance == "project_scene_context"
    assert result.quality_summary.status == "passed"


def test_context_intent_changes_own_fingerprint_without_changing_source():
    draft = _draft()
    source = _source(draft)
    first_request = (
        localization_context_intent
        .build_localization_context_intent_input(
            draft,
            source_lock=source,
            upstream_operation_id="source_operation_01",
            requirements_profile=_requirements(),
            selection_source="project_default",
        )
    )
    second_request = first_request.model_copy(
        update={
            "delivery_intent": (
                first_request.delivery_intent.model_copy(
                    update={"audience": "中文金融从业者"}
                )
            )
        }
    )
    pipeline = (
        localization_context_intent
        .DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE
    )
    first = pipeline.lock(first_request)
    second = pipeline.lock(second_request)

    assert first.source_fingerprint == second.source_fingerprint
    assert (
        first.context_intent_fingerprint
        != second.context_intent_fingerprint
    )


def test_context_intent_fingerprint_ignores_runtime_operation_identity():
    draft = _draft()
    source = _source(draft)
    first_request = (
        localization_context_intent
        .build_localization_context_intent_input(
            draft,
            source_lock=source,
            upstream_operation_id="formal_operation_01:source",
            requirements_profile=_requirements(),
            selection_source="project_default",
        )
    )
    second_request = first_request.model_copy(
        update={
            "upstream_operation_id": "formal_retry_02:source",
        }
    )
    pipeline = (
        localization_context_intent
        .DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE
    )

    first = pipeline.lock(first_request)
    second = pipeline.lock(second_request)

    assert first != second
    assert (
        first.context_intent_fingerprint
        == second.context_intent_fingerprint
    )


def test_context_intent_contract_rejects_unknown_fields():
    draft = _draft()
    request = (
        localization_context_intent
        .build_localization_context_intent_input(
            draft,
            source_lock=_source(draft),
            upstream_operation_id="source_operation_01",
            requirements_profile=_requirements(),
            selection_source="project_default",
        )
    )
    with pytest.raises(ValidationError):
        localization_context_intent.LocalizationContextIntentInput(
            **request.model_dump(mode="json"),
            unknown=True,
        )


def test_context_intent_projection_is_chinese_and_debuggable():
    draft = _draft()
    result = (
        localization_context_intent
        .DEFAULT_LOCALIZATION_CONTEXT_INTENT_PIPELINE.lock(
            localization_context_intent
            .build_localization_context_intent_input(
                draft,
                source_lock=_source(draft),
                upstream_operation_id="source_operation_01",
                requirements_profile=_requirements(),
                selection_source="project_default",
            )
        )
    )
    projected = (
        localization_context_intent
        .project_localization_context_intent_step_result(result)
    )

    assert projected["label"] == "固定本次本土化要求"
    assert "中文字幕与配音台词" in projected["summary"]
    assert "项目默认配置" in projected["summary"]
    assert {
        item["label"] for item in projected["debug"]["metrics"]
    } >= {"上下文来源", "模型调用"}


def test_default_requirements_are_plain_language_and_ui_ready():
    profile = _requirements()

    assert profile.deliverables.label == "中文字幕与配音台词"
    assert profile.deliverables.rendered_audio is False
    assert profile.timing.label == "按画面语义时间对应"
    assert profile.timing.source_cue_alignment_required is False
    assert profile.source_reference.asr_document_brief_usage == (
        "reference_only"
    )
    assert profile.source_reference.full_source_reanalysis_required is True
