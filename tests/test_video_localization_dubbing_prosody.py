from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.dubbing_prosody import (  # noqa: E402
    COMMA_GAP_MS,
    RUN_ON_GAP_MS,
    build_slices,
    plan_prosody_cuts,
    repair_clips,
)


def _words(texts, *, gaps=None):
    """Words with a natural internal pause; ``gaps[i]`` widens the pause after
    word ``i`` so a cut point has room for both safety margins."""

    words = []
    cursor = 0
    for index, text in enumerate(texts, start=1):
        start = cursor
        end = cursor + 200
        words.append(
            {
                "word_id": f"w{index:03d}",
                "text": text,
                "start_ms": start,
                "end_ms": end,
            }
        )
        gap = 100 if gaps is None else gaps[index - 1]
        cursor = end + gap
    return words


def _boundary(left_index, right_index, gap, words, *, overlap=0, status="fully_retained"):
    return {
        "boundary_id": f"{words[left_index - 1]['word_id']}:{words[right_index - 1]['word_id']}",
        "left_word_id": words[left_index - 1]["word_id"],
        "right_word_id": words[right_index - 1]["word_id"],
        "left_text": words[left_index - 1]["text"],
        "right_text": words[right_index - 1]["text"],
        "final_gap_ms": gap,
        "final_overlap_ms": overlap,
        "left_render_status": status,
        "right_render_status": status,
    }


def _clip(source_start_ms=0, source_end_ms=2000, subtitles=("s1",)):
    return {
        "clip_id": "clip_1",
        "candidate_id": "candidate_1",
        "start_ms": 1000,
        "end_ms": 3000,
        "source_start_ms": source_start_ms,
        "source_end_ms": source_end_ms,
        "target_subtitle_ids": list(subtitles),
    }


def test_run_on_pause_without_punctuation_gets_closed():
    words = _words(["比", "例", "也", "要"], gaps=[100, 400, 100, 0])
    boundaries = [_boundary(2, 3, 400, words)]
    cuts = plan_prosody_cuts(
        boundaries=boundaries,
        aligned_words=words,
        expected_spoken_text="比例也要",
    )
    assert len(cuts) == 1
    assert cuts[0].punctuation_class == "none"
    assert cuts[0].gap_ms == RUN_ON_GAP_MS


def test_comma_pause_that_touches_has_no_safe_cut_point():
    words = _words(["色", "我"])
    # The words touch, so neither side can keep a safety margin.
    words[1]["start_ms"] = words[0]["end_ms"]
    boundaries = [_boundary(1, 2, 0, words)]
    assert (
        plan_prosody_cuts(
            boundaries=boundaries,
            aligned_words=words,
            expected_spoken_text="色，我",
        )
        == []
    )


def test_missing_comma_break_with_room_is_opened():
    words = _words(["好", "我"], gaps=[200, 0])
    boundaries = [_boundary(1, 2, 0, words)]
    cuts = plan_prosody_cuts(
        boundaries=boundaries,
        aligned_words=words,
        expected_spoken_text="好，我",
    )
    assert len(cuts) == 1
    assert cuts[0].punctuation_class == "comma"
    assert cuts[0].gap_ms == COMMA_GAP_MS


def test_clipped_speech_is_never_cut():
    words = _words(["好", "我"], gaps=[400, 0])
    boundaries = [_boundary(1, 2, 400, words, status="partially_cut")]
    assert (
        plan_prosody_cuts(
            boundaries=boundaries,
            aligned_words=words,
            expected_spoken_text="好我",
        )
        == []
    )


def test_slices_tile_the_crop_and_keep_every_word_once():
    words = _words(["一", "二", "三", "四"], gaps=[100, 400, 100, 0])
    boundaries = [_boundary(2, 3, 400, words)]
    cuts = plan_prosody_cuts(
        boundaries=boundaries,
        aligned_words=words,
        expected_spoken_text="一二三四",
    )
    clip = _clip(source_start_ms=0, source_end_ms=words[-1]["end_ms"])
    slices = build_slices(clip=clip, cuts=cuts, aligned_words=words)
    assert len(slices) == 2
    assert slices[0]["source_start_ms"] == 0
    assert slices[-1]["source_end_ms"] == clip["source_end_ms"]
    assert slices[0]["source_end_ms"] == slices[1]["source_start_ms"]
    covered = [word for item in slices for word in item["alignment_word_ids"]]
    assert covered == [f"w{index:03d}" for index in range(1, 5)]


def test_repair_clips_rewrites_ids_and_offsets():
    words = _words(["一", "二", "三", "四"], gaps=[100, 400, 100, 0])
    boundaries = [_boundary(2, 3, 400, words)]
    clip = _clip(source_start_ms=0, source_end_ms=words[-1]["end_ms"])
    repaired = repair_clips(
        [clip],
        boundaries=boundaries,
        aligned_words=words,
        expected_spoken_text="一二三四",
    )
    assert len(repaired) == 2
    assert repaired[0]["clip_id"] == "clip_1"
    assert repaired[1]["clip_id"].endswith("__part_002")
    assert repaired[1]["dubbing_slice_index"] == 2
    assert repaired[1]["dubbing_slice_count"] == 2
    assert repaired[0]["end_ms"] <= repaired[1]["start_ms"]

    untouched = repair_clips(
        [clip, dict(clip, clip_id="clip_2")],
        boundaries=boundaries,
        aligned_words=words,
        expected_spoken_text="一二三四",
    )
    assert [item["clip_id"] for item in untouched] == ["clip_1", "clip_2"]
