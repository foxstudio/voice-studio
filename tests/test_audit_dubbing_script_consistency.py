"""Behavioral coverage for the read-only dubbing script-consistency audit.

These tests pin how the audit separates checked cues, normalized text
differences, and cues it could not check.  They use fixed fictional data and
never touch a project, server or audio.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dubbing_script_consistency.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "audit_dubbing_script_consistency",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_opposite_meaning_with_same_character_count_is_reported():
    module = _load_module()
    draft = {
        "localized_subtitles": [
            {"subtitle_id": "cue_1", "text": "可以删除", "tts_text": "不可删除"}
        ]
    }

    result = module.audit_cues(draft)

    assert result["checked_count"] == 1
    assert result["same_count"] == 0
    assert [item["code"] for item in result["findings"]] == ["spoken_text_differs"]
    assert result["findings"][0]["subtitle_id"] == "cue_1"


def test_equivalent_whitespace_is_normalized_but_not_removed():
    module = _load_module()
    draft = {
        "localized_subtitles": [
            {"subtitle_id": "cue_1", "text": "你好 世界", "tts_text": "  你好   世界  "},
            {"subtitle_id": "cue_2", "text": "你好\u3000世界", "tts_text": "你好 世界"},
        ]
    }

    result = module.audit_cues(draft)

    assert result["findings"] == []
    assert result["checked_count"] == 2
    assert result["same_count"] == 2


def test_display_symbols_that_can_change_reading_are_kept():
    module = _load_module()
    draft = {
        "localized_subtitles": [
            {"subtitle_id": "decimal", "text": "1.5 公里", "tts_text": "15 公里"},
            {"subtitle_id": "sign", "text": "-3 度", "tts_text": "3 度"},
            {"subtitle_id": "wordgap", "text": "now here", "tts_text": "nowhere"},
            {"subtitle_id": "case", "text": "US", "tts_text": "us"},
            {"subtitle_id": "punct", "text": "你好，世界", "tts_text": "你好世界"},
            {"subtitle_id": "word_boundary", "text": "AI 技术", "tts_text": "AI技术"},
        ]
    }

    result = module.audit_cues(draft)

    assert result["same_count"] == 0
    assert result["checked_count"] == 6
    assert {item["subtitle_id"] for item in result["findings"]} == {
        "decimal",
        "sign",
        "wordgap",
        "case",
        "punct",
        "word_boundary",
    }


def test_empty_tts_text_is_legal_fallback_to_text():
    module = _load_module()
    draft = {
        "localized_subtitles": [
            {"subtitle_id": "cue_1", "text": "这是一句台词", "tts_text": None},
            {"subtitle_id": "cue_2", "text": "这也是一句", "tts_text": "  "},
        ]
    }

    result = module.audit_cues(draft)

    assert result["findings"] == []
    assert result["checked_count"] == 2
    assert result["unchecked"] == []


def test_missing_id_or_text_is_explicitly_unchecked():
    module = _load_module()
    draft = {
        "localized_subtitles": [
            {"subtitle_id": "", "text": "没有编号", "tts_text": "没有编号"},
            {"subtitle_id": "cue_2", "text": "", "tts_text": "只有朗读"},
        ]
    }

    result = module.audit_cues(draft)

    assert result["checked_count"] == 0
    assert {item["reason"] for item in result["unchecked"]} == {
        "missing_subtitle_id",
        "missing_text",
    }


def test_api_error_returns_incomplete_exit(monkeypatch, capsys):
    module = _load_module()

    def boom(*_args, **_kwargs):
        raise module.ApiError("HTTP 409: version conflict")

    monkeypatch.setattr(module, "api", boom)

    assert module.main(["--project", "p1"]) == 1
    assert "HTTP 409" in capsys.readouterr().err


def test_differences_exit_two_and_callout_pending_review(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "api",
        lambda *_a, **_k: {
            "video_localization": {
                "localized_subtitles": [
                    {"subtitle_id": "cue_1", "text": "可以删除", "tts_text": "不可删除"}
                ]
            }
        },
    )

    assert module.main(["--project", "p1"]) == 2
    output = capsys.readouterr().out
    assert "待 Agent 核对" in output
    assert "不代表音频发音正确" in output


def test_empty_scope_is_incomplete_not_success(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(module, "api", lambda *_a, **_k: {"localized_subtitles": []})

    assert module.main(["--project", "p1"]) == 1
    assert "不能宣称" in capsys.readouterr().out


def test_all_checked_and_same_exits_zero(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "api",
        lambda *_a, **_k: {
            "video_localization": {
                "localized_subtitles": [
                    {"subtitle_id": "cue_1", "text": "正常一句", "tts_text": "  正常一句  "}
                ]
            }
        },
    )

    assert module.main(["--project", "p1"]) == 0


def test_json_output_keeps_compatible_fields(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "api",
        lambda *_a, **_k: {
            "localized_subtitles": [
                {"subtitle_id": "cue_1", "text": "甲", "tts_text": "乙"}
            ]
        },
    )

    assert module.main(["--project", "p1", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    for field in ("project_id", "cue_count", "finding_count", "findings"):
        assert field in payload
    assert payload["status"] == "findings"
    assert payload["checked_count"] == 1
