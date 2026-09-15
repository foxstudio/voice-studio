import json
import sys
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    development_checkpoints,
    localization_formal_retry,
    localization_workflow_nodes,
)


class _CheckpointResult(BaseModel):
    contract_version: str = "test-checkpoint-v1"
    value: int
    source_fingerprint: str = "a" * 64
    route: dict[str, str] = Field(
        default_factory=lambda: {"phase": "test"}
    )


def _install_test_step(monkeypatch, *, behavior: str = "test-behavior"):
    monkeypatch.setitem(
        localization_formal_retry.LOCALIZATION_WORKFLOW_NODE_SPECS,
        "test_step",
        SimpleNamespace(result_model=_CheckpointResult),
    )
    monkeypatch.setattr(
        localization_formal_retry,
        "checkpoint_behavior_fingerprint",
        lambda step_id: behavior if step_id == "test_step" else "other",
    )
    return lambda step_id: behavior if step_id == "test_step" else None


def test_formal_retry_loads_atomic_checkpoints_by_prefix(tmp_path):
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        behavior_fingerprint_resolver=(
            localization_workflow_nodes.checkpoint_behavior_fingerprint
        ),
    )
    writer(
        "finalize_localization_spoken_script.section_r01_0001",
        _CheckpointResult(value=1),
    )
    writer(
        "finalize_localization_spoken_script.section_r01_0002",
        _CheckpointResult(value=2),
    )
    recovery = localization_formal_retry.LocalizationFormalRetryRecovery(
        tmp_path,
        project_id="project-1",
        source_operation_ids=["source-operation-1"],
    )

    checkpoints = recovery.load_checkpoints_by_prefix(
        "finalize_localization_spoken_script.section_",
        result_model=_CheckpointResult,
    )

    assert [item.value for item in checkpoints] == [1, 2]


def test_formal_retry_does_not_reuse_rejected_result(tmp_path, monkeypatch):
    behavior_resolver = _install_test_step(monkeypatch)
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        behavior_fingerprint_resolver=behavior_resolver,
    )
    writer("test_step", _CheckpointResult(value=1))
    recovery = localization_formal_retry.LocalizationFormalRetryRecovery(
        tmp_path,
        project_id="project-1",
        source_operation_ids=["source-operation-1"],
    )
    recovery._contract_versions["test_step"] = "test-checkpoint-v1"

    result = recovery.recover(
        "test_step",
        is_reusable=lambda candidate: candidate.value == 2,
    )

    assert result is None
    assert recovery.reused_step_ids == []


def test_formal_retry_accepts_warning_result_when_caller_marks_it_reusable(
    tmp_path,
    monkeypatch,
):
    behavior_resolver = _install_test_step(monkeypatch)
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        behavior_fingerprint_resolver=behavior_resolver,
    )
    writer("test_step", _CheckpointResult(value=1))
    recovery = localization_formal_retry.LocalizationFormalRetryRecovery(
        tmp_path,
        project_id="project-1",
        source_operation_ids=["source-operation-1"],
    )
    recovery._contract_versions["test_step"] = "test-checkpoint-v1"

    result = recovery.recover(
        "test_step",
        is_reusable=lambda candidate: candidate.value == 1,
    )

    assert result == _CheckpointResult(value=1)
    assert recovery.reused_step_ids == ["test_step"]


def test_formal_retry_requires_matching_lineage_and_route(
    tmp_path,
    monkeypatch,
):
    behavior_resolver = _install_test_step(monkeypatch)
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        behavior_fingerprint_resolver=behavior_resolver,
    )
    writer("test_step", _CheckpointResult(value=1))
    recovery = localization_formal_retry.LocalizationFormalRetryRecovery(
        tmp_path,
        project_id="project-1",
        source_operation_ids=["source-operation-1"],
    )
    recovery._contract_versions["test_step"] = "test-checkpoint-v1"

    accepted = recovery.recover(
        "test_step",
        expected_lineage={"source_fingerprint": "a" * 64},
        expected_route={"phase": "test"},
    )

    assert accepted == _CheckpointResult(value=1)


def test_formal_retry_rejects_checkpoint_from_changed_node_behavior(
    tmp_path,
    monkeypatch,
):
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        behavior_fingerprint_resolver=lambda _step_id: "old-behavior",
    )
    writer("test_step", _CheckpointResult(value=1))
    checkpoint_path = (
        tmp_path
        / "source-operation-1"
        / "test_step"
        / "checkpoint.json"
    )
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))[
        "behavior_fingerprint"
    ] == "old-behavior"

    monkeypatch.setitem(
        localization_formal_retry.LOCALIZATION_WORKFLOW_NODE_SPECS,
        "test_step",
        SimpleNamespace(result_model=_CheckpointResult),
    )
    monkeypatch.setattr(
        localization_formal_retry,
        "checkpoint_behavior_fingerprint",
        lambda _step_id: "new-behavior",
    )
    recovery = localization_formal_retry.LocalizationFormalRetryRecovery(
        tmp_path,
        project_id="project-1",
        source_operation_ids=["source-operation-1"],
    )
    recovery._contract_versions["test_step"] = "test-checkpoint-v1"

    assert recovery.recover("test_step") is None
    assert recovery.reused_step_ids == []


def test_formal_retry_rejects_changed_behavior_for_internal_checkpoint_families(
    tmp_path,
    monkeypatch,
):
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        behavior_fingerprint_resolver=(
            localization_workflow_nodes.checkpoint_behavior_fingerprint
        ),
    )
    writer(
        "generate_localization_spoken_script.chunk_responses",
        _CheckpointResult(value=1),
    )
    writer(
        "finalize_localization_spoken_script.section_r01_0001",
        _CheckpointResult(value=2),
    )
    recovery = localization_formal_retry.LocalizationFormalRetryRecovery(
        tmp_path,
        project_id="project-1",
        source_operation_ids=["source-operation-1"],
    )
    actual = localization_formal_retry.checkpoint_behavior_fingerprint

    def changed_behavior(step_id: str) -> str | None:
        if step_id.startswith(
            (
                "generate_localization_spoken_script",
                "finalize_localization_spoken_script",
            )
        ):
            return "changed-behavior"
        return actual(step_id)

    monkeypatch.setattr(
        localization_formal_retry,
        "checkpoint_behavior_fingerprint",
        changed_behavior,
    )

    assert recovery.load_generation_checkpoint(_CheckpointResult) is None
    assert recovery.load_checkpoints_by_prefix(
        "finalize_localization_spoken_script.section_",
        result_model=_CheckpointResult,
    ) == []


def test_formal_retry_keeps_but_does_not_reuse_v1_checkpoint(
    tmp_path,
    monkeypatch,
):
    behavior_resolver = _install_test_step(monkeypatch)
    writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        behavior_fingerprint_resolver=behavior_resolver,
    )
    writer("test_step", _CheckpointResult(value=1))
    checkpoint_path = (
        tmp_path
        / "source-operation-1"
        / "test_step"
        / "checkpoint.json"
    )
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "localization-development-checkpoint-v1"
    payload.pop("behavior_fingerprint")
    checkpoint_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    assert development_checkpoints.load_development_checkpoint(
        tmp_path,
        project_id="project-1",
        workflow_operation_id="source-operation-1",
        step_id="test_step",
        result_model=_CheckpointResult,
    ) == _CheckpointResult(value=1)
    recovery = localization_formal_retry.LocalizationFormalRetryRecovery(
        tmp_path,
        project_id="project-1",
        source_operation_ids=["source-operation-1"],
    )
    recovery._contract_versions["test_step"] = "test-checkpoint-v1"

    assert recovery.recover("test_step") is None
    assert checkpoint_path.is_file()
