from __future__ import annotations

import re


_DIGITS = "零一二三四五六七八九"
_SECTION_UNITS = ["", "万", "亿"]
_PARENTHETICAL_ANNOTATION_PATTERN = re.compile(r"[（(]\s*([^()（）]{1,24}?)\s*[）)]")
_BILINGUAL_TERM_LABEL_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9+./&-]{1,15}")


def clean_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.replace("，,", "，").replace("。。", "。")
    cleaned = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", cleaned)
    return cleaned


def split_sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*", text) if item.strip()]


def normalize_spoken_numbers(text: str) -> str:
    normalized = clean_text(text)
    normalized = re.sub(r"(\d+(?:\.\d+)?)\s*%", lambda m: f"百分之{_number_to_chinese(m.group(1))}", normalized)
    normalized = re.sub(r"(?<!\d)(\d{4})\s*年", lambda m: f"{_digits_to_chinese(m.group(1))}年", normalized)
    normalized = re.sub(
        r"(\d+(?:\.\d+)?)\s*[Kk](?=$|[\s\u4e00-\u9fff，。！？、,.!?;:])",
        lambda m: f"{_number_to_chinese(m.group(1))}开",
        normalized,
    )
    normalized = re.sub(r"\d+(?:\.\d+)?", lambda m: _number_to_chinese(m.group(0)), normalized)
    normalized = re.sub(
        r"([零〇一二两三四五六七八九十百千万亿点]+)\s*[Kk](?=$|[\s\u4e00-\u9fff，。！？、,.!?;:])",
        r"\1开",
        normalized,
    )
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    return _ensure_sentence_punctuation(normalized)


def normalize_tts_pronunciation(text: str) -> str:
    """Normalize localized Chinese copy into deterministic TTS pronunciation text."""
    normalized = str(text or "").strip()
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(
        r"(\d+(?:\.\d+)?)\s*%",
        lambda match: f"百分之{_number_to_chinese(match.group(1))}",
        normalized,
    )
    normalized = re.sub(
        r"(?<!\d)(\d{4})\s*年",
        lambda match: f"{_digits_to_chinese(match.group(1))}年",
        normalized,
    )
    normalized = re.sub(
        r"(\d+(?:\.\d+)?)\s*[Kk](?=$|[\s\u4e00-\u9fff，。！？、,.!?;:])",
        lambda match: f"{_number_to_chinese(match.group(1))} K",
        normalized,
    )
    normalized = re.sub(r"\d+(?:\.\d+)?", lambda match: _number_to_chinese(match.group(0)), normalized)
    normalized = re.sub(
        r"([零〇一二两三四五六七八九十百千万亿点]+)\s*[Kk](?=$|[\s\u4e00-\u9fff，。！？、,.!?;:])",
        r"\1 K",
        normalized,
    )
    normalized = re.sub(
        r"(?<![A-Za-z])([A-Z]{2,})(?![A-Za-z])",
        lambda match: " ".join(match.group(1)),
        normalized,
    )
    normalized = re.sub(r"([\u4e00-\u9fff])([A-Za-z])", r"\1 \2", normalized)
    normalized = re.sub(r"([A-Za-z])([\u4e00-\u9fff])", r"\1 \2", normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", normalized)
    return re.sub(r"[ \t]{2,}", " ", normalized)


def canonicalize_bilingual_term_annotations(text: str) -> str:
    """Keep compact professional acronyms as full-width subtitle annotations."""

    def replace(match: re.Match[str]) -> str:
        label = _bilingual_term_label(match.group(1))
        return f"（{label}）" if label else match.group(0)

    normalized = _PARENTHETICAL_ANNOTATION_PATTERN.sub(replace, str(text or ""))
    normalized = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=（)", "", normalized)
    normalized = re.sub(r"(?<=）)\s+(?=[\u3400-\u9fff])", "", normalized)
    return normalized


def bilingual_term_annotation_spans(text: str) -> list[tuple[int, int]]:
    """Return only parenthetical spans that contain a compact Latin acronym."""
    return [
        (match.start(), match.end())
        for match in _PARENTHETICAL_ANNOTATION_PATTERN.finditer(str(text or ""))
        if _bilingual_term_label(match.group(1))
    ]


def strip_bilingual_term_annotations(text: str) -> str:
    """Remove subtitle-only acronym annotations while preserving ordinary asides."""

    def replace(match: re.Match[str]) -> str:
        return "" if _bilingual_term_label(match.group(1)) else match.group(0)

    normalized = _PARENTHETICAL_ANNOTATION_PATTERN.sub(replace, str(text or ""))
    normalized = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", normalized)
    normalized = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", normalized)
    return normalized.strip()


def _bilingual_term_label(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or ""))
    if not _BILINGUAL_TERM_LABEL_PATTERN.fullmatch(compact) or not re.search(r"[A-Z]", compact):
        return ""
    return compact


def _number_to_chinese(value: str) -> str:
    if "." in value:
        integer, decimal = value.split(".", 1)
        return f"{_integer_to_chinese(int(integer or '0'))}点{_digits_to_chinese(decimal)}"
    return _integer_to_chinese(int(value))


def _digits_to_chinese(value: str) -> str:
    return "".join(_DIGITS[int(char)] for char in value if char.isdigit())


def _integer_to_chinese(value: int) -> str:
    if value == 0:
        return _DIGITS[0]
    sections: list[int] = []
    while value:
        sections.append(value % 10000)
        value //= 10000

    parts: list[str] = []
    need_zero = False
    for index in range(len(sections) - 1, -1, -1):
        section = sections[index]
        if section == 0:
            need_zero = bool(parts)
            continue
        if need_zero or (parts and section < 1000):
            parts.append("零")
        parts.append(_section_to_chinese(section))
        parts.append(_SECTION_UNITS[index])
        need_zero = section < 1000
    result = "".join(parts).rstrip("零")
    return result[1:] if result.startswith("一十") else result


def _section_to_chinese(section: int) -> str:
    chars: list[str] = []
    zero = False
    for divisor, unit in [(1000, "千"), (100, "百"), (10, "十"), (1, "")]:
        digit = section // divisor
        section %= divisor
        if digit == 0:
            if chars:
                zero = True
            continue
        if zero:
            chars.append("零")
            zero = False
        chars.append(f"{_DIGITS[digit]}{unit}")
    result = "".join(chars)
    return result[1:] if result.startswith("一十") and len(result) > 2 else result


def _ensure_sentence_punctuation(text: str) -> str:
    if not text:
        return text
    if re.search(r"[。！？!?….]$", text):
        return text
    return f"{text}。"
