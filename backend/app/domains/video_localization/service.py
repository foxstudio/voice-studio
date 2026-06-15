from __future__ import annotations

from app.domains.video_localization.quality_gate import evaluate_quality_gate
from app.schemas.voice_studio import VideoLocalizationDraft, VideoLocalizationExport, now_iso
from app.services import project_store

VIDEO_LOCALIZATION_KEY = "video_localization"


def get_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    raw = project.parameters.get(VIDEO_LOCALIZATION_KEY) or {}
    return VideoLocalizationDraft(**raw)


def save_video_localization(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    next_draft = _with_fresh_gate(draft, updated_at=now_iso())
    project.parameters = {**project.parameters, VIDEO_LOCALIZATION_KEY: next_draft.model_dump()}
    project_store.save_project(project)
    return next_draft


def export_video_localization(project_id: str) -> VideoLocalizationExport | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    next_draft = _with_fresh_gate(draft, updated_at=draft.updated_at)
    project.parameters = {**project.parameters, VIDEO_LOCALIZATION_KEY: next_draft.model_dump()}
    project_store.save_project(project)
    summary = {
        "cue_count": len(next_draft.cues),
        "ready_cue_count": sum(1 for cue in next_draft.cues if cue.review_status in {"ready", "locked"}),
        "blocker_count": len(next_draft.quality_gate.blockers),
        "warning_count": len(next_draft.quality_gate.warnings),
    }
    return VideoLocalizationExport(
        project_id=project.project_id,
        project_name=project.name,
        exported_at=now_iso(),
        export_summary=summary,
        **next_draft.model_dump(),
    )


def _with_fresh_gate(draft: VideoLocalizationDraft, updated_at: str | None) -> VideoLocalizationDraft:
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
