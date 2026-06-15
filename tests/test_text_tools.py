from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import text_normalizer  # noqa: E402


def test_normalize_spoken_numbers_converts_years_counts_and_percentages():
    text = text_normalizer.normalize_spoken_numbers("1992 年，有 130 人，增长 3.5%。")

    assert text == "一九九二年，有一百三十人，增长百分之三点五。"


def test_normalize_spoken_numbers_adds_sentence_punctuation():
    text = text_normalizer.normalize_spoken_numbers("2026年发布")

    assert text == "二零二六年发布。"
