from __future__ import annotations

import sys
import hashlib
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import asr_uncertainty, subtitle_segmentation
from app.domains.video_localization.schemas import VideoLocalizationDraft, VideoLocalizationTranscriptEditOperation, VideoLocalizationTranscriptSegment
from app.schemas.asr_uncertainty import AsrUnconfirmedTextSpan
from tests.test_video_localization_subtitle_segmentation import _state


def _fixture(phrase="oddname"):
    state = _state([
        ("Normal", 1000, 1300, "asr_0001"), ("speech.", 1300, 1700, "asr_0001"),
        (phrase, 3000, 3400, "asr_0001"), ("appears.", 3400, 4000, "asr_0001"),
        ("Continue", 5500, 5900, "asr_0001"), ("safely.", 5900, 6300, "asr_0001"),
    ])
    state.segments[0].review_flags = ["asr_unresolved_text"]
    return state


def _warning(excerpt, issue="issue-1"):
    return {"code": "needs_confirmation", "issue_id": issue, "segment_id": "asr_0001",
            "excerpt": excerpt, "message": "保留当前文字，需复听。"}


def _cues(state):
    return subtitle_segmentation.cues_from_transcription(state, existing_cue_ids=set())


@pytest.mark.parametrize("phrase", ["interface", "princess"])
def test_explicit_saved_warning_does_not_spread_to_unrelated_sentences(phrase):
    state = _fixture(phrase)
    state.transcript_quality_cycle = {"warnings": [_warning(phrase)]}
    before = state.model_dump(mode="json")
    cues = _cues(state)
    assert [cue.en_subtitle_text for cue in cues if "asr_unresolved_text" in cue.quality_flags] == [f"{phrase} appears"]
    assert state.model_dump(mode="json") == before


def test_extra_needs_confirmation_is_not_hidden_by_existing_rejected_operation():
    state = _fixture()
    state.segments[0].review_operations = [VideoLocalizationTranscriptEditOperation(
        start_word_id="source_word_000003", end_word_id="source_word_000003", source_text="oddname",
        replacement_text="", reason="未确认名称。", confidence=0, status="rejected",
    )]
    state.transcript_quality_cycle = {"warnings": [_warning("Continue safely.")]}
    cues = _cues(state)
    assert [cue.en_subtitle_text for cue in cues if "asr_unresolved_text" in cue.quality_flags] == [
        "oddname appears", "Continue safely",
    ]


def _span(state, excerpt="oddname", **patch):
    return AsrUnconfirmedTextSpan(
        issue_id="issue-new", segment_id="asr_0001", excerpt=excerpt, source_task_id="review-op",
        segment_text_sha256=asr_uncertainty.transcript_segment_text_fingerprint(state.segments[0].raw_text),
        **patch,
    )


def test_new_typed_span_survives_roundtrip_without_changing_legacy_dump():
    state = _fixture()
    old = state.segments[0].model_dump(mode="json")
    assert "unconfirmed_text_spans" not in old
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    restored = VideoLocalizationTranscriptSegment.model_validate(old)
    assert digest(restored.model_dump(mode="json")) == digest(old)
    state.segments[0].unconfirmed_text_spans = [_span(state)]
    restored = type(state).model_validate(state.model_dump(mode="json"))
    assert restored.segments[0].unconfirmed_text_spans == state.segments[0].unconfirmed_text_spans
    projected = asr_uncertainty.project_asr_uncertainty(restored)
    assert projected.unresolved_word_ids == {"word_000003"}
    assert not projected.scope_unknown_word_ids


@pytest.mark.parametrize("stale", ["text", "segment"])
def test_stale_new_span_is_unknown_not_verified(stale):
    state = _fixture()
    span = _span(state)
    span = span.model_copy(update={"segment_text_sha256": "0" * 64} if stale == "text" else {"segment_id": "other"})
    state.segments[0].unconfirmed_text_spans = [span]
    projected = asr_uncertainty.project_asr_uncertainty(state)
    assert not projected.unresolved_word_ids
    assert projected.scope_unknown_word_ids == {word.word_id for word in state.words}


@pytest.mark.parametrize("source", ["typed", "rejected", "warning"])
def test_repeated_excerpt_never_silently_selects_first_occurrence(source):
    state = _fixture("Normal")
    if source == "typed":
        state.segments[0].unconfirmed_text_spans = [_span(state, "Normal")]
    elif source == "warning":
        state.transcript_quality_cycle = {"warnings": [_warning("Normal")]}
    else:
        state.segments[0].review_operations = [VideoLocalizationTranscriptEditOperation(
            start_word_id="old-1", end_word_id="old-1", source_text="Normal", replacement_text="",
            confidence=0, status="rejected",
        )]
    projected = asr_uncertainty.project_asr_uncertainty(state)
    assert not projected.unresolved_word_ids
    assert projected.scope_unknown_word_ids == {"word_000001", "word_000003"}
    if source == "typed":
        state.segments[0].unconfirmed_text_spans = [_span(state, "Normal", match_policy="all_occurrences")]
        projected = asr_uncertainty.project_asr_uncertainty(state)
        assert projected.unresolved_word_ids == {"word_000001", "word_000003"}
        assert not projected.scope_unknown_word_ids


def _notes(state, notes):
    state.transcript_quality_cycle = {"task_step_results": {"review_decisions_r1": {"debug": {"notes": notes}}}}


def test_complete_saved_note_merges_with_typed_warning_and_preserves_quotes():
    state = _fixture("Duke's")
    warning = _warning("Duke's")
    note = " ".join(f"{key}={value!r}" for key, value in warning.items())
    _notes(state, [note])
    state.transcript_quality_cycle["warnings"] = [_warning("Continue safely.", "issue-two")]
    projection = asr_uncertainty.project_asr_uncertainty(state)
    assert projection.unresolved_word_ids == {"word_000003", "word_000005", "word_000006"}
    assert not projection.scope_unknown_word_ids


@pytest.mark.parametrize("note", [
    "code='needs_confirmation' issue_id='issue' segment_id='asr_0001' excerpt='oddname'",
    "code='needs_confirmation' issue_id='issue' segment_id='asr_0001' excerpt='oddname...' message='truncated'",
    "code='needs_confirmation' issue_id='issue' segment_id='asr_0001' excerpt='oddname' message='ok' extra='bad'",
    "code='needs_confirmation' issue_id='issue' segment_id='asr_0001' excerpt=__import__('os').getcwd() message='bad'",
])
def test_truncated_or_nonliteral_notes_never_create_precise_scope(note):
    state = _fixture()
    _notes(state, [note])
    projected = asr_uncertainty.project_asr_uncertainty(state)
    assert not projected.unresolved_word_ids
    assert projected.scope_unknown_word_ids == {word.word_id for word in state.words}


def test_source_input_and_segmentation_share_flags_without_mutating_cues_or_timing():
    from app.domains.video_localization.localization_source import build_localization_source_input

    state = _fixture()
    state.transcript_quality_cycle = {"warnings": [_warning("oddname")]}
    cues = _cues(state)
    draft = VideoLocalizationDraft(transcription=state, cues=cues)
    expected = build_localization_source_input(draft)
    for cue in draft.cues:
        cue.quality_flags = sorted(set([*cue.quality_flags, "asr_unresolved_text"]))
    before = draft.model_dump(mode="json")
    actual = build_localization_source_input(draft)
    assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
    assert draft.model_dump(mode="json") == before
    assert ["asr_unresolved_text" in cue.quality_flags for cue in actual.cues] == [False, True, False]
    # Source lock already projects acoustic word timing; this change must not
    # alter that established projection or mutate the original display timing.
    assert [(cue.start_ms, cue.end_ms, cue.text, cue.source_word_ids) for cue in expected.cues] == [
        (cue.start_ms, cue.end_ms, cue.text, cue.source_word_ids) for cue in actual.cues
    ]


def test_review_decisions_produces_distinct_uncertainty_not_a_rejected_edit(monkeypatch):
    from app.domains.video_localization import review_decisions
    from app.services import llm_runtime
    from tests.test_video_localization_review_decisions import _request

    request = _request()
    request.issues = [request.issues[0].model_copy(update={"needs_confirmation": True, "proposed_replacement": ""})]
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"decisions": [{
        "issue_id": request.issues[0].issue_id, "accept": False, "replacement": "", "reason": "保留待核实。", "confidence": 0.8,
    }]})
    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(request)
    segment = result.updated_segments[0]
    assert segment.unconfirmed_text_spans[0].excerpt == request.issues[0].current_excerpt
    assert segment.unconfirmed_text_spans[0].segment_text_sha256 == asr_uncertainty.transcript_segment_text_fingerprint(segment.raw_text)
    assert not segment.review_operations
    assert result.decisions[0].outcome == "needs_confirmation"

    replay_request = request.model_copy(
        update={"segments": result.updated_segments},
        deep=True,
    )
    replay = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(replay_request)
    assert len(replay.updated_segments[0].unconfirmed_text_spans) == 1

    resolved_request = replay_request.model_copy(
        update={
            "segments": replay.updated_segments,
            "issues": [
                request.issues[0].model_copy(
                    update={
                        "needs_confirmation": False,
                        "proposed_replacement": "AI means less demand",
                    }
                )
            ],
        },
        deep=True,
    )
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: {"decisions": [{
        "issue_id": resolved_request.issues[0].issue_id,
        "accept": True,
        "replacement": "AI means less demand",
        "reason": "复听后已经确认。",
        "confidence": 0.99,
    }]})
    resolved = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(resolved_request)
    assert resolved.updated_segments[0].unconfirmed_text_spans == []
    assert "asr_unresolved_text" not in resolved.updated_segments[0].review_flags
    assert "asr_unresolved_scope_unknown" not in resolved.updated_segments[0].review_flags


def test_brief_scope_unknown_rule_does_not_claim_entire_cue_is_wrong():
    from app.domains.video_localization import localization_document_brief as brief
    from tests.test_video_localization_document_brief_adaptive_rules import _request

    request = _request(glossary=False, speakers=True, style=False, uncertainties=False, spoken=False,
                       unresolved=False, strategy="adaptive")
    request.source_lock.input.cues[0].quality_flags = ["asr_unresolved_scope_unknown"]
    rules = brief.build_document_brief_adaptive_rules(request)
    assert "preserve_unlocated_asr_uncertainty" in rules.rule_ids
    assert "prioritize_unresolved_asr_evidence" not in rules.rule_ids
    assert any("不表示该 cue 全部文字已被确认错误" in paragraph for paragraph in rules.paragraphs)
