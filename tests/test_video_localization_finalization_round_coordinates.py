"""A finalization round owns one coordinate space, including after resume."""

import pytest

from tests.test_video_localization_development_llm_batches import _journal, _request, _trace
from app.domains.video_localization import localization_spoken_script as spoken


def _round_fixture(monkeypatch):
    request = _request(monkeypatch)
    content = spoken.LocalizationSpokenScriptContent(
        title="固定坐标",
        sections=[
            spoken.LocalizationSpokenScriptSection(
                section_id="section_0001", heading="准备", paragraphs=["继续。留在第一章。"]
            ),
            spoken.LocalizationSpokenScriptSection(
                section_id="section_0002", heading="执行", paragraphs=["开始。继续。继续。结束。"]
            ),
        ],
    )
    issues = [
        spoken.LocalizationSpokenScriptReviewIssue(
            issue_id="a",
            sentence_id="section_0001.paragraph_0001.sentence_0001",
            severity="medium",
            kind="meaning",
            excerpt="继续。",
            reason_zh="叙事位置不对。",
            required_change_zh="将该句移到下一章开始之前。",
        ),
        spoken.LocalizationSpokenScriptReviewIssue(
            issue_id="b",
            sentence_id="section_0002.paragraph_0001.sentence_0003",
            severity="medium",
            kind="meaning",
            excerpt="继续。",
            reason_zh="指定位置表达错误。",
            required_change_zh="将指定的第三句改成停下。",
        ),
    ]
    plans = [
        spoken.LocalizationSpokenScriptSectionEdits(
            section_id="section_0001",
            edits=[
                spoken.LocalizationSpokenScriptEditOperation(
                    issue_id="a",
                    replacement="继续。",
                    placement="before",
                    anchor_sentence_id="section_0002.paragraph_0001.sentence_0001",
                    anchor_sentence="开始。",
                ),
            ],
        ),
        spoken.LocalizationSpokenScriptSectionEdits(
            section_id="section_0002",
            edits=[
                spoken.LocalizationSpokenScriptEditOperation(issue_id="b", replacement="停下。"),
            ],
        ),
    ]
    script = spoken._script_with_content(request.script, content)
    structure = request.document_brief.content.structure
    brief = request.document_brief.model_copy(
        update={
            "content": request.document_brief.content.model_copy(
                update={
                    "structure": [structure[0], structure[0].model_copy(update={"section_id": "section_0002"})],
                }
            )
        }
    )
    request = request.model_copy(
        update={
            "script": script,
            "document_brief": brief,
            "fidelity_review": request.fidelity_review.model_copy(
                update={
                    "script_fingerprint": script.result_fingerprint,
                    "issues": issues,
                    "llm_calls": request.script.llm_calls,
                }
            ),
            "naturalness_review": request.naturalness_review.model_copy(
                update={
                    "script_fingerprint": script.result_fingerprint,
                    "issues": [],
                    "status": "passed",
                    "impression": "original_chinese_transcript",
                    "scores": {"naturalness": 4.5},
                    "llm_calls": request.script.llm_calls,
                }
            ),
        }
    )
    return request, plans, {"section_0001": [issues[0]], "section_0002": [issues[1]]}


def test_cross_section_plan_join_uses_frozen_ids_even_when_neighbor_text_repeats(monkeypatch):
    request, plans, issues = _round_fixture(monkeypatch)
    result, revised = spoken._apply_round_content_edits(request.script.content, plans, issues_by_section=issues)
    assert result.sections[0].paragraphs == ["留在第一章。"]
    assert result.sections[1].paragraphs == ["继续。开始。继续。停下。结束。"]
    assert revised == ["section_0001", "section_0002"]
    assert request.script.content.sections[1].paragraphs == ["开始。继续。继续。结束。"]


@pytest.mark.parametrize("restart", [False, True, "journal"])
def test_real_finalization_loop_and_checkpoint_resume_keep_the_round_coordinate_space(monkeypatch, tmp_path, restart):
    request, plans, _ = _round_fixture(monkeypatch)
    calls = []
    checkpoints = []
    if restart == "journal":
        monkeypatch.setattr(
            spoken.llm_runtime,
            "resolve_profile",
            lambda *a, **k: spoken.llm_runtime.ResolvedProfile(
                profile_id="profile",
                protocol="openai_compatible",
                base_url="http://localhost:9000/v1",
                model_id="model",
                reasoning_effort="low",
            ),
        )
    journal = _journal(tmp_path) if restart == "journal" else None

    def complete(prompt, payload, *, trace_sink, **kwargs):
        _trace(trace_sink)
        if "current_section" in payload:
            section_id = payload["current_section"]["section_id"]
            calls.append(section_id)
            if section_id == "section_0002":
                assert payload["current_section"]["paragraphs"] == ["开始。继续。继续。结束。"]
                assert payload["issues"][0]["editable_sentence_id"] == "section_0002.paragraph_0001.sentence_0003"
            return next(plan for plan in plans if plan.section_id == section_id).model_dump(mode="json")
        if prompt == spoken.FINALIZATION_FIDELITY_REGRESSION_PROMPT:
            return {"status": "passed", "issues": [], "summary_zh": "固定原意复核通过。"}
        if prompt == spoken.FINALIZATION_NATURALNESS_REGRESSION_PROMPT:
            return {
                "impression": "original_chinese_transcript",
                "naturalness": 4.5,
                "persona": 4.5,
                "emotion": 4.5,
                "flow": 4.5,
                "issues": [],
                "summary_zh": "固定自然度复核通过。",
            }
        return {"status": "passed", "remaining_issue_ids": [], "summary_zh": "既定问题已解决。"}

    def save_then_interrupt(checkpoint):
        checkpoints.append(checkpoint)
        if restart and len(checkpoints) == 1:
            raise RuntimeError("本地中断：第一章已持久保存，第二章尚未调用")

    monkeypatch.setattr(spoken.llm_runtime, "complete_json", complete)
    if restart:
        with pytest.raises(RuntimeError, match="本地中断"):
            spoken.finalize_localization_spoken_script(
                request, on_section_checkpoint=save_then_interrupt, batch_journal=journal
            )
        assert calls == ["section_0001"]
        checkpoint = spoken.LocalizationSpokenScriptFinalizationCheckpoint.model_validate(
            checkpoints[-1].model_dump(mode="json")
        )
        assert checkpoint.contract_version == "localization-spoken-script-final-checkpoint-v4"
        legacy = checkpoint.model_dump(mode="json")
        legacy["contract_version"] = "localization-spoken-script-final-checkpoint-v3"
        legacy.pop("round_edit_plans")
        with pytest.raises(ValueError):
            spoken.LocalizationSpokenScriptFinalizationCheckpoint.model_validate(legacy)
        assert checkpoint.round_edit_plans == [plans[0]]
        assert checkpoint.working_content.sections[1].paragraphs == ["继续。开始。继续。继续。结束。"]
        # Partial results without their original edit provenance must not be resumed.
        with pytest.raises(ValueError, match="冻结坐标"):
            spoken.finalize_localization_spoken_script(
                request, resume_checkpoint=checkpoint.model_copy(update={"round_edit_plans": []})
            )
        result = spoken.finalize_localization_spoken_script(
            request,
            resume_checkpoint=None if restart == "journal" else checkpoint,
            on_section_checkpoint=checkpoints.append,
            batch_journal=_journal(tmp_path) if restart == "journal" else None,
        )
    else:
        result = spoken.finalize_localization_spoken_script(request, on_section_checkpoint=checkpoints.append)
    assert calls == ["section_0001", "section_0002"]
    assert result.content.sections[1].paragraphs == ["继续。开始。继续。停下。结束。"]
    assert result.quality_summary.status == "revised"
    assert checkpoints[-1].round_edit_plans == plans
    wrong_review = checkpoints[-1].post_fidelity_review.model_copy(update={"script_fingerprint": "0" * 64})
    with pytest.raises(ValueError, match="后置质检编号"):
        spoken.finalize_localization_spoken_script(
            request, resume_checkpoint=checkpoints[-1].model_copy(update={"post_fidelity_review": wrong_review}),
        )


def test_section_end_resume_rebuilds_review_excerpts_and_identical_post_review_payloads(monkeypatch):
    request, plans, issues = _round_fixture(monkeypatch)
    request = request.model_copy(
        update={
            "fidelity_review": request.fidelity_review.model_copy(update={"issues": issues["section_0001"]}),
            "naturalness_review": request.naturalness_review.model_copy(
                update={
                    "issues": issues["section_0002"],
                    "status": "needs_revision",
                }
            ),
        }
    )
    edit_calls = []
    review_payloads = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        _trace(trace_sink)
        if "current_section" in payload:
            section_id = payload["current_section"]["section_id"]
            edit_calls.append(section_id)
            return next(plan for plan in plans if plan.section_id == section_id).model_dump(mode="json")
        review_payloads.append(payload)
        # The fixed post-check leaves b unresolved so the finalizer must rebase
        # its excerpt onto b's already-revised text, also after section resume.
        return {"status": "needs_revision", "remaining_issue_ids": ["b"], "summary_zh": "保留一项待确认。"}

    monkeypatch.setattr(spoken.llm_runtime, "complete_json", complete)
    uninterrupted = spoken.finalize_localization_spoken_script(request, max_revision_rounds=1)
    baseline_payloads = list(review_payloads)
    assert uninterrupted.post_naturalness_review.issues[0].excerpt == "停下。"
    edit_calls.clear()
    review_payloads.clear()
    checkpoints = []

    def stop_at_section_end(checkpoint):
        checkpoints.append(checkpoint)
        if checkpoint.next_section_index == 2 and checkpoint.review_stage == "sections":
            raise RuntimeError("章节结束、后置复核尚未开始")

    with pytest.raises(RuntimeError, match="章节结束"):
        spoken.finalize_localization_spoken_script(
            request, max_revision_rounds=1, on_section_checkpoint=stop_at_section_end
        )
    restored = spoken.LocalizationSpokenScriptFinalizationCheckpoint.model_validate(
        checkpoints[-1].model_dump(mode="json")
    )
    resumed = spoken.finalize_localization_spoken_script(request, max_revision_rounds=1, resume_checkpoint=restored)
    assert edit_calls == ["section_0001", "section_0002"]
    assert review_payloads == baseline_payloads
    assert resumed.content == uninterrupted.content
    assert resumed.post_naturalness_review.issues[0].excerpt == "停下。"
    assert resumed.post_naturalness_review.issues[0].sentence_id == "section_0002.paragraph_0001.sentence_0004"
    assert resumed.post_naturalness_review.script_fingerprint == spoken._script_with_content(
        request.script, resumed.content,
    ).result_fingerprint


@pytest.mark.parametrize("remaining", ["a", "b"])
def test_cross_round_closure_never_reuses_old_ids_for_repeated_text(monkeypatch, remaining):
    request, plans, _ = _round_fixture(monkeypatch)
    edit_calls = []
    closure_calls = []

    def complete(prompt, payload, *, trace_sink, **kwargs):
        _trace(trace_sink)
        if "current_section" in payload:
            section_id = payload["current_section"]["section_id"]
            edit_calls.append(section_id)
            if len(edit_calls) <= 2:
                return next(plan for plan in plans if plan.section_id == section_id).model_dump(mode="json")
            assert remaining == "b"
            assert payload["issues"][0]["editable_sentence_id"] == "section_0002.paragraph_0001.sentence_0004"
            return {"section_id": section_id, "edits": [{"issue_id": "b", "replacement": "暂停。"}]}
        if prompt in {spoken.FINALIZATION_CLOSURE_PROMPT, spoken.FINALIZATION_NATURALNESS_CLOSURE_PROMPT}:
            closure_calls.append(payload)
            if len(closure_calls) == 1:
                return {"status": "needs_revision", "remaining_issue_ids": [remaining], "summary_zh": "指定问题尚未关闭。"}
            return {"status": "passed", "remaining_issue_ids": [], "summary_zh": "已关闭。"}
        if prompt == spoken.FINALIZATION_FIDELITY_REGRESSION_PROMPT:
            return {"status": "passed", "issues": [], "summary_zh": "原意通过。"}
        return {
            "impression": "original_chinese_transcript", "naturalness": 4.5,
            "persona": 4.5, "emotion": 4.5, "flow": 4.5,
            "issues": [], "summary_zh": "自然度通过。",
        }

    monkeypatch.setattr(spoken.llm_runtime, "complete_json", complete)
    result = spoken.finalize_localization_spoken_script(request)
    if remaining == "a":
        assert edit_calls == ["section_0001", "section_0002"]
        assert len(closure_calls) == 1
        assert result.quality_summary.status == "warning"
        assert result.content.sections[1].paragraphs == ["继续。开始。继续。停下。结束。"]
        assert result.post_fidelity_review.issues[0].sentence_id is None
        assert "无法唯一绑定" in result.post_fidelity_review.summary_zh
    else:
        assert edit_calls == ["section_0001", "section_0002", "section_0002"]
        assert result.quality_summary.status == "revised"
        assert result.content.sections[1].paragraphs == ["继续。开始。继续。暂停。结束。"]


@pytest.mark.parametrize("excerpt", ["继续。", "继续呀。", ""])
def test_closure_binding_rejects_ambiguous_missing_or_deleted_targets(monkeypatch, excerpt):
    request, plans, issues = _round_fixture(monkeypatch)
    content, _ = spoken._apply_round_content_edits(request.script.content, plans, issues_by_section=issues)
    script = spoken._script_with_content(request.script, content)
    review = request.fidelity_review.model_copy(update={
        "script_fingerprint": script.result_fingerprint,
        "issues": [issues["section_0002"][0]],
    })
    rebound, bound = spoken._bind_closure_review_to_script(review, script, {"b": excerpt})
    assert not bound
    assert rebound.issues[0].sentence_id is None
    assert content.sections[1].paragraphs == ["继续。开始。继续。停下。结束。"]


def test_round_rejects_review_ids_from_another_script_projection(monkeypatch):
    request, plans, issues = _round_fixture(monkeypatch)
    content, _ = spoken._apply_round_content_edits(request.script.content, plans, issues_by_section=issues)
    assert spoken._round_review_coordinates_match(
        request.script, request.script.content, request.fidelity_review, request.naturalness_review,
    )
    assert not spoken._round_review_coordinates_match(
        request.script, content, request.fidelity_review, request.naturalness_review,
    )
    rebound, bound = spoken._bind_closure_review_to_script(
        request.fidelity_review, spoken._script_with_content(request.script, content), {"b": "停下。"},
    )
    assert not bound
    assert all(issue.sentence_id is None for issue in rebound.issues)


def test_deleted_target_never_rebinds_to_the_only_surviving_duplicate(monkeypatch):
    request, _, _ = _round_fixture(monkeypatch)
    issue = request.fidelity_review.issues[1].model_copy(update={
        "required_change_zh": "删除整句，属于无依据新增内容。",
        "kind": "addition",
    })
    plan = spoken.LocalizationSpokenScriptSectionEdits(
        section_id="section_0002",
        edits=[spoken.LocalizationSpokenScriptEditOperation(issue_id="b", operation="delete", replacement="")],
    )
    issues = {"section_0002": [issue]}
    # Isolate the two same-text candidates to demonstrate that deletion leaves
    # exactly one occurrence, which still is not the deleted target's identity.
    content = request.script.content.model_copy(update={"sections": [request.script.content.sections[1]]})
    changed, _ = spoken._apply_round_content_edits(content, [plan], issues_by_section=issues)
    assert changed.sections[0].paragraphs == ["开始。继续。结束。"]
    excerpts = spoken._revised_excerpts_from_plans(content, [plan], issues_by_section=issues)
    assert excerpts == {"b": ""}
    script = spoken._script_with_content(request.script, changed)
    review = request.fidelity_review.model_copy(update={
        "script_fingerprint": script.result_fingerprint, "issues": [issue],
    })
    rebound, bound = spoken._bind_closure_review_to_script(review, script, excerpts)
    assert not bound
    assert rebound.issues[0].sentence_id is None
