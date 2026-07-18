from __future__ import annotations

import re

from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationCue,
    VideoLocalizationSpeaker,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationSpeakerCreate,
    VideoLocalizationSpeakerUpdate,
    VideoLocalizationTimeRange,
)


def bind_diarization_clusters(
    draft: VideoLocalizationDraft,
    cues: list[VideoLocalizationCue],
    clusters: list[VideoLocalizationSpeakerCluster],
) -> tuple[list[VideoLocalizationSpeaker], list[VideoLocalizationCue], list[VideoLocalizationSpeakerCluster]]:
    if not clusters:
        return draft.speakers, cues, clusters

    next_speakers = list(draft.speakers)
    cluster_to_speaker: dict[str, str] = {}
    for speaker in next_speakers:
        for cluster_id in speaker.acoustic_cluster_ids:
            cluster_to_speaker[cluster_id] = speaker.speaker_id

    existing_ids = {speaker.speaker_id for speaker in next_speakers}
    for index, cluster in enumerate(clusters, start=1):
        if cluster.cluster_id in cluster_to_speaker:
            continue
        speaker_id = _next_available_speaker_id(existing_ids)
        existing_ids.add(speaker_id)
        cluster_to_speaker[cluster.cluster_id] = speaker_id
        next_speakers.append(
            VideoLocalizationSpeaker(
                speaker_id=speaker_id,
                display_name=f"说话人 {index}",
                acoustic_cluster_ids=[cluster.cluster_id],
                review_status="needs_review",
                notes="由声学说话人分离自动建立，真实身份需结合证据确认。",
            )
        )

    bound_clusters = [
        cluster.model_copy(update={"business_speaker_id": cluster_to_speaker.get(cluster.cluster_id)})
        for cluster in clusters
    ]
    bound_cues = [
        cue.model_copy(update={"speaker_id": cluster_to_speaker.get(cue.speaker_cluster_id or "")})
        if cue.speaker_cluster_id
        else cue
        for cue in cues
    ]
    return next_speakers, bound_cues, bound_clusters


def with_created_speaker(draft: VideoLocalizationDraft, payload: VideoLocalizationSpeakerCreate) -> VideoLocalizationDraft:
    speaker_id = _normalize_speaker_id(payload.speaker_id) or _next_speaker_id(draft)
    if any(speaker.speaker_id == speaker_id for speaker in draft.speakers):
        raise AppException(400, "VIDEO_LOCALIZATION_SPEAKER_EXISTS", "Speaker already exists")

    speaker = VideoLocalizationSpeaker(
        speaker_id=speaker_id,
        display_name=(payload.display_name or "").strip() or _default_display_name(len(draft.speakers)),
        route=payload.route,
        review_status=payload.review_status,
        notes=(payload.notes or "").strip() or None,
    )
    return reconcile_speakers(draft.model_copy(update={"speakers": [*draft.speakers, speaker]}))


def with_updated_speaker(draft: VideoLocalizationDraft, speaker_id: str, payload: VideoLocalizationSpeakerUpdate) -> VideoLocalizationDraft:
    update = payload.model_dump(exclude_unset=True)
    if "display_name" in update:
        update["display_name"] = (update["display_name"] or "").strip() or None
    if "notes" in update:
        update["notes"] = (update["notes"] or "").strip() or None

    updated = False
    next_speakers: list[VideoLocalizationSpeaker] = []
    for speaker in draft.speakers:
        if speaker.speaker_id != speaker_id:
            next_speakers.append(speaker)
            continue
        next_speakers.append(speaker.model_copy(update=update))
        updated = True

    if not updated:
        raise AppException(404, "VIDEO_LOCALIZATION_SPEAKER_NOT_FOUND", "Speaker not found")

    return reconcile_speakers(draft.model_copy(update={"speakers": next_speakers}))


def reconcile_speakers(draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    next_speakers: list[VideoLocalizationSpeaker] = []

    for speaker in draft.speakers:
        reference_clip_ids = []
        time_ranges: list[VideoLocalizationTimeRange] = []
        seen_reference_ids: set[str] = set()
        seen_ranges: set[tuple[int, int]] = set()

        for clip in draft.reference_clips:
            if clip.speaker_id != speaker.speaker_id:
                continue
            if clip.reference_clip_id in seen_reference_ids:
                continue
            seen_reference_ids.add(clip.reference_clip_id)
            reference_clip_ids.append(clip.reference_clip_id)
            if clip.start_ms is None or clip.end_ms is None or clip.end_ms <= clip.start_ms:
                continue
            key = (clip.start_ms, clip.end_ms)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            range_source = "manual_selection" if "generated_from_selection" in clip.quality_flags else "reference_candidate"
            time_ranges.append(VideoLocalizationTimeRange(start_ms=clip.start_ms, end_ms=clip.end_ms, source=range_source))

        for cue in draft.cues:
            if cue.speaker_id != speaker.speaker_id:
                continue
            if cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
                continue
            key = (cue.start_ms, cue.end_ms)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            time_ranges.append(VideoLocalizationTimeRange(start_ms=cue.start_ms, end_ms=cue.end_ms, source="cue"))

        next_speakers.append(
            speaker.model_copy(
                update={
                    "reference_clip_ids": reference_clip_ids,
                    "time_ranges": time_ranges,
                }
            )
        )

    return draft.model_copy(update={"speakers": next_speakers})


def _normalize_speaker_id(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")
    return normalized.lower()


def _next_speaker_id(draft: VideoLocalizationDraft) -> str:
    return _next_available_speaker_id({speaker.speaker_id for speaker in draft.speakers})


def _next_available_speaker_id(existing_ids: set[str]) -> str:
    index = 1
    while True:
        candidate = f"speaker_{index:02d}"
        if candidate not in existing_ids:
            return candidate
        index += 1


def _default_display_name(index: int) -> str:
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if index < len(labels):
        return labels[index]
    return f"S{index + 1}"
