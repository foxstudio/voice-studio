from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import llm_observability  # noqa: E402


def _call(**patch):
    values = {
        "purpose": "document_understanding",
        "round_index": 1,
        "duration_ms": 1500,
        "finish_reason": "stop",
        "error_code": None,
        "request_chars": 1200,
        "prompt_tokens": 900,
        "completion_tokens": 120,
        "reasoning_tokens": 40,
        "cost_usd": 0.0045,
        "reasoning_effort_requested": "low",
        "profile_id": "chatgpt-subscription",
        "model_id": "gpt-5.4-mini",
        "provider_host": "local-codex-cli",
    }
    values.update(patch)
    return SimpleNamespace(**values)


def test_shared_llm_projection_aggregates_usage_and_localizes_purpose():
    items, metrics = llm_observability.project_llm_calls(
        [
            _call(),
            _call(
                purpose="whole_recheck",
                round_index=2,
                prompt_tokens=1000,
                completion_tokens=200,
                reasoning_tokens=60,
                cost_usd=0.0055,
            ),
        ],
        calls_complete=True,
    )

    assert [item["title"] for item in items] == [
        "理解全文",
        "重新通读全文",
    ]
    assert {item["label"]: item["value"] for item in metrics} == {
        "调用模型": "gpt-5.4-mini",
        "调用方式": "本地 Codex CLI（local-codex-cli）",
        "模型配置": "chatgpt-subscription",
        "模型调用": "2",
        "输入 Token": "1,900",
        "输出 Token": "320",
        "其中思考": "100",
        "模型费用": "$0.010000",
    }


def test_shared_llm_projection_handles_unknown_purpose_and_missing_usage():
    items, metrics = llm_observability.project_llm_calls(
        [
            _call(
                purpose="future_purpose",
                finish_reason=None,
                prompt_tokens=None,
                completion_tokens=None,
                reasoning_tokens=None,
                cost_usd=None,
            )
        ]
    )

    assert items[0]["title"] == "模型调用"
    assert items[0]["text"] == "停止原因：服务未返回"
    assert {
        item["label"]: item["value"]
        for item in items[0]["facts"]
    } == {
        "输入规模": "1,200 字符",
        "推理强度": "low",
    }
    assert metrics[-1] == {
        "label": "模型费用",
        "value": "未知",
    }


def test_shared_llm_projection_distinguishes_verified_zero_calls():
    items, metrics = llm_observability.project_llm_calls(
        [],
        calls_complete=True,
    )

    assert items == []
    assert metrics[-1] == {
        "label": "模型费用",
        "value": "$0.000000",
    }
    assert not any(item["label"] == "调用模型" for item in metrics)


def test_shared_llm_projection_unknown_history_is_not_zero():
    _, metrics = llm_observability.project_llm_calls([], calls_complete=False)
    assert {item["value"] for item in metrics} == {"未知"}


def test_shared_llm_projection_partial_history_labels_only_recorded_usage():
    _, metrics = llm_observability.project_llm_calls([_call()], calls_complete=False)
    by_label = {item["label"]: item["value"] for item in metrics}
    assert by_label["模型调用"] == "未知（已记录 1）"
    assert by_label["输入 Token"] == "未知（已记录 900）"
    assert by_label["输出 Token"] == "未知（已记录 120）"
    assert by_label["其中思考"] == "未知（已记录 40）"
    assert by_label["模型费用"] == "未知（已记录 $0.004500）"


def test_complete_call_list_does_not_make_missing_usage_known():
    _, metrics = llm_observability.project_llm_calls(
        [_call(), _call(prompt_tokens=None, completion_tokens=None,
                        reasoning_tokens=None, cost_usd=None)],
        calls_complete=True,
    )
    by_label = {item["label"]: item["value"] for item in metrics}
    assert by_label["模型调用"] == "2"
    assert by_label["输入 Token"] == "未知（已记录 900）"
    assert by_label["输出 Token"] == "未知（已记录 120）"
    assert by_label["其中思考"] == "未知（已记录 40）"
    assert by_label["模型费用"] == "未知（已记录 $0.004500）"


def test_shared_llm_projection_known_zero_usage_stays_zero():
    _, metrics = llm_observability.project_llm_calls(
        [_call(prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, cost_usd=0)],
        calls_complete=True,
    )
    by_label = {item["label"]: item["value"] for item in metrics}
    assert by_label["模型调用"] == "1"
    assert by_label["输入 Token"] == by_label["输出 Token"] == by_label["其中思考"] == "0"
    assert by_label["模型费用"] == "$0.000000"


def test_shared_enrichment_respects_explicit_incomplete_history():
    result = llm_observability.enrich_task_step_with_llm_calls(
        {"status": "warning", "summary": "候选已恢复。"},
        SimpleNamespace(llm_calls=[], quality_summary=SimpleNamespace(
            call_telemetry_complete=False,
        )),
    )
    by_label = {item["label"]: item["value"] for item in result["debug"]["metrics"]}
    assert by_label["模型调用"] == by_label["输入 Token"] == by_label["模型费用"] == "未知"


def test_shared_llm_projection_lists_each_actual_model_and_provider_once():
    items, metrics = llm_observability.project_llm_calls(
        [
            _call(),
            _call(),
            _call(
                profile_id="quality",
                model_id="claude-sonnet",
                provider_host="openrouter.ai",
            ),
        ]
    )

    by_label = {item["label"]: item["value"] for item in metrics}
    assert by_label["调用模型"] == "gpt-5.4-mini、claude-sonnet"
    assert by_label["调用方式"] == (
        "本地 Codex CLI（local-codex-cli）、"
        "OpenRouter API（openrouter.ai）"
    )
    assert by_label["模型配置"] == "chatgpt-subscription、quality"
    assert all(
        fact["label"] not in {"实际模型", "调用方式", "模型配置"}
        for item in items
        for fact in item["facts"]
    )


def test_task_debug_projection_never_persists_prompt_or_model_output():
    atomic_result = SimpleNamespace(
        input=SimpleNamespace(
            contract_version="input-v1",
            upstream_contract_version="upstream-v1",
            upstream_operation_id="operation-1",
            source_track_id="vocals",
        ),
        contract_version="output-v1",
        profile_id="profile-1",
        model_id="model-1",
        stop_reason="completed",
        llm_calls=[_call()],
        warnings=[],
    )

    result = llm_observability.enrich_task_step_with_llm_calls(
        {"status": "success", "summary": "已完成。"},
        atomic_result,
    )
    serialized = str(result)
    metric_labels = [
        item["label"] for item in result["debug"]["metrics"]
    ]

    assert result["debug"]["sections"][0]["title"] == "模型调用明细"
    assert metric_labels.count("调用模型") == 1
    assert "实际模型" not in metric_labels
    assert metric_labels.count("模型配置") == 1
    assert "prompt" not in serialized.lower()
    assert "response_text" not in serialized
    assert "reasoning_content" not in serialized


def test_task_debug_projection_preserves_existing_debug_content():
    atomic_result = SimpleNamespace(
        input=None,
        contract_version="output-v1",
        llm_calls=[_call()],
        warnings=[],
    )

    result = llm_observability.enrich_task_step_with_llm_calls(
        {
            "status": "success",
            "summary": "已完成。",
            "debug": {
                "description": "保留当前节点自己的调试说明。",
                "metrics": [{"label": "上游指纹", "value": "abc123"}],
                "sections": [
                    {
                        "title": "节点诊断",
                        "items": [
                            {
                                "title": "输入检查",
                                "text": "结构完整。",
                                "facts": [],
                                "links": [],
                            }
                        ],
                    }
                ],
                "notes": ["节点自己的调试提醒。"],
            },
        },
        atomic_result,
    )

    assert result["debug"]["description"] == (
        "保留当前节点自己的调试说明。"
    )
    assert {"label": "上游指纹", "value": "abc123"} in (
        result["debug"]["metrics"]
    )
    assert [item["title"] for item in result["debug"]["sections"]] == [
        "节点诊断",
        "模型调用明细",
    ]
    assert result["debug"]["notes"] == ["节点自己的调试提醒。"]
