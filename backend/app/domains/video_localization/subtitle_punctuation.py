"""Shared punctuation rules for Simplified Chinese display subtitles."""

from __future__ import annotations

import re


_ELLIPSIS = re.compile(r"(?:\.{3,}|…+|⋯+)")
_QUESTION_OR_EXCLAMATION = re.compile(r"[!?！？]+")
_REMOVED_DISPLAY_PUNCTUATION = frozenset(
    ',，。;；:：、?？…⋯"“”「」『』—–－―'
)
_OPENING_MARKS = "“‘「『【《（([{"
_CLOSING_MARKS = "”’」』】》）)]}"
_ASCII_TERM = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+#/+%\-]*"
    r"(?:[ \t]+[A-Za-z0-9][A-Za-z0-9._+#/+%\-]*)*"
)
_SPOKEN_NUMBER = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?"
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_SMALL_NUMBER_UNITS = {"十": 10, "百": 100, "千": 1_000}
_LARGE_NUMBER_UNITS = {"万": 10_000, "亿": 100_000_000}
_DISPLAY_NUMBER_SUFFIXES = tuple(
    sorted(
        {
            "公里每小时",
            "千瓦时",
            "摄氏度",
            "毫秒",
            "分钟",
            "公里",
            "厘米",
            "毫米",
            "千克",
            "毫克",
            "毫升",
            "世纪",
            "镜头",
            "点数",
            "小时",
            "个月",
            "年份",
            "版本",
            "帧",
            "秒",
            "年",
            "月",
            "日",
            "天",
            "周",
            "岁",
            "人",
            "个",
            "次",
            "倍",
            "成",
            "批",
            "条",
            "张",
            "套",
            "部",
            "场",
            "章",
            "集",
            "期",
            "页",
            "项",
            "种",
            "轮",
            "元",
            "美元",
            "米",
        },
        key=len,
        reverse=True,
    )
)
_GRAMMATICAL_NUMBER_PREFIXES = frozenset("这那每同另下之几数")
_SINGLE_DIGIT_DISPLAY_SUFFIXES = (
    "公里每小时",
    "千瓦时",
    "摄氏度",
    "毫秒",
    "分钟",
    "公里",
    "厘米",
    "毫米",
    "千克",
    "毫克",
    "毫升",
    "世纪",
    "点数",
    "小时",
    "个月",
    "年份",
    "版本",
    "帧",
    "秒",
    "年",
    "月",
    "日",
    "天",
    "周",
    "岁",
    "倍",
    "成",
    "批",
    "元",
    "美元",
    "米",
)


def normalize_display_subtitle_punctuation(text: str) -> str:
    """Normalize punctuation for readable on-screen subtitles, not TTS text."""

    normalized_lines = [
        _normalize_line(source_line)
        for source_line in (text.splitlines() or [text])
    ]
    return "\n".join(line for line in normalized_lines if line)


def _normalize_line(source: str) -> str:
    line = _ELLIPSIS.sub("…", source.strip())
    line = _QUESTION_OR_EXCLAMATION.sub(
        _single_question_or_exclamation,
        line,
    )
    line = normalize_display_subtitle_numbers(line)
    line = re.sub(r"[:：](?=[“\"「『])", " ", line)
    output: list[str] = []
    for index, character in enumerate(line):
        previous = line[index - 1] if index > 0 else ""
        following = line[index + 1] if index + 1 < len(line) else ""
        if character == ".":
            if _is_semantic_ascii_period(previous, following):
                output.append(character)
            else:
                output.append(" ")
            continue
        if character == "," and previous.isdigit() and following.isdigit():
            output.append(character)
            continue
        if character in _REMOVED_DISPLAY_PUNCTUATION:
            output.append(" ")
            continue
        output.append(character)

    cleaned = re.sub(r"[ \t]+", " ", "".join(output)).strip()
    cleaned = re.sub(
        rf"\s+([{re.escape(_CLOSING_MARKS)}])",
        r"\1 ",
        cleaned,
    )
    cleaned = re.sub(
        rf"([{re.escape(_OPENING_MARKS)}])\s+",
        r"\1",
        cleaned,
    )
    cleaned = re.sub(r"\s+([!?！？、…])", r"\1", cleaned)
    cleaned = _space_cjk_ascii_terms(cleaned)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _single_question_or_exclamation(match: re.Match[str]) -> str:
    value = match.group(0)
    question = any(character in "?？" for character in value)
    fullwidth = any(character in "？！" for character in value)
    if question:
        return "？" if fullwidth else "?"
    return "！" if fullwidth else "!"


def _is_semantic_ascii_period(previous: str, following: str) -> bool:
    if previous.isdigit() and following.isdigit():
        return True
    return (
        previous.isascii()
        and following.isascii()
        and previous.isalnum()
        and following.islower()
    )


def _space_cjk_ascii_terms(text: str) -> str:
    """Separate general Latin/number terms from adjacent CJK text."""

    def add_boundary_spaces(match: re.Match[str]) -> str:
        value = match.group(0)
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        return (
            (" " if _is_cjk_ideograph(before) else "")
            + value
            + (" " if _is_cjk_ideograph(after) else "")
        )

    return _ASCII_TERM.sub(add_boundary_spaces, text)


def normalize_display_subtitle_numbers(text: str) -> str:
    """Use Arabic digits for high-confidence on-screen numeric phrases.

    Spoken/TTS copy intentionally keeps Chinese pronunciation.  This display
    projection converts only contexts that are unambiguously numeric: a
    measure/count suffix, an ordinal classifier, a Latin product version, or
    a Chinese digit sequence followed by an ASCII unit.  Lexical phrases such
    as ``一见钟情`` and ``一点点`` remain untouched.
    """

    source = str(text or "")

    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        before = source[: match.start()]
        after = source[match.end() :]
        stripped_before = before.rstrip()
        stripped_after = after.lstrip()
        has_numeric_suffix = any(
            stripped_after.startswith(suffix)
            for suffix in _DISPLAY_NUMBER_SUFFIXES
        )
        is_ordinal = (
            stripped_before.endswith("第") and has_numeric_suffix
        )
        follows_latin_term = bool(
            re.search(r"[A-Za-z][A-Za-z0-9._+#/+%\-]*\s*$", before)
        )
        precedes_ascii_unit = bool(
            re.match(r"[A-Za-z](?![A-Za-z])", stripped_after)
        )
        has_explicit_magnitude = any(
            character in value for character in "百千万亿"
        )
        magnitude_at_boundary = has_explicit_magnitude and (
            not stripped_after
            or stripped_after[0] in "，。！？、,.!?;；:：)）]】"
        )
        previous_character = stripped_before[-1:] or ""
        if previous_character in _GRAMMATICAL_NUMBER_PREFIXES:
            return value
        single_plain_digit = (
            len(value) == 1 and value in _CHINESE_DIGITS
        )
        if (
            single_plain_digit
            and not is_ordinal
            and not follows_latin_term
            and not precedes_ascii_unit
            and not any(
                stripped_after.startswith(suffix)
                for suffix in _SINGLE_DIGIT_DISPLAY_SUFFIXES
            )
        ):
            return value
        if not (
            has_numeric_suffix
            or is_ordinal
            or follows_latin_term
            or precedes_ascii_unit
            or magnitude_at_boundary
        ):
            return value
        parsed = _parse_spoken_chinese_number(value)
        if parsed is None:
            return value
        if follows_latin_term and before and not before[-1].isspace():
            return f" {parsed}"
        return parsed

    normalized = _SPOKEN_NUMBER.sub(replace, source)
    return re.sub(
        r"\b(\d{3,4})\s+([PpKk])\b",
        r"\1\2",
        normalized,
    )


def _parse_spoken_chinese_number(value: str) -> str | None:
    integer_text, separator, decimal_text = value.partition("点")
    integer = _parse_chinese_integer(integer_text)
    if integer is None:
        return None
    if not separator:
        for unit in ("亿", "万"):
            if value.endswith(unit) and value != unit:
                prefix = _parse_chinese_integer(value[: -len(unit)])
                if prefix is not None:
                    return f"{prefix} {unit}"
        return str(integer)
    if not decimal_text or any(
        character not in _CHINESE_DIGITS for character in decimal_text
    ):
        return None
    decimal = "".join(
        str(_CHINESE_DIGITS[character]) for character in decimal_text
    )
    return f"{integer}.{decimal}"


def _parse_chinese_integer(value: str) -> int | None:
    if not value:
        return None
    if all(character in _CHINESE_DIGITS for character in value):
        return int(
            "".join(str(_CHINESE_DIGITS[character]) for character in value)
        )
    if any(
        character not in {
            *_CHINESE_DIGITS,
            *_SMALL_NUMBER_UNITS,
            *_LARGE_NUMBER_UNITS,
        }
        for character in value
    ):
        return None
    total = 0
    section = 0
    number = 0
    for character in value:
        if character in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[character]
            continue
        if character in _SMALL_NUMBER_UNITS:
            section += (number or 1) * _SMALL_NUMBER_UNITS[character]
            number = 0
            continue
        section += number
        number = 0
        total += (section or 1) * _LARGE_NUMBER_UNITS[character]
        section = 0
    return total + section + number


def _is_cjk_ideograph(character: str) -> bool:
    if not character:
        return False
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x323AF
    )


__all__ = [
    "normalize_display_subtitle_numbers",
    "normalize_display_subtitle_punctuation",
]
