from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api import video_localization as video_localization_api  # noqa: E402
from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization import service  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationSubtitleCue,
)
from app.errors import AppException  # noqa: E402


PROJECT_ID = "project_concurrency"


@pytest.fixture(autouse=True)
def clear_operation_runtime_state():
    operation_queue._runtime.reset()
    yield
    operation_queue._runtime.reset()


def _operation(status: str = "running") -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        operation_id="localization_operation",
        project_id=PROJECT_ID,
        kind="localization_draft",
        status=status,
    )


def _draft(operation: VideoLocalizationOperation | None = None) -> VideoLocalizationDraft:
    return VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=1200,
                en_subtitle_text="Original text",
                zh_localized_subtitle_text="当前镜像",
                tts_recommended_text="当前口播",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=0,
                end_ms=1200,
                text="当前目标轨",
                source_cue_ids=["cue_0001"],
            )
        ],
        localization_state={"owner": "backend"},
        operations=[operation] if operation is not None else [],
    )


def _install_memory_store(monkeypatch, initial: VideoLocalizationDraft, *, on_save=None):
    state = {"draft": initial}

    monkeypatch.setattr(service.project_store, "get_project", lambda project_id: object() if project_id == PROJECT_ID else None)
    monkeypatch.setattr(service.draft_store, "get", lambda project_id: state["draft"] if project_id == PROJECT_ID else None)

    def save(project_id, draft, **_kwargs):
        assert project_id == PROJECT_ID
        state["draft"] = draft
        if on_save is not None:
            on_save(draft)
        return draft

    monkeypatch.setattr(service.draft_store, "save", save)

    return state


def test_target_track_mutations_cannot_bypass_active_localization_lock(monkeypatch):
    current = _draft(_operation())
    _install_memory_store(monkeypatch, current)

    with pytest.raises(AppException) as exc_info:
        service.update_cue(
            PROJECT_ID,
            "cue_0001",
            VideoLocalizationCueUpdate(
                zh_localized_subtitle_text="绕过镜像",
                tts_recommended_text="绕过口播",
            ),
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_TRACK_BUSY"

    forged_operation = current.operations[0].model_copy(update={"status": "success"})
    incoming = current.model_copy(
        update={
            "cues": [
                current.cues[0].model_copy(
                    update={
                        "en_subtitle_text": "Edited source text",
                        "zh_localized_subtitle_text": "绕过镜像",
                        "tts_recommended_text": "绕过口播",
                    }
                )
            ],
            "localized_subtitles": [
                current.localized_subtitles[0].model_copy(update={"text": "绕过目标轨"})
            ],
            "localization_state": {"owner": "client"},
            "operations": [forged_operation],
        }
    )

    saved = service.replace_video_localization_from_client(PROJECT_ID, incoming)

    assert saved is not None
    assert saved.cues[0].en_subtitle_text == "Edited source text"
    assert saved.cues[0].zh_localized_subtitle_text == "当前镜像"
    assert saved.cues[0].tts_recommended_text == "当前口播"
    assert saved.localized_subtitles[0].text == "当前目标轨"
    assert saved.localization_state == {"owner": "backend"}
    assert saved.operations[0].status == "running"


def test_localization_endpoint_reuses_active_queue_operation(monkeypatch):
    active = _operation()
    state = _install_memory_store(monkeypatch, _draft(active))
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(operation_queue.operation_state, "validate_prerequisites", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operation_queue, "_enqueue", enqueued.append)
    returned = (
        video_localization_api.submit_video_localization_localization_operation(
            PROJECT_ID,
            video_localization_api.VideoLocalizationLocalizationOperationRequest(),
        )
    )

    assert returned.operation_id == active.operation_id
    assert returned.status == "running"
    assert enqueued == [(PROJECT_ID, active.operation_id)]
    assert len(state["draft"].operations) == 1
