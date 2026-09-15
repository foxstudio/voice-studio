"""Fixed multimodal evidence reentry through the public domain facade."""
import hashlib
import json

import pytest

from tests.test_video_localization_development_llm_batches import fixed_profile, _trace  # noqa: F401
from tests.test_video_localization_localization_document_evidence import _brief
from app.domains.video_localization import localization_document_evidence as evidence
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.domains.video_localization.localization_document_brief import LocalizationEvidenceQuestion
from app.services import llm_runtime
from app.services.localization_ai_policy import LocalizationAiPhaseRoute


def _case(tmp_path, monkeypatch):
    _, brief = _brief(with_question=False)
    questions = [LocalizationEvidenceQuestion(
        question_id=f"question_{i + 1:04d}", kind="visual", question_zh="画面文字是什么？",
        source_cue_ids=["cue_0001"], reason_zh="核对拼写",
    ) for i in range(2)]
    brief = brief.model_copy(update={"content": brief.content.model_copy(update={"evidence_questions": questions})})
    frames = []
    for index, question in enumerate(questions):
        image = tmp_path / f"frame-{index}.jpg"
        data = f"fixed image bytes {index}".encode()
        image.write_bytes(data)
        frames.append(evidence.LocalizationDocumentVisualFrame(
            question_id=question.question_id, question_zh=question.question_zh,
            source_cue_ids=["cue_0001"], timestamp_ms=index, path=str(image), sha256=hashlib.sha256(data).hexdigest(),
        ))
    monkeypatch.setattr(llm_runtime, "MAX_IMAGE_COUNT", 1)
    visual = evidence.LocalizationDocumentVisualResult(
        brief_fingerprint=brief.result_fingerprint, result_fingerprint="a" * 64, frames=frames, status="passed",
    )
    research = evidence.LocalizationDocumentResearchResult(
        brief_fingerprint=brief.result_fingerprint, result_fingerprint="c" * 64, status="not_needed",
    )
    route = LocalizationAiPhaseRoute(phase="evidence_adjudication", profile_id="profile", model_id="model",
        reasoning_effort="low", output_format="json", prompt_strategy="adaptive")
    return brief, research, visual, route


def _journal(root, execution):
    return DevelopmentLlmBatchReplay(root=root, project_id="project", development_session_id="session",
        step_id_prefix="evidence.llm_batch", retry_rejected_execution_id=execution)


def _answer(payload):
    return {"answers": [{"question_id": q["question_id"], "source_cue_ids": q["source_cue_ids"],
        "evidence_image_positions": [1], "status": "supported", "conclusion_zh": "可读文字",
        "constraint_zh": "使用可读拼写", "source_urls": []} for q in payload["questions"]]}


def test_later_image_batch_refusal_reuses_earlier_candidate(tmp_path, monkeypatch):
    brief, research, visual, route = _case(tmp_path, monkeypatch)
    calls = []

    def complete(prompt, payload, images, *, trace_sink, **kwargs):
        question = payload["questions"][0]["question_id"]
        calls.append(question)
        _trace(trace_sink)
        if calls == ["question_0001", "question_0002"]:
            raise llm_runtime.LlmRuntimeError("拒绝", code="codex_cli_rate_limited", status_code=429)
        return _answer(payload)

    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", complete)
    root = tmp_path / "journal"
    with pytest.raises(llm_runtime.LlmRuntimeError):
        evidence.adjudicate_localization_document_evidence(brief, research, visual, route=route,
            batch_journal=_journal(root, "op1"))
    first = [json.loads(p.read_text())["result"] for p in root.rglob("checkpoint.json")]
    assert next(x for x in first if x["batch_id"] == "evidence-images-01")["status"] == "response_received"
    result = evidence.adjudicate_localization_document_evidence(brief, research, visual, route=route,
        batch_journal=_journal(root, "op2"))
    assert calls == ["question_0001", "question_0002", "question_0002"]
    assert result.status == "passed" and len(result.answers) == 2
    replay = evidence.adjudicate_localization_document_evidence(brief, research, visual, route=route,
        batch_journal=_journal(root, "op3"))
    assert calls == ["question_0001", "question_0002", "question_0002"]
    assert replay.model_dump() == result.model_dump()


def test_unsupported_images_replays_original_text_fallback(tmp_path, monkeypatch):
    brief, research, visual, route = _case(tmp_path, monkeypatch)
    calls = []

    def image_call(*args, trace_sink, **kwargs):
        calls.append("image")
        _trace(trace_sink)
        raise llm_runtime.LlmRuntimeError("不支持图片", code="llm_image_input_unsupported", status_code=400)

    def text_call(prompt, payload, *, trace_sink, **kwargs):
        calls.append("text")
        assert payload["visual_fallback_reason"] == "model_does_not_support_images"
        _trace(trace_sink)
        return {"answers": [{"question_id": q["question_id"], "status": "uncertain",
            "conclusion_zh": "无图不能核实"} for q in payload["questions"]]}

    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", image_call)
    monkeypatch.setattr(llm_runtime, "complete_json", text_call)
    root = tmp_path / "journal"
    original = evidence.adjudicate_localization_document_evidence(brief, research, visual, route=route,
        batch_journal=_journal(root, "op1"))
    replay = evidence.adjudicate_localization_document_evidence(brief, research, visual, route=route,
        batch_journal=_journal(root, "op2"))
    assert calls == ["image", "text", "text"]
    assert replay.model_dump() == original.model_dump()
    assert replay.status == "warning"
