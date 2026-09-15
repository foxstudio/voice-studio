"""Stable source-lineage fingerprint for the localization workflow."""

from __future__ import annotations

import hashlib
import json

from app.domains.video_localization.schemas import VideoLocalizationDraft


def source_fingerprint(draft: VideoLocalizationDraft) -> str:
    """Fingerprint every source fact that can change localization output."""

    payload = {
        "transcription_revision_ids": sorted(
            {
                cue.transcription_revision_id
                for cue in draft.cues
                if cue.transcription_revision_id
            }
        ),
        "cues": [
            {
                "cue_id": cue.cue_id,
                "speaker_id": cue.speaker_id,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "text": cue.en_subtitle_text,
                "source_word_ids": cue.source_word_ids,
            }
            for cue in draft.cues
        ],
        "speakers": [
            {
                "speaker_id": speaker.speaker_id,
                "display_name": speaker.display_name,
                "notes": speaker.notes,
            }
            for speaker in sorted(
                draft.speakers,
                key=lambda item: item.speaker_id,
            )
        ],
        "transcription": (
            {
                "revision_id": draft.transcription.revision_id,
                "language": draft.transcription.language,
                "source_track_id": draft.transcription.source_track_id,
                "source_audio_sha256": (
                    draft.transcription.source_audio_sha256
                ),
                "corrected_text": draft.transcription.corrected_text,
                "segments": [
                    {
                        "segment_id": segment.segment_id,
                        "raw_text": segment.raw_text,
                        "corrected_text": segment.corrected_text,
                    }
                    for segment in draft.transcription.segments
                ],
                "words": [
                    {
                        "word_id": word.word_id,
                        "segment_id": word.segment_id,
                        "text": word.text,
                        "start_ms": word.start_ms,
                        "end_ms": word.end_ms,
                        "timing_source": word.timing_source,
                    }
                    for word in draft.transcription.words
                ],
            }
            if draft.transcription
            else None
        ),
        "glossary": [
            item.model_dump(mode="json")
            for item in draft.glossary
        ],
        "scene_context": draft.scene_context,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["source_fingerprint"]
