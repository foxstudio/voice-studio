"""Explicit evidence import reuses the single batch path without fake calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.development_candidate_recovery import DevelopmentCandidateReceiptStore  # noqa: E402
from app.domains.video_localization.llm_candidate_provenance import DevelopmentCandidateImport  # noqa: E402
from app.domains.video_localization import development_llm_batches as batches  # noqa: E402
from app.domains.video_localization import localization_document_brief as facade  # noqa: E402
from app.services import llm_runtime  # noqa: E402
from app.services.video_localization_llm_provider_execution import provider_configuration_fingerprint  # noqa: E402
from tests.test_video_localization_brief_stages import setup as _stage_setup, _provider, _outline, _details, _records  # noqa: E402
from tests.test_video_localization_localization_document_evidence import _brief  # noqa: E402


@pytest.fixture
def setup(monkeypatch):
    return _stage_setup.__wrapped__(monkeypatch)


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _store(root, project="project", session="new-session"):
    return DevelopmentCandidateReceiptStore(root=root, project_id=project, development_session_id=session, development_mode=True)


def _journal(root, store=None, session="new-session"):
    return batches.DevelopmentLlmBatchReplay(root=root, project_id="project", development_session_id=session,
        step_id_prefix="brief", candidate_recovery_store=store)


def _import_record(store, record):
    return store.import_candidate(DevelopmentCandidateImport(
        completeness="complete", batch_id=record["batch_id"], attempt=record["attempt"],
        candidate=record["raw_json_candidate"], source_evidence_fingerprint="e" * 64,
        baseline={"batch_id": record["batch_id"], "attempt": record["attempt"],
                  "input_fingerprint": record["input_fingerprint"], "candidate_fingerprint": record["candidate_fingerprint"]},
    ))


def _evidence():
    profile = llm_runtime.resolve_profile("profile")
    call_input = batches.DevelopmentLlmCallInput(system_prompt="prompt", user_payload={"data": "actual"},
        profile_id="profile", model_id="model", profile_configuration_fingerprint=provider_configuration_fingerprint(profile),
        temperature=0.0, max_tokens=6000, timeout=600, reasoning_effort="low")
    candidate = {"value": "exact original"}
    return DevelopmentCandidateImport(completeness="complete", batch_id="batch", attempt=0,
        candidate=candidate, source_evidence_fingerprint="e" * 64,
        baseline={"batch_id": "batch", "attempt": 0, "input_fingerprint": _sha(call_input.model_dump(mode="json")), "candidate_fingerprint": _sha(candidate)})


def _call(journal, *, prompt="prompt", payload=None):
    attempt = journal.attempt(batch_id="batch", attempt=0, model_id="model", call_id="new-call",
                              purpose="localization_document_brief", round_index=1)
    result = attempt.complete_json(prompt, payload or {"data": "actual"}, profile_id="profile", temperature=0.0,
                                   max_tokens=6000, timeout=600, reasoning_effort="low")
    return attempt, result


def test_explicit_import_creates_new_session_record_without_telemetry(setup, tmp_path):
    store = _store(tmp_path / "receipts")
    evidence = _evidence()
    receipt = store.import_candidate(evidence)
    assert store.import_candidate(evidence) == receipt
    journal = _journal(tmp_path / "journal", store)
    attempt, candidate = _call(journal)
    assert candidate == evidence.candidate
    assert attempt.reused_calls == []
    assert attempt.checkpoint.status == "candidate_imported"
    assert attempt.checkpoint.llm_calls == []
    assert attempt.recovered_candidate.telemetry == "unavailable"
    attempt.record_validation(validator_version="current-domain")
    replay, same = _call(_journal(tmp_path / "journal"))
    assert same == candidate
    assert replay.recovered_candidate == receipt.provenance
    assert replay.checkpoint.validations[0].validator_version == "current-domain"
    serialized = json.loads(next((tmp_path / "journal").rglob("checkpoint.json")).read_text())
    assert serialized["workflow_operation_id"] == "new-session"
    assert serialized["result"]["llm_calls"] == []
    assert "duration_ms" not in json.dumps(serialized)


@pytest.mark.parametrize("change", ["prompt", "payload", "profile"])
def test_changed_actual_input_fails_closed_without_provider_call(setup, tmp_path, monkeypatch, change):
    store = _store(tmp_path / "receipts")
    store.import_candidate(_evidence())
    if change == "profile":
        profile = llm_runtime.resolve_profile("profile")
        from dataclasses import replace
        monkeypatch.setattr(llm_runtime, "resolve_profile", lambda *a, **k: replace(profile, base_url="http://localhost:9999/v1"))
    with pytest.raises(ValueError, match="SHA"):
        _call(_journal(tmp_path / "journal", store), prompt="changed" if change == "prompt" else "prompt",
              payload={"data": "changed"} if change == "payload" else None)
    assert not list((tmp_path / "journal").rglob("checkpoint.json"))


@pytest.mark.parametrize("mutation", ["partial", "missing_baseline", "candidate", "batch", "attempt"])
def test_incomplete_or_mismatched_import_is_refused(setup, tmp_path, mutation):
    raw = _evidence().model_dump(mode="json")
    if mutation == "partial":
        raw["completeness"] = "partial"
    elif mutation == "missing_baseline":
        del raw["baseline"]
    elif mutation == "candidate":
        raw["candidate"]["value"] = "changed"
    elif mutation == "batch":
        raw["batch_id"] = "other"
    else:
        raw["attempt"] = 1
    with pytest.raises((ValueError, ValidationError)):
        _store(tmp_path).import_candidate(DevelopmentCandidateImport.model_validate(raw))
    assert not list(tmp_path.rglob("checkpoint.json"))


def test_corrupt_receipt_fails_closed_and_conflicting_import_cannot_overwrite(setup, tmp_path):
    store = _store(tmp_path / "receipts")
    evidence = _evidence()
    store.import_candidate(evidence)
    modified = evidence.model_copy(update={"source_evidence_fingerprint": "f" * 64})
    with pytest.raises(ValueError, match="禁止覆盖"):
        store.import_candidate(modified)
    path = next((tmp_path / "receipts").rglob("checkpoint.json"))
    path.write_text("corrupt")
    with pytest.raises(ValueError, match="缺损"):
        _call(_journal(tmp_path / "journal", store))


def test_formal_and_cross_identity_replay_cannot_use_receipts(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="正式"):
        batches.DevelopmentLlmBatchReplay(root=None, project_id="project", development_session_id="new-session",
            step_id_prefix="brief", checkpoint_writer=lambda *a: None, candidate_recovery_store=store)
    with pytest.raises(ValueError, match="同项目"):
        _journal(tmp_path / "journal", store, session="different")
    with pytest.raises(ValueError, match="开发模式"):
        DevelopmentCandidateReceiptStore(root=tmp_path, project_id="project", development_session_id="s", development_mode=False)


def _record_real_fixture_calls(setup, tmp_path, monkeypatch):
    request, payload = setup
    cue_ids = [cue.cue_id for cue in payload.source_cues]
    calls = []
    original = tmp_path / "original"
    _provider(monkeypatch, [_outline(cue_ids), *[_details(c) for c in cue_ids]], calls, original)
    facade.analyze_localization_document(request, batch_journal=_journal(original, session="old-session"))
    return request, _records(original)


@pytest.mark.parametrize("recover_all", [True, False])
def test_same_facade_validates_recovered_candidates_and_reports_unknown_calls(setup, tmp_path, monkeypatch, recover_all):
    request, records = _record_real_fixture_calls(setup, tmp_path, monkeypatch)
    store = _store(tmp_path / "receipts")
    imported = records if recover_all else [next(x for x in records if x["batch_id"] == "brief-outline")]
    for record in imported:
        _import_record(store, record)
    new_calls = []
    expected = {x["batch_id"]: x["raw_json_candidate"] for x in records}
    remaining = [_details(c.cue_id) for c in setup[1].source_cues]
    if recover_all:
        monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("paid call while all candidates recovered"))
    else:
        _provider(monkeypatch, remaining, new_calls, tmp_path / "current")
    result = facade.analyze_localization_document(request, batch_journal=_journal(tmp_path / "current", store))
    assert result.quality_summary.status == "warning"
    assert result.quality_summary.model_call_count is None
    assert result.quality_summary.recorded_model_call_count == len(new_calls)
    assert result.quality_summary.recovered_candidate_count == len(imported)
    assert result.quality_summary.call_telemetry_complete is False
    assert len(result.llm_calls) == len(new_calls)
    assert len(result.recovered_candidates) == len(imported)
    for record in _records(tmp_path / "current"):
        assert record["raw_json_candidate"] == expected[record["batch_id"]]
        assert record["status"] == "validation_passed"
    assert facade.LocalizationDocumentBriefResult.model_validate_json(result.model_dump_json()) == result
    view = facade.project_localization_document_brief_step_result(result)
    assert view["status"] == "warning"
    assert any("未知" in note for note in view["notes"])
    assert any("恢复来源" in section["title"] for section in view["debug"]["sections"])
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("replay called model"))
    assert facade.analyze_localization_document(request, batch_journal=_journal(tmp_path / "current")) == result


@pytest.mark.parametrize("invalid", ["schema", "reference"])
def test_recovered_invalid_candidate_reaches_original_validator_without_paid_repair(setup, tmp_path, monkeypatch, invalid):
    request, records = _record_real_fixture_calls(setup, tmp_path, monkeypatch)
    store = _store(tmp_path / "receipts")
    for record in records:
        record = deepcopy(record)
        if record["batch_id"] == "brief-details-chunk_0001":
            if invalid == "schema":
                del record["raw_json_candidate"]["terminology"]
            else:
                record["raw_json_candidate"]["immutable_facts"][0]["source_cue_ids"] = ["cue_9999"]
            record["candidate_fingerprint"] = _sha(record["raw_json_candidate"])
        _import_record(store, record)
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *a, **k: pytest.fail("invalid imported candidate triggered paid repair"))
    with pytest.raises(ValueError):
        facade.analyze_localization_document(request, batch_journal=_journal(tmp_path / "current", store))
    records = _records(tmp_path / "current")
    failed = next(x for x in records if x["batch_id"] == "brief-details-chunk_0001")
    assert failed["status"] == "validation_failed"
    assert failed["llm_calls"] == []
    assert failed["recovered_candidate"]["telemetry"] == "unavailable"


def test_legacy_v2_dump_remains_exact_and_zero_trace_without_provenance_is_rejected():
    _, brief = _brief(with_question=False)
    raw = brief.model_dump(mode="json")
    raw["contract_version"] = "localization-document-brief-v2"
    raw["prompt_version"] = "localization-document-brief-prompt-v13"
    assert facade.LocalizationDocumentBriefResult.model_validate(raw).model_dump(mode="json") == raw
    assert "recovered_candidates" not in raw
    assert "recorded_model_call_count" not in raw["quality_summary"]
    raw["llm_calls"] = []
    with pytest.raises(ValidationError):
        facade.LocalizationDocumentBriefResult.model_validate(raw)
