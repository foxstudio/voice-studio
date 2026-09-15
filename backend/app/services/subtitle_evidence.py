"""Application service for external subtitle evidence consumers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.errors import AppException
from app.schemas.subtitle_evidence import (
    SubtitleEvidenceRequest,
    SubtitleEvidenceResult,
    SubtitleEvidenceSegmentInput,
)
from app.schemas.voice_studio import (
    TranscriptionRecord,
    VideoLocalizationTranscriptSegment,
)
from app.services import (
    audio_tools,
    database as db,
    video_localization_operations,
)


class SubtitleEvidenceApplicationService:
    """Resolve retained audio, then reuse the canonical ASR domain facade."""

    def __init__(
        self,
        *,
        evidence_runner=None,
    ) -> None:
        self._evidence_runner = (
            evidence_runner
            or video_localization_operations.run_external_subtitle_evidence
        )

    def analyze(
        self,
        transcription_id: str,
        request: SubtitleEvidenceRequest,
    ) -> SubtitleEvidenceResult:
        stored = db.get_one(
            "transcriptions",
            "transcription_id",
            transcription_id,
        )
        if not stored:
            raise AppException(
                404,
                "TRANSCRIPTION_NOT_FOUND",
                "Transcription not found",
            )

        source_audio_path = Path(
            str(stored.get("source_audio_path") or "")
        )
        if not source_audio_path.is_file():
            raise AppException(
                400,
                "ASR_SOURCE_AUDIO_MISSING",
                "This transcription does not retain its source audio",
            )

        record = TranscriptionRecord(**stored)
        duration_ms = self._duration_ms(record, source_audio_path)
        language = request.language or record.language
        public_segments = self._segments(
            request,
            record,
            duration_ms=duration_ms,
        )
        domain_segments = [
            VideoLocalizationTranscriptSegment(
                segment_id=item.segment_id,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                raw_text=item.text,
            )
            for item in public_segments
        ]

        audio_sha256 = self._file_sha256(source_audio_path)
        source_track_id = f"transcription:{record.transcription_id}"
        alignment, boundaries = self._evidence_runner(
            audio_path=str(source_audio_path),
            audio_sha256=audio_sha256,
            source_track_id=source_track_id,
            segments=domain_segments,
            language=language,
            duration_ms=duration_ms,
            video_frame_rate=request.video_frame_rate,
        )

        alignment_status = str(alignment.metadata.get("status") or "")
        timing_confidence = str(
            alignment.metadata.get("timing_confidence") or ""
        )
        if alignment_status != "completed" or timing_confidence != "high":
            raise AppException(
                409,
                "ASR_STRICT_ALIGNMENT_INCOMPLETE",
                "严格逐字对齐没有生成完整的真实时间证据",
            )
        previous_end_ms = -1
        invalid_words = []
        for item in alignment.words:
            if (
                item.end_ms <= item.start_ms
                or item.start_ms < previous_end_ms
            ):
                invalid_words.append(item.word_id)
            previous_end_ms = max(previous_end_ms, item.end_ms)
        if invalid_words:
            raise AppException(
                409,
                "ASR_STRICT_ALIGNMENT_INCOMPLETE",
                "严格逐字对齐仍含零时长或重叠声学单元",
                {"word_ids": invalid_words[:20]},
            )

        analysis_status = str(boundaries.metadata.get("status") or "failed")
        if analysis_status not in {"completed", "partial", "failed"}:
            analysis_status = "failed"
        return SubtitleEvidenceResult(
            transcription_id=record.transcription_id,
            engine_id=record.engine_id,
            filename=record.filename,
            language=language,
            text=(
                "\n".join(item.text for item in public_segments)
                if request.segments
                else record.text
            ),
            audio_sha256=audio_sha256,
            duration_ms=duration_ms,
            segments=public_segments,
            aligned_words=alignment.words,
            boundary_features=boundaries.boundary_features,
            subtitle_entry_by_word_id=(
                boundaries.subtitle_entry_by_word_id
            ),
            alignment_status="completed",
            alignment_engine_id=str(
                alignment.metadata.get("engine_id")
                or "qwen3-forced-aligner-0.6B"
            ),
            timing_confidence="high",
            quality_flags=[
                str(value)
                for value in alignment.metadata.get("quality_flags", [])
            ],
            alignment_call_count=int(
                alignment.metadata.get("alignment_call_count")
                or len(public_segments)
            ),
            audio_analysis_status=analysis_status,
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _duration_ms(
        record: TranscriptionRecord,
        source_audio_path: Path,
    ) -> int:
        duration_ms = int(record.duration_ms or 0)
        if duration_ms <= 0:
            duration_ms = int(
                audio_tools.probe_audio(source_audio_path).get(
                    "duration_ms"
                )
                or 0
            )
        if duration_ms <= 0:
            raise AppException(
                400,
                "ASR_AUDIO_DURATION_UNAVAILABLE",
                "无法读取原始音频时长，不能生成严格字幕时间证据",
            )
        return duration_ms

    @staticmethod
    def _segments(
        request: SubtitleEvidenceRequest,
        record: TranscriptionRecord,
        *,
        duration_ms: int,
    ) -> list[SubtitleEvidenceSegmentInput]:
        segments = list(request.segments)
        if not segments:
            segments = [
                SubtitleEvidenceSegmentInput(
                    segment_id=f"segment-{index:04d}",
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    text=item.text,
                )
                for index, item in enumerate(record.segments, start=1)
                if item.text.strip() and item.end_ms > item.start_ms
            ]
        if not segments and record.text.strip():
            segments = [
                SubtitleEvidenceSegmentInput(
                    segment_id="segment-0001",
                    start_ms=0,
                    end_ms=duration_ms,
                    text=record.text,
                )
            ]
        if not segments:
            raise AppException(
                400,
                "ASR_TRANSCRIPT_EMPTY",
                "没有可用于严格逐字对齐的字幕文字",
            )
        for item in segments:
            if item.end_ms > duration_ms:
                raise AppException(
                    400,
                    "ASR_SEGMENT_OUT_OF_RANGE",
                    f"字幕片段 {item.segment_id} 超出音频时长",
                )
        return segments


_SERVICE: SubtitleEvidenceApplicationService | None = None


def get_service() -> SubtitleEvidenceApplicationService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SubtitleEvidenceApplicationService()
    return _SERVICE


__all__ = ["SubtitleEvidenceApplicationService", "get_service"]
