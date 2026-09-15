from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.api import video_localization as video_localization_api  # noqa: E402
from app.domains.video_localization import (  # noqa: E402
    draft_store,
    dub_subtitles,
    exporting,
    media_assets,
    service as project_service,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationDubSubtitleCue,
    VideoLocalizationSubtitleCue,
)
from app.domains.video_localization.dubbing_production_service import (  # noqa: E402
    dubbing_production,
)
from app.schemas.video_localization_dubbing_production import (  # noqa: E402
    DubbingCandidateCqcInput,
    DubbingTimelineAuditReport,
)
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    GenerationTask,
    HistoryItem,
    TaskStatus,
    TTSVerificationResponse,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
)
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
    video_localization_dubbing_executor,
    video_localization_tts_handoff,
    task_queue,
    generation_task_scheduler,
)
from app.schemas.video_localization_dubbing_production import (  # noqa: E402
    DubbingProductionExecuteResponse,
)


SOURCE_REVISION = "current-source-revision"


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    client = TestClient(app)
    project = client.post(
        "/api/projects",
        json={"name": "配音生产契约", "description": ""},
    ).json()
    project_id = project["project_id"]
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_1",
                    "speaker_id": "speaker_1",
                    "start_ms": 0,
                    "end_ms": 900,
                    "audio_route": "clone_from_source",
                    "review_status": "ready",
                    "en_subtitle_text": "First sentence",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_1",
                    "start_ms": 0,
                    "end_ms": 900,
                    "text": "第一句",
                    "tts_text": "第一句",
                    "source_cue_ids": ["cue_1"],
                }
            ],
            "localization_state": {
                "source_fingerprint": SOURCE_REVISION,
            },
        },
    )
    assert response.status_code == 200, response.text
    return client, project_id


def _current_revision(client: TestClient, project_id: str) -> str:
    response = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/snapshot"
    )
    assert response.status_code == 200
    return response.json()["source_revision"]


def _plan_payload(source_revision: str) -> dict:
    return {
        "schema_version": "dubbing-generation-plan-input-v1",
        "source_revision": source_revision,
        "semantic_units": [
            {
                "unit_id": "localized_1",
                "subtitle_ids": ["localized_1"],
                "source_cue_ids": ["cue_1"],
                "speaker_id": "speaker_1",
                "scene_id": "scene_1",
                "start_ms": 0,
                "end_ms": 900,
                "source_anchor_start_ms": 0,
                "source_anchor_end_ms": 900,
                "display_text": "第一句",
                "spoken_text": "第一句",
                "speech_policy": "translate",
            }
        ],
        "boundaries": [],
    }


def test_priority_update_of_full_pipeline_is_durable_scoped_and_recovers_dispatch(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path)
    draft = client.get(f"/api/projects/{project_id}/video-localization").json()
    draft["cues"].append({**draft["cues"][0], "cue_id": "cue_2", "start_ms": 3000, "end_ms": 3900, "en_subtitle_text": "Second sentence"})
    draft["localized_subtitles"].append({**draft["localized_subtitles"][0], "subtitle_id": "localized_2", "start_ms": 3000, "end_ms": 3900, "text": "第二句", "tts_text": "第二句", "source_cue_ids": ["cue_2"]})
    assert client.put(f"/api/projects/{project_id}/video-localization", json=draft).status_code == 200
    snapshot = client.get(f"/api/projects/{project_id}/video-localization/dubbing/snapshot").json()
    payload = {"source_revision": snapshot["source_revision"], "semantic_units": snapshot["semantic_units"], "boundaries": snapshot["boundaries"], "policy": {"preferred_group_units": 1}}
    response = client.post(f"/api/projects/{project_id}/video-localization/dubbing/plan", json=payload)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert len(plan["groups"]) == 2
    current = draft_store.get(project_id)
    tasks, workflows = [], []
    for index, group in enumerate(plan["groups"]):
        task = GenerationTask(
            task_id=f"priority-task-{index}", engine_id="omnivoice", project_id=project_id,
            status=TaskStatus.queued, input_text=group["spoken_text"],
            parameters={"text": group["spoken_text"], "engine_id": "omnivoice",
                        "resource_priority": "normal", "source": "video_localization",
                        "video_localization_execution_scope": "all_remaining",
                        "video_localization_dubbing_plan_revision": plan["plan_revision"],
                        "video_localization_dubbing_group_id": group["group_id"]},
        )
        database.upsert("tasks", task.task_id, task.model_dump())
        tasks.append(task)
        workflows.append(VideoLocalizationTtsTask(
            workflow_id=f"priority-workflow-{index}", project_id=project_id,
            segment_id=group["group_id"], generation_task_id=task.task_id,
            status="queued", subtitle_summary=group["spoken_text"], text=group["spoken_text"],
            start_ms=group["target_start_ms"], end_ms=group["target_end_ms"],
            source_cue_ids=[f"cue_{index + 1}"],
            stages=[VideoLocalizationTtsTaskStage(kind="generation", status="queued", parameters=task.parameters),
                    VideoLocalizationTtsTaskStage(kind="placement", status="pending")],
        ))
    draft_store.save(project_id, current.model_copy(update={"tts_tasks": workflows}), intent="runtime")
    monkeypatch.setattr(video_localization_dubbing_executor, "_projection", replace(
        video_localization_dubbing_executor._projection,
        get_video_localization=project_service.get_video_localization,
        read_production_run=dubbing_production.read_production_run,
        reconcile_workflow_terminal_states=project_service.reconcile_dubbing_workflow_terminal_states,
        set_group_scheduling_priority=dubbing_production.set_group_scheduling_priority,
    ))
    monkeypatch.setattr(video_localization_tts_handoff, "resolve_generation_priority", dubbing_production.resolve_generation_priority)
    selected = plan["groups"][0]["group_id"]
    execute = {"scope": "all_remaining", "start_group_id": selected, "end_group_id": selected,
               "max_in_flight_groups": 1, "resource_priority": "foreground_resume"}
    response = client.post(f"/api/projects/{project_id}/video-localization/dubbing/production-run/execute", json=execute)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "waiting"
    assert task_queue.get_task(tasks[0].task_id).parameters["resource_priority"] == "foreground_resume"
    assert task_queue.get_task(tasks[1].task_id).parameters["resource_priority"] == "normal"
    groups = client.get(f"/api/projects/{project_id}/video-localization/dubbing/production-run").json()["groups"]
    assert [group["resource_priority"] for group in groups] == ["foreground_resume", "normal"]
    # A stale worker's progress write must not undo the queue update.
    task_queue._save(tasks[0])
    assert task_queue.get_task(tasks[0].task_id).parameters["resource_priority"] == "foreground_resume"
    # Simulate process loss between the plan write and task-row projection.
    raw = database.get_one("tasks", "task_id", tasks[0].task_id)
    raw["parameters"]["resource_priority"] = "normal"
    database.upsert("tasks", tasks[0].task_id, raw)
    database.set_db_path(tmp_path / "voice_studio.db")
    async def after_restart():
        def describe(task_id):
            if task_id == "other-project-task":
                return generation_task_scheduler.descriptor(task_id, project_id="other", priority="normal")
            return task_queue._queued_task_descriptor(task_id)
        queue = generation_task_scheduler.ProjectFairGenerationQueue(describe)
        queue.put_nowait("other-project-task")
        for task_id in task_queue._recover_incomplete_tasks():
            queue.put_nowait(task_id)
        return [await queue.get(), await queue.get(), await queue.get()]
    import asyncio
    assert asyncio.run(after_restart()) == [tasks[0].task_id, "other-project-task", tasks[1].task_id]
    # Omission means preserve; an explicit normal resets only this range.
    execute.pop("resource_priority")
    assert client.post(f"/api/projects/{project_id}/video-localization/dubbing/production-run/execute", json=execute).status_code == 200
    assert dubbing_production.resolve_generation_priority(tasks[0]) == "foreground_resume"
    execute["resource_priority"] = "normal"
    assert client.post(f"/api/projects/{project_id}/video-localization/dubbing/production-run/execute", json=execute).status_code == 200
    assert dubbing_production.resolve_generation_priority(tasks[0]) == "normal"
    # Creating a new plan never grants the project permanent priority.
    dubbing_production.set_group_scheduling_priority(project_id, source_revision=plan["source_revision"], plan_revision=plan["plan_revision"], group_ids=[selected], priority="foreground_resume")
    response = client.post(f"/api/projects/{project_id}/video-localization/dubbing/plan", json=payload)
    assert response.status_code == 200, response.text
    assert draft_store.get(project_id).dubbing_production.scheduling_policies == []


def test_http_plan_uses_versioned_application_facade(tmp_path: Path):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "dubbing-generation-plan-v1"
    assert payload["plan_revision"] == 1
    assert payload["groups"][0]["unit_ids"] == ["localized_1"]
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.dubbing_production.active_plan is not None
    assert saved.dubbing_production.enforcement_mode == "planned"
    assert saved.dubbing_production.active_plan.source_revision == source_revision
    assert saved.dubbing_production.active_plan.plan_revision == 1


def test_http_single_and_full_execution_share_one_executor(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id = _client(tmp_path)
    calls: list[tuple[str, str, str | None, str, bool, float | None]] = []

    async def fake_advance(
        target_project_id: str,
        *,
        scope: str,
        group_id=None,
        review_mode="full",
        regenerate_existing=False,
        repair_timeline_capacity=False,
        **_kwargs,
    ):
        calls.append(
            (
                target_project_id,
                scope,
                group_id,
                review_mode,
                regenerate_existing,
                _kwargs.get("ordinary_speed_baseline"),
            )
        )
        return DubbingProductionExecuteResponse(
            status="queued",
            scope=scope,
            group_id="group_localized_1_localized_1_1",
            task_id=f"task-{scope}",
            message="queued",
        )

    monkeypatch.setattr(
        video_localization_dubbing_executor,
        "advance",
        fake_advance,
    )

    for scope in ("single_group", "all_remaining"):
        group_id = "group_localized_1_localized_1_1" if scope == "single_group" else None
        review_mode = "full" if scope == "single_group" else "supervised"
        response = client.post(
            f"/api/projects/{project_id}/video-localization/dubbing/production-run/execute",
            json={
                "schema_version": "dubbing-production-execute-v1",
                "scope": scope,
                "review_mode": review_mode,
                **(
                    {"ordinary_speed_baseline": 1.18}
                    if scope == "all_remaining"
                    else {}
                ),
                **({"group_id": group_id} if group_id else {}),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["task_id"] == f"task-{scope}"

    assert calls == [
        (
            project_id,
            "single_group",
            "group_localized_1_localized_1_1",
            "full",
            False,
            None,
        ),
        (project_id, "all_remaining", None, "supervised", False, 1.18),
    ]


def test_http_refreshes_plan_timing_without_replanning_groups(tmp_path: Path):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    original = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan/refresh-timing"
    )

    assert response.status_code == 200, response.text
    refreshed = response.json()
    assert refreshed["plan_revision"] == original["plan_revision"] + 1
    assert [item["group_id"] for item in refreshed["groups"]] == [
        item["group_id"] for item in original["groups"]
    ]
    assert [item["unit_ids"] for item in refreshed["groups"]] == [
        item["unit_ids"] for item in original["groups"]
    ]


def test_http_resets_only_requested_current_plan_group_placements(tmp_path: Path):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    group = plan["groups"][0]
    draft = draft_store.get(project_id)
    assert draft is not None
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "timeline_clips": [
                    {
                        "clip_id": "planned_clip",
                        "candidate_id": "candidate_1",
                        "result_id": "result_1",
                        "track_id": "dub",
                        "dubbing_group_id": group["group_id"],
                        "start_ms": 0,
                        "end_ms": 900,
                        "audio_path": str(tmp_path / "candidate.wav"),
                    },
                    {
                        "clip_id": "legacy_clip_without_group_id",
                        "candidate_id": "candidate_legacy",
                        "result_id": "result_legacy",
                        "track_id": "dub",
                        "target_subtitle_ids": ["localized_1"],
                        "start_ms": 0,
                        "end_ms": 900,
                        "audio_path": str(tmp_path / "legacy.wav"),
                    },
                    {
                        "clip_id": "unrelated_clip",
                        "track_id": "music",
                        "start_ms": 0,
                        "end_ms": 900,
                        "audio_path": str(tmp_path / "music.wav"),
                    },
                ]
            }
        ),
        intent="runtime",
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/reset-groups",
        json={
            "schema_version": "dubbing-timeline-group-reset-v1",
            "source_revision": source_revision,
            "plan_revision": plan["plan_revision"],
            "group_ids": [group["group_id"]],
        },
    )

    assert response.status_code == 200, response.text
    assert [item["clip_id"] for item in response.json()["timeline_clips"]] == [
        "unrelated_clip"
    ]


def test_http_production_run_reports_next_recoverable_group_action(tmp_path: Path):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()

    response = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "dubbing-production-run-v1"
    assert payload["plan_revision"] == plan["plan_revision"]
    assert payload["status"] == "running"
    assert payload["next_group_id"] == plan["groups"][0]["group_id"]
    assert payload["next_action"] == "generate_candidate"
    assert payload["groups"][0]["stage"] == "ready_to_generate"


def test_http_production_run_does_not_wait_on_interrupted_prepared_workflow(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    group = plan["groups"][0]
    draft = draft_store.get(project_id)
    assert draft is not None
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "tts_tasks": [
                    VideoLocalizationTtsTask(
                        workflow_id="interrupted_workflow",
                        project_id=project_id,
                        segment_id=group["group_id"],
                        subtitle_summary="第一句",
                        text="第一句",
                        source_cue_ids=["cue_1"],
                        start_ms=0,
                        end_ms=900,
                        status="prepared",
                        updated_at="2020-01-01T00:00:00",
                        stages=[
                            VideoLocalizationTtsTaskStage(
                                kind="generation",
                                status="pending",
                                parameters={
                                    "video_localization_dubbing_plan_revision": plan[
                                        "plan_revision"
                                    ],
                                    "video_localization_dubbing_group_id": group[
                                        "group_id"
                                    ],
                                },
                            ),
                            VideoLocalizationTtsTaskStage(kind="placement"),
                        ],
                    )
                ]
            }
        ),
        intent="runtime",
    )

    response = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["groups"][0]["stage"] == "failed"
    assert payload["groups"][0]["recommended_action"] == "regenerate_candidate"
    assert payload["groups"][0]["passed_candidate_id"] is None
    assert payload["groups"][0]["attempt_count"] == 0


def test_http_production_run_does_not_accept_unreviewed_clip_over_old_failure(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    group = plan["groups"][0]
    common_parameters = {
        "video_localization_dubbing_plan_revision": plan["plan_revision"],
        "video_localization_dubbing_group_id": group["group_id"],
    }
    draft = draft_store.get(project_id)
    assert draft is not None
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "tts_tasks": [
                    VideoLocalizationTtsTask(
                        workflow_id="old_failure",
                        project_id=project_id,
                        segment_id="localized_1",
                        subtitle_summary="第一句",
                        text="第一句",
                        source_cue_ids=["cue_1"],
                        start_ms=0,
                        end_ms=900,
                        status="failed",
                        generation_task_id="old_task",
                        stages=[
                            VideoLocalizationTtsTaskStage(
                                kind="generation",
                                status="failed",
                                parameters=common_parameters,
                                error_message="旧任务失败",
                            ),
                            VideoLocalizationTtsTaskStage(kind="placement"),
                        ],
                    ),
                    VideoLocalizationTtsTask(
                        workflow_id="new_success",
                        project_id=project_id,
                        segment_id="localized_1",
                        subtitle_summary="第一句",
                        text="第一句",
                        source_cue_ids=["cue_1"],
                        start_ms=0,
                        end_ms=900,
                        status="success",
                        generation_task_id="new_task",
                        result_id="new_result",
                        timeline_clip_id="clip_localized_1",
                        stages=[
                            VideoLocalizationTtsTaskStage(
                                kind="generation",
                                status="success",
                                parameters=common_parameters,
                            ),
                            VideoLocalizationTtsTaskStage(
                                kind="placement",
                                status="success",
                            ),
                        ],
                    ),
                ],
                "timeline_clips": [
                    {
                        "clip_id": "clip_localized_1",
                        "track_id": "dub",
                        "dub_lane": 1,
                        "subtitle_id": "localized_1",
                        "target_subtitle_ids": ["localized_1"],
                        "candidate_id": "candidate_new_task",
                        "result_id": "new_result",
                        "start_ms": 0,
                        "end_ms": 800,
                        "source_start_ms": 0,
                        "source_end_ms": 800,
                    }
                ],
            }
        ),
        intent="runtime",
    )

    response = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    )

    assert response.status_code == 200, response.text
    progress = response.json()["groups"][0]
    assert progress["stage"] == "needs_gap_processing"
    assert progress["recommended_action"] == "process_gaps"
    assert progress["formal_clip_ids"] == []


def test_http_records_terminal_group_failure_and_exact_reset_reopens_it(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    group_id = plan["groups"][0]["group_id"]

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run/failure",
        json={
            "schema_version": "dubbing-production-group-failure-request-v1",
            "source_revision": source_revision,
            "plan_revision": plan["plan_revision"],
            "group_id": group_id,
            "title": "配音组生成失败",
            "note": "输入修正、缩组和主引擎有界重试后仍没有可安全落位的完整候选。",
            "reason_code": "DUBBING_GROUP_RECOVERY_EXHAUSTED",
            "attempted_strategy_codes": [
                "repair_input",
                "shorten_group",
                "retry_omnivoice",
            ],
            "attempt_count": 3,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "timeline_markers" not in payload
    failure = payload["dubbing_production"]["group_failures"][0]
    assert failure["group_id"] == group_id
    assert failure["reason_code"] == "DUBBING_GROUP_RECOVERY_EXHAUSTED"

    run = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    ).json()
    assert run["groups"][0]["stage"] == "failed"
    assert run["failed_group_count"] == 1
    assert run["next_action"] == "complete"

    reset = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/reset-groups",
        json={
            "schema_version": "dubbing-timeline-group-reset-v1",
            "source_revision": source_revision,
            "plan_revision": plan["plan_revision"],
            "group_ids": [group_id],
        },
    )
    assert reset.status_code == 200, reset.text
    assert "timeline_markers" not in reset.json()
    assert reset.json()["dubbing_production"]["group_failures"] == []
    reopened = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    ).json()
    assert reopened["groups"][0]["stage"] == "ready_to_generate"
    assert reopened["failed_group_count"] == 0


def test_http_discarding_group_candidates_does_not_recover_old_workflow(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    group = plan["groups"][0]
    workflow = VideoLocalizationTtsTask(
        workflow_id="workflow_old",
        project_id=project_id,
        segment_id=group["subtitle_ids"][0],
        subtitle_summary="旧候选",
        text=group["spoken_text"],
        source_cue_ids=["cue_1"],
        start_ms=group["target_start_ms"],
        end_ms=group["target_end_ms"],
        status="success",
        generation_task_id="task_old",
        result_id="candidate_old",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation",
                status="success",
                progress=1.0,
                parameters={
                    "video_localization_dubbing_plan_revision": plan[
                        "plan_revision"
                    ],
                    "video_localization_dubbing_group_id": group["group_id"],
                },
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement",
                status="success",
                progress=1.0,
            ),
        ],
    )

    project_service.update_video_localization_atomic(
        project_id,
        lambda draft: draft.model_copy(
            update={"tts_tasks": [*draft.tts_tasks, workflow]}
        ),
        intent="runtime",
    )
    before = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    ).json()
    assert before["groups"][0]["stage"] == "needs_gap_processing"
    assert before["groups"][0]["candidate_ids"] == ["candidate_old"]

    reset = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/reset-groups",
        json={
            "schema_version": "dubbing-timeline-group-reset-v1",
            "source_revision": source_revision,
            "plan_revision": plan["plan_revision"],
            "group_ids": [group["group_id"]],
            "discard_current_candidates": True,
        },
    )

    assert reset.status_code == 200, reset.text
    payload = reset.json()
    assert payload["tts_tasks"][-1]["workflow_id"] == "workflow_old"
    assert set(payload["ui_state"]["discarded_tts_task_ids"]) >= {
        "workflow_old",
        "task_old",
        "candidate_old",
    }
    reopened = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    ).json()
    assert reopened["groups"][0]["stage"] == "ready_to_generate"
    assert reopened["groups"][0]["candidate_ids"] == []


def test_http_plan_rejects_stale_source_revision(tmp_path: Path):
    client, project_id = _client(tmp_path)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload("stale-source-revision"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DUBBING_SOURCE_CHANGED"
    )


def test_http_production_run_preserves_current_plan_when_lane_is_empty(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    def replace_content(draft):
        return draft.model_copy(
            update={
                "cues": [
                    VideoLocalizationCue(
                        cue_id=f"cue_{index}",
                        speaker_id="speaker_1",
                        start_ms=(index - 1) * 4_000,
                        end_ms=index * 4_000,
                        audio_route="clone_from_source",
                        review_status="ready",
                        source_word_ids=[f"word_{index}"],
                    )
                    for index in range(1, 6)
                ],
                "localized_subtitles": [
                    VideoLocalizationSubtitleCue(
                        subtitle_id=f"localized_{index}",
                        start_ms=(index - 1) * 4_000,
                        end_ms=index * 4_000,
                        text=f"第{index}句",
                        tts_text=f"第{index}句",
                        source_cue_ids=[f"cue_{index}"],
                        source_word_ids=[f"word_{index}"],
                    )
                    for index in range(1, 6)
                ],
                "localization_state": {"source_fingerprint": SOURCE_REVISION},
            }
        )

    project_service.update_video_localization_atomic(
        project_id,
        replace_content,
        intent="content",
    )
    snapshot = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/snapshot"
    ).json()
    plan_response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json={
            "schema_version": "dubbing-generation-plan-input-v1",
            "source_revision": snapshot["source_revision"],
            "semantic_units": snapshot["semantic_units"],
            "boundaries": [
                {
                    **boundary,
                    "same_scene": True,
                    "semantic_relation": "continuous",
                    "no_break_with_next": True,
                }
                for boundary in snapshot["boundaries"]
            ],
        },
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    response = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    )

    assert response.status_code == 200, response.text
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.timeline_clips == []
    assert saved.dubbing_production.active_plan is not None
    assert saved.dubbing_production.active_plan.plan_revision == plan["plan_revision"]


def test_plan_commit_rechecks_source_revision_inside_atomic_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    original_update = project_service.update_video_localization_atomic

    def update_after_concurrent_source_change(
        target_project_id,
        updater,
        **kwargs,
    ):
        current = draft_store.get(target_project_id)
        assert current is not None
        changed_subtitles = [
            subtitle.model_copy(update={"text": "并发修改后的第一句"})
            for subtitle in current.localized_subtitles
        ]
        draft_store.save(
            target_project_id,
            current.model_copy(
                update={"localized_subtitles": changed_subtitles}
            ),
            intent="content",
        )
        return original_update(
            target_project_id,
            updater,
            **kwargs,
        )

    monkeypatch.setattr(
        project_service,
        "update_video_localization_atomic",
        update_after_concurrent_source_change,
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DUBBING_SOURCE_CHANGED"
    )
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.dubbing_production.active_plan is None


def test_http_plan_rejects_caller_rewriting_server_snapshot_facts(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    payload = _plan_payload(_current_revision(client, project_id))
    payload["semantic_units"][0]["spoken_text"] = "调用方伪造的台词"

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DUBBING_PLAN_SNAPSHOT_MISMATCH"
    )


def test_localized_text_edit_rebases_compatible_active_plan(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    created = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    )
    assert created.status_code == 200, created.text
    original_plan = created.json()

    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    draft["localized_subtitles"][0]["text"] = "更新后的上屏字幕"
    draft["localized_subtitles"][0]["tts_text"] = "更新后的配音台词"
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    )

    assert saved.status_code == 200, saved.text
    rebased = saved.json()["dubbing_production"]["active_plan"]
    assert rebased is not None
    assert rebased["source_revision"] != source_revision
    assert rebased["plan_revision"] == original_plan["plan_revision"] + 1
    assert rebased["semantic_units"][0]["speech_policy"] == "translate"
    assert rebased["groups"][0]["spoken_text"] == "更新后的配音台词"


def test_source_identity_change_does_not_rebase_active_plan(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    created = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    )
    assert created.status_code == 200, created.text

    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    draft["cues"][0]["speaker_id"] = "speaker_changed"
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["dubbing_production"]["active_plan"] is None


def test_source_identity_change_keeps_unaffected_units_in_larger_plan(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    draft["cues"].append(
        {
            "cue_id": "cue_2",
            "speaker_id": "speaker_1",
            "start_ms": 1_000,
            "end_ms": 1_900,
            "audio_route": "manual_review",
            "review_status": "ready",
            "en_subtitle_text": "Second sentence",
        }
    )
    draft["localized_subtitles"].append(
        {
            "subtitle_id": "localized_2",
            "start_ms": 1_000,
            "end_ms": 1_900,
            "text": "第二句",
            "tts_text": "第二句",
            "source_cue_ids": ["cue_2"],
        }
    )
    assert client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    ).status_code == 200
    snapshot = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/snapshot"
    ).json()
    reviewed_units = [
        {**unit, "speech_policy": "translate"}
        for unit in snapshot["semantic_units"]
    ]
    created = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json={
            "source_revision": snapshot["source_revision"],
            "semantic_units": reviewed_units,
            "boundaries": snapshot["boundaries"],
        },
    )
    assert created.status_code == 200, created.text

    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    draft["cues"][1]["speaker_id"] = "speaker_changed"
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    )

    assert saved.status_code == 200, saved.text
    plan = saved.json()["dubbing_production"]["active_plan"]
    assert plan is not None
    assert [unit["speech_policy"] for unit in plan["semantic_units"]] == [
        "translate",
        "needs_review",
    ]
    assert [group["unit_ids"] for group in plan["groups"]] == [
        ["localized_1"]
    ]


def test_full_draft_save_cannot_downgrade_or_rewrite_active_plan(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    assert client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).status_code == 200

    current = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    forged = dict(current)
    forged["dubbing_production"] = {
        "schema_version": "dubbing-production-state-v2",
        "enforcement_mode": "legacy",
        "active_plan": None,
        "candidate_reports": [],
        "candidate_inputs": [],
        "latest_timeline_audit": None,
    }
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=forged,
    )

    assert response.status_code == 200
    protected = response.json()["dubbing_production"]
    assert protected["enforcement_mode"] == "planned"
    assert protected["active_plan"]["source_revision"] == source_revision

    forged = response.json()
    forged["dubbing_production"]["active_plan"]["semantic_units"][0][
        "spoken_text"
    ] = "调用方伪造的台词"
    forged["dubbing_production"]["active_plan"]["groups"][0][
        "spoken_text"
    ] = "调用方伪造的台词"
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=forged,
    )

    assert response.status_code == 200
    assert response.json()["dubbing_production"]["active_plan"][
        "semantic_units"
    ][0]["spoken_text"] == "第一句"


def test_legacy_dubbing_state_is_read_as_planned_without_losing_audio(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    project = project_store.get_project(project_id)
    assert project is not None
    raw = dict(project.parameters["video_localization"])
    raw["dubbing_production"] = {
        "schema_version": "dubbing-production-state-v2",
        "enforcement_mode": "legacy",
        "plan_revision_counter": 0,
        "active_plan": None,
        "candidate_reports": [],
        "candidate_inputs": [],
        "group_failures": [],
        "latest_timeline_audit": None,
    }
    raw["timeline_clips"] = [
        {
            "clip_id": "legacy_clip",
            "track_id": "dub",
            "audio_path": "/managed/historical.wav",
            "start_ms": 0,
            "end_ms": 800,
        }
    ]
    project.parameters["video_localization"] = raw
    project_store.save_project(project, touch_updated_at=False)

    response = client.get(
        f"/api/projects/{project_id}/video-localization"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dubbing_production"]["enforcement_mode"] == "planned"
    assert payload["dubbing_production"]["active_plan"] is None
    assert payload["timeline_clips"][0]["clip_id"] == "legacy_clip"
    assert payload["timeline_clips"][0]["audio_path"] == (
        "/managed/historical.wav"
    )


def test_http_contract_rejects_unknown_fields(tmp_path: Path):
    client, project_id = _client(tmp_path)
    payload = _plan_payload(_current_revision(client, project_id))
    payload["video_specific_exception"] = True

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_openapi_exposes_all_dubbing_production_contracts():
    paths = app.openapi()["paths"]
    projection = paths["/api/projects/{project_id}/video-localization/dubbing/candidates/{candidate_id}/current-projection"]["post"]
    assert projection["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/DubbingCurrentProjectionRequest")

    assert "/api/projects/{project_id}/video-localization/dubbing/plan" in paths
    assert (
        "/api/projects/{project_id}/video-localization/dubbing/plan/refresh-timing"
        in paths
    )
    assert (
        "/api/projects/{project_id}/video-localization/dubbing/snapshot"
        in paths
    )
    assert (
        "/api/projects/{project_id}/video-localization/dubbing/production-run"
        in paths
    )
    assert (
        "/api/projects/{project_id}/video-localization/dubbing/candidates/{candidate_id}/semantic-boundaries"
        in paths
    )
    assert (
        "/api/projects/{project_id}/video-localization/dubbing/candidates/{candidate_id}/semantic-boundaries/review"
        in paths
    )
    assert not any("/dubbing/timeline/audit" in path for path in paths)
    assert not any("/dubbing/timeline/rebalance" in path for path in paths)
    assert not any("/dubbing/timeline/compact-final" in path for path in paths)
    assert not any("/dubbing/timeline/trim-safe-padding-overlaps" in path for path in paths)
    assert not any("/dubbing/timeline/split-and-rebalance" in path for path in paths)
    assert not any("/dubbing/timeline/edit-gate" in path for path in paths)
    assert not any("/production-run/reconcile-existing" in path for path in paths)


def test_retired_timeline_edit_gate_endpoint_is_not_available(tmp_path: Path):
    client, project_id = _client(tmp_path)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/edit-gate",
        json={"schema_version": "dubbing-timeline-edit-gate-commit-v1"},
    )

    assert response.status_code == 404


def _retired_test_current_timeline_audit_requires_active_plan(tmp_path: Path):
    client, project_id = _client(tmp_path)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/audit-current"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DUBBING_PLAN_REQUIRED"
    )


def _retired_test_raw_timeline_audit_is_advisory_and_does_not_authorize_project(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    assert client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).status_code == 200
    advisory = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/audit",
        json={
            "schema_version": "dubbing-timeline-audit-input-v1",
            "current_source_revision": source_revision,
            "current_timeline_revision": "caller-assumed-timeline",
            "phase": "timeline",
            "expected_units": [
                {
                    "unit_id": "localized_1",
                    "speaker_id": "speaker_1",
                    "scene_id": "scene_1",
                    "speech_policy": "translate",
                    "source_anchor_start_ms": 0,
                    "source_anchor_end_ms": 900,
                }
            ],
            "clips": [
                {
                    "clip_id": "caller_clip",
                    "candidate_id": "caller_candidate",
                    "group_id": "dubbing_group_0001",
                    "unit_ids": ["localized_1"],
                    "speaker_id": "speaker_1",
                    "scene_id": "scene_1",
                    "dub_lane": 0,
                    "timeline_start_ms": 0,
                    "timeline_end_ms": 800,
                    "source_revision": source_revision,
                    "cqc_status": "passed",
                }
            ],
        },
    )

    assert advisory.status_code == 200
    assert advisory.json()["status"] == "passed"
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.dubbing_production.latest_timeline_audit is None

    authoritative = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/audit-current?phase=timeline"
    )
    assert authoritative.status_code == 200
    assert authoritative.json()["status"] == "failed"
    assert {
        finding["code"]
        for finding in authoritative.json()["findings"]
    } == {"TIMELINE_DUB_COVERAGE_MISSING"}


def _retired_test_current_timeline_audit_commit_rechecks_concurrent_timeline_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    assert client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).status_code == 200
    original_update = project_service.update_video_localization_atomic

    def update_after_timeline_change(target_project_id, updater, **kwargs):
        current = draft_store.get(target_project_id)
        assert current is not None
        draft_store.save(
            target_project_id,
            current.model_copy(
                update={
                    "timeline_clips": [
                        {
                            "clip_id": "concurrent_clip",
                            "candidate_id": "concurrent_candidate",
                            "track_id": "dub",
                            "target_subtitle_ids": ["localized_1"],
                            "start_ms": 0,
                            "end_ms": 800,
                            "status": "ready",
                        }
                    ]
                }
            ),
            intent="runtime",
        )
        return original_update(target_project_id, updater, **kwargs)

    monkeypatch.setattr(
        project_service,
        "update_video_localization_atomic",
        update_after_timeline_change,
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/timeline/audit-current?phase=timeline"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DUBBING_AUDIT_STATE_CHANGED"
    )
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.dubbing_production.latest_timeline_audit is None


def test_dub_subtitle_operation_uses_audio_prerequisites_not_delivery_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    assert client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).status_code == 200
    monkeypatch.setattr(
        dub_subtitles,
        "validate_prerequisites",
        lambda _draft: None,
    )
    monkeypatch.setattr(
        "app.domains.video_localization.operation_queue._enqueue",
        lambda *_args, **_kwargs: None,
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/operations/dub-subtitles",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "dub_subtitle_generation"


def _automatic_candidate_cqc_input(
    source_revision: str,
    *,
    plan_revision: int = 0,
) -> DubbingCandidateCqcInput:
    return DubbingCandidateCqcInput(
        source_revision=source_revision,
        plan_revision=plan_revision,
        group_id="dubbing_group_0001",
        candidate_id="candidate_1",
        task_status="success",
        artifact_id="artifact_1",
        audio_sha256="a" * 64,
        expected_spoken_text="第一句",
        reference_transcript="source reference",
        candidate_transcript="第一句",
        target_start_ms=0,
        target_end_ms=900,
        audio={
            "duration_ms": 800,
            "peak_dbfs": -1.0,
            "clipping_ratio": 0,
            "leading_silence_ms": 40,
            "trailing_silence_ms": 50,
            "internal_pauses": [],
        },
        subjective_reviews=[],
    )


def _passing_candidate_review_payload(source_revision: str) -> dict:
    return {
        "schema_version": "dubbing-candidate-review-command-v1",
        "source_revision": source_revision,
        "candidate_id": "candidate_1",
        "subjective_reviews": [
            {"dimension": dimension, "status": "passed"}
            for dimension in (
                "meaning",
                "pronunciation",
                "prosody_parse",
                "voice_match",
                "naturalness",
            )
        ],
    }


def _retired_test_candidate_cqc_report_is_persisted_on_matching_candidate(tmp_path: Path):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan_response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    )
    assert plan_response.status_code == 200
    draft = draft_store.get(project_id)
    assert draft is not None
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "generated_candidates": [
                    {
                        "candidate_id": "candidate_1",
                        "recipe_id": "recipe_1",
                        "status": "success",
                    }
                ],
                "dubbing_production": draft.dubbing_production.model_copy(
                    update={
                        "candidate_inputs": [
                            _automatic_candidate_cqc_input(
                                source_revision,
                                plan_revision=(
                                    draft.dubbing_production.active_plan.plan_revision
                                ),
                            )
                        ]
                    }
                ),
                "timeline_clips": [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "candidate_1",
                        "track_id": "dub",
                        "status": "ready",
                    }
                ],
            }
        ),
        intent="runtime",
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/candidates/cqc",
        json=_passing_candidate_review_payload(source_revision),
    )

    assert response.status_code == 200
    assert response.json()["overall_status"] == "passed"
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.generated_candidates[0]["cqc_status"] == "passed"
    assert saved.generated_candidates[0]["cqc_report"]["schema_version"] == (
        "dubbing-candidate-cqc-v1"
    )
    assert saved.timeline_clips[0]["cqc_status"] == "passed"


def _retired_test_candidate_review_uses_audible_edges_not_trailing_silence_for_scene_limit(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan_response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    )
    assert plan_response.status_code == 200
    draft = draft_store.get(project_id)
    assert draft is not None
    plan_revision = draft.dubbing_production.active_plan.plan_revision
    raw_input = _automatic_candidate_cqc_input(
        source_revision,
        plan_revision=plan_revision,
    ).model_dump(mode="json")
    raw_input.update(
        {
            "planned_scene_end_ms": 900,
            "placement_start_ms": 0,
            "placement_end_ms": 1_040,
            "audio": {
                "duration_ms": 1_040,
                "peak_dbfs": -1.0,
                "clipping_ratio": 0,
                "leading_silence_ms": 100,
                "trailing_silence_ms": 190,
                "speech_start_ms": 100,
                "speech_end_ms": 850,
                "speech_span_ms": 750,
                "voiced_spans": [{"start_ms": 100, "end_ms": 850}],
                "voiced_duration_ms": 750,
                "speaking_rate_ratio": 1.0,
                "internal_pauses": [],
            },
        }
    )
    frozen = DubbingCandidateCqcInput(**raw_input)
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "generated_candidates": [
                    {
                        "candidate_id": "candidate_1",
                        "recipe_id": "recipe_1",
                        "status": "success",
                    }
                ],
                "dubbing_production": draft.dubbing_production.model_copy(
                    update={"candidate_inputs": [frozen]}
                ),
                "timeline_clips": [
                    {
                        "clip_id": "clip_with_tail_silence",
                        "candidate_id": "candidate_1",
                        "track_id": "dub",
                        "target_subtitle_ids": ["localized_1"],
                        "start_ms": 0,
                        "end_ms": 1_040,
                        "source_start_ms": 0,
                        "source_end_ms": 1_040,
                        "status": "ready",
                    }
                ],
            }
        ),
        intent="runtime",
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/candidates/cqc",
        json=_passing_candidate_review_payload(source_revision),
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert "CANDIDATE_OVERRUNS_SCENE" not in {
        finding["code"] for finding in report["findings"]
    }
    saved = draft_store.get(project_id)
    assert saved is not None
    reviewed_input = saved.dubbing_production.candidate_inputs[0]
    assert reviewed_input.placement_start_ms == 100
    assert reviewed_input.placement_end_ms == 850
    assert saved.dubbing_production.candidate_reports[0].candidate_id == (
        "candidate_1"
    )
    assert saved.dubbing_production.candidate_inputs[0].subjective_reviews == []


def _retired_test_candidate_review_commit_rechecks_frozen_input_inside_atomic_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    assert client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).status_code == 200
    current = draft_store.get(project_id)
    assert current is not None
    draft_store.save(
        project_id,
        current.model_copy(
            update={
                "dubbing_production": current.dubbing_production.model_copy(
                    update={
                        "candidate_inputs": [
                            _automatic_candidate_cqc_input(
                                source_revision,
                                plan_revision=(
                                    current.dubbing_production.active_plan.plan_revision
                                ),
                            )
                        ]
                    }
                )
            }
        ),
        intent="runtime",
    )
    original_update = project_service.update_video_localization_atomic

    def update_after_frozen_input_was_replaced(
        target_project_id,
        updater,
        **kwargs,
    ):
        latest = draft_store.get(target_project_id)
        assert latest is not None
        draft_store.save(
            target_project_id,
            latest.model_copy(
                update={
                    "dubbing_production": (
                        latest.dubbing_production.model_copy(
                            update={"candidate_inputs": []}
                        )
                    )
                }
            ),
            intent="runtime",
        )
        return original_update(
            target_project_id,
            updater,
            **kwargs,
        )

    monkeypatch.setattr(
        project_service,
        "update_video_localization_atomic",
        update_after_frozen_input_was_replaced,
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/candidates/cqc",
        json=_passing_candidate_review_payload(source_revision),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_DUBBING_CQC_STATE_CHANGED"
    )
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.dubbing_production.candidate_reports == []


def test_full_draft_save_cannot_inject_candidate_cqc_authority(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    assert client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).status_code == 200
    automatic_input = _automatic_candidate_cqc_input(source_revision)
    current = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    current["dubbing_production"]["candidate_inputs"] = [
        automatic_input.model_dump(mode="json")
    ]
    current["generated_candidates"] = [
        {
            "candidate_id": "candidate_1",
            "status": "success",
            "cqc_status": "passed",
        }
    ]
    current["timeline_clips"] = [
        {
            "clip_id": "clip_1",
            "candidate_id": "candidate_1",
            "track_id": "dub",
            "start_ms": 0,
            "end_ms": 900,
            "cqc_status": "passed",
        }
    ]

    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=current,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dubbing_production"]["candidate_inputs"] == []
    assert body["dubbing_production"]["candidate_reports"] == []
    assert body["generated_candidates"][0].get("cqc_status") is None
    assert body["timeline_clips"] == []


def test_replanning_clears_candidate_and_timeline_cqc_mirrors(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan_payload = _plan_payload(source_revision)
    assert client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=plan_payload,
    ).status_code == 200
    draft = draft_store.get(project_id)
    assert draft is not None
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "generated_candidates": [
                    {
                        "candidate_id": "candidate_1",
                        "status": "success",
                        "cqc_status": "passed",
                        "cqc_report_version": "dubbing-candidate-cqc-v1",
                    }
                ],
                "timeline_clips": [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "candidate_1",
                        "track_id": "dub",
                        "start_ms": 0,
                        "end_ms": 900,
                        "cqc_status": "passed",
                        "cqc_report_version": "dubbing-candidate-cqc-v1",
                    }
                ],
            }
        ),
        intent="runtime",
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=plan_payload,
    )

    assert response.status_code == 200
    saved = draft_store.get(project_id)
    assert saved is not None
    assert "cqc_status" not in saved.generated_candidates[0]
    assert "cqc_report_version" not in saved.generated_candidates[0]
    assert "cqc_status" not in saved.timeline_clips[0]
    assert "cqc_report_version" not in saved.timeline_clips[0]


def _retired_test_candidate_review_rejects_caller_supplied_automatic_evidence(tmp_path: Path):
    client, project_id = _client(tmp_path)
    payload = _passing_candidate_review_payload(
        _current_revision(client, project_id)
    )
    payload["audio_sha256"] = "b" * 64
    payload["candidate_transcript"] = "伪造识别结果"

    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/candidates/cqc",
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_legacy_candidate_apply_route_is_not_a_second_write_path(tmp_path: Path):
    client, project_id = _client(tmp_path)
    draft = draft_store.get(project_id)
    assert draft is not None
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "generated_candidates": [
                    {
                        "candidate_id": "candidate_1",
                        "recipe_id": "recipe_1",
                        "status": "success",
                    }
                ]
            }
        ),
        intent="runtime",
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/candidates/candidate_1/apply"
    )

    assert response.status_code == 404


def _retired_test_worker_verification_projects_automatic_cqc_without_fake_listening(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    plan_response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()
    group = plan["groups"][0]
    audio_path = (
        media_assets.project_video_localization_dir(project_id)
        / "tts"
        / "candidate.wav"
    )
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16_000
    audio = 0.2 * np.sin(
        2 * np.pi * 220 * np.arange(sample_rate) / sample_rate
    )
    sf.write(audio_path, audio, sample_rate)
    planned = draft_store.get(project_id)
    assert planned is not None
    draft_store.save(
        project_id,
        planned.model_copy(
            update={
                "generated_candidates": [
                    {
                        "candidate_id": "candidate_task_1",
                        "task_id": "task_1",
                        "result_id": "result_1",
                        "cue_id": "cue_1",
                        "audio_path": str(audio_path),
                        "status": "success",
                        "cqc_status": "not_reviewed",
                    }
                ],
                "timeline_clips": [
                    {
                        "clip_id": "clip_candidate_task_1",
                        "candidate_id": "candidate_task_1",
                        "task_id": "task_1",
                        "result_id": "result_1",
                        "track_id": "dub",
                        "target_subtitle_ids": ["localized_1"],
                        "start_ms": 0,
                        "end_ms": 900,
                        "audio_path": str(audio_path),
                        "status": "ready",
                        "cqc_status": "not_reviewed",
                    }
                ],
            }
        ),
        intent="runtime",
    )
    task = GenerationTask(
        task_id="task_1",
        generation_id="task_1",
        engine_id="omnivoice",
        project_id=project_id,
        segment_id=group["group_id"],
        localized_subtitle_id="localized_1",
        cue_id="cue_1",
        bind_to_video_localization=True,
        input_text="第一句。",
        status=TaskStatus.success,
        result_id="result_1",
        parameters={
            "source": "video_localization",
            "video_localization_target_subtitle_ids": ["localized_1"],
            "video_localization_dubbing_plan_revision": plan[
                "plan_revision"
            ],
            "video_localization_dubbing_group_id": group["group_id"],
            "ref_text": "source reference",
            "text": "第一句。",
        },
    )
    history = HistoryItem(
        result_id="result_1",
        task_id="task_1",
        engine_id="omnivoice",
        project_id=project_id,
        segment_id=group["group_id"],
        localized_subtitle_id="localized_1",
        cue_id="cue_1",
        bind_to_video_localization=True,
        input_text="第一句。",
        output_audio_id="audio_1",
        output_path=str(audio_path),
        duration_ms=1_000,
    )
    verification = TTSVerificationResponse(
        status="passed",
        coverage=1,
        similarity=1,
        expected_text="第一句",
        transcript_text="第一句",
        normalized_expected="第一句",
        normalized_transcript="第一句",
    )

    report = dubbing_production.sync_generated_candidate_automatic_cqc(
        task,
        history,
        verification,
    )

    assert report is not None
    assert report.automatic_status == "passed"
    assert report.subjective_status == "passed"
    assert report.overall_status == "passed"
    assert report.recommended_action == "accept"
    evidence_response = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/candidates/candidate_task_1/cqc-input"
    )
    assert evidence_response.status_code == 200
    frozen_input = evidence_response.json()
    assert frozen_input["candidate_id"] == "candidate_task_1"
    assert frozen_input["expected_spoken_text"] == "第一句。"
    assert frozen_input["subjective_reviews"] == []

    failed_review_response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/candidates/cqc",
        json={
            "schema_version": "dubbing-candidate-review-command-v1",
            "source_revision": frozen_input["source_revision"],
            "candidate_id": frozen_input["candidate_id"],
            "subjective_reviews": [
                {
                    "dimension": dimension,
                    "status": "failed" if dimension == "prosody_parse" else "passed",
                    "note": "断句仍需时间线修整" if dimension == "prosody_parse" else "已复核",
                }
                for dimension in (
                    "meaning",
                    "pronunciation",
                    "prosody_parse",
                    "voice_match",
                    "naturalness",
                )
            ],
        },
    )
    assert failed_review_response.status_code == 200
    assert failed_review_response.json()["overall_status"] == "passed"

    # A playable placement is not completion by itself. The shared finisher
    # must still persist the per-gap semantic decision and current edit gate.
    repair_placement_response = client.post(
        f"/api/projects/{project_id}/video-localization/candidates/candidate_task_1/apply"
    )
    assert repair_placement_response.status_code == 200
    repair_run = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/production-run"
    ).json()
    assert repair_run["groups"][0]["stage"] == "needs_timeline_work"
    assert repair_run["groups"][0]["recommended_action"] == "place_candidate"

    placed = draft_store.get(project_id)
    assert placed is not None
    edited_clips = []
    for raw in placed.timeline_clips:
        clip = dict(raw)
        if clip.get("candidate_id") == "candidate_task_1":
            clip.update(
                {
                    "start_ms": 0,
                    "end_ms": 800,
                    "source_start_ms": 0,
                    "source_end_ms": 800,
                }
            )
        edited_clips.append(clip)
    draft_store.save(
        project_id,
        placed.model_copy(update={"timeline_clips": edited_clips}),
        intent="content",
    )

    reviewed_response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/candidates/cqc",
        json={
            "schema_version": "dubbing-candidate-review-command-v1",
            "source_revision": frozen_input["source_revision"],
            "candidate_id": frozen_input["candidate_id"],
            "subjective_reviews": [
                {
                    "dimension": dimension,
                    "status": "passed",
                    "note": "已实听",
                }
                for dimension in (
                    "meaning",
                    "pronunciation",
                    "prosody_parse",
                    "voice_match",
                    "naturalness",
                )
            ],
        },
    )
    assert reviewed_response.status_code == 200
    assert reviewed_response.json()["overall_status"] == "passed"
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.dubbing_production.candidate_reports[0].candidate_id == (
        "candidate_task_1"
    )
    assert saved.dubbing_production.candidate_inputs[0].subjective_reviews == []
    assert saved.dubbing_production.candidate_inputs[0].placement_start_ms == 0
    assert saved.dubbing_production.candidate_inputs[0].placement_end_ms == 800
    assert saved.generated_candidates[0]["cqc_status"] == "passed"
    assert saved.timeline_clips[0]["cqc_status"] == "passed"
    apply_response = client.post(
        f"/api/projects/{project_id}/video-localization/candidates/candidate_task_1/apply"
    )
    assert apply_response.status_code == 200

    current = draft_store.get(project_id)
    assert current is not None
    current = current.model_copy(
        update={
            "source_media": current.source_media.model_copy(
                update={"duration_ms": 1_000}
            )
        }
    )
    frozen_subtitle_input = dub_subtitles.freeze_workflow_input(current)
    source_clip = frozen_subtitle_input.prepare_track.clips[0]
    saved = draft_store.save(
        project_id,
        current.model_copy(
            update={
                "dub_subtitles": [
                    VideoLocalizationDubSubtitleCue(
                        subtitle_id="dub_1",
                        start_ms=0,
                        end_ms=900,
                        text="第一句",
                        source_clip_ids=[source_clip.clip_id],
                        dub_lanes=[source_clip.dub_lane],
                        source_audio_sha256=source_clip.audio_sha256,
                    )
                ],
                "dub_subtitle_source_revision": (
                    frozen_subtitle_input.source_revision
                ),
            }
        ),
        intent="runtime",
    )
    assert saved is not None
    export_response = client.get(
        f"/api/projects/{project_id}/video-localization/subtitles/zh",
        params={"localized_variant": "dub"},
    )
    assert export_response.status_code == 200, export_response.text

    original_export_subtitles = exporting.export_subtitles
    export_calls = 0

    def replace_audio_after_render(*args, **kwargs):
        nonlocal export_calls
        result = original_export_subtitles(*args, **kwargs)
        export_calls += 1
        if export_calls == 1:
            sf.write(
                audio_path,
                np.full(sample_rate, 0.15, dtype=np.float32),
                sample_rate,
            )
        return result

    monkeypatch.setattr(
        exporting,
        "export_subtitles",
        replace_audio_after_render,
    )
    changed_during_export = client.get(
        f"/api/projects/{project_id}/video-localization/subtitles/zh",
        params={"localized_variant": "dub"},
    )
    assert changed_during_export.status_code == 200
    monkeypatch.setattr(
        exporting,
        "export_subtitles",
        original_export_subtitles,
    )

    sf.write(
        audio_path,
        0.2
        * np.sin(2 * np.pi * 440 * np.arange(sample_rate) / sample_rate),
        sample_rate,
    )
    replaced_response = client.get(
        f"/api/projects/{project_id}/video-localization/subtitles/zh",
        params={"localized_variant": "dub"},
    )
    assert replaced_response.status_code == 200
    assert "第一句" in replaced_response.text


def test_worker_from_previous_plan_cannot_repopulate_cqc_after_replan(
    tmp_path: Path,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    first = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    before_replan = draft_store.get(project_id)
    assert before_replan is not None
    draft_store.save(
        project_id,
        before_replan.model_copy(
            update={
                "tts_tasks": [
                    VideoLocalizationTtsTask(
                        workflow_id="old_workflow",
                        project_id=project_id,
                        segment_id=first["groups"][0]["group_id"],
                        subtitle_summary="第一句",
                        text="第一句",
                        source_cue_ids=["cue_1"],
                        start_ms=0,
                        end_ms=900,
                        status="queued",
                        generation_task_id="old_task",
                        stages=[
                            VideoLocalizationTtsTaskStage(
                                kind="generation",
                                status="queued",
                                parameters={
                                    "video_localization_dubbing_plan_revision": first[
                                        "plan_revision"
                                    ],
                                    "video_localization_dubbing_group_id": first[
                                        "groups"
                                    ][0]["group_id"],
                                },
                            ),
                            VideoLocalizationTtsTaskStage(kind="placement"),
                        ],
                    )
                ],
                "ui_state": {
                    **before_replan.ui_state,
                    "latest_tts_task_by_segment": {
                        "cue_1": "old_task"
                    },
                },
            }
        ),
        intent="runtime",
    )
    second = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    assert second["plan_revision"] == first["plan_revision"] + 1

    task = GenerationTask(
        task_id="old_task",
        generation_id="old_task",
        engine_id="omnivoice",
        project_id=project_id,
        segment_id=first["groups"][0]["group_id"],
        localized_subtitle_id="localized_1",
        cue_id="cue_1",
        bind_to_video_localization=True,
        input_text="第一句",
        status=TaskStatus.success,
        result_id="old_result",
        parameters={
            "source": "video_localization",
            "video_localization_target_subtitle_ids": ["localized_1"],
            "video_localization_dubbing_plan_revision": first[
                "plan_revision"
            ],
            "video_localization_dubbing_group_id": first["groups"][0][
                "group_id"
            ],
        },
    )
    history = HistoryItem(
        result_id="old_result",
        task_id="old_task",
        engine_id="omnivoice",
        project_id=project_id,
        segment_id=first["groups"][0]["group_id"],
        localized_subtitle_id="localized_1",
        cue_id="cue_1",
        bind_to_video_localization=True,
        input_text="第一句",
        output_path=str(tmp_path / "old-result.wav"),
        generation_id="old_task",
    )

    sf.write(
        history.output_path,
        np.zeros(16_000, dtype=np.float32),
        16_000,
    )

    with pytest.raises(AppException) as discarded:
        video_localization_tts_handoff.place_generated_result(
            task,
            history,
        )
    assert discarded.value.code == "VIDEO_LOCALIZATION_TTS_RESULT_DISCARDED"

    assert dubbing_production.sync_generated_candidate_automatic_cqc(
        task,
        history,
        None,
    ) is None
    saved = draft_store.get(project_id)
    assert saved is not None
    assert saved.dubbing_production.candidate_inputs == []
    assert saved.dubbing_production.candidate_reports == []
    assert not any(
        clip.get("task_id") == "old_task"
        for clip in saved.timeline_clips
    )
    old_workflow = next(
        item
        for item in saved.tts_tasks
        if item.workflow_id == "old_workflow"
    )
    assert old_workflow.status == "cancelled"
    assert "old_task" in saved.ui_state["discarded_tts_task_ids"]


@pytest.mark.parametrize("renumbered_group", [False, True])
@pytest.mark.parametrize("legacy_plan_metadata_missing", [False, True])
def test_explicit_history_resync_rebinds_equivalent_candidate_after_timing_refresh(
    tmp_path: Path,
    monkeypatch,
    renumbered_group: bool,
    legacy_plan_metadata_missing: bool,
):
    client, project_id = _client(tmp_path)
    source_revision = _current_revision(client, project_id)
    first = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(source_revision),
    ).json()
    second = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan/refresh-timing"
    ).json()
    group = second["groups"][0]
    historical_group_id = (
        "dubbing_group_0099"
        if renumbered_group
        else group["group_id"]
    )
    audio_path = tmp_path / "equivalent-result.wav"
    sf.write(
        audio_path,
        0.2 * np.sin(2 * np.pi * 220 * np.arange(16_000) / 16_000),
        16_000,
    )
    task = GenerationTask(
        task_id="equivalent_task",
        generation_id="equivalent_task",
        engine_id="omnivoice",
        project_id=project_id,
        segment_id=group["group_id"],
        localized_subtitle_id="localized_1",
        cue_id="cue_1",
        bind_to_video_localization=True,
        input_text="第一句",
        status=TaskStatus.success,
        result_id="equivalent_result",
        parameters={
            "source": "video_localization",
            "text": "第一句",
            "video_localization_target_subtitle_ids": ["localized_1"],
            **(
                {}
                if legacy_plan_metadata_missing
                else {
                    "video_localization_dubbing_plan_revision": first[
                        "plan_revision"
                    ],
                    "video_localization_dubbing_group_id": historical_group_id,
                }
            ),
        },
    )
    history = HistoryItem(
        result_id="equivalent_result",
        task_id="equivalent_task",
        engine_id="omnivoice",
        project_id=project_id,
        segment_id=historical_group_id,
        localized_subtitle_id="localized_1",
        cue_id="cue_1",
        bind_to_video_localization=True,
        input_text="第一句",
        output_path=str(audio_path),
        generation_id="equivalent_task",
    )
    from app.services import history_store

    # A durable result can be rebound without raw-content approval.
    history_store.add(history)
    observed_calls = []

    def transcribe(**kwargs):
        observed_calls.append(kwargs)
        return {"text": "第一句", "incomplete_chunk_ranges": []}

    monkeypatch.setattr("app.services.asr_service.transcribe", transcribe)
    aligned_calls = []
    def align(**kwargs):
        aligned_calls.append(kwargs)
        return []
    monkeypatch.setattr("app.services.qwen_forced_aligner.align_audio", align)

    assert dubbing_production.sync_generated_candidate_automatic_cqc(
        task,
        history,
        None,
    ) is None
    rebound = dubbing_production.sync_generated_candidate_automatic_cqc(
        task,
        history,
        None,
        allow_equivalent_plan_rebind=True,
    )

    assert rebound is not None
    assert rebound.plan_revision == second["plan_revision"]
    assert observed_calls == []
    assert len(aligned_calls) == 1
    assert aligned_calls[0]["audio_path"] == str(audio_path)
    saved_history = history_store.get(history.result_id)
    assert saved_history is not None
    assert saved_history.content_evidence is None


@pytest.mark.parametrize("report_state", ["raw_content_rejected", "absent"])
@pytest.mark.parametrize("has_alignment", [True, False])
def test_service_split_uses_media_evidence_not_raw_content_approval(
    tmp_path: Path, report_state: str, has_alignment: bool,
):
    from app.domains.video_localization import dubbing_production as domain
    from app.schemas.video_localization_dubbing_production import (
        DubbingQualityFinding, DubbingTimelineSplitRequest,
    )

    client, project_id = _client(tmp_path)
    revision = _current_revision(client, project_id)
    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json=_plan_payload(revision),
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    group_id = plan["groups"][0]["group_id"]
    audio_path = tmp_path / "split-material.wav"
    sf.write(audio_path, np.zeros(12800, dtype=np.float32), 16000)
    managed_path = media_assets.adopt_tts_audio(
        project_id, audio_path, "localized_1", "split-material-task",
    )
    audio_sha = media_assets.file_sha256(managed_path)
    frozen_data = _automatic_candidate_cqc_input(
        revision, plan_revision=plan["plan_revision"],
    ).model_dump()
    frozen_data.update(group_id=group_id, audio_sha256=audio_sha)
    frozen_data["audio"]["aligned_words"] = [
        {"word_id": "w1", "text": "第", "start_ms": 80, "end_ms": 200},
        {"word_id": "w2", "text": "一句", "start_ms": 500, "end_ms": 700},
    ] if has_alignment else []
    frozen = DubbingCandidateCqcInput.model_validate(frozen_data)
    report = domain.build_candidate_gap_processing_report(frozen).model_copy(update={
        "overall_status": "failed",
        "automatic_status": "failed",
        "findings": [DubbingQualityFinding(
            code="REFERENCE_AUDIO_CONTAMINATION_SUSPECTED", severity="blocking",
            message="旧整文件转写包含多余英文", entity_ids=["candidate_1"],
        )],
    })
    clip = {
        "clip_id": "split-material", "track_id": "dub", "candidate_id": "candidate_1",
        "result_id": "candidate_1", "audio_path": str(managed_path),
        "start_ms": 0, "end_ms": 800, "source_start_ms": 0, "source_end_ms": 800,
        "target_subtitle_ids": ["localized_1"], "source_cue_ids": ["cue_1"],
        "dubbing_group_id": group_id,
    }

    def seed(current):
        return current.model_copy(update={
            "timeline_clips": [clip],
            "dubbing_production": current.dubbing_production.model_copy(update={
                "candidate_inputs": [frozen],
                "candidate_reports": [report] if report_state == "raw_content_rejected" else [],
            }),
        })

    draft = project_service.update_video_localization_atomic(project_id, seed, intent="content")
    request = DubbingTimelineSplitRequest.model_validate({
        "source_revision": revision, "plan_revision": plan["plan_revision"],
        "timeline_revision": domain.dubbing_timeline_projection_revision(draft),
        "commands": [{
            "clip_id": clip["clip_id"], "candidate_id": "candidate_1", "audio_sha256": audio_sha,
            "slices": [
                {"target_subtitle_ids": ["localized_1"], "source_start_ms": 0,
                 "source_end_ms": 300, "speech_start_ms": 80, "speech_end_ms": 200,
                 "alignment_word_ids": ["w1"]},
                {"target_subtitle_ids": ["localized_1"], "source_start_ms": 400,
                 "source_end_ms": 800, "speech_start_ms": 500, "speech_end_ms": 700,
                 "alignment_word_ids": ["w2"]},
            ],
        }],
    })
    if not has_alignment:
        with pytest.raises(AppException) as failure:
            dubbing_production.split_and_rebalance_current_timeline(project_id, request)
        assert failure.value.code == "VIDEO_LOCALIZATION_DUBBING_SPLIT_ALIGNMENT_INVALID"
        assert draft_store.get(project_id).timeline_clips == draft.timeline_clips
        return

    result = dubbing_production.split_and_rebalance_current_timeline(project_id, request)
    assert len(result.timeline_clips) == 2
    assert [(item["source_start_ms"], item["source_end_ms"]) for item in result.timeline_clips] == [
        (0, 300), (400, 800),
    ]
    assert draft_store.get(project_id).timeline_clips == result.timeline_clips


def test_group_preflight_public_api_is_read_only(tmp_path):
    client, project_id = _client(tmp_path)
    prefix = f'/api/projects/{project_id}/video-localization'
    response = client.post(prefix + '/dubbing/plan', json=_plan_payload(_current_revision(client, project_id)))
    assert response.status_code == 200, response.text
    group_id = response.json()['groups'][0]['group_id']
    before = client.get(prefix).json()
    response = client.get(prefix + f'/dubbing/groups/{group_id}/preflight?speed=1.25')
    assert response.status_code == 200, response.text
    assert response.json()['estimated_speech_duration_ms'] is None
    assert response.json()['status'] == 'warning'
    assert client.get(prefix).json() == before
