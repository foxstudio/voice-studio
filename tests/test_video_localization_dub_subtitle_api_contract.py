from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api import video_localization as video_localization_api
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationOperationRequest,
)
from app.domains.video_localization import source_pipeline
from app.main import app


client = TestClient(app)


def _operation(project_id: str) -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        project_id=project_id,
        kind="dub_subtitle_generation",
        label="根据合成配音生成字幕",
    )


def test_generic_operation_request_rejects_dub_subtitle_workflow() -> None:
    with pytest.raises(ValidationError):
        VideoLocalizationOperationRequest(
            kind="dub_subtitle_generation",
            parameters={"engine_id": "qwen3-asr-mlx"},
        )


def test_dub_subtitle_request_validates_formal_and_development_modes() -> None:
    formal = (
        video_localization_api.VideoLocalizationDubSubtitleOperationRequest()
    )
    assert formal.model_dump() == {
        "engine_id": "qwen3-asr-mlx",
        "regeneration_mode": "auto",
        "execution_mode": "full",
        "development_target_step_id": None,
        "development_session_id": None,
    }

    development = (
        video_localization_api.VideoLocalizationDubSubtitleOperationRequest(
            execution_mode="development_target",
            development_target_step_id="transcribe_track",
            development_session_id="dub-debug-1",
        )
    )
    assert development.development_target_step_id == "transcribe_track"

    with pytest.raises(ValidationError):
        video_localization_api.VideoLocalizationDubSubtitleOperationRequest(
            execution_mode="full",
            development_target_step_id="prepare_track",
            development_session_id="dub-debug-1",
        )
    with pytest.raises(ValidationError):
        video_localization_api.VideoLocalizationDubSubtitleOperationRequest(
            execution_mode="development_target",
            development_target_step_id="prepare_track",
        )
    with pytest.raises(ValidationError):
        video_localization_api.VideoLocalizationDubSubtitleOperationRequest(
            execution_mode="development_target",
            development_target_step_id="removed_old_step",
            development_session_id="dub-debug-1",
        )
    with pytest.raises(ValidationError):
        video_localization_api.VideoLocalizationDubSubtitleOperationRequest(
            execution_mode="development_target",
            development_target_step_id="prepare_track",
            development_session_id="dub-debug-1",
            force_development_target=False,
        )


def test_source_asr_rejects_dub_track_and_keeps_the_dedicated_workflow() -> None:
    with pytest.raises(ValidationError):
        video_localization_api.VideoLocalizationAsrOperationRequest(
            source_track_id="dub",
        )

    with pytest.raises(Exception) as raised:
        source_pipeline.validate_english_asr_source(
            VideoLocalizationDraft(),
            "dub",
        )
    assert getattr(raised.value, "code", None) == (
        "VIDEO_LOCALIZATION_ASR_SOURCE_TRACK_UNSUPPORTED"
    )


def test_typed_dub_subtitle_endpoint_submits_only_validated_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str, dict]] = []

    def submit(project_id: str, kind: str, parameters: dict):
        captured.append((project_id, kind, parameters))
        return _operation(project_id)

    monkeypatch.setattr(
        video_localization_api.video_localization_operations,
        "submit_operation",
        submit,
    )

    response = client.post(
        "/api/projects/project-dub/video-localization/"
        "operations/dub-subtitles",
        json={
            "engine_id": "qwen3-asr-mlx",
            "execution_mode": "development_target",
            "development_target_step_id": "proofread_text",
            "development_session_id": "dub-debug-1",
        },
    )

    assert response.status_code == 200
    assert captured == [
        (
            "project-dub",
            "dub_subtitle_generation",
            {
                "engine_id": "qwen3-asr-mlx",
                "regeneration_mode": "auto",
                "execution_mode": "development_target",
                "development_target_step_id": "proofread_text",
                "development_session_id": "dub-debug-1",
            },
        )
    ]

    generic = client.post(
        "/api/projects/project-dub/video-localization/operations",
        json={
            "kind": "dub_subtitle_generation",
            "parameters": {"engine_id": "qwen3-asr-mlx"},
        },
    )
    assert generic.status_code == 400
    assert generic.json()["error"]["code"] == "INVALID_REQUEST"


def test_typed_dub_subtitle_endpoint_allows_an_explicit_full_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        video_localization_api.video_localization_operations,
        "submit_operation",
        lambda _project_id, _kind, parameters: (
            captured.append(parameters) or _operation("project-dub")
        ),
    )

    response = client.post(
        "/api/projects/project-dub/video-localization/operations/dub-subtitles",
        json={"regeneration_mode": "full"},
    )

    assert response.status_code == 200
    assert captured[0]["regeneration_mode"] == "full"


def test_dub_subtitle_workflow_definition_has_the_current_six_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        video_localization_api,
        "_project_exists",
        lambda _project_id: True,
    )

    response = client.get(
        "/api/projects/project-dub/video-localization/"
        "workflows/dub-subtitles"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "dub-subtitle-generation"
    assert [
        task["id"]
        for stage in payload["stages"]
        for task in stage["atomic_tasks"]
    ] == [
        "prepare_track",
        "transcribe_track",
        "proofread_text",
        "align_words",
        "segment_subtitles",
        "commit",
    ]


def test_openapi_exposes_only_the_typed_dub_subtitle_entrypoint() -> None:
    app.openapi_schema = None
    schema = app.openapi()
    typed = schema["paths"][
        "/api/projects/{project_id}/video-localization/"
        "operations/dub-subtitles"
    ]["post"]
    workflow = schema["paths"][
        "/api/projects/{project_id}/video-localization/"
        "workflows/dub-subtitles"
    ]["get"]

    assert typed["summary"] == "启动合成配音字幕工作流"
    request_schema = typed["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert request_schema["$ref"].endswith(
        "/VideoLocalizationDubSubtitleOperationRequest"
    )
    request_properties = schema["components"]["schemas"][
        "VideoLocalizationDubSubtitleOperationRequest"
    ]["properties"]
    assert "force_development_target" not in request_properties
    assert request_properties["regeneration_mode"]["default"] == "auto"
    assert "执行必要依赖到目标并停止" in (
        request_properties["execution_mode"]["description"]
    )
    assert "执行必要依赖到指定原子任务并停止" in typed["description"]
    generic_kind = schema["components"]["schemas"][
        "VideoLocalizationOperationRequest"
    ]["properties"]["kind"]["enum"]
    assert "dub_subtitle_generation" not in generic_kind
    assert workflow["summary"] == "查看合成配音字幕工作流结构"


def test_typed_dub_subtitle_review_endpoint_uses_the_domain_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str, list[dict]]] = []

    def review(project_id: str, *, source_revision: str, cues: list[dict]):
        captured.append((project_id, source_revision, cues))
        return VideoLocalizationDraft()

    monkeypatch.setattr(
        video_localization_api.video_localization_service,
        "review_dub_subtitles",
        review,
    )

    response = client.post(
        "/api/projects/project-dub/video-localization/dub-subtitles/review",
        json={
            "source_revision": "a" * 64,
            "cues": [
                {
                    "subtitle_id": "dub-1",
                    "source_subtitle_ids": ["dub-1", "dub-2"],
                    "start_ms": 100,
                    "end_ms": 1_400,
                    "text": "嚯，做得真不错！",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert captured == [
        (
            "project-dub",
            "a" * 64,
            [
                {
                    "subtitle_id": "dub-1",
                    "source_subtitle_ids": ["dub-1", "dub-2"],
                    "start_ms": 100,
                    "end_ms": 1_400,
                    "text": "嚯，做得真不错！",
                }
            ],
        )
    ]


def test_openapi_exposes_typed_dub_subtitle_review_command() -> None:
    app.openapi_schema = None
    schema = app.openapi()
    review = schema["paths"][
        "/api/projects/{project_id}/video-localization/"
        "dub-subtitles/review"
    ]["post"]

    assert review["summary"] == "提交合成配音字幕复审结果"
    request_schema = review["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert request_schema["$ref"].endswith(
        "/VideoLocalizationDubSubtitleReviewRequest"
    )
