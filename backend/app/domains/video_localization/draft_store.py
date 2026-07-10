from __future__ import annotations

from datetime import datetime

from app.domains.video_localization.quality_gate import evaluate_quality_gate
from app.domains.video_localization import project_manifest
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.services import project_store

VIDEO_LOCALIZATION_KEY = "video_localization"
_USE_CURRENT_TIME = object()


def get(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    raw = project.parameters.get(VIDEO_LOCALIZATION_KEY)
    if isinstance(raw, dict) and raw:
        try:
            return VideoLocalizationDraft(**raw)
        except (ValueError, TypeError):
            pass
    return project_manifest.read_project_snapshot(project_id) or VideoLocalizationDraft()


def save(project_id: str, draft: VideoLocalizationDraft, *, updated_at: str | None | object = _USE_CURRENT_TIME) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    actual_updated_at = datetime.now().isoformat(timespec="microseconds") if updated_at is _USE_CURRENT_TIME else updated_at
    next_draft = with_fresh_gate(draft, updated_at=actual_updated_at)
    project.parameters = {**project.parameters, VIDEO_LOCALIZATION_KEY: next_draft.model_dump()}
    project_store.save_project(project)
    project_manifest.write_project_snapshot(project, next_draft)
    return next_draft


def with_fresh_gate(draft: VideoLocalizationDraft, updated_at: str | None) -> VideoLocalizationDraft:
    gate = evaluate_quality_gate(draft)
    status = _status_for_gate(draft, gate.status)
    return draft.model_copy(update={"quality_gate": gate, "status": status, "updated_at": updated_at})


def _status_for_gate(draft: VideoLocalizationDraft, gate_status: str) -> str:
    if gate_status == "blocked":
        return "blocked"
    if draft.status in {"tts_running", "candidate"}:
        return draft.status
    if gate_status == "pass" and draft.cues:
        return "ready_for_tts"
    if draft.cues:
        return "reviewing"
    return "draft"
