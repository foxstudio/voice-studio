"""Local safety rules for evidence-backed proper-name normalization."""

from __future__ import annotations

import re
import unicodedata


_CURRENCY = re.compile(
    r"(?:[$€£¥￥]|\b(?:usd|eur|gbp|cny|rmb|dollars?|euros?|yuan)\b)",
    re.IGNORECASE,
)
_MEASUREMENT_WITH_NUMBER = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:"
    r"percent|percentage|times|tokens?|parameters?|"
    r"million|billion|trillion|thousand|earnings"
    r")\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TRAILING_VERSION = re.compile(
    r"(?:^|\s)v?(?P<version>\d+(?:\.\d+)*)$",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_NAME_TOKEN = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)
_NAME_CONNECTORS = {
    "&",
    "and",
    "da",
    "de",
    "del",
    "di",
    "du",
    "for",
    "la",
    "of",
    "the",
    "van",
    "von",
}


def looks_like_proper_name_candidate(value: object) -> bool:
    """Reject prices and measurements before they enter name resolution."""

    text = str(value or "").strip()
    if not 2 <= len(text) <= 160 or not _WORD.search(text):
        return False
    if _CURRENCY.search(text) or _MEASUREMENT_WITH_NUMBER.search(text):
        return False
    if len(text.split()) > 10:
        return False
    return True


def is_safe_proper_name_replacement(
    canonical_name: object,
    transcript_variant: object,
) -> bool:
    """Return whether a variant may be replaced by a canonical proper name."""

    canonical = str(canonical_name or "").strip()
    variant = str(transcript_variant or "").strip()
    if not (
        looks_like_proper_name_candidate(canonical)
        and looks_like_proper_name_candidate(variant)
    ):
        return False
    if not _looks_like_compact_name_span(variant):
        return False
    if bool(_CURRENCY.search(canonical)) != bool(_CURRENCY.search(variant)):
        return False
    canonical_numbers = set(_NUMBER.findall(canonical))
    variant_numbers = set(_NUMBER.findall(variant))
    if variant_numbers and canonical_numbers != variant_numbers:
        variant_version = _TRAILING_VERSION.search(variant)
        if variant_version is None:
            return False
        canonical_version = _TRAILING_VERSION.search(canonical)
        if (
            canonical_version is not None
            and canonical_version.group("version").split(".", 1)[0]
            != variant_version.group("version").split(".", 1)[0]
        ):
            return False
    return True


def _looks_like_compact_name_span(value: str) -> bool:
    """Reject sentence fragments while keeping compact proper-name spellings."""

    if re.search(r"[,;:!?]", value) or re.search(r"(?<!\d)\.(?:\s|$)", value):
        return False
    tokens = _NAME_TOKEN.findall(value)
    if not tokens or len(tokens) > 6:
        return False
    if len(tokens) == 1:
        return True
    for token in tokens:
        if token.casefold() in _NAME_CONNECTORS or token.replace(".", "").isdigit():
            continue
        letters = [character for character in token if character.isalpha()]
        if not letters:
            continue
        if any(character.isupper() for character in letters):
            continue
        if not any(
            "LATIN" in unicodedata.name(character, "")
            for character in letters
        ):
            continue
        return False
    return True


__all__ = [
    "is_safe_proper_name_replacement",
    "looks_like_proper_name_candidate",
]
