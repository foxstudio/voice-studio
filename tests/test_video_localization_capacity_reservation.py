from __future__ import annotations

from pathlib import Path

import pytest

from app.domains.video_localization import service
from app.errors import AppException
from app.schemas.video_localization_dubbing_production import (
    DubbingGenerationGroup,
    DubbingGenerationPlan,
    DubbingProductionGroupFailure,
    DubbingProductionState,
    DubbingSemanticUnit,
    DubbingSpeechIsland,
)
from app.schemas.voice_studio import (
    GenerateRequest,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
)


SOURCE_REVISION = "a" * 64
PROJECT_ID = "project-capacity"
GROUP_ID = "group-1"
WORKFLOW_ID = "workflow-old"
TASK_ID = "task-old"
RESULT_ID = "result-old"


def _draft(tmp_path: Path, *, generation_status: str = "success") -> VideoLocalizationDraft:
    group = DubbingGenerationGroup(
        group_id=GROUP_ID,
        island_id="island-1",
        unit_ids=["unit-1"],
        subtitle_ids=["localized-1"],
        speaker_id="speaker-1",
        spoken_text="需要更快但仍完整的声音。",
        target_start_ms=1_000,
        target_end_ms=2_000,
        source_reference_start_ms=1_000,
        source_reference_end_ms=2_000,
    )
    plan = DubbingGenerationPlan(
        source_revision=SOURCE_REVISION,
        plan_revision=3,
        status="passed",
        semantic_units=[
            DubbingSemanticUnit(
                unit_id="unit-1",
                subtitle_ids=["localized-1"],
                source_cue_ids=["cue-1"],
                speaker_id="speaker-1",
                start_ms=1_000,
                end_ms=2_000,
                source_anchor_start_ms=1_000,
                source_anchor_end_ms=2_000,
                display_text=group.spoken_text,
                spoken_text=group.spoken_text,
            )
        ],
        speech_islands=[
            DubbingSpeechIsland(
                island_id="island-1",
                unit_ids=["unit-1"],
                speaker_id="speaker-1",
                start_ms=1_000,
                end_ms=2_000,
            )
        ],
        groups=[group],
    )
    frozen = GenerateRequest(
        text=group.spoken_text,
        engine_id="omnivoice",
        source="video_localization",
        project_id=PROJECT_ID,
        segment_id=GROUP_ID,
        bind_to_video_localization=True,
        ref_text="reference",
        reference_audio_path=str(tmp_path / "reference.wav"),
        custom_reference_source_audio_path=str(tmp_path / "vocals.wav"),
        custom_reference_trim_start_ms=0,
        custom_reference_trim_end_ms=1_000,
        video_localization_dubbing_plan_revision=plan.plan_revision,
        video_localization_dubbing_group_id=group.group_id,
        video_localization_target_subtitle_ids=list(group.subtitle_ids),
        video_localization_source_cue_ids=["cue-1"],
    ).model_dump(mode="json")
    workflow = VideoLocalizationTtsTask(
        workflow_id=WORKFLOW_ID,
        project_id=PROJECT_ID,
        segment_id="localized-1",
        subtitle_summary=group.spoken_text,
        text=group.spoken_text,
        source_cue_ids=["cue-1"],
        start_ms=1_000,
        end_ms=2_000,
        status="running",
        generation_task_id=TASK_ID,
        result_id=RESULT_ID,
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation",
                status=generation_status,
                progress=1.0 if generation_status == "success" else 0.5,
                parameters=frozen,
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement",
                status="running",
                progress=0.0,
            ),
        ],
    )
    return VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue-1",
                start_ms=1_000,
                end_ms=2_000,
                en_subtitle_text="Source line.",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized-1",
                start_ms=1_000,
                end_ms=2_000,
                text=group.spoken_text,
                source_cue_ids=["cue-1"],
            )
        ],
        generated_candidates=[
            {
                "candidate_id": f"candidate_{TASK_ID}",
                "task_id": TASK_ID,
                "result_id": RESULT_ID,
                "audio_path": str(tmp_path / "old.wav"),
            }
        ],
        tts_tasks=[workflow],
        dubbing_production=DubbingProductionState(
            active_plan=plan,
            group_failures=[
                DubbingProductionGroupFailure(
                    source_revision=SOURCE_REVISION,
                    plan_revision=plan.plan_revision,
                    group_id=group.group_id,
                    candidate_id=f"candidate_{TASK_ID}",
                    title="配音需要容量恢复",
                    note="完整语音无法装入当前窗口。",
                    reason_code="group_capacity_recovery_decision_required",
                    attempt_count=1,
                    created_at="2026-09-08T00:00:00Z",
                )
            ],
        ),
    )


def _capacity_parameters(draft: VideoLocalizationDraft) -> dict[str, object]:
    plan = draft.dubbing_production.active_plan
    assert plan is not None
    group = plan.groups[0]
    return {
        "source": "video_localization",
        "text": group.spoken_text,
        "speed": 1.05,
        "video_localization_dubbing_group_id": group.group_id,
        "video_localization_dubbing_plan_revision": plan.plan_revision,
        "video_localization_target_subtitle_ids": list(group.subtitle_ids),
        "video_localization_source_cue_ids": ["cue-1"],
    }


@pytest.fixture
def isolated_draft(tmp_path: Path, monkeypatch):
    current = _draft(tmp_path)
    monkeypatch.setattr(service.project_store, "get_project", lambda _project_id: object())
    monkeypatch.setattr(service, "reconcile_tts_workflow_tasks", lambda tasks: tasks)

    def get(_project_id: str):
        return current

    def save(_project_id: str, draft: VideoLocalizationDraft, *, intent: str):
        nonlocal current
        assert intent == "runtime"
        current = draft
        return draft

    monkeypatch.setattr(service.draft_store, "get", get)
    monkeypatch.setattr(service.draft_store, "save", save)
    return lambda: current


def _reserve(current, **updates):
    draft = current()
    parameters = {**_capacity_parameters(draft), **updates.pop("parameters", {})}
    return service.reserve_single_tts_handoff(
        PROJECT_ID,
        GROUP_ID,
        parameters=parameters,
        target_subtitle_ids=["localized-1"],
        source_cue_ids=["cue-1"],
        workflow_id=updates.pop("workflow_id", "workflow-new"),
        capacity_replaces_workflow_ids=updates.pop(
            "capacity_replaces_workflow_ids", [WORKFLOW_ID]
        ),
        **updates,
    )


def test_capacity_retry_reserves_beside_exact_generated_open_receipt(isolated_draft):
    before = isolated_draft().tts_tasks[0]

    reserved = _reserve(isolated_draft)

    assert reserved is not None
    assert reserved.workflow_id == "workflow-new"
    after = isolated_draft()
    assert after.tts_tasks[0] == before
    assert after.tts_tasks[0].stages[1].status == "running"
    assert after.generated_candidates[0]["result_id"] == RESULT_ID


@pytest.mark.parametrize(
    "invalidate",
    ["missing_failure", "changed_plan", "missing_candidate", "terminal_receipt"],
)
def test_invalid_capacity_replacement_intent_is_rejected(
    isolated_draft,
    invalidate,
):
    draft = isolated_draft()
    if invalidate == "missing_failure":
        draft.dubbing_production = draft.dubbing_production.model_copy(
            update={"group_failures": []}
        )
    elif invalidate == "changed_plan":
        assert draft.dubbing_production.active_plan is not None
        draft.dubbing_production = draft.dubbing_production.model_copy(
            update={
                "active_plan": draft.dubbing_production.active_plan.model_copy(
                    update={"plan_revision": 4}
                )
            }
        )
    elif invalidate == "missing_candidate":
        draft.generated_candidates = []
    else:
        old = draft.tts_tasks[0]
        old.stages[1].status = "failed"
        old.status = "failed"

    with pytest.raises(AppException) as caught:
        _reserve(isolated_draft)

    assert caught.value.code == "VIDEO_LOCALIZATION_TTS_CAPACITY_REPLACEMENT_INVALID"
    assert len(isolated_draft().tts_tasks) == 1


@pytest.mark.parametrize('changed_text', [False, True])
def test_capacity_retry_rebinds_only_equivalent_historical_request(isolated_draft, changed_text):
    old = isolated_draft().tts_tasks[0]
    old.stages[0].parameters['video_localization_dubbing_plan_revision'] = 2
    if changed_text:
        old.stages[0].parameters['text'] = '不同的内容。'
        with pytest.raises(AppException) as caught:
            _reserve(isolated_draft)
        assert caught.value.code == 'VIDEO_LOCALIZATION_TTS_CAPACITY_REPLACEMENT_INVALID'
    else:
        assert _reserve(isolated_draft).workflow_id == 'workflow-new'
        assert isolated_draft().tts_tasks[0].stages[0].parameters['video_localization_dubbing_plan_revision'] == 2


@pytest.mark.parametrize('changed_text', [False, True])
def test_adoption_closes_only_equivalent_old_plan_placement(isolated_draft, changed_text):
    draft = isolated_draft()
    old = draft.tts_tasks[0]
    old.stages[0].parameters['video_localization_dubbing_plan_revision'] = 2
    if changed_text:
        old.stages[0].parameters['text'] = '不同的内容。'
    updated = service.with_superseded_group_tts_placements_closed(
        draft, group_id=GROUP_ID, selected_generation_task_id='new-task', selected_result_id='new-result')
    assert updated.tts_tasks[0].stages[1].status == ('running' if changed_text else 'cancelled')
    assert old.stages[1].status == 'running'
    assert updated.tts_tasks[0].stages[0] == old.stages[0]


def test_capacity_retry_never_exempts_active_generation(isolated_draft):
    draft = isolated_draft()
    draft.tts_tasks[0].stages[0].status = "running"

    with pytest.raises(AppException) as caught:
        _reserve(isolated_draft)

    assert caught.value.code == "VIDEO_LOCALIZATION_TTS_CAPACITY_REPLACEMENT_INVALID"


@pytest.mark.parametrize("error_code", [
    "TTS_PLACEMENT_REGENERATION_EXHAUSTED",
    "TTS_PLACEMENT_REGENERATION_REQUIRED",
])
def test_capacity_retry_accepts_only_the_known_legacy_misclosed_receipt(
    isolated_draft, error_code,
):
    draft = isolated_draft()
    old = draft.tts_tasks[0]
    old.status = "failed"
    old.stages[1].status = "failed"
    old.stages[1].error_code = error_code

    reserved = _reserve(isolated_draft)

    assert reserved is not None
    assert reserved.workflow_id == "workflow-new"
    preserved = isolated_draft().tts_tasks[0]
    assert preserved.status == "failed"
    assert preserved.stages[1].status == "failed"
    assert (
        preserved.stages[1].error_code
        == error_code
    )


def test_second_capacity_retry_remains_single_flight(isolated_draft):
    first = _reserve(isolated_draft)
    assert first is not None

    with pytest.raises(AppException) as caught:
        _reserve(isolated_draft, workflow_id="workflow-newer")

    assert caught.value.code == "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY"
    assert caught.value.detail_dict["workflow_id"] == "workflow-new"


def test_terminal_reconcile_keeps_capacity_receipt_open(tmp_path: Path, monkeypatch):
    draft = _draft(tmp_path)
    captured: list[VideoLocalizationDraft] = []

    def atomic(_project_id, updater, *, intent):
        assert intent == "runtime"
        updated = updater(draft)
        captured.append(updated)
        return updated

    monkeypatch.setattr(service, "update_video_localization_atomic", atomic)

    updated = service.reconcile_dubbing_workflow_terminal_states(PROJECT_ID)

    assert updated is not None
    assert captured
    placement = updated.tts_tasks[0].stages[1]
    assert updated.tts_tasks[0].status == "running"
    assert placement.status == "running"
    assert placement.error_code is None


def test_reconcile_adopted_result_closes_old_sibling_but_preserves_new_take(tmp_path, monkeypatch):
    draft = _draft(tmp_path)
    old = draft.tts_tasks[0]
    selected = old.model_copy(deep=True, update={
        'workflow_id': 'selected', 'generation_task_id': 'selected-task', 'result_id': 'selected-result'})
    newer = old.model_copy(deep=True, update={
        'workflow_id': 'newer', 'generation_task_id': 'newer-task', 'result_id': 'newer-result'})
    draft.tts_tasks = [old, selected, newer]
    draft.timeline_clips = [dict(clip_id='formal', track_id='dub', dub_lane=0, status='ready',
        result_id='selected-result', target_subtitle_ids=['localized-1'], start_ms=1000, end_ms=2000,
        source_start_ms=0, source_end_ms=1000)]
    before_clips = list(draft.timeline_clips)
    def atomic(_project_id, updater, *, intent):
        nonlocal draft
        draft = updater(draft)
        return draft
    monkeypatch.setattr(service, 'update_video_localization_atomic', atomic)
    updated = service.reconcile_dubbing_workflow_terminal_states(PROJECT_ID)
    assert [task.status for task in updated.tts_tasks] == ['cancelled', 'success', 'running']
    assert updated.tts_tasks[0].result_id == RESULT_ID
    assert updated.timeline_clips == before_clips
    assert service.reconcile_dubbing_workflow_terminal_states(PROJECT_ID) == updated


def test_terminal_reconcile_still_closes_exhausted_noncapacity_group(
    tmp_path: Path,
    monkeypatch,
):
    draft = _draft(tmp_path)
    capacity_failure = draft.dubbing_production.group_failures[0]
    draft.dubbing_production = draft.dubbing_production.model_copy(
        update={
            "group_failures": [
                capacity_failure.model_copy(
                    update={"reason_code": "generated_result_closeout_unavailable"}
                )
            ]
        }
    )

    def atomic(_project_id, updater, *, intent):
        assert intent == "runtime"
        return updater(draft)

    monkeypatch.setattr(service, "update_video_localization_atomic", atomic)

    updated = service.reconcile_dubbing_workflow_terminal_states(PROJECT_ID)

    assert updated is not None
    placement = updated.tts_tasks[0].stages[1]
    assert updated.tts_tasks[0].status == "failed"
    assert placement.status == "failed"
    assert placement.error_code == "TTS_PLACEMENT_REGENERATION_EXHAUSTED"


def test_ordinary_reservation_behavior_is_unchanged(isolated_draft):
    draft = isolated_draft()

    with pytest.raises(AppException) as caught:
        service.reserve_single_tts_handoff(
            PROJECT_ID,
            GROUP_ID,
            parameters=_capacity_parameters(draft),
            target_subtitle_ids=["localized-1"],
            source_cue_ids=["cue-1"],
            workflow_id="workflow-ordinary",
        )

    assert caught.value.code == "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY"
