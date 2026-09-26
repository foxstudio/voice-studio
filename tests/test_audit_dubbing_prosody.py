"""Behavioral coverage for the dubbing prosody heuristic audit.

Pins the checked / unchecked split, same-candidate coverage, and the CLI
contract that an unchecked or failed audit never returns success.  All data is
fictional; no server, project or audio is touched.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dubbing_prosody.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "audit_dubbing_prosody",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit(text: str, *, left: str, right: str, gap: int) -> dict:
    return {
        "expected_spoken_text": text,
        "aligned_words": [
            {"word_id": "w0", "text": text or "x", "start_ms": 0, "end_ms": 1}
        ],
        "boundaries": [
            {
                "boundary_id": f"{left}:{right}",
                "left_word_id": left,
                "right_word_id": right,
                "left_text": left,
                "right_text": right,
                "left_source_start_ms": 0,
                "left_source_end_ms": 0,
                "right_source_start_ms": gap,
                "right_source_end_ms": gap + 1,
                "source_relation": "separated",
                "source_gap_ms": gap,
                "source_overlap_ms": 0,
                "final_relation": "separated" if gap else "touching",
                "final_gap_ms": gap,
                "final_overlap_ms": 0,
                "left_render_status": "fully_retained",
                "right_render_status": "fully_retained",
            }
        ],
    }


def _projection(clips: list[dict]) -> dict:
    return {"timeline_clips": [{"track_id": "dub", **clip} for clip in clips]}


def test_candidate_api_failure_is_unchecked_not_passed(monkeypatch):
    module = _load_module()
    calls = {"n": 0}

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "甲",
                    }
                ]
            )
        calls["n"] += 1
        raise module.ApiError("HTTP 404: candidate not found")

    monkeypatch.setattr(module, "api", fake_api)
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["offender_count"] == 0
    assert report["checked_clip_count"] == 0
    assert report["unchecked_clip_count"] == 1
    assert report["status"] == "incomplete"
    assert "HTTP 404" in report["unchecked"][0]["detail"]
    assert calls["n"] == 1


def test_missing_candidate_id_is_unchecked(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "api",
        lambda *_a, **_k: _projection(
            [
                {
                    "clip_id": "clip_1",
                    "candidate_id": "",
                    "dubbing_group_id": "group_1",
                    "tts_target_text": "甲",
                }
            ]
        ),
    )
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["unchecked"][0]["reason"] == "missing_candidate_id"
    assert report["status"] == "incomplete"


def test_same_candidate_across_clips_is_fetched_once(monkeypatch):
    module = _load_module()
    candidate_calls = {"n": 0}

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    },
                    {
                        "clip_id": "clip_2",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_2",
                        "tts_target_text": "",
                    },
                ]
            )
        candidate_calls["n"] += 1
        return _audit("甲，乙", left="甲", right="乙", gap=0)

    monkeypatch.setattr(module, "api", fake_api)
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert candidate_calls["n"] == 1
    assert report["clip_count"] == 2
    assert report["checked_clip_count"] == 2
    assert report["group_count"] == 2


def test_heuristic_hit_reports_suspicion_and_exit_two(monkeypatch, capsys):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return _audit("甲乙", left="甲", right="乙", gap=500)

    monkeypatch.setattr(module, "api", fake_api)
    assert module.main(["--project", "p1"]) == 2
    output = capsys.readouterr().out
    assert "待语义评估" in output
    assert "该连续却断开" not in output


def test_missing_evidence_is_unchecked(monkeypatch):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return {"expected_spoken_text": "", "boundaries": []}

    monkeypatch.setattr(module, "api", fake_api)
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["unchecked"][0]["reason"] == "missing_evidence"
    assert report["status"] == "incomplete"


def test_empty_boundary_entries_are_not_checked(monkeypatch):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return {
            "expected_spoken_text": "甲乙",
            "aligned_words": [
                {"word_id": "w0", "text": "甲", "start_ms": 0, "end_ms": 100}
            ],
            "boundaries": [{}],
        }

    monkeypatch.setattr(module, "api", fake_api)
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["checked_clip_count"] == 0
    assert report["unchecked"][0]["reason"] == "missing_evidence"
    assert "缺少" in report["unchecked"][0]["detail"]


def test_empty_payload_is_incomplete(monkeypatch):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return {}

    monkeypatch.setattr(module, "api", fake_api)
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["checked_clip_count"] == 0
    assert report["status"] == "incomplete"


def test_single_word_candidate_without_boundaries_is_checked(monkeypatch):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return {
            "expected_spoken_text": "啊",
            "aligned_words": [
                {"word_id": "w0", "text": "啊", "start_ms": 0, "end_ms": 100}
            ],
            "boundaries": [],
        }

    monkeypatch.setattr(module, "api", fake_api)
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["checked_clip_count"] == 1
    assert report["no_adjacent_boundary_clip_count"] == 1
    assert report["unchecked_clip_count"] == 0
    assert report["status"] == "checked"


def _single_group_api(audit):
    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return audit

    return fake_api


def test_multiple_words_without_boundaries_is_unchecked(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "api",
        _single_group_api(
            {
                "expected_spoken_text": "啊哦",
                "aligned_words": [
                    {"word_id": "w0", "text": "啊", "start_ms": 0, "end_ms": 100},
                    {"word_id": "w1", "text": "哦", "start_ms": 100, "end_ms": 200},
                ],
                "boundaries": [],
            }
        ),
    )
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["checked_clip_count"] == 0
    assert report["unchecked"][0]["reason"] == "missing_evidence"
    assert "2 个词" in report["unchecked"][0]["detail"]


def test_malformed_aligned_word_is_unchecked(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "api",
        _single_group_api(
            {
                "expected_spoken_text": "啊",
                "aligned_words": [{}, {}],
                "boundaries": [],
            }
        ),
    )
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["checked_clip_count"] == 0
    assert "不合法" in report["unchecked"][0]["detail"]


def test_duplicate_boundary_id_is_unchecked(monkeypatch):
    module = _load_module()
    word = {"word_id": "w0", "text": "啊", "start_ms": 0, "end_ms": 100}
    boundary = {
        "boundary_id": "w0:w0",
        "left_word_id": "w0",
        "right_word_id": "w0",
        "left_text": "啊",
        "right_text": "啊",
        "final_gap_ms": 500,
        "final_overlap_ms": 0,
    }
    monkeypatch.setattr(
        module,
        "api",
        _single_group_api(
            {
                "expected_spoken_text": "啊啊",
                "aligned_words": [
                    word,
                    dict(word, word_id="w1", start_ms=200, end_ms=300),
                ],
                "boundaries": [boundary, dict(boundary)],
            }
        ),
    )
    report = module.build_report("http://x", "p1", min_gap_ms=300, max_join_gap_ms=100)

    assert report["checked_clip_count"] == 0
    assert "重复" in report["unchecked"][0]["detail"]


def test_no_dub_clips_is_incomplete_exit_one(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "api", lambda *_a, **_k: {"timeline_clips": []})

    assert module.main(["--project", "p1"]) == 1


def test_all_checked_and_clean_exits_zero(monkeypatch):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return _audit("甲，乙", left="甲", right="乙", gap=50)

    monkeypatch.setattr(module, "api", fake_api)
    assert module.main(["--project", "p1"]) == 0


def test_json_keeps_compatible_fields(monkeypatch, capsys):
    module = _load_module()

    def fake_api(_base, path, **_kwargs):
        if path.endswith("timeline-projection"):
            return _projection(
                [
                    {
                        "clip_id": "clip_1",
                        "candidate_id": "cand_1",
                        "dubbing_group_id": "group_1",
                        "tts_target_text": "",
                    }
                ]
            )
        return _audit("甲乙", left="甲", right="乙", gap=500)

    monkeypatch.setattr(module, "api", fake_api)
    assert module.main(["--project", "p1", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    for field in (
        "project_id",
        "min_gap_ms",
        "max_join_gap_ms",
        "clip_count",
        "group_count",
        "offender_count",
        "groups",
    ):
        assert field in payload
    assert payload["assessment"] == "heuristic_pending_semantic_review"
