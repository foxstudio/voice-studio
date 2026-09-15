from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_document_brief,
    localization_document_evidence,
)
from app.domains.video_localization.workflow_contracts import (  # noqa: E402
    LOCALIZATION_WORKFLOW_V3_DEFINITION,
)
from app.domains.video_localization.workflow_ledger import (  # noqa: E402
    LocalizationWorkflowLedger,
)
from app.services import web_search  # noqa: E402
from app.services import llm_runtime  # noqa: E402
from app.services.localization_ai_policy import (  # noqa: E402
    LocalizationAiPhaseRoute,
)
from tests.test_video_localization_localization_document_brief import (  # noqa: E402
    _inputs,
)


def _brief(*, with_question: bool):
    source, context, route = _inputs()
    content = localization_document_brief.LocalizationDocumentBriefContent(
        purpose="说明一个工具的用法。",
        audience="中文大众观众",
        structure=[
            {
                "section_id": "section_0001",
                "title": "开场",
                "function_zh": "说明主题。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        speaker_profile={
            "identity_zh": "创作者",
            "expertise_zh": "熟悉工具",
            "audience_distance_zh": "平等交流",
            "rhythm_zh": "自然口语",
            "stable_traits_zh": ["直接"],
        },
        emotional_arc=[
            {
                "section_id": "section_0001",
                "emotion_zh": "平静",
                "intensity": 2,
                "speech_acts": ["说明"],
            }
        ],
        immutable_facts=[
            {
                "fact_id": "fact_0001",
                "statement_zh": "介绍一个工具。",
                "source_cue_ids": ["cue_0001"],
            }
        ],
        cultural_adaptation_rules=["使用自然中文"],
        disfluency_policy_zh="保留有意义的停顿。",
        evidence_questions=(
            [
                {
                    "question_id": "question_0001",
                    "kind": "web",
                    "question_zh": "产品官方名称是什么？",
                    "query": "Example official product name",
                    "source_cue_ids": ["cue_0001"],
                    "reason_zh": "避免把产品名听错。",
                }
            ]
            if with_question
            else []
        ),
    )
    brief = localization_document_brief.LocalizationDocumentBriefResult(
        source_fingerprint=source.source_fingerprint,
        context_intent_fingerprint=context.context_intent_fingerprint,
        result_fingerprint="b" * 64,
        content=content,
        route=route,
        llm_calls=[
            {
                "call_id": "brief",
                "purpose": "localization_document_brief",
                "round_index": 1,
                "profile_id": "profile",
                "model_id": "model",
                "provider_host": "local",
                "request_chars": 1,
                "request_body_bytes": 1,
                "max_tokens": 1,
                "timeout_seconds": 1,
                "reasoning_effort_requested": "low",
                "reasoning_control_applied": True,
                "duration_ms": 1,
                "content_chars": 1,
                "reasoning_chars": 0,
            }
        ],
        quality_summary={
            "status": "warning" if with_question else "passed",
            "section_count": 1,
            "fact_count": 1,
            "term_relation_count": 0,
            "evidence_question_count": 1 if with_question else 0,
            "source_reference_complete": True,
            "model_call_count": 1,
        },
    )
    return source, brief


def test_evidence_collection_skips_when_document_has_no_questions(tmp_path: Path):
    source, brief = _brief(with_question=False)

    research = (
        localization_document_evidence.collect_localization_document_research(
            brief
        )
    )
    visual = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=None,
            frame_dir=tmp_path,
        )
    )

    assert research.status == "not_needed"
    assert research.answers == []
    assert visual.status == "not_needed"
    assert visual.frames == []
    assert (
        localization_document_evidence
        .project_localization_document_research_result(research)["status"]
        == "not_needed"
    )
    assert (
        localization_document_evidence
        .project_localization_document_visual_result(visual)["status"]
        == "not_needed"
    )

    evidence = (
        localization_document_evidence
        .adjudicate_localization_document_evidence(
            brief,
            research,
            visual,
            route=_inputs()[2],
        )
    )

    assert evidence.status == "not_needed"
    projected = (
        localization_document_evidence
        .project_localization_document_evidence_result(evidence)
    )
    assert projected["status"] == "not_needed"
    assert projected["summary"] == "没有证据问题，本步骤无需执行。"
    assert projected["notes"] == ["未调用模型。"]


def test_research_query_count_follows_document_questions(monkeypatch):
    _source, brief = _brief(with_question=True)
    queries = []

    def fake_search(_settings, query, *, api_key):
        queries.append(query)
        return [
            web_search.SearchResult(
                title="Example",
                url="https://example.com/official",
                snippet="Official product page",
            )
        ]

    monkeypatch.setattr(
        localization_document_evidence.web_search,
        "search",
        fake_search,
    )

    result = (
        localization_document_evidence.collect_localization_document_research(
            brief
        )
    )

    assert queries == ["Example official product name"]
    assert result.status == "passed"
    assert len(result.answers) == 1
    assert len(result.answers[0].sources) == 1
    projected = (
        localization_document_evidence
        .project_localization_document_research_result(result, brief=brief)
    )
    assert projected["status"] == "success"
    assert projected["sections"][0]["title"] == "查询结果"
    assert projected["sections"][0]["items"][0] == {
        "title": "产品官方名称是什么？",
        "meta": "question_0001",
        "text": "找到 1 条可供后续判断的资料。",
        "tone": "positive",
        "facts": [
            {
                "label": "查询内容",
                "value": "Example official product name",
            },
            {"label": "查询结果", "value": "已找到资料"},
            {
                "label": "为什么要查",
                "value": "避免把产品名听错。",
            },
        ],
        "links": [
            {
                "title": "Example",
                "url": "https://example.com/official",
                "text": "Official product page",
            }
        ],
    }


def test_visual_capture_executes_requested_question_and_projects_detail(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=False)
    visual_question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0002",
        kind="visual",
        question_zh="画面里的产品名称如何拼写？",
        source_cue_ids=["cue_0001"],
        reason_zh="需要读取画面文字。",
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": [visual_question]}
            )
        }
    )
    extracted = []

    def fake_extract(_video_path, destination, timestamp_ms):
        extracted.append(timestamp_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpeg-test-frame")

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    result = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )

    assert result.status == "passed"
    assert len(result.frames) == 3
    assert extracted == [135, 495, 810]
    projected = (
        localization_document_evidence
        .project_localization_document_visual_result(result, brief=brief)
    )
    assert projected["status"] == "success"
    assert projected["sections"][0]["title"] == "截图结果"
    assert projected["sections"][0]["items"][0] == {
        "title": "画面里的产品名称如何拼写？",
        "meta": "0.135 秒、0.495 秒、0.81 秒",
        "text": "已沿问题对应时间范围保存 3 张画面，供下一步统一判断。",
        "tone": "positive",
        "facts": [
            {"label": "对应原文", "value": "cue_0001"},
            {"label": "截图状态", "value": "已保存 3 张"},
            {
                "label": "为什么要看",
                "value": "需要读取画面文字。",
            },
        ],
        "links": [],
        "_frames": [
            {
                "frame_id": f"frame_{item.sha256[:12]}",
                "timestamp_ms": item.timestamp_ms,
                "round_index": 1,
            }
            for item in result.frames
        ],
        "_frame_rate": 30.0,
    }


def test_visual_capture_samples_across_question_time_range(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=False)
    source = source.model_copy(
        update={
            "input": source.input.model_copy(
                update={
                    "cues": [
                        source.input.cues[0].model_copy(
                            update={"end_ms": 1_900}
                        )
                    ]
                }
            )
        }
    )
    visual_question = (
        localization_document_brief.LocalizationEvidenceQuestion(
            question_id="question_0002",
            kind="visual",
            question_zh="变化后的最终形态是什么？",
            source_cue_ids=["cue_0001"],
            reason_zh="需要同时看到变化过程和最终结果。",
        )
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": [visual_question]}
            )
        }
    )
    extracted: list[int] = []

    def fake_extract(_video_path, destination, timestamp_ms):
        extracted.append(timestamp_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            f"jpeg-{timestamp_ms}".encode()
        )

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    result = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )

    assert len(result.frames) == 3
    assert extracted[0] < extracted[1] < extracted[2]
    assert extracted[0] > source.input.cues[0].start_ms
    assert extracted[-1] < source.input.cues[-1].end_ms
    assert extracted[-1] >= 1_700


def test_visual_capture_adds_neighbor_context_and_samples_long_dialogue(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=False)
    template = source.input.cues[0]
    cues = [
        template.model_copy(
            update={
                "cue_id": f"cue_{index:04d}",
                "start_ms": index * 2_000,
                "end_ms": index * 2_000 + 1_800,
            }
        )
        for index in range(1, 7)
    ]
    source = source.model_copy(
        update={"input": source.input.model_copy(update={"cues": cues})}
    )
    question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0002",
        kind="visual",
        question_zh="读取连续硬字幕的完整含义。",
        source_cue_ids=[
            "cue_0002",
            "cue_0003",
            "cue_0004",
            "cue_0005",
        ],
        reason_zh="音频识别可能把画面字幕听错。",
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": [question]}
            )
        }
    )
    extracted = []

    def fake_extract(_video_path, destination, timestamp_ms):
        extracted.append(timestamp_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"jpeg-{timestamp_ms}".encode())

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    result = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )

    assert len(extracted) == 6
    assert extracted == sorted(extracted)
    assert [frame.source_cue_ids for frame in result.frames] == [
        [f"cue_{index:04d}"] for index in range(1, 7)
    ]
    assert question.source_cue_ids == [
        "cue_0002",
        "cue_0003",
        "cue_0004",
        "cue_0005",
    ]


def test_visual_capture_keeps_middle_frame_for_every_long_core_cue_when_capped(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=False)
    template = source.input.cues[0]
    cues = [
        template.model_copy(
            update={
                "cue_id": f"cue_{index:04d}",
                "start_ms": index * 7_000,
                "end_ms": index * 7_000 + (
                    6_500
                    if index == 4
                    else 6_000
                    if 2 <= index <= 6
                    else 1_000
                ),
            }
        )
        for index in range(1, 8)
    ]
    source = source.model_copy(
        update={"input": source.input.model_copy(update={"cues": cues})}
    )
    question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0002",
        kind="visual",
        question_zh="读取连续硬字幕的完整含义。",
        source_cue_ids=[f"cue_{index:04d}" for index in range(2, 7)],
        reason_zh="长句内可能出现多次字幕切换。",
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": [question]}
            )
        }
    )
    extracted: list[int] = []

    def fake_extract(_video_path, destination, timestamp_ms):
        extracted.append(timestamp_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"jpeg-{timestamp_ms}".encode())

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    result = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )

    assert len(result.frames) == 8
    for cue in cues[1:6]:
        middle = localization_document_evidence._sample_timestamps(
            cue.start_ms,
            cue.end_ms,
            3,
        )[1]
        assert middle in extracted
    longest_core_cue = cues[3]
    assert (
        localization_document_evidence._sample_timestamps(
            longest_core_cue.start_ms,
            longest_core_cue.end_ms,
            3,
        )[-1]
        in extracted
    )


def test_visual_capture_samples_separate_distant_cue_clusters(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=False)
    template = source.input.cues[0]
    cues = [
        template.model_copy(
            update={
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1_000,
            }
        ),
        template.model_copy(
            update={
                "cue_id": "cue_0002",
                "start_ms": 2_000,
                "end_ms": 3_000,
            }
        ),
        template.model_copy(
            update={
                "cue_id": "cue_0003",
                "start_ms": 600_000,
                "end_ms": 601_000,
            }
        ),
        template.model_copy(
            update={
                "cue_id": "cue_0004",
                "start_ms": 1_200_000,
                "end_ms": 1_201_000,
            }
        ),
    ]
    source = source.model_copy(
        update={"input": source.input.model_copy(update={"cues": cues})}
    )
    question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0002",
        kind="visual",
        question_zh="核对三个相距很远的场景。",
        source_cue_ids=[item.cue_id for item in cues],
        reason_zh="不能用三个场景中间的无关画面代替。",
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": [question]}
            )
        }
    )
    extracted = []

    def fake_extract(_video_path, destination, timestamp_ms):
        extracted.append(timestamp_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"jpeg-{timestamp_ms}".encode())

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    result = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )

    assert extracted == [1_500, 600_500, 1_200_500]
    assert [item.source_cue_ids for item in result.frames] == [
        ["cue_0001", "cue_0002"],
        ["cue_0003"],
        ["cue_0004"],
    ]


def test_visual_capture_keeps_nearby_nonconsecutive_cues_exact(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=False)
    template = source.input.cues[0]
    cues = [
        template.model_copy(
            update={
                "cue_id": "cue_0166",
                "start_ms": 636_279,
                "end_ms": 637_079,
            }
        ),
        template.model_copy(
            update={
                "cue_id": "cue_0169",
                "start_ms": 642_000,
                "end_ms": 644_000,
            }
        ),
    ]
    source = source.model_copy(
        update={"input": source.input.model_copy(update={"cues": cues})}
    )
    question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0003",
        kind="visual",
        question_zh="分别读取两句画面字幕。",
        source_cue_ids=["cue_0166", "cue_0169"],
        reason_zh="两句之间还有其他台词，不能截取中间画面。",
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": [question]}
            )
        }
    )
    extracted = []

    def fake_extract(_video_path, destination, timestamp_ms):
        extracted.append(timestamp_ms)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"jpeg-{timestamp_ms}".encode())

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    result = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )

    assert extracted == [636_679, 643_000]
    assert [item.source_cue_ids for item in result.frames] == [
        ["cue_0166"],
        ["cue_0169"],
    ]


def test_visual_result_contract_accepts_collector_global_limit():
    frames = [
        localization_document_evidence.LocalizationDocumentVisualFrame(
            question_id=f"question_{index:04d}",
            question_zh="读取画面文字。",
            source_cue_ids=["cue_0001"],
            timestamp_ms=index,
            path=f"/tmp/frame-{index}.jpg",
            sha256=f"{index:064x}",
        )
        for index in range(
            localization_document_evidence.MAX_VISUAL_FRAMES
        )
    ]

    result = (
        localization_document_evidence.LocalizationDocumentVisualResult(
            brief_fingerprint="b" * 64,
            result_fingerprint="r" * 64,
            frames=frames,
            status="passed",
        )
    )

    assert len(result.frames) == (
        localization_document_evidence.MAX_VISUAL_FRAMES
    )


def test_visual_capture_samples_every_allowed_question_without_truncation(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=False)
    questions = [
        localization_document_brief.LocalizationEvidenceQuestion(
            question_id=f"question_{index:04d}",
            kind="visual",
            question_zh=f"读取第 {index} 个画面问题。",
            source_cue_ids=["cue_0001"],
            reason_zh="需要读取画面文字。",
        )
        for index in range(
            1,
            localization_document_evidence.MAX_VISUAL_QUESTIONS + 1,
        )
    ]
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": questions}
            )
        }
    )

    def fake_extract(_video_path, destination, timestamp_ms):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"jpeg-{timestamp_ms}".encode())

    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    result = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )

    assert len(result.frames) == (
        localization_document_evidence.MAX_VISUAL_QUESTIONS
        * localization_document_evidence.MAX_VISUAL_FRAMES_PER_QUESTION
    )
    assert {
        question.question_id
        for question in questions
    } == {frame.question_id for frame in result.frames}
    assert result.status == "passed"
    assert result.failed_question_ids == []


def test_evidence_adjudication_executes_with_search_and_visual_inputs(
    monkeypatch,
    tmp_path: Path,
):
    source, brief = _brief(with_question=True)
    visual_question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0002",
        kind="visual",
        question_zh="画面中的产品名称如何拼写？",
        source_cue_ids=["cue_0001"],
        reason_zh="需要用画面核对名称。",
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={
                    "evidence_questions": [
                        *brief.content.evidence_questions,
                        visual_question,
                    ]
                }
            )
        }
    )
    monkeypatch.setattr(
        localization_document_evidence.web_search,
        "search",
        lambda *_args, **_kwargs: [
            web_search.SearchResult(
                title="Example 官方页面",
                url="https://example.com/official",
                snippet="Example 是产品官方名称。",
            )
        ],
    )
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")

    def fake_extract(_video_path, destination, _timestamp_ms):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpeg-test-frame")

    monkeypatch.setattr(
        localization_document_evidence.media_assets,
        "extract_video_frame",
        fake_extract,
    )

    def fake_complete_multimodal_json(
        _prompt,
        payload,
        images,
        *,
        trace_sink,
        **_kwargs,
    ):
        assert len(images) == 3
        visual_rows = [
            item
            for item in payload["visual_frames"]
            if item["question_id"] == "question_0002"
        ]
        assert visual_rows == [
            {
                "question_id": "question_0002",
                "image_positions": [1, 2, 3],
                "timestamps_ms": [135, 495, 810],
                "source_cue_ids_by_image": [
                    ["cue_0001"],
                    ["cue_0001"],
                    ["cue_0001"],
                ],
            }
        ]
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="review-profile",
                model_id="review-model",
                provider_host="local-codex-cli",
                request_chars=600,
                request_body_bytes=900,
                max_tokens=4_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=50,
                finish_reason="stop",
                prompt_tokens=120,
                completion_tokens=50,
                total_tokens=170,
                content_chars=180,
            )
        )
        return {
            "answers": [
                {
                    "question_id": "question_0001",
                    "source_cue_ids": ["cue_0001"],
                    "evidence_image_positions": [],
                    "status": "supported",
                    "conclusion_zh": "官方资料确认产品名为 Example。",
                    "constraint_zh": "产品名统一写作 Example。",
                    "source_urls": ["https://example.com/official"],
                },
                {
                    "question_id": "question_0002",
                    "source_cue_ids": ["cue_0001"],
                    "evidence_image_positions": [1, 2, 3],
                    "status": "supported",
                    "conclusion_zh": "画面可用于核对产品名。",
                    "constraint_zh": "按画面显示保留产品拼写。",
                    "source_urls": [],
                },
            ]
        }

    monkeypatch.setattr(
        localization_document_evidence.llm_runtime,
        "complete_multimodal_json",
        fake_complete_multimodal_json,
    )
    research = (
        localization_document_evidence.collect_localization_document_research(
            brief
        )
    )
    visual = (
        localization_document_evidence.collect_localization_document_visuals(
            brief,
            source,
            source_video_path=video_path,
            frame_dir=tmp_path / "frames",
        )
    )
    result = (
        localization_document_evidence
        .adjudicate_localization_document_evidence(
            brief,
            research,
            visual,
            route=LocalizationAiPhaseRoute(
                phase="evidence_adjudication",
                profile_id="review-profile",
                model_id="review-model",
                reasoning_effort="low",
                output_format="json",
                prompt_strategy="adaptive",
            ),
        )
    )

    assert result.status == "passed"
    assert result.constraints == [
        "产品名统一写作 Example。",
        "按画面显示保留产品拼写。",
    ]
    projected = (
        localization_document_evidence
        .project_localization_document_evidence_result(
            result,
            brief=brief,
        )
    )
    assert projected["status"] == "success"
    assert [section["title"] for section in projected["sections"]] == [
        "证据结论",
    ]
    assert projected["sections"][0]["items"][0]["links"] == [
        {
            "title": "资料 1",
            "url": "https://example.com/official",
        }
    ]
    assert projected["sections"][0]["items"][0]["title"] == (
        "产品官方名称是什么？"
    )
    assert all(
        metric["label"] != "调用模型"
        for metric in projected["metrics"]
    )


def test_evidence_adjudication_batches_images_at_runtime_limit(
    monkeypatch,
    tmp_path: Path,
):
    _source, brief = _brief(with_question=False)
    questions = [
        localization_document_brief.LocalizationEvidenceQuestion(
            question_id=f"question_{index:04d}",
            kind="visual",
            question_zh=f"读取第 {index} 张图。",
            source_cue_ids=["cue_0001"],
            reason_zh="需要读取画面文字。",
        )
        for index in range(
            1,
            (localization_document_evidence.llm_runtime.MAX_IMAGE_COUNT * 2)
            + 2,
        )
    ]
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": questions}
            )
        }
    )
    frames = []
    for index, question in enumerate(questions, start=1):
        path = tmp_path / f"frame-{index:02d}.jpg"
        path.write_bytes(f"jpeg-{index}".encode())
        frames.append(
            localization_document_evidence.LocalizationDocumentVisualFrame(
                question_id=question.question_id,
                question_zh=question.question_zh,
                source_cue_ids=["cue_0001"],
                timestamp_ms=index,
                path=str(path),
                sha256=(
                    localization_document_evidence.media_assets
                    .file_sha256(path)
                ),
            )
        )
    visual = (
        localization_document_evidence.LocalizationDocumentVisualResult(
            brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="v" * 64,
            frames=frames,
            status="passed",
        )
    )
    research = (
        localization_document_evidence.LocalizationDocumentResearchResult(
            brief_fingerprint=brief.result_fingerprint,
            result_fingerprint="r" * 64,
            status="not_needed",
        )
    )
    batch_sizes = []

    def fake_complete_multimodal_json(
        prompt,
        payload,
        images,
        **_kwargs,
    ):
        assert "只有人物动作、表情或场景不能代替台词含义" in prompt
        assert "不能授权中文创作新增动作旁白" in prompt
        batch_sizes.append(len(images))
        return {
            "answers": [
                {
                    "question_id": item["question_id"],
                    "source_cue_ids": item["source_cue_ids"],
                    "evidence_image_positions": next(
                        row["image_positions"]
                        for row in payload["visual_frames"]
                        if row["question_id"] == item["question_id"]
                    ),
                    "status": "supported",
                    "conclusion_zh": "画面文字清楚。",
                    "constraint_zh": "按画面文字表达。",
                    "source_urls": [],
                }
                for item in payload["questions"]
            ]
        }

    monkeypatch.setattr(
        localization_document_evidence.llm_runtime,
        "complete_multimodal_json",
        fake_complete_multimodal_json,
    )

    result = (
        localization_document_evidence
        .adjudicate_localization_document_evidence(
            brief,
            research,
            visual,
            route=LocalizationAiPhaseRoute(
                phase="evidence_adjudication",
                profile_id="review-profile",
                model_id="review-model",
                reasoning_effort="low",
                output_format="json",
                prompt_strategy="adaptive",
            ),
        )
    )

    assert batch_sizes == [
        localization_document_evidence.llm_runtime.MAX_IMAGE_COUNT,
        localization_document_evidence.llm_runtime.MAX_IMAGE_COUNT,
        1,
    ]
    assert result.status == "passed"
    assert len(result.answers) == len(questions)


def test_evidence_answer_keeps_adjacent_frame_provenance_without_edit_ownership(monkeypatch, tmp_path: Path):
    _source, brief = _brief(with_question=False)
    question = localization_document_brief.LocalizationEvidenceQuestion(
        question_id="question_0001",
        kind="visual",
        question_zh="读取连续硬字幕的完整含义。",
        source_cue_ids=["cue_0001"],
        reason_zh="避免把相邻异语台词按错误 ASR 翻译。",
    )
    brief = brief.model_copy(
        update={
            "content": brief.content.model_copy(
                update={"evidence_questions": [question]}
            )
        }
    )
    frame_path = tmp_path / "adjacent.jpg"
    frame_path.write_bytes(b"adjacent-frame")
    visual = localization_document_evidence.LocalizationDocumentVisualResult(
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="v" * 64,
        frames=[
            localization_document_evidence.LocalizationDocumentVisualFrame(
                question_id=question.question_id,
                question_zh=question.question_zh,
                source_cue_ids=["cue_0002"],
                timestamp_ms=2_000,
                path=str(frame_path),
                sha256=(
                    localization_document_evidence.media_assets.file_sha256(
                        frame_path
                    )
                ),
            )
        ],
        status="passed",
    )
    research = localization_document_evidence.LocalizationDocumentResearchResult(
        brief_fingerprint=brief.result_fingerprint,
        result_fingerprint="r" * 64,
        status="not_needed",
    )

    monkeypatch.setattr(
        localization_document_evidence.llm_runtime,
        "complete_multimodal_json",
        lambda *_args, **_kwargs: {
            "answers": [
                {
                    "question_id": "question_0001",
                    "evidence_image_positions": [1],
                    "anchored_image_positions": [1],
                    "status": "supported",
                    "conclusion_zh": (
                        "两张连续字幕共同确认了一句台词；cue_0002 的错误"
                        "听写不得另行翻译。"
                    ),
                    "constraint_zh": "只使用画面确认的完整台词。",
                    "anchored_target_text_zh": "她仍在那里等着。",
                    "source_urls": [],
                }
            ]
        },
    )

    result = localization_document_evidence.adjudicate_localization_document_evidence(
        brief,
        research,
        visual,
        route=LocalizationAiPhaseRoute(
            phase="evidence_adjudication",
            profile_id="review-profile",
            model_id="review-model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        ),
    )

    assert result.answers[0].source_cue_ids == ["cue_0001"]
    assert result.answers[0].observed_source_cue_ids == ["cue_0002"]
    assert result.answers[0].evidence_image_positions == [1]
    assert result.answers[0].anchored_source_cue_ids == ["cue_0002"]


def test_ledger_reports_running_then_not_needed_for_optional_evidence():
    _source, brief = _brief(with_question=False)
    reports = []
    ledger = LocalizationWorkflowLedger(
        "operation-1",
        definition=LOCALIZATION_WORKFLOW_V3_DEFINITION,
        on_report=lambda step_id, result: reports.append(
            (step_id, result["status"], result["summary"])
        ),
    )

    ledger.run(
        "collect_localization_research_evidence_v3",
        "正在检查是否需要查询资料。",
        lambda: (
            localization_document_evidence
            .collect_localization_document_research(brief)
        ),
        (
            localization_document_evidence
            .project_localization_document_research_result
        ),
    )

    assert reports == [
        (
            "collect_localization_research_evidence_v3",
            "running",
            "正在检查是否需要查询资料。",
        ),
        (
            "collect_localization_research_evidence_v3",
            "not_needed",
            "全文没有需要联网确认的问题，本步骤无需执行。",
        ),
    ]
