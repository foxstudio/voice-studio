from __future__ import annotations

from app.domains.video_localization import draft_store
from app.domains.video_localization import subtitles
from app.domains.video_localization.readiness import build_production_readiness_audit
from app.schemas.voice_studio import VideoLocalizationDraft, VideoLocalizationExport, now_iso


def export_subtitles(draft: VideoLocalizationDraft, kind: str) -> str:
    return subtitles.export_srt(draft, kind)


def export_bundle(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> VideoLocalizationExport | None:
    next_draft = draft_store.save(project_id, draft, updated_at=draft.updated_at)
    if not next_draft:
        return None
    summary = {
        "cue_count": len(next_draft.cues),
        "ready_cue_count": sum(1 for cue in next_draft.cues if cue.review_status in {"ready", "locked"}),
        "blocker_count": len(next_draft.quality_gate.blockers),
        "warning_count": len(next_draft.quality_gate.warnings),
    }
    return VideoLocalizationExport(
        project_id=project_id,
        project_name=project_name,
        exported_at=now_iso(),
        export_summary=summary,
        **next_draft.model_dump(),
    )


def production_readiness(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> dict:
    next_draft = draft_store.with_fresh_gate(draft, updated_at=draft.updated_at)
    return build_production_readiness_audit(project_id=project_id, project_name=project_name, draft=next_draft)
