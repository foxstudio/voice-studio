"""Fixed observation/core boundaries; no inference or production project data.

The tutorial baseline is an excerpt of the sealed recovery candidate (q21).
The independent drama baseline uses the same partial-screen-evidence relation.
These tests establish projection safety, not probabilistic translation quality.
"""
import copy
import hashlib
import json

import pytest

from tests.test_video_localization_development_llm_batches import fixed_profile, _trace  # noqa: F401
from tests.test_video_localization_localization_creation_context import _request
from app.domains.video_localization import localization_creation_context as creation
from app.domains.video_localization import localization_document_evidence as evidence
from app.domains.video_localization import localization_source
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.domains.video_localization.localization_document_brief import LocalizationEvidenceQuestion
from app.services import llm_runtime
from app.services.localization_ai_policy import LocalizationAiPhaseRoute


BASELINES = {
    "tutorial": {
        "texts": ["so without 3D visualization", "they simply have No sales", "Order renders too early"],
        "observation": "画面有硬字幕，连续呈现 NOTHING TO FILM → NO VISUALS → NO SALES，但画面没有 without 3D visualization。",
        "constraint": "不得把展示画面擅自限定为三维展示。",
        "anchor": "没有东西可拍，就没有展示画面，也就没有销量。",
    },
    "drama": {
        "texts": ["She cannot leave until the bridge is repaired.", "He whispered an unclear name.", "The guard returned at dawn."],
        "observation": "画面字幕只显示她不能离开，没有显示修桥条件；姓名不可读。",
        "constraint": "不能仅从画面确认修桥条件或姓名。",
        "anchor": "她不能离开。",
    },
}


def _case(tmp_path, name):
    baseline = BASELINES[name]
    request = _request()
    original = request.source_lock.input
    cues, words = [], []
    for index, text in enumerate(baseline["texts"], 1):
        word_id = f"word_{index:04d}"
        words.append(original.words[0].model_copy(update={
            "word_id": word_id, "text": text, "start_ms": index * 1000,
            "end_ms": index * 1000 + 500,
        }))
        cues.append(original.cues[0].model_copy(update={
            "cue_id": f"cue_{index:04d}", "text": text,
            "start_ms": index * 1000, "end_ms": index * 1000 + 500,
            "source_word_ids": [word_id],
            "quality_flags": ["asr_unresolved_text"] if index == 2 else [],
        }))
    source = localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
        original.model_copy(update={"cues": cues, "words": words, "pauses": []})
    )
    question = LocalizationEvidenceQuestion(
        question_id="question_0001", kind="visual", question_zh="画面能否核实这段未确认文字？",
        source_cue_ids=["cue_0002"], reason_zh="不能把候选原文当作已核实内容。",
    )
    brief = request.document_brief.model_copy(update={
        "source_fingerprint": source.source_fingerprint,
        "content": request.document_brief.content.model_copy(update={
            "structure": [request.document_brief.content.structure[0].model_copy(update={
                "source_cue_ids": [cue.cue_id for cue in cues],
            })], "evidence_questions": [question],
        }),
    })
    request = request.model_copy(update={
        "source_lock": source, "document_brief": brief,
        "context_intent": request.context_intent.model_copy(update={"source_fingerprint": source.source_fingerprint}),
    })
    frames = []
    for index in (1, 2, 3):
        path = tmp_path / f"{name}-{index}.jpg"
        data = f"fixed frame {name} {index}".encode()
        path.write_bytes(data)
        frames.append(evidence.LocalizationDocumentVisualFrame(
            question_id=question.question_id, question_zh=question.question_zh,
            source_cue_ids=[f"cue_{index:04d}"], timestamp_ms=index * 1000,
            path=str(path), sha256=hashlib.sha256(data).hexdigest(),
        ))
    visual = evidence.LocalizationDocumentVisualResult(
        brief_fingerprint=brief.result_fingerprint, result_fingerprint="v" * 64, frames=frames, status="passed",
    )
    research = evidence.LocalizationDocumentResearchResult(
        brief_fingerprint=brief.result_fingerprint, result_fingerprint="r" * 64, status="not_needed",
    )
    candidate = {"answers": [{
        "question_id": question.question_id, "evidence_image_positions": [1, 2, 3],
        "anchored_image_positions": [1], "status": "supported",
        "conclusion_zh": baseline["observation"], "constraint_zh": baseline["constraint"],
        "anchored_target_text_zh": baseline["anchor"], "source_urls": [],
    }]}
    route = LocalizationAiPhaseRoute(phase="evidence_adjudication", profile_id="profile", model_id="model",
        reasoning_effort="low", output_format="json", prompt_strategy="adaptive")
    return request, research, visual, candidate, route


@pytest.mark.parametrize("name", BASELINES)
def test_visual_observation_never_expands_editable_core(tmp_path, monkeypatch, name):
    request, research, visual, raw, route = _case(tmp_path, name)
    original = request.model_dump(mode="json")
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", lambda *_a, **_k: copy.deepcopy(raw))
    result = evidence.adjudicate_localization_document_evidence(request.document_brief, research, visual, route=route)
    answer = result.answers[0]
    assert answer.source_cue_ids == ["cue_0002"]
    assert answer.observed_source_cue_ids == ["cue_0001", "cue_0002", "cue_0003"]
    assert answer.anchored_source_cue_ids == ["cue_0001"]
    locked = creation.lock_localization_creation_context(request.model_copy(update={"evidence": result}))
    constraint = locked.content.verified_evidence_constraints[0]
    assert constraint.source_cue_ids == ["cue_0002"]
    assert constraint.source_kind == "visual"
    assert constraint.observation_zh == BASELINES[name]["observation"]
    assert constraint.status == "supported"
    assert constraint.anchored_target_text_zh == BASELINES[name]["anchor"]
    assert locked.content.translatable_source_cue_ids == ["cue_0001", "cue_0002", "cue_0003"]
    projected = creation.project_localization_creation_source_lock(request.source_lock, locked)
    assert projected.input.cues == request.source_lock.input.cues
    assert request.model_dump(mode="json") == original
    rendered = creation.render_verified_evidence_constraint(constraint)
    assert BASELINES[name]["observation"] in rendered
    assert BASELINES[name]["anchor"] in rendered
    assert "不是" in rendered and "旁白" in rendered


def test_local_reassembly_replays_same_paid_candidate(tmp_path, monkeypatch):
    request, research, visual, raw, route = _case(tmp_path, "tutorial")
    calls = []
    def complete(*_args, trace_sink, **_kwargs):
        calls.append(1)
        _trace(trace_sink)
        return copy.deepcopy(raw)
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", complete)
    journal = DevelopmentLlmBatchReplay(root=tmp_path / "journal", project_id="project",
        development_session_id="session", step_id_prefix="evidence", retry_rejected_execution_id="op1")
    first = evidence.adjudicate_localization_document_evidence(request.document_brief, research, visual,
        route=route, batch_journal=journal)
    records = [json.loads(p.read_text())["result"] for p in (tmp_path / "journal").rglob("checkpoint.json")]
    assert records[0]["raw_json_candidate"] == raw
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", lambda *_a, **_k: pytest.fail("replay called provider"))
    second = evidence.adjudicate_localization_document_evidence(request.document_brief, research, visual,
        route=route, batch_journal=journal)
    assert second.model_dump() == first.model_dump()
    assert second.answers[0].source_cue_ids == ["cue_0002"]
    assert len(calls) == 1


def test_legacy_answer_is_not_mutated_or_given_fake_provenance(tmp_path):
    request, _research, _visual, raw, _route = _case(tmp_path, "drama")
    row = raw["answers"][0]
    legacy = {k: v for k, v in row.items() if k != "anchored_image_positions"}
    legacy.update(source_cue_ids=["cue_0002", "cue_0001", "cue_0003"], anchored_source_cue_ids=["cue_0001"])
    answer = evidence.LocalizationDocumentEvidenceAnswer.model_validate(legacy)
    before = answer.model_dump(mode="json")
    assert "observed_source_cue_ids" not in before
    result = evidence.LocalizationDocumentEvidenceAdjudicationResult(
        brief_fingerprint=request.document_brief.result_fingerprint, result_fingerprint="e" * 64,
        answers=[answer], status="passed",
    )
    locked = creation.lock_localization_creation_context(request.model_copy(update={"evidence": result}))
    item = locked.content.verified_evidence_constraints[0]
    assert item.source_cue_ids == ["cue_0002"]
    assert item.source_kind is None and item.observation_zh is None
    assert answer.model_dump(mode="json") == before
    old_constraint = {"question_id": "question_0001", "source_cue_ids": ["cue_0002"],
        "constraint_zh": "旧约束", "anchored_target_text_zh": "旧文字"}
    parsed = creation.LocalizationVerifiedEvidenceConstraint.model_validate(old_constraint)
    assert parsed.model_dump(mode="json") == old_constraint
    assert "来源未分型" in creation.render_verified_evidence_constraint(parsed)


def test_partial_readonly_anchor_is_an_observation_not_new_scope(tmp_path, monkeypatch):
    request, research, visual, raw, route = _case(tmp_path, "drama")
    raw["answers"][0].update(status="uncertain", constraint_zh="")
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", lambda *_a, **_k: raw)
    result = evidence.adjudicate_localization_document_evidence(request.document_brief, research, visual, route=route)
    locked = creation.lock_localization_creation_context(request.model_copy(update={"evidence": result}))
    item = locked.content.verified_evidence_constraints[0]
    assert item.source_cue_ids == ["cue_0002"]
    assert item.status == "uncertain"
    assert item.anchored_target_text_zh == BASELINES["drama"]["anchor"]
    assert locked.content.unresolved_evidence_question_ids == ["question_0001"]
    assert locked.content.translatable_source_cue_ids == ["cue_0001", "cue_0002", "cue_0003"]


def test_core_order_ignores_model_ids_and_frame_order(tmp_path, monkeypatch):
    request, research, visual, raw, route = _case(tmp_path, "tutorial")
    question = request.document_brief.content.evidence_questions[0].model_copy(update={
        "source_cue_ids": ["cue_0003", "cue_0002"],
    })
    brief = request.document_brief.model_copy(update={"content": request.document_brief.content.model_copy(update={
        "evidence_questions": [question],
    })})
    raw["answers"][0].update(source_cue_ids=["cue_9999"], evidence_image_positions=[3, 2, 1])
    monkeypatch.setattr(llm_runtime, "complete_multimodal_json", lambda *_a, **_k: raw)
    answer = evidence.adjudicate_localization_document_evidence(brief, research, visual, route=route).answers[0]
    assert answer.source_cue_ids == ["cue_0002", "cue_0003"]
    assert answer.observed_source_cue_ids == ["cue_0003", "cue_0002", "cue_0001"]


def test_web_observation_without_anchor_is_preserved(tmp_path, monkeypatch):
    request, research, visual, raw, route = _case(tmp_path, "tutorial")
    question = request.document_brief.content.evidence_questions[0].model_copy(update={"kind": "web"})
    brief = request.document_brief.model_copy(update={"content": request.document_brief.content.model_copy(update={
        "evidence_questions": [question],
    })})
    raw["answers"][0].update(evidence_image_positions=[], anchored_image_positions=[], anchored_target_text_zh="",
        conclusion_zh="官方资料确认产品拼写为 Example。", constraint_zh="只确认名称，不推断具体操作。")
    monkeypatch.setattr(llm_runtime, "complete_json", lambda *_a, **_k: raw)
    result = evidence.adjudicate_localization_document_evidence(brief, research,
        visual.model_copy(update={"frames": [], "status": "not_needed"}), route=route)
    item = creation.lock_localization_creation_context(request.model_copy(update={
        "document_brief": brief, "evidence": result,
    })).content.verified_evidence_constraints[0]
    assert result.answers[0].observed_source_cue_ids == []
    assert item.source_kind == "web" and not item.anchored_target_text_zh
    assert "官方资料确认产品拼写为 Example。" in creation.render_verified_evidence_constraint(item)


def test_rendered_evidence_fits_consumer_bound_without_truncating():
    item = creation.LocalizationVerifiedEvidenceConstraint(question_id="question_0001", source_cue_ids=["cue_0001"],
        constraint_zh="限" * 1000, observation_zh="观" * 1000, anchored_target_text_zh="字" * 500,
        source_kind="visual", status="supported")
    rendered = creation.render_verified_evidence_constraint(item)
    assert len(rendered) <= 3000
    assert all(value in rendered for value in (item.constraint_zh, item.observation_zh, item.anchored_target_text_zh))
