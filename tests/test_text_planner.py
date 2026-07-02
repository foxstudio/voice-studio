from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import text_planner  # noqa: E402


def test_short_text_plan_allows_direct_generate():
    plan = text_planner.plan_text(
        text="大家好，欢迎来到本期内容。",
        engine_id="indextts-v2",
    )

    assert plan.planner == "rules"
    assert plan.llm_available is False
    assert plan.mode == "direct"
    assert plan.recommended_action == "direct_generate_with_verification"
    assert plan.requires_user_confirmation is False
    assert len(plan.segments) == 1
    assert plan.segments[0].text == "大家好，欢迎来到本期内容。"


def test_long_omnivoice_text_recommends_split_with_confirmation():
    text = (
        "这是第一句话，用来介绍今天的主题。"
        "接下来我们会继续展开背景，说明为什么这个问题值得关注。"
        "然后我们会进入案例部分，观察不同声音模型在长文本中的稳定性。"
        "最后我们会给出一个更适合本地生成的分段方案。"
        "如果单次生成承载太多内容，后续校对和重试都会变得更困难。"
        "因此系统需要先给出可解释的分段计划，再进入生成流程。"
        "补充一段文字，让测试样本明确超过当前 OmniVoice 的提示阈值。"
    )

    plan = text_planner.plan_text(text=text, engine_id="omnivoice")

    assert plan.mode == "longform_recommended"
    assert plan.recommended_action == "split_verify_merge"
    assert plan.requires_user_confirmation is True
    assert plan.threshold == 150
    assert len(plan.segments) > 1
    assert all(segment.char_count <= 90 for segment in plan.segments)
    assert "OmniVoice" in " ".join(plan.warnings)


def test_index_tts_hard_threshold_recommends_split_verify_merge():
    sentence = "长文本生成需要尽量按照自然句切分，避免模型在单次生成里漏掉内容。"
    text = sentence * 22

    plan = text_planner.plan_text(text=text, engine_id="indextts-v2")

    assert plan.mode == "longform_strongly_recommended"
    assert plan.recommended_action == "split_verify_merge"
    assert plan.requires_user_confirmation is True
    assert plan.hard_threshold == 600
    assert len(plan.segments) > 3
    assert plan.privacy_notice


def test_confucius4_long_text_prefers_split_verify_merge():
    sentence = "跨语种情绪迁移需要分段检查音色、语气和发音是否稳定。"
    text = sentence * 20

    plan = text_planner.plan_text(text=text, engine_id="confucius4-mlx-int8")

    assert plan.mode == "longform_strongly_recommended"
    assert plan.recommended_action == "split_verify_merge"
    assert plan.threshold == 24
    assert plan.hard_threshold == 48
    assert len(plan.segments) > 1
    assert all(segment.char_count <= 24 for segment in plan.segments)


def test_cosyvoice_long_text_prefers_verified_split_merge():
    text = (
        "更大，等于更聪明吗？黑板上先放两张卡。左边，尼安德特人。"
        "脑容量大约一千二百到一千七百毫升，平均不小。右边，GPT-4.5。"
        "大，贵，表达能力很强。可这两张卡，都没站到下一阶段的正中间。"
        "先别急着下结论。这不是说大没用。第一关就叫：装得下。"
    )

    plan = text_planner.plan_text(text=text, engine_id="cosyvoice-sft")

    assert plan.mode == "longform_recommended"
    assert plan.recommended_action == "split_verify_merge"
    assert plan.threshold == 80
    assert plan.hard_threshold == 320


def test_qwen3_tts_long_text_prefers_verified_split_merge():
    sentence = "千问三语音实验引擎适合先用短句确认音色、节奏和内容完整性。"
    text = sentence * 16

    plan = text_planner.plan_text(text=text, engine_id="qwen3-tts-mlx-0.6b")

    assert plan.mode == "longform_strongly_recommended"
    assert plan.recommended_action == "split_verify_merge"
    assert plan.threshold == 120
    assert plan.hard_threshold == 360
    assert len(plan.segments) > 1
    assert all(segment.char_count <= 120 for segment in plan.segments)


def test_generate_plan_endpoint_returns_contract_shape():
    client = TestClient(app)
    response = client.post(
        "/api/generate/plan",
        json={
            "text": "这是第一句话。这里是第二句话。这里是第三句话。",
            "engine_id": "omnivoice",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["planner"] == "rules"
    assert data["llm_available"] is False
    assert "segments" in data
    assert data["segments"][0]["index"] == 1
    assert data["segments"][0]["segment_reason"]


def test_generate_endpoint_allows_cosyvoice_official_chunk_window(monkeypatch):
    async def fake_submit(req, project_id=None, segment_id=None):
        assert req.engine_id == "cosyvoice-sft"
        assert project_id is None
        assert segment_id is None
        return "cosy-merged-task"

    monkeypatch.setattr("app.api.generate.task_queue.submit", fake_submit)
    client = TestClient(app)
    response = client.post(
        "/api/generate",
        json={
            "text": (
                "更大，等于更聪明吗？黑板上先放两张卡。左边，尼安德特人。"
                "脑容量大约一千二百到一千七百毫升，平均不小。右边，GPT-4.5。"
                "大，贵，表达能力很强。可这两张卡，都没站到下一阶段的正中间。"
                "先别急着下结论。这不是说大没用。第一关就叫：装得下。"
            ),
            "engine_id": "cosyvoice-sft",
            "speaker_id": "中文男",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"task_id": "cosy-merged-task", "status": "queued"}
