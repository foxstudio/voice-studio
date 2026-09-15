from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
)
from app.domains.video_localization.tts_placement import (  # noqa: E402
    FrozenTtsPlacementTarget,
    TtsResultMetadata,
    place_frozen_tts_result,
    replace_with_local_phrase,
    with_explicit_single_tts_result,
)
from app.domains.video_localization import tts_pipeline  # noqa: E402
from app.services import audio_tools  # noqa: E402


def _subtitle(subtitle_id: str, start_ms: int) -> VideoLocalizationSubtitleCue:
    return VideoLocalizationSubtitleCue(
        subtitle_id=subtitle_id,
        start_ms=start_ms,
        end_ms=start_ms + 800,
        text=f"台词 {subtitle_id}",
        source_cue_ids=[subtitle_id.replace("localized", "cue")],
    )


def _noncontiguous_target() -> FrozenTtsPlacementTarget:
    return FrozenTtsPlacementTarget(
        segment_id="group_localized_0001_localized_0003_2",
        target_subtitle_ids=("localized_0001", "localized_0003"),
        source_cue_ids=("cue_0001", "cue_0003"),
        start_ms=1_000,
        end_ms=3_800,
    )


def test_planned_outer_silence_is_trimmed_at_neighbor_boundaries():
    clip = {
        "start_ms": 920,
        "end_ms": 1_950,
        "target_start_ms": 1_000,
        "source_start_ms": 20,
        "source_end_ms": 1_050,
        "alignment_lead_ms": 80,
        "alignment_trail_ms": 80,
    }

    fitted = tts_pipeline._trim_planned_clip_outer_silence_against_neighbors(
        clip,
        [
            {"target_start_ms": 0, "start_ms": 0, "end_ms": 950},
            {"target_start_ms": 2_000, "start_ms": 1_900, "end_ms": 2_500},
        ],
    )

    assert fitted["start_ms"] == 950
    assert fitted["source_start_ms"] == 50
    assert fitted["alignment_lead_ms"] == 50
    assert fitted["end_ms"] == 1_900
    assert fitted["source_end_ms"] == 1_000
    assert fitted["alignment_trail_ms"] == 30


def test_speech_boundary_alignment_keeps_complete_quiet_tail():
    fitted = tts_pipeline._speech_boundary_alignment(
        "/tmp/not-read-when-leading-silence-is-measured.wav",
        9_900,
        441_458,
        measured_leading_silence_ms=163,
        measured_trailing_silence_ms=320,
    )

    assert fitted["source_start_ms"] == 83
    assert fitted["source_end_ms"] == 9_900
    assert fitted["alignment_trail_ms"] == 320
    assert fitted["start_ms"] == 441_378
    assert fitted["end_ms"] == 451_195


def test_complete_audio_duration_rounds_up_to_contain_every_sample(
    tmp_path: Path,
):
    audio_path = tmp_path / "sub-millisecond-tail.wav"
    audio_tools.write_audio(
        audio_path,
        np.full(48_001, 0.1, dtype=np.float32),
        48_000,
    )

    assert audio_tools.probe_audio(audio_path)["duration_ms"] == 1_000
    assert audio_tools.probe_audio_duration_ceil_ms(audio_path) == 1_001


def test_planned_clip_shifts_instead_of_trimming_previous_terminal_audio():
    clip = {
        "start_ms": 1_000,
        "end_ms": 2_000,
        "target_start_ms": 1_000,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
        "alignment_lead_ms": 0,
        "alignment_trail_ms": 0,
    }
    previous = {
        "target_start_ms": 0,
        "start_ms": 0,
        "end_ms": 1_050,
        "source_start_ms": 0,
        "source_end_ms": 1_050,
        "alignment_lead_ms": 0,
        "alignment_trail_ms": 80,
    }

    fitted, neighbors = (
        tts_pipeline._fit_planned_clip_and_neighbor_outer_silence(
            clip,
            [previous],
        )
    )

    assert fitted["start_ms"] == 1_050
    assert fitted["end_ms"] == 2_050
    assert neighbors[0]["end_ms"] == 1_050
    assert neighbors[0]["source_end_ms"] == 1_050
    assert neighbors[0]["alignment_trail_ms"] == 80


def test_planned_clip_never_trims_its_terminal_audio_against_following_clip():
    clip = {
        "start_ms": 1_000,
        "end_ms": 1_950,
        "target_start_ms": 1_000,
        "source_start_ms": 50,
        "source_end_ms": 1_000,
        "alignment_lead_ms": 0,
        "alignment_trail_ms": 320,
    }
    following = {
        "start_ms": 1_900,
        "end_ms": 2_500,
        "target_start_ms": 2_000,
        "source_start_ms": 20,
        "source_end_ms": 620,
        "alignment_lead_ms": 80,
        "alignment_trail_ms": 80,
    }

    fitted, neighbors = (
        tts_pipeline._fit_planned_clip_and_neighbor_outer_silence(
            clip,
            [following],
        )
    )

    assert fitted["end_ms"] == 1_950
    assert fitted["source_end_ms"] == 1_000
    assert fitted["alignment_trail_ms"] == 320
    assert neighbors[0]["start_ms"] == 1_950
    assert neighbors[0]["source_start_ms"] == 70


def _result(**updates) -> TtsResultMetadata:
    values = {
        "result_id": "result_01",
        "output_path": "/tmp/result_01.wav",
        "duration_ms": 2_200,
        "task_id": "task_01",
        "generation_id": "generation_01",
    }
    values.update(updates)
    return TtsResultMetadata(**values)


def test_noncontiguous_targets_create_one_explicit_clip_without_touching_middle_subtitle():
    subtitles = [
        _subtitle("localized_0001", 1_000),
        _subtitle("localized_0002", 2_000),
        _subtitle("localized_0003", 3_000),
    ]
    draft = VideoLocalizationDraft(localized_subtitles=subtitles)

    placed = place_frozen_tts_result(draft, _noncontiguous_target(), _result())

    assert len(placed.draft.timeline_clips) == 1
    clip = placed.draft.timeline_clips[0]
    assert clip["subtitle_id"] == "group_localized_0001_localized_0003_2"
    assert clip["target_subtitle_ids"] == ["localized_0001", "localized_0003"]
    assert clip["source_cue_ids"] == ["cue_0001", "cue_0003"]
    assert clip["cue_id"] == "cue_0001"
    assert (clip["start_ms"], clip["end_ms"]) == (1_000, 3_200)
    assert placed.mirrored_subtitle_ids == ()
    assert all(item.tts_result_id is None for item in placed.draft.localized_subtitles)
    assert draft.timeline_clips == []


def test_single_target_writes_result_mirror_only_to_that_explicit_subtitle():
    subtitles = [
        _subtitle("localized_0001", 1_000),
        _subtitle("localized_0002", 2_000),
    ]
    target = FrozenTtsPlacementTarget(
        segment_id="localized_0002",
        target_subtitle_ids=("localized_0002",),
        source_cue_ids=("cue_0002",),
        start_ms=2_000,
        end_ms=2_800,
    )

    placed = place_frozen_tts_result(
        VideoLocalizationDraft(localized_subtitles=subtitles),
        target,
        _result(duration_ms=700),
    )

    first, second = placed.draft.localized_subtitles
    assert first.tts_result_id is None
    assert second.tts_result_id == "result_01"
    assert second.tts_audio_path == "/tmp/result_01.wav"
    assert second.tts_generation_id == "generation_01"
    assert second.generated_duration_ms == 700
    assert placed.mirrored_subtitle_ids == ("localized_0002",)


def test_explicit_clip_update_preserves_identity_lane_and_custom_fields():
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            _subtitle("localized_0001", 1_000),
            _subtitle("localized_0002", 2_000),
            _subtitle("localized_0003", 3_000),
        ],
        timeline_clips=[
            {
                "clip_id": "existing_clip",
                "track_id": "dub",
                "subtitle_id": "legacy_group",
                "target_subtitle_ids": ["localized_0001", "localized_0002"],
                "dub_lane": 2,
                "custom_marker": "keep",
                "status": "queued",
                "tts_target_binding_status": "stale",
                "tts_target_text": "旧台词",
            }
        ],
    )
    refreshed_target = FrozenTtsPlacementTarget(
        segment_id="group_localized_0001_localized_0003_2",
        target_subtitle_ids=("localized_0001", "localized_0003"),
        source_cue_ids=("cue_0001", "cue_0003"),
        start_ms=1_000,
        end_ms=3_800,
        text="重新生成的新台词",
    )

    placed = place_frozen_tts_result(
        draft,
        refreshed_target,
        _result(
            timeline_clip_id="existing_clip",
            placement_start_ms=900,
            placement_end_ms=3_050,
            source_start_ms=50,
            source_end_ms=2_200,
            speech_onset_ms=120,
            alignment_lead_ms=70,
        ),
    )

    assert len(placed.draft.timeline_clips) == 1
    clip = placed.draft.timeline_clips[0]
    assert clip["clip_id"] == "existing_clip"
    assert clip["dub_lane"] == 2
    assert clip["custom_marker"] == "keep"
    assert clip["tts_target_binding_status"] == "current"
    assert clip["tts_target_text"] == "重新生成的新台词"
    assert clip["target_subtitle_ids"] == ["localized_0001", "localized_0003"]
    assert (clip["start_ms"], clip["end_ms"]) == (900, 3_050)
    assert (clip["source_start_ms"], clip["source_end_ms"]) == (50, 2_200)
    assert clip["speech_onset_ms"] == 120


def test_overlapping_target_selection_preserves_the_previous_take():
    subtitles = [
        _subtitle("localized_0001", 1_000),
        _subtitle("localized_0002", 2_000),
        _subtitle("localized_0003", 3_000),
        _subtitle("localized_0004", 4_000),
    ]
    first_target = _noncontiguous_target()
    draft = place_frozen_tts_result(
        VideoLocalizationDraft(localized_subtitles=subtitles),
        first_target,
        _result(task_id=None),
    ).draft
    second_target = FrozenTtsPlacementTarget(
        segment_id=first_target.segment_id,
        target_subtitle_ids=("localized_0001", "localized_0004"),
        source_cue_ids=("cue_0001", "cue_0004"),
        start_ms=1_000,
        end_ms=4_800,
    )

    placed = place_frozen_tts_result(
        draft,
        second_target,
        _result(result_id="result_02", output_path="/tmp/result_02.wav", task_id=None, generation_id="generation_02"),
    )

    assert len(placed.draft.timeline_clips) == 2
    assert placed.draft.timeline_clips[0] == draft.timeline_clips[0]
    assert placed.draft.timeline_clips[1]["target_subtitle_ids"] == ["localized_0001", "localized_0004"]
    assert placed.draft.timeline_clips[1]["result_id"] == "result_02"


def test_missing_frozen_target_is_rejected_without_group_name_fallback():
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            _subtitle("localized_0001", 1_000),
            _subtitle("localized_0002", 2_000),
            _subtitle("localized_0003", 3_000),
        ]
    )
    target = FrozenTtsPlacementTarget(
        segment_id="group_localized_0001_localized_0003_3",
        target_subtitle_ids=("localized_0001", "deleted_localized", "localized_0003"),
        source_cue_ids=("cue_0001", "cue_0002", "cue_0003"),
        start_ms=1_000,
        end_ms=3_800,
    )

    with pytest.raises(KeyError, match="deleted_localized"):
        place_frozen_tts_result(draft, target, _result())

    assert draft.timeline_clips == []
    assert all(item.tts_result_id is None for item in draft.localized_subtitles)


def test_repeated_selection_adds_a_new_clip_and_preserves_the_old_identity():
    target = _noncontiguous_target()
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            _subtitle("localized_0001", 1_000),
            _subtitle("localized_0002", 2_000),
            _subtitle("localized_0003", 3_000),
        ],
        timeline_clips=[
            {
                "clip_id": "frozen_clip",
                "track_id": "dub",
                "subtitle_id": target.segment_id,
                "target_subtitle_ids": ["localized_0001", "localized_0003"],
                "result_id": "old_result",
            }
        ],
    )

    placed = place_frozen_tts_result(draft, target, _result(task_id="task-new"))

    assert len(placed.draft.timeline_clips) == 2
    assert placed.draft.timeline_clips[0] == draft.timeline_clips[0]
    assert placed.clip_id != "frozen_clip"
    assert placed.draft.timeline_clips[1]["result_id"] == "result_01"
    assert placed.draft.timeline_clips[1]["task_id"] == "task-new"
    replayed = place_frozen_tts_result(placed.draft, target, _result(task_id="task-new"))
    assert replayed.draft.timeline_clips == placed.draft.timeline_clips


@pytest.mark.parametrize("replace_clip_id", [None, "old_left"])
def test_placement_preserves_other_takes_even_when_targets_intersect(replace_clip_id):
    target = FrozenTtsPlacementTarget(
        segment_id="group_localized_0002_localized_0003_2",
        target_subtitle_ids=("localized_0002", "localized_0003"),
        source_cue_ids=("cue_0002", "cue_0003"),
        start_ms=2_000,
        end_ms=3_800,
    )
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            _subtitle("localized_0001", 1_000),
            _subtitle("localized_0002", 2_000),
            _subtitle("localized_0003", 3_000),
            _subtitle("localized_0004", 4_000),
        ],
        timeline_clips=[
            {
                "clip_id": "old_left",
                "track_id": "dub",
                "subtitle_id": "localized_0002",
                "target_subtitle_ids": ["localized_0002"],
                "result_id": "old_left_result",
            },
            {
                "clip_id": "old_right",
                "track_id": "dub",
                "subtitle_id": "localized_0003",
                "target_subtitle_ids": ["localized_0003"],
                "result_id": "old_right_result",
            },
            {
                "clip_id": "unrelated",
                "track_id": "dub",
                "subtitle_id": "localized_0004",
                "target_subtitle_ids": ["localized_0004"],
                "result_id": "unrelated_result",
            },
            {
                "clip_id": "manual_copy",
                "track_id": "dub",
                "subtitle_id": "localized_0002",
                "target_subtitle_ids": ["localized_0002"],
                "result_id": "manual_result",
                "manual_history_copy": True,
            },
        ],
    )

    placed = place_frozen_tts_result(draft, target, _result(timeline_clip_id=replace_clip_id))

    by_id = {item["clip_id"]: item for item in placed.draft.timeline_clips}
    assert set(by_id) == {placed.clip_id, "old_left", "old_right", "unrelated", "manual_copy"}
    for original in draft.timeline_clips:
        if original["clip_id"] != replace_clip_id:
            assert by_id[original["clip_id"]] == original
    assert by_id[placed.clip_id]["target_subtitle_ids"] == [
        "localized_0002",
        "localized_0003",
    ]
    assert by_id[placed.clip_id]["result_id"] == "result_01"


def test_production_adapter_freezes_explicit_target_ids_on_clip():
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            _subtitle("localized_0001", 1_000),
            _subtitle("localized_0002", 2_000),
            _subtitle("localized_0003", 3_000),
        ]
    )

    placed = with_explicit_single_tts_result(
        draft,
        segment_id="group_localized_0001_localized_0003_2",
        target_subtitle_ids=["localized_0001", "localized_0003"],
        source_cue_ids=["cue_0001", "cue_0003"],
        target_start_ms=1_000,
        target_end_ms=3_800,
        result_id="result_adapter",
        output_path="/tmp/result_adapter.wav",
        duration_ms=2_100,
        task_id="task_adapter",
    )

    assert placed.timeline_clips[0]["target_subtitle_ids"] == ["localized_0001", "localized_0003"]
    assert placed.timeline_clips[0]["source_cue_ids"] == ["cue_0001", "cue_0003"]
    assert placed.timeline_clips[0]["result_id"] == "result_adapter"


def test_production_adapter_places_current_take_without_review_labels():
    base = VideoLocalizationDraft(
        localized_subtitles=[
            _subtitle("localized_0001", 1_000),
            _subtitle("localized_0002", 2_000),
        ]
    )
    draft = base.model_copy(
        update={
            "dubbing_production": base.dubbing_production.model_copy(
                update={"enforcement_mode": "planned"}
            )
        }
    )

    placed = with_explicit_single_tts_result(
        draft,
        segment_id="group_localized_0001_localized_0002_2",
        target_subtitle_ids=["localized_0001", "localized_0002"],
        source_cue_ids=["cue_0001", "cue_0002"],
        target_start_ms=1_000,
        target_end_ms=2_800,
        result_id="result_planned",
        output_path="/tmp/result_planned.wav",
        duration_ms=1_700,
        task_id="task_planned",
    )

    assert len(placed.timeline_clips) == 1
    assert placed.timeline_clips[0]["result_id"] == "result_planned"
    assert "cqc_status" not in placed.timeline_clips[0]
    assert "timeline_edit_gate" not in placed.timeline_clips[0]
    assert placed.timeline_clips[0]["target_subtitle_ids"] == [
        "localized_0001",
        "localized_0002",
    ]


def test_callback_replay_drops_legacy_review_labels_from_the_current_take():
    target = _noncontiguous_target()
    first = place_frozen_tts_result(
        VideoLocalizationDraft(
            localized_subtitles=[
                _subtitle("localized_0001", 1_000),
                _subtitle("localized_0002", 2_000),
                _subtitle("localized_0003", 3_000),
            ]
        ),
        target,
        _result(),
    ).draft
    clip = dict(first.timeline_clips[0])
    clip.update(
        {
            "cqc_status": "passed",
            "cqc_report_version": "dubbing-candidate-cqc-v1",
        }
    )
    reviewed = first.model_copy(update={"timeline_clips": [clip]})

    replayed = place_frozen_tts_result(
        reviewed,
        target,
        _result(),
    ).draft

    assert len(replayed.timeline_clips) == 1
    assert "cqc_status" not in replayed.timeline_clips[0]
    assert "cqc_report_version" not in replayed.timeline_clips[0]


def test_local_phrase_repair_replaces_old_clips_only_after_candidate_is_ready():
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "old_left",
                "track_id": "dub",
                "status": "ready",
                "start_ms": 1_000,
                "end_ms": 1_800,
                "dub_lane": 2,
            },
            {
                "clip_id": "old_right",
                "track_id": "dub",
                "status": "ready",
                "start_ms": 1_800,
                "end_ms": 2_600,
                "dub_lane": 2,
            },
            {
                "clip_id": "candidate",
                "track_id": "dub",
                "status": "ready",
                "start_ms": 3_000,
                "end_ms": 4_400,
                "source_start_ms": 0,
                "source_end_ms": 1_400,
                "audio_path": "/tmp/candidate.wav",
                "result_id": "result_candidate",
                "dub_lane": 0,
            },
        ]
    )

    repaired = replace_with_local_phrase(
        draft,
        candidate_clip_id="candidate",
        replace_clip_ids=("old_left", "old_right"),
        target_start_ms=1_000,
        target_end_ms=2_450,
        source_start_ms=80,
        source_end_ms=1_300,
        target_text="局部修补台词",
    )

    assert [item["clip_id"] for item in repaired.timeline_clips] == ["candidate"]
    clip = repaired.timeline_clips[0]
    assert (clip["start_ms"], clip["end_ms"]) == (1_000, 2_450)
    assert (clip["source_start_ms"], clip["source_end_ms"]) == (80, 1_300)
    assert clip["dub_lane"] == 2
    assert clip["tts_target_text"] == "局部修补台词"
    assert clip["local_phrase_repair"]["replaced_clip_ids"] == ["old_left", "old_right"]


def test_local_phrase_repair_keeps_old_clips_when_candidate_is_not_ready():
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {"clip_id": "old", "track_id": "dub", "status": "ready", "start_ms": 0, "end_ms": 500},
            {"clip_id": "candidate", "track_id": "dub", "status": "running", "start_ms": 0, "end_ms": 500},
        ]
    )

    with pytest.raises(ValueError, match="ready"):
        replace_with_local_phrase(
            draft,
            candidate_clip_id="candidate",
            replace_clip_ids=("old",),
            target_start_ms=0,
            target_end_ms=500,
            source_start_ms=0,
            source_end_ms=400,
            target_text="修补",
        )

    assert [item["clip_id"] for item in draft.timeline_clips] == ["old", "candidate"]
