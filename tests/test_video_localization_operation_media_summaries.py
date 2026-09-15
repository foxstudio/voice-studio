from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_step_results,
    operation_queue,
    operation_state,
    workflow_contracts,
)
from app.domains.video_localization.schemas import VideoLocalizationDraft, VideoLocalizationOperation  # noqa: E402


def test_section_review_reader_projection_hides_safely_rejected_model_suggestions():
    result = operation_queue._without_non_actionable_section_review_items(
        {
            "status": "warning",
            "sections": [
                {
                    "title": "检查结果",
                    "items": [
                        {
                            "title": "需要复核：这一处",
                            "text": (
                                "S2 返回了一条无法定位到原文的建议，"
                                "已忽略。"
                            ),
                        },
                        {
                            "title": "asr_0002",
                            "text": "这一处有明确片段和复听理由。",
                        },
                    ],
                }
            ],
        }
    )

    assert result["sections"] == [
        {
            "title": "检查结果",
            "items": [
                {
                    "title": "asr_0002",
                    "text": "这一处有明确片段和复听理由。",
                }
            ],
        }
    ]


def test_asr_operation_registers_required_steps_up_front():
    placeholders = operation_queue._asr_step_placeholders()

    assert list(placeholders) == [
        "asr",
        "diarization",
        "initial_analysis_join",
        "understand_document",
        "visual_evidence",
        "research",
        "normalize_entities",
        "section_review_r1",
        "review_decisions_r1",
        "whole_recheck_r1",
        "transcript_quality_gate",
        "alignment",
        "audio_boundaries",
        "boundary_review",
        "subtitle_track",
    ]
    assert all(step["status"] == "todo" for step in placeholders.values())


def test_compact_operation_summaries_keep_web_visible_atomic_result_counts():
    draft = VideoLocalizationDraft.model_validate(
        {
            "operations": [
                {
                    "operation_id": "visual_task",
                    "project_id": "project_1",
                    "kind": "english_asr",
                    "status": "success",
                    "label": "听写字幕",
                    "result_summary": {
                        "stage": "画面取证已完成（开发单步）",
                        "stage_id": "visual_evidence",
                        "execution_scope": "partial",
                        "frame_count": 7,
                    },
                },
                {
                    "operation_id": "research_task",
                    "project_id": "project_1",
                    "kind": "english_asr",
                    "status": "success",
                    "label": "听写字幕",
                    "result_summary": {
                        "stage": "资料查询已完成（开发单步）",
                        "stage_id": "research",
                        "execution_scope": "partial",
                        "evidence_count": 6,
                    },
                },
            ]
        }
    )
    summaries = [
        operation_queue.project_repository_operation_summary(operation)
        for operation in draft.operations
    ]
    by_id = {
        item.operation_id: item.result_summary
        for item in summaries
    }
    assert by_id["visual_task"]["frame_count"] == 7
    assert by_id["research_task"]["evidence_count"] == 6


def test_final_asr_refresh_keeps_observed_debug_and_uses_authoritative_business_result():
    observed = {
        "asr": {
            "status": "running",
            "summary": "正在生成原始听写。",
            "sections": [],
            "debug": {
                "description": "本次运行实际保存的调试记录。",
                "metrics": [{"label": "运行批次", "value": "formal-run-1"}],
                "sections": [],
                "notes": [],
            },
        },
        "obsolete": {
            "status": "success",
            "summary": "旧流程残留步骤。",
            "debug": {"metrics": [{"label": "不应保留", "value": "1"}]},
        },
    }
    authoritative = {
        "asr": {
            "status": "success",
            "summary": "原始听写已经完成。",
            "sections": [{"title": "结果", "items": []}],
            "debug": {
                "description": "重建结果的默认调试信息。",
                "metrics": [{"label": "默认值", "value": "fallback"}],
                "sections": [],
                "notes": [],
            },
        },
        "subtitle_track": {
            "status": "success",
            "summary": "字幕轨已经写入。",
            "sections": [],
        },
    }

    merged = operation_queue._merge_authoritative_task_step_results(
        observed,
        authoritative,
    )

    assert list(merged) == ["asr", "subtitle_track"]
    assert merged["asr"]["status"] == "success"
    assert merged["asr"]["summary"] == "原始听写已经完成。"
    assert merged["asr"]["sections"] == [{"title": "结果", "items": []}]
    assert merged["asr"]["debug"] == observed["asr"]["debug"]
    assert "obsolete" not in merged


def test_final_asr_refresh_merge_is_idempotent():
    observed = {
        "asr": {
            "status": "success",
            "summary": "原始听写已经完成。",
            "debug": {
                "description": "本次运行实际保存的调试记录。",
                "metrics": [{"label": "运行批次", "value": "formal-run-1"}],
                "sections": [],
                "notes": [],
            },
        }
    }
    authoritative = {
        "asr": {
            "status": "success",
            "summary": "原始听写已经完成。",
            "metrics": [{"label": "字幕数量", "value": "184"}],
        }
    }

    first = operation_queue._merge_authoritative_task_step_results(
        observed,
        authoritative,
    )
    second = operation_queue._merge_authoritative_task_step_results(
        first,
        authoritative,
    )

    assert second == first


def test_unused_asr_steps_are_marked_as_skipped_after_success():
    summary = {
        "task_step_results": {
            "asr": {"label": "生成原始听写", "order": 10, "status": "success", "summary": "转写完成。"},
        }
    }

    completed = operation_queue._mark_unused_asr_steps_skipped(summary)

    assert completed["task_step_results"]["asr"]["status"] == "success"
    assert completed["task_step_results"]["section_review_r1"]["status"] == "skipped"
    assert completed["task_step_results"]["review_decisions_r1"]["status"] == "skipped"

    operation = VideoLocalizationOperation(
        operation_id="completed_asr",
        project_id="project_1",
        kind="english_asr",
        status="success",
        label="听写字幕",
    )
    final_result = operation_queue._build_task_final_result(operation, completed)

    assert final_result["metrics"][0] == {"label": "已执行步骤", "value": "1"}
    assert final_result["coverage"]["total_count"] == 1
    assert [item["title"] for item in final_result["sections"][0]["items"]] == [
        "生成原始听写"
    ]


def test_successful_degraded_asr_closes_the_interrupted_review_step():
    summary = {
        "task_step_results": {
            "asr": {"label": "生成原始听写", "order": 10, "status": "success"},
            "understand_document": {
                "label": "理解全文并规划复查",
                "order": 30,
                "status": "running",
                "summary": "正在通读整份原始转写。",
            },
            "asr_review_deferred": {
                "label": "ASR 语言复核",
                "order": 30,
                "status": "warning",
                "summary": "语言复核暂时没有完成，已保留原始 ASR 并继续生成时间轴。",
                "error_detail": {
                    "code": "llm_insufficient_credits",
                    "message": "语言模型服务余额不足，请充值或改用其他模型配置",
                },
            },
        }
    }

    completed = operation_queue._mark_unused_asr_steps_skipped(summary)

    interrupted = completed["task_step_results"]["understand_document"]
    assert interrupted["status"] == "warning"
    assert interrupted["error_detail"]["code"] == "llm_insufficient_credits"
    assert "没有完成" in interrupted["summary"]


def test_quality_gate_depends_on_the_single_local_whole_recheck():
    workflow = workflow_contracts.asr_workflow_summary()
    tasks = {
        task["id"]: task
        for stage in workflow["stages"]
        for task in stage["atomic_tasks"]
    }

    gate = tasks["transcript_quality_gate"]

    assert gate["depends_on"] == ["whole_recheck_r1"]
    assert gate["dependency_mode"] == "all"
    assert tasks["visual_evidence"]["optional"] is True
    assert tasks["research"]["optional"] is True
    assert "section_review_r2" not in tasks
    assert "review_decisions_r2" not in tasks
    assert "whole_recheck_r2" not in tasks
    assert tasks["section_review_r1"]["optional"] is False


def test_source_audio_workflow_is_one_versioned_path_free_step():
    workflow = workflow_contracts.source_audio_workflow_summary()

    assert workflow["schema_version"] == "source-audio-workflow-v1"
    assert workflow["workflow_id"] == "source-audio"
    assert len(workflow["stages"]) == 1
    assert workflow["stages"][0]["id"] == "source_audio"
    assert workflow["stages"][0]["atomic_tasks"] == [
        {
            "id": "extract_source_audio",
            "label": "提取并保存原始音轨",
            "description": (
                "锁定当前源视频，完成本地音轨提取和结构校验，"
                "再把结果与任务状态一起写入项目。"
            ),
            "order": 10,
            "execution": "serial",
            "depends_on": [],
            "optional": False,
            "dependency_mode": "all",
            "output_contract_version": "source-audio-step-output-v1",
        }
    ]


def test_completed_asr_history_keeps_review_rounds_and_final_changes():
    draft = VideoLocalizationDraft.model_validate(
        {
            "transcription": {
                "segments": [
                    {
                        "segment_id": "asr_0001",
                        "start_ms": 1_000,
                        "end_ms": 2_000,
                        "raw_text": "CineSense 2",
                        "corrected_text": "Seedance 2",
                    },
                    {
                        "segment_id": "asr_0002",
                        "start_ms": 2_000,
                        "end_ms": 3_000,
                        "raw_text": "C-Ends",
                        "corrected_text": "C-Ends",
                    },
                ],
                "transcript_quality_cycle": {
                    "prompt_version": "asr-flow-v5",
                    "assessment_rounds": 2,
                    "changes": [
                        {
                            "segment_id": "asr_0001",
                            "before": "CineSense 2",
                            "after": "Seedance 2",
                            "reason": "Inconsistent name; canonical is Seedance 2",
                            "confidence": 0.9,
                        }
                    ],
                    "warnings": [
                        {
                            "segment_id": "asr_0001",
                            "excerpt": "Seedance 2",
                            "message": "建议会改变数字或否定关系，已保留原文。",
                        },
                        {
                            "segment_id": "asr_0002",
                            "excerpt": "C-Ends",
                            "message": "Low confidence; no strong evidence for replacement",
                        }
                    ],
                    "task_step_results": {
                        "understand_document": {"label": "理解全文并规划复查", "order": 30, "status": "success", "summary": "已理解全文。"},
                        "research": {"label": "核对名称与背景", "order": 40, "status": "success", "summary": "已查证名称。"},
                        "section_review_r1": {"label": "第 1 轮分段复查", "order": 50, "status": "warning", "summary": "旧轮次提醒。"},
                    },
                },
            }
        }
    )

    results = operation_state.english_asr_summary(draft)["task_step_results"]

    assert list(results) == [
        "asr", "understand_document", "research", "section_review_r1",
        "transcript_quality_gate", "alignment", "audio_boundaries", "boundary_review", "subtitle_track",
    ]
    assert results["section_review_r1"]["summary"] == "旧轮次提醒。"
    quality = results["transcript_quality_gate"]
    assert quality["summary"] == "整篇校对完成，已自动继续校时；另有 1 处建议复听。"
    assert quality["sections"][0]["items"][0]["before"] == "CineSense 2"
    assert quality["sections"][0]["items"][0]["after"] == "Seedance 2"
    assert quality["sections"][1]["title"] == "建议复听"
    assert quality["review_targets"][0]["title"] == "听一下“C-Ends”"
    assert "没有足够证据" in quality["review_targets"][0]["detail"]


def test_completed_asr_history_prefers_persisted_quality_gate_result():
    persisted_gate = {
        "status": "warning",
        "purpose": "确认全文和时间结构可以安全进入校时。",
        "summary": "已自动继续校时，建议复听 3 处。",
        "metrics": [
            {"label": "建议复听", "value": "3"},
            {"label": "阻断项", "value": "0"},
        ],
        "sections": [{"title": "建议复听", "items": []}],
        "notes": [],
    }
    draft = VideoLocalizationDraft.model_validate(
        {
            "transcription": {
                "segments": [
                    {
                        "segment_id": "asr_0001",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "raw_text": "Current best transcript.",
                    }
                ],
                "transcript_quality_cycle": {
                    "prompt_version": "asr-flow-v5",
                    "assessment_rounds": 1,
                    "changes": [],
                    "warnings": [
                        {
                            "segment_id": "asr_0001",
                            "excerpt": "Current best transcript",
                            "message": "Low confidence.",
                        }
                    ],
                    "task_step_results": {
                        "transcript_quality_gate": persisted_gate,
                    },
                },
            }
        }
    )

    quality = operation_state.english_asr_summary(draft)[
        "task_step_results"
    ]["transcript_quality_gate"]

    assert quality["summary"] == persisted_gate["summary"]
    assert quality["metrics"] == persisted_gate["metrics"]
    assert "请人工试听" not in quality["summary"]


def test_compact_task_summary_keeps_explicit_review_targets():
    result = operation_queue._compact_live_step_result(
        {
            "status": "warning",
            "summary": "还有一处需要试听。",
            "review_targets": [
                {"title": "听一下名称", "location": "00:01.000 - 00:02.000", "detail": "确认名称是否听对。"}
            ],
            "sections": [],
        }
    )

    assert result["review_targets"] == [
        {"title": "听一下名称", "location": "00:01.000 - 00:02.000", "detail": "确认名称是否听对。"}
    ]


def test_completed_asr_summary_groups_and_keeps_all_workflow_steps():
    executed_step_ids = [
        "asr",
        "understand_document",
        "visual_evidence",
        "research",
        "normalize_entities",
        "section_review_r1",
        "review_decisions_r1",
        "whole_recheck_r1",
        "transcript_quality_gate",
        "alignment",
        "audio_boundaries",
        "boundary_review",
        "subtitle_track",
    ]
    operation = VideoLocalizationOperation(
        operation_id="completed_workflow",
        project_id="project_1",
        kind="english_asr",
        status="success",
        label="听写字幕",
    )
    final_result = operation_queue._build_task_final_result(
        operation,
        {
            "cue_count": 184,
            "task_step_results": {
                step_id: {
                    "label": step_id,
                    "order": index * 10,
                    "status": (
                        "warning"
                        if index in {4, 8, 12, 15}
                        else "success"
                    ),
                    "summary": f"{step_id} 已完成。",
                }
                for index, step_id in enumerate(executed_step_ids, start=1)
            },
        },
    )
    compact = operation_queue._compact_live_step_result(final_result)

    assert final_result["detail_mode"] == "workflow_summary"
    assert [section["title"] for section in final_result["sections"]] == [
        "生成原始听写",
        "理解与校对全文",
        "时间与字幕整理",
    ]
    assert sum(len(section["items"]) for section in compact["sections"]) == 13
    assert final_result["coverage"] == {
        "mode": "complete",
        "shown_count": 13,
        "total_count": 13,
        "unit": "个任务步骤",
    }


def test_final_summary_prefers_actionable_review_targets_and_deduplicates_legacy_warnings():
    operation = VideoLocalizationOperation(
        operation_id="review_targets",
        project_id="project_1",
        kind="english_asr",
        status="success",
        label="听写字幕",
    )
    repeated_detail = "无资料可确认真实姓名与机构，无法给出更可信替换。"
    final_result = operation_queue._build_task_final_result(
        operation,
        {
            "task_step_results": {
                "research": {
                    "label": "核对名称与背景",
                    "order": 40,
                    "status": "warning",
                    "summary": "部分名称没有找到足够资料。",
                    "sections": [],
                },
                "whole_recheck_r1": {
                    "label": "第 1 轮全文复核",
                    "order": 70,
                    "status": "warning",
                    "summary": "仍有内容需要抽查。",
                    "sections": [{
                        "title": "检查结果",
                        "items": [{
                            "title": "需要复核：这一处",
                            "text": repeated_detail,
                            "meta": "",
                            "tone": "warning",
                        }],
                    }],
                },
                "transcript_quality_gate": {
                    "label": "进入校时前检查",
                    "order": 290,
                    "status": "warning",
                    "summary": "整篇校对完成，已自动继续校时。",
                    "review_targets": [{
                        "title": "听一下嘉宾姓名",
                        "location": "12秒 – 15秒",
                        "detail": repeated_detail,
                    }],
                    "sections": [],
                },
                "alignment": {
                    "label": "对齐逐词时间",
                    "order": 300,
                    "status": "warning",
                    "summary": "部分逐词时间需要抽查。",
                    "review_targets": [{
                        "title": "抽查低可信度词",
                        "location": "1分32秒 – 1分49秒",
                        "detail": "这段逐词时间按原始识别片段估算。",
                    }],
                    "sections": [],
                },
            },
        },
    )

    assert final_result["review_targets"] == [
        {
            "title": "听一下嘉宾姓名",
            "location": "12秒 – 15秒",
            "detail": repeated_detail,
        },
        {
            "title": "抽查低可信度词",
            "location": "1分32秒 – 1分49秒",
            "detail": "这段逐词时间按原始识别片段估算。",
        },
        {
            "title": "核对名称与背景",
            "detail": "部分名称没有找到足够资料。",
        },
    ]


def test_final_asr_summary_uses_final_subtitle_gate_instead_of_resolved_intermediate_warnings():
    operation = VideoLocalizationOperation(
        operation_id="final_gate",
        project_id="project_1",
        kind="english_asr",
        status="success",
        label="听写字幕",
    )
    final_result = operation_queue._build_task_final_result(
        operation,
        {
            "cue_count": 12,
            "task_step_results": {
                "transcript_quality_gate": {
                    "label": "进入校时前检查",
                    "order": 290,
                    "status": "warning",
                    "summary": "中途曾建议复听 GPT Image 2.0。",
                    "review_targets": [{
                        "title": "旧提醒",
                        "detail": "中途曾建议复听 GPT Image 2.0。",
                    }],
                    "sections": [],
                },
                "subtitle_track": {
                    "label": "生成并检查字幕轨",
                    "order": 330,
                    "status": "warning",
                    "summary": "最终字幕仍有 1 项需要复核。",
                    "review_targets": [{
                        "title": "字幕 cue_0007",
                        "location": "cue_0007",
                        "detail": "这一条为保留完整意思而偏长，请确认画面内是否来得及读完。",
                    }],
                    "sections": [],
                    "final_quality_gate": {
                        "status": "blocked",
                        "blocker_count": 1,
                        "warning_count": 0,
                    },
                },
            },
        },
    )

    assert final_result["review_targets"] == [{
        "title": "字幕 cue_0007",
        "location": "cue_0007",
        "detail": "这一条为保留完整意思而偏长，请确认画面内是否来得及读完。",
    }]
    assert {"label": "最终硬问题", "value": "1"} in final_result["metrics"]
    assert {"label": "最终提醒", "value": "0"} in final_result["metrics"]
    assert final_result["status"] == "warning"


def test_alignment_history_uses_complete_saved_facts():
    segment = type(
        "Segment",
        (),
        {
            "start_ms": 92_000,
            "end_ms": 109_000,
            "corrected_text": "And the data centers",
            "raw_text": "And the data centers",
        },
    )()
    notes = asr_step_results._alignment_notes(
        "片段 asr_0016 未生成可用的词级对齐结果",
        {"asr_0016": segment},
        frame_rate=30.0,
    )

    assert notes == [
        (
            "1分32秒 – 1分49秒“And the data centers”："
            "这一段没有生成可用的逐词对齐结果，当前时间按原始识别片段估算，"
            "建议抽查字幕入点和出点。"
        )
    ]


def test_regular_step_summary_still_limits_large_detail_lists():
    result = operation_queue._compact_live_step_result(
        {
            "status": "success",
            "summary": "生成了大量逐项结果。",
            "sections": [
                {
                    "title": "逐项结果",
                    "items": [{"title": f"结果 {index}"} for index in range(1, 16)],
                }
            ],
        }
    )

    assert len(result["sections"][0]["items"]) == 8
    assert result["coverage"]["truncated"] is True


def test_saved_asr_results_explain_empty_and_legacy_detail_states():
    visual = asr_step_results._reader_ready_flow_result(
        "visual_evidence",
        {
            "metrics": [
                {"label": "截图", "value": "4"},
                {"label": "画面观察", "value": "2"},
            ],
            "sections": [],
        },
    )
    normalization = asr_step_results._reader_ready_flow_result(
        "normalize_entities",
        {"metrics": [], "sections": []},
    )
    understanding = asr_step_results._reader_ready_flow_result(
        "understand_document",
        {
            "sections": [
                {
                    "title": "全文理解",
                    "items": [
                        {
                            "meta": "字幕 1 - 16",
                            "facts": [
                                {"label": "重点名称", "value": "Kimi K3"}
                            ],
                        }
                    ],
                }
            ]
        },
    )

    assert visual["sections"][0]["items"][0]["title"] == "这条历史任务只保存了数量"
    assert "4 张截图" in visual["sections"][0]["items"][0]["text"]
    assert normalization["sections"][0]["items"][0]["title"] == "本次无需统一名称"
    item = understanding["sections"][0]["items"][0]
    assert item["meta"] == "听写片段 1 - 16"
    assert item["facts"][0]["label"] == "待核对名称"


def test_localization_operation_registers_v3_atomic_steps_up_front():
    placeholders = operation_queue._localization_step_placeholders()
    workflow_tasks = [
        task
        for stage in (
            operation_queue.workflow_contracts
            .LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
        )
        for task in stage.atomic_tasks
    ]

    assert list(placeholders) == [task.id for task in workflow_tasks]
    assert all(step["status"] == "todo" for step in placeholders.values())
    orders = [step["order"] for step in placeholders.values()]
    assert orders == [task.order for task in workflow_tasks]
    assert orders == sorted(set(orders))


def test_queued_localization_operation_already_contains_required_language_steps():
    summary = operation_queue._initial_operation_summary("localization_draft")

    assert summary["stage_id"] == "lock_localization_source"
    assert summary["workflow_id"] == "localization-v3"
    assert list(summary["task_step_results"]) == list(operation_queue._localization_step_placeholders())


def test_unused_localization_steps_are_marked_as_skipped_after_success():
    summary = {
        "task_step_results": {
            **operation_queue._localization_step_placeholders(),
            "lock_localization_source": {
                "label": "固定本次英文源数据",
                "order": 10,
                "status": "success",
                "summary": "输入已锁定。",
            },
        }
    }

    completed = operation_queue._mark_unused_localization_steps_skipped(summary)

    assert completed["task_step_results"]["lock_localization_source"]["status"] == "success"
    assert completed["task_step_results"]["build_localization_dual_tracks"]["status"] == "skipped"


def test_media_operation_summaries_only_include_known_draft_facts():
    draft = VideoLocalizationDraft.model_validate(
        {
            "source_media": {
                "audio_path": "/media/source.wav",
                "duration_ms": 12_345,
                "metadata": {
                    "audio_sample_rate": 48_000,
                    "audio_channels": 2,
                    "audio_extract_status": "completed",
                },
            },
            "stems": {
                "vocals_clean_path": "/media/vocals.wav",
                "background_path": "/media/background.wav",
                "separation_engine_id": "bs-roformer-viperx-1297:residual-v1",
                "separation_status": "completed",
            },
        }
    )

    assert operation_state.source_audio_summary(draft) == {
        "audio_path": "/media/source.wav",
        "duration_ms": 12_345,
        "sample_rate": 48_000,
        "channels": 2,
        "audio_extract_status": "completed",
        "track_count": 1,
        "available_track_count": 0,
        "media_status": "missing",
    }
    assert operation_state.stems_summary(draft) == {
        "vocals_clean_path": "/media/vocals.wav",
        "background_path": "/media/background.wav",
        "duration_ms": 12_345,
        "separation_engine_id": "bs-roformer-viperx-1297:residual-v1",
        "separation_status": "completed",
        "track_count": 2,
        "available_track_count": 0,
        "media_status": "missing",
    }


def test_media_operation_summaries_omit_unknown_values_and_count_existing_tracks():
    draft = VideoLocalizationDraft.model_validate(
        {
            "source_media": {"metadata": {}},
            "stems": {
                "vocals_clean_path": "/media/vocals.wav",
                "separation_status": "running",
            },
        }
    )

    assert operation_state.source_audio_summary(draft) == {
        "track_count": 0,
        "available_track_count": 0,
        "media_status": "unconfigured",
    }
    assert operation_state.stems_summary(draft) == {
        "vocals_clean_path": "/media/vocals.wav",
        "separation_status": "running",
        "track_count": 1,
        "available_track_count": 0,
        "media_status": "missing",
    }


def test_operation_summary_whitelist_keeps_media_facts():
    operation = VideoLocalizationOperation(
        operation_id="stems_1",
        project_id="project_1",
        kind="stems",
        status="success",
        result_summary={
            "duration_ms": 12_345,
            "sample_rate": 44_100,
            "channels": 2,
            "audio_extract_status": "completed",
            "separation_engine_id": "bs-roformer-viperx-1297:residual-v1",
            "separation_status": "completed",
            "track_count": 2,
            "vocals_clean_path": "/media/private-vocals.wav",
        },
    )
    summary = operation_queue.project_repository_operation_summary(
        operation
    ).result_summary
    assert summary["duration_ms"] == 12_345
    assert summary["sample_rate"] == 44_100
    assert summary["channels"] == 2
    assert summary["audio_extract_status"] == "completed"
    assert summary["separation_engine_id"] == "bs-roformer-viperx-1297:residual-v1"
    assert summary["separation_status"] == "completed"
    assert summary["track_count"] == 2
    assert "vocals_clean_path" not in summary
