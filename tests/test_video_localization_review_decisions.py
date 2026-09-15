from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_targeted_relisten,
    development_checkpoints,
    review_decisions,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime  # noqa: E402


def _segment(
    index: int,
    text: str,
) -> VideoLocalizationTranscriptSegment:
    return VideoLocalizationTranscriptSegment(
        segment_id=f"asr_{index:04d}",
        start_ms=(index - 1) * 1_000,
        end_ms=index * 1_000,
        raw_text=text,
    )


def _request() -> review_decisions.AsrReviewDecisionsInput:
    return review_decisions.AsrReviewDecisionsInput(
        upstream_operation_id="section-review-operation",
        source_track_id="vocals",
        source_audio_sha256="source-sha256",
        language="en",
        profile_id="review-profile",
        round_index=1,
        document_summary="一段关于 AI 芯片需求的访谈。",
        content_logic=["讨论模型效率", "分析芯片需求"],
        speaker_style="专业访谈",
        segments=[
            _segment(
                1,
                "Better, more efficient AI less demand for hardware.",
            ),
            _segment(2, "Micron trades at six times future earnings."),
        ],
        issues=[
            {
                "issue_id": "issue-01",
                "section_id": "S1",
                "segment_id": "asr_0001",
                "current_excerpt": "AI less demand",
                "proposed_replacement": "AI means less demand",
                "reason": "可能漏掉 means。",
                "confidence": 0.82,
            },
            {
                "issue_id": "issue-02",
                "section_id": "S2",
                "segment_id": "asr_0002",
                "current_excerpt": "future earnings",
                "proposed_replacement": "forward earnings",
                "reason": "金融语境中可能是 forward earnings。",
                "confidence": 0.91,
            },
        ],
        upstream_status="completed",
    )


def test_review_decisions_applies_only_supplied_issue_and_streams_full_snapshot(
    monkeypatch,
):
    def fake_complete_json(*_args, **_kwargs):
        return {
            "decisions": [
                {
                    "issue_id": "issue-01",
                    "accept": True,
                    "replacement": "AI means less demand",
                    "reason": "结合句意可以确认漏掉 means。",
                    "confidence": 0.94,
                    "needs_confirmation": False,
                    "evidence_source_ids": [],
                },
                {
                    "issue_id": "issue-02",
                    "accept": False,
                    "replacement": "forward earnings",
                    "reason": "仅凭文本还不能确认，保留原文。",
                    "confidence": 0.72,
                    "needs_confirmation": False,
                    "evidence_source_ids": [],
                },
                {
                    "issue_id": "invented-issue",
                    "accept": True,
                    "replacement": "invented",
                    "reason": "不属于输入。",
                    "confidence": 0.99,
                },
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)
    snapshots: list[list[VideoLocalizationTranscriptSegment]] = []

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        _request(),
        on_segments_changed=lambda value: snapshots.append(value),
    )

    assert result.status == "completed"
    assert result.updated_segments[0].corrected_text == (
        "Better, more efficient AI means less demand for hardware."
    )
    assert result.updated_segments[1].corrected_text is None
    assert [item.outcome for item in result.decisions] == [
        "applied",
        "rejected",
    ]
    assert len(result.changes) == 1
    assert result.changes[0].issue_id == "issue-01"
    assert result.changes[0].source_task_id == "review_decisions_r1"
    assert result.cumulative_locked_changes[0].issue_id == "issue-01"
    assert (
        result.cumulative_locked_changes[0].source_task_id
        == "review_decisions_r1"
    )
    assert result.cumulative_locked_changes[0].round_index == 1
    assert len(snapshots) == 1
    assert len(snapshots[0]) == 2
    assert snapshots[0][0].corrected_text == (
        result.updated_segments[0].corrected_text
    )
    assert result.quality_summary.segment_ids_unchanged is True
    assert result.quality_summary.source_timing_unchanged is True
    assert [
        (item.start_ms, item.end_ms) for item in result.updated_segments
    ] == [(0, 1_000), (1_000, 2_000)]
    assert any(
        item.code == "invalid_decision"
        and item.issue_id == "invented-issue"
        for item in result.warnings
    )


def test_review_decisions_uses_compact_context_and_minimal_output(monkeypatch):
    payloads: list[dict] = []

    def fake_complete_json(*_args, **kwargs):
        payloads.append(kwargs["user_payload"])
        return {
            "decisions": [
                {
                    "issue_id": "issue-01",
                    "accept": True,
                    "reason": "上下文支持候选。",
                    "confidence": 0.94,
                },
                {
                    "issue_id": "issue-02",
                    "accept": False,
                    "reason": "证据不足，保留原文。",
                    "confidence": 0.75,
                },
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        _request()
    )

    assert "transcript" in payloads[0]["document"]
    assert "segments" not in payloads[0]["document"]
    assert "[asr_0001]" in payloads[0]["document"]["transcript"]
    assert set(payloads[0]["output"]["decisions"][0]) == {
        "issue_id",
        "accept",
        "reason",
        "confidence",
        "evidence_source_ids",
    }
    assert result.updated_segments[0].corrected_text == (
        "Better, more efficient AI means less demand for hardware."
    )


def test_review_decisions_keeps_short_audio_relisten_available_for_final_choice(
    monkeypatch,
):
    payloads: list[dict] = []
    request = _request().model_copy(
        update={
            "round_index": 2,
            "acoustic_candidates": [
                asr_targeted_relisten.AsrAcousticCandidate(
                    section_id="S1",
                    start_ms=0,
                    end_ms=2_000,
                    text=(
                        "Better, more efficient AI means less demand "
                        "for hardware."
                    ),
                )
            ],
        },
        deep=True,
    )

    def fake_complete_json(*_args, **kwargs):
        payloads.append(kwargs["user_payload"])
        return {
            "decisions": [
                {
                    "issue_id": "issue-01",
                    "accept": True,
                    "reason": "短音频重听支持候选。",
                    "confidence": 0.96,
                },
                {
                    "issue_id": "issue-02",
                    "accept": False,
                    "reason": "没有对应的声音证据。",
                    "confidence": 0.72,
                },
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)

    review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(request)

    assert payloads[0]["short_audio_relisten"][0]["section_id"] == "S1"
    assert payloads[0]["short_audio_relisten"][0]["issue_ids"] == []
    assert "AI means less demand" in payloads[0]["short_audio_relisten"][0]["text"]


def test_review_decisions_normalizes_safe_orthography_without_an_llm_call(
    monkeypatch,
):
    request = _request().model_copy(
        update={
            "issues": [],
            "segments": [
                _segment(1, "It was priced$ 3 ,or 20 % more."),
                _segment(2, "The the Price stayed high."),
            ],
        },
        deep=True,
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("empty issue review must not call the LLM")

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        fail_if_called,
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(request)

    assert result.status == "completed"
    assert result.updated_segments[0].corrected_text == (
        "It was priced $3, or 20% more."
    )
    assert result.updated_segments[1].corrected_text is None
    assert len(result.changes) == 1
    assert result.changes[0].issue_id == "orthography-1-asr_0001"
    assert result.llm_calls == []
    assert result.quality_summary.applied_change_count == 1


def test_review_decisions_applies_the_more_likely_candidate_without_human_pause(
    monkeypatch,
):
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "issue-01",
                    "accept": True,
                    "replacement": "AI means less demand",
                    "reason": "在两种候选中，补出 means 更符合原句语法。",
                    "confidence": 0.65,
                    "needs_confirmation": True,
                    "evidence_source_ids": [],
                },
                {
                    "issue_id": "issue-02",
                    "accept": False,
                    "replacement": "forward earnings",
                    "reason": "当前文本仍是更可能的原话。",
                    "confidence": 0.55,
                    "needs_confirmation": True,
                    "evidence_source_ids": [],
                },
            ]
        },
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        _request()
    )

    assert result.status == "completed"
    assert result.updated_segments[0].corrected_text == (
        "Better, more efficient AI means less demand for hardware."
    )
    assert result.updated_segments[1].corrected_text is None
    assert [item.outcome for item in result.decisions] == [
        "applied",
        "rejected",
    ]
    assert result.quality_summary.unresolved_issue_count == 0


def test_review_decisions_preserves_upstream_unresolved_text_signal(
    monkeypatch,
):
    request = _request().model_copy(
        update={
            "issues": [
                review_decisions.AsrReviewDecisionIssue(
                    issue_id="issue-unresolved",
                    section_id="S1",
                    segment_id="asr_0001",
                    current_excerpt="AI less demand",
                    proposed_replacement="",
                    reason="当前文字不成立，现有证据不足以还原原话。",
                    confidence=0.99,
                    needs_confirmation=True,
                )
            ]
        },
        deep=True,
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "issue-unresolved",
                    "accept": False,
                    "replacement": "",
                    "reason": "不能凭空猜写，保留当前最高概率文本。",
                    "confidence": 0.88,
                }
            ]
        },
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        request
    )

    assert result.status == "partial"
    assert result.decisions[0].outcome == "needs_confirmation"
    assert result.warnings[0].code == "needs_confirmation"
    assert result.warnings[0].segment_id == "asr_0001"
    assert "asr_unresolved_text" in (
        result.updated_segments[0].review_flags
    )
    assert result.quality_summary.unresolved_issue_count == 1


def test_review_decisions_rejects_replacement_not_proposed_by_review(
    monkeypatch,
):
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "issue-01",
                    "accept": True,
                    "replacement": "AI creates no demand",
                    "reason": "模型发明了另一种改法。",
                    "confidence": 0.99,
                }
            ]
        },
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        _request()
    )

    assert result.status == "partial"
    assert result.updated_segments[0].corrected_text is None
    assert result.decisions[0].outcome == "invalid"
    assert result.decisions[1].outcome == "invalid"
    assert result.quality_summary.applied_change_count == 0
    assert result.quality_summary.only_supplied_issues_considered is True


def test_apply_decisions_preserves_locked_change_and_allows_separate_fix():
    segments = [
        _segment(1, "Run Seedance 2.0 quicklyy."),
    ]
    segments[0] = segments[0].model_copy(
        update={"corrected_text": "Run Seedance 2.0 quicklyy."}
    )
    locked = [
        {
            "segment_id": "asr_0001",
            "before": "Run Cineon 2.0 quicklyy.",
            "after": "Run Seedance 2.0 quicklyy.",
            "reason": "资料确认规范名称。",
            "confidence": 0.98,
        }
    ]

    protected, protected_changes, protected_warnings = (
        review_decisions.apply_decisions(
            segments,
            [
                {
                    "issue_id": "issue-name",
                    "segment_id": "asr_0001",
                    "excerpt": "Seedance",
                    "accept": True,
                    "replacement": "Cineon",
                    "reason": "修改名称。",
                    "confidence": 0.98,
                }
            ],
            evidence=[],
            locked_changes=locked,
        )
    )
    corrected, changes, warnings = review_decisions.apply_decisions(
        segments,
        [
            {
                "issue_id": "issue-typo",
                "segment_id": "asr_0001",
                "excerpt": "quicklyy",
                "accept": True,
                "replacement": "quickly",
                "reason": "修正独立听写错误。",
                "confidence": 0.98,
            }
        ],
        evidence=[],
        locked_changes=locked,
    )

    assert protected[0].corrected_text == "Run Seedance 2.0 quicklyy."
    assert protected_changes == []
    assert protected_warnings[0]["code"] == "protected_change"
    assert corrected[0].corrected_text == "Run Seedance 2.0 quickly."
    assert len(changes) == 1
    assert warnings == []


def test_review_decisions_ignores_historical_lock_superseded_before_round():
    segment = _segment(
        1,
        "Use Runway now.",
    ).model_copy(
        update={"corrected_text": "Use Runway now."}
    )
    request = _request().model_copy(
        update={
            "round_index": 2,
            "segments": [segment],
            "issues": [],
            "locked_changes": [
                review_decisions.AsrLockedTranscriptChange(
                    segment_id="asr_0001",
                    before="Use Cineon now.",
                    after="Use Seedance now.",
                    reason="第一处修正规范名称。",
                    confidence=0.98,
                    round_index=1,
                ),
                review_decisions.AsrLockedTranscriptChange(
                    segment_id="asr_0001",
                    before="Use Seedance now.",
                    after="Use Runway now.",
                    reason="同轮后续证据确认最终名称。",
                    confidence=0.98,
                    round_index=1,
                ),
            ],
        }
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        request
    )

    assert result.status == "completed"
    assert result.updated_segments == [segment]
    assert result.quality_summary.locked_changes_preserved is True


def test_review_decisions_keeps_partial_upstream_visible(monkeypatch):
    request = _request().model_copy(
        update={
            "issues": [],
            "upstream_status": "partial",
            "upstream_warnings": [
                {
                    "code": "section_failed",
                    "section_id": "S2",
                    "message": "S2 没有完成。",
                }
            ],
        }
    )
    calls = 0

    def fake_complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete)
    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        request
    )

    assert calls == 0
    assert result.status == "partial"
    assert result.quality_summary.upstream_review_complete is False


def test_review_decisions_reports_one_warning_for_each_missing_decision(
    monkeypatch,
):
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {"decisions": []},
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        _request()
    )

    assert [
        (item.code, item.issue_id) for item in result.warnings
    ] == [
        ("missing_decision", "issue-01"),
        ("missing_decision", "issue-02"),
    ]


def test_review_decisions_retries_only_missing_issue_coverage(monkeypatch):
    payloads: list[dict] = []

    def fake_complete_json(*_args, **kwargs):
        payload = kwargs["user_payload"]
        payloads.append(payload)
        issue_ids = [
            item["issue_id"] for item in payload["candidate_issues"]
        ]
        if issue_ids == ["issue-01", "issue-02"]:
            return {
                "decisions": [
                    {
                        "issue_id": "issue-01",
                        "accept": False,
                        "replacement": "AI means less demand",
                        "reason": "保留原文。",
                        "confidence": 0.8,
                    }
                ]
            }
        assert issue_ids == ["issue-02"]
        return {
            "decisions": [
                {
                    "issue_id": "issue-02",
                    "accept": True,
                    "replacement": "forward earnings",
                    "reason": "金融语境支持该候选。",
                    "confidence": 0.95,
                }
            ]
        }

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        fake_complete_json,
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        _request()
    )

    assert len(payloads) == 2
    assert result.status == "completed"
    assert result.updated_segments[1].corrected_text == (
        "Micron trades at six times forward earnings."
    )
    assert not any(
        item.code == "missing_decision" for item in result.warnings
    )


def test_review_decisions_applies_adjacent_segment_patches_atomically(
    monkeypatch,
):
    request = review_decisions.AsrReviewDecisionsInput(
        contract_version="asr-review-decisions-input-v4",
        upstream_contract_version="asr-section-review-v4",
        upstream_operation_id="section-review-operation",
        source_track_id="vocals",
        source_audio_sha256="source-sha256",
        language="en",
        profile_id="review-profile",
        round_index=1,
        document_summary="一段关于模型公司的访谈。",
        segments=[
            _segment(
                11,
                "People ask what about those model companies because",
            ),
            _segment(
                12,
                "Although most of them are not public.",
            ),
        ],
        issues=[
            {
                "issue_id": "boundary-11-12",
                "section_id": "S1",
                "segment_id": "asr_0011",
                "scope": "adjacent_segments",
                "target_segment_ids": ["asr_0011", "asr_0012"],
                "current_excerpt": (
                    "what about those model companies because | Although"
                ),
                "proposed_replacement": (
                    "what about those model companies? Because | although"
                ),
                "reason": "问句与原因说明被错误粘连。",
                "confidence": 0.96,
                "origin": "deterministic_boundary_rule",
                "patches": [
                    {
                        "segment_id": "asr_0011",
                        "current_excerpt": (
                            "what about those model companies because"
                        ),
                        "proposed_replacement": (
                            "what about those model companies? Because"
                        ),
                    },
                    {
                        "segment_id": "asr_0012",
                        "current_excerpt": "Although",
                        "proposed_replacement": "although",
                    },
                ],
            }
        ],
        upstream_status="completed",
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "boundary-11-12",
                    "accept": True,
                    "replacement": (
                        "what about those model companies? Because | although"
                    ),
                    "reason": "跨段修改只调整结构，不改变原词。",
                    "confidence": 0.98,
                }
            ]
        },
    )
    snapshots: list[list[VideoLocalizationTranscriptSegment]] = []

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
        request,
        on_segments_changed=lambda value: snapshots.append(value),
    )

    assert result.status == "completed"
    assert result.updated_segments[0].corrected_text == (
        "People ask what about those model companies? Because"
    )
    assert result.updated_segments[1].corrected_text == (
        "although most of them are not public."
    )
    assert result.decisions[0].target_segment_ids == [
        "asr_0011",
        "asr_0012",
    ]
    assert result.decisions[0].outcome == "applied"
    assert len(result.changes) == 2
    assert len(snapshots) == 1


def test_review_decisions_rebalances_an_accepted_cross_segment_correction(
    monkeypatch,
):
    request = review_decisions.AsrReviewDecisionsInput(
        contract_version="asr-review-decisions-input-v4",
        upstream_contract_version="asr-section-review-v4",
        upstream_operation_id="section-review-operation",
        source_track_id="vocals",
        source_audio_sha256="source-sha256",
        language="en",
        profile_id="review-profile",
        round_index=2,
        document_summary="一段视频制作教程。",
        segments=[
            _segment(56, "But that was slow."),
            _segment(57, "Instead of camera movement."),
        ],
        issues=[
            {
                "issue_id": "boundary-56-57",
                "section_id": "S4",
                "segment_id": "asr_0056",
                "scope": "adjacent_segments",
                "target_segment_ids": ["asr_0056", "asr_0057"],
                "current_excerpt": (
                    "But that was slow. | Instead of camera movement."
                ),
                "proposed_replacement": (
                    "But that was a slow and steady camera movement. |"
                ),
                "reason": "短音频重听支持完整句子。",
                "confidence": 0.99,
                "origin": "llm_section_review",
                "patches": [
                    {
                        "segment_id": "asr_0056",
                        "current_excerpt": "But that was slow.",
                        "proposed_replacement": (
                            "But that was a slow and steady camera movement."
                        ),
                    },
                    {
                        "segment_id": "asr_0057",
                        "current_excerpt": "Instead of camera movement.",
                        "proposed_replacement": "",
                    },
                ],
            }
        ],
        upstream_status="completed",
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "boundary-56-57",
                    "accept": True,
                    "reason": "短音频重听与上下文一致。",
                    "confidence": 0.99,
                }
            ]
        },
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(request)

    corrected = [
        item.corrected_text for item in result.updated_segments
    ]
    assert all(corrected)
    assert " ".join(corrected) == (
        "But that was a slow and steady camera movement."
    )
    assert result.decisions[0].outcome == "applied"
    assert len(result.changes) == 2
    assert not result.warnings


def test_review_decisions_allows_decimal_punctuation_across_segments(
    monkeypatch,
):
    request = review_decisions.AsrReviewDecisionsInput(
        contract_version="asr-review-decisions-input-v4",
        upstream_contract_version="asr-section-review-v4",
        upstream_operation_id="section-review-operation",
        source_track_id="vocals",
        source_audio_sha256="source-sha256",
        language="en",
        profile_id="review-profile",
        round_index=2,
        document_summary="一段视频模型介绍。",
        segments=[
            _segment(1, "Created with Seedance 2."),
            _segment(2, "0 in 4K."),
        ],
        issues=[
            {
                "issue_id": "boundary-1-2",
                "section_id": "S1",
                "segment_id": "asr_0001",
                "scope": "adjacent_segments",
                "target_segment_ids": ["asr_0001", "asr_0002"],
                "current_excerpt": "Seedance 2. | 0 in 4K.",
                "proposed_replacement": "Seedance 2.0 | in 4K.",
                "reason": "小数点被片段边界拆开。",
                "confidence": 0.99,
                "origin": "deterministic_boundary_rule",
                "patches": [
                    {
                        "segment_id": "asr_0001",
                        "current_excerpt": "Seedance 2.",
                        "proposed_replacement": "Seedance 2.0",
                    },
                    {
                        "segment_id": "asr_0002",
                        "current_excerpt": "0 in 4K.",
                        "proposed_replacement": "in 4K.",
                    },
                ],
            }
        ],
        upstream_status="completed",
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "boundary-1-2",
                    "accept": True,
                    "reason": "只修正被切开的版本号标点。",
                    "confidence": 0.99,
                }
            ]
        },
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(request)

    assert [item.corrected_text for item in result.updated_segments] == [
        "Created with Seedance 2.0",
        "in 4K.",
    ]
    assert result.decisions[0].outcome == "applied"
    assert not result.warnings


def test_review_decisions_rolls_back_whole_patch_set_when_one_anchor_is_invalid(
    monkeypatch,
):
    request = review_decisions.AsrReviewDecisionsInput(
        contract_version="asr-review-decisions-input-v4",
        upstream_contract_version="asr-section-review-v4",
        upstream_operation_id="section-review-operation",
        source_track_id="vocals",
        source_audio_sha256="source-sha256",
        language="en",
        profile_id="review-profile",
        document_summary="一段数字讨论。",
        segments=[
            _segment(17, "The market is a 2."),
            _segment(18, "8 trillion dollars."),
        ],
        issues=[
            {
                "issue_id": "boundary-17-18",
                "section_id": "S1",
                "segment_id": "asr_0017",
                "scope": "adjacent_segments",
                "target_segment_ids": ["asr_0017", "asr_0018"],
                "current_excerpt": "2. | 8",
                "proposed_replacement": "2.8 |",
                "reason": "小数被片段边界拆开。",
                "confidence": 0.97,
                "origin": "deterministic_boundary_rule",
                "patches": [
                    {
                        "segment_id": "asr_0017",
                        "current_excerpt": "2.",
                        "proposed_replacement": "2.8",
                    },
                    {
                        "segment_id": "asr_0018",
                        "current_excerpt": "8",
                        "proposed_replacement": "9",
                    },
                ],
            }
        ],
        upstream_status="completed",
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "boundary-17-18",
                    "accept": True,
                    "replacement": "2.8 |",
                    "reason": "接受跨段小数修复。",
                    "confidence": 0.98,
                }
            ]
        },
    )

    result = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(request)

    assert [item.corrected_text for item in result.updated_segments] == [
        None,
        None,
    ]
    assert result.changes == []
    assert result.decisions[0].outcome == "needs_confirmation"


def test_review_decisions_rejects_timing_mutation(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "issue_id": "issue-01",
                    "accept": False,
                    "replacement": "AI means less demand",
                    "reason": "保留原文。",
                    "confidence": 0.9,
                }
            ]
        },
    )

    def mutate_timing(segments, *_args, **_kwargs):
        changed = [item.model_copy(deep=True) for item in segments]
        changed[0] = changed[0].model_copy(update={"start_ms": 10})
        return changed, [], []

    monkeypatch.setattr(
        review_decisions,
        "apply_decisions",
        mutate_timing,
    )
    prepared_snapshots: list[
        review_decisions.AsrReviewDecisionsPreparedSnapshot
    ] = []
    writer = (
        development_checkpoints.LocalizationDevelopmentCheckpointWriter(
            tmp_path,
            project_id="project-1",
            workflow_operation_id="operation-1",
        )
    )

    def capture_prepared(
        snapshot: (
            review_decisions.AsrReviewDecisionsPreparedSnapshot
        ),
    ) -> None:
        prepared_snapshots.append(snapshot)
        writer("review_decisions_r1_prepared", snapshot)

    with pytest.raises(
        ValueError,
        match="immutable transcript invariants",
    ):
        review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
            _request(),
            on_prepared_decisions=capture_prepared,
        )

    assert len(prepared_snapshots) == 1
    assert (
        prepared_snapshots[0].contract_version
        == "asr-review-decisions-prepared-snapshot-v1"
    )
    assert prepared_snapshots[0].request == _request()
    assert [item.issue_id for item in prepared_snapshots[0].decisions] == [
        "issue-01",
        "issue-02",
    ]
    persisted = development_checkpoints.load_development_checkpoint(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="operation-1",
        step_id="review_decisions_r1_prepared",
        result_model=(
            review_decisions.AsrReviewDecisionsPreparedSnapshot
        ),
    )
    assert persisted == prepared_snapshots[0]


def test_review_decisions_contract_import_has_no_legacy_cycle():
    script = f"""
import sys
sys.path.insert(0, {str(BACKEND)!r})
import app.domains.video_localization.review_decisions
assert 'app.domains.video_localization.entity_normalization' not in sys.modules
assert 'app.domains.video_localization.section_review' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
