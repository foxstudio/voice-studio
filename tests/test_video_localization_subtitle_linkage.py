from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationSubtitleCue,
)
from app.domains.video_localization.subtitle_linkage import (  # noqa: E402
    SourceCueChange,
    SourceCueReplacement,
    audit_subtitle_linkages,
    localized_source_cue_ids,
    remap_localized_source_links,
)


def _cue(cue_id: str, *, words: list[str], speaker: str = "speaker_01") -> VideoLocalizationCue:
    return VideoLocalizationCue(
        cue_id=cue_id,
        speaker_id=speaker,
        start_ms=0,
        end_ms=1_000,
        source_word_ids=words,
    )


def _subtitle(
    subtitle_id: str,
    *,
    source_ids: list[str],
    words: list[str],
    linked_cue_id: str | None = None,
) -> VideoLocalizationSubtitleCue:
    return VideoLocalizationSubtitleCue(
        subtitle_id=subtitle_id,
        start_ms=0,
        end_ms=1_000,
        text="本土化台词",
        linked_cue_id=linked_cue_id,
        source_cue_ids=source_ids,
        source_word_ids=words,
    )


def test_canonical_source_ids_do_not_resurrect_stale_legacy_primary():
    subtitle = _subtitle(
        "localized_0001",
        source_ids=["cue_0002", "cue_0003"],
        words=[],
        linked_cue_id="deleted_cue",
    )

    assert localized_source_cue_ids(subtitle) == ("cue_0002", "cue_0003")


def test_remap_source_merge_deduplicates_ids_and_preserves_word_evidence():
    subtitle = _subtitle(
        "localized_0001",
        source_ids=["cue_0001", "cue_0002"],
        words=["word_01", "word_02"],
        linked_cue_id="cue_0001",
    )
    replacement = SourceCueReplacement("cue_merged", ("word_01", "word_02"))

    result = remap_localized_source_links(
        [subtitle],
        [
            SourceCueChange("cue_0001", ("word_01",), (replacement,)),
            SourceCueChange("cue_0002", ("word_02",), (replacement,)),
        ],
    )

    remapped = result.subtitles[0]
    assert remapped.source_cue_ids == ["cue_merged"]
    assert remapped.linked_cue_id == "cue_merged"
    assert remapped.source_word_ids == ["word_01", "word_02"]
    assert result.changed_subtitle_ids == ("localized_0001",)
    assert result.review_required_subtitle_ids == ()


def test_remap_source_split_uses_word_ids_for_one_to_many_links():
    first = _subtitle(
        "localized_0001",
        source_ids=["cue_parent"],
        words=["word_01"],
        linked_cue_id="cue_parent",
    )
    spanning = _subtitle(
        "localized_0002",
        source_ids=["cue_parent"],
        words=["word_01", "word_02"],
        linked_cue_id="cue_parent",
    )
    change = SourceCueChange(
        "cue_parent",
        ("word_01", "word_02"),
        (
            SourceCueReplacement("cue_left", ("word_01",)),
            SourceCueReplacement("cue_right", ("word_02",)),
        ),
    )

    result = remap_localized_source_links([first, spanning], [change])

    assert result.subtitles[0].source_cue_ids == ["cue_left"]
    assert result.subtitles[1].source_cue_ids == ["cue_left", "cue_right"]
    assert result.review_required_subtitle_ids == ()


def test_remap_source_split_without_word_evidence_keeps_all_children_for_review():
    subtitle = _subtitle(
        "localized_0001",
        source_ids=["cue_parent"],
        words=[],
        linked_cue_id="cue_parent",
    )
    change = SourceCueChange(
        "cue_parent",
        (),
        (
            SourceCueReplacement("cue_left", ("word_01",)),
            SourceCueReplacement("cue_right", ("word_02",)),
        ),
    )

    result = remap_localized_source_links([subtitle], [change])

    assert result.subtitles[0].source_cue_ids == ["cue_left", "cue_right"]
    assert result.review_required_subtitle_ids == ("localized_0001",)


def test_remap_source_delete_removes_owned_words_and_reports_orphan():
    subtitle = _subtitle(
        "localized_0001",
        source_ids=["cue_deleted"],
        words=["word_01", "word_02"],
        linked_cue_id="cue_deleted",
    )

    result = remap_localized_source_links(
        [subtitle],
        [SourceCueChange("cue_deleted", ("word_01", "word_02"), ())],
    )

    remapped = result.subtitles[0]
    assert remapped.source_cue_ids == []
    assert remapped.linked_cue_id is None
    assert remapped.source_word_ids == []
    assert result.orphaned_subtitle_ids == ("localized_0001",)


def test_audit_reports_missing_noncontiguous_cross_speaker_and_word_mismatch():
    cues = [
        _cue("cue_0001", words=["word_01"]),
        _cue("cue_0002", words=["word_02"]),
        _cue("cue_0003", words=["word_03"], speaker="speaker_02"),
    ]
    subtitle = _subtitle(
        "localized_0001",
        source_ids=["cue_0001", "cue_0003", "missing"],
        words=["word_01", "outside"],
        linked_cue_id="cue_0002",
    )

    audit = audit_subtitle_linkages(cues, [subtitle])[0]

    assert set(audit.codes) == {
        "source_cue_not_found",
        "legacy_primary_mismatch",
        "source_cues_noncontiguous",
        "source_cues_cross_speaker",
        "source_words_outside_linked_cues",
    }


def test_audit_detects_localized_split_that_cloned_the_same_source_evidence():
    cues = [_cue("cue_0001", words=["word_01", "word_02"])]
    copied_words = [
        _subtitle(
            "localized_0001",
            source_ids=["cue_0001"],
            words=["word_01", "word_02"],
            linked_cue_id="cue_0001",
        ),
        _subtitle(
            "localized_0002",
            source_ids=["cue_0001"],
            words=["word_01", "word_02"],
            linked_cue_id="cue_0001",
        ),
    ]

    audits = audit_subtitle_linkages(cues, copied_words)

    assert all("source_words_multiply_assigned" in audit.codes for audit in audits)


def test_audit_marks_shared_source_without_word_partition_for_review():
    cues = [_cue("cue_0001", words=[])]
    unpartitioned = [
        _subtitle(
            "localized_0001",
            source_ids=["cue_0001"],
            words=[],
            linked_cue_id="cue_0001",
        ),
        _subtitle(
            "localized_0002",
            source_ids=["cue_0001"],
            words=[],
            linked_cue_id="cue_0001",
        ),
    ]

    audits = audit_subtitle_linkages(cues, unpartitioned)

    assert all("shared_source_without_word_partition" in audit.codes for audit in audits)
