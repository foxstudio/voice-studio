from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import transcript_boundary_issues  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationTranscriptSegment,
)


def _segment(index: int, text: str) -> VideoLocalizationTranscriptSegment:
    return VideoLocalizationTranscriptSegment(
        segment_id=f"asr_{index:04d}",
        start_ms=(index - 1) * 5_000,
        end_ms=index * 5_000,
        raw_text=text,
    )


def test_finds_question_boundary_before_cross_segment_connector_pair():
    findings = transcript_boundary_issues.find_transcript_boundary_issues(
        [
            _segment(
                11,
                "But now people ask what about those model companies because",
            ),
            _segment(
                12,
                "Although most of them are not public, we see more of them.",
            ),
        ],
        language="en",
    )

    assert len(findings) == 1
    assert findings[0].related_segment_ids == ["asr_0011", "asr_0012"]
    assert [
        (item.segment_id, item.current_excerpt, item.proposed_replacement)
        for item in findings[0].patches
    ] == [
        (
            "asr_0011",
            "what about those model companies because",
            "what about those model companies? Because",
        ),
        ("asr_0012", "Although", "although"),
    ]


def test_does_not_treat_valid_because_although_structure_as_question():
    findings = transcript_boundary_issues.find_transcript_boundary_issues(
        [
            _segment(1, "Demand remains strong because"),
            _segment(
                2,
                "although models are more efficient, usage keeps growing.",
            ),
        ],
        language="en",
    )

    assert findings == []


def test_does_not_report_question_boundary_after_it_was_repaired():
    findings = transcript_boundary_issues.find_transcript_boundary_issues(
        [
            _segment(
                11,
                "But now people ask what about those model companies? Because",
            ),
            _segment(
                12,
                "although most of them are not public, we see more of them.",
            ),
        ],
        language="en",
    )

    assert findings == []


def test_finds_decimal_split_across_adjacent_segments():
    findings = transcript_boundary_issues.find_transcript_boundary_issues(
        [
            _segment(17, "The total opportunity is a 2."),
            _segment(18, "8 trillion dollar market."),
            _segment(63, "Micron trades at 6."),
            _segment(64, "5 times forward earnings."),
        ],
        language="en",
    )

    assert [item.code for item in findings] == [
        "decimal_split_across_segments",
        "decimal_split_across_segments",
    ]
    assert [
        [
            (patch.current_excerpt, patch.proposed_replacement)
            for patch in item.patches
        ]
        for item in findings
    ] == [
        [("2.", "2.8"), ("8", "")],
        [("6.", "6.5"), ("5", "")],
    ]
