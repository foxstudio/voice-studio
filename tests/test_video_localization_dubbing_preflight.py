import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.video_localization.dubbing_preflight import assess_group_preflight
from app.models.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
)
from app.schemas.video_localization_dubbing_production import (
    DubbingAutomaticAudioEvidence,
    DubbingCandidateAlignedWord,
    DubbingCandidateCqcInput,
    DubbingGenerationGroup,
    DubbingGenerationPlan,
    DubbingProductionState,
)


SOURCE_REVISION = "a" * 64


def _group(index, *, speaker="speaker-a", text="ABCDE", start=None, end=None):
    start = index * 2_000 if start is None else start
    end = start + 1_500 if end is None else end
    return DubbingGenerationGroup(
        group_id=f"group-{index}", island_id=f"island-{index}",
        unit_ids=[f"unit-{index}"], subtitle_ids=[f"subtitle-{index}"],
        speaker_id=speaker, spoken_text=text, target_start_ms=start,
        target_end_ms=end, source_reference_start_ms=start,
        source_reference_end_ms=end,
    )


def _input(group, *, candidate_id, speech_span_ms=1_000):
    return DubbingCandidateCqcInput(
        source_revision=SOURCE_REVISION, plan_revision=1, group_id=group.group_id,
        candidate_id=candidate_id, task_status="success",
        expected_spoken_text=group.spoken_text, target_start_ms=group.target_start_ms,
        target_end_ms=group.target_end_ms,
        audio=DubbingAutomaticAudioEvidence(
            duration_ms=speech_span_ms + 200, peak_dbfs=-3, clipping_ratio=0,
            leading_silence_ms=100, trailing_silence_ms=100,
            speech_start_ms=100, speech_end_ms=speech_span_ms + 100,
            speech_span_ms=speech_span_ms,
            # The preflight must use explicit speech_span_ms and text units,
            # rather than treating this one alignment item as five text units.
            aligned_words=[DubbingCandidateAlignedWord(
                word_id="one-alignment-item", text=group.spoken_text,
                start_ms=100, end_ms=speech_span_ms + 100,
            )],
        ),
    )


def _workflow(group, *, candidate_id, speed=1.1, engine="omnivoice"):
    task_id = candidate_id.removeprefix("candidate_")
    parameters = {
        "engine_id": engine, "speed": speed,
        "video_localization_dubbing_group_id": group.group_id,
        "video_localization_dubbing_plan_revision": 1,
    }
    return VideoLocalizationTtsTask(
        workflow_id=f"workflow-{candidate_id}", project_id="project",
        segment_id=group.group_id, subtitle_summary=group.spoken_text,
        text=group.spoken_text, start_ms=group.target_start_ms,
        end_ms=group.target_end_ms, status="success", generation_task_id=task_id,
        stages=[
            VideoLocalizationTtsTaskStage(kind="generation", status="success", parameters=parameters),
            VideoLocalizationTtsTaskStage(kind="placement", status="success"),
        ],
    )


def _draft(groups, inputs=(), workflows=(), clips=(), *, discarded=()):
    plan = DubbingGenerationPlan(
        source_revision=SOURCE_REVISION, plan_revision=1, status="passed",
        semantic_units=[], speech_islands=[], groups=groups,
    )
    return VideoLocalizationDraft(
        dubbing_production=DubbingProductionState(
            plan_revision_counter=1, active_plan=plan,
            candidate_inputs=list(inputs),
        ),
        tts_tasks=list(workflows), timeline_clips=list(clips),
        ui_state={"discarded_tts_task_ids": list(discarded)},
    )


def test_preflight_estimates_from_three_nearest_matching_frozen_candidates():
    groups = [_group(index) for index in range(4)]
    target = groups[2]
    inputs = [_input(group, candidate_id=f"candidate_task-{index}", speech_span_ms=1_000)
              for index, group in enumerate(groups) if group is not target]
    workflows = [_workflow(group, candidate_id=f"candidate_task-{index}")
                 for index, group in enumerate(groups)]

    result = assess_group_preflight(_draft(groups, inputs, workflows), target, speed=1.1)

    assert result.status == "ready"
    assert result.estimated_speech_duration_ms == 1_000
    assert result.estimate_evidence.confidence == "high"
    assert len(result.estimate_evidence.reference_observations) == 3
    assert result.estimate_evidence.reference_observations[0].text_units.unit_count == 5


def test_preflight_uses_explicit_frozen_engine_for_first_generation_without_workflow():
    groups = [_group(0), _group(1), _group(2)]
    target = groups[1]
    inputs = [
        _input(groups[0], candidate_id="candidate_task-0", speech_span_ms=1_000),
        _input(groups[2], candidate_id="candidate_task-2", speech_span_ms=1_000),
    ]
    # The target is about to be submitted, so its workflow does not exist yet.
    workflows = [
        _workflow(groups[0], candidate_id="candidate_task-0"),
        _workflow(groups[2], candidate_id="candidate_task-2"),
    ]

    result = assess_group_preflight(
        _draft(groups, inputs, workflows), target, speed=1.1,
        engine_id="omnivoice",
    )

    assert result.status == "ready"
    assert result.estimated_speech_duration_ms == 1_000
    assert result.estimate_evidence.confidence == "medium"


def test_preflight_warns_when_frozen_estimate_exceeds_user_edited_neighbor_window():
    groups = [_group(0), _group(1), _group(2)]
    target = groups[1]
    inputs = [_input(groups[0], candidate_id="candidate_task-0", speech_span_ms=1_200),
              _input(groups[2], candidate_id="candidate_task-2", speech_span_ms=1_200)]
    workflows = [_workflow(group, candidate_id=f"candidate_task-{index}")
                 for index, group in enumerate(groups)]
    clips = [
        {"clip_id": "left", "track_id": "dub", "status": "ready", "dub_lane": 0,
         "dubbing_group_id": groups[0].group_id, "start_ms": 0, "end_ms": 2_600},
        {"clip_id": "right", "track_id": "dub", "status": "ready", "dub_lane": 0,
         "dubbing_group_id": groups[2].group_id, "start_ms": 3_300, "end_ms": 5_000},
    ]

    result = assess_group_preflight(_draft(groups, inputs, workflows, clips), target, speed=1.1)

    assert (result.usable_start_ms, result.usable_end_ms) == (2_600, 3_300)
    assert result.status == "warning"
    assert result.reason_codes == ["estimated_speech_exceeds_usable_window"]


def test_preflight_blocks_only_an_objectively_empty_window():
    groups = [_group(0), _group(1), _group(2)]
    target = groups[1]
    workflows = [_workflow(group, candidate_id=f"candidate_task-{index}")
                 for index, group in enumerate(groups)]
    clips = [
        {"clip_id": "left", "track_id": "dub", "status": "ready", "dub_lane": 0,
         "dubbing_group_id": groups[0].group_id, "start_ms": 0, "end_ms": 4_200},
        {"clip_id": "right", "track_id": "dub", "status": "ready", "dub_lane": 0,
         "dubbing_group_id": groups[2].group_id, "start_ms": 3_900, "end_ms": 5_000},
    ]

    result = assess_group_preflight(_draft(groups, (), workflows, clips), target, speed=1.1)

    assert result.status == "blocked"
    assert result.reason_codes == ["no_usable_window"]
    assert result.estimated_speech_duration_ms is None


def test_preflight_excludes_discarded_and_other_speaker_candidates_without_magic_rate():
    target = _group(1, text="ABCDE")
    discarded_group = _group(0, text="ABCDE")
    other_speaker_group = _group(2, speaker="speaker-b", text="ABCDE")
    groups = [discarded_group, target, other_speaker_group]
    inputs = [
        _input(discarded_group, candidate_id="candidate_discarded", speech_span_ms=200),
        _input(other_speaker_group, candidate_id="candidate_other", speech_span_ms=10_000),
    ]
    workflows = [_workflow(group, candidate_id=candidate_id)
                 for group, candidate_id in [
                     (discarded_group, "candidate_discarded"),
                     (target, "candidate_target"),
                     (other_speaker_group, "candidate_other"),
                 ]]

    result = assess_group_preflight(
        _draft(groups, inputs, workflows, discarded=("discarded",)), target, speed=1.1,
    )

    assert result.status == "warning"
    assert result.estimated_speech_duration_ms is None
    assert result.reason_codes == ["speech_duration_estimate_unavailable"]
