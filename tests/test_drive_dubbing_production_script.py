"""Pure-logic coverage for the dubbing production driver script.

The script only orchestrates public API calls; these tests pin the parts that
decide what an Agent is shown and what the script is allowed to submit, without
starting a server or touching a project.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "drive_dubbing_production.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "drive_dubbing_production",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_surfaces_the_concrete_failure_reason():
    module = _load_module()
    run = {
        "status": "needs_attention",
        "accepted_group_count": 135,
        "group_count": 138,
        "deferred_group_count": 0,
        "attention_group_count": 3,
        "next_action": "process_gaps",
        "next_group_id": "dubbing_group_0021",
        "groups": [
            {"group_id": "dubbing_group_0001", "stage": "accepted"},
            {
                "group_id": "dubbing_group_0021",
                "stage": "failed",
                "recommended_action": "process_gaps",
                "last_error": "请先准备人声轨，再生成字幕配音",
                "candidate_ids": ["candidate_a"],
            },
        ],
    }

    summary = module.summarize(run)

    assert summary["accepted"] == 135
    assert [item["group_id"] for item in summary["unfinished"]] == [
        "dubbing_group_0021"
    ]
    assert summary["unfinished"][0]["last_error"] == "请先准备人声轨，再生成字幕配音"


def test_candidate_aliases_normalize_prefix_and_drop_duplicates():
    module = _load_module()
    group = {
        "candidate_ids": [
            "candidate_old",
            "some-task-id",
            "candidate_new",
            "new",
        ]
    }

    assert module.candidate_id_aliases(group) == [
        "candidate_new",
        "candidate_some-task-id",
        "candidate_old",
    ]
    assert module.candidate_id_aliases({"candidate_ids": []}) == []


def test_review_body_orders_dispositions_by_the_current_boundaries():
    module = _load_module()
    audit = {
        "source_revision": "a" * 64,
        "plan_revision": 7,
        "candidate_id": "candidate_x",
        "audio_sha256": "b" * 64,
        "candidate_evidence_fingerprint": "c" * 64,
        "candidate_clip_projection_fingerprint": "d" * 64,
        "boundaries": [
            {"boundary_id": "w1:w2"},
            {"boundary_id": "w2:w3"},
        ],
    }
    reviews = [
        {"boundary_id": "w2:w3", "disposition": "acceptable"},
        {"boundary_id": "w1:w2", "disposition": "acceptable"},
    ]

    body = module.build_review_body(audit, reviews)

    assert body["schema_version"] == "dubbing-candidate-review-command-v2"
    assert [item["boundary_id"] for item in body["semantic_boundary_reviews"]] == [
        "w1:w2",
        "w2:w3",
    ]
    assert body["audio_sha256"] == "b" * 64


def test_review_body_refuses_incomplete_coverage():
    module = _load_module()
    audit = {
        "source_revision": "a" * 64,
        "plan_revision": 7,
        "candidate_id": "candidate_x",
        "audio_sha256": "b" * 64,
        "candidate_evidence_fingerprint": "c" * 64,
        "candidate_clip_projection_fingerprint": "d" * 64,
        "boundaries": [{"boundary_id": "w1:w2"}],
    }

    with pytest.raises(module.ApiError, match="边界处置没有完整覆盖"):
        module.build_review_body(audit, [])
    with pytest.raises(module.ApiError, match="边界处置没有完整覆盖"):
        module.build_review_body(
            audit,
            [
                {"boundary_id": "w1:w2"},
                {"boundary_id": "w9:w10"},
            ],
        )


def test_continuous_reviews_accepts_touching_and_short_internal_gaps():
    module = _load_module()
    audit = {
        "expected_spoken_text": "先看这个，再看那个。",
        "boundaries": [
            {
                "boundary_id": "w1:w2",
                "left_text": "先看",
                "right_text": "这个",
                "final_gap_ms": 0,
                "final_relation": "touching",
                "left_render_status": "fully_retained",
                "right_render_status": "fully_retained",
            },
            {
                "boundary_id": "w2:w3",
                "left_text": "这个",
                "right_text": "再看",
                "final_gap_ms": 320,
                "final_relation": "separated",
                "left_render_status": "fully_retained",
                "right_render_status": "fully_retained",
            },
        ],
    }

    reviews = module.continuous_reviews(audit)

    assert [item["boundary_id"] for item in reviews] == ["w1:w2", "w2:w3"]
    assert reviews[0]["semantic_role"] == "continuous_phrase"
    assert reviews[1]["semantic_role"] == "semantic_boundary"
    assert all(item["disposition"] == "acceptable" for item in reviews)


def test_continuous_reviews_refuses_long_pauses_and_clipped_speech():
    module = _load_module()
    long_pause = {
        "expected_spoken_text": "先看这个再看那个。",
        "boundaries": [
            {
                "boundary_id": "w1:w2",
                "left_text": "先看",
                "right_text": "这个",
                "final_gap_ms": 1_200,
                "final_relation": "separated",
                "left_render_status": "fully_retained",
                "right_render_status": "fully_retained",
            }
        ],
    }
    clipped = {
        "expected_spoken_text": "先看这个。",
        "boundaries": [
            {
                "boundary_id": "w1:w2",
                "left_text": "先看",
                "right_text": "这个",
                "final_gap_ms": 40,
                "final_relation": "touching",
                "left_render_status": "partially_retained",
                "right_render_status": "fully_retained",
            }
        ],
    }

    with pytest.raises(module.ApiError, match="需要 Agent 判断"):
        module.continuous_reviews(long_pause)
    with pytest.raises(module.ApiError, match="字音未完整保留"):
        module.continuous_reviews(clipped)


def test_evidenced_gap_policy_accepts_proven_word_boundary_pauses():
    module = _load_module()
    boundary = {
        "boundary_id": "w3:w4",
        "left_text": "批准后",
        "right_text": "Astra",
        "final_gap_ms": 160,
        "final_relation": "separated",
        "left_render_status": "fully_retained",
        "right_render_status": "fully_retained",
        "low_energy_evidence": [
            {
                "decision_reason": "内部气口没有可靠安全切点，保留原始停顿。",
                "edit_decision": "retain",
            }
        ],
    }
    audit = {
        "expected_spoken_text": "批准后Astra 就会在",
        "boundaries": [boundary],
    }
    unexplained = dict(audit, boundaries=[{**boundary, "low_energy_evidence": []}])

    reviews = module.continuous_reviews(audit, gap_policy="evidenced")

    assert reviews[0]["semantic_role"] == "semantic_boundary"
    assert "低能量区已记录安全处理依据" in reviews[0]["reason"]
    with pytest.raises(module.ApiError, match="需要 Agent 判断"):
        module.continuous_reviews(unexplained, gap_policy="evidenced")
    with pytest.raises(module.ApiError, match="需要 Agent 判断"):
        module.continuous_reviews(audit, gap_policy="strict")


def test_review_file_accepts_a_wrapped_document(tmp_path: Path):
    module = _load_module()
    path = tmp_path / "reviews.json"
    path.write_text(
        '{"semantic_boundary_reviews": [{"boundary_id": "w1:w2"}]}',
        encoding="utf-8",
    )

    assert module.load_review_file(path) == [{"boundary_id": "w1:w2"}]


def test_parser_requires_a_project_and_a_subcommand():
    module = _load_module()
    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["status"])


def _run_with(stages: dict[str, str]) -> dict:
    groups = [
        {
            "group_id": group_id,
            "stage": stage,
            "recommended_action": "",
            "last_error": None,
            "candidate_ids": [],
        }
        for group_id, stage in stages.items()
    ]
    return {
        "status": "needs_attention",
        "accepted_group_count": sum(1 for stage in stages.values() if stage == "accepted"),
        "group_count": len(groups),
        "deferred_group_count": 0,
        "attention_group_count": 0,
        "next_action": "process_gaps",
        "next_group_id": None,
        "groups": groups,
    }


def test_run_step_reviews_waiting_groups_before_advancing():
    module = _load_module()
    plan = module.plan_run_step(
        _run_with(
            {
                "dubbing_group_0001": "accepted",
                "dubbing_group_0002": "needs_semantic_review",
                "dubbing_group_0003": "ready_to_generate",
            }
        )
    )
    assert plan == {"action": "review", "group_ids": ["dubbing_group_0002"]}


def test_run_step_advances_remaining_groups():
    module = _load_module()
    plan = module.plan_run_step(
        _run_with(
            {
                "dubbing_group_0001": "accepted",
                "dubbing_group_0002": "ready_to_generate",
                "dubbing_group_0003": "needs_gap_processing",
            }
        )
    )
    assert plan == {
        "action": "advance",
        "group_ids": ["dubbing_group_0002", "dubbing_group_0003"],
    }


def test_run_step_completes_when_every_group_is_terminal():
    module = _load_module()
    plan = module.plan_run_step(
        _run_with(
            {
                "dubbing_group_0001": "accepted",
                "dubbing_group_0002": "deferred_manual_timing",
            }
        )
    )
    assert plan == {"action": "complete", "group_ids": []}


def _audit_with_two_boundaries(first_gap: int = 0, second_gap: int = 6000) -> dict:
    def boundary(left, right, gap):
        return {
            "boundary_id": f"{left}:{right}",
            "left_text": left,
            "right_text": right,
            "final_gap_ms": gap,
            "final_relation": "separated" if gap else "touching",
            "left_render_status": "fully_retained",
            "right_render_status": "fully_retained",
            "low_energy_evidence": [],
        }

    return {
        "expected_spoken_text": "大家好。还有一点",
        "boundaries": [
            boundary("大", "家", first_gap),
            boundary("好", "还", second_gap),
        ],
    }


def test_merge_agent_decisions_fills_only_what_rules_cannot_prove():
    module = _load_module()
    audit = _audit_with_two_boundaries()
    reviews = module.merge_agent_decisions(
        audit,
        [
            {
                "boundary_id": "好:还",
                "semantic_role": "semantic_boundary",
                "disposition": "acceptable",
                "reason": "句号处的语义停顿，两侧字音完整。",
            }
        ],
        gap_policy="evidenced",
    )
    by_id = {item["boundary_id"]: item for item in reviews}
    assert set(by_id) == {"大:家", "好:还"}
    assert by_id["好:还"]["reason"].startswith("句号处")
    assert by_id["大:家"]["disposition"] == "acceptable"


def test_merge_agent_decisions_requires_a_call_for_every_open_boundary():
    module = _load_module()
    audit = _audit_with_two_boundaries()
    with pytest.raises(module.ApiError):
        module.merge_agent_decisions(audit, [], gap_policy="evidenced")


def test_merge_agent_decisions_rejects_unknown_and_duplicate_boundaries():
    module = _load_module()
    audit = _audit_with_two_boundaries()
    decision = {
        "boundary_id": "好:还",
        "semantic_role": "semantic_boundary",
        "disposition": "acceptable",
        "reason": "句号处停顿。",
    }
    with pytest.raises(module.ApiError):
        module.merge_agent_decisions(
            audit,
            [decision, dict(decision)],
            gap_policy="evidenced",
        )
    with pytest.raises(module.ApiError):
        module.merge_agent_decisions(
            audit,
            [{**decision, "boundary_id": "不:存在"}],
            gap_policy="evidenced",
        )


def test_advance_reports_capacity_recovery_without_polling_rounds(monkeypatch):
    module = _load_module()
    calls = {"execute": 0}

    def fake_execute(*_args, **_kwargs):
        calls["execute"] += 1
        return {
            "status": "needs_attention",
            "required_action": "resolve_capacity",
            "message": "超出实际时间窗",
        }

    monkeypatch.setattr(module, "read_run", lambda *_a, **_k: _run_with({"g": "needs_gap_processing"}))
    monkeypatch.setattr(module, "find_group", lambda run, group_id: {"group_id": group_id, "stage": "needs_gap_processing"})
    monkeypatch.setattr(module, "execute_group", fake_execute)
    outcome = module.advance_group("http://x", "p", "g", poll_seconds=0, max_rounds=5)
    assert outcome["result"] == "capacity_recovery_required"
    assert calls["execute"] == 1


def test_speed_baseline_prefers_explicit_then_recent_median(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "recent_formal_speeds",
        lambda *_a, **_k: [1.2, 1.25, 1.3],
    )
    assert module.resolve_ordinary_speed_baseline("http://x", "p") == 1.25
    assert module.resolve_ordinary_speed_baseline("http://x", "p", 1.1) == 1.1
    monkeypatch.setattr(module, "recent_formal_speeds", lambda *_a, **_k: [])
    assert module.resolve_ordinary_speed_baseline("http://x", "p") is None


def test_recent_formal_speeds_skips_exceptions_and_unfinished(monkeypatch):
    module = _load_module()
    payload = [
        {
            "status": "success",
            "result_id": "r1",
            "created_at": "2026-01-01T00:00:03",
            "stages": [{"kind": "generation", "parameters": {"speed": 1.25}}],
        },
        {
            "status": "success",
            "result_id": "r2",
            "created_at": "2026-01-01T00:00:02",
            "stages": [
                {
                    "kind": "generation",
                    "parameters": {
                        "speed": 1.3,
                        "content_speed_exception_reason": "容量修复",
                    },
                }
            ],
        },
        {
            "status": "needs_attention",
            "created_at": "2026-01-01T00:00:04",
            "stages": [{"kind": "generation", "parameters": {"speed": 1.3}}],
        },
        {
            "status": "success",
            "result_id": "r3",
            "created_at": "2026-01-01T00:00:01",
            "stages": [{"kind": "generation", "parameters": {"speed": 1.2}}],
        },
    ]
    monkeypatch.setattr(module, "api", lambda *_a, **_k: payload)
    assert module.recent_formal_speeds("http://x", "p") == [1.25, 1.2]


def test_execute_group_sends_the_baseline_for_ordinary_generation(monkeypatch):
    module = _load_module()
    seen = {}

    def fake_api(_base, path, payload=None, **_kwargs):
        seen["payload"] = payload
        return {"status": "queued"}

    monkeypatch.setattr(module, "api", fake_api)
    module.execute_group("http://x", "p", "g", speed_baseline=1.25)
    assert seen["payload"]["ordinary_speed_baseline"] == 1.25
    assert "regenerate_existing" not in seen["payload"]


def test_recent_formal_speeds_reads_the_project_tts_route(monkeypatch):
    module = _load_module()
    seen = {}

    def fake_api(_base, path, **_kwargs):
        seen["path"] = path
        return []

    monkeypatch.setattr(module, "api", fake_api)
    module.recent_formal_speeds("http://x", "project-1")
    assert seen["path"] == "/api/projects/project-1/video-localization/tts/tasks"


def _alignment_audit(words):
    return {
        "expected_spoken_text": "".join(item[0] for item in words),
        "aligned_words": [
            {
                "word_id": f"w{index}",
                "text": text,
                "start_ms": start,
                "end_ms": end,
            }
            for index, (text, start, end) in enumerate(words)
        ],
    }


def _alignment_clip(source_start_ms=0, source_end_ms=6000):
    return {
        "clip_id": "clip_1",
        "candidate_id": "candidate_1",
        "start_ms": 1000,
        "end_ms": 7000,
        "source_start_ms": source_start_ms,
        "source_end_ms": source_end_ms,
        "target_subtitle_ids": ["s1", "s2"],
    }


def test_plan_alignment_slices_tiles_the_crop_and_aligns_each_line():
    module = _load_module()
    audit = _alignment_audit(
        [("早", 100, 400), ("安", 400, 700), ("好", 700, 900), ("再", 3000, 3300), ("见", 3300, 3600)]
    )
    subtitles = [
        # First line already starts where the take starts: 1000 + (100 - 80).
        {"subtitle_id": "s1", "text": "早安好", "start_ms": 1020},
        {"subtitle_id": "s2", "text": "再见", "start_ms": 5000},
    ]
    plan = module.plan_alignment_slices(
        audit,
        _alignment_clip(source_start_ms=80, source_end_ms=3700),
        subtitles,
    )
    assert plan is not None
    slices = plan["slices"]
    assert [item["target_subtitle_ids"] for item in slices] == [["s1"], ["s2"]]
    assert slices[0]["source_start_ms"] == 80
    assert slices[-1]["source_end_ms"] == 3700
    assert slices[0]["source_end_ms"] == slices[1]["source_start_ms"]
    # Slice one runs 80..2920 from timeline 1000, so the next line waits
    # until its own cue at 5000.
    assert slices[1]["timeline_gap_before_ms"] == 5000 - (1000 + (2920 - 80))
    assert plan["max_deviation_ms"] > 0


def test_plan_alignment_slices_skips_an_already_aligned_take():
    module = _load_module()
    audit = _alignment_audit([("早", 100, 400), ("安", 400, 700), ("好", 700, 900), ("再", 1000, 1300)])
    subtitles = [
        {"subtitle_id": "s1", "text": "早安好", "start_ms": 1100},
        {"subtitle_id": "s2", "text": "再", "start_ms": 2000},
    ]
    assert (
        module.plan_alignment_slices(
            audit,
            _alignment_clip(source_start_ms=80, source_end_ms=1400),
            subtitles,
        )
        is None
    )


def test_plan_alignment_slices_refuses_unmatchable_text():
    module = _load_module()
    audit = _alignment_audit([("早", 100, 400), ("安", 400, 700)])
    subtitles = [
        {"subtitle_id": "s1", "text": "早安", "start_ms": 100},
        {"subtitle_id": "s2", "text": "完全不同", "start_ms": 2000},
    ]
    assert (
        module.plan_alignment_slices(
            audit,
            _alignment_clip(source_start_ms=80, source_end_ms=800),
            subtitles,
        )
        is None
    )
