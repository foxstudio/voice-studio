from app.domains.video_localization.binding_repair import repair_bindings
from app.models.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSpokenSegment,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTranscriptionState,
)
from app.schemas.video_localization_binding_repair import BindingRepairRequest


def _draft():
    words = [
        VideoLocalizationAlignedWord(word_id=f"w{index}", segment_id="asr1", text=str(index), start_ms=index * 100, end_ms=(index + 1) * 100)
        for index in range(1, 10)
    ]
    cues = [
        VideoLocalizationCue(cue_id="c1", start_ms=100, end_ms=500, source_word_ids=["w1", "w2", "w3", "w4"]),
        VideoLocalizationCue(cue_id="c2", start_ms=500, end_ms=900, source_word_ids=["w5", "w6", "w7", "w8"]),
        VideoLocalizationCue(cue_id="c3", start_ms=900, end_ms=1000, source_word_ids=["w9"]),
    ]
    subtitles = [
        VideoLocalizationSubtitleCue(subtitle_id="left", text="左文案", tts_text="左朗读", start_ms=100, end_ms=900, linked_cue_id="c1", source_cue_ids=["c1", "c2"], source_word_ids=[f"w{i}" for i in range(1, 9)], spoken_segment_id="s1"),
        VideoLocalizationSubtitleCue(subtitle_id="right", text="右文案", tts_text="右朗读", start_ms=900, end_ms=1000, linked_cue_id="c3", source_cue_ids=["c3"], source_word_ids=["w9"], spoken_segment_id="s2"),
    ]
    return VideoLocalizationDraft(
        transcription=VideoLocalizationTranscriptionState(revision_id="tr1", source_track_id="vocals", alignment_source_track_id="vocals", source_audio_sha256="a" * 64, alignment_audio_sha256="a" * 64, words=words),
        cues=cues,
        localized_subtitles=subtitles,
        localized_spoken_segments=[
            VideoLocalizationSpokenSegment(segment_id="s1", paragraph_id="p1", text="旧左台词", start_ms=100, end_ms=900, source_cue_ids=["c1", "c2"], source_word_ids=[f"w{i}" for i in range(1, 9)]),
            VideoLocalizationSpokenSegment(segment_id="s2", paragraph_id="p2", text="旧右台词", start_ms=900, end_ms=1000, source_cue_ids=["c3"], source_word_ids=["w9"]),
        ],
    )


def _request(left, right):
    return BindingRepairRequest(expected_project_revision="r1", transcription_revision_id="tr1", audio_sha256="a" * 64, reason="boundary repair", bindings=[
        {"subtitle_id": "left", "source_word_ids": left},
        {"subtitle_id": "right", "source_word_ids": right},
    ])


def test_repair_moves_words_and_preserves_text_and_ids():
    original = _draft()
    result = repair_bindings(original, _request(["w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8", "w9"]))
    left, right = result.localized_subtitles
    assert left.source_word_ids == ["w1", "w2", "w3", "w4"]
    assert right.source_word_ids == ["w5", "w6", "w7", "w8", "w9"]
    assert (left.start_ms, left.end_ms, right.start_ms, right.end_ms) == (100, 500, 500, 1000)
    assert (left.text, left.tts_text, right.text, right.tts_text) == ("左文案", "左朗读", "右文案", "右朗读")
    assert [item.segment_id for item in result.localized_spoken_segments] == ["s1", "s2"]
    assert [item.text for item in result.localized_spoken_segments] == ["旧左台词", "旧右台词"]
    assert [(item.start_ms, item.end_ms) for item in result.localized_spoken_segments] == [(100, 500), (500, 1000)]


def test_repair_preserves_unmoved_subtitle_display_edge_gap():
    original = _draft()
    left, right = original.localized_subtitles
    original = original.model_copy(update={
        "localized_subtitles": [
            left.model_copy(update={"start_ms": 123}),
            right.model_copy(update={"end_ms": 1033}),
        ],
    })
    result = repair_bindings(original, _request(["w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8", "w9"]))
    left, right = result.localized_subtitles
    assert (left.start_ms, left.end_ms) == (123, 500)
    assert (right.start_ms, right.end_ms) == (500, 1033)


def test_repair_rejects_nonconserving_words():
    original = _draft()
    try:
        repair_bindings(original, _request(["w1", "w2"], ["w5", "w6", "w7", "w8", "w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_WORD_COVERAGE_INVALID"
    else:
        raise AssertionError("expected conservative repair rejection")


def test_repair_is_idempotent_when_binding_already_matches():
    original = _draft()
    request = _request([f"w{i}" for i in range(1, 9)], ["w9"])
    assert repair_bindings(original, request) is original


def test_repair_rejects_stale_stored_audio_before_idempotence():
    original = _draft()
    original.transcription = original.transcription.model_copy(update={"source_audio_sha256": "b" * 64})
    try:
        repair_bindings(original, _request([f"w{i}" for i in range(1, 9)], ["w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_AUDIO_CHANGED"
    else:
        raise AssertionError("expected audio rejection")


def test_repair_rejects_non_vocals_and_duplicate_cue_ownership():
    original = _draft()
    original.transcription = original.transcription.model_copy(update={"source_track_id": "original"})
    try:
        repair_bindings(original, _request([f"w{i}" for i in range(1, 9)], ["w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_VOCALS_REQUIRED"
    else:
        raise AssertionError("expected vocals rejection")

    original = _draft().model_copy(update={
        "cues": [*_draft().cues, VideoLocalizationCue(cue_id="duplicate", start_ms=500, end_ms=600, source_word_ids=["w5"])]
    })
    try:
        repair_bindings(original, _request(["w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8", "w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_CUE_OWNERSHIP_INVALID"
    else:
        raise AssertionError("expected cue ownership rejection")


def test_repair_rejects_existing_generated_dependency():
    original = _draft()
    original = original.model_copy(update={
        "localized_subtitles": [
            original.localized_subtitles[0].model_copy(update={"tts_result_id": "result"}),
            original.localized_subtitles[1],
        ]
    })
    try:
        repair_bindings(original, _request(["w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8", "w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_TTS_EXISTS"
    else:
        raise AssertionError("expected generated dependency rejection")


def test_repair_rejects_duplicate_transcription_word_and_parent_cue_dependency():
    original = _draft()
    original.transcription = original.transcription.model_copy(update={
        "words": [*original.transcription.words, original.transcription.words[4]],
    })
    try:
        repair_bindings(original, _request(["w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8", "w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_WORD_ID_DUPLICATE"
    else:
        raise AssertionError("expected duplicate word rejection")

    original = _draft().model_copy(update={"generated_candidates": [{"cue_id": "c1"}]})
    try:
        repair_bindings(original, _request(["w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8", "w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_CANDIDATE_EXISTS"
    else:
        raise AssertionError("expected parent cue dependency rejection")


def test_repair_rejects_overlap_at_changed_boundary():
    original = _draft()
    words = [
        word.model_copy(update={"end_ms": 650}) if word.word_id == "w4" else word
        for word in original.transcription.words
    ]
    original.transcription = original.transcription.model_copy(update={"words": words})
    try:
        repair_bindings(original, _request(["w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8", "w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_SUBTITLE_OVERLAP"
    else:
        raise AssertionError("expected changed-boundary overlap rejection")


def test_repair_rejects_noncontinuous_word_window():
    original = _draft()
    original = original.model_copy(update={
        "localized_subtitles": [
            original.localized_subtitles[0].model_copy(update={"source_word_ids": ["w1", "w2", "w3", "w4", "w6"]}),
            original.localized_subtitles[1].model_copy(update={"source_word_ids": ["w7", "w8", "w9"]}),
        ],
        "localized_spoken_segments": [
            original.localized_spoken_segments[0].model_copy(update={"source_word_ids": ["w1", "w2", "w3", "w4", "w6"]}),
            original.localized_spoken_segments[1].model_copy(update={"source_word_ids": ["w7", "w8", "w9"]}),
        ],
    })
    try:
        repair_bindings(original, _request(["w1", "w2", "w3", "w4", "w6"], ["w7", "w8", "w9"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "VIDEO_LOCALIZATION_BINDING_REPAIR_WORD_ORDER_INVALID"
    else:
        raise AssertionError("expected continuity rejection")


def test_repair_supports_three_adjacent_binding_chain():
    original = _draft()
    left, middle = original.localized_subtitles
    third = VideoLocalizationSubtitleCue(subtitle_id="third", text="第三文案", start_ms=900, end_ms=1000, linked_cue_id="c3", source_cue_ids=["c3"], source_word_ids=["w9"], spoken_segment_id="s3")
    original = original.model_copy(update={
        "localized_subtitles": [
            left.model_copy(update={"source_word_ids": ["w1", "w2", "w3", "w4", "w5", "w6"], "source_cue_ids": ["c1", "c2"], "end_ms": 700}),
            middle.model_copy(update={"source_word_ids": ["w7", "w8"], "source_cue_ids": ["c2"], "start_ms": 700, "end_ms": 900}),
            third,
        ],
        "localized_spoken_segments": [
            original.localized_spoken_segments[0].model_copy(update={"source_word_ids": ["w1", "w2", "w3", "w4", "w5", "w6"], "end_ms": 700}),
            original.localized_spoken_segments[1].model_copy(update={"source_word_ids": ["w7", "w8"], "start_ms": 700, "end_ms": 900}),
            VideoLocalizationSpokenSegment(segment_id="s3", paragraph_id="p3", text="旧第三台词", start_ms=900, end_ms=1000, source_cue_ids=["c3"], source_word_ids=["w9"]),
        ],
    })
    request = BindingRepairRequest(expected_project_revision="r1", transcription_revision_id="tr1", audio_sha256="a" * 64, reason="three-way boundary repair", bindings=[
        {"subtitle_id": "left", "source_word_ids": ["w1", "w2", "w3", "w4"]},
        {"subtitle_id": "right", "source_word_ids": ["w5", "w6", "w7"]},
        {"subtitle_id": "third", "source_word_ids": ["w8", "w9"]},
    ])
    result = repair_bindings(original, request)
    assert [item.source_word_ids for item in result.localized_subtitles] == [
        ["w1", "w2", "w3", "w4"], ["w5", "w6", "w7"], ["w8", "w9"],
    ]
