from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_document_evidence,
    localization_generation_request,
    localization_review_request,
    localization_workflow_nodes,
    workflow_behavior,
    workflow_contracts,
)


def test_localization_workflow_summary_exposes_only_v3():
    assert (
        workflow_contracts.localization_workflow_summary()["workflow_id"]
        == "localization-v3"
    )
    assert not hasattr(
        workflow_contracts,
        "localization_workflow_v3_summary",
    )


def test_document_first_localization_workflow_has_complete_dependency_order():
    definition = workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION
    tasks = [
        task
        for stage in definition.stages
        for task in stage.atomic_tasks
    ]
    task_by_id = {task.id: task for task in tasks}

    assert definition.workflow_id == "localization-v3"
    assert len(tasks) == 17
    assert len(task_by_id) == len(tasks)
    assert (
        task_by_id["generate_localization_spoken_script"].depends_on
        == ["lock_localization_creation_context"]
    )
    assert (
        task_by_id["generate_localization_spoken_script"].label
        == "生成全文本土化初稿"
    )
    assert (
        task_by_id["lock_localization_creation_context"].depends_on
        == [
            "analyze_localization_document",
            "adjudicate_localization_evidence_v3",
        ]
    )
    assert task_by_id[
        "review_localization_fidelity"
    ].execution == "parallel"
    assert task_by_id[
        "review_localization_fidelity"
    ].depends_on == ["generate_localization_spoken_script"]
    assert task_by_id[
        "review_localization_naturalness"
    ].execution == "parallel"
    assert task_by_id[
        "review_localization_naturalness"
    ].depends_on == ["generate_localization_spoken_script"]
    assert task_by_id[
        "finalize_localization_spoken_script"
    ].execution == "join"
    assert task_by_id[
        "finalize_localization_spoken_script"
    ].depends_on == [
        "review_localization_fidelity",
        "review_localization_naturalness",
    ]
    assert task_by_id[
        "align_localization_semantics"
    ].output_contract_version == "localization-semantic-alignment-v14"
    assert task_by_id[
        "generate_localization_spoken_script"
    ].output_contract_version == "localization-spoken-script-v4"
    assert task_by_id[
        "build_localization_dual_tracks"
    ].output_contract_version == "localization-dual-tracks-v7"
    assert (
        task_by_id["build_localization_dual_tracks"].depends_on
        == ["adjudicate_localization_alignment"]
    )
    assert task_by_id[
        "adjudicate_localization_display_boundaries"
    ].depends_on == ["build_localization_dual_tracks"]
    assert task_by_id[
        "validate_localization_tracks"
    ].depends_on == ["adjudicate_localization_display_boundaries"]

    seen: set[str] = set()
    for task in sorted(tasks, key=lambda item: item.order):
        assert set(task.depends_on).issubset(seen)
        seen.add(task.id)


def test_evidence_node_behavior_fingerprints_are_atomic(
    monkeypatch,
):
    research_before = (
        localization_workflow_nodes.node_behavior_fingerprint(
            "collect_localization_research_evidence_v3"
        )
    )
    visual_before = (
        localization_workflow_nodes.node_behavior_fingerprint(
            "collect_localization_visual_evidence_v3"
        )
    )
    original_getsource = workflow_behavior.inspect.getsource

    def changed_visual_source(value):
        source = original_getsource(value)
        if (
            value
            is localization_document_evidence
            .collect_localization_document_visuals
        ):
            return source + "\n# simulated visual-only change"
        return source

    monkeypatch.setattr(
        workflow_behavior.inspect,
        "getsource",
        changed_visual_source,
    )

    assert (
        localization_workflow_nodes.node_behavior_fingerprint(
            "collect_localization_research_evidence_v3"
        )
        == research_before
    )
    assert (
        localization_workflow_nodes.node_behavior_fingerprint(
            "collect_localization_visual_evidence_v3"
        )
        != visual_before
    )


def test_model_request_boundaries_participate_in_node_fingerprints():
    specs = localization_workflow_nodes.LOCALIZATION_WORKFLOW_NODE_SPECS

    assert localization_generation_request in (
        specs["generate_localization_spoken_script"].implementation_modules
    )
    for step_id in (
        "review_localization_fidelity",
        "review_localization_naturalness",
        "finalize_localization_spoken_script",
    ):
        assert localization_review_request in (
            specs[step_id].implementation_modules
        )
