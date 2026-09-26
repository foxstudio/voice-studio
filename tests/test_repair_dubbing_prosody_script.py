"""Read-only coverage for the dubbing prosody repair suggestion script.

The script may suggest punctuation/timing-based cuts, but it must never submit
a staged split or treat a suggestion as a confirmed semantic decision.  Fixed
fictional data only; no server, project or audio is touched.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_dubbing_prosody.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "repair_dubbing_prosody",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit() -> dict:
    return {
        "expected_spoken_text": "甲乙，丙丁",
        "aligned_words": [
            {"word_id": "w1", "text": "甲", "start_ms": 0, "end_ms": 300},
            {"word_id": "w2", "text": "乙", "start_ms": 400, "end_ms": 500},
            {"word_id": "w3", "text": "丙", "start_ms": 700, "end_ms": 900},
            {"word_id": "w4", "text": "丁", "start_ms": 1000, "end_ms": 1200},
        ],
        "boundaries": [
            {
                "boundary_id": "w2:w3",
                "left_text": "乙",
                "right_text": "丙",
                "left_word_id": "w2",
                "right_word_id": "w3",
                "final_gap_ms": 40,
            }
        ],
    }


def _clip() -> dict:
    return {
        "clip_id": "clip_1",
        "track_id": "dub",
        "candidate_id": "cand_1",
        "dubbing_group_id": "g1",
        "target_subtitle_ids": ["s1"],
        "start_ms": 0,
        "end_ms": 2000,
        "source_start_ms": 0,
        "source_end_ms": 2000,
    }


def test_plan_repair_returns_a_suggestion_without_writing():
    module = _load_module()
    plans = module.plan_repair(_audit(), _clip())

    assert plans
    plan = plans[0]
    assert len(plan["slices"]) == 2
    assert plan["cuts"][0]["punctuation_class"] == "comma"


def test_repair_group_only_suggests_and_never_splits(monkeypatch):
    module = _load_module()
    paths: list[str] = []

    def fake_api(_base, path, *_a, **_k):
        paths.append(path)
        if path.endswith("timeline-projection"):
            return {"timeline_clips": [_clip()]}
        return _audit()

    monkeypatch.setattr(module, "api", fake_api)
    report = module.repair_group(
        "http://x",
        "p",
        "g1",
        min_gap_ms=300,
        max_join_gap_ms=100,
    )

    assert report["result"] == "suggested"
    assert "response" not in report
    assert not any("staged-split" in path for path in paths)
    assert not any("workspace-revision" in path for path in paths)


def test_apply_flag_is_refused_with_migration(monkeypatch, capsys):
    module = _load_module()

    def forbidden(*_a, **_k):
        raise AssertionError("--apply must not reach the API")

    monkeypatch.setattr(module, "api", forbidden)
    code = module.main(["--project", "p", "--apply"])

    assert code == 1
    assert "废止" in capsys.readouterr().err


def test_main_prints_pending_review_suggestions(monkeypatch, capsys):
    module = _load_module()

    def fake_api(_base, path, *_a, **_k):
        if path.endswith("timeline-projection"):
            return {"timeline_clips": [_clip()]}
        return _audit()

    monkeypatch.setattr(module, "api", fake_api)
    assert module.main(["--project", "p", "--group", "g1"]) == 0
    output = capsys.readouterr().out
    assert "待语义核对建议" in output
    assert "未写入任何内容" in output
