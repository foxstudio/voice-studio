from __future__ import annotations

from pathlib import Path

from app.domains.video_localization import media_health, dubbing_completion
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationDraft, now_iso


def build_production_readiness_audit(*, project_id: str, project_name: str, draft: VideoLocalizationDraft) -> dict:
    media = media_health.inspect_project_media(draft).health
    checks = _readiness_checks(draft, media)
    completion = None
    # Modern projects share the same physical projection as Agent execution.
    # Legacy cue-only projects retain their read-only compatibility projection.
    if draft.localized_subtitles and draft.source_media.duration_ms:
        completion = dubbing_completion.read_current_completion(project_id, draft)
        checks = [check for check in checks if check["code"] not in {"tts_audio_coverage", "tts_failures"}]
        checks.append(_readiness_check(
            "tts_audio_coverage", "实际配音覆盖",
            "blocked" if completion.status == "incomplete" else "warning" if completion.status == "complete_with_warnings" else "pass",
            "由 Agent 核对当前音频及收尾缺失", details=completion.model_dump(mode="json"),
        ))
    summary = _readiness_summary(draft)
    if completion is not None:
        summary.update(
            tts_route_count=len(completion.expected_target_subtitle_ids),
            generated_tts_count=len(completion.covered_target_subtitle_ids),
            failed_tts_count=len(completion.unresolved_group_ids),
            coverage_source="timeline",
        )
    blocking = [check for check in checks if check["status"] == "blocked"]
    warnings = [check for check in checks if check["status"] == "warning"]
    return {
        "project_id": project_id,
        "project_name": project_name,
        "generated_at": now_iso(),
        "status": "blocked" if blocking else "warning" if warnings else "ready_for_mix",
        "summary": summary,
        "dubbing_completion": completion.model_dump(mode="json") if completion else None,
        "checks": checks,
        "cue_status": [_readiness_cue_status(cue) for cue in draft.cues],
        "next_actions": [item["action"] for item in checks if item.get("action")],
    }


def _readiness_summary(draft: VideoLocalizationDraft) -> dict:
    tts_route_cues = [cue for cue in draft.cues if cue.audio_route in {"clone_from_source", "preset_tts"}]
    generated_cues = [cue for cue in tts_route_cues if _path_exists(cue.tts_audio_path)]
    failed_cues = [cue for cue in draft.cues if cue.tts_batch_status in {"failed", "cancelled"}]
    return {
        "cue_count": len(draft.cues),
        "tts_route_count": len(tts_route_cues),
        "generated_tts_count": len(generated_cues),
        "failed_tts_count": len(failed_cues),
        "preserve_original_count": sum(1 for cue in draft.cues if cue.audio_route == "preserve_original_audio"),
        "ready_or_locked_count": sum(1 for cue in draft.cues if cue.review_status in {"ready", "locked"}),
        "quality_gate_status": draft.quality_gate.status,
        "blocker_count": len(draft.quality_gate.blockers),
        "warning_count": len(draft.quality_gate.warnings),
    }


def _readiness_checks(
    draft: VideoLocalizationDraft,
    media: media_health.ProjectMediaHealth,
) -> list[dict]:
    tts_route_cues = [cue for cue in draft.cues if cue.audio_route in {"clone_from_source", "preset_tts"}]
    missing_tts_cue_ids = [cue.cue_id for cue in tts_route_cues if not _path_exists(cue.tts_audio_path)]
    failed_tts_cue_ids = [cue.cue_id for cue in draft.cues if cue.tts_batch_status in {"failed", "cancelled"}]
    quality_gate_check_status = (
        "pass"
        if draft.quality_gate.status == "pass"
        else "blocked"
        if draft.quality_gate.status == "blocked"
        else "warning"
    )
    return [
        _readiness_check(
            "source_video",
            "源视频",
            "pass" if media.source_video.status == "available" else "blocked",
            (
                "重新关联或导入源视频"
                if media.source_video.status != "unconfigured"
                else "导入源视频"
            ),
            details={
                "media_status": media.source_video.status,
                "reason_code": media.source_video.reason_code,
            },
        ),
        _readiness_check(
            "source_audio",
            "源音轨",
            "pass" if media.source_audio.status == "available" else "blocked",
            "抽取或恢复源音轨",
            details={
                "media_status": media.source_audio.status,
                "reason_code": media.source_audio.reason_code,
                "selected_source": media.source_audio.selected_source,
            },
        ),
        _readiness_check(
            "background_stem",
            "背景音乐/环境声 stem",
            "pass" if media.background.status == "available" else "warning",
            "运行人声/背景声分离",
            details={
                "media_status": media.background.status,
                "reason_code": media.background.reason_code,
            },
        ),
        _readiness_check(
            "clean_vocals",
            "干净人声 stem",
            "pass" if media.vocals.status == "available" else "warning",
            "运行人声/背景声分离",
            details={
                "media_status": media.vocals.status,
                "reason_code": media.vocals.reason_code,
            },
        ),
        _readiness_check(
            "source_subtitles",
            "源字幕",
            "pass" if draft.cues else "blocked",
            "先生成或导入源字幕",
            details={"cue_count": len(draft.cues)},
        ),
        _readiness_check(
            "quality_gate",
            "质量门",
            quality_gate_check_status,
            (
                "完成字幕质量检查"
                if draft.quality_gate.status == "unknown"
                else "修复质量门阻断项"
            ),
            details={"status": draft.quality_gate.status},
        ),
        _readiness_check(
            "tts_audio_coverage",
            "中文 TTS 音频覆盖",
            "pass" if not missing_tts_cue_ids else "blocked",
            "继续生成或同步 TTS 结果",
            details={"missing_cue_ids": missing_tts_cue_ids, "expected_count": len(tts_route_cues)},
        ),
        _readiness_check(
            "tts_failures",
            "TTS 失败状态",
            "pass" if not failed_tts_cue_ids else "blocked",
            "复查失败原因并重新生成",
            details={"failed_cue_ids": failed_tts_cue_ids},
        ),
    ]


def _readiness_check(code: str, label: str, status: str, action: str, *, details: dict | None = None) -> dict:
    item = {"code": code, "label": label, "status": status}
    if status != "pass":
        item["action"] = action
    if details:
        item["details"] = details
    return item


def _readiness_cue_status(cue: VideoLocalizationCue) -> dict:
    return {
        "cue_id": cue.cue_id,
        "speaker_id": cue.speaker_id,
        "audio_route": cue.audio_route,
        "review_status": cue.review_status,
        "start_ms": cue.start_ms,
        "end_ms": cue.end_ms,
        "has_tts_audio": _path_exists(cue.tts_audio_path),
        "tts_audio_path": cue.tts_audio_path,
        "tts_batch_task_id": cue.tts_batch_task_id,
        "tts_batch_status": cue.tts_batch_status,
        "tts_batch_error": cue.tts_batch_error,
        "generated_duration_ms": cue.generated_duration_ms,
        "source_duration_ms": cue.source_duration_ms,
    }


def _path_exists(value: str | None) -> bool:
    return bool(value and Path(value).is_file())
