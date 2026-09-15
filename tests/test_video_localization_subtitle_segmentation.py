from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    source_boundary_evidence,
    subtitle_segmentation,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationBoundaryReview,
    VideoLocalizationCue,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTranscriptEditOperation,
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


def test_single_voice_auto_analysis_does_not_mark_every_cue_for_speaker_review():
    state = _state(
        [
            ("This", 0, 280, "asr_0001"),
            ("is", 300, 500, "asr_0001"),
            ("ready.", 520, 900, "asr_0001"),
        ]
    ).model_copy(
        update={
            "diarization_status": "completed",
            "speaker_clusters": [
                VideoLocalizationSpeakerCluster(
                    cluster_id="cluster_01",
                    source_label="S01",
                    source_engine_id="test-diarization",
                    start_ms=0,
                    end_ms=900,
                    duration_ms=900,
                    segment_count=1,
                )
            ],
        }
    )

    generated = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    assert generated[0].review_status == "ready"
    assert "needs_speaker_assignment" not in generated[0].quality_flags


def test_multi_voice_auto_analysis_marks_only_unassigned_cues_for_speaker_review():
    state = _state(
        [
            ("This", 0, 280, "asr_0001"),
            ("is", 300, 500, "asr_0001"),
            ("unassigned.", 520, 900, "asr_0001"),
        ]
    ).model_copy(
        update={
            "diarization_status": "completed",
            "speaker_clusters": [
                VideoLocalizationSpeakerCluster(
                    cluster_id=f"cluster_{index:02d}",
                    source_label=f"S{index:02d}",
                    source_engine_id="test-diarization",
                    start_ms=0,
                    end_ms=900,
                    duration_ms=900,
                    segment_count=1,
                )
                for index in (1, 2)
            ],
        }
    )

    generated = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    assert generated[0].review_status == "needs_review"
    assert "needs_speaker_assignment" in generated[0].quality_flags


def test_dash_is_soft_break_evidence_but_not_display_punctuation():
    with_dash = _state(
        [
            ("前半句——", 0, 800, "asr_0001"),
            ("后半句", 900, 1700, "asr_0001"),
        ]
    ).words
    without_dash = [
        word.model_copy(update={"text": word.text.replace("——", "")})
        for word in with_dash
    ]

    assert source_boundary_evidence._punctuation("前半句——") == "clause"
    assert subtitle_segmentation._segment_cost(
        with_dash,
        0,
        1,
    ) < subtitle_segmentation._segment_cost(
        without_dash,
        0,
        1,
    )
    assert subtitle_segmentation._normalize_subtitle_punctuation(
        "前半句——后半句"
    ) == ("前半句 后半句", True)


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


def test_clean_generated_cue_is_ready_without_losing_asr_provenance():
    state = _state(
        [
            ("This", 0, 300, "asr_0001"),
            ("is", 320, 500, "asr_0001"),
            ("ready.", 520, 900, "asr_0001"),
        ]
    )
    state = state.model_copy(
        update={
            "words": [
                word.model_copy(update={"speaker_cluster_id": "speaker_1"})
                for word in state.words
            ]
        }
    )

    cues = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    assert len(cues) == 1
    assert cues[0].review_status == "ready"
    assert "generated_by_asr" in cues[0].quality_flags
    assert "needs_speaker_assignment" not in cues[0].quality_flags


def test_segmentation_recomputes_character_limit_after_display_punctuation_cleanup():
    tokens = "Now, it doesn't watch the video the way you and I do.".split()
    state = _state(
        [
            (token, index * 180, index * 180 + 150, "asr_0001")
            for index, token in enumerate(tokens)
        ]
    )
    state = state.model_copy(
        update={
            "words": [
                word.model_copy(update={"speaker_cluster_id": "speaker_1"})
                for word in state.words
            ]
        }
    )

    cues = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    assert [cue.en_subtitle_text for cue in cues] == [
        "Now it doesn't watch the video the way you and I do"
    ]
    assert "segmentation_review_required" not in cues[0].quality_flags
    assert cues[0].review_status == "ready"


def test_segmentation_uses_strict_display_limits_when_a_semantic_partition_is_feasible():
    tokens = (
        "Seedance pulled the light straight from the generated "
        "environment and bounced it onto me."
    ).split()
    state = _state(
        [
            (token, index * 220, index * 220 + 180, "asr_0001")
            for index, token in enumerate(tokens)
        ]
    )
    profile = subtitle_segmentation.PROFILES[subtitle_segmentation.DEFAULT_PROFILE_ID]

    cues = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    assert [word_id for cue in cues for word_id in cue.source_word_ids] == [
        word.word_id for word in state.words
    ]
    assert all(len(cue.en_subtitle_text or "") <= profile.max_source_chars for cue in cues)
    assert all("segmentation_review_required" not in cue.quality_flags for cue in cues)
    assert [cue.en_subtitle_text for cue in cues] == [
        "Seedance pulled the light",
        "straight from the generated environment",
        "and bounced it onto me",
    ]
    assert not any(
        (cue.en_subtitle_text or "").casefold().endswith(tuple(subtitle_segmentation.BAD_BREAK_ENDINGS))
        for cue in cues[:-1]
    )


def test_segmentation_keeps_terminal_review_fragment_in_its_own_cue():
    state = _state(
        [
            ("Okay,", 23_064, 23_384, "asr_0006"),
            ("that's", 23_544, 23_704, "asr_0006"),
            ("the", 23_704, 23_784, "asr_0006"),
            ("whole", 23_784, 24_024, "asr_0006"),
            ("blueprint.", 24_024, 24_504, "asr_0006"),
            ("My", 24_504, 24_664, "asr_0007"),
            ("real", 24_664, 24_791, "asr_0007"),
        ]
    )
    state.segments[-1].review_flags = [
        "media_end_clipped",
        "terminal_fragment_review_required",
    ]

    cues = subtitle_segmentation.cues_from_transcription(state, existing_cue_ids=set())

    assert [cue.en_subtitle_text for cue in cues] == [
        "Okay that's the whole blueprint",
        "My real",
    ]
    assert "terminal_fragment_review_required" not in cues[0].quality_flags
    assert "terminal_fragment_review_required" in cues[1].quality_flags
    assert cues[-1].end_ms == 25_291


def test_segmentation_preserves_unlocated_asr_uncertainty_without_claiming_exact_scope():
    state = _state(
        [
            ("Unclear", 1_000, 1_400, "asr_0001"),
            ("speech", 1_400, 1_900, "asr_0001"),
        ]
    )
    state.segments[0].review_flags = ["asr_unresolved_text"]

    cues = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    assert len(cues) == 1
    assert "asr_unresolved_scope_unknown" in cues[0].quality_flags
    assert "asr_unresolved_text" not in cues[0].quality_flags


def test_segmentation_limits_unresolved_review_to_matching_words():
    state = _state(
        [
            ("Normal", 1_000, 1_300, "asr_0001"),
            ("speech.", 1_300, 1_700, "asr_0001"),
            ("Fartifact's", 3_000, 3_400, "asr_0001"),
            ("no", 3_400, 3_600, "asr_0001"),
            ("smell.", 3_600, 4_000, "asr_0001"),
            ("Continue", 5_500, 5_900, "asr_0001"),
            ("safely.", 5_900, 6_300, "asr_0001"),
        ]
    )
    state.segments[0].review_flags = ["asr_unresolved_text"]
    state.segments[0].review_operations = [
        VideoLocalizationTranscriptEditOperation(
            start_word_id="source_word_000003",
            end_word_id="source_word_000005",
            source_text="Fartifact's no smell.",
            replacement_text="",
            reason="现有证据不足以还原。",
            confidence=0.0,
            status="rejected",
        )
    ]

    cues = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    unresolved_cues = [
        cue
        for cue in cues
        if "asr_unresolved_text" in cue.quality_flags
    ]
    assert [cue.en_subtitle_text for cue in unresolved_cues] == [
        "Fartifact's no smell"
    ]


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


def test_existential_opening_stays_with_its_complement_across_long_pause():
    state = _state(
        [
            ("See", 0, 80, "asr_0001"),
            ("y'all", 80, 320, "asr_0001"),
            ("next", 320, 560, "asr_0001"),
            ("month,", 640, 960, "asr_0001"),
            ("you", 960, 1_040, "asr_0001"),
            ("old", 1_040, 1_360, "asr_0001"),
            ("farts.", 1_360, 1_760, "asr_0001"),
            ("There's", 1_760, 2_360, "asr_0001"),
            ("been", 12_800, 12_880, "asr_0002"),
            ("a", 12_880, 12_960, "asr_0002"),
            ("bomb", 13_040, 13_360, "asr_0002"),
            ("planted", 13_440, 13_760, "asr_0002"),
            ("in", 13_760, 13_920, "asr_0002"),
            ("the", 13_920, 14_000, "asr_0002"),
            ("cinema.", 14_000, 14_480, "asr_0002"),
        ]
    )
    left = state.words[7]
    right = state.words[8]
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
            low_energy_ms=3_200,
            low_energy_ratio=0.58,
            gap_rms_dbfs=-44,
            speech_reference_dbfs=-18,
            noise_floor_dbfs=-52,
            energy_drop_db=26,
            confidence="medium",
        )
    ]

    cues = subtitle_segmentation.cues_from_transcription(
        state,
        existing_cue_ids=set(),
    )

    assert [cue.en_subtitle_text for cue in cues] == [
        "See y'all next month you old farts",
        "There's been a bomb planted in the cinema",
    ]


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
    assert cues[0].review_status == "needs_review"


def test_generated_cue_postprocess_refines_entry_and_applies_shared_display_exit_timing():
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
        subtitle_entry_by_word_id={"word_000001": 140},
        frame_rate=25.0,
        media_duration_ms=2_500,
    )

    assert refined[0].start_ms == 60
    assert refined[0].end_ms == 1140
    assert refined[0].source_duration_ms == 840
    assert refined[0].end_ms < refined[1].start_ms
    assert refined[0].en_subtitle_text == "Hello"
    assert "timing:acoustic-entry-refined" in refined[0].quality_flags
    assert "timing:entry-lead-in" in refined[0].quality_flags
    assert "timing:display-exit-extended" in refined[0].quality_flags
    assert "punctuation:minimal-style-normalized" in refined[0].quality_flags
    assert refined[-1].end_ms == 2500


def test_generated_cue_entry_lead_in_never_overlaps_previous_cue():
    cues = [
        VideoLocalizationCue(
            cue_id="cue_0001",
            start_ms=0,
            end_ms=1000,
            en_subtitle_text="First",
            source_word_ids=["word_000001"],
        ),
        VideoLocalizationCue(
            cue_id="cue_0002",
            start_ms=1040,
            end_ms=1800,
            en_subtitle_text="Second",
            source_word_ids=["word_000002"],
        ),
    ]

    refined = subtitle_segmentation.postprocess_generated_cues(cues)

    assert refined[0].start_ms == 0
    assert refined[1].start_ms == 1000
    assert refined[0].end_ms <= refined[1].start_ms


def test_minimal_punctuation_removes_questions_and_unneeded_quotes():
    text = 'Version 2.0, costs 1,000. Ask "why?" Visit example.com!'

    normalized, changed = subtitle_segmentation._normalize_subtitle_punctuation(text)

    assert changed is True
    assert normalized == "Version 2.0 costs 1,000 Ask why Visit example.com!"


def test_generated_cues_keep_two_frames_before_the_next_entry():
    cues = [
        VideoLocalizationCue(cue_id="cue_0001", start_ms=0, end_ms=800, en_subtitle_text="First"),
        VideoLocalizationCue(cue_id="cue_0002", start_ms=1000, end_ms=1700, en_subtitle_text="second"),
        VideoLocalizationCue(cue_id="cue_0003", start_ms=1900, end_ms=2600, en_subtitle_text="third"),
    ]

    extended = subtitle_segmentation.postprocess_generated_cues(
        cues,
        frame_rate=25.0,
        media_duration_ms=3_000,
    )

    assert [cue.start_ms for cue in extended] == [0, 1000, 1900]
    assert [cue.end_ms for cue in extended] == [920, 1820, 3000]
