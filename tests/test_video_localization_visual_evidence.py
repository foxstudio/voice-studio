from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_visual_evidence_operation_projection,
    media_assets,
    operation_queue,
    visual_evidence,
)
from app.services import llm_runtime  # noqa: E402


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        ("visible_text", "可见文字"),
        ("chart", "图表"),
        ("object", "物体或界面"),
        ("scene_context", "场景信息"),
    ],
)
def test_visual_question_kind_uses_one_shared_chinese_label(kind, label):
    assert visual_evidence.visual_question_kind_label(kind) == label


def _question(
    question_id: str = "visual_01",
    *,
    start_ms: int = 0,
    end_ms: int = 2_000,
    frame_strategy: str = "nearby",
) -> visual_evidence.AsrVisualEvidenceQuestion:
    return visual_evidence.AsrVisualEvidenceQuestion(
        question_id=question_id,
        start_ordinal=1,
        end_ordinal=1,
        start_segment_id="asr_0001",
        end_segment_id="asr_0001",
        start_ms=start_ms,
        end_ms=end_ms,
        kind="visible_text",
        reason="听写中的专名不清楚，需要读取画面字幕条。",
        question="画面中直接显示了哪些姓名和机构名称？",
        frame_strategy=frame_strategy,
    )


def _request(
    *,
    questions: list[visual_evidence.AsrVisualEvidenceQuestion] | None = None,
    max_frames_per_question: int = 4,
    max_total_frames: int = 16,
) -> visual_evidence.AsrVisualEvidenceInput:
    return visual_evidence.AsrVisualEvidenceInput(
        upstream_operation_id="understanding-operation",
        video_sha256="video-sha256",
        video_duration_ms=120_000,
        profile_id="vision-profile",
        document_summary="两位嘉宾讨论人工智能芯片投资。",
        segments=[
            {
                "ordinal": 1,
                "segment_id": "asr_0001",
                "start_ms": 0,
                "end_ms": 2_000,
                "text": "Duan Feeny from Capital Management.",
                "speaker_cluster_id": "cluster_01",
            }
        ],
        questions=questions if questions is not None else [_question()],
        policy={
            "max_frames_per_question": max_frames_per_question,
            "max_total_frames": max_total_frames,
        },
    )


def _install_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="vision-profile",
            model_id="vision-model",
        ),
    )


def _install_fake_extractor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    extracted_timestamps: list[int] | None = None,
) -> None:
    def fake_extract_frame(
        *,
        video_path: Path,
        frame_root: Path,
        question_id: str,
        frame_index: int,
        round_index: int,
        timestamp_ms: int,
    ) -> visual_evidence.AsrVisualEvidenceFrame:
        assert video_path.is_file()
        frame_root.mkdir(parents=True, exist_ok=True)
        if extracted_timestamps is not None:
            extracted_timestamps.append(timestamp_ms)
        data = f"{question_id}:{frame_index}:{timestamp_ms}".encode()
        destination = frame_root / (
            f"{question_id}-{frame_index}-{timestamp_ms}.jpg"
        )
        destination.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return visual_evidence.AsrVisualEvidenceFrame(
            frame_id=f"frame_{digest[:12]}",
            question_id=question_id,
            frame_index=frame_index,
            round_index=round_index,
            timestamp_ms=timestamp_ms,
            file_name=destination.name,
            sha256=digest,
            size_bytes=len(data),
        )

    monkeypatch.setattr(
        visual_evidence,
        "_extract_frame",
        fake_extract_frame,
    )
    monkeypatch.setattr(
        visual_evidence.shutil,
        "which",
        lambda _name: "/usr/local/bin/ffmpeg",
    )


def _visual_answer(
    *,
    answer: str = "画面字幕条显示嘉宾姓名和任职机构。",
    confidence: float = 0.98,
    needs_more_frames: bool = False,
) -> dict:
    return {
        "answer": answer,
        "visible_text": [
            "JoAnne Feeney",
            "Advisors Capital",
        ],
        "search_terms": [
            "JoAnne Feeney Advisors Capital",
        ],
        "relevant_frame_indexes": [1],
        "confidence": confidence,
        "needs_web_search": True,
        "needs_more_frames": needs_more_frames,
        "more_frames_reason": (
            "当前画面还没有出现姓名条。"
            if needs_more_frames
            else ""
        ),
        "limitations": ["画面证据不能单独决定规范名称。"],
    }


def test_special_language_subtitle_questions_are_focused_and_run_first():
    segments = [
        visual_evidence.AsrVisualEvidenceSegment(
            ordinal=1,
            segment_id="asr_0001",
            start_ms=0,
            end_ms=60_000,
            text="The friends enter the underworld.",
        ),
        visual_evidence.AsrVisualEvidenceSegment(
            ordinal=2,
            segment_id="asr_0002",
            start_ms=92_000,
            end_ms=96_000,
            text="終わったぜ。最後に本気で楽しんだの、何百年前だよ。",
        ),
        visual_evidence.AsrVisualEvidenceSegment(
            ordinal=3,
            segment_id="asr_0003",
            start_ms=120_000,
            end_ms=123_000,
            text="Patience, my little friend.",
        ),
    ]
    ordinary = _question("visual_name")
    subtitle = _question(
        "visual_subtitle",
        start_ms=0,
        end_ms=123_000,
    ).model_copy(
        update={
            "start_ordinal": 1,
            "end_ordinal": 3,
            "start_segment_id": "asr_0001",
            "end_segment_id": "asr_0003",
            "question": "请逐字抄录这段特殊语言对应的原片字幕。",
        }
    )

    result = visual_evidence._focus_and_prioritize_subtitle_questions(
        segments,
        [ordinary, subtitle],
    )

    assert [item.question_id for item in result] == [
        "visual_subtitle",
        "visual_name",
    ]
    assert result[0].start_ordinal == 2
    assert result[0].end_ordinal == 2
    assert result[0].start_segment_id == "asr_0002"
    assert result[0].end_segment_id == "asr_0002"
    assert result[0].start_ms == 92_000
    assert result[0].end_ms == 96_000
    assert result[0].frame_strategy == "look_ahead"


def test_no_questions_is_a_fast_noop(tmp_path: Path):
    request = _request(
        questions=[],
    )

    result = visual_evidence.VisualEvidenceService().run(
        request,
        source_video_path=tmp_path / "missing-video.webm",
        frame_dir=tmp_path / "frames",
    )

    assert result.status == "not_needed"
    assert result.stop_reason == "no_questions"
    assert result.frames == []
    assert result.observations == []
    assert result.quality_summary.status == "passed"
    assert result.quality_summary.source_text_unchanged is True


def test_missing_video_returns_a_traceable_failure(tmp_path: Path):
    result = visual_evidence.VisualEvidenceService().run(
        _request(),
        source_video_path=tmp_path / "missing-video.webm",
        frame_dir=tmp_path / "frames",
    )

    assert result.status == "failed"
    assert result.stop_reason == "source_unavailable"
    assert result.frames == []
    assert result.observations[0].error_code == "source_unavailable"
    assert result.quality_summary.failed_question_count == 1


def test_missing_ffmpeg_returns_a_traceable_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        visual_evidence.shutil,
        "which",
        lambda _name: None,
    )

    result = visual_evidence.VisualEvidenceService().run(
        _request(),
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert result.status == "failed"
    assert result.stop_reason == "extractor_unavailable"
    assert result.frames == []
    assert result.observations[0].error_code == "extractor_unavailable"


def test_extracts_frames_and_returns_multimodal_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    _install_fake_extractor(monkeypatch)
    calls: list[dict] = []

    def fake_complete_multimodal_json(**kwargs):
        calls.append(kwargs)
        kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="vision-profile",
                model_id="vision-model",
                provider_host="openrouter.ai",
                request_chars=1200,
                request_body_bytes=250000,
                max_tokens=1600,
                timeout_seconds=120,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=2800,
                finish_reason="stop",
                prompt_tokens=900,
                completion_tokens=120,
                reasoning_tokens=40,
                total_tokens=1020,
                cost_usd=0.0045,
                content_chars=240,
                reasoning_chars=80,
            )
        )
        return _visual_answer()

    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        fake_complete_multimodal_json,
    )
    request = _request()

    result = visual_evidence.VisualEvidenceService().run(
        request,
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert result.status == "completed"
    assert result.stop_reason == "completed"
    assert result.profile_id == "vision-profile"
    assert result.model_id == "vision-model"
    assert len(result.frames) == 1
    assert result.observations[0].status == "answered"
    assert result.observations[0].visible_text == [
        "JoAnne Feeney",
        "Advisors Capital",
    ]
    assert result.observations[0].search_terms == [
        "JoAnne Feeney Advisors Capital"
    ]
    assert len(calls) == 1
    assert len(calls[0]["images"]) == 1
    assert result.observations[0].round_count == 1
    assert result.quality_summary.model_call_count == 1
    assert result.quality_summary.second_round_question_count == 0
    assert "reasoning_effort" not in calls[0]
    assert calls[0]["user_payload"]["transcript_context"][0]["text"] == (
        request.segments[0].text
    )
    assert result.llm_calls[0].purpose == "visual_analysis"
    assert result.llm_calls[0].cost_usd == 0.0045
    manifest = json.loads(
        (tmp_path / "frames" / "manifest.json").read_text(
            encoding="utf-8",
        )
    )
    assert manifest["schema_version"] == (
        "asr-visual-evidence-frame-manifest-v1"
    )
    assert manifest["upstream_operation_id"] == (
        "understanding-operation"
    )
    assert manifest["frames"][0]["frame_id"] == result.frames[0].frame_id
    assert manifest["frames"][0]["sha256"] == result.frames[0].sha256
    debug = asr_visual_evidence_operation_projection.step_result(
        result,
        project_id="project_1",
        operation_id="operation_1",
    )
    observation = debug["sections"][0]["items"][0]
    assert "_frame_ids" not in observation
    assert "_frames" not in observation
    assert "_frame_rate" not in observation
    assert observation["links"][0]["url"].endswith(
        f"/{result.frames[0].frame_id}"
    )
    debug = debug["debug"]
    assert debug["sections"][0]["title"] == "模型调用明细"
    assert "模型费用" in {
        item["label"] for item in debug["metrics"]
    }


def test_formal_frame_resolver_only_serves_manifest_registered_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    operation_id = "operation_1"
    frame_root = tmp_path / "frames"
    frame_root.mkdir()
    frame_data = b"registered screenshot"
    frame_path = frame_root / "frame.jpg"
    frame_path.write_bytes(frame_data)
    digest = hashlib.sha256(frame_data).hexdigest()
    frame_id = f"frame_{digest[:12]}"
    (frame_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": (
                    "asr-visual-evidence-frame-manifest-v1"
                ),
                "upstream_operation_id": (
                    f"{operation_id}:understand_document"
                ),
                "frames": [
                    {
                        "frame_id": frame_id,
                        "file_name": frame_path.name,
                        "sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        operation_queue,
        "get_operation",
        lambda _project_id, _operation_id: SimpleNamespace(
            kind="english_asr",
            parameters={},
        ),
    )
    monkeypatch.setattr(
        operation_queue.media_assets,
        "visual_evidence_frame_dir",
        lambda _project_id, _operation_id: frame_root,
    )

    assert operation_queue.get_formal_visual_evidence_frame(
        "project_1",
        operation_id,
        frame_id,
    ) == frame_path

    frame_path.write_bytes(b"tampered")
    with pytest.raises(Exception) as exc_info:
        operation_queue.get_formal_visual_evidence_frame(
            "project_1",
            operation_id,
            frame_id,
        )
    assert "指纹不一致" in str(exc_info.value)


def test_formal_frame_resolver_serves_localization_frame_from_isolated_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    operation_id = "operation_1"
    frame_root = tmp_path / "frames"
    localization_root = frame_root / "localization-v3"
    localization_root.mkdir(parents=True)
    frame_path = localization_root / "question_0001-01.jpg"
    frame_path.write_bytes(b"localization screenshot")
    digest = hashlib.sha256(frame_path.read_bytes()).hexdigest()
    frame_id = f"frame_{digest[:12]}"
    monkeypatch.setattr(
        operation_queue,
        "get_operation",
        lambda _project_id, _operation_id: SimpleNamespace(
            kind="localization_draft",
            parameters={},
        ),
    )
    monkeypatch.setattr(
        operation_queue.media_assets,
        "visual_evidence_frame_dir",
        lambda _project_id, _operation_id: frame_root,
    )

    assert operation_queue.get_formal_visual_evidence_frame(
        "project_1",
        operation_id,
        frame_id,
    ) == frame_path


def test_localization_visual_links_are_hydrated_from_saved_frames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    frame_root = tmp_path / "frames"
    localization_root = frame_root / "localization-v3"
    localization_root.mkdir(parents=True)
    for index in range(1, 4):
        (localization_root / f"question_0001-{index:02d}.jpg").write_bytes(
            f"frame-{index}".encode()
        )
    monkeypatch.setattr(
        operation_queue.media_assets,
        "visual_evidence_frame_dir",
        lambda _project_id, _operation_id: frame_root,
    )
    projected = operation_queue._with_localization_visual_evidence_links(
        "project_1",
        "operation_1",
        {
            "sections": [{
                "title": "截图结果",
                "items": [{
                    "title": "需要确认的画面问题",
                    "meta": "1 秒、2 秒、3 秒",
                    "links": [],
                    "facts": [],
                }],
            }],
        },
    )

    links = projected["sections"][0]["items"][0]["links"]
    assert len(links) == 3
    assert links[0]["meta"] == "1秒"
    assert links[0]["url"].startswith(
        "/api/projects/project_1/video-localization/operations/"
        "operation_1/visual-evidence-frames/frame_"
    )


def test_visual_evidence_thumbnail_is_small_cached_derivative(
    tmp_path: Path,
):
    source_path = tmp_path / "frame.jpg"
    Image.new("RGB", (1920, 1080), color=(20, 40, 60)).save(
        source_path,
        format="JPEG",
        quality=95,
    )
    original = source_path.read_bytes()

    first = media_assets.visual_evidence_thumbnail(source_path)
    first_mtime = first.stat().st_mtime_ns
    second = media_assets.visual_evidence_thumbnail(source_path)

    assert first == second
    assert first.parent.name == ".thumbnails"
    assert first.name.endswith("-jpeg-w320-q72-v1.jpg")
    with Image.open(first) as preview:
        assert preview.size == (320, 180)
    assert first.stat().st_size < source_path.stat().st_size
    assert first.stat().st_mtime_ns == first_mtime
    assert source_path.read_bytes() == original


def test_visual_evidence_thumbnail_falls_back_to_original(
    tmp_path: Path,
):
    source_path = tmp_path / "frame.jpg"
    source_path.write_bytes(b"not an image")

    assert media_assets.visual_evidence_thumbnail(source_path) == source_path


def test_unsupported_image_input_degrades_without_blocking_other_questions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    _install_fake_extractor(monkeypatch)
    llm_calls = 0

    def unsupported_model(**_kwargs):
        nonlocal llm_calls
        llm_calls += 1
        raise llm_runtime.LlmRuntimeError(
            "当前模型不支持图片输入。",
            code="llm_image_input_unsupported",
            status_code=400,
        )

    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        unsupported_model,
    )
    request = _request(
        questions=[
            _question("visual_01"),
            _question("visual_02", start_ms=3_000, end_ms=5_000),
        ],
    )

    result = visual_evidence.VisualEvidenceService().run(
        request,
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert result.status == "skipped"
    assert result.stop_reason == "vision_unavailable"
    assert llm_calls == 1
    assert len(result.frames) == 2
    assert [item.status for item in result.observations] == [
        "unresolved",
        "unresolved",
    ]
    assert result.observations[0].error_code == (
        "llm_image_input_unsupported"
    )
    assert result.observations[1].error_code == (
        "llm_image_input_unsupported"
    )


def test_one_invalid_answer_does_not_stop_later_questions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    _install_fake_extractor(monkeypatch)
    answers = iter(
        [
            {
                **_visual_answer(),
                "confidence": 2,
            },
            _visual_answer(answer="第二个画面问题已正常回答。"),
        ]
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        lambda **_kwargs: next(answers),
    )
    request = _request(
        questions=[
            _question("visual_01"),
            _question("visual_02", start_ms=3_000, end_ms=5_000),
        ],
    )

    result = visual_evidence.VisualEvidenceService().run(
        request,
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert result.status == "partial"
    assert result.stop_reason == "partial_failure"
    assert [item.status for item in result.observations] == [
        "failed",
        "answered",
    ]
    assert result.observations[0].error_code == "vision_response_invalid"
    assert result.observations[1].answer == (
        "第二个画面问题已正常回答。"
    )


def test_look_ahead_uses_bounded_future_timestamps():
    assert visual_evidence._frame_timestamps(
        _question(
            start_ms=0,
            end_ms=2_000,
            frame_strategy="look_ahead",
        ),
        duration_ms=120_000,
        limit=4,
    ) == [2_000, 0]
    assert visual_evidence._frame_timestamps(
        _question(
            start_ms=60_000,
            end_ms=93_000,
            frame_strategy="look_ahead",
        ),
        duration_ms=120_000,
        limit=4,
    ) == [93_000, 90_000, 75_000, 60_000]


def test_nearby_prefers_midpoint_and_chart_prefers_end():
    assert visual_evidence._frame_timestamps(
        _question(start_ms=10_000, end_ms=20_000),
        duration_ms=120_000,
        limit=3,
    ) == [15_000, 10_000, 20_000]
    chart_question = _question(start_ms=10_000, end_ms=20_000)
    chart_question = chart_question.model_copy(update={"kind": "chart"})
    assert visual_evidence._frame_timestamps(
        chart_question,
        duration_ms=120_000,
        limit=3,
    ) == [20_000, 15_000, 10_000]


def test_known_model_time_references_are_localized_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    _install_fake_extractor(monkeypatch)
    answer = _visual_answer(
        answer="第 1 张截图位于 210091ms，可见姓名条。",
    )
    answer["limitations"] = ["210091毫秒附近没有看到更多文字。"]
    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        lambda **_kwargs: answer,
    )
    request = _request(
        questions=[
            _question(
                start_ms=165_091,
                end_ms=212_000,
                frame_strategy="look_ahead",
            )
        ]
    ).model_copy(update={"video_duration_ms": 300_000})

    result = visual_evidence.VisualEvidenceService().run(
        request,
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    observation = result.observations[0]
    assert "ms" not in observation.answer
    assert "毫秒" not in " ".join(observation.limitations)
    assert "3分30秒3帧" in observation.answer
    assert "3分30秒3帧" in observation.limitations[0]


def test_failed_first_extraction_uses_replacement_as_first_round(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    _install_fake_extractor(monkeypatch)
    successful_extractor = visual_evidence._extract_frame
    extraction_attempts: list[tuple[int, int, int]] = []

    def fail_first_frame(**kwargs):
        extraction_attempts.append(
            (
                kwargs["timestamp_ms"],
                kwargs["frame_index"],
                kwargs["round_index"],
            )
        )
        if kwargs["timestamp_ms"] == 45_000:
            raise OSError("first frame unavailable")
        return successful_extractor(**kwargs)

    monkeypatch.setattr(
        visual_evidence,
        "_extract_frame",
        fail_first_frame,
    )
    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        return _visual_answer()

    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        fake_complete,
    )

    result = visual_evidence.VisualEvidenceService().run(
        _request(
            questions=[
                _question(
                    end_ms=60_000,
                    frame_strategy="look_ahead",
                )
            ]
        ),
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert extraction_attempts == [
        (45_000, 1, 1),
        (30_000, 1, 1),
    ]
    assert len(calls) == 1
    assert calls[0]["user_payload"]["round_index"] == 1
    assert result.frames[0].frame_index == 1
    assert result.frames[0].round_index == 1
    assert result.observations[0].round_count == 1
    assert result.observations[0].frame_ids_by_round == [
        [result.frames[0].frame_id]
    ]


def test_low_confidence_first_frame_triggers_one_bounded_second_round(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    _install_fake_extractor(monkeypatch)
    answers = iter(
        [
            _visual_answer(
                answer="当前画面尚未出现姓名条。",
                confidence=0.4,
                needs_more_frames=True,
            ),
            _visual_answer(),
        ]
    )
    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        return next(answers)

    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        fake_complete,
    )

    result = visual_evidence.VisualEvidenceService().run(
        _request(
            questions=[
                _question(
                    end_ms=60_000,
                    frame_strategy="look_ahead",
                )
            ]
        ),
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert len(calls) == 2
    assert len(calls[0]["images"]) == 1
    assert len(calls[1]["images"]) == 3
    assert calls[1]["user_payload"]["round_index"] == 2
    assert calls[1]["user_payload"]["previous_answer"] is not None
    assert "timestamp_ms" not in str(calls[0]["user_payload"])
    assert "start_ms" not in str(calls[0]["user_payload"])
    assert calls[0]["user_payload"]["frames"][0]["time"] == "45秒"
    assert len(result.frames) == 4
    assert result.observations[0].round_count == 2
    assert result.observations[0].second_round_trigger == (
        "当前画面还没有出现姓名条。"
    )
    assert result.quality_summary.model_call_count == 2
    assert result.quality_summary.second_round_question_count == 1


def test_total_frame_limit_is_shared_across_questions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    extracted_timestamps: list[int] = []
    _install_fake_extractor(
        monkeypatch,
        extracted_timestamps=extracted_timestamps,
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        lambda **_kwargs: _visual_answer(
            confidence=0.4,
            needs_more_frames=True,
        ),
    )
    request = _request(
        questions=[
            _question(
                "visual_01",
                end_ms=60_000,
                frame_strategy="look_ahead",
            ),
            _question(
                "visual_02",
                start_ms=60_000,
                end_ms=120_000,
                frame_strategy="look_ahead",
            ),
            _question(
                "visual_03",
                start_ms=90_000,
                end_ms=92_000,
                frame_strategy="look_ahead",
            ),
        ],
        max_frames_per_question=4,
        max_total_frames=5,
    )

    result = visual_evidence.VisualEvidenceService().run(
        request,
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert len(result.frames) == 5
    assert len(extracted_timestamps) == 5
    assert [item.question_id for item in result.frames] == [
        "visual_01",
        "visual_01",
        "visual_01",
        "visual_01",
        "visual_02",
    ]
    assert result.observations[2].status == "unresolved"
    assert result.observations[2].limitations == [
        "已达到本次任务的截图数量上限。"
    ]
    assert result.observations[2].round_count == 0
    assert result.quality_summary.model_call_count == 3


def test_result_cannot_decide_names_or_modify_transcript_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    video_path = tmp_path / "source.webm"
    video_path.write_bytes(b"video")
    _install_profile(monkeypatch)
    _install_fake_extractor(monkeypatch)
    monkeypatch.setattr(
        llm_runtime,
        "complete_multimodal_json",
        lambda **_kwargs: _visual_answer(),
    )
    request = _request()
    original_input = request.model_dump(mode="json")

    result = visual_evidence.VisualEvidenceService().run(
        request,
        source_video_path=video_path,
        frame_dir=tmp_path / "frames",
    )

    assert request.model_dump(mode="json") == original_input
    assert result.input.model_dump(mode="json") == original_input
    assert result.input.segments[0].text == (
        "Duan Feeny from Capital Management."
    )
    assert result.quality_summary.source_text_unchanged is True
    assert result.quality_summary.canonical_name_decided is False
    assert result.quality_summary.text_modified is False
