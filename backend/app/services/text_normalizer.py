from __future__ import annotations

import re


_DIGITS = "零一二三四五六七八九"
_SECTION_UNITS = ["", "万", "亿"]


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
    normalized = re.sub(r"\d+(?:\.\d+)?", lambda m: _number_to_chinese(m.group(0)), normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    return _ensure_sentence_punctuation(normalized)


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
    return result[1:] if result.startswith("一十") and len(result) > 2 else result


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
