from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import transcript_text_rules  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationTranscriptSegment,
)


def _segment(
    text: str,
    *,
    segment_id: str = "asr_0001",
) -> VideoLocalizationTranscriptSegment:
    return VideoLocalizationTranscriptSegment(
        segment_id=segment_id,
        start_ms=0,
        end_ms=1_000,
        raw_text=text,
    )


def test_normalize_orthography_repairs_spacing_without_changing_words():
    segments, changes = transcript_text_rules.normalize_transcript_orthography(
        [
            _segment(
                "It was priced$ 3 on one side,$ 15 on the other ,or 20 % more."
            )
        ]
    )

    assert segments[0].corrected_text == (
        "It was priced $3 on one side, $15 on the other, or 20% more."
    )
    assert segments[0].start_ms == 0
    assert segments[0].end_ms == 1_000
    assert segments[0].segment_id == "asr_0001"
    assert "asr_orthography_normalized" in segments[0].review_flags
    assert len(changes) == 1
    assert changes[0].before.startswith("It was priced$")
    assert changes[0].after.startswith("It was priced $3")


def test_normalize_orthography_does_not_rewrite_names_case_or_repetition():
    source = (
        "JoAnne discussed NVIDIA and Kimi K3. "
        "The the price became a massive Price."
    )

    segments, changes = transcript_text_rules.normalize_transcript_orthography(
        [_segment(source)]
    )

    assert (segments[0].corrected_text or segments[0].raw_text) == source
    assert changes == []


def test_find_text_candidates_flags_repetition_redundancy_and_case_evidence():
    segments = [
        _segment(
            "The the products, which both of which improved, had a massive Price.",
            segment_id="asr_0001",
        ),
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0002",
            start_ms=1_000,
            end_ms=2_000,
            raw_text="The price later fell.",
        ),
    ]

    candidates = transcript_text_rules.find_transcript_text_candidates(
        segments,
        language="en",
    )

    by_code = {item.code: item for item in candidates}
    assert by_code["adjacent_repetition"].current_excerpt == "The the"
    assert by_code["adjacent_repetition"].proposed_replacement == "The"
    assert by_code["redundant_relative_phrase"].current_excerpt == (
        "which both of which"
    )
    assert by_code["redundant_relative_phrase"].proposed_replacement == (
        "both of which"
    )
    assert by_code["inconsistent_internal_capitalization"].current_excerpt == (
        "Price"
    )
    assert by_code[
        "inconsistent_internal_capitalization"
    ].proposed_replacement == "price"


def test_find_text_candidates_detects_repetition_after_other_words():
    candidates = transcript_text_rules.find_transcript_text_candidates(
        [_segment("I think they're they're so called.")],
        language="en",
    )

    repeated = next(
        item for item in candidates if item.code == "adjacent_repetition"
    )
    assert repeated.current_excerpt == "they're they're"
    assert repeated.proposed_replacement == "they're"


def test_find_text_candidates_routes_repeated_phonetic_term_outliers_for_review():
    segments = [
        _segment(
            "The scale I use for every prompt is free.",
            segment_id="asr_0001",
        ),
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0002",
            start_ms=1_000,
            end_ms=2_000,
            raw_text=(
                "Turn on the skill. The skill writes the prompt. "
                "This skill is free, and the skill does the heavy lifting."
            ),
        ),
    ]

    candidates = transcript_text_rules.find_transcript_text_candidates(
        segments,
        language="en",
    )

    candidate = next(
        item
        for item in candidates
        if item.code == "phonetic_term_outlier"
    )
    assert candidate.segment_id == "asr_0001"
    assert candidate.current_excerpt == "scale"
    assert candidate.proposed_replacement == "skill"
    assert candidate.confidence < 0.8


def test_find_text_candidates_does_not_lower_possible_missing_sentence_starts():
    segments = [
        _segment(
            "More supply comes on Why? There's a reason But I disagree.",
        ),
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0002",
            start_ms=1_000,
            end_ms=2_000,
            raw_text="why there but",
        ),
    ]

    candidates = transcript_text_rules.find_transcript_text_candidates(
        segments,
        language="en",
    )

    assert not any(
        item.code == "inconsistent_internal_capitalization"
        for item in candidates
    )
