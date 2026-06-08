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
    )

    plan = text_planner.plan_text(text=text, engine_id="omnivoice")

    assert plan.mode == "longform_recommended"
    assert plan.recommended_action == "split_generate"
    assert plan.requires_user_confirmation is True
    assert plan.threshold == 120
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
