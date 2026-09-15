from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


from app.domains.video_localization import (  # noqa: E402
    document_understanding_contracts,
)
from app.domains.video_localization import (  # noqa: E402
    asr_document_understanding_execution as execution,
)
from app.domains.video_localization import (  # noqa: E402
    asr_document_understanding_managed_contracts as managed_contracts,
)
from app.domains.video_localization import (  # noqa: E402
    asr_document_understanding_result_reader as result_reader,
)
from app.domains.video_localization import (  # noqa: E402
    managed_local_step,
)
from app.domains.video_localization.operation_detail_errors import (  # noqa: E402
    OperationDetailRepairRequired,
)
from app.errors import AppException  # noqa: E402
from app.services import database, llm_runtime  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (  # noqa: E402
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (  # noqa: E402
    video_localization_operation_step_store as step_store,
)
from test_video_localization_document_understanding_provider_gateway import (  # noqa: E402
    T0,
    FakeArtifactBackend,
    _claim,
    _profile,
    _trace,
)


@pytest.fixture(autouse=True)
def _managed_clock(monkeypatch):
    monkeypatch.setattr(
        managed_local_step,
        "_now",
        lambda: T0 + timedelta(seconds=1),
    )


def _prepared_input(
    profile: llm_runtime.ResolvedProfile,
    *,
    scene_context: str = "locked context",
) -> managed_contracts.AsrDocumentUnderstandingPreparedInputV2:
    return managed_contracts.AsrDocumentUnderstandingPreparedInputV2(
            raw_artifact_fingerprint="a" * 64,
            diarization_artifact_fingerprint="b" * 64,
            join_artifact_fingerprint="c" * 64,
        profile_configuration_fingerprint=(provider_execution.provider_configuration_fingerprint(profile)),
            behavior_fingerprint="d" * 64,
        request=document_understanding_contracts.AsrDocumentUnderstandingInput(
                upstream_operation_id="initial-analysis-1",
                source_track_id="vocals",
                source_audio_sha256="e" * 64,
                language="en",
                scene_context=scene_context,
                profile_id=profile.profile_id,
                segments=[
                    {
                        "ordinal": 1,
                        "segment_id": "asr_0001",
                        "start_ms": 0,
                        "end_ms": 1_000,
                        "text": "Hello.",
                    }
                ],
            ),
        )


def _valid_brief() -> dict:
    return {
        "summary": "创作者讲解视频制作流程。",
        "logic": ["先介绍目标", "再说明步骤"],
        "speaker_style": "第一人称教程讲解。",
        "entities": [],
        "search_queries": [],
        "visual_questions": [],
        "sections": [
            {
                "id": "S1",
                "start_segment": 1,
                "end_segment": 1,
                "role": "完整教程",
                "focus": ["核对步骤"],
            }
        ],
    }


def _execute(
    prepared,
    *,
    fence,
    backend,
    profile,
    complete_json,
):
    return execution.execute_prepared_document_understanding(
        prepared,
        execution_fence=fence,
        resolved_profile=profile,
        file_backend=backend,
        is_cancelled=lambda: False,
        complete_json=complete_json,
        clock=lambda: T0 + timedelta(seconds=1),
    )


def test_execution_commits_input_calls_and_final_output_then_reuses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    monkeypatch.setattr(
        managed_local_step,
        "managed_artifact_files",
        backend,
    )
    profile = _profile()
    prepared = _prepared_input(profile)
    provider_calls: list[dict] = []

    def complete_json(**kwargs):
        provider_calls.append(kwargs)
        _trace(
            kwargs["trace_sink"],
            profile=profile,
            response_id="document-response-1",
        )
        return _valid_brief()

    first = _execute(
        prepared,
        fence=fence,
        backend=backend,
        profile=profile,
        complete_json=complete_json,
    )
    repeated = _execute(
        prepared,
        fence=fence,
        backend=backend,
        profile=profile,
        complete_json=complete_json,
    )

    assert len(provider_calls) == 1
    assert first.brief == repeated.brief
    assert first.stage_timing.duration_ms == 25
    assert repeated.stage_timing.duration_ms == 25
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert [step.status for step in steps] == [
        "success",
        "success",
        "success",
    ]
    assert {step.step_id for step in steps} == {
        "prepare_document_input",
        ("document_call_understand_document_full_document_attempt_1"),
        "finalize_document_understanding",
    }
    final_step = next(step for step in steps if step.step_id == "finalize_document_understanding")
    final_artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        final_step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert final_artifact is not None
    output = managed_contracts.parse_step_output(backend.files[("project-1", final_artifact.storage_key)])
    assert output.prepared_input_fingerprint == (managed_contracts.prepared_input_fingerprint(prepared))
    assert len(output.calls) == 1
    assert output.calls[0].artifact_fingerprint == (
        next(step.output_fingerprint for step in steps if step.step_id.startswith("document_call_"))
    )
    statements: list[str] = []
    with database.read_conn() as connection:
        connection.set_trace_callback(statements.append)
        rebuilt = result_reader.read_result_from_connection(
            connection,
            "project-1",
            "operation-1",
            file_backend=backend,
        )
    assert rebuilt is not None
    assert rebuilt.input.scene_context == "locked context"
    assert rebuilt.brief == first.brief
    assert rebuilt.stage_timing.duration_ms == 25
    assert rebuilt.raw_responses[0].stage == "full_document"
    assert not any(" from projects" in statement.casefold() for statement in statements)


def test_changed_locked_input_is_rejected_without_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    monkeypatch.setattr(
        managed_local_step,
        "managed_artifact_files",
        backend,
    )
    profile = _profile()
    calls = 0

    def complete_json(**kwargs):
        nonlocal calls
        calls += 1
        _trace(kwargs["trace_sink"], profile=profile)
        return _valid_brief()

    _execute(
        _prepared_input(profile),
        fence=fence,
        backend=backend,
        profile=profile,
        complete_json=complete_json,
    )
    with pytest.raises(AppException) as raised:
        _execute(
            _prepared_input(
                profile,
                scene_context="edited after submit",
            ),
            fence=fence,
            backend=backend,
            profile=profile,
            complete_json=complete_json,
        )

    assert raised.value.code == ("VIDEO_LOCALIZATION_ASR_DOCUMENT_UNDERSTANDING_PREPARE_INPUT_CHANGED")
    assert calls == 1


def test_final_normalization_failure_reuses_successful_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    monkeypatch.setattr(
        managed_local_step,
        "managed_artifact_files",
        backend,
    )
    profile = _profile()
    calls = 0

    def complete_json(**kwargs):
        nonlocal calls
        calls += 1
        _trace(
            kwargs["trace_sink"],
            profile=profile,
            response_id=f"invalid-brief-{calls}",
        )
        return {
            "summary": "",
            "logic": [],
            "speaker_style": "",
            "sections": [],
        }

    for _attempt in range(2):
        with pytest.raises(AppException) as raised:
            _execute(
                _prepared_input(profile),
                fence=fence,
                backend=backend,
                profile=profile,
                complete_json=complete_json,
            )
        assert raised.value.code == ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_PROVIDER_FAILED")

    assert calls == 2
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert len([step for step in steps if step.step_id.startswith("document_call_")]) == 2
    assert all(step.status == "success" for step in steps if step.step_id.startswith("document_call_"))
    assert not any(step.step_id == "finalize_document_understanding" for step in steps)


def test_reader_rejects_corrupt_call_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fence = _claim(tmp_path)
    backend = FakeArtifactBackend()
    monkeypatch.setattr(
        managed_local_step,
        "managed_artifact_files",
        backend,
    )
    profile = _profile()

    def complete_json(**kwargs):
        _trace(kwargs["trace_sink"], profile=profile)
        return _valid_brief()

    _execute(
        _prepared_input(profile),
        fence=fence,
        backend=backend,
        profile=profile,
        complete_json=complete_json,
    )
    provider_step = next(
        step
        for step in step_store.list_step_attempts(
            "project-1",
            "operation-1",
        )
        if step.step_id.startswith("document_call_")
    )
    artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        provider_step.step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    assert artifact is not None
    backend.files[("project-1", artifact.storage_key)] = b'{"corrupt":true}'

    with database.read_conn() as connection:
        with pytest.raises(OperationDetailRepairRequired) as raised:
            result_reader.read_result_from_connection(
                connection,
                "project-1",
                "operation-1",
                file_backend=backend,
            )

    assert raised.value.issue_codes == ("call_artifact_invalid",)
