from __future__ import annotations

import difflib
import re

from app.schemas.voice_studio import TTSVerificationResponse, TTSVerificationSegment

_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;…])\s*|\n+")
_PUNCT_RE = re.compile(r"[\s`~!@#$%^&*()_\-+=\[\]{}\\|;:'\",<.>/?，。！？；：、“”‘’（）《》【】—…·]+")
_VERIFICATION_CONTROL_TAG_RE = re.compile(
    r"""
    <\|(?:pause|break|silence)_\d+\|>
    |<\s*/?\s*(?:pause|break|silence|laughter|laugh|cough|sigh|sniff)(?:[_-]\d+)?\s*>
    |\[
        (?:
            pause|break|silence|cough|laughter|laugh|sigh|sniff
            |confirmation-[a-z]+
            |question-[a-z]+
            |surprise-[a-z]+
            |dissatisfaction-[a-z]+
        )
    \]
    |\(\s*(?:pause|break|silence|cough|laughter|laugh|sigh|sniff)\s*\)
    """,
    re.IGNORECASE | re.VERBOSE,
)

SEED_AUDIO_ENGINE_ID = "doubao-seed-audio-1.0"
_SEED_AUDIO_SPOKEN_RE = re.compile(r'“([^”]+)”|"([^"]+)"')
_SEED_AUDIO_NON_SPOKEN_LINE_RE = re.compile(r"^(?:音效|音乐|配乐|背景|结尾)")
_PARENTHETICAL_CONTENT_RE = re.compile(r"[（(][^（）()]*[)）]")


def seed_audio_spoken_text(prompt_text: str) -> str:
    """Return only explicit speech from a Seed Audio scene prompt.

    Seed Audio prompts commonly mix dialogue with music, sound effects, voice
    casting, emotion, and staging instructions. ASR can only verify the quoted
    speech, so non-verbal instruction lines must not count as missing words.
    """
    spoken: list[str] = []
    for raw_line in prompt_text.splitlines():
        line = raw_line.strip()
        if not line or _SEED_AUDIO_NON_SPOKEN_LINE_RE.match(line):
            continue
        for match in _SEED_AUDIO_SPOKEN_RE.finditer(line):
            value = (match.group(1) or match.group(2) or "").strip()
            if value:
                spoken.append(value)
    return "\n".join(spoken)


def verification_expected_text(
    expected_text: str,
    *,
    engine_id: str | None = None,
    filter_parenthetical_content: bool = False,
) -> str:
    if engine_id == SEED_AUDIO_ENGINE_ID:
        return seed_audio_spoken_text(expected_text)
    value = expected_text.strip()
    if filter_parenthetical_content:
        # Match the provider's user-visible “不朗读圆括号内容” control so a
        # successful filtered annotation cannot be misreported as ASR loss.
        value = _PARENTHETICAL_CONTENT_RE.sub("", value)
    return value.strip()


def skipped_non_speech_report(
    *,
    original_prompt: str,
    result_id: str | None = None,
    transcription_id: str | None = None,
    asr_engine_id: str | None = None,
) -> TTSVerificationResponse:
    return TTSVerificationResponse(
        status="skipped",
        coverage=0.0,
        similarity=0.0,
        expected_text="",
        transcript_text="",
        normalized_expected="",
        normalized_transcript="",
        warnings=["没有检测到明确需要发音的对白；纯音乐、环境音和音效内容不适用 ASR 覆盖率。"],
        suggestions=["如需校对人声，请在生成描述中用中文双引号标出要说出的内容。"],
        result_id=result_id,
        transcription_id=transcription_id,
        asr_engine_id=asr_engine_id,
    )


def verify_transcript(*, expected_text: str, transcript_text: str, result_id: str | None = None, transcription_id: str | None = None, asr_engine_id: str | None = None) -> TTSVerificationResponse:
    expected = expected_text.strip()
    transcript = transcript_text.strip()
    verification_expected = strip_verification_control_tags(expected)
    normalized_expected = normalize_text(verification_expected)
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
            missing_segments=_segments_for_empty_transcript(verification_expected),
            segment_results=_segments_for_empty_transcript(verification_expected),
            warnings=["转录文本为空，无法确认音频是否包含目标内容。"],
            suggestions=["建议重试生成或更换 ASR 引擎后再校对。"],
            result_id=result_id,
            transcription_id=transcription_id,
            asr_engine_id=asr_engine_id,
        )

    segment_results = [_evaluate_segment(index, part, normalized_transcript) for index, part in enumerate(split_expected_text(verification_expected), start=1)]
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


def strip_verification_control_tags(text: str) -> str:
    return _VERIFICATION_CONTROL_TAG_RE.sub(" ", text)


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
