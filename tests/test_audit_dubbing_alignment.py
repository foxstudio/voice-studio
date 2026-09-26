"""Behavioral coverage for the dubbing alignment diagnostic audit.

Pins that the measurement is a diagnostic against the intermediate translation
subtitle start, that missing/unmatched/out-of-slice evidence never counts as a
pass, and that the CLI separates incomplete from findings.  Fixed fictional
data only; no server, project or audio is touched.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dubbing_alignment.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "audit_dubbing_alignment",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clip(**overrides) -> dict:
    clip = {
        "clip_id": "clip_1",
        "track_id": "dub",
        "candidate_id": "cand_1",
        "dubbing_group_id": "group_1",
        "target_subtitle_ids": ["s1", "s2"],
        "start_ms": 0,
        "end_ms": 3000,
        "source_start_ms": 0,
        "source_end_ms": 3000,
        "target_end_ms": 3000,
    }
    clip.update(overrides)
    return clip


def _audit(words: list[tuple[str, int, int]]) -> dict:
    return {
        "aligned_words": [
            {"word_id": f"w{i}", "text": text, "start_ms": start, "end_ms": end}
            for i, (text, start, end) in enumerate(words)
        ]
    }


def _run(monkeypatch, clips, subtitles, audit=None, *, threshold=250):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return {"timeline_clips": clips}
        if "semantic-boundaries" in path:
            if isinstance(audit, Exception):
                raise audit
            return audit if audit is not None else {}
        return {"video_localization": {"localized_subtitles": subtitles}}

    monkeypatch.setattr(module, "api", fake_api)
    return module, module.build_report("http://x", "p1", threshold_ms=threshold)


def test_matched_within_threshold_is_checked_and_passes(monkeypatch):
    _, report = _run(
        monkeypatch,
        [_clip()],
        [
            {"subtitle_id": "s1", "text": "甲", "start_ms": 0},
            {"subtitle_id": "s2", "text": "乙", "start_ms": 1000},
        ],
        _audit([("甲", 0, 100), ("乙", 1000, 1100)]),
    )

    assert report["multi_subtitle_clip_count"] == 1
    assert report["checked_clip_count"] == 1
    assert report["unchecked_clip_count"] == 0
    assert report["over_threshold_count"] == 0
    assert report["clips"][0]["reference_basis"] == "intermediate_localized_subtitle_start"


def test_over_threshold_is_a_reference_finding_not_a_hard_verdict(monkeypatch):
    _, report = _run(
        monkeypatch,
        [_clip()],
        [
            {"subtitle_id": "s1", "text": "甲", "start_ms": 0},
            {"subtitle_id": "s2", "text": "乙", "start_ms": 1000},
        ],
        _audit([("甲", 0, 100), ("乙", 1600, 1700)]),
    )

    assert report["checked_clip_count"] == 1
    assert report["over_threshold_count"] == 1
    assert report["clips"][0]["worst_deviation_ms"] == 600


def test_unmatchable_subtitle_is_not_reported_as_pass(monkeypatch):
    _, report = _run(
        monkeypatch,
        [_clip()],
        [
            {"subtitle_id": "s1", "text": "甲", "start_ms": 0},
            {"subtitle_id": "s2", "text": "丙", "start_ms": 1000},
        ],
        _audit([("甲", 0, 100)]),
    )

    clip = report["clips"][0]
    assert clip["checked"] is False
    assert clip["over_threshold"] is False
    assert "无法" in clip["unchecked_reason"]
    # The matched line keeps its diagnostic deviation even though the clip as a
    # whole is not covered.
    assert clip["subtitles"][0]["deviation_ms"] == 0
    assert report["status"] == "incomplete"


def test_word_outside_source_slice_is_not_a_pass(monkeypatch):
    _, report = _run(
        monkeypatch,
        [_clip(source_start_ms=1000, source_end_ms=2000)],
        [
            {"subtitle_id": "s1", "text": "甲", "start_ms": 0},
            {"subtitle_id": "s2", "text": "乙", "start_ms": 1000},
        ],
        _audit([("甲", 400, 500), ("乙", 1200, 1300)]),
    )

    clip = report["clips"][0]
    assert clip["checked"] is False
    assert "源范围" in clip["unchecked_reason"]


def test_missing_source_range_is_unchecked(monkeypatch):
    _, report = _run(
        monkeypatch,
        [_clip(source_start_ms=None, source_end_ms=None)],
        [
            {"subtitle_id": "s1", "text": "甲", "start_ms": 0},
            {"subtitle_id": "s2", "text": "乙", "start_ms": 1000},
        ],
        _audit([("甲", 0, 100), ("乙", 1000, 1100)]),
    )

    clip = report["clips"][0]
    assert clip["checked"] is False
    assert "source_start_ms" in clip["unchecked_reason"]
    assert report["status"] == "incomplete"


def test_missing_reference_start_is_not_treated_as_zero(monkeypatch):
    _, report = _run(
        monkeypatch,
        [_clip()],
        [
            {"subtitle_id": "s1", "text": "甲"},
            {"subtitle_id": "s2", "text": "乙", "start_ms": 1000},
        ],
        _audit([("甲", 0, 100), ("乙", 1000, 1100)]),
    )

    clip = report["clips"][0]
    assert clip["checked"] is False
    assert clip["subtitles"][0]["reference_start_ms"] is None
    assert "start_ms" in clip["unchecked_reason"]


def test_missing_candidate_id_is_unchecked(monkeypatch):
    _, report = _run(
        monkeypatch,
        [_clip(candidate_id="")],
        [{"subtitle_id": "s1", "text": "甲", "start_ms": 0}],
        {},
    )

    assert report["clips"][0]["checked"] is False
    assert "candidate_id" in report["clips"][0]["unchecked_reason"]


def test_candidate_api_error_is_unchecked_and_exit_one(monkeypatch, capsys):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return {"timeline_clips": [_clip()]}
        if "semantic-boundaries" in path:
            raise module.ApiError("HTTP 404: candidate not found")
        return {"video_localization": {"localized_subtitles": []}}

    monkeypatch.setattr(module, "api", fake_api)
    report = module.build_report("http://x", "p1", threshold_ms=250)
    assert report["unchecked_clip_count"] == 1
    assert "HTTP 404" in report["unchecked"][0]["detail"]

    assert module.main(["--project", "p1"]) == 1
    assert "未检查" in capsys.readouterr().out


def test_single_subtitle_clips_are_empty_scope_exit_one(monkeypatch):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return {"timeline_clips": [_clip(target_subtitle_ids=["s1"])]}
        if "semantic-boundaries" in path:
            return _audit([])
        return {"video_localization": {"localized_subtitles": []}}

    monkeypatch.setattr(module, "api", fake_api)
    assert module.main(["--project", "p1"]) == 1


def test_json_keeps_compatible_fields(monkeypatch, capsys):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return {
                "timeline_clips": [
                    _clip(target_subtitle_ids=["s1"]),
                    _clip(clip_id="clip_2"),
                ]
            }
        if "semantic-boundaries" in path:
            return _audit([("甲", 0, 100), ("乙", 1000, 1100)])
        return {
            "video_localization": {
                "localized_subtitles": [
                    {"subtitle_id": "s1", "text": "甲", "start_ms": 0},
                    {"subtitle_id": "s2", "text": "乙", "start_ms": 1000},
                ]
            }
        }

    monkeypatch.setattr(module, "api", fake_api)
    assert module.main(["--project", "p1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for field in (
        "project_id",
        "threshold_ms",
        "multi_subtitle_clip_count",
        "over_threshold_count",
        "clips",
    ):
        assert field in payload
    assert payload["reference_basis"] == "intermediate_localized_subtitle_start"
