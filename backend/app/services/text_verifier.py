from __future__ import annotations

import difflib
import re

from app.schemas.voice_studio import TTSVerificationResponse, TTSVerificationSegment

_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;…])\s*|\n+")
_PUNCT_RE = re.compile(r"[\s`~!@#$%^&*()_\-+=\[\]{}\\|;:'\",<.>/?，。！？；：、“”‘’（）《》【】—…·]+")


def verify_transcript(*, expected_text: str, transcript_text: str, result_id: str | None = None, transcription_id: str | None = None, asr_engine_id: str | None = None) -> TTSVerificationResponse:
    expected = expected_text.strip()
    transcript = transcript_text.strip()
    normalized_expected = normalize_text(expected)
    normalized_transcript = normalize_text(transcript)

    warnings: list[str] = []
    suggestions: list[str] = []
    if not expected:
        return TTSVerificationResponse(
            status="skipped",
            coverage=0.0,
            similarity=0.0,
            expected_text=expected,
            transcript_text=transcript,
            normalized_expected=normalized_expected,
            normalized_transcript=normalized_transcript,
            warnings=["缺少原始合成文本，无法校对。"],
            suggestions=["请提供 expected_text，或使用包含 input_text 的 result_id。"],
            result_id=result_id,
            transcription_id=transcription_id,
            asr_engine_id=asr_engine_id,
        )
    if not transcript:
        return TTSVerificationResponse(
            status="failed",
            coverage=0.0,
            similarity=0.0,
            expected_text=expected,
            transcript_text=transcript,
            normalized_expected=normalized_expected,
            normalized_transcript=normalized_transcript,
            missing_segments=_segments_for_empty_transcript(expected),
            segment_results=_segments_for_empty_transcript(expected),
            warnings=["转录文本为空，无法确认音频是否包含目标内容。"],
            suggestions=["建议重试生成或更换 ASR 引擎后再校对。"],
            result_id=result_id,
            transcription_id=transcription_id,
            asr_engine_id=asr_engine_id,
        )

    segment_results = [_evaluate_segment(index, part, normalized_transcript) for index, part in enumerate(split_expected_text(expected), start=1)]
    missing = [item for item in segment_results if item.status == "failed"]
    coverage = _coverage(normalized_expected, normalized_transcript)
    similarity = difflib.SequenceMatcher(None, normalized_expected, normalized_transcript).ratio()

    if missing or coverage < 0.78:
        status = "failed"
        warnings.append("检测到原文中有句子或片段没有被转录文本充分覆盖。")
        suggestions.append("建议重试失败段落；如果原文较长，先分段生成再逐段校对。")
    elif any(item.status == "warning" for item in segment_results) or coverage < 0.9:
        status = "warning"
        warnings.append("整体内容基本覆盖，但存在轻微差异，建议人工快速复听。")
        suggestions.append("如果这是正式输出，建议缩短段落后重新生成重点句。")
    else:
        status = "passed"
        suggestions.append("转录内容覆盖原始文本，可以继续使用或进入合并流程。")

    return TTSVerificationResponse(
        status=status,
        coverage=round(coverage, 4),
        similarity=round(similarity, 4),
        expected_text=expected,
        transcript_text=transcript,
        normalized_expected=normalized_expected,
        normalized_transcript=normalized_transcript,
        missing_segments=missing,
        segment_results=segment_results,
        warnings=warnings,
        suggestions=suggestions,
        result_id=result_id,
        transcription_id=transcription_id,
        asr_engine_id=asr_engine_id,
    )


def normalize_text(text: str) -> str:
    return _PUNCT_RE.sub("", text).lower()


def split_expected_text(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_RE.split(text.strip()) if part.strip()]
    if not parts and text.strip():
        parts = [text.strip()]
    merged: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{part}" if current else part
        if current and len(normalize_text(candidate)) > 80:
            merged.append(current)
            current = part
        else:
            current = candidate
        if len(normalize_text(current)) >= 30:
            merged.append(current)
            current = ""
    if current:
        merged.append(current)
    return merged


def _evaluate_segment(index: int, text: str, normalized_transcript: str) -> TTSVerificationSegment:
    normalized = normalize_text(text)
    coverage = 1.0 if normalized and normalized in normalized_transcript else _coverage(normalized, normalized_transcript)
    if coverage >= 0.86:
        status = "passed"
    elif coverage >= 0.68:
        status = "warning"
    else:
        status = "failed"
    return TTSVerificationSegment(
        index=index,
        expected_text=text,
        normalized_expected=normalized,
        coverage=round(coverage, 4),
        status=status,
    )


def _coverage(expected: str, transcript: str) -> float:
    if not expected:
        return 0.0
    if expected in transcript:
        return 1.0
    matcher = difflib.SequenceMatcher(None, expected, transcript)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return max(0.0, min(1.0, matched / len(expected)))


def _segments_for_empty_transcript(expected_text: str) -> list[TTSVerificationSegment]:
    return [
        TTSVerificationSegment(
            index=index,
            expected_text=part,
            normalized_expected=normalize_text(part),
            coverage=0.0,
            status="failed",
        )
        for index, part in enumerate(split_expected_text(expected_text), start=1)
    ]
