from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from app.domains.video_localization import (  # noqa: E402
    operation_state,
    operation_queue,
    semantic_tts_grouping_execution,
    service,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationOperation,
)
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, llm_runtime, settings_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_execution as operation_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)


def _client(tmp_path: Path) -> TestClient:
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
    return TestClient(app)


def _profile(*, remote: bool = False) -> llm_runtime.ResolvedProfile:
    return llm_runtime.ResolvedProfile(
        profile_id="semantic-test",
        protocol="openai_compatible",
        base_url=(
            "https://llm.example.com/v1"
            if remote
            else "http://127.0.0.1:11434/v1"
        ),
        model_id="semantic-model",
        api_key="test-secret" if remote else None,
    )


def _create_project_with_localized_subtitles(
    client: TestClient,
) -> str:
    project_id = client.post(
        "/api/projects",
        json={"name": "语义分组持久执行", "description": ""},
    ).json()["project_id"]
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_1",
                    "speaker_id": "speaker_a",
                    "start_ms": 0,
                    "end_ms": 1000,
                },
                {
                    "cue_id": "cue_2",
                    "speaker_id": "speaker_a",
                    "start_ms": 1000,
                    "end_ms": 2000,
                },
                {
                    "cue_id": "cue_3",
                    "speaker_id": "speaker_b",
                    "start_ms": 2000,
                    "end_ms": 3000,
                },
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "先介绍事情背景。",
                    "source_cue_ids": ["cue_1"],
                },
                {
                    "subtitle_id": "localized_2",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "text": "接着说明具体做法。",
                    "source_cue_ids": ["cue_2"],
                },
                {
                    "subtitle_id": "localized_3",
                    "start_ms": 2000,
                    "end_ms": 3000,
                    "text": "另一个人作出回应。",
                    "source_cue_ids": ["cue_3"],
                },
            ],
        },
    )
    assert response.status_code == 200
    return project_id


def _successful_completion(
    calls: list[dict],
    profile: llm_runtime.ResolvedProfile,
):
    def complete_json(**kwargs):
        calls.append(kwargs)
        kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id=profile.profile_id,
                model_id=profile.model_id,
                provider_host=(
                    "llm.example.com"
                    if profile.base_url.startswith("https://")
                    else "127.0.0.1"
                ),
                request_chars=100,
                request_body_bytes=120,
                max_tokens=1200,
                timeout_seconds=180,
                reasoning_effort_requested=None,
                reasoning_control_applied=True,
                duration_ms=25,
                finish_reason="stop",
                prompt_tokens=80,
                completion_tokens=20,
                total_tokens=100,
                content_chars=50,
                response_id=f"response-{len(calls)}",
            )
        )
        return {
            "groups": [
                ["localized_1", "localized_2"],
                ["localized_3"],
            ]
        }

    return complete_json


def _submit(
    client: TestClient,
    project_id: str,
) -> dict:
    response = client.post(
        (
            f"/api/projects/{project_id}/video-localization/"
            "operations"
        ),
        json={
            "kind": "semantic_tts_grouping",
            "parameters": {
                "profile_id": "semantic-test",
                "target_chars": 60,
                "max_chars": 80,
            },
        },
    )
    assert response.status_code == 200
    return response.json()


def _mark_running(project_id: str, operation_id: str) -> None:
    operation_queue._mark_operation(
        project_id,
        operation_id,
        kind="semantic_tts_grouping",
        status="running",
        progress=0.35,
        started_at="2026-07-31T08:00:00+00:00",
        result_summary={
            "stage": "判断语义和场景",
            "stage_id": "group",
            **operation_queue
            ._semantic_tts_grouping_workflow_summary_fields(),
        },
    )


def _crashed_claim(project_id: str, operation_id: str):
    decision = operation_execution.acquire_execution_claim(
        project_id,
        operation_id,
        runner_id="crashed-worker",
    )
    assert decision.acquired
    assert decision.claim is not None
    return decision.claim


def _expire_claim(attempt_id: str) -> None:
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_attempts
            SET lease_expires_at_ms = 0
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        )


def test_semantic_grouping_api_operation_pins_profile_and_commits(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_project_with_localized_subtitles(client)
    profile = _profile()
    provider_calls: list[dict] = []
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: profile,
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        _successful_completion(provider_calls, profile),
    )

    submitted = _submit(client, project_id)
    operation_queue._process(
        project_id,
        submitted["operation_id"],
    )

    completed = operation_queue.get_operation(
        project_id,
        submitted["operation_id"],
    )
    assert completed is not None
    assert completed.status == "success"
    assert completed.parameters["workflow_id"] == (
        "semantic-tts-grouping"
    )
    assert len(
        completed.parameters[
            "profile_configuration_fingerprint"
        ]
    ) == 64
    assert completed.result_summary["workflow_schema_version"] == (
        "semantic-tts-grouping-workflow-v2"
    )
    assert completed.result_summary["workflow_id"] == (
        "semantic-tts-grouping"
    )
    assert completed.result_summary["semantic_group_count"] == 2
    assert len(provider_calls) == 1
    assert provider_calls[0]["idempotency_key"].startswith(
        "vsl_semgrp_"
    )
    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    groups = draft["localization_state"][
        "semantic_tts_grouping"
    ]["groups"]
    assert [item["subtitle_ids"] for item in groups] == [
        ["localized_1", "localized_2"],
        ["localized_3"],
    ]
    ledger = ledger_store.get_operation(
        project_id,
        submitted["operation_id"],
    )
    assert ledger is not None
    assert ledger.workflow_version == (
        "semantic-tts-grouping-workflow-v2"
    )
    steps = step_store.list_step_attempts(
        project_id,
        submitted["operation_id"],
    )
    by_step = {step.step_id: step for step in steps}
    assert set(by_step) == {
        "prepare",
        "group",
        "semantic_grouping_round_1",
        "validate",
        "write",
    }
    assert all(step.status == "success" for step in steps)
    assert by_step["semantic_grouping_round_1"].cost_class == (
        "external_free"
    )
    assert all(
        by_step[step_id].cost_class == "local_free"
        for step_id in {"prepare", "group", "validate", "write"}
    )
    detail_response = client.get(
        f"/api/projects/{project_id}/video-localization/"
        f"operations/{submitted['operation_id']}"
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["result_summary"]["semantic_group_count"] == 2
    assert {
        step_id: result["status"]
        for step_id, result in detail["result_summary"][
            "task_step_results"
        ].items()
    } == {
        "prepare": "success",
        "group": "success",
        "validate": "success",
        "write": "success",
    }


def test_recovery_reuses_success_after_crash_before_draft_commit(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_project_with_localized_subtitles(client)
    profile = _profile()
    provider_calls: list[dict] = []
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: profile,
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        _successful_completion(provider_calls, profile),
    )
    submitted = _submit(client, project_id)
    operation_id = submitted["operation_id"]
    _mark_running(project_id, operation_id)
    claim = _crashed_claim(project_id, operation_id)
    draft = operation_queue.service.get_video_localization(project_id)
    assert draft is not None

    semantic_tts_grouping_execution.execute_semantic_tts_grouping(
        claim.execution_fence,
        draft,
        profile_id=profile.profile_id,
        expected_profile_configuration_fingerprint=(
            submitted["parameters"][
                "profile_configuration_fingerprint"
            ]
        ),
        target_chars=60,
        max_chars=80,
    )
    assert "semantic_tts_grouping" not in draft.localization_state
    _expire_claim(claim.attempt.attempt_id)

    operation_queue._recover_project_operations(project_id)

    completed = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert completed is not None and completed.status == "success"
    assert len(provider_calls) == 1
    assert len(
        attempt_store.list_attempts(project_id, operation_id)
    ) == 2
    persisted = operation_queue.service.get_video_localization(
        project_id
    )
    assert persisted is not None
    assert (
        len(
            persisted.localization_state[
                "semantic_tts_grouping"
            ]["groups"]
        )
        == 2
    )


def test_profile_change_fails_before_provider_and_retry_repins(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_project_with_localized_subtitles(client)
    original_profile = _profile()
    changed_profile = llm_runtime.ResolvedProfile(
        **{
            **original_profile.__dict__,
            "model_id": "semantic-model-v2",
        }
    )
    current_profile = original_profile
    provider_calls = 0
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: current_profile,
    )
    submitted = _submit(client, project_id)
    original_fingerprint = submitted["parameters"][
        "profile_configuration_fingerprint"
    ]
    current_profile = changed_profile

    def unexpected_provider(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        pytest.fail("changed profile must fail before Provider")

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        unexpected_provider,
    )
    operation_queue._process(
        project_id,
        submitted["operation_id"],
    )

    failed = operation_queue.get_operation(
        project_id,
        submitted["operation_id"],
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_CHANGED"
    )
    assert provider_calls == 0
    failed_steps = step_store.list_step_attempts(
        project_id,
        submitted["operation_id"],
    )
    assert len(failed_steps) == 1
    assert failed_steps[0].step_id == "prepare"
    assert failed_steps[0].cost_class == "local_free"
    assert failed_steps[0].status == "failed"
    assert failed_steps[0].error_code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_CHANGED"
    )
    failed_detail = client.get(
        f"/api/projects/{project_id}/video-localization/"
        f"operations/{submitted['operation_id']}"
    )
    assert failed_detail.status_code == 200
    assert failed_detail.json()["error_code"] == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_CHANGED"
    )

    retried = operation_queue.retry(
        project_id,
        submitted["operation_id"],
    )
    assert retried is not None
    assert retried.operation_id != submitted["operation_id"]
    assert (
        retried.parameters[
            "profile_configuration_fingerprint"
        ]
        != original_fingerprint
    )
    assert retried.parameters[
        "profile_configuration_fingerprint"
    ] == (
        semantic_tts_grouping_execution
        .provider_configuration_fingerprint(changed_profile)
    )


def test_recovery_blocks_unknown_paid_result_without_replay(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_project_with_localized_subtitles(client)
    profile = _profile(remote=True)
    provider_calls = 0
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: profile,
    )

    def timeout(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise llm_runtime.LlmRuntimeError(
            "private Provider response",
            code="llm_timeout",
            status_code=504,
        )

    monkeypatch.setattr(llm_runtime, "complete_json", timeout)
    submitted = _submit(client, project_id)
    operation_id = submitted["operation_id"]
    _mark_running(project_id, operation_id)
    claim = _crashed_claim(project_id, operation_id)
    draft = operation_queue.service.get_video_localization(project_id)
    assert draft is not None
    with pytest.raises(AppException):
        semantic_tts_grouping_execution.execute_semantic_tts_grouping(
            claim.execution_fence,
            draft,
            profile_id=profile.profile_id,
            expected_profile_configuration_fingerprint=(
                submitted["parameters"][
                    "profile_configuration_fingerprint"
                ]
            ),
            target_chars=60,
            max_chars=80,
        )
    _expire_claim(claim.attempt.attempt_id)

    operation_queue._recover_project_operations(project_id)

    completed = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert completed is not None
    assert completed.status == "failed"
    assert completed.error_code == (
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_RESULT_UNKNOWN"
    )
    assert "private Provider response" not in (
        completed.error_message or ""
    )
    assert provider_calls == 1
    all_steps = step_store.list_step_attempts(
        project_id,
        operation_id,
    )
    assert {
        step.step_id: step.status
        for step in all_steps
        if step.step_id == "prepare"
    } == {"prepare": "success"}
    assert [
        step.status
        for step in all_steps
        if step.step_id.startswith("semantic_grouping_round_")
    ] == ["result_unknown"]


def test_recovery_replays_unknown_local_provider_once(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_project_with_localized_subtitles(client)
    profile = _profile()
    provider_calls = 0
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: profile,
    )

    def first_timeout(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise llm_runtime.LlmRuntimeError(
            "local timeout",
            code="llm_timeout",
            status_code=504,
        )

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        first_timeout,
    )
    submitted = _submit(client, project_id)
    operation_id = submitted["operation_id"]
    _mark_running(project_id, operation_id)
    claim = _crashed_claim(project_id, operation_id)
    draft = operation_queue.service.get_video_localization(project_id)
    assert draft is not None
    with pytest.raises(AppException):
        semantic_tts_grouping_execution.execute_semantic_tts_grouping(
            claim.execution_fence,
            draft,
            profile_id=profile.profile_id,
            expected_profile_configuration_fingerprint=(
                submitted["parameters"][
                    "profile_configuration_fingerprint"
                ]
            ),
            target_chars=60,
            max_chars=80,
        )
    _expire_claim(claim.attempt.attempt_id)
    replay_calls: list[dict] = []
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        _successful_completion(replay_calls, profile),
    )

    operation_queue._recover_project_operations(project_id)

    completed = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert completed is not None and completed.status == "success"
    assert provider_calls == 1
    assert len(replay_calls) == 1
    all_steps = step_store.list_step_attempts(
        project_id,
        operation_id,
    )
    assert [
        step.status
        for step in all_steps
        if step.step_id.startswith("semantic_grouping_round_")
    ] == ["result_unknown", "success"]
    assert {
        step.step_id
        for step in all_steps
        if step.cost_class == "local_free"
    } == {"prepare", "group", "validate", "write"}


def test_legacy_semantic_operation_is_interrupted_not_resumed(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = _create_project_with_localized_subtitles(client)
    legacy_operation = VideoLocalizationOperation(
        project_id=project_id,
        kind="semantic_tts_grouping",
        status="running",
        progress=0.35,
        started_at="2026-07-31T08:00:00+00:00",
        parameters={
            "profile_id": "legacy-profile",
            "target_chars": 60,
            "max_chars": 80,
        },
        result_summary={
            "stage": "旧版语义分组",
            "workflow_schema_version": "operation-v1",
        },
    )
    operation_id = legacy_operation.operation_id
    service.update_video_localization_atomic(
        project_id,
        lambda draft: operation_state.with_operation(
            draft,
            legacy_operation,
        ),
        intent="runtime",
    )
    claim = _crashed_claim(project_id, operation_id)
    _expire_claim(claim.attempt.attempt_id)
    provider_calls = 0

    def unexpected_provider(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        pytest.fail("legacy operation must not be resumed")

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        unexpected_provider,
    )

    operation_queue._recover_project_operations(project_id)

    completed = operation_queue.get_operation(
        project_id,
        operation_id,
    )
    assert completed is not None
    assert completed.status == "failed"
    assert completed.error_code == (
        "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED"
    )
    assert provider_calls == 0
