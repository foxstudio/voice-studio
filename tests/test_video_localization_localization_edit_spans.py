"""Cross-content fixed baselines for structural editing, with no model calls."""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.localization_edit_spans import (
    TextEdit,
    apply_text_edits,
    editable_spans,
    validate_quote_structure,
)
from app.domains.video_localization import localization_spoken_script as script


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("先连接设备；再检查指示灯。完成了吗？", ["先连接设备；", "再检查指示灯。", "完成了吗？"]),
        ("他说：“打开面板。然后调整参数。”接着演示。", ["他说：“打开面板。然后调整参数。”", "接着演示。"]),
        ("她喊：“别走！我还没说完。”门关上了。", ["她喊：“别走！我还没说完。”", "门关上了。"]),
        (
            "她说：“牌子写着‘请慢行！注意路面。’记住了吗？”他点头。",
            ["她说：“牌子写着‘请慢行！注意路面。’记住了吗？”", "他点头。"],
        ),
        ("“等等！”“我在这里。别担心。”", ["“等等！”", "“我在这里。别担心。”"]),
        ('他说："准备好了？开始！"继续。', ['他说："准备好了？开始！"', "继续。"]),
        ("查看「设置。高级选项。」然后继续。完成。", ["查看「设置。高级选项。」", "然后继续。", "完成。"]),
        ("  第一句。\n 第二句！？  ", ["第一句。", "第二句！？"]),
        ("don’t stop。继续。", ["don’t stop。", "继续。"]),
        ('这是一块 27" 显示器。颜色需要调整。', ['这是一块 27" 显示器。', "颜色需要调整。"]),
        ("保留 James’ 的标志。颜色需要调整。", ["保留 James’ 的标志。", "颜色需要调整。"]),
        ('他说：“选择 27\" 显示器。然后调色。”', ['他说：“选择 27\" 显示器。然后调色。”']),
        ('编号是 "27"。继续。', ['编号是 "27"。', "继续。"]),
        ("保留‘James’这个名字。继续。", ["保留‘James’这个名字。", "继续。"]),
    ],
)
def test_editable_spans_keep_quote_closed_cross_content_baselines(text, expected):
    spans = editable_spans(text)
    assert [part for _, _, part in spans] == expected
    assert all(text[start:end] == part for start, end, part in spans)
    assert "".join(text.split()) == "".join("".join(expected).split())
    for _, _, part in spans:
        validate_quote_structure(part)


@pytest.mark.parametrize("text", ["先检查。“打开面板。稍等。", "这句有错。”下一句。", "“外层‘内层。”’"])
def test_malformed_quotes_remain_visible_but_are_not_editable(text):
    assert editable_spans(text) == [(0, len(text), text)]
    with pytest.raises(ValueError, match="不能独立编辑"):
        validate_quote_structure(text)


@pytest.mark.parametrize("text", ['27" 显示器。颜色偏蓝。', "James’ 的标志。颜色偏蓝。"])
def test_non_quote_symbols_do_not_prevent_unrelated_issue_edit(text):
    section = _section(text)
    issue = _issue("颜色偏蓝。")
    content = script.LocalizationSpokenScriptContent(title="符号语境", sections=[section])
    assert script._locate_review_issues(content, [issue]) == {section.section_id: [issue]}
    assert _apply(section, issue, "颜色偏红。").paragraphs == [text.replace("颜色偏蓝。", "颜色偏红。")]


def test_immutable_offset_edits_preserve_repeated_text_and_order():
    text = "继续。继续。结束。"
    assert apply_text_edits(text, [TextEdit(3, 6, "停下。"), TextEdit(0, 3, "好。")]) == "好。停下。结束。"
    with pytest.raises(ValueError, match="重叠"):
        apply_text_edits(text, [TextEdit(0, 6, ""), TextEdit(3, 6, "")])


def _section(text, section_id="section_0001"):
    return script.LocalizationSpokenScriptSection(section_id=section_id, heading="正文", paragraphs=[text])


def _issue(excerpt, sentence_id=None, **updates):
    return script.LocalizationSpokenScriptReviewIssue(
        **dict(
            issue_id="f1",
            severity="medium",
            kind="meaning",
            excerpt=excerpt,
            sentence_id=sentence_id,
            reason_zh="需修正含义。",
            required_change_zh="只修正该处表达。",
        )
        | updates
    )


def _apply(section, issue, replacement, **edit_fields):
    return script._apply_section_edits(
        section,
        script.LocalizationSpokenScriptSectionEdits(
            section_id=section.section_id,
            edits=[
                script.LocalizationSpokenScriptEditOperation(
                    issue_id=issue.issue_id, replacement=replacement, **edit_fields
                )
            ],
        ),
        issues=[issue],
    )


@pytest.mark.parametrize(
    ("text", "excerpt", "replacement", "readonly"),
    [
        (
            "讲解员说：“打开菜单。然后选择参数。”演示结束。",
            "打开菜单。",
            "讲解员说：“先打开菜单。然后选择参数。”",
            "然后选择参数。”",
        ),
        ("她喊：“不要走！我还在这里。”门关上了。", "不要走！", "她喊：“先别走！我还在这里。”", "我还在这里。”"),
    ],
)
def test_review_edit_anchor_projection_and_readonly_scope_are_consistent(text, excerpt, replacement, readonly):
    section = _section(text)
    content = script.LocalizationSpokenScriptContent(title="基准", sections=[section])
    issue = _issue(excerpt)
    reference, _, editable = script._locate_issue_sentence_reference(section, issue)
    anchors = script._placement_anchor_references(content)
    assert anchors[0]["anchor_sentence_id"] == reference
    assert anchors[0]["anchor_sentence"] == editable
    assert f"[{reference}] {editable}" in script.script_review_text(content)
    scoped = script._merge_review_issues_by_target_sentence(section, [issue])[0]
    assert scoped.model_dump()["source_excerpts"] == [excerpt]
    assert scoped.model_dump()["readonly_fragments"] == [readonly]
    assert script._allowed_edit_operations(scoped) == ["replace"]
    assert _apply(section, scoped, replacement).paragraphs == [text.replace(editable, replacement)]
    with pytest.raises(ValueError, match="只读相邻内容"):
        _apply(section, scoped, replacement.replace(readonly, "”"))


def test_quote_partial_delete_requires_preserving_unaffected_sentence():
    section = _section("“文件已经核对。价值判断没有依据。”下一步。")
    issue = _issue("价值判断没有依据。", kind="addition", required_change_zh="删除这句无依据的判断。")
    with pytest.raises(ValueError, match="只读相邻内容"):
        _apply(section, issue, "", operation="delete")
    assert _apply(section, issue, "“文件已经核对。”").paragraphs == ["“文件已经核对。”下一步。"]


def test_insert_inside_quoted_unit_keeps_both_existing_sentences_in_order():
    section = _section("他说：“已经登记。现在出发。”")
    issue = _issue("现在出发。", kind="omission", required_change_zh="在本句之前补回确认身份。")
    scoped = script._merge_review_issues_by_target_sentence(section, [issue])[0]
    assert script._allowed_edit_operations(scoped) == ["replace"]
    assert _apply(section, scoped, "他说：“已经登记。身份确认了。现在出发。”").paragraphs == [
        "他说：“已经登记。身份确认了。现在出发。”"
    ]
    with pytest.raises(ValueError, match="只读相邻内容"):
        _apply(section, scoped, "他说：“身份确认了。现在出发。”")


def test_repeated_text_is_edited_by_original_id_after_an_earlier_multi_sentence_edit():
    section = _section("开始。继续。继续。结束。")
    issues = [
        _issue("开始。", "section_0001.paragraph_0001.sentence_0001"),
        _issue("继续。", "section_0001.paragraph_0001.sentence_0003", issue_id="f2"),
    ]
    result = script._apply_section_edits(
        section,
        script.LocalizationSpokenScriptSectionEdits(
            section_id=section.section_id,
            edits=[
                script.LocalizationSpokenScriptEditOperation(issue_id="f1", replacement="准备。现在开始。"),
                script.LocalizationSpokenScriptEditOperation(issue_id="f2", replacement="停下。"),
            ],
        ),
        issues=issues,
    )
    assert result.paragraphs == ["准备。现在开始。继续。停下。结束。"]


@pytest.mark.parametrize("destination_id", ["section_0001", "section_0002"])
def test_repeated_same_paragraph_anchor_uses_selected_id_for_local_and_cross_moves(destination_id):
    source = _section("真的？其他内容。" if destination_id != "section_0001" else "出发。真的？出发。其他内容。")
    issue = _issue("真的？", required_change_zh="将该句移到后一次出发之后。")
    sections = [source] if destination_id == "section_0001" else [source, _section("出发。出发。", destination_id)]
    sentence_number = 3 if destination_id == "section_0001" else 2
    content = script.LocalizationSpokenScriptContent(title="反应落位", sections=sections)
    revised, _ = script._apply_content_edits(
        content,
        script.LocalizationSpokenScriptSectionEdits(
            section_id=source.section_id,
            edits=[
                script.LocalizationSpokenScriptEditOperation(
                    issue_id=issue.issue_id,
                    replacement="真的？",
                    placement="after",
                    anchor_sentence="出发。",
                    anchor_sentence_id=f"{destination_id}.paragraph_0001.sentence_{sentence_number:04d}",
                )
            ],
        ),
        issues=[issue],
    )
    if destination_id == "section_0001":
        assert revised.sections[0].paragraphs == ["出发。出发。真的？其他内容。"]
    else:
        assert revised.sections[0].paragraphs == ["其他内容。"]
        assert revised.sections[1].paragraphs == ["出发。出发。真的？"]


def test_nested_quotes_and_dropped_closer_are_not_repaired_by_replacement_guard():
    source = "他说：“请看‘注意事项’。按顺序操作。”"
    for replacement in ["他说：“请看‘注意事项”。按顺序操作。’", "他说：“请看注意事项。"]:
        with pytest.raises(ValueError, match="引号边界"):
            script._prepare_replacement_sentence(source, replacement, issue_id="f1")


def test_old_sentence_id_cannot_silently_address_the_next_quote_closed_unit():
    section = _section("“先打开。再设置。”最后关闭。")
    issue = _issue("再设置。", "section_0001.paragraph_0001.sentence_0002")
    with pytest.raises(ValueError, match="不能复用旧编号"):
        script._locate_issue_sentence_reference(section, issue)


def test_unclosed_quote_is_visible_but_rejected_at_existing_ambiguity_boundary():
    section = _section("说明：“打开面板。再调参数。")
    content = script.LocalizationSpokenScriptContent(title="异常输入", sections=[section])
    assert "说明：“打开面板。再调参数。" in script.script_review_text(content)
    with pytest.raises(ValueError, match="不能独立编辑"):
        script._locate_review_issues(content, [_issue("再调参数。")])


def test_scoped_merged_issue_keeps_original_targets_in_serializable_contract():
    section = _section("“第一句。第二句。第三句。”")
    issues = [_issue("第一句。"), _issue("第三句。", issue_id="f2")]
    merged = script._merge_review_issues_by_target_sentence(section, issues)[0]
    restored = type(merged).model_validate(merged.model_dump(mode="json"))
    assert restored.source_excerpts == ["第一句。", "第三句。"]
    assert restored.readonly_fragments == ["第二句。"]
    assert _apply(section, restored, "“修改第一句。第二句。修改第三句。”").paragraphs == [
        "“修改第一句。第二句。修改第三句。”"
    ]


def test_projection_changes_invalidate_both_reviews_but_not_generation(monkeypatch):
    from app.domains.video_localization import localization_workflow_nodes as nodes
    from app.domains.video_localization import workflow_behavior

    step_ids = [
        "review_localization_fidelity",
        "review_localization_naturalness",
        "generate_localization_spoken_script",
    ]
    before = {step: nodes.node_behavior_fingerprint(step) for step in step_ids}
    original = workflow_behavior.inspect.getsource

    def changed_projection(value):
        source = original(value)
        return source + "\n# changed projection" if value is script.script_review_text else source

    monkeypatch.setattr(workflow_behavior.inspect, "getsource", changed_projection)
    assert nodes.node_behavior_fingerprint(step_ids[0]) != before[step_ids[0]]
    assert nodes.node_behavior_fingerprint(step_ids[1]) != before[step_ids[1]]
    assert nodes.node_behavior_fingerprint(step_ids[2]) == before[step_ids[2]]


def test_unclosed_quote_returns_finalization_warning_without_calling_model(monkeypatch):
    from app.services.localization_ai_policy import LocalizationAiPhaseRoute
    from tests.test_video_localization_localization_spoken_script import _source_and_brief

    source, brief = _source_and_brief()
    route = LocalizationAiPhaseRoute(
        phase="spoken_script_finalization",
        profile_id="fixed",
        model_id="fixed",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    content = script.LocalizationSpokenScriptContent(title="异常输入", sections=[_section("说明：“打开。再设置。")])
    draft = script.LocalizationSpokenScriptResult.model_construct(
        source_fingerprint=source.source_fingerprint,
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="b" * 64,
        content=content,
        dynamic_rule_ids=[],
        route=route,
        llm_calls=[],
        quality_summary=None,
    )
    fidelity = script.LocalizationSpokenScriptReviewResult.model_construct(
        review_kind="fidelity",
        source_fingerprint=source.source_fingerprint,
        script_fingerprint=draft.result_fingerprint,
        result_fingerprint="c" * 64,
        status="needs_revision",
        impression="not_applicable",
        scores={},
        issues=[_issue("再设置。")],
        summary_zh="存在局部问题。",
        route=route.model_copy(update={"phase": "fidelity_review"}),
        llm_calls=[],
    )
    naturalness = fidelity.model_copy(
        update={
            "review_kind": "naturalness",
            "status": "passed",
            "result_fingerprint": "d" * 64,
            "impression": "original_chinese_transcript",
            "issues": [],
            "scores": {"naturalness": 4.5},
            "route": route.model_copy(update={"phase": "naturalness_review"}),
        }
    )

    def reject_call(*args, **kwargs):
        pytest.fail("Malformed structural input must not call any model")

    monkeypatch.setattr(script.llm_runtime, "complete_json", reject_call)
    result = script.finalize_localization_spoken_script(
        script.LocalizationSpokenScriptFinalizationInput(
            script_operation_id="script",
            fidelity_review_operation_id="fidelity",
            naturalness_review_operation_id="naturalness",
            source_lock=source,
            document_brief=brief,
            script=draft,
            fidelity_review=fidelity,
            naturalness_review=naturalness,
            route=route,
            post_fidelity_route=fidelity.route,
            post_naturalness_route=naturalness.route,
        )
    )
    assert result.content == content
    assert result.quality_summary.status == "warning"
    assert result.quality_summary.model_call_count == 0
