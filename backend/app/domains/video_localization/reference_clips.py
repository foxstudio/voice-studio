from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.domains.video_localization import media_assets, media_health
from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationReferenceClip,
)
from app.services import audio_tools


@dataclass(frozen=True)
class AutomaticReferenceMedia:
    reference_clip_id: str
    cue_id: str
    speaker_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    audio_path: Path
    audio_sha256: str


@dataclass(frozen=True)
class AutomaticReferenceCandidateResult:
    draft: VideoLocalizationDraft
    media: tuple[AutomaticReferenceMedia, ...]
    generated_paths: tuple[Path, ...]
    candidate_clip_count: int
    linked_cue_count: int


def build_automatic_reference_candidates(
    project_id: str,
    draft: VideoLocalizationDraft,
    *,
    operation_id: str | None = None,
    reusable_media: tuple[AutomaticReferenceMedia, ...] = (),
) -> AutomaticReferenceCandidateResult:
    project_media = media_health.inspect_project_media(draft)
    if (
        draft.stems.separation_status != "completed"
        or project_media.health.vocals.status == "unconfigured"
    ):
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING",
            "Separate clean vocals before creating reference clips",
        )
    vocals_path = project_media.paths.vocals
    if vocals_path is None:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND",
            "Clean vocals file is missing",
        )
    refs_dir = (
        media_assets.ensure_project_video_localization_dir(
            project_id
        )
        / "references"
    )
    refs_dir.mkdir(parents=True, exist_ok=True)
    existing_refs = {
        clip.reference_clip_id: clip
        for clip in draft.reference_clips
    }
    next_refs = list(draft.reference_clips)
    next_cues: list[VideoLocalizationCue] = []
    reusable_by_id = {
        item.reference_clip_id: item
        for item in reusable_media
    }
    if len(reusable_by_id) != len(reusable_media):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REFERENCE_REPLAY_INVALID",
            "参考音候选恢复结果包含重复 ID。",
        )
    result_media: list[AutomaticReferenceMedia] = []
    generated_paths: list[Path] = []
    changed = False
    try:
        for cue in draft.cues:
            next_cue = cue
            if not _cue_can_seed_reference(cue):
                next_cues.append(next_cue)
                continue
            reference_id = _reference_id_for_cue(cue)
            if reference_id not in existing_refs:
                reusable = reusable_by_id.pop(
                    reference_id,
                    None,
                )
                media = (
                    _verified_reusable_media(cue, reusable)
                    if reusable is not None
                    else _cut_automatic_reference_media(
                        project_id,
                        operation_id,
                        refs_dir,
                        vocals_path,
                        cue,
                        reference_id,
                    )
                )
                if reusable is None:
                    generated_paths.append(media.audio_path)
                reference = VideoLocalizationReferenceClip(
                    reference_clip_id=reference_id,
                    speaker_id=cue.speaker_id,
                    source_stem="vocals_clean",
                    start_ms=cue.start_ms,
                    end_ms=cue.end_ms,
                    duration_ms=media.duration_ms,
                    audio_path=str(media.audio_path),
                    cleanliness="needs_review",
                    asr_text=cue.en_subtitle_text,
                    asr_status=(
                        "candidate"
                        if cue.en_subtitle_text
                        else "pending"
                    ),
                    quality_flags=[
                        "generated_from_cue",
                        "needs_cleanliness_review",
                    ],
                )
                next_refs.append(reference)
                existing_refs[reference_id] = reference
                result_media.append(media)
                changed = True
            if not next_cue.reference_clip_id:
                next_cue = next_cue.model_copy(
                    update={
                        "reference_clip_id": reference_id
                    }
                )
                changed = True
            next_cues.append(next_cue)
        if reusable_by_id:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REFERENCE_REPLAY_INVALID",
                "参考音候选恢复结果与当前候选 cue 不一致。",
            )
        if not changed:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_EMPTY",
                "No cue has speaker and time range for reference clipping",
            )
    except Exception:
        for path in generated_paths:
            path.unlink(missing_ok=True)
        raise
    next_draft = draft.model_copy(
        update={
            "reference_clips": next_refs,
            "cues": next_cues,
        }
    )
    linked_cue_count = sum(
        bool(cue.reference_clip_id)
        for cue in next_draft.cues
        if _cue_can_seed_reference(cue)
    )
    return AutomaticReferenceCandidateResult(
        draft=next_draft,
        media=tuple(result_media),
        generated_paths=tuple(generated_paths),
        candidate_clip_count=sum(
            1
            for cue in next_draft.cues
            if _cue_can_seed_reference(cue)
            and cue.reference_clip_id
        ),
        linked_cue_count=linked_cue_count,
    )


def automatic_reference_candidate_revision(
    draft: VideoLocalizationDraft,
) -> str:
    payload = {
        "stems": {
            "separation_status": draft.stems.separation_status,
            "vocals_clean_path": draft.stems.vocals_clean_path,
            "vocals_clean_sha256": (
                draft.stems.vocals_clean_sha256
            ),
        },
        "cues": [
            cue.model_dump(mode="json")
            for cue in draft.cues
        ],
        "reference_clips": [
            clip.model_dump(mode="json")
            for clip in draft.reference_clips
        ],
        "speakers": [
            speaker.model_dump(mode="json")
            for speaker in draft.speakers
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def clean_vocals_content_fingerprint(
    draft: VideoLocalizationDraft,
) -> str:
    project_media = media_health.inspect_project_media(draft)
    vocals_path = project_media.paths.vocals
    if vocals_path is None:
        if project_media.health.vocals.status == "unconfigured":
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING",
                "Separate clean vocals before creating reference clips",
            )
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND",
            "Clean vocals file is missing",
        )
    return media_assets.file_sha256(vocals_path)


def automatic_candidate_cue_identities(
    draft: VideoLocalizationDraft,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "cue_id": cue.cue_id,
            "speaker_id": str(cue.speaker_id),
            "start_ms": int(cue.start_ms or 0),
            "end_ms": int(cue.end_ms or 0),
            "source_text_sha256": hashlib.sha256(
                str(cue.en_subtitle_text or "").encode("utf-8")
            ).hexdigest(),
        }
        for cue in draft.cues
        if _cue_can_seed_reference(cue)
    )


def _cut_automatic_reference_media(
    project_id: str,
    operation_id: str | None,
    refs_dir: Path,
    vocals_path: Path,
    cue: VideoLocalizationCue,
    reference_id: str,
) -> AutomaticReferenceMedia:
    clip_path = (
        media_assets.automatic_reference_clip_path(
            project_id,
            operation_id,
            reference_id,
        )
        if operation_id is not None
        else media_assets.unique_path(
            refs_dir / f"{reference_id}.wav"
        )
    )
    if operation_id is not None:
        clip_path.unlink(missing_ok=True)
    try:
        media_assets.cut_audio_clip(
            vocals_path,
            clip_path,
            int(cue.start_ms or 0),
            int(cue.end_ms or 0),
        )
        meta = audio_tools.probe_audio(clip_path)
        return AutomaticReferenceMedia(
            reference_clip_id=reference_id,
            cue_id=cue.cue_id,
            speaker_id=str(cue.speaker_id),
            start_ms=int(cue.start_ms or 0),
            end_ms=int(cue.end_ms or 0),
            duration_ms=int(
                meta.get("duration_ms")
                or _cue_duration_ms(cue)
                or 0
            ),
            audio_path=clip_path,
            audio_sha256=media_assets.file_sha256(
                clip_path
            ),
        )
    except Exception:
        if operation_id is not None:
            clip_path.unlink(missing_ok=True)
        raise


def _verified_reusable_media(
    cue: VideoLocalizationCue,
    media: AutomaticReferenceMedia,
) -> AutomaticReferenceMedia:
    if (
        media.cue_id != cue.cue_id
        or media.speaker_id != str(cue.speaker_id)
        or media.start_ms != int(cue.start_ms or 0)
        or media.end_ms != int(cue.end_ms or 0)
        or not media.audio_path.is_file()
        or media_assets.file_sha256(media.audio_path)
        != media.audio_sha256
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REFERENCE_REPLAY_INVALID",
            "已完成的参考音候选与当前 cue 或媒体文件不一致。",
        )
    return media


def _cue_can_seed_reference(cue: VideoLocalizationCue) -> bool:
    return bool(cue.speaker_id and cue.speaker_id != "mixed" and cue.start_ms is not None and cue.end_ms is not None and cue.end_ms > cue.start_ms)


def _cue_duration_ms(cue: VideoLocalizationCue) -> int | None:
    if cue.start_ms is None or cue.end_ms is None:
        return None
    return max(0, cue.end_ms - cue.start_ms)


def _reference_id_for_cue(cue: VideoLocalizationCue) -> str:
    speaker_id = _safe_identifier(cue.speaker_id or "speaker")
    cue_id = _safe_identifier(cue.cue_id)
    return f"ref_{speaker_id}_{cue_id}"


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "item"
