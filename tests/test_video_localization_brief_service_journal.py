"""Exercise service-owned journal injection without model or downstream work."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_video_localization_localization_source import _configure
from tests.test_video_localization_localization_context_intent import _draft
from tests.test_video_localization_localization_spoken_script import _source_and_brief
from app.domains.video_localization import development_llm_batches, service
from app.domains.video_localization.development_checkpoints import LocalizationDevelopmentCheckpointWriter
from app.domains.video_localization.localization_workflow_execution import LocalizationDevelopmentExecutionConfig
from app.schemas.voice_studio import ProjectCreate
from app.services import llm_runtime, project_store
from app.services.localization_ai_policy import LocalizationAiPhaseRoute
from app.errors import AppException
from tests.brief_stage_fixtures import stage_candidate


class _BriefReached(BaseException):
    """Stop the formal fixture immediately after its tested real atomic node."""


@pytest.fixture
def service_case(tmp_path, monkeypatch):
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(ProjectCreate(name="brief journal service fixture"))
    service.save_video_localization(project.project_id, _draft())
    route = LocalizationAiPhaseRoute(
        phase="document_understanding",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    policy = SimpleNamespace(routes=[route], route=lambda phase: route)
    monkeypatch.setattr(service.localization_ai_policy, "resolve_localization_ai_policy", lambda *a, **k: policy)
    resolved = llm_runtime.ResolvedProfile(
        profile_id="profile",
        protocol="openai_compatible",
        base_url="http://localhost:9000/v1",
        model_id="model",
        reasoning_effort="low",
    )
    monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: resolved)
    _, brief = _source_and_brief()
    candidate = brief.content.model_dump(mode="json")
    calls = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        calls.append((prompt, payload))
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=6000,
                timeout_seconds=600,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=1,
                finish_reason="stop",
            )
        )
        return stage_candidate(candidate, payload)

    monkeypatch.setattr(llm_runtime, "complete_json", complete)
    # No test may accidentally continue into a paid or visual evidence node.
    monkeypatch.setattr(
        service.localization_document_evidence,
        "collect_localization_document_research",
        lambda *a, **k: pytest.fail("unexpected downstream research"),
    )
    monkeypatch.setattr(
        service.localization_document_evidence,
        "collect_localization_document_visuals",
        lambda *a, **k: pytest.fail("unexpected downstream visual work"),
    )
    return project.project_id, calls


def _batch_records(root):
    return [
        json.loads(path.read_text())["result"]
        for path in root.rglob("checkpoint.json")
        if json.loads(path.read_text())["result"].get("contract_version") == "localization-development-llm-batch-v1"
    ]


def test_service_explicit_recovery_revalidates_candidates_without_fabricating_calls(
    tmp_path, monkeypatch, service_case,
):
    from app.domains.video_localization.development_candidate_recovery import DevelopmentCandidateReceiptStore
    from app.domains.video_localization.llm_candidate_provenance import DevelopmentCandidateImport

    project_id, calls = service_case
    old_root = tmp_path / "old"
    service.run_localization_v3_draft(
        project_id, operation_id="old-op", profile_id="profile",
        development_execution=LocalizationDevelopmentExecutionConfig(
            development_session_id="old-session", target_step_id="analyze_localization_document",
            snapshot_root=old_root,
        ),
    )
    assert len(calls) == 2
    new_root = tmp_path / "new"
    store = DevelopmentCandidateReceiptStore(
        root=new_root / "candidate-recovery", project_id=project_id,
        development_session_id="new-session", development_mode=True,
    )
    for record in _batch_records(old_root):
        store.import_candidate(DevelopmentCandidateImport(
            completeness="complete", batch_id=record["batch_id"], attempt=record["attempt"],
            candidate=record["raw_json_candidate"], source_evidence_fingerprint="e" * 64,
            baseline={key: record[key] for key in (
                "batch_id", "attempt", "input_fingerprint", "candidate_fingerprint",
            )},
        ))
    before = service.get_video_localization(project_id)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("recovered candidate called model"))
    _, summary = service.run_localization_v3_draft(
        project_id, operation_id="new-op", profile_id="profile",
        development_execution=LocalizationDevelopmentExecutionConfig(
            development_session_id="new-session", target_step_id="analyze_localization_document",
            snapshot_root=new_root, recover_verified_candidates=True,
        ),
    )
    assert "analyze_localization_document" in summary["executed_step_ids"]
    records = _batch_records(new_root)
    assert len(records) == 2
    assert all(record["status"] == "validation_passed" and not record["llm_calls"] for record in records)
    assert service.get_video_localization(project_id) == before


def test_service_recovery_store_is_never_opened_by_default(tmp_path, monkeypatch, service_case):
    project_id, calls = service_case
    monkeypatch.setattr(service, "DevelopmentCandidateReceiptStore", lambda **k: pytest.fail("implicit recovery store"))
    service.run_localization_v3_draft(
        project_id, operation_id="ordinary-dev", profile_id="profile",
        development_execution=LocalizationDevelopmentExecutionConfig(
            development_session_id="ordinary", target_step_id="analyze_localization_document", snapshot_root=tmp_path / "dev",
        ),
    )
    assert len(calls) == 2


def test_service_evidence_batches_receive_same_development_owner(tmp_path, monkeypatch, service_case):
    project_id, calls = service_case
    domain = service.localization_document_evidence
    monkeypatch.setattr(domain, "collect_localization_document_research", lambda brief:
        domain.LocalizationDocumentResearchResult(brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="r" * 64, status="not_needed"))
    monkeypatch.setattr(domain, "collect_localization_document_visuals", lambda brief, *a, **k:
        domain.LocalizationDocumentVisualResult(brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="v" * 64, status="not_needed"))
    root = tmp_path / "evidence-development"

    def reached(*args, batch_journal, **kwargs):
        assert batch_journal.root == root
        assert batch_journal.project_id == project_id
        assert batch_journal.session_id == "evidence-session"
        assert batch_journal.retry_rejected_execution_id == "evidence-op"
        assert batch_journal.candidate_recovery_store is None
        raise _BriefReached()

    monkeypatch.setattr(domain, "adjudicate_localization_document_evidence", reached)
    with pytest.raises(_BriefReached):
        service.run_localization_v3_draft(
            project_id, operation_id="evidence-op", profile_id="profile",
            development_execution=LocalizationDevelopmentExecutionConfig(
                development_session_id="evidence-session", target_step_id="adjudicate_localization_evidence_v3",
                snapshot_root=root,
            ),
        )
    assert len(calls) == 2


def test_service_development_injects_replay_without_atomic_callback(tmp_path, monkeypatch, service_case):
    project_id, calls = service_case
    before = service.get_video_localization(project_id)
    root = tmp_path / "development"
    development = LocalizationDevelopmentExecutionConfig(
        development_session_id="same-session", target_step_id="analyze_localization_document", snapshot_root=root
    )
    _, first = service.run_localization_v3_draft(
        project_id, operation_id="op-first", profile_id="profile", development_execution=development
    )
    assert len(calls) == 2
    records = _batch_records(root)
    assert {record["batch_id"] for record in records} == {"brief-outline", "brief-details-chunk_0001"}
    assert all(record["status"] == "validation_passed" for record in records)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("development candidate not reused"))
    _, second = service.run_localization_v3_draft(
        project_id, operation_id="op-second", profile_id="profile", development_execution=development
    )
    assert "analyze_localization_document" in first["executed_step_ids"]
    assert "analyze_localization_document" in second["executed_step_ids"]
    assert "lock_localization_source" in second["reused_step_ids"]
    assert all(len(record["validations"]) == 2 for record in _batch_records(root))
    assert service.get_video_localization(project_id) == before


def test_service_formal_diagnostic_callback_writes_but_never_reads_candidates(tmp_path, monkeypatch, service_case):
    project_id, calls = service_case
    before = service.get_video_localization(project_id)
    root = tmp_path / "formal-diagnostics"
    writer = LocalizationDevelopmentCheckpointWriter(root, project_id=project_id, workflow_operation_id="formal-op")
    statuses = []

    def callback(step_id, result):
        if result is not None and getattr(result, "contract_version", None) == "localization-development-llm-batch-v1":
            statuses.append(result.status)
            writer(step_id, result)
        if step_id == "analyze_localization_document":
            raise _BriefReached()

    monkeypatch.setattr(
        development_llm_batches,
        "load_development_checkpoint",
        lambda *a, **k: pytest.fail("formal journal must not read"),
    )
    for _ in range(2):
        with pytest.raises(_BriefReached):
            service.run_localization_v3_draft(
                project_id, operation_id="formal-op", profile_id="profile", on_atomic_result=callback
            )
    assert len(calls) == 4
    assert statuses == ["prepared", "response_received", "validation_passed"] * 4
    assert len(_batch_records(root)) == 2
    assert service.get_video_localization(project_id) == before


def test_service_without_journal_keeps_legacy_single_argument_node_call(tmp_path, monkeypatch, service_case):
    project_id, calls = service_case
    before = service.get_video_localization(project_id)
    actual = service.localization_document_brief.analyze_localization_document
    results = []

    # Intentionally no **kwargs: optional wiring must not add a None keyword.
    def legacy_node(request):
        results.append(actual(request))
        raise _BriefReached()

    monkeypatch.setattr(service.localization_document_brief, "analyze_localization_document", legacy_node)
    monkeypatch.setattr(service, "DevelopmentLlmBatchReplay", lambda *a, **k: pytest.fail("disabled journal created"))
    with pytest.raises(_BriefReached):
        service.run_localization_v3_draft(project_id, operation_id="ordinary-formal", profile_id="profile")
    assert len(calls) == 2
    assert results[0].quality_summary.status == "passed"
    assert _batch_records(tmp_path) == []
    assert service.get_video_localization(project_id) == before


@pytest.mark.parametrize("failure_code,status_code", [("codex_cli_rate_limited", 429), ("codex_cli_execution_failed", 502)])
def test_service_new_development_execution_recovers_only_explicit_rate_refusal(
    tmp_path, monkeypatch, service_case, failure_code, status_code
):
    project_id, successful_calls = service_case
    complete = llm_runtime.complete_json
    refused = False

    def refuse_first_detail(prompt, payload, **kwargs):
        nonlocal refused
        if "core_source_cues" in payload and not refused:
            refused = True
            raise llm_runtime.LlmRuntimeError(
                "fixed quota refusal", code=failure_code, status_code=status_code
            )
        return complete(prompt, payload, **kwargs)

    monkeypatch.setattr(llm_runtime, "complete_json", refuse_first_detail)
    root = tmp_path / "development"
    development = LocalizationDevelopmentExecutionConfig(
        development_session_id="quota-session", target_step_id="analyze_localization_document", snapshot_root=root
    )
    with pytest.raises(AppException, match="fixed quota refusal"):
        service.run_localization_v3_draft(
            project_id, operation_id="refused-operation", profile_id="profile", development_execution=development
        )
    failed = next(path for path in root.rglob("checkpoint.json")
                  if json.loads(path.read_text())["result"].get("status") == "call_failed")
    failed_bytes = failed.read_bytes()
    assert len(successful_calls) == 1

    if failure_code == "codex_cli_execution_failed":
        with pytest.raises(AppException, match="不会自动重复模型调用"):
            service.run_localization_v3_draft(
                project_id, operation_id="unapproved-operation", profile_id="profile", development_execution=development
            )
        assert len(successful_calls) == 1
        development = replace(development, retry_unknown_batch_step_id=json.loads(failed_bytes)["step_id"])

    _, resumed = service.run_localization_v3_draft(
        project_id, operation_id="resumed-operation", profile_id="profile", development_execution=development
    )
    assert len(successful_calls) == 2
    assert failed.read_bytes() == failed_bytes
    records = _batch_records(root)
    recovery = next(record for record in records if record.get("recovery_ordinal") == 1)
    assert recovery["recovery_execution_id"] == "resumed-operation"
    assert recovery["status"] == "validation_passed"
    assert "lock_localization_source" in resumed["reused_step_ids"]
    development = replace(development, retry_unknown_batch_step_id=None)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("paid response was not reused"))
    service.run_localization_v3_draft(
        project_id, operation_id="replayed-operation", profile_id="profile", development_execution=development
    )
    assert failed.read_bytes() == failed_bytes


@pytest.mark.parametrize("target,module_name,function_name", [
    ("generate_localization_spoken_script", "localization_spoken_script", "generate_localization_spoken_script"),
    ("review_localization_fidelity", "localization_spoken_script", "review_localization_spoken_script_fidelity"),
    ("review_localization_naturalness", "localization_spoken_script", "review_localization_spoken_script_naturalness"),
    ("adjudicate_localization_alignment", "localization_alignment_adjudication", "adjudicate_localization_alignment"),
    ("adjudicate_localization_display_boundaries", "localization_display_adjudication", "adjudicate_display_boundaries"),
])
def test_remaining_paid_nodes_receive_isolated_journal(
    tmp_path, monkeypatch, service_case, target, module_name, function_name,
):
    import numpy as np

    project_id, _ = service_case
    before = service.get_video_localization(project_id)
    route = LocalizationAiPhaseRoute(
        phase="document_understanding", profile_id="profile", model_id="model",
        reasoning_effort="low", output_format="json", prompt_strategy="adaptive",
    )
    monkeypatch.setattr(service.localization_ai_policy, "resolve_localization_ai_policy",
        lambda *a, **k: SimpleNamespace(routes=[route],
            route=lambda phase: route.model_copy(update={
                "phase": phase,
                "output_format": "markdown" if phase == "spoken_script_creation" else "json",
            })))
    domain = service.localization_document_evidence
    monkeypatch.setattr(domain, "collect_localization_document_research", lambda brief:
        domain.LocalizationDocumentResearchResult(brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="r" * 64, status="not_needed"))
    monkeypatch.setattr(domain, "collect_localization_document_visuals", lambda brief, *a, **k:
        domain.LocalizationDocumentVisualResult(brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="v" * 64, status="not_needed"))
    brief_complete = llm_runtime.complete_json

    def complete(prompt, payload, *, trace_sink, **kwargs):
        if "editable_source_cues" not in payload and "localized_full_text" not in payload:
            return brief_complete(prompt, payload, trace_sink=trace_sink, **kwargs)
        trace_sink(llm_runtime.LlmCompletionTrace(
            profile_id="profile", model_id="model", provider_host="local",
            request_chars=100, request_body_bytes=120, max_tokens=5000,
            timeout_seconds=300, reasoning_effort_requested="low",
            reasoning_control_applied=True, duration_ms=1, finish_reason="stop",
        ))
        if "editable_source_cues" in payload:
            return {"chunk_id": payload["chunk_id"], "suggested_title": "问候",
                    "paragraphs": ["你好，世界。"]}
        if "source_full_text" in payload:
            return {"status": "passed", "issues": [], "summary_zh": "原意一致。"}
        return {"impression": "original_chinese_transcript", "naturalness": 4.8,
                "persona": 4.8, "emotion": 4.8, "flow": 4.8,
                "issues": [], "summary_zh": "表达自然。"}

    monkeypatch.setattr(llm_runtime, "complete_json", complete)

    class FixedEncoder:
        model_id = "sentence-transformers/LaBSE"
        model_fingerprint = "f" * 64

        def encode(self, texts):
            return np.tile(np.array([1.0, 0.0], dtype=np.float32), (len(texts), 1))

    monkeypatch.setattr(service, "LabseTextEncoder", FixedEncoder)
    root = tmp_path / "remaining-development"

    def reached(*args, batch_journal, **kwargs):
        assert batch_journal.root == root
        assert batch_journal.project_id == project_id
        assert batch_journal.session_id == "remaining-session"
        assert batch_journal.prefix == f"{target}.llm_batch"
        assert batch_journal.retry_rejected_execution_id == "remaining-op"
        assert batch_journal.candidate_recovery_store is None
        raise _BriefReached()

    monkeypatch.setattr(getattr(service, module_name), function_name, reached)
    with pytest.raises(_BriefReached):
        service.run_localization_v3_draft(
            project_id, operation_id="remaining-op", profile_id="profile",
            development_execution=LocalizationDevelopmentExecutionConfig(
                development_session_id="remaining-session", target_step_id=target,
                snapshot_root=root,
            ),
        )
    assert service.get_video_localization(project_id) == before
