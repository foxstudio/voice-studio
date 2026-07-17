from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import subtitle_segmentation  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationBoundaryReview,
    VideoLocalizationCue,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)


def _state(tokens: list[tuple[str, int, int, str]]) -> VideoLocalizationTranscriptionState:
    segment_ids = list(dict.fromkeys(item[3] for item in tokens))
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id=segment_id,
            start_ms=min(item[1] for item in tokens if item[3] == segment_id),
            end_ms=max(item[2] for item in tokens if item[3] == segment_id),
            raw_text=" ".join(item[0] for item in tokens if item[3] == segment_id),
        )
        for segment_id in segment_ids
    ]
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:06d}",
            segment_id=segment_id,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, (text, start_ms, end_ms, segment_id) in enumerate(tokens, start=1)
    ]
    return VideoLocalizationTranscriptionState(
        language="en",
        engine_id="test-asr",
        raw_text=" ".join(item[0] for item in tokens),
        corrected_text=" ".join(item[0] for item in tokens),
        segments=segments,
        words=words,
        review_status="completed",
        alignment_status="completed",
        timing_confidence="high",
    )


def test_segmentation_prefers_sentence_and_pause_boundaries_without_losing_words():
    state = _state(
        [
            ("This", 0, 280, "asr_0001"),
            ("is", 300, 500, "asr_0001"),
            ("one.", 520, 900, "asr_0001"),
            ("This", 1800, 2050, "asr_0002"),
            ("is", 2080, 2250, "asr_0002"),
            ("two.", 2280, 2700, "asr_0002"),
        ]
    )

    cues = subtitle_segmentation.cues_from_transcription(state, existing_cue_ids=set())

    assert [cue.en_subtitle_text for cue in cues] == ["This is one", "This is two"]
    assert [word_id for cue in cues for word_id in cue.source_word_ids] == [word.word_id for word in state.words]
    assert all(left.end_ms <= right.start_ms for left, right in zip(cues, cues[1:]))
    assert all(cue.timing_confidence == "high" for cue in cues)


def test_segmentation_can_merge_coarse_asr_segments_inside_one_sentence():
    state = _state(
        [
            ("We", 0, 300, "asr_0001"),
            ("shipped", 320, 900, "asr_0001"),
            ("the", 920, 1100, "asr_0002"),
            ("first", 1120, 1450, "asr_0002"),
            ("pass.", 1470, 1900, "asr_0002"),
        ]
    )

    cues = subtitle_segmentation.cues_from_transcription(state, existing_cue_ids=set())

    assert len(cues) == 1
    assert cues[0].en_subtitle_text == "We shipped the first pass"
    assert cues[0].source_word_ids == [word.word_id for word in state.words]


def test_fallback_boundaries_cover_all_words_once():
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:06d}",
            segment_id="asr_0001",
            text=f"w{index}",
            start_ms=(index - 1) * 8000,
            end_ms=(index - 1) * 8000 + 100,
        )
        for index in range(1, 38)
    ]

    boundaries = subtitle_segmentation._optimal_boundaries(words)

    assert boundaries[-1] == len(words)
    assert boundaries == sorted(set(boundaries))
    assert all(0 < boundary <= len(words) for boundary in boundaries)


def test_short_video_profile_produces_tighter_cues_and_records_provenance():
    state = _state([(f"word{index}", index * 300, index * 300 + 240, "asr_0001") for index in range(20)])

    generic = subtitle_segmentation.cues_from_transcription(state, existing_cue_ids=set(), profile_id="generic_zh")
    short = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
        profile_id="short_video_large_text",
    )

    assert len(short) >= len(generic)
    assert all("segmentation:short_video_large_text" in cue.quality_flags for cue in short)


def test_segmentation_never_splits_decimal_number_at_period():
    state = _state(
        [
            ("mixed", 0, 200, "asr_0001"),
            ("with", 220, 400, "asr_0001"),
            ("Seedance", 420, 780, "asr_0001"),
            ("2", 800, 900, "asr_0001"),
            (".", 900, 940, "asr_0001"),
            ("0", 940, 1040, "asr_0001"),
            ("in", 1060, 1180, "asr_0001"),
            ("4K", 1200, 1450, "asr_0001"),
            (".", 1450, 1500, "asr_0001"),
        ]
    )

    cues = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
        profile_id="short_video_large_text",
    )

    assert "2.0" in " ".join(cue.en_subtitle_text or "" for cue in cues)
    assert not any((cue.en_subtitle_text or "").endswith("2.") for cue in cues[:-1])


def test_segmentation_avoids_dependent_starters_and_incomplete_endings():
    profile = subtitle_segmentation.SubtitleSegmentationProfile(
        profile_id="syntax-test",
        max_words=5,
        target_words=4,
        max_duration_ms=10_000,
        min_duration_ms=100,
        max_source_chars=100,
        target_duration_ms=1600,
        candidate_audio_pause_ms=180,
        strong_audio_pause_ms=280,
    )
    cases = [
        (["The", "skill", "I", "use", "for", "every", "prompt", "is", "free."], 4),
        (["rebuilds", "the", "entire", "world", "around", "me."], 3),
        (["standing", "on", "the", "ground", "is", "now", "far", "above."], 5),
    ]

    for tokens, forbidden_boundary in cases:
        state = _state([(token, index * 400, index * 400 + 320, "asr_0001") for index, token in enumerate(tokens)])
        boundaries = subtitle_segmentation._optimal_boundaries(state.words, profile)
        assert forbidden_boundary not in boundaries[:-1]


def test_confirmed_audio_pause_influences_global_boundary_selection():
    state = _state([(f"word{index}", index * 500, index * 500 + 160, "asr_0001") for index in range(12)])
    left = state.words[5]
    right = state.words[6]
    state.audio_boundary_status = "completed"
    state.audio_boundary_analysis_version = "energy-pause-v1"
    state.audio_boundary_features = [
        VideoLocalizationAudioBoundaryEvidence(
            boundary_id=f"{left.word_id}:{right.word_id}",
            left_word_id=left.word_id,
            right_word_id=right.word_id,
            start_ms=left.end_ms,
            end_ms=right.start_ms,
            gap_ms=right.start_ms - left.end_ms,
            low_energy_ms=320,
            low_energy_ratio=0.9,
            gap_rms_dbfs=-48,
            speech_reference_dbfs=-18,
            noise_floor_dbfs=-55,
            energy_drop_db=30,
            confidence="high",
        )
    ]

    cues = subtitle_segmentation.cues_from_transcription(state, existing_cue_ids=set())

    assert cues[0].source_word_ids[-1] == left.word_id
    assert cues[1].source_word_ids[0] == right.word_id
    assert "boundary:audio-pause-high" in cues[0].quality_flags


def test_audio_pause_does_not_override_bad_break_semantics():
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:06d}",
            segment_id="asr_0001",
            text=text,
            start_ms=(index - 1) * 700,
            end_ms=(index - 1) * 700 + 220,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, text in enumerate(["This", "is", "the", "final", "result."], start=1)
    ]
    evidence = VideoLocalizationAudioBoundaryEvidence(
        boundary_id="word_000003:word_000004",
        left_word_id="word_000003",
        right_word_id="word_000004",
        start_ms=words[2].end_ms,
        end_ms=words[3].start_ms,
        gap_ms=words[3].start_ms - words[2].end_ms,
        low_energy_ms=360,
        low_energy_ratio=0.9,
        gap_rms_dbfs=-50,
        speech_reference_dbfs=-18,
        noise_floor_dbfs=-58,
        energy_drop_db=32,
        confidence="high",
    )

    bad_break_cost = subtitle_segmentation._segment_cost(
        words,
        0,
        3,
        audio_boundaries={(evidence.left_word_id, evidence.right_word_id): evidence},
        audio_analysis_available=True,
    )
    complete_phrase_cost = subtitle_segmentation._segment_cost(
        words,
        0,
        len(words),
        audio_boundaries={(evidence.left_word_id, evidence.right_word_id): evidence},
        audio_analysis_available=True,
    )

    assert bad_break_cost > complete_phrase_cost


def test_semantic_avoid_review_outweighs_confirmed_audio_pause():
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:06d}",
            segment_id="asr_0001",
            text=text,
            start_ms=(index - 1) * 700,
            end_ms=(index - 1) * 700 + 220,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, text in enumerate(["Each", "one", "bigger", "and", "better."], start=1)
    ]
    pair = (words[1].word_id, words[2].word_id)
    evidence = VideoLocalizationAudioBoundaryEvidence(
        boundary_id=f"{pair[0]}:{pair[1]}",
        left_word_id=pair[0],
        right_word_id=pair[1],
        start_ms=words[1].end_ms,
        end_ms=words[2].start_ms,
        gap_ms=words[2].start_ms - words[1].end_ms,
        low_energy_ms=450,
        low_energy_ratio=0.9,
        gap_rms_dbfs=-50,
        speech_reference_dbfs=-18,
        noise_floor_dbfs=-58,
        energy_drop_db=32,
        confidence="high",
    )
    review = VideoLocalizationBoundaryReview(
        boundary_id=f"{pair[0]}:{pair[1]}",
        left_word_id=pair[0],
        right_word_id=pair[1],
        decision="avoid",
        confidence=0.95,
        reason="modifier remains attached",
    )

    cost = subtitle_segmentation._segment_cost(
        words,
        0,
        2,
        audio_boundaries={pair: evidence},
        audio_analysis_available=True,
        boundary_reviews={pair: review},
    )
    cost_without_review = subtitle_segmentation._segment_cost(
        words,
        0,
        2,
        audio_boundaries={pair: evidence},
        audio_analysis_available=True,
    )

    assert cost > cost_without_review + 10


def test_high_confidence_avoid_review_is_never_selected_as_final_boundary():
    state = _state([(f"word{index}", index * 400, index * 400 + 300, "asr_0001") for index in range(12)])
    forbidden_end = 6
    left = state.words[forbidden_end - 1]
    right = state.words[forbidden_end]
    review = VideoLocalizationBoundaryReview(
        boundary_id=f"{left.word_id}:{right.word_id}",
        left_word_id=left.word_id,
        right_word_id=right.word_id,
        decision="avoid",
        confidence=0.95,
        reason="incomplete_syntax",
    )

    boundaries = subtitle_segmentation._optimal_boundaries(
        state.words,
        boundary_reviews={(left.word_id, right.word_id): review},
    )

    assert forbidden_end not in boundaries[:-1]
    assert boundaries[-1] == len(state.words)


def test_terminal_sentence_boundary_can_override_incorrect_avoid_review():
    state = _state(
        [
            ("This", 0, 300, "asr_0001"),
            ("ends.", 320, 700, "asr_0001"),
            ("Another", 720, 1100, "asr_0001"),
            ("sentence", 1120, 1500, "asr_0001"),
        ]
    )
    left = state.words[1]
    right = state.words[2]
    review = VideoLocalizationBoundaryReview(
        boundary_id=f"{left.word_id}:{right.word_id}",
        left_word_id=left.word_id,
        right_word_id=right.word_id,
        decision="avoid",
        confidence=0.95,
        reason="incorrect_incomplete_syntax",
    )

    assert subtitle_segmentation._boundary_forbidden_by_review(
        state.words,
        2,
        {(left.word_id, right.word_id): review},
    ) is False


def test_semantic_integrity_can_relax_word_target_instead_of_using_forbidden_boundary():
    state = _state([(f"word{index}", index * 400, index * 400 + 300, "asr_0001") for index in range(24)])
    reviews = {}
    for end in range(1, 19):
        left = state.words[end - 1]
        right = state.words[end]
        reviews[(left.word_id, right.word_id)] = VideoLocalizationBoundaryReview(
            boundary_id=f"{left.word_id}:{right.word_id}",
            left_word_id=left.word_id,
            right_word_id=right.word_id,
            decision="avoid",
            confidence=0.95,
            reason="incomplete_syntax",
        )

    boundaries = subtitle_segmentation._optimal_boundaries(
        state.words,
        boundary_reviews=reviews,
    )

    assert boundaries[0] > subtitle_segmentation.PROFILES[subtitle_segmentation.DEFAULT_PROFILE_ID].max_words
    assert all(boundary not in range(1, 19) for boundary in boundaries[:-1])
    assert boundaries[-1] == len(state.words)


def test_fallback_never_uses_forbidden_boundary_when_every_internal_split_is_avoided():
    state = _state([(f"word{index}", index * 400, index * 400 + 300, "asr_0001") for index in range(60)])
    reviews = {}
    for end in range(1, len(state.words)):
        left = state.words[end - 1]
        right = state.words[end]
        reviews[(left.word_id, right.word_id)] = VideoLocalizationBoundaryReview(
            boundary_id=f"{left.word_id}:{right.word_id}",
            left_word_id=left.word_id,
            right_word_id=right.word_id,
            decision="avoid",
            confidence=0.95,
            reason="single indivisible semantic unit",
        )

    boundaries = subtitle_segmentation._optimal_boundaries(state.words, boundary_reviews=reviews)

    assert boundaries == [len(state.words)]

    state = state.model_copy(update={"boundary_reviews": list(reviews.values())})
    cues = subtitle_segmentation.cues_from_transcription(state, existing_cue_ids=set())
    assert len(cues) == 1
    assert "segmentation_review_required" in cues[0].quality_flags


def test_generated_cue_postprocess_refines_early_start_pads_short_tail_and_applies_minimal_punctuation():
    cues = [
        VideoLocalizationCue(
            cue_id="cue_0001",
            start_ms=0,
            end_ms=900,
            en_subtitle_text="Hello,",
            source_word_ids=["word_000001"],
        ),
        VideoLocalizationCue(
            cue_id="cue_0002",
            start_ms=1300,
            end_ms=2100,
            en_subtitle_text="world.",
            source_word_ids=["word_000002"],
        ),
    ]

    refined = subtitle_segmentation.postprocess_generated_cues(
        cues,
        speech_onset_by_word_id={"word_000001": 140},
        continuous_pairs=set(),
    )

    assert refined[0].start_ms == 140
    assert refined[0].end_ms == 1080
    assert refined[0].end_ms < refined[1].start_ms
    assert refined[0].en_subtitle_text == "Hello"
    assert "timing:energy-onset-refined" in refined[0].quality_flags
    assert "timing:readability-tail" in refined[0].quality_flags
    assert "punctuation:minimal-style-normalized" in refined[0].quality_flags
    assert refined[-1].end_ms == 2100


def test_minimal_punctuation_keeps_questions_and_structural_marks():
    text = 'Version 2.0, costs 1,000. Ask "why?" Visit example.com!'

    normalized, changed = subtitle_segmentation._normalize_subtitle_punctuation(text)

    assert changed is True
    assert normalized == 'Version 2.0 costs 1,000 Ask "why?" Visit example.com'


def test_extend_continuous_short_pauses_aligns_to_next_start_and_keeps_last_cue_unchanged():
    cues = [
        VideoLocalizationCue(cue_id="cue_0001", start_ms=0, end_ms=800, en_subtitle_text="First"),
        VideoLocalizationCue(cue_id="cue_0002", start_ms=1000, end_ms=1700, en_subtitle_text="second"),
        VideoLocalizationCue(cue_id="cue_0003", start_ms=1900, end_ms=2600, en_subtitle_text="third"),
    ]

    extended = subtitle_segmentation.extend_continuous_short_pauses(
        cues,
        continuous_pairs={("cue_0001", "cue_0002"), ("cue_0002", "cue_0003")},
    )

    assert [cue.end_ms for cue in extended] == [1000, 1900, 2600]
    assert all(left.end_ms == right.start_ms for left, right in zip(extended, extended[1:]))
