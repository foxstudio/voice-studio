"""The neutral contracts preserve old imports, JSON and planning behavior."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import localization_brief_contracts as contracts  # noqa: E402
from app.domains.video_localization import localization_document_brief as facade  # noqa: E402
from app.domains.video_localization.localization_generation_chunks import plan_localization_generation_chunks  # noqa: E402
from tests.test_video_localization_localization_document_brief import _inputs  # noqa: E402
from tests.test_video_localization_localization_document_evidence import _brief  # noqa: E402


@pytest.mark.parametrize("name", [
    "LocalizationDocumentSection", "LocalizationSpeakerProfile", "LocalizationEmotionalArcItem",
    "LocalizationImmutableFact", "LocalizationTermRelation", "LocalizationEvidenceQuestion",
    "LocalizationSpeechQualificationCandidate", "LocalizationTerminologyDecision", "LocalizationSemanticAttention",
    "LocalizationCreativeStrategyDraft", "LocalizationDocumentBriefContent", "LocalizationDocumentBriefInput",
    "LocalizationDocumentBriefQualitySummary", "LocalizationDocumentBriefResult", "LocalizationDocumentBriefAdaptiveRules",
    "LocalizationDocumentBriefSourceCue", "LocalizationDocumentBriefSourcePayload",
])
def test_facade_reexports_identical_class_objects(name):
    assert getattr(facade, name) is getattr(contracts, name)


@pytest.mark.parametrize("version", ["v13", "v14"])
@pytest.mark.parametrize("contract_version", ["v2", "v3"])
def test_result_versions_roundtrip_without_relabeling_history(version, contract_version):
    _, result = _brief(with_question=True)
    serialized = result.model_dump(mode="json")
    serialized["prompt_version"] = f"localization-document-brief-prompt-{version}"
    serialized["contract_version"] = f"localization-document-brief-{contract_version}"
    restored = contracts.LocalizationDocumentBriefResult.model_validate(serialized)
    assert restored.model_dump(mode="json") == serialized
    assert restored.result_fingerprint == result.result_fingerprint
    assert restored.contract_version == serialized["contract_version"]
    assert facade.LocalizationDocumentBriefResult.model_validate_json(restored.model_dump_json()) == restored


def test_new_results_default_v14_and_unknown_versions_are_rejected():
    _, result = _brief(with_question=False)
    assert result.prompt_version == contracts.PROMPT_VERSION == facade.PROMPT_VERSION == "localization-document-brief-prompt-v14"
    serialized = result.model_dump(mode="json")
    serialized.pop("prompt_version")
    assert contracts.LocalizationDocumentBriefResult.model_validate(serialized).prompt_version.endswith("v14")
    serialized["prompt_version"] = "localization-document-brief-prompt-v12"
    with pytest.raises(ValidationError):
        contracts.LocalizationDocumentBriefResult.model_validate(serialized)


def test_typed_source_payload_preserves_existing_projection_exactly():
    source, context, route = _inputs()
    request = contracts.LocalizationDocumentBriefInput(
        source_operation_id="source", context_operation_id="context", source_lock=source,
        context_intent=context, route=route,
    )
    raw = facade._prompt_payload(request)
    typed = contracts.LocalizationDocumentBriefSourcePayload.model_validate(raw)
    assert typed.model_dump(mode="json") == raw
    assert isinstance(typed.document_context, contracts.LocalizationDocumentContext)
    assert isinstance(typed.delivery_intent, contracts.LocalizationDeliveryIntent)
    assert set(type(typed.source_cues[0]).model_fields) == {"cue_id", "text", "speaker_id", "quality_flags"}
    invalid = {**raw, "source_cues": [{**raw["source_cues"][0], "start_ms": 0}]}
    with pytest.raises(ValidationError):
        contracts.LocalizationDocumentBriefSourcePayload.model_validate(invalid)


def test_105_facts_are_preserved_without_truncation():
    _, result = _brief(with_question=False)
    raw = result.content.model_dump(mode="json")
    fact = raw["immutable_facts"][0]
    raw["immutable_facts"] = [{**fact, "fact_id": f"fact_{index:04d}"} for index in range(1, 106)]
    content = contracts.LocalizationDocumentBriefContent.model_validate(raw)
    assert content.model_dump(mode="json") == raw
    assert contracts.validate_localization_brief_storage_capacity(content) > 0
    restored = contracts.LocalizationDocumentBriefResult.model_validate({**result.model_dump(mode="json"), "content": raw})
    assert restored.contract_version == "localization-document-brief-v3"
    assert len(restored.content.immutable_facts) == 105


def test_planner_accepts_old_facade_and_new_contracts_without_loading_facade():
    source, result = _brief(with_question=False)
    original = plan_localization_generation_chunks(source_fingerprint=source.source_fingerprint,
        cues=source.input.cues, sections=result.content.structure, pauses=source.input.pauses)
    restored_sections = [contracts.LocalizationDocumentSection.model_validate(section.model_dump(mode="json"))
                         for section in result.content.structure]
    restored = plan_localization_generation_chunks(source_fingerprint=source.source_fingerprint,
        cues=source.input.cues, sections=restored_sections, pauses=source.input.pauses)
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
    assert [cue_id for chunk in restored.chunks for cue_id in chunk.source_cue_ids] == [cue.cue_id for cue in source.input.cues]
    # Fresh interpreter proves actual import direction, not just source text.
    checked = subprocess.run([sys.executable, "-B", "-c",
        "import sys; import app.domains.video_localization.localization_generation_chunks; "
        "assert 'app.domains.video_localization.localization_document_brief' not in sys.modules"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend")},
        capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0, checked.stderr
