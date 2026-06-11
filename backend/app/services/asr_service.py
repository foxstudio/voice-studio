from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.errors import AppException
from app.schemas.voice_studio import TimestampMode, TranscriptionRecord, TranscriptionSegment
from app.services import engine_registry, mimo_client, qwen_forced_aligner, qwen_mlx_asr, settings_store


SUPPORTED_ENGINES = {"mimo-v2.5-asr", "qwen3-asr-mlx"}
SUPPORTED_LANGUAGES = {"auto", "zh", "en"}
SUPPORTED_SUFFIXES = {".wav", ".mp3"}


def validate_request(engine_id: str, language: str, suffix: str) -> None:
    if engine_id not in SUPPORTED_ENGINES:
        raise AppException(400, "ASR_ENGINE_UNSUPPORTED", f"Unsupported ASR engine: {engine_id}")
    if language not in SUPPORTED_LANGUAGES:
        raise AppException(400, "ASR_LANGUAGE_UNSUPPORTED", "ASR language must be auto, zh, or en")
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppException(400, "ASR_AUDIO_FORMAT_UNSUPPORTED", "ASR currently supports wav and mp3 files")
    ensure_engine_ready(engine_id)


def ensure_engine_ready(engine_id: str) -> dict[str, Any]:
    health = engine_registry.health_check(engine_id)
    if health.get("healthy"):
        return health

    status = health.get("status", "unavailable")
    detail = health.get("detail") or ", ".join(health.get("missing", [])) or "ASR engine is unavailable"
    code_by_status = {
        "cloud_disabled": "MIMO_API_KEY_REQUIRED",
        "api_key_missing": "MIMO_API_KEY_REQUIRED",
        "runtime_missing": "QWEN3_ASR_RUNTIME_MISSING",
        "model_missing": "QWEN3_ASR_MODEL_MISSING",
        "not_found": "ASR_ENGINE_UNSUPPORTED",
    }
    raise AppException(400, code_by_status.get(status, "ASR_ENGINE_UNAVAILABLE"), str(detail))


def transcribe(*, engine_id: str, audio_path: str, language: str) -> dict[str, Any]:
    ensure_engine_ready(engine_id)
    if engine_id == "mimo-v2.5-asr":
        settings = settings_store.get()
        return mimo_client.transcribe_audio(
            base_url=settings.mimo_base_url,
            api_key=settings_store.mimo_api_key() or "",
            audio_path=audio_path,
            language=language,
        )
    if engine_id == "qwen3-asr-mlx":
        return qwen_mlx_asr.transcribe_audio(
            audio_path=audio_path,
            language=language,
            model_path=str(settings_store.model_path(engine_id)),
        )
    raise AppException(400, "ASR_ENGINE_UNSUPPORTED", f"Unsupported ASR engine: {engine_id}")


def timestamp_metadata_for(engine_id: str, segments: list[dict[str, Any]] | list[TranscriptionSegment] | None) -> dict[str, Any]:
    has_segments = bool(segments)
    return {
        "has_source_audio": True,
        "timestamp_mode": TimestampMode.native if has_segments else TimestampMode.none,
        "timestamp_source_engine_id": engine_id if has_segments else None,
    }


def upload_path_for(engine_id: str, record_id: str, suffix: str) -> Path:
    upload_dir = settings_store.cache_dir() / "asr_uploads" / engine_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir / f"{record_id}{suffix}"


def supplement_timestamps(
    *,
    record: TranscriptionRecord,
    source_audio_path: str,
    strategy: str = "auto",
    overwrite: bool = False,
) -> TranscriptionRecord:
    if record.segments and not overwrite:
        return record
    if strategy not in {"auto", "forced_aligner", "qwen3-asr-mlx"}:
        raise AppException(400, "ASR_TIMESTAMP_STRATEGY_UNSUPPORTED", f"Unsupported timestamp strategy: {strategy}")
    if not Path(source_audio_path).exists():
        raise AppException(400, "ASR_SOURCE_AUDIO_MISSING", "The original uploaded audio is no longer available")

    if strategy in {"auto", "forced_aligner"}:
        try:
            precise_segments = _forced_align_segments(record=record, source_audio_path=source_audio_path)
            if precise_segments:
                record.segments = precise_segments
                record.timestamp_mode = TimestampMode.supplemented
                record.timestamp_source_engine_id = "qwen3-forced-aligner-0.6B"
                record.has_source_audio = True
                return record
        except Exception as exc:
            if strategy == "forced_aligner":
                raise AppException(400, "ASR_FORCED_ALIGN_FAILED", str(exc)) from exc

    coarse_segments = _coarse_supplement_segments(record=record, source_audio_path=source_audio_path)
    if not coarse_segments:
        raise AppException(400, "ASR_TIMESTAMP_UNAVAILABLE", "Local Qwen timestamp supplementation did not return segments")

    record.segments = coarse_segments
    record.timestamp_mode = TimestampMode.supplemented
    record.timestamp_source_engine_id = "qwen3-asr-mlx"
    record.has_source_audio = True
    return record


def export_formats(record: TranscriptionRecord) -> list[str]:
    formats = ["txt"]
    if record.segments:
        formats.append("srt")
    return formats


def export_text(record: TranscriptionRecord, fmt: str) -> str:
    if fmt == "txt":
        return record.text
    if fmt == "srt":
        if not record.segments:
            raise AppException(400, "ASR_SRT_UNAVAILABLE", "This transcription does not contain timestamp segments yet")
        return _segments_to_srt(record.segments)
    raise AppException(400, "ASR_EXPORT_FORMAT_UNSUPPORTED", f"Unsupported ASR export format: {fmt}")


def normalize_segments(items: list[dict[str, Any]] | list[TranscriptionSegment] | None) -> list[TranscriptionSegment]:
    return [item if isinstance(item, TranscriptionSegment) else TranscriptionSegment(**item) for item in (items or [])]


def _forced_align_segments(*, record: TranscriptionRecord, source_audio_path: str) -> list[TranscriptionSegment]:
    if (record.duration_ms or 0) > 5 * 60 * 1000:
        raise RuntimeError("forced aligner 当前只对 5 分钟以内的单条音频启用")

    language_name = _alignment_language_name(record.text, record.language)
    items = qwen_forced_aligner.align_audio(
        audio_path=source_audio_path,
        transcript_text=record.text,
        language=language_name,
    )
    return _forced_align_items_to_segments(record.text, items)


def _coarse_supplement_segments(*, record: TranscriptionRecord, source_audio_path: str) -> list[TranscriptionSegment]:
    result = transcribe(engine_id="qwen3-asr-mlx", audio_path=source_audio_path, language=record.language)
    raw_segments = normalize_segments(result.get("segments"))
    if not raw_segments:
        return []
    return _map_segments_to_transcript(raw_segments, record.text)


def _map_segments_to_transcript(segments: list[TranscriptionSegment], transcript_text: str) -> list[TranscriptionSegment]:
    target_text = " ".join((transcript_text or "").split()).strip()
    if not target_text:
        return segments

    desired_count = len(segments)
    chunks = _split_text_into_chunks(target_text, desired_count)
    if not chunks:
        return segments

    mapped: list[TranscriptionSegment] = []
    for index, segment in enumerate(segments):
        text = chunks[index] if index < len(chunks) else ""
        mapped.append(
            TranscriptionSegment(
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=text or segment.text,
                language=segment.language,
            )
        )
    return mapped


def _forced_align_items_to_segments(transcript_text: str, items: list[dict[str, Any]]) -> list[TranscriptionSegment]:
    sentences = _sentence_units(transcript_text)
    if not sentences:
        return []

    tokens = [item for item in items if str(item.get("text", "")).strip()]
    if not tokens:
        return []

    token_cursor = 0
    segments: list[TranscriptionSegment] = []
    for sentence in sentences:
        sentence_tokens = _forced_align_sentence_tokens(sentence)
        token_count = len(sentence_tokens)
        if token_count <= 0:
            continue
        selected = tokens[token_cursor : token_cursor + token_count]
        if not selected:
            break
        token_cursor += len(selected)
        start_ms = int(round(float(selected[0].get("start_time", 0)) * 1000))
        end_ms = int(round(float(selected[-1].get("end_time", 0)) * 1000))
        segments.append(
            TranscriptionSegment(
                start_ms=start_ms,
                end_ms=max(start_ms, end_ms),
                text=sentence.strip(),
                language=None,
            )
        )

    if segments:
        return segments

    return [
        TranscriptionSegment(
            start_ms=int(round(float(tokens[0].get("start_time", 0)) * 1000)),
            end_ms=int(round(float(tokens[-1].get("end_time", 0)) * 1000)),
            text=transcript_text.strip(),
            language=None,
        )
    ]


def _split_text_into_chunks(text: str, count: int) -> list[str]:
    count = max(1, int(count or 1))
    units = [item for item in _sentence_units(text) if item]
    if not units:
        return [text.strip()]
    if count == 1:
        return ["".join(units).strip()]

    while len(units) < count:
        split_index = max(range(len(units)), key=lambda idx: len(units[idx]))
        pieces = _split_unit_once(units[split_index])
        if len(pieces) == 1:
            break
        units = units[:split_index] + pieces + units[split_index + 1 :]

    if len(units) >= count:
        return _merge_units_to_count(units, count)

    chunks = units[:]
    while len(chunks) < count:
        chunks.append("")
    return chunks


def _sentence_units(text: str) -> list[str]:
    raw = re.split(r"(?<=[。！？!?；;…])|\n+", text.strip())
    return [item.strip() for item in raw if item and item.strip()]


def _alignment_language_name(text: str, language: str) -> str:
    if language == "zh":
        return "Chinese"
    if language == "en":
        return "English"
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    return "English" if ascii_letters > cjk_chars else "Chinese"


def _forced_align_sentence_tokens(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    tokens: list[str] = []
    current: list[str] = []

    def flush_current() -> None:
        nonlocal current
        if current:
            token = re.sub(r"[^A-Za-z0-9']+", "", "".join(current))
            if token:
                tokens.append(token)
            current = []

    for ch in cleaned:
        if re.match(r"[\u4e00-\u9fff]", ch):
            flush_current()
            tokens.append(ch)
        elif re.match(r"[A-Za-z0-9']", ch):
            current.append(ch)
        else:
            flush_current()

    flush_current()
    return tokens


def _split_unit_once(unit: str) -> list[str]:
    for pattern in [r"(?<=[，,、：:])", r"(?<=\s)"]:
        parts = [item.strip() for item in re.split(pattern, unit) if item.strip()]
        if len(parts) > 1:
            mid = len(parts) // 2
            return ["".join(parts[:mid]).strip(), "".join(parts[mid:]).strip()]
    midpoint = max(1, len(unit) // 2)
    return [unit[:midpoint].strip(), unit[midpoint:].strip()]


def _merge_units_to_count(units: list[str], count: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    remaining_chars = sum(max(1, len(item)) for item in units)

    for index, unit in enumerate(units):
        remaining_units = len(units) - index
        remaining_slots = count - len(chunks)
        if remaining_units == remaining_slots:
            if current:
                chunks.append("".join(current).strip())
            chunks.extend(item.strip() for item in units[index:])
            return chunks[:count]

        current.append(unit)
        unit_chars = max(1, len(unit))
        current_chars += unit_chars
        remaining_chars -= unit_chars
        ideal_chars = max(1, (current_chars + remaining_chars) / remaining_slots)
        if len(chunks) < count - 1 and current_chars >= ideal_chars:
            chunks.append("".join(current).strip())
            current = []
            current_chars = 0

    if current:
        chunks.append("".join(current).strip())

    if len(chunks) > count:
        head = chunks[: count - 1]
        tail = "".join(chunks[count - 1 :]).strip()
        return head + [tail]
    while len(chunks) < count:
        chunks.append("")
    return chunks


def _segments_to_srt(segments: list[TranscriptionSegment]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_format_srt_time(item.start_ms)} --> {_format_srt_time(item.end_ms)}",
                    item.text.strip(),
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def _format_srt_time(value_ms: int) -> str:
    total_ms = max(0, int(value_ms))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
