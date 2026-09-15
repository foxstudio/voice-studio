from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    subtitle_punctuation,
    subtitle_segmentation,
)


def test_chinese_film_subtitle_punctuation_keeps_only_useful_display_marks():
    source = (
        "先说一句，继续。问题：他说：“真的吗？！”——别停"
        "A、B、C……"
    )

    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == "先说一句 继续 问题 他说 真的吗 别停 A B C"
    )


@pytest.mark.parametrize(
    "source",
    [
        "前半句：后半句",
        "前半句——后半句",
        "前半句—后半句",
        "前半句–后半句",
        "前半句－后半句",
        "前半句―后半句",
    ],
)
def test_colons_and_typographic_dashes_are_not_displayed(source: str):
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == "前半句 后半句"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("这是GPT Image 2效果", "这是 GPT Image 2 效果"),
        ("用2FA验证GPT-4o模型", "用 2FA 验证 GPT-4o 模型"),
        ("打开C++项目和v2.0版本", "打开 C++ 项目和 v2.0 版本"),
        ("第2章有100人", "第 2 章有 100 人"),
        ("约30秒完成50%", "约 30 秒完成 50%"),
        ("支持2.0版本和4K画面", "支持 2.0 版本和 4K 画面"),
    ],
)
def test_general_ascii_terms_are_spaced_from_adjacent_chinese(
    source: str,
    expected: str,
):
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == expected
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("这个二十世纪初的故事", "这个 20 世纪初的故事"),
        ("第二十世纪的第一个版本", "第 20 世纪的第 1 个版本"),
        ("一九零五年启程", "1905 年启程"),
        (
            "模型选Seedance二点五，时长三十秒，一零八零P",
            "模型选 Seedance 2.5 时长 30 秒 1080P",
        ),
        ("一共十六个镜头，高三倍", "一共 16 个镜头 高 3 倍"),
    ],
)
def test_spoken_chinese_numbers_use_screen_display_forms(
    source: str,
    expected: str,
):
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == expected
    )


def test_display_number_normalization_preserves_non_numeric_chinese_phrases():
    source = (
        "一见钟情，一点点消失，唯一变化，一样好，二十出头，"
        "这一场，人物之一，每一帧，几千种"
    )

    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == (
            "一见钟情 一点点消失 唯一变化 一样好 二十出头 "
            "这一场 人物之一 每一帧 几千种"
        )
    )


def test_large_chinese_magnitude_keeps_compact_screen_unit():
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(
            "累计播放量接近五亿"
        )
        == "累计播放量接近 5 亿"
    )


def test_small_ordinary_chinese_counts_remain_natural_words():
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(
            "新建一个项目 两人同行 三张视图"
        )
        == "新建一个项目 两人同行 三张视图"
    )


def test_unneeded_ascii_quotes_are_removed_but_apostrophes_are_kept():
    source = "He said \"don't stop?\""

    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == "He said don't stop"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("真的吗？？", "真的吗"),
        ("真的吗？！", "真的吗"),
        ("太好了！！", "太好了！"),
        ("Really!?", "Really"),
        ("Stop!!", "Stop!"),
    ],
)
def test_question_and_exclamation_clusters_never_stack(
    source: str,
    expected: str,
):
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == expected
    )


def test_numeric_and_identifier_punctuation_is_not_corrupted():
    source = "Version 2.0，1,000 人访问 example.com。"

    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == "Version 2.0 1,000 人访问 example.com"
    )


def test_sentence_period_before_an_uppercase_word_is_removed():
    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(
            "Stop.Next"
        )
        == "Stop Next"
    )


def test_removed_commas_and_periods_become_one_space_per_line():
    source = "  先说一句，  继续。。下一句,again.  \n第二行，结束。 "

    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(source)
        == "先说一句 继续 下一句 again\n第二行 结束"
    )


def test_asr_and_localization_display_paths_share_the_same_normalization():
    source = "先说一句，继续。真的吗？！A、B……"
    expected = "先说一句 继续 真的吗 A B"

    assert (
        subtitle_punctuation.normalize_display_subtitle_punctuation(
            source
        )
        == expected
    )
    assert subtitle_segmentation._normalize_subtitle_punctuation(source) == (
        expected,
        True,
    )
