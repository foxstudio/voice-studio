"""Pure-logic coverage for the dubbing production driver script.

The script only orchestrates public API calls; these tests pin the parts that
decide what an Agent is shown and what the script is allowed to submit, without
starting a server or touching a project.  The driver must never derive a
semantic role from punctuation, timing or low-energy evidence on its own.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_heuristic_disposition_helpers_are_gone():
    module = _load_module()
    for name in (
        "rule_reviews",
        "continuous_reviews",
        "merge_agent_decisions",
        "evidenced_pause",
        "recent_formal_speeds",
    ):
        assert not hasattr(module, name), name


def test_reusable_agent_reviews_requires_complete_valid_reviews():
    module = _load_module()
    audit = {
        "status": "pending_agent",
        "boundaries": [{"boundary_id": "a"}, {"boundary_id": "b"}],
    }

    # Punctuation, 0ms, short gaps, safe-retain evidence and long pauses are
    # present in the boundaries, but no Agent wrote anything: refuse.
    heuristic = {
        **audit,
        "boundaries": [
            {
                "boundary_id": "a",
                "final_relation": "touching",
                "final_gap_ms": 0,
                "left_render_status": "fully_retained",
                "right_render_status": "fully_retained",
                "low_energy_evidence": [
                    {"decision_reason": "retain", "edit_decision": "retain"}
                ],
            },
            {"boundary_id": "b", "final_gap_ms": 5000},
        ],
    }
    assert module.reusable_agent_reviews(heuristic) is None

    partial = {
        **audit,
        "agent_reviews": [
            {
                "boundary_id": "a",
                "semantic_role": "continuous_phrase",
                "disposition": "acceptable",
                "reason": "x",
            }
        ],
    }
    assert module.reusable_agent_reviews(partial) is None

    complete = {
        **audit,
        "agent_reviews": [
            {
                "boundary_id": "a",
                "semantic_role": "continuous_phrase",
                "disposition": "acceptable",
                "reason": "x",
            },
            {
                "boundary_id": "b",
                "semantic_role": "semantic_boundary",
                "disposition": "uncertain",
                "reason": "y",
            },
        ],
    }
    reviews = module.reusable_agent_reviews(complete)
    assert reviews is not None
    assert [item["boundary_id"] for item in reviews] == ["a", "b"]

    # A stale / non-pending audit must not be resubmitted as if it were live.
    assert module.reusable_agent_reviews({**complete, "status": "accepted"}) is None
    # Invalid or duplicate entries are refused too.
    broken = {
        **complete,
        "agent_reviews": [
            {**complete["agent_reviews"][0], "semantic_role": "maybe"},
            complete["agent_reviews"][1],
        ],
    }
    assert module.reusable_agent_reviews(broken) is None


def _identity(**overrides) -> dict:
    identity = {
        "source_revision": "a" * 64,
        "plan_revision": 7,
        "candidate_id": "candidate_c1",
        "audio_sha256": "b" * 64,
        "candidate_evidence_fingerprint": "c" * 64,
        "candidate_clip_projection_fingerprint": "d" * 64,
    }
    identity.update(overrides)
    return identity


def _audit_with_boundaries(*boundary_ids: str, identity: dict | None = None) -> dict:
    return {
        **(identity or _identity()),
        "status": "pending_agent",
        "aligned_words": [
            {"word_id": "w0", "text": "x", "start_ms": 0, "end_ms": 1}
        ],
        "boundaries": [{"boundary_id": value} for value in boundary_ids],
    }


def test_review_file_requires_candidate_identity(tmp_path: Path):
    module = _load_module()
    path = tmp_path / "reviews.json"
    path.write_text(
        json.dumps({"semantic_boundary_reviews": [{"boundary_id": "w1:w2"}]}),
        encoding="utf-8",
    )
    with pytest.raises(module.ApiError, match="身份"):
        module.load_review_file(path)

    path.write_text(json.dumps([{"boundary_id": "w1:w2"}]), encoding="utf-8")
    with pytest.raises(module.ApiError, match="对象"):
        module.load_review_file(path)


def test_review_file_keeps_the_original_identity(tmp_path: Path):
    module = _load_module()
    identity = _identity()
    payload = {
        **identity,
        "schema_version": module.REVIEW_SCHEMA,
        "semantic_boundary_reviews": [
            {
                "boundary_id": "w1:w2",
                "semantic_role": "semantic_boundary",
                "disposition": "acceptable",
                "reason": "句末语义停顿",
            }
        ],
    }
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = module.load_review_file(path)
    assert module.review_identity(loaded) == identity
    assert loaded["semantic_boundary_reviews"][0]["reason"] == "句末语义停顿"


def test_review_with_a_file_submits_the_agent_decisions(monkeypatch, tmp_path: Path):
    module = _load_module()
    run = _run_with({"g": "needs_semantic_review"})
    run["groups"][0]["candidate_ids"] = ["c1"]
    monkeypatch.setattr(module, "read_run", lambda *_a, **_k: run)
    monkeypatch.setattr(module, "pick_agent_candidate", lambda group, **k: "candidate_c1")
    seen: dict = {}
    monkeypatch.setattr(
        module,
        "submit_reviews",
        lambda base, project, cid, reviews, **kwargs: seen.update(
            {"cid": cid, "reviews": reviews, "identity": kwargs.get("identity")}
        )
        or {"ok": True},
    )
    path = tmp_path / "reviews.json"
    path.write_text(
        json.dumps(
            {
                **_identity(),
                "semantic_boundary_reviews": [
                    {
                        "boundary_id": "w1:w2",
                        "semantic_role": "semantic_boundary",
                        "disposition": "acceptable",
                        "reason": "句末语义停顿",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert module.main(["--project", "p", "review", "--group", "g", "--file", str(path)]) == 0
    assert seen["cid"] == "candidate_c1"
    assert seen["identity"]["audio_sha256"] == "b" * 64
    assert seen["reviews"][0]["reason"] == "句末语义停顿"


def test_submit_rejects_a_stale_identity_without_posting(monkeypatch):
    module = _load_module()
    audit = _audit_with_boundaries("w1:w2")
    posted: list = []
    monkeypatch.setattr(module, "read_audit", lambda *_a, **_k: audit)
    monkeypatch.setattr(module, "api", lambda *a, **k: posted.append(a) or {})

    with pytest.raises(module.ApiError, match="STALE"):
        module.submit_reviews(
            "http://x",
            "p",
            "candidate_c1",
            [{"boundary_id": "w1:w2"}],
            identity=_identity(audio_sha256="e" * 64),
        )
    assert posted == []


def test_submit_uses_the_original_identity(monkeypatch):
    module = _load_module()
    audit = _audit_with_boundaries("w1:w2")
    seen: dict = {}

    def fake_api(_base, _path, payload=None, **_kwargs):
        seen["payload"] = payload
        return {}

    monkeypatch.setattr(module, "read_audit", lambda *_a, **_k: audit)
    monkeypatch.setattr(module, "api", fake_api)

    module.submit_reviews(
        "http://x",
        "p",
        "candidate_c1",
        [{"boundary_id": "w1:w2"}],
        identity=_identity(),
    )
    assert seen["payload"]["schema_version"] == module.REVIEW_SCHEMA
    assert seen["payload"]["source_revision"] == "a" * 64
    assert seen["payload"]["audio_sha256"] == "b" * 64


def test_build_review_body_rejects_duplicate_and_missing_id():
    module = _load_module()
    audit = _audit_with_boundaries("w1:w2")

    with pytest.raises(module.ApiError, match="重复"):
        module.build_review_body(
            audit,
            [{"boundary_id": "w1:w2"}, {"boundary_id": "w1:w2"}],
        )
    with pytest.raises(module.ApiError, match="boundary_id"):
        module.build_review_body(audit, [{"boundary_id": ""}])


def test_build_review_body_allows_empty_only_with_word_evidence():
    module = _load_module()
    single_word = {
        **_identity(),
        "boundaries": [],
        "aligned_words": [
            {"word_id": "w0", "text": "a", "start_ms": 0, "end_ms": 1}
        ],
    }
    body = module.build_review_body(single_word, [])
    assert body["semantic_boundary_reviews"] == []

    for label, words in (
        ("no words", []),
        ("malformed word", [{}]),
        (
            "multiple words",
            [
                {"word_id": "w0", "text": "a", "start_ms": 0, "end_ms": 1},
                {"word_id": "w1", "text": "b", "start_ms": 1, "end_ms": 2},
            ],
        ),
    ):
        audit = {**_identity(), "boundaries": [], "aligned_words": words}
        with pytest.raises(module.ApiError, match="无需检查"):
            module.build_review_body(audit, []), label


def test_boundaries_outputs_the_review_identity(monkeypatch, capsys):
    module = _load_module()
    run = _run_with({"g": "needs_semantic_review"})
    run["groups"][0]["candidate_ids"] = ["c1"]
    monkeypatch.setattr(module, "read_run", lambda *_a, **_k: run)
    monkeypatch.setattr(module, "pick_agent_candidate", lambda group, **k: "candidate_c1")
    monkeypatch.setattr(module, "read_audit", lambda *_a, **_k: _audit_with_boundaries("w1:w2"))

    assert module.main(["--project", "p", "boundaries", "--group", "g"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_revision"] == "a" * 64
    assert payload["plan_revision"] == 7
    assert payload["audio_sha256"] == "b" * 64
    assert payload["candidate_clip_projection_fingerprint"] == "d" * 64


def test_review_without_file_is_refused(monkeypatch, capsys):
    module = _load_module()
    run = _run_with({"g": "needs_semantic_review"})
    run["groups"][0]["candidate_ids"] = ["c1"]
    monkeypatch.setattr(module, "read_run", lambda *_a, **_k: run)
    monkeypatch.setattr(module, "pick_agent_candidate", lambda group, **k: "candidate_c1")

    assert module.main(["--project", "p", "review", "--group", "g"]) == 1
    assert "--file" in capsys.readouterr().err


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


def test_new_range_speed_defaults_to_one_and_validates_the_range():
    module = _load_module()
    # No history lookup by default: a new range starts at 1.0 even if the
    # project's last takes ran faster.
    assert module.resolve_ordinary_speed_baseline("http://x", "p") == 1.0
    assert module.resolve_ordinary_speed_baseline("http://x", "p", 1.15) == 1.15
    with pytest.raises(module.ApiError):
        module.resolve_ordinary_speed_baseline("http://x", "p", 0.9)
    with pytest.raises(module.ApiError):
        module.resolve_ordinary_speed_baseline("http://x", "p", 2.5)


def test_advance_sends_default_one_and_does_not_regenerate(monkeypatch):
    module = _load_module()
    captured: dict = {}

    def fake_advance(base_url, project_id, group_id, **kwargs):
        captured.update(kwargs)
        return {"group_id": group_id, "result": "terminal", "stage": "accepted"}

    monkeypatch.setattr(module, "read_run", lambda *_a, **_k: _run_with({"g": "ready_to_generate"}))
    monkeypatch.setattr(module, "advance_group", fake_advance)

    assert module.main(["--project", "p", "advance", "--group", "g"]) == 0
    assert captured["speed_baseline"] == 1.0
    assert captured.get("regenerate") in (None, False)


def test_execute_group_sends_the_baseline_for_ordinary_generation(monkeypatch):
    module = _load_module()
    seen = {}

    def fake_api(_base, path, payload=None, **_kwargs):
        seen["payload"] = payload
        return {"status": "queued"}

    monkeypatch.setattr(module, "api", fake_api)
    module.execute_group("http://x", "p", "g", speed_baseline=1.0)
    assert seen["payload"]["ordinary_speed_baseline"] == 1.0
    assert "regenerate_existing" not in seen["payload"]


def test_run_stops_for_agent_without_fabricating_reviews_or_splits(monkeypatch, capsys):
    module = _load_module()
    run = _run_with({"g_review": "needs_semantic_review", "g_gen": "ready_to_generate"})
    for group in run["groups"]:
        if group["group_id"] == "g_review":
            group["candidate_ids"] = ["c1"]

    advanced: list[str] = []
    advance_calls: list[dict] = []
    submitted = {"count": 0}
    api_paths: list[str] = []

    monkeypatch.setattr(module, "read_run", lambda *_a, **_k: run)

    def fake_advance(base_url, project_id, group_id, **kwargs):
        advanced.append(group_id)
        advance_calls.append(kwargs)
        return {"group_id": group_id, "result": "terminal", "stage": "accepted"}

    monkeypatch.setattr(module, "advance_group", fake_advance)
    monkeypatch.setattr(module, "pick_agent_candidate", lambda group, **k: "candidate_c1")
    monkeypatch.setattr(
        module,
        "read_audit",
        lambda *_a, **_k: {
            "status": "pending_agent",
            "boundaries": [{"boundary_id": "w1:w2"}],
            "agent_reviews": [],
        },
    )
    monkeypatch.setattr(
        module,
        "submit_reviews",
        lambda *_a, **_k: submitted.__setitem__("count", submitted["count"] + 1) or {},
    )
    monkeypatch.setattr(
        module,
        "api",
        lambda base, path, *a, **k: api_paths.append(path) or {},
    )

    code = module.main(
        ["--project", "p", "run", "--max-cycles", "2", "--poll-seconds", "0"]
    )

    assert code == 2
    assert submitted["count"] == 0
    assert not any("staged-split" in path for path in api_paths)
    # The independent generating group still moved; the review group did not
    # hold it back.
    assert "g_gen" in advanced
    assert all(call.get("regenerate") in (None, False) for call in advance_calls)
    output = capsys.readouterr().out
    assert "agent_decision_required" in output
    assert "w1:w2" in output


def test_run_resubmits_complete_agent_reviews(monkeypatch):
    module = _load_module()
    run = _run_with({"g_review": "needs_semantic_review"})
    run["groups"][0]["candidate_ids"] = ["c1"]
    state = {"submitted": 0, "cycles": 0}

    def fake_read_run(*_a, **_k):
        state["cycles"] += 1
        if state["submitted"]:
            return _run_with({"g_review": "accepted"})
        return run

    monkeypatch.setattr(module, "read_run", fake_read_run)
    monkeypatch.setattr(module, "pick_agent_candidate", lambda group, **k: "candidate_c1")
    monkeypatch.setattr(
        module,
        "read_audit",
        lambda *_a, **_k: {
            **_identity(),
            "status": "pending_agent",
            "boundaries": [{"boundary_id": "w1:w2"}],
            "aligned_words": [
                {"word_id": "w0", "text": "x", "start_ms": 0, "end_ms": 1}
            ],
            "agent_reviews": [
                {
                    "boundary_id": "w1:w2",
                    "semantic_role": "continuous_phrase",
                    "disposition": "acceptable",
                    "reason": "Agent 已核对的连续表达",
                }
            ],
        },
    )
    seen: dict = {}
    monkeypatch.setattr(
        module,
        "submit_reviews",
        lambda *a, **k: state.__setitem__("submitted", state["submitted"] + 1)
        or seen.update({"identity": k.get("identity")})
        or {},
    )
    monkeypatch.setattr(module, "advance_group", lambda *a, **k: {"group_id": "g_review", "result": "terminal", "stage": "accepted"})

    assert module.main(["--project", "p", "run", "--max-cycles", "3", "--poll-seconds", "0"]) == 0
    assert state["submitted"] == 1
    assert seen["identity"]["audio_sha256"] == "b" * 64


def test_legacy_flags_error_with_migration_guidance(capsys):
    module = _load_module()

    assert module.main(["--project", "p", "review", "--group", "g", "--accept-continuous"]) == 1
    assert "已废弃" in capsys.readouterr().err

    assert module.main(["--project", "p", "review", "--group", "g", "--gap-policy", "evidenced"]) == 1
    assert "已废弃" in capsys.readouterr().err

    assert module.main(["--project", "p", "run", "--no-align"]) == 1
    assert "已废弃" in capsys.readouterr().err


def test_invalid_speed_baseline_exits_one(capsys):
    module = _load_module()
    assert module.main(["--project", "p", "advance", "--group", "g", "--speed-baseline", "0.5"]) == 1
    assert "speed-baseline" in capsys.readouterr().err


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
