from types import SimpleNamespace

import pytest
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import GenerateRequest
from app.errors import AppException
from app.services import video_localization_dubbing_executor as executor
from app.domains.video_localization.dubbing_generation_identity import (
    RUNTIME_FIELDS,
    compatible_generated_identities,
    frozen_group_request,
)


@pytest.fixture(autouse=True)
def configured_executor_identity_projection():
    """Private executor helpers require the same explicit domain ports as app."""

    executor.configure_projection(executor.DubbingExecutionProjection(
        read_production_run=lambda _project_id: None,
        get_video_localization=lambda _project_id: None,
        frozen_group_request=frozen_group_request,
        compatible_generated_identities=compatible_generated_identities,
        retry_runtime_fields=RUNTIME_FIELDS,
        reserve_single_tts_handoff=lambda *_args, **_kwargs: None,
        build_single_tts_handoff=lambda *_args, **_kwargs: None,
        finalize_generated_candidate=lambda *_args, **_kwargs: None,
        recover_and_finalize_generated_group=lambda *_args, **_kwargs: None,
        find_continuous_boundary_underfill_groups=lambda *_args, **_kwargs: [],
        mark_workflow_placement_failed=lambda *_args, **_kwargs: None,
        reconcile_workflow_terminal_states=lambda *_args, **_kwargs: None,
        record_group_failure=lambda *_args, **_kwargs: None,
        decide_generation_speed=lambda *_args, **_kwargs: None,
    ))


def frozen_fixture():
    request = GenerateRequest(
        text="请在这里稍等一下。", engine_id="omnivoice", project_id="project-1",
        source="video_localization", bind_to_video_localization=True,
        video_localization_dubbing_group_id="group-1", video_localization_dubbing_plan_revision=1,
        video_localization_target_subtitle_ids=["subtitle-1"], video_localization_source_cue_ids=["cue-1"],
        video_localization_generation_attempt=1, speed=1.05, language="zh",
        ref_text="Please wait here.", reference_audio_path="/managed/reference.wav",
        custom_reference_source_audio_path="/managed/vocals.wav",
        custom_reference_trim_start_ms=1000, custom_reference_trim_end_ms=4000,
        engine_parameters={"num_step": 20}, seed=None,
    )
    group = SimpleNamespace(group_id="group-1", spoken_text=request.text, subtitle_ids=["subtitle-1"], unit_ids=["unit-1"])
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(plan_revision=1, groups=[group], semantic_units=[SimpleNamespace(unit_id="unit-1", source_cue_ids=["cue-1"])]),
            candidate_inputs=[], candidate_reports=[],
        ),
        tts_tasks=[SimpleNamespace(stages=[SimpleNamespace(kind="generation", parameters=request.model_dump(mode="json"))])],
        timeline_clips=[{"clip_id": "keep-old", "dubbing_group_id": "group-1", "candidate_id": "candidate-old"}],
    )
    return request, group, draft


def test_retry_reads_frozen_controls_instead_of_current_speed():
    request, group, draft = frozen_fixture()
    assert executor._frozen_group_retry_request(draft, group) == request


@pytest.mark.parametrize("field,value", [("ref_text", ""), ("speed", None), ("video_localization_dubbing_plan_revision", 2), ("text", "已改动台词"), ("video_localization_source_cue_ids", ["other"])])
def test_retry_refuses_missing_or_stale_submission(field, value):
    request, group, draft = frozen_fixture()
    parameters = request.model_dump(mode="json")
    parameters[field] = value
    assert executor._frozen_group_retry_request(draft, group, parameters) is None


@pytest.mark.parametrize("change", [{"speed": 1.10}, {"ref_text": "Different reference"}, {"custom_reference_trim_start_ms": 1100}, {"engine_parameters": {"num_step": 30}}])
def test_retry_detects_changed_reference_or_generation_controls(change):
    request, _, _ = frozen_fixture()
    assert not executor._same_retry_contract(request, request.model_copy(update=change))


def test_retry_allows_new_task_identity_without_changing_content():
    request, _, _ = frozen_fixture()
    assert executor._same_retry_contract(request, request.model_copy(update={"generation_id": "new", "video_localization_generation_attempt": 2, "resource_priority": "foreground_resume"}))


def test_retry_does_not_fill_missing_engine_settings_from_current_defaults():
    request, group, draft = frozen_fixture()
    parameters = request.model_dump(mode="json")
    del parameters["temperature"]
    assert executor._frozen_group_retry_request(draft, group, parameters) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["single_group", "all_remaining"])
@pytest.mark.parametrize("review_mode", ["full", "risk_based", "supervised"])
async def test_same_frozen_retry_for_both_scopes_and_modes(monkeypatch, scope, review_mode):
    request, _, draft = frozen_fixture()
    before = list(draft.timeline_clips)
    calls = []
    monkeypatch.setattr(executor, "_require_projection", lambda: SimpleNamespace(
        get_video_localization=lambda _: draft,
        frozen_group_request=frozen_group_request,
        retry_runtime_fields=RUNTIME_FIELDS,
    ))

    async def queue(project_id, **kwargs):
        calls.append((project_id, kwargs))
        return SimpleNamespace(status="queued")

    monkeypatch.setattr(executor, "_queue_group_for_review_mode", queue)
    result = await executor._queue_whole_group_recovery("project-1", group_id="group-1", review_mode=review_mode, window=executor._bounded_window(), attempt=2, scope=scope)
    assert result.status == "queued"
    assert calls[0][1]["scope"] == scope
    assert calls[0][1]["generation_parameters"] == request.model_dump(mode="json")
    assert calls[0][1]["frozen_retry_request"] == request
    assert draft.timeline_clips == before


@pytest.mark.asyncio
async def test_retry_budget_includes_persisted_candidate_and_does_not_loop(monkeypatch):
    _, _, draft = frozen_fixture()
    draft.dubbing_production.candidate_inputs = [SimpleNamespace(group_id="group-1", candidate_id="candidate-new", plan_revision=1)]
    monkeypatch.setattr(executor, "_require_projection", lambda: SimpleNamespace(
        get_video_localization=lambda _: draft,
        frozen_group_request=frozen_group_request,
        retry_runtime_fields=RUNTIME_FIELDS,
    ))
    result = await executor._queue_whole_group_recovery("project-1", group_id="group-1", review_mode="full", window=executor._bounded_window(), attempt=2, scope="single_group")
    assert result is None


@pytest.mark.asyncio
async def test_changed_reference_is_not_submitted_and_old_timeline_is_preserved(monkeypatch):
    request, group, draft = frozen_fixture()
    before = list(draft.timeline_clips)
    failed = []
    actual = request.model_copy(update={"ref_text": "Unexpected changed reference", "video_localization_workflow_id": "abcdef123456"})
    monkeypatch.setattr(executor, "_require_projection", lambda: SimpleNamespace(
        assess_group_preflight=None,
        reserve_single_tts_handoff=lambda *_a, **_k: SimpleNamespace(workflow_id="abcdef123456"),
        build_single_tts_handoff=lambda *_a, **_k: actual,
        retry_runtime_fields=RUNTIME_FIELDS,
    ))
    monkeypatch.setattr(executor.video_localization_tts_handoff, "finalize_submission", lambda value: value)
    monkeypatch.setattr(executor.video_localization_tts_handoff, "mark_workflow_terminal", lambda *a, **k: failed.append(k))

    async def unexpected_submit(*args, **kwargs):
        pytest.fail("changed frozen reference must not enter the generation queue")

    monkeypatch.setattr(executor.task_queue, "submit", unexpected_submit)
    with pytest.raises(AppException) as caught:
        await executor._queue_group("project-1", draft=draft, group=group, scope="single_group", attempt=2,
                                    generation_parameters=request.model_dump(mode="json"), frozen_retry_request=request)
    assert caught.value.code == "VIDEO_LOCALIZATION_DUBBING_RETRY_INPUT_CHANGED"
    assert failed[0]["status"] == "failed"
    assert draft.timeline_clips == before


@pytest.mark.asyncio
async def test_retry_respects_existing_single_flight_reservation(monkeypatch):
    request, group, draft = frozen_fixture()

    def busy(*args, **kwargs):
        raise AppException(409, "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY", "busy")

    monkeypatch.setattr(executor, "_require_projection", lambda: SimpleNamespace(
        assess_group_preflight=None, reserve_single_tts_handoff=busy,
        retry_runtime_fields=RUNTIME_FIELDS,
    ))
    response = await executor._queue_group("project-1", draft=draft, group=group, scope="single_group", attempt=2,
                                            generation_parameters=request.model_dump(mode="json"), frozen_retry_request=request)
    assert response.status == "waiting"


@pytest.mark.asyncio
async def test_real_handoff_and_saved_task_preserve_frozen_retry_contract(tmp_path, monkeypatch):
    """Use real managed-file preparation and persisted submission, no inference."""
    from tests.test_video_localization_tts_selection import _project_with_independent_ranges, _create_dubbing_plan
    from app.domains.video_localization import service, draft_store
    from app.services import task_queue, video_localization_tts_handoff

    # Reuse project fixture data but restore its mocked media adapters before
    # preparing either reference: both handoffs below crop real managed audio.
    with monkeypatch.context() as fixture_patch:
        client, project_id, _ = _project_with_independent_ranges(tmp_path, fixture_patch)
    plan = _create_dubbing_plan(client, project_id)
    group_id = plan["groups"][0]["group_id"]
    draft = draft_store.get(project_id)
    group = next(item for item in draft.dubbing_production.active_plan.groups if item.group_id == group_id)
    source_ids = executor._source_cue_ids(draft, group)
    first = service.build_single_tts_handoff(project_id, group_id, target_subtitle_ids=list(group.subtitle_ids),
                                           source_cue_ids=source_ids, parameters={"engine_id": "omnivoice", "speed": 1.05})
    first = service.finalize_single_tts_submission(first.model_copy(update={
        "video_localization_execution_scope": "single_group", "video_localization_generation_attempt": 1,
    }))
    enqueued = []
    monkeypatch.setattr(task_queue, "start_worker", lambda: None)
    monkeypatch.setattr(task_queue, "_enqueue_task_id", lambda task_id: enqueued.append(task_id))
    task_id = await task_queue.submit(first, project_id=project_id, segment_id=first.segment_id)
    saved = task_queue.get_task(task_id)
    assert saved is not None and enqueued == [task_id]
    draft = draft_store.get(project_id)
    frozen = executor._frozen_group_retry_request(draft, group, saved.parameters)
    assert frozen is not None
    assert executor._same_retry_contract(first, frozen)

    # End only this fixture's task before preparing the allowed new attempt.
    task_queue.cancel_task(task_id)
    second = service.build_single_tts_handoff(project_id, group_id, target_subtitle_ids=list(group.subtitle_ids),
                                            source_cue_ids=source_ids, parameters=frozen.model_dump(mode="json"))
    second = service.finalize_single_tts_submission(second.model_copy(update={
        "video_localization_execution_scope": "single_group", "video_localization_generation_attempt": 2,
    }))
    assert first.video_localization_workflow_id != second.video_localization_workflow_id
    assert first.reference_audio_path == second.reference_audio_path
    assert executor._same_retry_contract(frozen, second)
    video_localization_tts_handoff.mark_workflow_terminal(second, status="failed", error_message="fixture complete", source_id=second.video_localization_workflow_id)


def test_recovery_decision_remains_typed_through_real_handoff(tmp_path, monkeypatch):
    from tests.test_video_localization_tts_selection import _project_with_independent_ranges, _create_dubbing_plan
    from app.domains.video_localization import service, draft_store
    from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision
    with monkeypatch.context() as fixture_patch:
        client, project_id, _ = _project_with_independent_ranges(tmp_path, fixture_patch)
    plan = _create_dubbing_plan(client, project_id)
    draft = draft_store.get(project_id)
    group = draft.dubbing_production.active_plan.groups[0]
    decision = DubbingRecoveryDecision(recovery_id='123456789abc', source_revision=plan['source_revision'],
        plan_revision=plan['plan_revision'], group_id=group.group_id, stage='semantic_phrases',
        phrases=[group.spoken_text[:1], group.spoken_text[1:]], reason='fixture')
    request = service.build_single_tts_handoff(project_id, group.group_id,
        target_subtitle_ids=list(group.subtitle_ids), source_cue_ids=executor._source_cue_ids(draft, group),
        parameters={'engine_id': 'omnivoice', 'video_localization_recovery': decision.model_dump()})
    assert request.video_localization_recovery == decision
    finalized = service.finalize_single_tts_submission(request)
    assert finalized.video_localization_recovery == decision
    assert finalized.video_localization_recovery.phrases == decision.phrases
