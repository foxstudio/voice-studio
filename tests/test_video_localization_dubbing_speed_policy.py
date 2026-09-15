from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.dubbing_speed_policy import (  # noqa: E402
    decide_dubbing_capacity_repair_speed,
    decide_dubbing_speed,
)


def _group(group_id: str, subtitle_id: str, start_ms: int, end_ms: int, text: str):
    return SimpleNamespace(
        group_id=group_id,
        unit_ids=[f"unit_{group_id}"],
        subtitle_ids=[subtitle_id],
        speaker_id="speaker_1",
        spoken_text=text,
        target_start_ms=start_ms,
        target_end_ms=end_ms,
    )


def _draft(
    current,
    previous,
    *,
    previous_speed: float = 1.1,
    previous_exception: bool = False,
):
    units = [
        SimpleNamespace(unit_id=f"unit_{previous.group_id}", source_cue_ids=["cue_previous"]),
        SimpleNamespace(unit_id=f"unit_{current.group_id}", source_cue_ids=["cue_current"]),
    ]
    return SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(groups=[previous, current], semantic_units=units),
            candidate_reports=[
                SimpleNamespace(
                    group_id="much_older_group",
                    candidate_id="candidate_old",
                    audio_evidence=SimpleNamespace(speaking_rate_ratio=1.25),
                )
            ],
        ),
        timeline_clips=[
            {
                "clip_id": "clip_previous",
                "track_id": "dub",
                "status": "ready",
                "dub_lane": 0,
                "start_ms": previous.target_start_ms,
                "end_ms": previous.target_end_ms,
                "target_subtitle_ids": list(previous.subtitle_ids),
                "task_id": "task_previous",
                "result_id": "result_previous",
                "candidate_id": "candidate_previous",
            }
        ],
        tts_tasks=[
            SimpleNamespace(
                generation_task_id="task_previous",
                result_id="result_previous",
                timeline_clip_id="clip_previous",
                stages=[
                    SimpleNamespace(
                        kind="generation",
                        parameters={
                            "speed": previous_speed,
                            "content_speed_exception_reason": (
                                "source pace exception"
                                if previous_exception
                                else None
                            ),
                            "content_speed_exception_evidence_ids": (
                                ["separated-vocals:test"]
                                if previous_exception
                                else []
                            ),
                        },
                    ),
                    SimpleNamespace(kind="placement", parameters={}),
                ],
            )
        ],
        cues=[],
        transcription=None,
        stems=SimpleNamespace(separation_status="completed"),
    )


def test_immediate_formal_clip_workflow_speed_beats_older_candidate_report():
    previous = _group("previous", "subtitle_previous", 1_000, 4_000, "上一段")
    current = _group(
        "current",
        "subtitle_current",
        4_100,
        12_100,
        "这是一段文字压力很低但仍然应当延续上一段听感的文本",
    )

    decision = decide_dubbing_speed(_draft(current, previous), current)

    assert decision.baseline_clip_id == "clip_previous"
    assert decision.baseline_speed == 1.1
    assert decision.speed == 1.05
    assert decision.content_speed_exception_reason is None


def test_capacity_repair_respects_frozen_baseline_without_a_formal_clip():
    previous = _group("previous", "subtitle_previous", 1000, 4000, "上一段")
    current = _group("current", "subtitle_current", 4100, 5000, "短句")
    decision = decide_dubbing_capacity_repair_speed(_draft(current, previous), current, ordinary_speed_baseline=1.0)
    assert decision.speed == 1.05
    assert decision.baseline_speed == 1.0
    assert "frozen-baseline:1.00" in decision.content_speed_exception_evidence_ids


def test_capacity_repair_keeps_frozen_limit_with_an_existing_formal_clip():
    previous = _group("previous", "subtitle_previous", 1000, 4000, "上一段")
    current = _group("current", "subtitle_current", 4100, 5000, "另一种内容")
    draft = _draft(current, previous)
    draft.timeline_clips.append({
        "clip_id": "current", "dubbing_group_id": current.group_id,
        "track_id": "dub", "status": "ready", "dub_lane": 0,
        "start_ms": 4100, "end_ms": 7000,
        "target_subtitle_ids": list(current.subtitle_ids),
    })
    decision = decide_dubbing_capacity_repair_speed(
        draft, current, ordinary_speed_baseline=1.1,
    )
    assert decision.speed == 1.15
    assert decision.baseline_speed == 1.1
    assert "frozen-baseline:1.10" in decision.content_speed_exception_evidence_ids


def test_capacity_repair_uses_one_evidence_labelled_speed_exception():
    previous = _group("previous", "subtitle_previous", 1_000, 4_000, "上一段")
    current = _group("current", "subtitle_current", 4_100, 5_000, "短句")
    draft = _draft(current, previous)
    draft.timeline_clips.append(
        {
            "clip_id": "clip_current",
            "dubbing_group_id": current.group_id,
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "start_ms": 4_100,
            "end_ms": 5_400,
            "target_subtitle_ids": list(current.subtitle_ids),
        }
    )

    decision = decide_dubbing_capacity_repair_speed(draft, current)

    assert decision.speed == 1.35
    assert "容量修复" in str(decision.content_speed_exception_reason)
    assert decision.content_speed_exception_evidence_ids[0].startswith(
        "timeline-capacity:current:"
    )


def test_capacity_repair_escalates_once_after_durable_standard_repair():
    previous = _group("previous", "subtitle_previous", 1_000, 4_000, "上一段")
    current = _group("current", "subtitle_current", 4_100, 5_000, "短句")
    draft = _draft(current, previous)
    draft.timeline_clips.append(
        {
            "clip_id": "clip_current",
            "dubbing_group_id": current.group_id,
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "start_ms": 4_100,
            "end_ms": 5_400,
            "target_subtitle_ids": list(current.subtitle_ids),
        }
    )
    draft.tts_tasks.append(
        SimpleNamespace(
            stages=[
                SimpleNamespace(
                    kind="generation",
                    parameters={
                        "video_localization_dubbing_group_id": "current",
                        "speed": 1.35,
                        "content_speed_exception_evidence_ids": [
                            "timeline-capacity:current:4100:5000"
                        ],
                    },
                )
            ]
        )
    )

    decision = decide_dubbing_capacity_repair_speed(draft, current)

    assert decision.speed == 1.8
    assert (
        "timeline-capacity:bounded-second-level-repair"
        in decision.content_speed_exception_evidence_ids
    )


def test_adjacent_speed_baseline_does_not_require_same_speaker():
    previous = _group("previous", "subtitle_previous", 1_000, 4_000, "上一段")
    previous.speaker_id = "speaker_a"
    current = _group("current", "subtitle_current", 4_100, 8_100, "短句")
    current.speaker_id = "speaker_b"

    decision = decide_dubbing_speed(_draft(current, previous), current)

    assert decision.baseline_speed == 1.1
    assert round(abs(decision.speed - decision.baseline_speed), 2) <= 0.05


def test_first_formal_clip_uses_text_pressure_without_fabricated_baseline():
    current = _group(
        "current",
        "subtitle_current",
        1_000,
        3_000,
        "这是一段文字密度非常高而且需要在很短时间表达完毕的中文内容",
    )
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(
                groups=[current],
                semantic_units=[
                    SimpleNamespace(unit_id="unit_current", source_cue_ids=[])
                ],
            ),
            candidate_reports=[],
        ),
        timeline_clips=[],
        tts_tasks=[],
        cues=[],
        transcription=None,
        stems=SimpleNamespace(separation_status="completed"),
    )

    decision = decide_dubbing_speed(draft, current)

    assert decision.baseline_speed is None
    assert decision.speed == 1.25


def test_reserved_previous_group_speed_is_parallel_generation_baseline():
    previous = _group("previous", "subtitle_previous", 1_000, 4_000, "上一段")
    current = _group(
        "current",
        "subtitle_current",
        4_100,
        10_100,
        "这一段文字压力较低但必须延续刚排队上一段的语速",
    )
    draft = _draft(current, previous, previous_speed=1.1)
    draft.timeline_clips = []
    draft.tts_tasks[0].workflow_id = "workflow_previous"
    draft.tts_tasks[0].stages[0].parameters[
        "video_localization_target_subtitle_ids"
    ] = [
        "subtitle_previous"
    ]

    decision = decide_dubbing_speed(draft, current)

    assert decision.baseline_clip_id == "workflow_previous"
    assert decision.baseline_speed == 1.1
    assert round(abs(decision.speed - decision.baseline_speed), 2) <= 0.05


def test_large_separated_vocals_pace_change_does_not_self_authorize_exception():
    previous = _group("previous", "subtitle_previous", 1_000, 5_000, "上一段")
    current = _group(
        "current",
        "subtitle_current",
        5_100,
        7_100,
        "这一段内容很多而且原英文说话速度也明显更快",
    )
    draft = _draft(current, previous)
    draft.cues = [
        SimpleNamespace(cue_id="cue_previous", source_word_ids=["p1", "p2", "p3", "p4"]),
        SimpleNamespace(cue_id="cue_current", source_word_ids=["c1", "c2", "c3", "c4"]),
    ]
    draft.transcription = SimpleNamespace(
        revision_id="transcription-v1",
        source_track_id="vocals",
        alignment_source_track_id="vocals",
        words=[
            SimpleNamespace(word_id="p1", start_ms=1_000, end_ms=1_300),
            SimpleNamespace(word_id="p2", start_ms=2_000, end_ms=2_300),
            SimpleNamespace(word_id="p3", start_ms=3_000, end_ms=3_300),
            SimpleNamespace(word_id="p4", start_ms=4_000, end_ms=4_300),
            SimpleNamespace(word_id="c1", start_ms=5_100, end_ms=5_300),
            SimpleNamespace(word_id="c2", start_ms=5_500, end_ms=5_700),
            SimpleNamespace(word_id="c3", start_ms=5_900, end_ms=6_100),
            SimpleNamespace(word_id="c4", start_ms=6_300, end_ms=6_500),
        ],
    )

    decision = decide_dubbing_speed(draft, current)

    assert decision.speed == 1.15
    assert decision.source_pace_ratio == 2.357
    assert decision.content_speed_exception_reason is None
    assert decision.content_speed_exception_evidence_ids == ()


def test_fast_source_only_permits_required_speed_and_does_not_force_acceleration():
    previous = _group("previous", "subtitle_previous", 1_000, 5_000, "上一段")
    current = _group(
        "current",
        "subtitle_current",
        5_100,
        11_100,
        "目标时间充足，不需要为了原英文说得快而加速。",
    )
    draft = _draft(current, previous)
    draft.cues = [
        SimpleNamespace(cue_id="cue_previous", source_word_ids=["p1", "p2", "p3", "p4"]),
        SimpleNamespace(cue_id="cue_current", source_word_ids=["c1", "c2", "c3", "c4"]),
    ]
    draft.transcription = SimpleNamespace(
        revision_id="transcription-v1",
        source_track_id="vocals",
        alignment_source_track_id="vocals",
        words=[
            SimpleNamespace(word_id="p1", start_ms=1_000, end_ms=1_300),
            SimpleNamespace(word_id="p2", start_ms=2_000, end_ms=2_300),
            SimpleNamespace(word_id="p3", start_ms=3_000, end_ms=3_300),
            SimpleNamespace(word_id="p4", start_ms=4_000, end_ms=4_300),
            SimpleNamespace(word_id="c1", start_ms=5_100, end_ms=5_300),
            SimpleNamespace(word_id="c2", start_ms=5_500, end_ms=5_700),
            SimpleNamespace(word_id="c3", start_ms=5_900, end_ms=6_100),
            SimpleNamespace(word_id="c4", start_ms=6_300, end_ms=6_500),
        ],
    )

    decision = decide_dubbing_speed(draft, current)

    assert decision.speed == 1.05
    assert decision.content_speed_exception_reason is None


def test_adjacent_normal_clip_prevents_false_source_pace_exception_from_older_median():
    older = _group("older", "subtitle_older", 1_000, 4_000, "更早正常段")
    previous = _group("previous", "subtitle_previous", 4_100, 7_100, "上一段")
    current = _group("current", "subtitle_current", 7_200, 11_200, "当前短句")
    draft = _draft(current, previous, previous_speed=1.05)
    draft.dubbing_production.active_plan.groups.insert(0, older)
    draft.dubbing_production.active_plan.semantic_units.insert(
        0,
        SimpleNamespace(unit_id="unit_older", source_cue_ids=["cue_older"]),
    )
    draft.timeline_clips.insert(
        0,
        {
            "clip_id": "clip_older",
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "start_ms": older.target_start_ms,
            "end_ms": older.target_end_ms,
            "target_subtitle_ids": list(older.subtitle_ids),
            "task_id": "task_older",
            "result_id": "result_older",
            "candidate_id": "candidate_older",
        },
    )
    draft.tts_tasks.insert(
        0,
        SimpleNamespace(
            generation_task_id="task_older",
            result_id="result_older",
            timeline_clip_id="clip_older",
            stages=[
                SimpleNamespace(
                    kind="generation",
                    parameters={
                        "speed": 1.15,
                        "content_speed_exception_reason": None,
                        "content_speed_exception_evidence_ids": [],
                    },
                )
            ],
        ),
    )
    draft.cues = [
        SimpleNamespace(cue_id="cue_previous", source_word_ids=["p1", "p2"]),
        SimpleNamespace(cue_id="cue_current", source_word_ids=["c1", "c2"]),
    ]
    draft.transcription = SimpleNamespace(
        revision_id="transcription-v1",
        source_track_id="vocals",
        alignment_source_track_id="vocals",
        words=[
            SimpleNamespace(word_id="p1", start_ms=4_100, end_ms=4_300),
            SimpleNamespace(word_id="p2", start_ms=4_500, end_ms=4_700),
            SimpleNamespace(word_id="c1", start_ms=7_200, end_ms=8_200),
            SimpleNamespace(word_id="c2", start_ms=9_500, end_ms=10_500),
        ],
    )

    decision = decide_dubbing_speed(draft, current)

    assert decision.speed == 1.0
    assert decision.content_speed_exception_reason is None


def test_previous_speed_exception_does_not_reset_stable_neighborhood_baseline():
    older = _group("older", "subtitle_older", 1_000, 4_000, "更早正常段")
    previous = _group("previous", "subtitle_previous", 4_100, 7_100, "上一段例外加速")
    current = _group(
        "current",
        "subtitle_current",
        7_200,
        13_200,
        "当前时间充足，应回到附近正常语速。",
    )
    draft = _draft(
        current,
        previous,
        previous_speed=1.3,
        previous_exception=True,
    )
    draft.dubbing_production.active_plan.groups.insert(0, older)
    draft.dubbing_production.active_plan.semantic_units.insert(
        0,
        SimpleNamespace(unit_id="unit_older", source_cue_ids=["cue_older"]),
    )
    draft.timeline_clips.insert(
        0,
        {
            "clip_id": "clip_older",
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "start_ms": older.target_start_ms,
            "end_ms": older.target_end_ms,
            "target_subtitle_ids": list(older.subtitle_ids),
            "task_id": "task_older",
            "result_id": "result_older",
            "candidate_id": "candidate_older",
        },
    )
    draft.tts_tasks.insert(
        0,
        SimpleNamespace(
            generation_task_id="task_older",
            result_id="result_older",
            timeline_clip_id="clip_older",
            stages=[
                SimpleNamespace(
                    kind="generation",
                    parameters={"speed": 1.05},
                )
            ],
        ),
    )

    decision = decide_dubbing_speed(draft, current)

    assert decision.baseline_speed == 1.05
    assert decision.baseline_clip_id == "clip_older"
    assert decision.speed == 1.0


def test_supervised_pending_candidate_is_not_a_stable_neighborhood_baseline():
    older = _group("older", "subtitle_older", 1_000, 4_000, "已验收正常段")
    previous = _group("previous", "subtitle_previous", 4_100, 7_100, "待监工候选")
    current = _group(
        "current",
        "subtitle_current",
        7_200,
        13_200,
        "当前时间充足，应沿用已验收稳定语速。",
    )
    draft = _draft(current, previous, previous_speed=1.25)
    draft.dubbing_production.active_plan.groups.insert(0, older)
    draft.dubbing_production.active_plan.semantic_units.insert(
        0,
        SimpleNamespace(unit_id="unit_older", source_cue_ids=["cue_older"]),
    )
    draft.timeline_clips[0]["manual_review_reason_codes"] = [
        "candidate_supervised_review_pending"
    ]
    draft.timeline_clips.insert(
        0,
        {
            "clip_id": "clip_older",
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "start_ms": older.target_start_ms,
            "end_ms": older.target_end_ms,
            "target_subtitle_ids": list(older.subtitle_ids),
            "task_id": "task_older",
            "result_id": "result_older",
            "candidate_id": "candidate_older",
        },
    )
    draft.tts_tasks.insert(
        0,
        SimpleNamespace(
            generation_task_id="task_older",
            result_id="result_older",
            timeline_clip_id="clip_older",
            stages=[
                SimpleNamespace(
                    kind="generation",
                    parameters={"speed": 1.05},
                )
            ],
        ),
    )

    decision = decide_dubbing_speed(draft, current)

    assert decision.baseline_speed == 1.05
    assert decision.baseline_clip_id == "clip_older"


def test_original_track_or_missing_word_evidence_cannot_exceed_adjacent_limit():
    previous = _group("previous", "subtitle_previous", 1_000, 5_000, "上一段")
    current = _group(
        "current",
        "subtitle_current",
        5_100,
        7_100,
        "这一段内容很多但没有可用的分离人声证据",
    )
    draft = _draft(current, previous)
    draft.transcription = SimpleNamespace(
        revision_id="transcription-v1",
        source_track_id="original",
        alignment_source_track_id="original",
        words=[],
    )

    decision = decide_dubbing_speed(draft, current)

    assert round(abs(decision.speed - decision.baseline_speed), 2) <= 0.05
    assert decision.content_speed_exception_reason is None
