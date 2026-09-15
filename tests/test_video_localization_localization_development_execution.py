from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from app.domains.video_localization import (
    localization_source,
    quality_gate,
    subtitle_punctuation,
)
from app.domains.video_localization.development_checkpoints import (
    LocalizationDevelopmentCheckpointWriter,
    LocalizationDevelopmentMaterializationStore,
    load_development_checkpoint,
    load_development_checkpoints_by_prefix,
)
from app.domains.video_localization.localization_workflow_execution import (
    LocalizationDevelopmentExecutionConfig,
    LocalizationDevelopmentTargetFailed,
    LocalizationDevelopmentTargetReached,
    LocalizationWorkflowExecution,
)
from app.domains.video_localization.localization_workflow_nodes import (
    LOCALIZATION_WORKFLOW_NODE_SPECS,
    LocalizationWorkflowNodeSpec,
)
from app.domains.video_localization.workflow_contracts import (
    LOCALIZATION_WORKFLOW_V3_DEFINITION,
)
from app.domains.video_localization.workflow_graph import WorkflowGraph
from app.domains.video_localization.workflow_ledger import (
    LocalizationWorkflowLedger,
)


def test_quality_gate_snapshot_fingerprint_tracks_shared_quality_rules():
    spec = LOCALIZATION_WORKFLOW_NODE_SPECS[
        "validate_localization_tracks"
    ]

    assert quality_gate in spec.implementation_modules


def test_dual_track_snapshot_fingerprint_tracks_shared_subtitle_rules():
    spec = LOCALIZATION_WORKFLOW_NODE_SPECS[
        "build_localization_dual_tracks"
    ]

    assert subtitle_punctuation in spec.implementation_modules


def test_development_node_artifacts_stay_inside_external_snapshot_root(
    tmp_path: Path,
) -> None:
    execution = LocalizationWorkflowExecution(
        LocalizationWorkflowLedger(
            "operation-1",
            definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
        ),
        project_id="project-1",
        operation_id="operation-1",
        development=LocalizationDevelopmentExecutionConfig(
            development_session_id="session-1",
            target_step_id="collect_localization_visual_evidence_v3",
            snapshot_root=tmp_path,
        ),
    )

    assert execution.development_artifact_dir_for(
        "collect_localization_visual_evidence_v3"
    ) == (
        tmp_path
        / "session-1"
        / "nodes"
        / "collect_localization_visual_evidence_v3"
    )

    formal_execution = LocalizationWorkflowExecution(
        LocalizationWorkflowLedger(
            "operation-2",
            definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
        ),
        project_id="project-1",
        operation_id="operation-2",
    )
    assert formal_execution.development_artifact_dir_for(
        "collect_localization_visual_evidence_v3"
    ) is None


class _FakeSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-source-lock-v1"
    ] = "localization-source-lock-v1"
    result_fingerprint: str


class _FakeContextResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-context-intent-v2"
    ] = "localization-context-intent-v2"
    result_fingerprint: str


class _FakeBriefResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-document-brief-v3"
    ] = "localization-document-brief-v3"
    result_fingerprint: str


def _project(result: BaseModel) -> dict:
    return {
        "status": "success",
        "summary": f"{result.result_fingerprint} 已完成。",
        "metrics": [],
        "sections": [],
        "notes": [],
    }


def test_localization_graph_plans_only_target_ancestors() -> None:
    graph = WorkflowGraph(LOCALIZATION_WORKFLOW_V3_DEFINITION)

    research_plan = graph.plan_for(
        "collect_localization_research_evidence_v3"
    )
    assert research_plan.ordered_step_ids == (
        "lock_localization_source",
        "lock_localization_context_intent",
        "analyze_localization_document",
        "collect_localization_research_evidence_v3",
    )
    assert "collect_localization_visual_evidence_v3" not in (
        research_plan.required_step_ids
    )

    join_plan = graph.plan_for("adjudicate_localization_evidence_v3")
    assert {
        "collect_localization_research_evidence_v3",
        "collect_localization_visual_evidence_v3",
    }.issubset(join_plan.required_step_ids)
    assert len(graph.ordered_step_ids) == 17


def test_materialization_store_rejects_changed_behavior_and_dependencies(
    tmp_path: Path,
) -> None:
    store = LocalizationDevelopmentMaterializationStore(
        tmp_path,
        project_id="project-1",
        development_session_id="session-1",
    )
    result = _FakeSourceResult(result_fingerprint="source-v1")
    saved = store.save(
        "lock_localization_source",
        result,
        produced_by_operation_id="operation-1",
        behavior_fingerprint="behavior-v1",
        dependency_fingerprints={},
    )

    valid = store.load(
        "lock_localization_source",
        result_model=_FakeSourceResult,
        expected_contract_version="localization-source-lock-v1",
        expected_behavior_fingerprint="behavior-v1",
        expected_dependency_fingerprints={},
    )
    assert valid.status == "valid"
    assert valid.result == result
    assert saved.materialization_fingerprint

    changed_code = store.load(
        "lock_localization_source",
        result_model=_FakeSourceResult,
        expected_contract_version="localization-source-lock-v1",
        expected_behavior_fingerprint="behavior-v2",
        expected_dependency_fingerprints={},
    )
    assert changed_code.status == "stale"
    assert "代码" in changed_code.reason

    changed_upstream = store.load(
        "lock_localization_source",
        result_model=_FakeSourceResult,
        expected_contract_version="localization-source-lock-v1",
        expected_behavior_fingerprint="behavior-v1",
        expected_dependency_fingerprints={"upstream": "new"},
    )
    assert changed_upstream.status == "stale"
    assert "上游" in changed_upstream.reason


def test_typed_atomic_checkpoint_round_trip_is_scoped_to_development_session(
    tmp_path: Path,
) -> None:
    writer = LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="session-1",
    )
    result = _FakeSourceResult(result_fingerprint="source-v1")
    writer("paid-node.response", result)

    loaded = load_development_checkpoint(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="session-1",
        step_id="paid-node.response",
        result_model=_FakeSourceResult,
    )
    wrong_project = load_development_checkpoint(
        tmp_path,
        project_id="project-2",
        workflow_operation_id="session-1",
        step_id="paid-node.response",
        result_model=_FakeSourceResult,
    )

    assert loaded == result
    assert wrong_project is None


def test_atomic_checkpoint_family_loader_returns_all_valid_matching_steps(
    tmp_path: Path,
) -> None:
    writer = LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="session-1",
    )
    first = _FakeSourceResult(result_fingerprint="round-1")
    second = _FakeSourceResult(result_fingerprint="round-2")
    writer("finalizer.round_01.section_0001", first)
    writer("finalizer.round_02.section_0003", second)
    writer(
        "other-node.response",
        _FakeSourceResult(result_fingerprint="other"),
    )

    loaded = load_development_checkpoints_by_prefix(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="session-1",
        step_id_prefix="finalizer.round_",
        result_model=_FakeSourceResult,
    )

    assert loaded == [first, second]


def test_development_execution_reuses_previous_node_without_calling_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        LOCALIZATION_WORKFLOW_NODE_SPECS,
        "lock_localization_source",
        LocalizationWorkflowNodeSpec(
            "lock_localization_source",
            _FakeSourceResult,
            (localization_source,),
        ),
    )
    monkeypatch.setitem(
        LOCALIZATION_WORKFLOW_NODE_SPECS,
        "lock_localization_context_intent",
        LocalizationWorkflowNodeSpec(
            "lock_localization_context_intent",
            _FakeContextResult,
            (localization_source,),
        ),
    )
    source = _FakeSourceResult(result_fingerprint="source-v1")
    context = _FakeContextResult(result_fingerprint="context-v1")

    first_ledger = LocalizationWorkflowLedger(
        "operation-1",
        definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
    )
    first = LocalizationWorkflowExecution(
        first_ledger,
        project_id="project-1",
        operation_id="operation-1",
        development=LocalizationDevelopmentExecutionConfig(
            development_session_id="session-1",
            target_step_id="lock_localization_source",
            snapshot_root=tmp_path,
        ),
        behavior_contexts={
            "lock_localization_source": {"source": "v1"},
        },
    )
    with pytest.raises(LocalizationDevelopmentTargetReached):
        first.run(
            "lock_localization_source",
            "运行源输入",
            lambda: source,
            _project,
        )
    assert first.executed_step_ids == ["lock_localization_source"]
    assert (
        first.may_resume_atomic_checkpoint(
            "lock_localization_source"
        )
        is False
    )
    assert (
        first.may_resume_atomic_checkpoint(
            "lock_localization_source",
            allow_completed_target=True,
        )
        is True
    )

    source_action_called = False

    def unexpected_source_action() -> _FakeSourceResult:
        nonlocal source_action_called
        source_action_called = True
        return source

    second_ledger = LocalizationWorkflowLedger(
        "operation-2",
        definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
    )
    second = LocalizationWorkflowExecution(
        second_ledger,
        project_id="project-1",
        operation_id="operation-2",
        development=LocalizationDevelopmentExecutionConfig(
            development_session_id="session-1",
            target_step_id="lock_localization_context_intent",
            snapshot_root=tmp_path,
        ),
        behavior_contexts={
            "lock_localization_source": {"source": "v1"},
        },
    )
    reused_source = second.run(
        "lock_localization_source",
        "运行源输入",
        unexpected_source_action,
        _project,
    )
    assert reused_source == source
    with pytest.raises(LocalizationDevelopmentTargetReached):
        second.run(
            "lock_localization_context_intent",
            "运行目标锁定",
            lambda: context,
            _project,
        )

    assert source_action_called is False
    assert second.reused_step_ids == ["lock_localization_source"]
    assert second.executed_step_ids == [
        "lock_localization_context_intent"
    ]


def test_development_target_reports_projected_failure_instead_of_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        LOCALIZATION_WORKFLOW_NODE_SPECS,
        "lock_localization_source",
        LocalizationWorkflowNodeSpec(
            "lock_localization_source",
            _FakeSourceResult,
            (localization_source,),
        ),
    )
    execution = LocalizationWorkflowExecution(
        LocalizationWorkflowLedger(
            "operation-1",
            definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
        ),
        project_id="project-1",
        operation_id="operation-1",
        development=LocalizationDevelopmentExecutionConfig(
            development_session_id="session-1",
            target_step_id="lock_localization_source",
            snapshot_root=tmp_path,
        ),
    )

    with pytest.raises(LocalizationDevelopmentTargetFailed):
        execution.run(
            "lock_localization_source",
            "运行目标节点",
            lambda: _FakeSourceResult(result_fingerprint="source-v1"),
            lambda _result: {
                "status": "failed",
                "summary": "目标节点检查失败。",
                "metrics": [],
                "sections": [],
                "notes": [],
            },
        )


def test_development_parallel_nodes_really_overlap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacements = {
        "lock_localization_source": _FakeSourceResult,
        "lock_localization_context_intent": _FakeContextResult,
        "analyze_localization_document": _FakeBriefResult,
        "collect_localization_research_evidence_v3": _FakeSourceResult,
        "collect_localization_visual_evidence_v3": _FakeContextResult,
    }
    for step_id, result_model in replacements.items():
        monkeypatch.setitem(
            LOCALIZATION_WORKFLOW_NODE_SPECS,
            step_id,
            LocalizationWorkflowNodeSpec(
                step_id,
                result_model,
                (localization_source,),
            ),
        )
    execution = LocalizationWorkflowExecution(
        LocalizationWorkflowLedger(
            "operation-1",
            definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
        ),
        project_id="project-1",
        operation_id="operation-1",
        development=LocalizationDevelopmentExecutionConfig(
            development_session_id="session-1",
            target_step_id="adjudicate_localization_evidence_v3",
            snapshot_root=tmp_path,
        ),
    )
    execution._lineage_fingerprints.update(
        {
            "lock_localization_source": "source-lineage",
            "lock_localization_context_intent": "context-lineage",
            "analyze_localization_document": "brief-lineage",
        }
    )
    barrier = threading.Barrier(2)
    running = 0
    maximum_running = 0
    lock = threading.Lock()

    def branch(result):
        nonlocal running, maximum_running
        with lock:
            running += 1
            maximum_running = max(maximum_running, running)
        barrier.wait(timeout=1)
        time.sleep(0.02)
        with lock:
            running -= 1
        return result

    results = execution.run_parallel(
        [
            (
                "collect_localization_research_evidence_v3",
                "查询",
                lambda: branch(
                    _FakeSourceResult(result_fingerprint="research-v1")
                ),
                _project,
                {},
            ),
            (
                "collect_localization_visual_evidence_v3",
                "截图",
                lambda: branch(
                    _FakeContextResult(result_fingerprint="visual-v1")
                ),
                _project,
                {},
            ),
        ],
        thread_name_prefix="development-parallel-test",
    )

    assert maximum_running == 2
    assert [item.result_fingerprint for item in results] == [
        "research-v1",
        "visual-v1",
    ]


def test_development_commit_target_executes_side_effect_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        LOCALIZATION_WORKFLOW_NODE_SPECS,
        "commit_localization_tracks",
        LocalizationWorkflowNodeSpec(
            "commit_localization_tracks",
            _FakeSourceResult,
            (localization_source,),
            has_formal_side_effect=True,
        ),
    )
    execution = LocalizationWorkflowExecution(
        LocalizationWorkflowLedger(
            "operation-1",
            definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
        ),
        project_id="project-1",
        operation_id="operation-1",
        development=LocalizationDevelopmentExecutionConfig(
            development_session_id="session-1",
            target_step_id="commit_localization_tracks",
            snapshot_root=tmp_path,
        ),
    )
    execution._lineage_fingerprints[
        "validate_localization_tracks"
    ] = "gate-lineage"
    calls = 0

    def commit() -> _FakeSourceResult:
        nonlocal calls
        calls += 1
        return _FakeSourceResult(result_fingerprint="write-v1")

    with pytest.raises(LocalizationDevelopmentTargetReached):
        execution.run_side_effect(
            "commit_localization_tracks",
            "保存字幕",
            commit,
            _project,
        )

    assert calls == 1
    assert execution.development_summary()[
        "formal_project_data_changed"
    ] is True


def test_upstream_behavior_change_invalidates_every_descendant_even_when_result_is_equal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacements = {
        "lock_localization_source": _FakeSourceResult,
        "lock_localization_context_intent": _FakeContextResult,
        "analyze_localization_document": _FakeBriefResult,
    }
    for step_id, result_model in replacements.items():
        monkeypatch.setitem(
            LOCALIZATION_WORKFLOW_NODE_SPECS,
            step_id,
            LocalizationWorkflowNodeSpec(
                step_id,
                result_model,
                (localization_source,),
            ),
        )
    source = _FakeSourceResult(result_fingerprint="same-source")
    context = _FakeContextResult(result_fingerprint="same-context")
    brief = _FakeBriefResult(result_fingerprint="same-brief")

    def build_execution(
        operation_id: str,
        *,
        source_behavior: str,
    ) -> LocalizationWorkflowExecution:
        return LocalizationWorkflowExecution(
            LocalizationWorkflowLedger(
                operation_id,
                definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
            ),
            project_id="project-1",
            operation_id=operation_id,
            development=LocalizationDevelopmentExecutionConfig(
                development_session_id="session-1",
                target_step_id="analyze_localization_document",
                snapshot_root=tmp_path,
                force_target=False,
            ),
            behavior_contexts={
                "lock_localization_source": {
                    "source_behavior": source_behavior,
                },
            },
        )

    first = build_execution("operation-1", source_behavior="v1")
    first.run(
        "lock_localization_source",
        "源输入",
        lambda: source,
        _project,
    )
    first.run(
        "lock_localization_context_intent",
        "目标",
        lambda: context,
        _project,
    )
    with pytest.raises(LocalizationDevelopmentTargetReached):
        first.run(
            "analyze_localization_document",
            "全文",
            lambda: brief,
            _project,
        )

    calls: list[str] = []
    second = build_execution("operation-2", source_behavior="v2")
    second.run(
        "lock_localization_source",
        "源输入",
        lambda: calls.append("source") or source,
        _project,
    )
    second.run(
        "lock_localization_context_intent",
        "目标",
        lambda: calls.append("context") or context,
        _project,
    )
    with pytest.raises(LocalizationDevelopmentTargetReached):
        second.run(
            "analyze_localization_document",
            "全文",
            lambda: calls.append("brief") or brief,
            _project,
        )

    assert calls == ["source", "context", "brief"]
    assert second.reused_step_ids == []
    assert second.executed_step_ids == [
        "lock_localization_source",
        "lock_localization_context_intent",
        "analyze_localization_document",
    ]
    assert "代码" in second.recomputed_reasons[
        "lock_localization_source"
    ]
    assert "上游" in second.recomputed_reasons[
        "lock_localization_context_intent"
    ]
    assert "上游" in second.recomputed_reasons[
        "analyze_localization_document"
    ]
