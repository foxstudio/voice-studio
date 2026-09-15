from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import GenerateRequest  # noqa: E402
from app.schemas.voice_studio import GenerationTask, TaskStatus  # noqa: E402
from app.domains.video_localization.dubbing_speed_policy import (  # noqa: E402
    DubbingSpeedDecision,
    decide_dubbing_speed,
)
from app.services import video_localization_dubbing_executor as executor  # noqa: E402
from app.services import task_queue  # noqa: E402
from app.domains.video_localization.dubbing_generation_identity import (  # noqa: E402
    RUNTIME_FIELDS,
    compatible_generated_identities,
    frozen_group_request,
)
from app.domains.video_localization.tts_parameter_pack import TtsParameterPack  # noqa: E402
from app.domains.video_localization.tts_selection import (  # noqa: E402
    TtsSelectionRequest,
    build_selection_snapshot,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
)
from app.schemas.video_localization_dubbing_recovery import (  # noqa: E402
    DubbingRecoveryDecision,
)


def _group():
    return SimpleNamespace(
        group_id="group_localized_1_localized_1_1",
        unit_ids=["unit_1"],
        subtitle_ids=["localized_1"],
        speaker_id="speaker_1",
        spoken_text="这是需要连续表达的一句话",
        target_start_ms=1_000,
        target_end_ms=3_000,
    )


def _draft(group):
    unit = SimpleNamespace(
        unit_id="unit_1",
        source_cue_ids=["cue_1"],
    )
    plan = SimpleNamespace(
        source_revision="a" * 64,
        plan_revision=1,
        groups=[group],
        semantic_units=[unit],
    )
    state = SimpleNamespace(
        active_plan=plan,
        candidate_reports=[],
    )
    return SimpleNamespace(
        dubbing_production=state,
        tts_tasks=[],
        timeline_clips=[],
    )


def _frozen_retry_request(group):
    return GenerateRequest(
        text=group.spoken_text,
        engine_id="omnivoice",
        project_id="project-1",
        source="video_localization",
        bind_to_video_localization=True,
        video_localization_dubbing_group_id=group.group_id,
        video_localization_dubbing_plan_revision=1,
        video_localization_target_subtitle_ids=list(group.subtitle_ids),
        video_localization_source_cue_ids=["cue_1"],
        video_localization_generation_attempt=1,
        speed=1.05,
        language="zh",
        ref_text="Please wait here.",
        reference_audio_path="/managed/reference.wav",
        custom_reference_source_audio_path="/managed/vocals.wav",
        custom_reference_trim_start_ms=1_000,
        custom_reference_trim_end_ms=4_000,
        engine_parameters={"num_step": 20},
    )


def _draft_with_frozen_parameter_pack(*, changed_speaker=False):
    group = _group()
    draft = VideoLocalizationDraft(
        localized_subtitles=[VideoLocalizationSubtitleCue(
            subtitle_id="localized_1", start_ms=1_000, end_ms=3_000,
            text=group.spoken_text, tts_text=group.spoken_text,
            source_cue_ids=["cue_1"],
        )],
        cues=[VideoLocalizationCue(
            cue_id="cue_1", speaker_id="speaker-1", start_ms=1_000,
            end_ms=3_000, en_subtitle_text="Please wait here.",
        )],
    )
    selection = build_selection_snapshot(
        draft,
        TtsSelectionRequest(
            target_subtitle_ids=["localized_1"], source_cue_ids=["cue_1"],
        ),
    )
    request = _frozen_retry_request(group)
    pack = TtsParameterPack(
        project_id="project-1", target=selection.target, source=selection.source,
        request=request,
    )
    active_plan = SimpleNamespace(
        source_revision="a" * 64, plan_revision=2, groups=[group],
        semantic_units=[SimpleNamespace(unit_id="unit_1", source_cue_ids=["cue_1"])],
    )
    historical = request.model_copy(update={
        "video_localization_dubbing_plan_revision": 1,
    }).model_dump(mode="json")
    historical["video_localization_parameter_pack"] = pack.model_dump(mode="json")
    if changed_speaker:
        draft.cues[0] = draft.cues[0].model_copy(update={"speaker_id": "speaker-2"})
    return draft.model_copy(update={
        "dubbing_production": SimpleNamespace(active_plan=active_plan),
        "tts_tasks": [SimpleNamespace(
            workflow_id="workflow-old", generation_task_id="task-old",
            result_id="result-old", status="success",
            stages=[SimpleNamespace(
                kind="generation", status="success", parameters=historical,
            )],
        )],
    }), group


def test_durable_attempt_count_uses_submitted_workflow_parameters():
    draft = SimpleNamespace(
        tts_tasks=[
            SimpleNamespace(
                stages=[
                    SimpleNamespace(
                        parameters={
                            "video_localization_dubbing_group_id": "group-1",
                            "video_localization_generation_attempt": 2,
                        }
                    )
                ]
            ),
            SimpleNamespace(
                stages=[
                    SimpleNamespace(
                        parameters={
                            "video_localization_dubbing_group_id": "group-2",
                            "video_localization_generation_attempt": 9,
                        }
                    )
                ]
            ),
        ]
    )

    assert executor._durable_group_attempt_count(draft, "group-1") == 2


def test_durable_attempt_count_ignores_workflows_from_stale_plan_revision():
    draft = SimpleNamespace(
        tts_tasks=[
            SimpleNamespace(
                stages=[
                    SimpleNamespace(
                        parameters={
                            "video_localization_dubbing_group_id": "group-1",
                            "video_localization_dubbing_plan_revision": 187,
                            "video_localization_generation_attempt": 2,
                        }
                    )
                ]
            )
        ],
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(plan_revision=189),
            candidate_inputs=[],
            candidate_reports=[],
        ),
        timeline_clips=[],
    )

    assert executor._durable_group_attempt_count(draft, "group-1") == 0


def test_durable_attempt_count_ignores_explicitly_discarded_workflow():
    draft = SimpleNamespace(
        tts_tasks=[
            SimpleNamespace(
                workflow_id="workflow-old",
                generation_task_id="task-old",
                result_id="result-old",
                stages=[
                    SimpleNamespace(
                        parameters={
                            "video_localization_dubbing_group_id": "group-1",
                            "video_localization_dubbing_plan_revision": 1,
                            "video_localization_generation_attempt": 2,
                        }
                    )
                ],
            )
        ],
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(plan_revision=1),
            candidate_inputs=[],
            candidate_reports=[],
        ),
        timeline_clips=[],
        ui_state={"discarded_tts_task_ids": ["task-old"]},
    )

    assert executor._durable_group_attempt_count(draft, "group-1") == 0


def test_durable_attempt_count_includes_distinct_generated_candidates():
    draft = SimpleNamespace(
        tts_tasks=[],
        dubbing_production=SimpleNamespace(
            candidate_inputs=[
                SimpleNamespace(group_id="group-1", candidate_id="candidate-1"),
                SimpleNamespace(group_id="group-1", candidate_id="candidate-2"),
            ],
            candidate_reports=[
                SimpleNamespace(group_id="group-1", candidate_id="candidate-1"),
                SimpleNamespace(group_id="group-2", candidate_id="candidate-9"),
            ],
        ),
        timeline_clips=[],
    )

    assert executor._durable_group_attempt_count(draft, "group-1") == 2


def test_only_bounded_whole_run_uses_pipelined_closeout():
    task = GenerationTask(
        task_id="task-pipeline",
        generation_id="task-pipeline",
        engine_id="omnivoice",
        status=TaskStatus.queued,
        input_text="测试",
        parameters={
            "text": "测试",
            "engine_id": "omnivoice",
            "video_localization_execution_scope": "all_remaining",
            "video_localization_max_in_flight_groups": 2,
        },
    )

    assert task_queue._uses_pipelined_closeout(task) is True
    task.parameters["video_localization_execution_scope"] = "single_group"
    assert task_queue._uses_pipelined_closeout(task) is False


def test_supervised_review_mode_survives_worker_task_round_trip():
    task = GenerationTask(
        task_id="task-supervised",
        generation_id="task-supervised",
        engine_id="omnivoice",
        status=TaskStatus.queued,
        input_text="测试",
        parameters={
            "text": "测试",
            "video_localization_dubbing_review_mode": "supervised",
        },
    )

    assert executor._task_review_mode(task) == "supervised"

    request = GenerateRequest(
        text="测试",
        engine_id="omnivoice",
        video_localization_dubbing_review_mode="supervised",
    )
    assert request.video_localization_dubbing_review_mode == "supervised"


def test_execution_window_survives_worker_task_round_trip():
    task = GenerationTask(
        task_id="task-window",
        generation_id="task-window",
        engine_id="omnivoice",
        status=TaskStatus.queued,
        input_text="测试",
        parameters={
            "text": "测试",
            "video_localization_execution_start_group_id": "group-80",
            "video_localization_execution_end_group_id": "group-139",
            "video_localization_max_in_flight_groups": 2,
            "video_localization_ordinary_speed_baseline": 1.18,
        },
    )

    assert executor._task_execution_window(task) == executor.DubbingExecutionWindow(
        start_group_id="group-80",
        end_group_id="group-139",
        max_in_flight_groups=2,
        ordinary_speed_baseline=1.18,
    )


def test_frozen_run_baseline_keeps_each_ordinary_group_at_the_requested_speed():
    group = _group()
    _configure(
        decide_generation_speed=lambda _draft, _group: DubbingSpeedDecision(
            speed=1.0,
            proposed_speed=1.0,
            baseline_speed=1.0,
            baseline_clip_id="older-clip",
            source_pace_ratio=0.7,
            content_speed_exception_reason="source pace",
            content_speed_exception_evidence_ids=("source",),
        ),
    )

    parameters, decision = executor._generation_parameters(
        _draft(group),
        group,
        ordinary_speed_baseline=1.18,
    )

    assert decision.speed == 1.18
    assert decision.baseline_speed == 1.18
    assert decision.content_speed_exception_reason is None
    assert parameters == {"engine_id": "omnivoice", "speed": 1.18}


def test_frozen_baseline_ignores_stale_placed_neighbor_when_previous_group_is_in_flight():
    placed = SimpleNamespace(
        group_id="group-placed",
        unit_ids=["unit-placed"],
        subtitle_ids=["subtitle-placed"],
        spoken_text="上一组",
        target_start_ms=0,
        target_end_ms=1_000,
    )
    in_flight = SimpleNamespace(
        group_id="group-in-flight",
        unit_ids=["unit-in-flight"],
        subtitle_ids=["subtitle-in-flight"],
        spoken_text="正在生成的上一组",
        target_start_ms=1_100,
        target_end_ms=2_100,
    )
    current = SimpleNamespace(
        group_id="group-current",
        unit_ids=["unit-current"],
        subtitle_ids=["subtitle-current"],
        spoken_text="短句",
        target_start_ms=2_200,
        target_end_ms=4_200,
    )
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(
                groups=[placed, in_flight, current],
                semantic_units=[
                    SimpleNamespace(unit_id="unit-placed", source_cue_ids=[]),
                    SimpleNamespace(unit_id="unit-in-flight", source_cue_ids=[]),
                    SimpleNamespace(unit_id="unit-current", source_cue_ids=[]),
                ],
            ),
            candidate_reports=[],
        ),
        # This is the last adopted neighbor the speed policy can see.
        timeline_clips=[{
            "clip_id": "clip-placed",
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "end_ms": 1_000,
            "target_subtitle_ids": ["subtitle-placed"],
            "task_id": "task-placed",
        }],
        tts_tasks=[
            SimpleNamespace(
                generation_task_id="task-placed",
                stages=[SimpleNamespace(kind="generation", parameters={"speed": 1.10})],
            ),
            # This is the actual immediately previous group, but it has no
            # formal clip yet because both groups are in the same pipeline.
            SimpleNamespace(
                workflow_id="workflow-in-flight",
                stages=[SimpleNamespace(
                    kind="generation",
                    parameters={
                        "speed": 1.15,
                        "video_localization_target_subtitle_ids": ["subtitle-in-flight"],
                    },
                )],
            ),
        ],
        cues=[],
        transcription=None,
        stems=SimpleNamespace(separation_status="completed"),
    )
    _configure()

    unconstrained = decide_dubbing_speed(draft, current)
    parameters, decision = executor._generation_parameters(
        draft,
        current,
        ordinary_speed_baseline=1.10,
    )

    assert unconstrained.speed == 1.05
    assert decision.speed == 1.10
    assert decision.baseline_speed == 1.10
    assert parameters == {"engine_id": "omnivoice", "speed": 1.10}


def _configure(**overrides):
    defaults = {
        "read_production_run": lambda _project_id: None,
        "get_video_localization": lambda _project_id: None,
        "frozen_group_request": frozen_group_request,
        "compatible_generated_identities": compatible_generated_identities,
        "retry_runtime_fields": RUNTIME_FIELDS,
        "reserve_single_tts_handoff": (
            lambda *_args, **_kwargs: SimpleNamespace(workflow_id="workflow-reserved")
        ),
        "build_single_tts_handoff": lambda *_args, **_kwargs: None,
        "finalize_generated_candidate": (
            lambda _project_id, _candidate_id, _group_id, **_kwargs: "accepted"
        ),
        "recover_and_finalize_generated_group": (
            lambda _project_id, _group_id, _candidate_ids, **_kwargs: None
        ),
        "find_continuous_boundary_underfill_groups": (
            lambda _project_id, _group_ids: []
        ),
        "mark_workflow_placement_failed": lambda *_args, **_kwargs: None,
        "reconcile_workflow_terminal_states": lambda _project_id: None,
        "record_group_failure": lambda *_args, **_kwargs: None,
        "decide_generation_speed": decide_dubbing_speed,
    }
    defaults.update(overrides)
    executor.configure_projection(
        executor.DubbingExecutionProjection(**defaults)
    )


@pytest.mark.asyncio
async def test_bounded_run_reports_complete_without_rewriting_other_timeline_groups():
    groups = [
        SimpleNamespace(
            group_id=f"group-{index}",
            stage="accepted",
            recommended_action="complete",
            candidate_ids=[f"candidate-{index}"],
            attempt_count=1,
        )
        for index in range(1, 4)
    ]
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=groups,
        ),
    )

    response = await executor.advance(
        "project-window-complete",
        scope="all_remaining",
        start_group_id="group-2",
        end_group_id="group-3",
    )

    assert response.status == "complete"


@pytest.mark.asyncio
async def test_bounded_run_retries_one_underfilled_boundary_before_completion(
    monkeypatch,
):
    groups = [
        SimpleNamespace(
            group_id=f"group-{index}",
            stage="accepted",
            recommended_action="complete",
            candidate_ids=[f"candidate-{index}"],
            attempt_count=1,
        )
        for index in range(1, 3)
    ]
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=None,
            candidate_inputs=[],
            candidate_reports=[],
        ),
        tts_tasks=[
            SimpleNamespace(
                stages=[
                    SimpleNamespace(
                        parameters={
                            "video_localization_dubbing_group_id": "group-1",
                            "video_localization_generation_attempt": 1,
                        }
                    )
                ]
            )
        ],
        timeline_clips=[],
    )
    calls = []

    async def fake_advance(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status="queued")

    monkeypatch.setattr(executor, "_advance_unlocked", fake_advance)
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=groups,
        ),
        get_video_localization=lambda _project_id: draft,
        find_continuous_boundary_underfill_groups=(
            lambda _project_id, _group_ids: ["group-1"]
        ),
    )

    response = await executor.advance(
        "project-window-underfill",
        scope="all_remaining",
        start_group_id="group-1",
        end_group_id="group-2",
    )

    assert response.status == "queued"
    assert calls[0][1]["target_group_id"] == "group-1"
    assert calls[0][1]["regenerate_existing"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('predecessor_stage', [None, 'deferred_manual_timing'])
async def test_full_run_recovers_existing_group_through_shared_finisher_before_advancing(
    monkeypatch, predecessor_stage,
):
    group = _group()
    first = SimpleNamespace(
        group_id=group.group_id,
        stage="needs_timeline_work",
        recommended_action="place_candidate",
        candidate_ids=["candidate-1"],
        attempt_count=1,
    )
    completed = SimpleNamespace(
        group_id=group.group_id,
        stage="accepted",
        recommended_action="complete",
        candidate_ids=["candidate-1"],
        attempt_count=1,
    )
    predecessor = ([SimpleNamespace(group_id='earlier-group', stage=predecessor_stage,
                    recommended_action='complete', candidate_ids=['earlier-candidate'], attempt_count=2)]
                   if predecessor_stage else [])
    runs = iter(
        [
            SimpleNamespace(
                next_action="place_candidate",
                next_group_id=group.group_id,
                groups=[*predecessor, first],
            ),
            SimpleNamespace(
                next_action="complete",
                next_group_id=None,
                groups=[*predecessor, completed],
            ),
        ]
    )
    calls: list[tuple[str, str, list[str]]] = []
    _configure(
        read_production_run=lambda _project_id: next(runs),
        recover_and_finalize_generated_group=(
            lambda project_id, group_id, candidate_ids: (
                calls.append((project_id, group_id, candidate_ids)) or "accepted"
            )
        ),
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail('Saved candidates must not generate again'),
    )

    result = await executor.advance("project-1", scope="all_remaining")
    await asyncio.sleep(0.01)

    assert result.status == "queued"
    assert calls == [("project-1", group.group_id, ["candidate-1"])]


@pytest.mark.asyncio
async def test_concurrent_advancers_submit_only_one_group(monkeypatch):
    group = _group()
    state = {"generating": False, "reserved": 0}

    def read_run(_project_id):
        progress = SimpleNamespace(
            group_id=group.group_id,
            stage=("generating" if state["generating"] else "ready_to_generate"),
            recommended_action=(
                "wait_for_generation"
                if state["generating"]
                else "generate_candidate"
            ),
            candidate_ids=[],
            attempt_count=0,
        )
        return SimpleNamespace(
            next_action=progress.recommended_action,
            next_group_id=group.group_id,
            groups=[progress],
        )

    def reserve(*_args, **_kwargs):
        state["reserved"] += 1
        state["generating"] = True
        return SimpleNamespace(workflow_id="workflow-1")

    _configure(
        read_production_run=read_run,
        get_video_localization=lambda _project_id: _draft(group),
        reserve_single_tts_handoff=reserve,
    )

    async def queue_stub(*_args, **_kwargs):
        return None

    monkeypatch.setattr(executor, "_queue_group", queue_stub)

    first, second = await asyncio.gather(
        executor.advance("project-concurrent", scope="all_remaining"),
        executor.advance("project-concurrent", scope="all_remaining"),
    )

    assert state["reserved"] == 1
    assert {first.status, second.status} == {"queued", "waiting"}


@pytest.mark.asyncio
async def test_all_remaining_fills_two_group_pipeline_inside_explicit_range(monkeypatch):
    groups = [
        SimpleNamespace(
            group_id=f"group-{index}",
            unit_ids=[f"unit-{index}"],
            subtitle_ids=[f"localized-{index}"],
            speaker_id="speaker-1",
            spoken_text=f"第{index}组",
            target_start_ms=index * 1_000,
            target_end_ms=(index + 1) * 1_000,
        )
        for index in range(1, 5)
    ]
    reserved: set[str] = set()

    def read_run(_project_id):
        progress = [
            SimpleNamespace(
                group_id=group.group_id,
                stage=("generating" if group.group_id in reserved else "ready_to_generate"),
                recommended_action=(
                    "wait_for_generation"
                    if group.group_id in reserved
                    else "generate_candidate"
                ),
                candidate_ids=[],
                attempt_count=0,
            )
            for group in groups
        ]
        return SimpleNamespace(
            next_action=progress[0].recommended_action,
            next_group_id=progress[0].group_id,
            groups=progress,
        )

    units = [
        SimpleNamespace(unit_id=f"unit-{index}", source_cue_ids=[f"cue-{index}"])
        for index in range(1, 5)
    ]
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(groups=groups, semantic_units=units),
            candidate_inputs=[],
            candidate_reports=[],
        ),
        tts_tasks=[],
        timeline_clips=[],
    )

    def reserve(_project_id, group_id, **_kwargs):
        reserved.add(group_id)
        return SimpleNamespace(workflow_id=f"workflow-{group_id}")

    _configure(
        read_production_run=read_run,
        get_video_localization=lambda _project_id: draft,
        reserve_single_tts_handoff=reserve,
    )

    async def queue_stub(*_args, **_kwargs):
        return None

    monkeypatch.setattr(executor, "_queue_group", queue_stub)

    result = await executor.advance(
        "project-window",
        scope="all_remaining",
        start_group_id="group-2",
        end_group_id="group-4",
        max_in_flight_groups=2,
    )
    await asyncio.sleep(0)

    assert result.status == "queued"
    assert result.queued_group_ids == ["group-2", "group-3"]
    assert reserved == {"group-2", "group-3"}
    assert "group-1" not in reserved
    assert "group-4" not in reserved


@pytest.mark.asyncio
async def test_full_run_recovers_exact_existing_formal_group_before_regenerating():
    group = _group()
    ready = SimpleNamespace(
        group_id=group.group_id,
        stage="ready_to_generate",
        recommended_action="generate_candidate",
        candidate_ids=[],
        attempt_count=0,
    )
    calls: list[tuple[str, str, list[str]]] = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="generate_candidate",
            next_group_id=group.group_id,
            groups=[ready],
        ),
        recover_and_finalize_generated_group=(
            lambda project_id, group_id, candidate_ids: (
                calls.append((project_id, group_id, candidate_ids)) or "accepted"
            )
        ),
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail(
            "an existing formal result must be finalized, not regenerated"
        ),
    )

    result = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
    )

    assert result.status == "complete"
    assert calls == [("project-1", group.group_id, [])]


@pytest.mark.asyncio
async def test_full_run_recovers_exact_rebased_unplaced_result_before_regenerating():
    group = _group()
    draft = _draft(group)
    draft.dubbing_production.active_plan.plan_revision = 3
    historical = _frozen_retry_request(group).model_copy(update={
        "video_localization_dubbing_plan_revision": 1,
    })
    draft.tts_tasks = [SimpleNamespace(
        generation_task_id="task-old-plan",
        result_id="result-old-plan",
        status="success",
        stages=[SimpleNamespace(
            kind="generation", status="success",
            parameters=historical.model_dump(mode="json"),
        )],
    )]
    ready = SimpleNamespace(
        group_id=group.group_id,
        stage="ready_to_generate",
        recommended_action="generate_candidate",
        candidate_ids=[],
        attempt_count=0,
    )
    calls: list[tuple[str, str, list[str]]] = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="generate_candidate",
            next_group_id=group.group_id,
            groups=[ready],
        ),
        get_video_localization=lambda _project_id: draft,
        recover_and_finalize_generated_group=(
            lambda project_id, group_id, candidate_ids: (
                calls.append((project_id, group_id, candidate_ids)) or "accepted"
            )
        ),
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail(
            "an exact old-plan result must be finalized, not regenerated"
        ),
    )

    result = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
    )

    assert result.status == "complete"
    assert calls == [(
        "project-1",
        group.group_id,
        ["result-old-plan", "task-old-plan", "candidate_task-old-plan"],
    )]


@pytest.mark.asyncio
async def test_explicit_failed_group_retry_replays_closeout_without_new_generation():
    group = _group()
    failed = SimpleNamespace(
        group_id=group.group_id,
        stage="failed",
        recommended_action="complete",
        candidate_ids=["candidate-existing"],
        attempt_count=1,
    )
    calls: list[tuple[str, str, list[str]]] = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[failed],
        ),
        recover_and_finalize_generated_group=(
            lambda project_id, group_id, candidate_ids: (
                calls.append((project_id, group_id, candidate_ids)) or "accepted"
            )
        ),
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail(
            "retrying local close-out must not submit TTS"
        ),
    )

    result = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
    )

    assert result.status == "complete"
    assert calls == [("project-1", group.group_id, ["candidate-existing"])]


@pytest.mark.asyncio
async def test_accepted_current_candidate_does_not_regenerate_for_historical_rejection():
    group = _group()
    accepted = SimpleNamespace(
        group_id=group.group_id,
        stage="accepted",
        recommended_action="complete",
        candidate_ids=["candidate-current", "candidate-rejected"],
        passed_candidate_id="candidate-current",
        attempt_count=1,
    )
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[accepted],
        ),
        find_continuous_boundary_underfill_groups=lambda *_args: [],
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail(
            "historical rejection must not submit TTS for an accepted group"
        ),
        recover_and_finalize_generated_group=lambda *_args, **_kwargs: pytest.fail(
            "historical rejection must not reopen an accepted group"
        ),
    )

    result = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
    )

    assert result.status == "complete"
    assert result.group_id == group.group_id


@pytest.mark.asyncio
async def test_explicit_failed_group_semantic_handoff_does_not_record_failure():
    group = _group()
    failed = SimpleNamespace(
        group_id=group.group_id,
        stage="failed",
        recommended_action="complete",
        candidate_ids=["candidate-existing"],
        attempt_count=1,
    )
    failures: list[tuple[object, ...]] = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[failed],
        ),
        recover_and_finalize_generated_group=(
            lambda *_args, **_kwargs: "needs_semantic_review"
        ),
        record_group_failure=lambda *args, **_kwargs: failures.append(args),
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail(
            "semantic handoff must not submit TTS"
        ),
    )

    result = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
    )

    assert result.status == "needs_attention"
    assert result.group_id == group.group_id
    assert "正在检查断句" in result.message
    assert failures == []


@pytest.mark.asyncio
async def test_failed_group_queues_one_frozen_retry_only_after_local_closeout_requires_it(
    monkeypatch,
):
    group = _group()
    failed = SimpleNamespace(
        group_id=group.group_id,
        stage="failed",
        recommended_action="complete",
        candidate_ids=["candidate-existing"],
        workflow_ids=["workflow-existing"],
        attempt_count=1,
    )
    draft = _draft(group)
    request = _frozen_retry_request(group)
    draft.tts_tasks = [
        SimpleNamespace(
            stages=[
                SimpleNamespace(
                    kind="generation",
                    parameters=request.model_dump(mode="json"),
                )
            ]
        )
    ]
    draft.dubbing_production.candidate_inputs = [
        SimpleNamespace(
            group_id=group.group_id,
            candidate_id="candidate-existing",
            source_revision=draft.dubbing_production.active_plan.source_revision,
            plan_revision=1,
        )
    ]
    queued = []
    closed = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[failed],
        ),
        get_video_localization=lambda _project_id: draft,
        recover_and_finalize_generated_group=lambda *_args, **_kwargs: "regeneration_required",
        mark_workflow_placement_failed=lambda project_id, workflow_id, **_kwargs: closed.append(
            (project_id, workflow_id)
        ),
    )

    async def queue_retry(project_id, **kwargs):
        queued.append((project_id, kwargs))
        return SimpleNamespace(status="queued", scope="single_group", group_id=group.group_id)

    monkeypatch.setattr(executor, "_queue_group_for_review_mode", queue_retry)

    response = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
    )

    assert response.status == "queued"
    assert closed == [("project-1", "workflow-existing")]
    assert queued[0][0] == "project-1"
    assert queued[0][1]["attempt"] == 2
    assert queued[0][1]["generation_parameters"] == request.model_dump(mode="json")
    assert queued[0][1]["frozen_retry_request"] == request
    assert not draft.timeline_clips


@pytest.mark.asyncio
async def test_capacity_handoff_is_machine_readable_and_does_not_queue_tts(
    monkeypatch,
):
    group = _group()
    failed = SimpleNamespace(
        group_id=group.group_id,
        stage="failed",
        recommended_action="complete",
        candidate_ids=["candidate-existing"],
        workflow_ids=["workflow-existing"],
        attempt_count=1,
    )
    draft = _draft(group)
    failures = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[failed],
        ),
        get_video_localization=lambda _project_id: draft,
        recover_and_finalize_generated_group=(
            lambda *_args, **_kwargs: "capacity_recovery_required"
        ),
        record_group_failure=lambda project_id, request: failures.append(
            (project_id, request)
        ),
    )
    queued = []

    async def fake_queue(*args, **kwargs):
        queued.append((args, kwargs))

    monkeypatch.setattr(executor, "_queue_whole_group_recovery", fake_queue)

    response = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
    )

    assert response.status == "needs_attention"
    assert response.required_action == "resolve_capacity"
    assert queued == []
    assert failures[0][1].reason_code == "group_capacity_recovery_decision_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["needs_gap_processing", "failed"])
async def test_explicit_capacity_retry_replaces_only_named_open_receipts(monkeypatch, stage):
    group = _group()
    draft = _draft(group)
    progress = SimpleNamespace(group_id=group.group_id, stage=stage,
        recommended_action="process_gaps", candidate_ids=["candidate-existing"],
        workflow_ids=["workflow-existing"], attempt_count=1,
        target_subtitle_ids=list(group.subtitle_ids))
    draft.dubbing_production.candidate_inputs = [SimpleNamespace(
        group_id=group.group_id, candidate_id="candidate-existing",
        source_revision=draft.dubbing_production.active_plan.source_revision, plan_revision=1)]
    captured = {}
    def reserve(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(workflow_id="new-capacity-workflow")
    def speed(_draft, _group, *, ordinary_speed_baseline):
        assert ordinary_speed_baseline == 1.0
        return DubbingSpeedDecision(speed=1.05, proposed_speed=1.05, baseline_speed=1.0)
    _configure(read_production_run=lambda _: SimpleNamespace(next_action="process_gaps", next_group_id=group.group_id, groups=[progress]),
        get_video_localization=lambda _: draft, reserve_single_tts_handoff=reserve,
        decide_capacity_repair_speed=speed,
        mark_workflow_placement_failed=lambda *a, **k: pytest.fail("keep original candidate recoverable"))
    async def queue(*args, **kwargs):
        captured["queued"] = kwargs
    monkeypatch.setattr(executor, "_queue_group", queue)
    monkeypatch.setattr(executor, "_queue_failed_group_recovery", lambda *a, **k: pytest.fail("capacity must not use unchanged-speed quality retry"))
    response = await executor.advance("project-1", scope="single_group", group_id=group.group_id,
        regenerate_existing=True, repair_timeline_capacity=True, ordinary_speed_baseline=1.0)
    await asyncio.sleep(0)
    assert response.status == "queued"
    assert captured["capacity_replaces_workflow_ids"] == ["workflow-existing"]
    assert captured["parameters"]["speed"] == 1.05
    assert captured["parameters"]["video_localization_dubbing_plan_revision"] == 1
    assert captured["parameters"]["video_localization_dubbing_group_id"] == group.group_id
    assert captured["queued"]["attempt"] == 2
    assert not draft.timeline_clips


@pytest.mark.asyncio
async def test_explicit_capacity_whole_retry_keeps_frozen_speed_and_open_receipt(
    monkeypatch,
):
    group = _group()
    request = _frozen_retry_request(group)
    progress = SimpleNamespace(
        group_id=group.group_id,
        stage="needs_gap_processing",
        recommended_action="process_gaps",
        candidate_ids=["candidate-existing"],
        workflow_ids=["workflow-existing"],
        attempt_count=1,
        target_subtitle_ids=list(group.subtitle_ids),
    )
    draft = _draft(group)
    draft.tts_tasks = [
        SimpleNamespace(
            workflow_id="workflow-existing",
            stages=[
                SimpleNamespace(
                    kind="generation",
                    parameters=request.model_dump(mode="json"),
                )
            ],
        )
    ]
    draft.dubbing_production.candidate_inputs = [
        SimpleNamespace(
            group_id=group.group_id,
            candidate_id="candidate-existing",
            source_revision=draft.dubbing_production.active_plan.source_revision,
            plan_revision=1,
        )
    ]
    draft.dubbing_production.group_failures = [
        SimpleNamespace(
            group_id=group.group_id,
            candidate_id="candidate-existing",
            source_revision=draft.dubbing_production.active_plan.source_revision,
            plan_revision=1,
            reason_code="group_capacity_recovery_decision_required",
        )
    ]
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="process_gaps",
            next_group_id=group.group_id,
            groups=[progress],
        ),
        get_video_localization=lambda _project_id: draft,
        mark_workflow_placement_failed=lambda *_args, **_kwargs: pytest.fail(
            "the original capacity receipt must stay recoverable"
        ),
    )
    queued = []

    async def queue_retry(project_id, **kwargs):
        queued.append((project_id, kwargs))
        return SimpleNamespace(
            status="queued",
            scope="single_group",
            group_id=group.group_id,
        )

    monkeypatch.setattr(executor, "_queue_group_for_review_mode", queue_retry)

    response = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
        regenerate_existing=True,
    )

    assert response.status == "queued"
    assert queued[0][1]["attempt"] == 2
    assert queued[0][1]["generation_parameters"]["speed"] == request.speed
    assert queued[0][1]["frozen_retry_request"] == request
    assert queued[0][1]["capacity_replaces_workflow_ids"] == [
        "workflow-existing"
    ]


@pytest.mark.asyncio
async def test_explicit_whole_retry_requires_current_capacity_failure():
    group = _group()
    progress = SimpleNamespace(
        group_id=group.group_id,
        stage="needs_gap_processing",
        recommended_action="process_gaps",
        candidate_ids=["candidate-existing"],
        workflow_ids=["workflow-existing"],
        attempt_count=1,
        target_subtitle_ids=list(group.subtitle_ids),
    )
    draft = _draft(group)
    draft.dubbing_production.candidate_inputs = [
        SimpleNamespace(
            group_id=group.group_id,
            candidate_id="candidate-existing",
            source_revision=draft.dubbing_production.active_plan.source_revision,
            plan_revision=1,
        )
    ]
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="process_gaps",
            next_group_id=group.group_id,
            groups=[progress],
        ),
        get_video_localization=lambda _project_id: draft,
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail(
            "capacity evidence is required before another generation"
        ),
    )

    with pytest.raises(executor.AppException) as caught:
        await executor.advance(
            "project-1",
            scope="single_group",
            group_id=group.group_id,
            regenerate_existing=True,
        )

    assert caught.value.code == "VIDEO_LOCALIZATION_DUBBING_ACCEPTED_GROUP_REQUIRED"


@pytest.mark.asyncio
async def test_explicit_failed_group_regeneration_uses_frozen_retry_without_formal_clip(
    monkeypatch,
):
    group = _group()
    failed = SimpleNamespace(
        group_id=group.group_id,
        stage="failed",
        recommended_action="complete",
        candidate_ids=["candidate-existing"],
        workflow_ids=["workflow-existing"],
        attempt_count=1,
        target_subtitle_ids=list(group.subtitle_ids),
    )
    draft = _draft(group)
    request = _frozen_retry_request(group)
    draft.tts_tasks = [
        SimpleNamespace(
            stages=[
                SimpleNamespace(
                    kind="generation",
                    parameters=request.model_dump(mode="json"),
                )
            ]
        )
    ]
    draft.dubbing_production.candidate_inputs = [
        SimpleNamespace(
            group_id=group.group_id,
            candidate_id="candidate-existing",
            source_revision=draft.dubbing_production.active_plan.source_revision,
            plan_revision=1,
        )
    ]
    queued = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[failed],
        ),
        get_video_localization=lambda _project_id: draft,
    )

    async def queue_retry(project_id, **kwargs):
        queued.append((project_id, kwargs))
        return SimpleNamespace(status="queued", scope="single_group", group_id=group.group_id)

    monkeypatch.setattr(executor, "_queue_group_for_review_mode", queue_retry)

    response = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
        regenerate_existing=True,
    )

    assert response.status == "queued"
    assert queued[0][1]["attempt"] == 2
    assert queued[0][1]["generation_parameters"] == request.model_dump(mode="json")
    assert queued[0][1]["frozen_retry_request"] == request
    assert not draft.timeline_clips


@pytest.mark.asyncio
async def test_explicit_failed_group_regeneration_rejects_deleted_candidate_evidence():
    group = _group()
    failed = SimpleNamespace(
        group_id=group.group_id,
        stage="failed",
        recommended_action="complete",
        candidate_ids=["candidate-deleted"],
        attempt_count=1,
        target_subtitle_ids=list(group.subtitle_ids),
    )
    draft = _draft(group)
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[failed],
        ),
        get_video_localization=lambda _project_id: draft,
        reserve_single_tts_handoff=lambda *_args, **_kwargs: pytest.fail(
            "deleted candidate evidence must not reserve another generation"
        ),
    )

    with pytest.raises(executor.AppException) as caught:
        await executor.advance(
            "project-1",
            scope="single_group",
            group_id=group.group_id,
            regenerate_existing=True,
        )

    assert caught.value.code == "VIDEO_LOCALIZATION_DUBBING_ACCEPTED_GROUP_REQUIRED"
    assert not draft.timeline_clips


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_attempts,durable_candidates", [(1, 1), (2, 2), (4, 4), (1, 3)])
async def test_explicit_regeneration_keeps_existing_formal_clip_until_atomic_replacement(
    monkeypatch, previous_attempts, durable_candidates,
):
    group = _group()
    accepted = SimpleNamespace(
        group_id=group.group_id,
        stage="accepted",
        recommended_action="complete",
        candidate_ids=["candidate-existing"],
        attempt_count=previous_attempts,
    )
    draft = _draft(group)
    draft.dubbing_production.candidate_inputs = [
        SimpleNamespace(group_id=group.group_id, candidate_id=f"candidate-{index}", plan_revision=1)
        for index in range(durable_candidates)
    ]
    draft.timeline_clips = [
        {
            "clip_id": "clip-existing",
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "start_ms": 1_000,
            "end_ms": 3_000,
            "target_subtitle_ids": list(group.subtitle_ids),
        }
    ]
    captured: dict[str, object] = {}

    def reserve(_project_id, _group_id, **kwargs):
        captured["existing_clip_count_at_reserve"] = len(draft.timeline_clips)
        captured["parameters"] = kwargs["parameters"]
        return SimpleNamespace(workflow_id="workflow-regenerated")

    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[accepted],
        ),
        get_video_localization=lambda _project_id: draft,
        reserve_single_tts_handoff=reserve,
        decide_capacity_repair_speed=lambda _draft, _group: SimpleNamespace(
            speed=1.35,
            proposed_speed=1.35,
            baseline_speed=1.0,
            baseline_clip_id="clip-existing",
            source_pace_ratio=None,
            content_speed_exception_reason="timeline capacity repair",
            generation_parameters=lambda: {
                "speed": 1.35,
                "content_speed_exception_reason": (
                    "timeline capacity repair"
                ),
                "content_speed_exception_evidence_ids": [
                    "timeline-capacity:group-1"
                ],
            },
        ),
    )

    async def queue_stub(*_args, **kwargs):
        captured["queued_parameters"] = kwargs["generation_parameters"]
        captured["queued_attempt"] = kwargs["attempt"]

    monkeypatch.setattr(executor, "_queue_group", queue_stub)

    response = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=group.group_id,
        regenerate_existing=True,
        repair_timeline_capacity=True,
    )
    await asyncio.sleep(0)

    assert response.status == "queued"
    assert captured["existing_clip_count_at_reserve"] == 1
    assert captured["parameters"] == captured["queued_parameters"]
    assert captured["parameters"]["speed"] == 1.35
    assert captured["queued_attempt"] == max(previous_attempts, durable_candidates) + 1


@pytest.mark.asyncio
async def test_frozen_run_regenerates_old_take_outside_speed_contract(monkeypatch):
    group = _group()
    accepted = SimpleNamespace(
        group_id=group.group_id,
        stage="accepted",
        recommended_action="complete",
        candidate_ids=["candidate-existing"],
        workflow_ids=["workflow-unplaceable"],
        attempt_count=1,
        speaking_rate_ratio=1.0,
    )
    draft = _draft(group)
    draft.timeline_clips = [
        {
            "clip_id": "clip-existing",
            "track_id": "dub",
            "status": "ready",
            "dub_lane": 0,
            "start_ms": 1_000,
            "end_ms": 3_000,
            "target_subtitle_ids": list(group.subtitle_ids),
        }
    ]
    captured: dict[str, object] = {}
    closed: list[tuple[str, str]] = []

    def reserve(_project_id, group_id, **kwargs):
        captured["group_id"] = group_id
        captured["parameters"] = kwargs["parameters"]
        captured["visible_clip_count"] = len(draft.timeline_clips)
        return SimpleNamespace(workflow_id="workflow-speed-replacement")

    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="complete",
            next_group_id=None,
            groups=[accepted],
        ),
        get_video_localization=lambda _project_id: draft,
        reserve_single_tts_handoff=reserve,
        mark_workflow_placement_failed=(
            lambda project_id, workflow_id, **_kwargs: closed.append(
                (project_id, workflow_id)
            )
        ),
    )

    async def queue_stub(*_args, **kwargs):
        captured["queued_parameters"] = kwargs["generation_parameters"]

    monkeypatch.setattr(executor, "_queue_group", queue_stub)

    response = await executor.advance(
        "project-speed-contract",
        scope="all_remaining",
        start_group_id=group.group_id,
        end_group_id=group.group_id,
        ordinary_speed_baseline=1.18,
    )
    await asyncio.sleep(0)

    assert response.status == "queued"
    assert captured["group_id"] == group.group_id
    assert captured["visible_clip_count"] == 1
    assert captured["parameters"] == captured["queued_parameters"]
    assert 1.13 <= captured["parameters"]["speed"] <= 1.23
    assert closed == [("project-speed-contract", "workflow-unplaceable")]


@pytest.mark.asyncio
async def test_semantic_recovery_closes_successful_placement_before_frozen_retry(
    monkeypatch,
):
    group = _group()
    frozen_request = _frozen_retry_request(group)
    draft = _draft(group)
    draft.dubbing_production.candidate_inputs = [SimpleNamespace(
        candidate_id="candidate-recover",
        group_id=group.group_id,
        source_revision=draft.dubbing_production.active_plan.source_revision,
        plan_revision=draft.dubbing_production.active_plan.plan_revision,
    )]
    draft.dubbing_production.candidate_reports = [SimpleNamespace(
        candidate_id="candidate-recover",
        group_id=group.group_id,
        source_revision=draft.dubbing_production.active_plan.source_revision,
        plan_revision=draft.dubbing_production.active_plan.plan_revision,
        semantic_boundary_audit=SimpleNamespace(status="recovery_required"),
    )]
    # Generation is already successful; only the placement stage is still
    # "running".  This is the durable workflow that otherwise keeps the
    # reservation busy after Agent requests a whole-group recovery.
    draft.tts_tasks = [SimpleNamespace(
        workflow_id="workflow-placed",
        stages=[SimpleNamespace(
            kind="generation", parameters=frozen_request.model_dump(mode="json"),
        )],
    )]
    progress = SimpleNamespace(
        group_id=group.group_id,
        stage="needs_regeneration",
        recommended_action="regenerate_candidate",
        candidate_ids=["candidate-recover"],
        workflow_ids=["workflow-placed"],
        attempt_count=1,
    )
    closed = []
    queued = []
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="regenerate_candidate",
            next_group_id=group.group_id,
            groups=[progress],
        ),
        get_video_localization=lambda _project_id: draft,
        mark_workflow_placement_failed=(
            lambda project_id, workflow_id, **kwargs: closed.append(
                (project_id, workflow_id, kwargs)
            )
        ),
    )

    async def queue_retry(project_id, **kwargs):
        queued.append((project_id, kwargs))
        return SimpleNamespace(status="queued", scope=kwargs["scope"], group_id=group.group_id)

    monkeypatch.setattr(executor, "_queue_group_for_review_mode", queue_retry)

    response = await executor.advance(
        "project-1", scope="single_group", group_id=group.group_id,
    )

    assert response.status == "queued"
    assert [(project_id, workflow_id) for project_id, workflow_id, _ in closed] == [
        ("project-1", "workflow-placed"),
    ]
    assert queued[0][1]["attempt"] == 2
    assert queued[0][1]["frozen_retry_request"] == frozen_request
    assert queued[0][1]["generation_parameters"] == frozen_request.model_dump(mode="json")


@pytest.mark.asyncio
async def test_full_run_queues_exactly_one_group_with_omnivoice(
    monkeypatch,
):
    group = _group()
    run = SimpleNamespace(
        next_action="generate_candidate",
        next_group_id=group.group_id,
        groups=[
            SimpleNamespace(
                group_id=group.group_id,
                stage="ready_to_generate",
                attempt_count=0,
            )
        ],
    )
    captured: dict[str, object] = {}

    def fake_handoff(project_id, segment_id, **kwargs):
        captured.update(
            project_id=project_id,
            segment_id=segment_id,
            kwargs=kwargs,
        )
        return GenerateRequest(
            text=group.spoken_text,
            source="video_localization",
            project_id=project_id,
            segment_id=kwargs["target_subtitle_ids"][0],
            bind_to_video_localization=True,
            video_localization_workflow_id=kwargs.get("workflow_id") or "workflow-1",
        )

    _configure(
        read_production_run=lambda _project_id: run,
        get_video_localization=lambda _project_id: _draft(group),
        build_single_tts_handoff=fake_handoff,
    )
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "finalize_submission",
        lambda request: request,
    )

    async def fake_submit(request, **kwargs):
        captured["request"] = request
        captured["submit_kwargs"] = kwargs
        return "task-1"

    monkeypatch.setattr(executor.task_queue, "submit", fake_submit)

    response = await executor.advance(
        "project-1",
        scope="all_remaining",
        review_mode="risk_based",
        start_group_id=group.group_id,
        end_group_id=group.group_id,
        ordinary_speed_baseline=1.18,
    )
    for _ in range(50):
        if "request" in captured:
            break
        await asyncio.sleep(0.01)

    request = captured["request"]
    assert response.status == "queued"
    assert response.task_id is None
    assert response.workflow_id == "workflow-reserved"
    assert request.engine_id == "omnivoice"
    assert request.video_localization_execution_scope == "all_remaining"
    assert request.video_localization_generation_attempt == 1
    assert request.video_localization_dubbing_review_mode == "risk_based"
    assert request.video_localization_execution_start_group_id == group.group_id
    assert request.video_localization_execution_end_group_id == group.group_id
    assert request.video_localization_ordinary_speed_baseline == 1.18
    assert captured["segment_id"] == group.group_id
    assert captured["submit_kwargs"]["segment_id"] == "localized_1"
    assert captured["kwargs"]["parameters"]["engine_id"] == "omnivoice"
    assert captured["kwargs"]["target_subtitle_ids"] == ["localized_1"]
    assert captured["kwargs"]["source_cue_ids"] == ["cue_1"]


@pytest.mark.asyncio
async def test_single_group_keeps_requested_ordinary_speed_baseline(
    monkeypatch,
):
    group = _group()
    run = SimpleNamespace(
        next_action="generate_candidate",
        next_group_id=group.group_id,
        groups=[
            SimpleNamespace(
                group_id=group.group_id,
                stage="ready_to_generate",
                attempt_count=0,
            )
        ],
    )
    captured: dict[str, object] = {}
    _configure(
        read_production_run=lambda _project_id: run,
        get_video_localization=lambda _project_id: _draft(group),
    )

    async def queue_stub(*_args, **kwargs):
        captured["window"] = kwargs["window"]
        return SimpleNamespace(
            status="queued",
            scope="single_group",
            group_id=group.group_id,
            message="queued",
        )

    monkeypatch.setattr(executor, "_queue_group", queue_stub)

    response = await executor.advance(
        "project-single-speed",
        scope="single_group",
        group_id=group.group_id,
        ordinary_speed_baseline=1.15,
    )
    for _ in range(50):
        if "window" in captured:
            break
        await asyncio.sleep(0.01)

    assert response.status == "queued"
    assert captured["window"].ordinary_speed_baseline == 1.15


@pytest.mark.asyncio
async def test_reference_preparation_does_not_block_status_polling(monkeypatch):
    group = _group()
    run = SimpleNamespace(
        next_action="generate_candidate",
        next_group_id=group.group_id,
        groups=[
            SimpleNamespace(
                group_id=group.group_id,
                stage="ready_to_generate",
                attempt_count=0,
            )
        ],
    )

    def slow_handoff(project_id, _segment_id, **_kwargs):
        time.sleep(0.08)
        return GenerateRequest(
            text=group.spoken_text,
            source="video_localization",
            project_id=project_id,
            segment_id="localized_1",
            bind_to_video_localization=True,
            video_localization_workflow_id=_kwargs.get("workflow_id") or "workflow-slow",
        )

    _configure(
        read_production_run=lambda _project_id: run,
        get_video_localization=lambda _project_id: _draft(group),
        build_single_tts_handoff=slow_handoff,
    )
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "finalize_submission",
        lambda request: request,
    )

    async def fake_submit(_request, **_kwargs):
        return "task-slow"

    monkeypatch.setattr(executor.task_queue, "submit", fake_submit)

    started = time.monotonic()
    response = await executor.advance(
        "project-1", scope="single_group", group_id=group.group_id
    )
    elapsed = time.monotonic() - started
    await asyncio.sleep(0.02)

    assert response.status == "queued"
    assert elapsed < 0.05
    assert any(not task.done() for task in executor._background_submissions)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_single_group_execution_queues_the_explicit_selected_group(
    monkeypatch,
):
    first = _group()
    selected = SimpleNamespace(
        **{
            **first.__dict__,
            "group_id": "group_localized_2_localized_2_1",
            "unit_ids": ["unit_2"],
            "subtitle_ids": ["localized_2"],
            "spoken_text": "这是用户当前选择的第二组",
            "target_start_ms": 4_000,
            "target_end_ms": 6_000,
        }
    )
    units = [
        SimpleNamespace(unit_id="unit_1", source_cue_ids=["cue_1"]),
        SimpleNamespace(unit_id="unit_2", source_cue_ids=["cue_2"]),
    ]
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(groups=[first, selected], semantic_units=units, plan_revision=1, source_revision="a" * 64),
            candidate_reports=[],
        )
    )
    run = SimpleNamespace(
        next_action="generate_candidate",
        next_group_id=first.group_id,
        groups=[
            SimpleNamespace(group_id=first.group_id, stage="ready_to_generate", attempt_count=0),
            SimpleNamespace(group_id=selected.group_id, stage="ready_to_generate", attempt_count=0),
        ],
    )
    captured: dict[str, object] = {}

    def fake_handoff(project_id, segment_id, **kwargs):
        captured.update(segment_id=segment_id, kwargs=kwargs)
        return GenerateRequest(
            text=selected.spoken_text,
            source="video_localization",
            project_id=project_id,
            segment_id=kwargs["target_subtitle_ids"][0],
            bind_to_video_localization=True,
            video_localization_workflow_id=kwargs.get("workflow_id") or "workflow-selected",
        )

    _configure(
        read_production_run=lambda _project_id: run,
        get_video_localization=lambda _project_id: draft,
        build_single_tts_handoff=fake_handoff,
    )
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "finalize_submission",
        lambda request: request,
    )
    monkeypatch.setattr(executor.task_queue, "submit", lambda *_args, **_kwargs: None)

    async def fake_submit(request, **kwargs):
        captured["request"] = request
        return "task-selected"

    monkeypatch.setattr(executor.task_queue, "submit", fake_submit)

    response = await executor.advance(
        "project-1",
        scope="single_group",
        group_id=selected.group_id,
    )
    for _ in range(50):
        if "request" in captured:
            break
        await asyncio.sleep(0.01)

    assert response.group_id == selected.group_id
    assert captured["segment_id"] == selected.group_id
    assert captured["request"].segment_id == "localized_2"
    assert captured["kwargs"]["target_subtitle_ids"] == ["localized_2"]
    assert captured["kwargs"]["source_cue_ids"] == ["cue_2"]


@pytest.mark.asyncio
async def test_executor_never_duplicates_an_active_group(monkeypatch):
    run = SimpleNamespace(
        next_action="wait_for_generation",
        next_group_id="group-1",
        groups=[
            SimpleNamespace(
                group_id="group-1",
                stage="generating",
            )
        ],
    )
    _configure(
        read_production_run=lambda _project_id: run,
    )

    response = await executor.advance(
        "project-1",
        scope="all_remaining",
    )

    assert response.status == "waiting"
    assert response.task_id is None


@pytest.mark.asyncio
async def test_executor_recovers_durable_candidate_from_stale_generating_state(
    monkeypatch,
):
    active = SimpleNamespace(
        group_id="group-1",
        stage="generating",
        candidate_ids=["result-1"],
    )
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            next_action="wait_for_generation",
            next_group_id="group-1",
            groups=[active],
        ),
        recover_and_finalize_generated_group=(
            lambda project_id, group_id, identities: "accepted"
        ),
    )
    continued: list[str] = []
    monkeypatch.setattr(
        executor,
        "_continue_all_remaining",
        lambda project_id: continued.append(project_id),
    )

    response = await executor.advance("project-1", scope="all_remaining")

    assert response.status == "queued"
    assert continued == ["project-1"]


@pytest.mark.asyncio
async def test_successful_managed_task_recovers_result_then_continues(monkeypatch):
    calls: list[object] = []
    task = GenerationTask(
        task_id="task-1",
        generation_id="task-1",
        result_id="result-1",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized-1",
        status=TaskStatus.success,
        input_text="完整句子",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": "group-1",
            "video_localization_execution_scope": "all_remaining",
            "video_localization_generation_attempt": 1,
        },
    )
    _configure(
        recover_and_finalize_generated_group=(
            lambda project_id, group_id, identities: (
                calls.append((project_id, group_id, identities)) or "accepted"
            )
        ),
    )

    monkeypatch.setattr(
        executor,
        "_continue_all_remaining",
        lambda project_id: calls.append((project_id, "continued")),
    )

    result = await executor.handle_completed_task(task)

    assert result == "accepted"
    assert calls == [
        (
            "project-1",
            "group-1",
            ["result-1", "task-1", "candidate_task-1"],
        ),
        ("project-1", "continued"),
    ]


@pytest.mark.asyncio
async def test_semantic_boundary_handoff_does_not_mark_failure_or_dispatch_successor(monkeypatch):
    task = GenerationTask(
        task_id="task-semantic",
        generation_id="task-semantic",
        result_id="result-semantic",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized-1",
        status=TaskStatus.success,
        input_text="完整句子",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": "group-1",
            "video_localization_execution_scope": "all_remaining",
        },
    )
    calls: list[str] = []
    _configure(
        recover_and_finalize_generated_group=(
            lambda *_args, **_kwargs: "needs_semantic_review"
        ),
        record_group_failure=lambda *_args, **_kwargs: calls.append("failure"),
    )
    monkeypatch.setattr(
        executor,
        "_continue_all_remaining",
        lambda _project_id: calls.append("continue"),
    )

    assert await executor.handle_completed_task(task) == "needs_semantic_review"
    assert calls == []


@pytest.mark.asyncio
async def test_parallel_completion_waits_for_preceding_group_before_adoption():
    recovered: list[str] = []
    task = GenerationTask(
        task_id="task-2",
        generation_id="task-2",
        result_id="result-2",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized-2",
        status=TaskStatus.success,
        input_text="第二组",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": "group-2",
            "video_localization_execution_scope": "all_remaining",
            "video_localization_generation_attempt": 1,
            "video_localization_max_in_flight_groups": 2,
        },
    )
    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            groups=[
                SimpleNamespace(group_id="group-1", stage="generating"),
                SimpleNamespace(group_id="group-2", stage="generating"),
            ]
        ),
        recover_and_finalize_generated_group=(
            lambda _project_id, group_id, _identities: (
                recovered.append(group_id) or "accepted"
            )
        ),
    )

    result = await executor.handle_completed_task(task)

    assert result == "waiting_for_preceding_group"
    assert recovered == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["single_group", "all_remaining"])
@pytest.mark.parametrize("review_mode", ["full", "supervised"])
async def test_managed_run_regenerates_uncuttable_strong_break_once(monkeypatch, scope, review_mode):
    task = GenerationTask(
        task_id="task-1",
        generation_id="task-1",
        result_id="result-1",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized-1",
        status=TaskStatus.success,
        input_text="第一句。第二句。",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": "group-1",
            "video_localization_execution_scope": scope,
            "video_localization_dubbing_review_mode": review_mode,
            "video_localization_generation_attempt": 1,
            "video_localization_workflow_id": "workflow-1",
        },
    )
    closed: list[tuple[str, str]] = []
    _configure(
        recover_and_finalize_generated_group=(
            lambda *_args, **_kwargs: "regeneration_required"
        ),
        mark_workflow_placement_failed=(
            lambda project_id, workflow_id, **_kwargs: closed.append(
                (project_id, workflow_id)
            )
        ),
    )
    queued: list[tuple[str, str, int]] = []

    async def fake_queue(project_id, *, group_id, attempt, **_kwargs):
        assert _kwargs["scope"] == scope
        assert _kwargs["review_mode"] == review_mode
        assert _kwargs["frozen_parameters"]["text"] == task.input_text
        queued.append((project_id, group_id, attempt))
        return SimpleNamespace(status="queued")

    monkeypatch.setattr(executor, "_queue_whole_group_recovery", fake_queue)

    result = await executor.handle_completed_task(task)

    assert result == "regeneration_queued"
    assert queued == [("project-1", "group-1", 2)]
    assert closed == [("project-1", "workflow-1")]


@pytest.mark.asyncio
async def test_managed_run_does_not_auto_regenerate_proven_capacity_overflow(
    monkeypatch,
):
    task = GenerationTask(
        task_id="capacity-task-1",
        generation_id="capacity-task-1",
        result_id="capacity-result-1",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized-1",
        status=TaskStatus.success,
        input_text="完整但超出时间窗的文本",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": "group-1",
            "video_localization_execution_scope": "single_group",
            "video_localization_dubbing_review_mode": "full",
            "video_localization_generation_attempt": 1,
            "video_localization_workflow_id": "workflow-1",
        },
    )
    failures = []
    closed = []
    _configure(
        mark_workflow_placement_failed=lambda *args, **kwargs: closed.append((args, kwargs)),
        recover_and_finalize_generated_group=(
            lambda *_args, **_kwargs: "capacity_recovery_required"
        ),
        get_video_localization=lambda _project_id: _draft(_group()),
        record_group_failure=lambda project_id, request: failures.append(
            (project_id, request)
        ),
    )
    queued = []

    async def fake_queue(*args, **kwargs):
        queued.append((args, kwargs))

    monkeypatch.setattr(executor, "_queue_whole_group_recovery", fake_queue)

    result = await executor.handle_completed_task(task)

    assert result == "capacity_recovery_required"
    assert queued == []
    assert closed == []
    assert failures[0][1].reason_code == "group_capacity_recovery_decision_required"
    assert failures[0][1].attempt_count == 1


@pytest.mark.asyncio
async def test_full_run_does_not_loop_after_gap_regeneration(monkeypatch):
    task = GenerationTask(
        task_id="task-2",
        generation_id="task-2",
        result_id="result-2",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized-1",
        status=TaskStatus.success,
        input_text="第一句。第二句。",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": "group-1",
            "video_localization_execution_scope": "all_remaining",
            "video_localization_generation_attempt": 2,
            "video_localization_workflow_id": "workflow-2",
        },
    )
    failures = []
    closed: list[tuple[str, str]] = []
    _configure(
        recover_and_finalize_generated_group=(
            lambda *_args, **_kwargs: "regeneration_required"
        ),
        get_video_localization=lambda _project_id: _draft(_group()),
        mark_workflow_placement_failed=(
            lambda project_id, workflow_id, **_kwargs: closed.append(
                (project_id, workflow_id)
            )
        ),
        record_group_failure=lambda project_id, request: failures.append(
            (project_id, request)
        ),
    )
    continued: list[str] = []
    monkeypatch.setattr(
        executor,
        "_continue_all_remaining",
        lambda project_id: continued.append(project_id),
    )

    result = await executor.handle_completed_task(task)

    assert result == "failed"
    assert failures[0][1].attempt_count == 2
    assert failures[0][1].reason_code == "group_recovery_decision_required"
    assert closed == [("project-1", "workflow-2")]
    assert continued == ["project-1"]


@pytest.mark.asyncio
async def test_retryable_closeout_failure_terminals_workflow_before_continuing(
    monkeypatch,
):
    task = GenerationTask(
        task_id="task-retryable",
        generation_id="task-retryable",
        result_id="result-retryable",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id="localized-1",
        status=TaskStatus.success,
        input_text="完整句子",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": "group-1",
            "video_localization_execution_scope": "all_remaining",
            "video_localization_generation_attempt": 1,
            "video_localization_workflow_id": "workflow-retryable",
        },
    )
    closed: list[tuple[str, str]] = []
    failures = []
    _configure(
        recover_and_finalize_generated_group=(
            lambda *_args, **_kwargs: "retryable_failure"
        ),
        get_video_localization=lambda _project_id: _draft(_group()),
        mark_workflow_placement_failed=(
            lambda project_id, workflow_id, **_kwargs: closed.append(
                (project_id, workflow_id)
            )
        ),
        record_group_failure=lambda project_id, request: failures.append(
            (project_id, request)
        ),
    )
    continued: list[str] = []
    monkeypatch.setattr(
        executor,
        "_continue_all_remaining",
        lambda project_id: continued.append(project_id),
    )

    result = await executor.handle_completed_task(task)

    assert result == "failed"
    assert closed == [("project-1", "workflow-retryable")]
    assert failures[0][1].reason_code == "generated_result_closeout_unavailable"
    assert continued == ["project-1"]


@pytest.mark.asyncio
async def test_continuous_runner_crosses_local_closeouts_until_generation_starts(
    monkeypatch,
):
    pending = SimpleNamespace(
        group_id="group-1",
        stage="needs_timeline_work",
        speaking_rate_ratio=None,
    )
    states = iter(
        [
            SimpleNamespace(
                status="needs_attention",
                next_action="place_candidate",
                active_group_count=0,
                groups=[pending],
            ),
            SimpleNamespace(
                status="running",
                next_action="wait_for_generation",
                active_group_count=1,
            ),
        ]
    )
    calls: list[tuple[str, str]] = []

    responses = iter(
        [
            SimpleNamespace(status="queued", workflow_id=None, task_id=None),
            SimpleNamespace(
                status="queued",
                workflow_id="workflow-1",
                task_id=None,
            ),
        ]
    )

    async def fake_advance(project_id, *, scope):
        calls.append((project_id, scope))
        return next(responses)

    _configure(read_production_run=lambda _project_id: next(states))
    monkeypatch.setattr(executor, "advance", fake_advance)
    executor._continuous_runs.clear()

    executor._continue_all_remaining("project-1")
    task = executor._continuous_runs["project-1"]
    await task

    assert calls == [
        ("project-1", "all_remaining"),
        ("project-1", "all_remaining"),
    ]
    assert "project-1" not in executor._continuous_runs


@pytest.mark.asyncio
async def test_continuous_runner_does_not_trust_global_complete_over_frozen_speed(
    monkeypatch,
):
    old_take = SimpleNamespace(
        group_id="group-1",
        stage="accepted",
        speaking_rate_ratio=1.0,
    )
    calls: list[str] = []
    responses = iter(
        [
            SimpleNamespace(status="queued", workflow_id=None, task_id=None),
            SimpleNamespace(
                status="queued",
                workflow_id="workflow-replacement",
                task_id=None,
            ),
        ]
    )

    async def fake_advance(project_id, **_kwargs):
        calls.append(project_id)
        return next(responses)

    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            status="completed",
            next_action="complete",
            active_group_count=0,
            groups=[old_take],
        ),
        get_video_localization=lambda _project_id: SimpleNamespace(tts_tasks=[]),
    )
    monkeypatch.setattr(executor, "advance", fake_advance)
    executor._continuous_runs.clear()

    executor._continue_all_remaining(
        "project-speed-window",
        window=executor.DubbingExecutionWindow(
            start_group_id="group-1",
            end_group_id="group-1",
            ordinary_speed_baseline=1.18,
        ),
    )
    task = executor._continuous_runs["project-speed-window"]
    await task

    assert calls == ["project-speed-window", "project-speed-window"]


@pytest.mark.asyncio
async def test_continuous_runner_stops_when_advance_queues_generation_before_projection_refresh(
    monkeypatch,
):
    calls: list[tuple[str, str]] = []

    async def fake_advance(project_id, *, scope):
        calls.append((project_id, scope))
        return SimpleNamespace(
            status="queued",
            workflow_id="workflow-1",
            task_id=None,
        )

    _configure(
        read_production_run=lambda _project_id: SimpleNamespace(
            status="needs_attention",
            next_action="generate_candidate",
            active_group_count=0,
        )
    )
    monkeypatch.setattr(executor, "advance", fake_advance)
    executor._continuous_runs.clear()

    executor._continue_all_remaining("project-1")
    task = executor._continuous_runs["project-1"]
    await task

    assert calls == [("project-1", "all_remaining")]
    assert "project-1" not in executor._continuous_runs


@pytest.mark.asyncio
async def test_generation_failure_is_recorded_without_automatic_retry(monkeypatch):
    group = _group()
    task = GenerationTask(
        task_id="task-failed",
        generation_id="task-failed",
        engine_id="omnivoice",
        project_id="project-1",
        segment_id=group.group_id,
        status=TaskStatus.failed,
        input_text=group.spoken_text,
        error_message="provider failed",
        parameters={
            "source": "video_localization",
            "video_localization_dubbing_group_id": group.group_id,
            "video_localization_execution_scope": "all_remaining",
            "video_localization_generation_attempt": 1,
        },
    )
    failures = []
    _configure(
        get_video_localization=lambda _project_id: _draft(group),
        record_group_failure=lambda project_id, request: failures.append(
            (project_id, request)
        ),
    )
    continued: list[str] = []
    monkeypatch.setattr(
        executor,
        "_continue_all_remaining",
        lambda project_id: continued.append(project_id),
    )

    result = await executor.handle_failed_task(task)

    assert result == "failed"
    assert len(failures) == 1
    assert failures[0][0] == "project-1"
    assert failures[0][1].attempt_count == 1
    assert continued == ["project-1"]


@pytest.mark.asyncio
async def test_queue_submission_failure_is_persisted_on_the_prepared_workflow(
    monkeypatch,
):
    group = _group()
    draft = _draft(group)
    request = GenerateRequest(
        text=group.spoken_text,
        source="video_localization",
        project_id="project-1",
        segment_id=group.group_id,
        bind_to_video_localization=True,
        video_localization_workflow_id="workflow-failed",
    )
    _configure(
        build_single_tts_handoff=lambda *_args, **_kwargs: request,
        get_video_localization=lambda _project_id: draft,
    )
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "finalize_submission",
        lambda value: value,
    )
    terminal: list[tuple[str, str]] = []
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "mark_workflow_terminal",
        lambda value, *, status, error_message, source_id=None: terminal.append(
            (status, error_message)
        ),
    )

    async def fail_submit(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(executor.task_queue, "submit", fail_submit)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await executor._queue_group(
            "project-1",
            draft=draft,
            group=group,
            scope="single_group",
            attempt=1,
        )

    assert terminal == [("failed", "配音任务未能进入生成队列，请重试")]


@pytest.mark.asyncio
async def test_queue_accepts_only_an_equivalent_runtime_plan_rebind(monkeypatch):
    group = _group()
    draft = _draft(group)
    current_group = _group()
    current = _draft(current_group)
    current.dubbing_production.active_plan.plan_revision = 2
    current.dubbing_production.active_plan.source_revision = "b" * 64
    request = _frozen_retry_request(current_group).model_copy(update={
        "video_localization_dubbing_plan_revision": 2,
        "video_localization_workflow_id": "workflow-rebound",
    })
    _configure(
        build_single_tts_handoff=lambda *_args, **_kwargs: request,
        get_video_localization=lambda _project_id: current,
        group_evidence_context_fingerprint=(
            lambda *_args: "same-current-group-input"
        ),
    )
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "finalize_submission",
        lambda value: value,
    )
    submitted = []

    async def submit(value, **_kwargs):
        submitted.append(value)
        return "task-rebound"

    monkeypatch.setattr(executor.task_queue, "submit", submit)

    response = await executor._queue_group(
        "project-1",
        draft=draft,
        group=group,
        scope="single_group",
        attempt=1,
        workflow_id="workflow-rebound",
        generation_parameters={"engine_id": "omnivoice", "speed": 1.05},
    )

    assert response.task_id == "task-rebound"
    assert len(submitted) == 1
    assert submitted[0].video_localization_dubbing_plan_revision == 2


@pytest.mark.asyncio
async def test_queue_rejects_changed_group_after_runtime_plan_rebind(monkeypatch):
    group = _group()
    draft = _draft(group)
    current_group = _group()
    current_group.spoken_text = "用户刚刚修改过的台词"
    current = _draft(current_group)
    current.dubbing_production.active_plan.plan_revision = 2
    current.dubbing_production.active_plan.source_revision = "b" * 64
    request = _frozen_retry_request(group).model_copy(update={
        "video_localization_dubbing_plan_revision": 2,
        "video_localization_workflow_id": "workflow-changed",
    })
    _configure(
        build_single_tts_handoff=lambda *_args, **_kwargs: request,
        get_video_localization=lambda _project_id: current,
    )
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "finalize_submission",
        lambda value: value,
    )
    terminal = []
    monkeypatch.setattr(
        executor.video_localization_tts_handoff,
        "mark_workflow_terminal",
        lambda _value, *, status, error_message, source_id=None: terminal.append(
            (status, error_message)
        ),
    )

    async def forbidden_submit(*_args, **_kwargs):
        pytest.fail("changed group input must not enter TTS")

    monkeypatch.setattr(executor.task_queue, "submit", forbidden_submit)

    with pytest.raises(executor.AppException) as caught:
        await executor._queue_group(
            "project-1",
            draft=draft,
            group=group,
            scope="single_group",
            attempt=1,
            workflow_id="workflow-changed",
            generation_parameters={"engine_id": "omnivoice", "speed": 1.05},
        )

    assert caught.value.code == "VIDEO_LOCALIZATION_DUBBING_PLAN_CHANGED"
    assert terminal == [("failed", "配音计划已经变化，请重新读取当前分组。")]


@pytest.mark.asyncio
async def test_later_group_submission_reads_durable_policy_not_old_callback_window(monkeypatch):
    from app.schemas.video_localization_dubbing_production import DubbingGroupSchedulingPolicy
    group = _group()
    current = _draft(group)
    current.dubbing_production.scheduling_policies = [DubbingGroupSchedulingPolicy(
        source_revision="a" * 64, plan_revision=1,
        group_id=group.group_id, priority="foreground_resume",
    )]
    request = GenerateRequest(text=group.spoken_text, source="video_localization",
                              project_id="project-priority", segment_id=group.group_id,
                              bind_to_video_localization=True, video_localization_workflow_id="prepared")
    _configure(get_video_localization=lambda _id: current,
               build_single_tts_handoff=lambda *_args, **_kwargs: request)
    monkeypatch.setattr(executor.video_localization_tts_handoff, "finalize_submission", lambda value: value)
    submitted = []
    async def capture(value, **_kwargs):
        submitted.append(value)
        return f"task-{len(submitted)}"
    monkeypatch.setattr(executor.task_queue, "submit", capture)
    for _ in range(2):
        # A fresh runtime after restart receives an old normal continuation.
        monkeypatch.setattr(executor, "_project_scheduling_locks", {})
        await executor._queue_group("project-priority", draft=_draft(group), group=group,
                                    scope="all_remaining", attempt=1,
                                    workflow_id="prepared", generation_parameters={},
                                    window=executor._bounded_window(resource_priority="normal"))
    assert [value.resource_priority for value in submitted] == ["foreground_resume", "foreground_resume"]
    restored = GenerationTask(engine_id="omnivoice", project_id="project-priority", input_text=group.spoken_text,
                              parameters=submitted[-1].model_dump())
    assert executor._task_execution_window(restored).resource_priority == "foreground_resume"


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,discarded', [('accepted', False), ('needs_regeneration', True)])
async def test_recovery_never_resurrects_accepted_or_deleted_result(monkeypatch, stage, discarded):
    from app.errors import AppException
    from app.services import longform_queue
    group = _group()
    draft = _draft(group)
    draft.ui_state = {'discarded_tts_task_ids': ['123456789abc'] if discarded else []}
    _configure(get_video_localization=lambda _: draft,
               validate_recovery_decision=lambda *_: (group, None),
               read_production_run=lambda _: SimpleNamespace(groups=[SimpleNamespace(group_id=group.group_id, stage=stage)]))
    monkeypatch.setattr(longform_queue, 'get_task', lambda _: pytest.fail('Must reject before touching an old task'))
    with pytest.raises(AppException):
        await executor.advance_recovery('project-1', SimpleNamespace(recovery_id='123456789abc'))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", "已经改变的台词"),
        ("video_localization_target_subtitle_ids", ["other-subtitle"]),
    ],
)
def test_rebase_compatible_retry_request_rebinds_only_exact_historical_group(
    field, value,
):
    _configure()
    group = _group()
    draft = _draft(group)
    draft.dubbing_production.active_plan.plan_revision = 3
    historical = _frozen_retry_request(group).model_copy(
        update={"video_localization_dubbing_plan_revision": 1}
    )

    rebound = executor._frozen_group_retry_request_for_current_group(
        draft,
        group,
        parameters=historical.model_dump(mode="json"),
        allow_equivalent_plan_rebind=True,
    )

    assert rebound is not None
    assert rebound.video_localization_dubbing_plan_revision == 3
    changed = historical.model_copy(update={field: value})
    assert executor._frozen_group_retry_request_for_current_group(
        draft,
        group,
        parameters=changed.model_dump(mode="json"),
        allow_equivalent_plan_rebind=True,
    ) is None


@pytest.mark.parametrize(
    ("workflow_status", "discarded"),
    [("cancelled", []), ("success", ["result-old-plan"])],
)
def test_rebased_generated_identity_never_revives_cancelled_or_discarded_result(
    workflow_status, discarded,
):
    _configure()
    group = _group()
    draft = _draft(group)
    draft.dubbing_production.active_plan.plan_revision = 3
    historical = _frozen_retry_request(group).model_copy(update={
        "video_localization_dubbing_plan_revision": 1,
    })
    draft.tts_tasks = [SimpleNamespace(
        workflow_id="workflow-old-plan",
        generation_task_id="task-old-plan",
        result_id="result-old-plan",
        status=workflow_status,
        stages=[SimpleNamespace(
            kind="generation", status="success",
            parameters=historical.model_dump(mode="json"),
        )],
    )]
    draft.ui_state = {"discarded_tts_task_ids": discarded}

    assert executor._rebase_compatible_generated_identities(draft, group) == []


def test_rebased_generated_identity_rechecks_valid_frozen_parameter_pack_source():
    _configure()
    current, group = _draft_with_frozen_parameter_pack()

    assert executor._rebase_compatible_generated_identities(current, group) == [
        "result-old", "task-old", "candidate_task-old",
    ]

    changed_source, changed_group = _draft_with_frozen_parameter_pack(
        changed_speaker=True,
    )
    assert executor._rebase_compatible_generated_identities(
        changed_source, changed_group,
    ) == []

    corrupt_pack, corrupt_group = _draft_with_frozen_parameter_pack()
    corrupt_pack.tts_tasks[0].stages[0].parameters[
        "video_localization_parameter_pack"
    ] = {"version": "unsupported"}
    assert executor._rebase_compatible_generated_identities(
        corrupt_pack, corrupt_group,
    ) == []


def test_rebased_generated_identity_keeps_authorized_nearby_reference_source():
    _configure()
    group = _group()
    draft = VideoLocalizationDraft(
        localized_subtitles=[VideoLocalizationSubtitleCue(
            subtitle_id="localized_1", start_ms=1_000, end_ms=3_000,
            text=group.spoken_text, tts_text=group.spoken_text,
            source_cue_ids=["cue_1"],
        )],
        cues=[
            VideoLocalizationCue(
                cue_id="cue_1", speaker_id="speaker-1", start_ms=1_000,
                end_ms=3_000, en_subtitle_text="Ordinary source.",
            ),
            VideoLocalizationCue(
                cue_id="cue_2", speaker_id="speaker-1", start_ms=3_100,
                end_ms=5_000, en_subtitle_text="Nearby reference.",
            ),
        ],
    )
    recovery = DubbingRecoveryDecision(
        recovery_id="abcdef123456", source_revision="a" * 64, plan_revision=1,
        group_id=group.group_id, stage="nearby_reference",
        phrases=["完整短语"], reference_cue_ids=["cue_2"], reason="授权换参考",
    )
    request = _frozen_retry_request(group).model_copy(update={
        "video_localization_dubbing_plan_revision": 1,
        "video_localization_source_cue_ids": ["cue_2"],
        "video_localization_recovery": recovery,
    })
    selection = build_selection_snapshot(
        draft,
        TtsSelectionRequest(
            target_subtitle_ids=["localized_1"], source_cue_ids=["cue_2"],
        ),
    )
    pack = TtsParameterPack(
        project_id="project-1", target=selection.target, source=selection.source,
        request=request,
    )
    historical = request.model_dump(mode="json")
    historical["video_localization_parameter_pack"] = pack.model_dump(mode="json")
    current = draft.model_copy(update={
        "dubbing_production": SimpleNamespace(active_plan=SimpleNamespace(
            source_revision="a" * 64, plan_revision=2, groups=[group],
            semantic_units=[SimpleNamespace(
                unit_id="unit_1", source_cue_ids=["cue_1"],
            )],
        )),
        "tts_tasks": [SimpleNamespace(
            generation_task_id="task-nearby", result_id="result-nearby",
            status="success", stages=[SimpleNamespace(
                kind="generation", status="success", parameters=historical,
            )],
        )],
    })

    assert executor._rebase_compatible_generated_identities(current, group) == [
        "result-nearby", "task-nearby", "candidate_task-nearby",
    ]


@pytest.mark.asyncio
async def test_nearby_reference_preserves_rebased_semantic_recovery_order(monkeypatch):
    from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision
    from app.services import longform_queue

    group = _group()
    draft = _draft(group)
    draft.ui_state = {}
    draft.dubbing_production.active_plan.plan_revision = 3
    whole = _frozen_retry_request(group).model_copy(update={
        "video_localization_dubbing_plan_revision": 1,
        "video_localization_generation_attempt": 2,
    })
    semantic_decision = DubbingRecoveryDecision(
        recovery_id="abcdef123456", source_revision="b" * 64, plan_revision=1,
        group_id=group.group_id, stage="semantic_phrases",
        phrases=["这是需要", "连续表达的一句话"], reason="历史语义分段",
    )
    semantic = whole.model_copy(update={
        "video_localization_generation_attempt": 3,
        "video_localization_recovery": semantic_decision,
    })
    draft.tts_tasks = [
        SimpleNamespace(generation_task_id="whole", stages=[
            SimpleNamespace(kind="generation", parameters=whole.model_dump(mode="json")),
        ]),
        SimpleNamespace(generation_task_id="semantic", stages=[
            SimpleNamespace(kind="generation", parameters=semantic.model_dump(mode="json")),
        ]),
    ]
    decision = DubbingRecoveryDecision(
        recovery_id="fedcba654321", source_revision="a" * 64, plan_revision=3,
        group_id=group.group_id, stage="nearby_reference",
        phrases=["这是需要", "连续表达的一句话"], reference_cue_ids=["cue-1"],
        reason="当前邻近参考",
    )
    progress = SimpleNamespace(
        group_id=group.group_id, stage="needs_regeneration", workflow_ids=[],
    )
    _configure(
        get_video_localization=lambda _project_id: draft,
        validate_recovery_decision=lambda _draft, request: (group, None),
        read_production_run=lambda _project_id: SimpleNamespace(groups=[progress]),
    )
    monkeypatch.setattr(longform_queue, "get_task", lambda _task_id: None)

    async def no_close(*_args, **_kwargs):
        return None

    queued = []

    async def queue(_project_id, **kwargs):
        queued.append(kwargs)
        return SimpleNamespace(status="queued", scope="single_group", group_id=group.group_id)

    monkeypatch.setattr(executor, "_close_unplaceable_workflow", no_close)
    monkeypatch.setattr(executor, "_queue_group", queue)

    response = await executor.advance_recovery("project-1", decision)

    assert response.status == "queued"
    assert queued[0]["attempt"] == 4
    assert queued[0]["generation_parameters"]["video_localization_dubbing_plan_revision"] == 3
    assert queued[0]["generation_parameters"]["video_localization_recovery"]["stage"] == "nearby_reference"


@pytest.mark.asyncio
async def test_recovery_resume_reuses_parent_without_submitting(monkeypatch):
    from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision
    from app.services import longform_queue
    group = _group()
    draft = _draft(group)
    draft.ui_state = {}
    decision = DubbingRecoveryDecision(recovery_id='123456789abc', source_revision='a'*64, plan_revision=1,
        group_id=group.group_id, stage='semantic_phrases', phrases=['这是需要', '连续表达的一句话'], reason='完整短语')
    request = _frozen_retry_request(group).model_copy(update={'video_localization_recovery': decision})
    parent = SimpleNamespace(parameters={'generate_request': request.model_dump()}, status=TaskStatus.running, longform_task_id=decision.recovery_id)
    _configure(get_video_localization=lambda _: draft, validate_recovery_decision=lambda *_: (group, None),
        read_production_run=lambda _: SimpleNamespace(groups=[SimpleNamespace(group_id=group.group_id, stage='generating')]))
    monkeypatch.setattr(longform_queue, 'get_task', lambda _: parent)
    monkeypatch.setattr(longform_queue, 'submit', lambda _: pytest.fail('Existing parent must be reused'))
    response = await executor.advance_recovery('project-1', decision)
    assert response.status == 'waiting'
    assert response.task_id == decision.recovery_id


@pytest.mark.asyncio
async def test_agent_reconciles_manual_partial_group_before_any_generation_or_closeout():
    group = _group()
    progress = SimpleNamespace(
        group_id=group.group_id, stage="needs_timeline_edit", recommended_action="edit_timeline",
        candidate_ids=[], workflow_ids=[], attempt_count=0,
        timeline_requires_reconciliation=True,
    )
    run = SimpleNamespace(next_action="edit_timeline", next_group_id=group.group_id, groups=[progress])
    def forbidden(*args, **kwargs):
        raise AssertionError("Manual timeline material must not be regenerated or replaced")
    _configure(
        read_production_run=lambda _: run,
        get_video_localization=lambda _: _draft(group),
        recover_and_finalize_generated_group=forbidden,
        reserve_single_tts_handoff=forbidden,
    )
    result = await executor.advance("project-1", scope="single_group", group_id=group.group_id)
    assert result.status == "needs_attention"
    assert result.task_id is None


@pytest.mark.asyncio
async def test_complete_executor_reads_physical_full_scope_and_returns_missing_work():
    from app.schemas.video_localization_dubbing_production import DubbingCompletionSnapshot, DubbingProductionExecuteResponse
    group = _group()
    calls = []
    def completion(_project_id, **kwargs):
        calls.append(kwargs)
        return DubbingCompletionSnapshot(source_revision="a"*64, start_ms=0, end_ms=9000,
            status="incomplete", missing_target_subtitle_ids=["later-unplanned"])
    _configure(get_video_localization=lambda _: _draft(group), read_completion=completion)
    response = DubbingProductionExecuteResponse(status="complete", scope="all_remaining", message="finished")
    checked = await executor._with_completion_facts("project-1", response, window=executor._bounded_window())
    assert calls == [{}]
    assert checked.status == "needs_attention"
    assert checked.completion.missing_target_subtitle_ids == ["later-unplanned"]
    await executor._with_completion_facts("project-1", response, group_id=group.group_id)
    assert calls[-1] == {"start_ms": 1000, "end_ms": 3000}
