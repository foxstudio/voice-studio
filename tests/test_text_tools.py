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


def test_normalize_spoken_numbers_uses_natural_cardinal_ten_pronunciation():
    assert text_normalizer.normalize_spoken_numbers(
        "估值 10 亿美元，增长 10%，另有 110 人和 10010 个样本"
    ) == "估值十亿美元，增长百分之十，另有一百一十人和一万零一十个样本。"


def test_normalize_tts_pronunciation_uses_natural_cardinal_ten_pronunciation():
    assert text_normalizer.normalize_tts_pronunciation(
        "估值 10 亿美元，增长 10%。"
    ) == "估值十亿美元，增长百分之十。"


def test_normalize_spoken_numbers_adds_sentence_punctuation():
    text = text_normalizer.normalize_spoken_numbers("2026年发布")

    assert text == "二零二六年发布。"


def test_normalize_spoken_numbers_uses_chinese_pronunciation_for_4k():
    assert text_normalizer.normalize_spoken_numbers("Seedance 2.0 输出 4K 分辨率") == "Seedance 二点零输出四开分辨率。"
    assert text_normalizer.normalize_spoken_numbers("已经改成四K画质") == "已经改成四开画质。"


def test_normalize_tts_pronunciation_separates_acronyms_and_preserves_product_names():
    source = "Seedance 2.0 是 AI 视频模型，支持 4K，2026 年提升 3.5%。"

    normalized = text_normalizer.normalize_tts_pronunciation(source)

    assert normalized == "Seedance 二点零是 A I 视频模型，支持四 K，二零二六年提升百分之三点五。"
    assert text_normalizer.normalize_tts_pronunciation(normalized) == normalized
    assert text_normalizer.normalize_tts_pronunciation("已经改成四K画质") == "已经改成四 K 画质"


def test_bilingual_term_annotations_are_removed_from_tts_without_dropping_chinese_asides():
    source = "视觉特效（VFX）和计算机生成图像 (CGI)，这段（需要复核）。"

    assert text_normalizer.strip_bilingual_term_annotations(source) == "视觉特效和计算机生成图像，这段（需要复核）。"


def test_bilingual_term_annotations_are_canonicalized_for_subtitle_display():
    assert text_normalizer.canonicalize_bilingual_term_annotations(
        "视觉特效 ( VFX ) 和计算机生成图像（CGI）"
    ) == "视觉特效（VFX）和计算机生成图像（CGI）"
