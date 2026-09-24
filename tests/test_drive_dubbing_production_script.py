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
