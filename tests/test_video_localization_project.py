from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.api import video_localization as video_localization_api  # noqa: E402
from app.models.schemas import LlmProviderProfile  # noqa: E402
from app.schemas.video_localization_tts_handoff import TtsTaskRegistrationEventV1  # noqa: E402
from app.schemas.video_localization_dubbing_production import (  # noqa: E402
    DubbingGenerationGroup,
    DubbingGenerationPlan,
    DubbingProductionState,
    DubbingSemanticUnit,
    DubbingSpeechIsland,
)
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    BatchSegmentResult,
    BatchTask,
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    TaskStatus,
    VideoLocalizationAlignedWord,
    VideoLocalizationBoundaryReview,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationResearchQuery,
    VideoLocalizationResearchSource,
    VideoLocalizationResearchState,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptEditOperation,
    VideoLocalizationTranscriptionState,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
    VideoLocalizationTimelineEditReceipt,
    VoiceFile,
)
from app.domains.video_localization import media_assets  # noqa: E402
from app.domains.video_localization import draft_store  # noqa: E402
from app.domains.video_localization import project_manifest  # noqa: E402
from app.domains.video_localization import project_lifecycle_cleanup  # noqa: E402
from app.domains.video_localization import project_snapshot_projection  # noqa: E402
from app.domains.video_localization import localization_source as video_localization_localization_source  # noqa: E402
from app.domains.video_localization import operation_queue as video_localization_operation_queue  # noqa: E402
from app.domains.video_localization import operation_state as video_localization_operation_state  # noqa: E402
from app.domains.video_localization import reference_clips as video_localization_reference_clips  # noqa: E402
from app.domains.video_localization import exporting as video_localization_exporting  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.domains.video_localization import source_pipeline as video_localization_source_pipeline  # noqa: E402
from app.domains.video_localization import transcript_quality_gate as video_localization_transcript_quality_gate  # noqa: E402
from app.domains.video_localization import tts_pipeline as video_localization_tts_pipeline  # noqa: E402
from app.domains.video_localization import timeline_edit_receipts as video_localization_timeline_edit_receipts  # noqa: E402
from app.domains.video_localization import whole_recheck as video_localization_whole_recheck  # noqa: E402
from app.services import (
    audio_tools,
    batch_queue,
    custom_reference_store,
    database,
    history_store,
    project_store,
    settings_store,
    task_queue,
    video_localization_operation_ledger_store,
    video_localization_operation_store,
    video_localization_tts_handoff,
    voice_store,
)  # noqa: E402
from app.services import video_localization_tts_handoff_store  # noqa: E402
from app.errors import AppException  # noqa: E402


def _attachment_filename(response) -> str:
    disposition = response.headers["content-disposition"]
    return unquote(disposition.split("filename*=UTF-8''", 1)[1])


def test_local_phrase_repair_commit_is_atomic_and_persistent(tmp_path: Path):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "局部短语原子替换", "description": ""},
    ).json()["project_id"]
    draft = {
        "project_type": "video_localization",
        "schema_version": "v1",
        "timeline_clips": [
            {
                "clip_id": "accepted_left",
                "track_id": "dub",
                "status": "ready",
                "start_ms": 1000,
                "end_ms": 1800,
                "dub_lane": 1,
            },
            {
                "clip_id": "accepted_right",
                "track_id": "dub",
                "status": "ready",
                "start_ms": 1800,
                "end_ms": 2600,
                "dub_lane": 1,
            },
            {
                "clip_id": "candidate",
                "track_id": "dub",
                "status": "ready",
                "start_ms": 3000,
                "end_ms": 4200,
                "source_start_ms": 0,
                "source_end_ms": 1200,
                "dub_lane": 0,
            },
        ],
    }
    assert client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    ).status_code == 200
    video_localization_service.update_video_localization_atomic(
        project_id,
        lambda current: current.model_copy(
            update={
                "timeline_clips": [
                    {
                        **dict(item),
                        "audio_path": str(tmp_path / "candidate.wav"),
                    }
                    if dict(item).get("clip_id") == "candidate"
                    else dict(item)
                    for item in current.timeline_clips
                ]
            }
        ),
        intent="runtime",
    )

    committed = client.post(
        f"/api/projects/{project_id}/video-localization/timeline-clips/candidate/local-phrase-repair/commit",
        json={
            "schema_version": "video-localization-local-phrase-repair-v1",
            "replace_clip_ids": ["accepted_left", "accepted_right"],
            "target_start_ms": 1000,
            "target_end_ms": 2450,
            "source_start_ms": 80,
            "source_end_ms": 1100,
            "target_text": "局部修补台词",
        },
    )

    assert committed.status_code == 200, committed.text
    clips = committed.json()["timeline_clips"]
    assert [item["clip_id"] for item in clips] == ["candidate"]
    assert clips[0]["start_ms"] == 1000
    assert clips[0]["end_ms"] == 2450
    assert clips[0]["tts_target_text"] == "局部修补台词"
    assert clips[0]["local_phrase_repair"] == {
        "schema_version": "video-localization-local-phrase-repair-v1",
        "replaced_clip_ids": ["accepted_left", "accepted_right"],
    }
    refreshed = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    assert refreshed["timeline_clips"] == clips


def test_local_phrase_repair_rejects_unfinished_candidate_without_deleting_old_clip(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "局部短语失败保留", "description": ""},
    ).json()["project_id"]
    draft = {
        "project_type": "video_localization",
        "schema_version": "v1",
        "timeline_clips": [
            {
                "clip_id": "accepted_old",
                "track_id": "dub",
                "status": "ready",
                "start_ms": 1000,
                "end_ms": 2000,
            },
            {
                "clip_id": "unfinished_candidate",
                "track_id": "dub",
                "status": "running",
                "start_ms": 1000,
                "end_ms": 2000,
            },
        ],
    }
    assert client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    ).status_code == 200

    rejected = client.post(
        f"/api/projects/{project_id}/video-localization/timeline-clips/unfinished_candidate/local-phrase-repair/commit",
        json={
            "schema_version": "video-localization-local-phrase-repair-v1",
            "replace_clip_ids": ["accepted_old"],
            "target_start_ms": 1000,
            "target_end_ms": 1900,
            "source_start_ms": 0,
            "source_end_ms": 800,
            "target_text": "不会提交",
        },
    )

    assert rejected.status_code == 409, rejected.text
    refreshed = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    assert [
        item["clip_id"] for item in refreshed["timeline_clips"]
    ] == ["accepted_old", "unfinished_candidate"]


def test_timeline_marker_feature_is_removed(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "无标记项目", "description": ""},
    ).json()
    project_id = project["project_id"]

    created = client.post(
        f"/api/projects/{project_id}/video-localization/timeline-markers",
        json={
            "start_ms": 1_250,
            "end_ms": 2_500,
            "title": "这里需要复核",
            "note": "听一下气口是否自然",
            "category": "review",
            "color": "orange",
            "status": "open",
        },
    )
    assert created.status_code == 404
    saved = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    assert "timeline_markers" not in saved


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    return TestClient(app)


def _create_current_dubbing_plan(
    client: TestClient,
    project_id: str,
) -> dict:
    snapshot_response = client.get(f"/api/projects/{project_id}/video-localization/dubbing/snapshot")
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["semantic_units"]
    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json={
            "schema_version": "dubbing-generation-plan-input-v1",
            "source_revision": snapshot["source_revision"],
            "semantic_units": [
                {
                    **unit,
                    "speech_policy": "translate",
                    "scene_id": "scene-test",
                }
                for unit in snapshot["semantic_units"]
            ],
            "boundaries": snapshot["boundaries"],
            "policy": {
                "preferred_group_units": 1,
                "hard_max_group_units": 2,
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _dubbing_lineage_kwargs(
    plan: dict,
    subtitle_id: str,
) -> dict:
    group = next(item for item in plan["groups"] if subtitle_id in item["subtitle_ids"])
    return {
        "dubbing_plan_revision": plan["plan_revision"],
        "dubbing_group_id": group["group_id"],
        "dubbing_target_subtitle_ids": group["subtitle_ids"],
    }


def _canonical_tts_task(
    project_id: str,
    workflow_id: str,
    *,
    segment_id: str = "localized_0001",
    source_cue_id: str = "cue_0001",
    start_ms: int = 2_000,
    end_ms: int = 3_000,
    text: str = "字幕",
) -> dict:
    target_snapshot = {
        "subtitle_ids": [segment_id],
        "segment_id": segment_id,
        "text": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "binding_fingerprint": "test-binding",
    }
    source_snapshot = {
        "cue_ids": [source_cue_id],
        "word_ids": [],
        "start_ms": start_ms,
        "end_ms": end_ms,
        "speaker_id": "speaker_01",
        "ref_text": "Source line.",
    }
    return {
        "workflow_id": workflow_id,
        "project_id": project_id,
        "segment_id": segment_id,
        "subtitle_summary": text,
        "text": text,
        "source_cue_ids": [source_cue_id],
        "start_ms": start_ms,
        "end_ms": end_ms,
        "stages": [
            {"kind": "generation", "parameters": {}},
            {
                "kind": "placement",
                "parameters": {
                    "target_snapshot": target_snapshot,
                    "source_snapshot": source_snapshot,
                },
            },
        ],
    }


def _set_canonical_tts_tasks(client: TestClient, project_id: str, tasks: list[dict]) -> None:
    current = video_localization_service.get_video_localization(project_id)
    assert current is not None
    saved = video_localization_service.save_video_localization(
        project_id,
        current.model_copy(
            update={
                "tts_tasks": [VideoLocalizationTtsTask.model_validate(item) for item in tasks]
            }
        ),
    )
    assert saved is not None


def _configure_asr_review_model(monkeypatch) -> None:
    profile = LlmProviderProfile(
        profile_id="deepseek-asr-review",
        name="DeepSeek ASR review",
        base_url="https://api.deepseek.com/v1",
        model_id="deepseek-chat",
        enabled=True,
    )

    def review_transcript(segments, **kwargs):
        step_result = {
            "status": "success",
            "purpose": "先理解全文，再分段复查，最后重新通读。",
            "summary": "已完成全文理解和 ASR 复核。",
            "metrics": [],
            "sections": [],
            "notes": [],
        }
        if kwargs.get("on_progress"):
            kwargs["on_progress"](0.30, "flow:understand_document|理解全文并规划复查")
        if kwargs.get("on_report"):
            kwargs["on_report"]("understand_document", step_result)
        return video_localization_source_pipeline.transcription.asr_flow.AsrReviewRun(
            segments=segments,
            research=VideoLocalizationResearchState(status="not_needed"),
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            report={
                "prompt_version": (video_localization_source_pipeline.transcription.asr_flow.PROMPT_VERSION),
                "status": "passed",
                "assessment_rounds": 2,
                "repair_rounds": 0,
                "total_repairs": 0,
                "changes": [],
                "warnings": [],
                "step_result": step_result,
                "task_flow": [],
                "task_step_results": {"understand_document": step_result},
            },
            stage_timings={"understand_document": {"duration_ms": 1}, "research": {"duration_ms": 0}},
            review_meta={
                "status": "completed",
                "profile_id": profile.profile_id,
                "model_id": profile.model_id,
                "error": None,
                "quality_flags": ["asr_flow_reviewed"],
                "task_flow": [],
                "task_step_results": {"understand_document": step_result},
            },
        )

    monkeypatch.setattr(
        video_localization_source_pipeline.transcription.asr_flow,
        "review_transcript",
        review_transcript,
    )
    pipeline = video_localization_source_pipeline.DEFAULT_ASR_PIPELINE
    original_run_transcript_review = pipeline.run_transcript_review

    def run_transcript_review(request, *, context=None):
        result = original_run_transcript_review(
            request,
            context=context,
        )
        gate_input = video_localization_transcript_quality_gate.AsrTranscriptQualityGateInput(
            upstream_operation_id="test-whole-recheck",
            source_track_id=request.source_track_id,
            source_audio_sha256=request.source_audio_sha256,
            language=request.language,
            round_index=1,
            segments=[item.model_copy(deep=True) for item in request.segments],
            upstream_status="completed",
            upstream_passed=True,
            upstream_next_action="finish",
            upstream_quality_summary=(
                video_localization_whole_recheck.AsrWholeRecheckQualitySummary(
                    status="passed",
                    segment_count=len(request.segments),
                    source_text_unchanged=True,
                    segment_ids_unchanged=True,
                    source_timing_unchanged=True,
                    next_sections_cover_all_segments=True,
                    unresolved_items_reference_known_segments=True,
                )
            ),
        )
        return result.model_copy(
            update={
                "quality_gate": (
                    video_localization_transcript_quality_gate.TranscriptQualityGateService().run(gate_input)
                )
            }
        )

    monkeypatch.setattr(
        pipeline,
        "run_transcript_review",
        run_transcript_review,
    )


def _project_root(project_id: str) -> Path:
    return media_assets.project_video_localization_dir(project_id)


def _save_server_video_localization(
    project_id: str,
    payload: dict,
) -> VideoLocalizationDraft:
    """Seed backend-owned draft state without going through the public PUT."""
    saved = video_localization_service.save_video_localization(
        project_id,
        VideoLocalizationDraft.model_validate(payload),
    )
    assert saved is not None
    return saved


def _portable_path(root: Path, value: str) -> Path:
    assert value.startswith("project://")
    relative = value.removeprefix("project://")
    return root if relative in {"", "."} else root / relative


def _completed_asr_result(draft, engine_id: str = "qwen3-asr-mlx"):
    transcript_quality_result = {
        "status": "success",
        "purpose": "确认当前整篇听写可以进入校时。",
        "summary": "当前整篇听写已通过质量门。",
        "metrics": [],
        "sections": [],
        "notes": [],
    }
    current_review_steps = {
        "research": {
            "status": "success",
            "summary": "当前资料查询步骤已完成。",
            "metrics": [],
            "sections": [],
            "notes": [],
        },
        "review_decisions_r1": {
            "status": "success",
            "summary": "当前复查结论步骤已完成。",
            "metrics": [],
            "sections": [],
            "notes": [],
        },
    }
    transcript = VideoLocalizationTranscriptionState(
        language="en",
        source_track_id="original",
        engine_id=engine_id,
        raw_text="Concurrent ASR result.",
        corrected_text="Concurrent ASR result.",
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1200,
                raw_text="Concurrent ASR result.",
                corrected_text="Concurrent ASR result.",
            )
        ],
        words=[
            VideoLocalizationAlignedWord(
                word_id="word_0001",
                segment_id="asr_0001",
                text="Concurrent",
                start_ms=0,
                end_ms=500,
            ),
            VideoLocalizationAlignedWord(
                word_id="word_0002",
                segment_id="asr_0001",
                text="ASR",
                start_ms=500,
                end_ms=850,
            ),
            VideoLocalizationAlignedWord(
                word_id="word_0003",
                segment_id="asr_0001",
                text="result.",
                start_ms=850,
                end_ms=1200,
            ),
        ],
        transcript_quality_cycle={
            "prompt_version": "asr-flow-v5",
            "task_step_results": current_review_steps,
            "step_result": transcript_quality_result,
        },
    )
    source_media = draft.source_media.model_copy(
        update={
            "metadata": {
                **draft.source_media.metadata,
                "english_asr_status": "completed",
                "english_asr_engine_id": engine_id,
                "english_asr_source_track_id": "original",
                "english_asr_segment_count": 1,
            }
        }
    )
    return draft.model_copy(update={"source_media": source_media, "transcription": transcript})


def test_video_localization_asr_operation_summary_distinguishes_raw_segments_from_cues():
    draft = _completed_asr_result(VideoLocalizationDraft())
    reviewed_segment = draft.transcription.segments[0].model_copy(
        update={
            "corrected_text": "Concurrent Seedance result.",
            "review_operations": [
                VideoLocalizationTranscriptEditOperation(
                    start_word_id="word_0002",
                    end_word_id="word_0002",
                    source_text="ASR",
                    replacement_text="Seedance",
                    reason="product name correction per video title",
                    confidence=0.96,
                    status="accepted",
                    evidence_source_ids=["source_01"],
                ),
                VideoLocalizationTranscriptEditOperation(
                    start_word_id="word_0003",
                    end_word_id="word_0003",
                    source_text="result",
                    replacement_text="output",
                    reason="Near-homophone correction based on glossary",
                    confidence=0.62,
                    status="rejected",
                    rejection_reason="llm_review_rejected:numbers_changed",
                ),
            ],
        }
    )
    research = VideoLocalizationResearchState(
        status="completed",
        provider="web-search",
        queries=[
            VideoLocalizationResearchQuery(
                query_id="query_01",
                query="Seedance official product name",
                category="proper_noun",
                reason="确认产品专名拼写",
                target_terms=["Seedance"],
            )
        ],
        sources=[
            VideoLocalizationResearchSource(
                source_id="source_01",
                query_id="query_01",
                title="Seedance 官方产品页",
                url="https://example.com/seedance",
                snippet="Seedance product documentation",
                provider="web-search",
            )
        ],
    )
    transcription = draft.transcription.model_copy(
        update={
            "segments": [reviewed_segment],
            "review_status": "completed",
            "review_profile_id": "subtitle-review",
            "review_model_id": "deepseek-chat",
            "research": research,
            "pipeline_timing": {
                "total_duration_ms": 4321,
                "stages": {
                    "boundary_review": {
                        "duration_ms": 1200,
                        "candidate_count": 3,
                        "batch_count": 2,
                        "round_count": 2,
                        "profile_id": "subtitle-review",
                        "model_id": "deepseek-chat",
                        "rounds": [
                            {
                                "round": 1,
                                "candidate_count": 2,
                                "batch_count": 1,
                                "duration_ms": 700,
                                "batches": [
                                    {
                                        "round": 1,
                                        "batch": 1,
                                        "candidate_count": 2,
                                        "duration_ms": 700,
                                        "status": "success",
                                        "attempt_count": 1,
                                    }
                                ],
                            },
                            {"round": 2, "candidate_count": 1, "batch_count": 1, "duration_ms": 500},
                        ],
                    }
                },
            },
        }
    )
    source_media = draft.source_media.model_copy(
        update={
            "metadata": {
                **draft.source_media.metadata,
                "english_asr_raw_segment_count": 1,
                "english_asr_segment_count": 3,
            }
        }
    )
    draft = draft.model_copy(
        update={
            "source_media": source_media,
            "transcription": transcription,
            "cues": [
                VideoLocalizationCue(
                    cue_id=f"cue_{index:04d}",
                    start_ms=(index - 1) * 500,
                    end_ms=index * 500,
                    en_subtitle_text=f"Subtitle {index}",
                    quality_flags=["generated_by_asr"],
                )
                for index in range(1, 4)
            ],
        }
    )

    summary = video_localization_operation_state.english_asr_summary(draft)

    assert summary["segment_count"] == 1
    assert summary["cue_count"] == 3
    assert summary["duration_ms"] == 4321
    assert summary["llm_profile_id"] == "subtitle-review"
    assert summary["llm_model_id"] == "deepseek-chat"
    assert summary["stage_timings"]["boundary_review"]["candidate_count"] == 3
    assert [item["candidate_count"] for item in summary["boundary_review_rounds"]] == [2, 1]
    assert summary["stage_timings"]["boundary_review"]["rounds"][0]["batches"][0]["status"] == "success"
    assert set(summary["task_step_results"]) == {
        "asr",
        "research",
        "review_decisions_r1",
        "transcript_quality_gate",
        "alignment",
        "audio_boundaries",
        "boundary_review",
        "subtitle_track",
    }
    assert summary["task_step_results"]["asr"]["status"] == "success"
    assert summary["task_step_results"]["transcript_quality_gate"]["status"] == "success"
    assert (
        summary["task_step_results"]["transcript_quality_gate"]["summary"]
        == "整篇校对完成，已自动继续校时，没有建议复听项。"
    )
    assert summary["task_step_results"]["asr"]["sections"][0]["items"][0]["text"] == "Concurrent ASR result."
    assert summary["task_step_results"]["research"]["summary"] == "当前资料查询步骤已完成。"
    assert summary["task_step_results"]["review_decisions_r1"]["summary"] == "当前复查结论步骤已完成。"
    assert summary["task_step_results"]["subtitle_track"]["status"] == "success"
    subtitle_items = summary["task_step_results"]["subtitle_track"]["sections"][0]["items"]
    assert [item["text"] for item in subtitle_items] == ["Subtitle 1", "Subtitle 2", "Subtitle 3"]
    assert summary["task_step_results"]["subtitle_track"]["coverage"] == {
        "mode": "complete",
        "shown_count": 3,
        "total_count": 3,
        "unit": "条最终字幕",
    }
    assert {item["label"]: item["value"] for item in summary["task_step_results"]["subtitle_track"]["metrics"]}[
        "字幕数量"
    ] == "3"


def test_video_localization_asr_step_results_show_all_human_readable_recognition_segments():
    draft = _completed_asr_result(VideoLocalizationDraft())
    base_segment = draft.transcription.segments[0]
    segments = [
        base_segment.model_copy(
            update={
                "segment_id": f"asr_{index:04d}",
                "start_ms": index * 1_000,
                "end_ms": index * 1_000 + 800,
                "raw_text": f"Sample {index}",
                "corrected_text": f"Sample {index}",
            }
        )
        for index in range(20)
    ]
    draft = draft.model_copy(update={"transcription": draft.transcription.model_copy(update={"segments": segments})})

    summary = video_localization_operation_state.english_asr_summary(draft)
    asr_result = summary["task_step_results"]["asr"]
    samples = asr_result["sections"][0]["items"]

    assert len(samples) == 20
    assert samples[0]["text"] == "Sample 0"
    assert samples[-1]["text"] == "Sample 19"
    assert asr_result["coverage"] == {
        "mode": "complete",
        "shown_count": 20,
        "total_count": 20,
        "unit": "个原始片段",
    }
    assert asr_result["purpose"].startswith("把音轨中的讲话转成原始文字")
    assert {item["label"]: item["value"] for item in asr_result["metrics"]}["语言"] == "英语"


def test_video_localization_asr_step_results_plain_language_and_upstream_warning():
    draft = _completed_asr_result(VideoLocalizationDraft())
    reviewed_segment = draft.transcription.segments[0].model_copy(
        update={
            "corrected_text": "Concurrent result.",
            "review_operations": [
                VideoLocalizationTranscriptEditOperation(
                    start_word_id="word_0002",
                    end_word_id="word_0002",
                    source_text="Cinebench",
                    replacement_text="Seedance",
                    reason="ASR misrecognition of proper noun; context and title indicate 'Seedance'",
                    confidence=0.93,
                    status="accepted",
                ),
                VideoLocalizationTranscriptEditOperation(
                    start_word_id="word_0002",
                    end_word_id="word_0002",
                    source_text="2.",
                    replacement_text="2.0",
                    reason="Version number segment incorrectly split; ASR split '2.0' into '2.' and '0'",
                    confidence=0.93,
                    status="accepted",
                ),
                VideoLocalizationTranscriptEditOperation(
                    start_word_id="word_0002",
                    end_word_id="word_0002",
                    source_text="ASR",
                    replacement_text="",
                    reason="Redundant '0' from version number; merged to previous segment",
                    confidence=0.93,
                    status="accepted",
                ),
            ],
        }
    )
    research = VideoLocalizationResearchState(
        status="completed",
        queries=[
            VideoLocalizationResearchQuery(
                query_id="query_01",
                query="Seedance official product name",
                category="proper_noun",
                reason="Verify correct product name and version.",
                target_terms=["Seedance 2.0"],
            )
        ],
    )
    transcription = draft.transcription.model_copy(
        update={
            "segments": [reviewed_segment],
            "review_status": "completed",
            "research": research,
            "boundary_review_status": "partial",
            "boundary_reviews": [
                VideoLocalizationBoundaryReview(
                    boundary_id="word_0001:word_0002",
                    left_word_id="word_0001",
                    right_word_id="word_0002",
                    decision="avoid",
                    confidence=0.9,
                    reason="protected:incomplete_syntax",
                ),
                VideoLocalizationBoundaryReview(
                    boundary_id="word_0002:word_0003",
                    left_word_id="word_0002",
                    right_word_id="word_0003",
                    decision="prefer",
                    confidence=0.9,
                    reason="clause_end",
                ),
            ],
        }
    )
    draft = draft.model_copy(
        update={
            "transcription": transcription,
            "cues": [
                VideoLocalizationCue(
                    cue_id="cue_0001",
                    start_ms=0,
                    end_ms=1_200,
                    en_subtitle_text="Concurrent result.",
                    quality_flags=["generated_by_asr"],
                )
            ],
        }
    )

    results = video_localization_operation_state.english_asr_summary(draft)["task_step_results"]

    assert "粗略定位" in results["asr"]["notes"][0]
    assert results["research"]["summary"] == "当前资料查询步骤已完成。"
    assert results["review_decisions_r1"]["summary"] == "当前复查结论步骤已完成。"
    boundary_items = results["boundary_review"]["sections"][0]["items"]
    assert "完整语法" in boundary_items[0]["text"]
    assert "完整的分句" in boundary_items[1]["text"]
    assert results["subtitle_track"]["status"] == "warning"
    assert results["subtitle_track"]["notes"] == []


def test_video_localization_asr_step_results_explain_reused_boundary_reviews():
    draft = _completed_asr_result(VideoLocalizationDraft())
    transcription = draft.transcription.model_copy(
        update={
            "boundary_review_status": "completed",
            "boundary_reviews": [
                VideoLocalizationBoundaryReview(
                    boundary_id="word_0001:word_0002",
                    left_word_id="word_0001",
                    right_word_id="word_0002",
                    decision="avoid",
                    confidence=0.9,
                    reason="incomplete_syntax",
                )
            ],
            "pipeline_timing": {
                "stages": {
                    "boundary_review": {
                        "candidate_count": 0,
                        "reused_review_count": 1,
                        "round_count": 0,
                        "batch_count": 0,
                    }
                }
            },
        }
    )
    draft = draft.model_copy(update={"transcription": transcription})

    result = video_localization_operation_state.english_asr_summary(draft)["task_step_results"]["boundary_review"]

    assert result["summary"] == "复用了 1 个已有断句判断，本轮不需要再次请求模型。"


def test_video_localization_asr_rerun_replaces_the_entire_source_subtitle_track():
    latest = _completed_asr_result(VideoLocalizationDraft()).model_copy(
        update={
            "cues": [
                VideoLocalizationCue(
                    cue_id="cue_0001",
                    start_ms=0,
                    end_ms=1200,
                    en_subtitle_text="Old ASR cue.",
                    quality_flags=["generated_by_asr"],
                ),
                VideoLocalizationCue(
                    cue_id="cue_0002",
                    start_ms=2000,
                    end_ms=2500,
                    en_subtitle_text="Manual cue.",
                    quality_flags=["protected_manual_edit"],
                ),
            ]
        }
    )
    result = _completed_asr_result(latest.model_copy(update={"cues": []}))

    merged = video_localization_source_pipeline.merge_english_asr_result(latest, result)

    assert [cue.cue_id for cue in merged.cues] == ["cue_0001"]
    assert merged.cues[0].en_subtitle_text == "Concurrent ASR result"


def test_video_localization_draft_round_trips_in_project_parameters(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化测试", "description": ""}).json()

    draft = {
        "project_type": "video_localization",
        "schema_version": "v1",
        "source_media": {"filename": "source.mp4", "duration_ms": 3400},
        "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
        "reference_clips": [
            {
                "reference_clip_id": "ref_001",
                "speaker_id": "speaker_01",
                "source_stem": "vocals_clean",
                "asr_text": "In 1992, this changed everything.",
                "cleanliness": "clean",
            }
        ],
        "cues": [
            {
                "cue_id": "cue_0001",
                "speaker_id": "speaker_01",
                "start_ms": 1200,
                "end_ms": 3400,
                "en_subtitle_text": "In 1992, this changed everything.",
                "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                "reference_clip_id": "ref_001",
                "review_status": "needs_review",
            }
        ],
        "ui_state": {
            "selected_cue_id": "cue_0001",
            "sidebar_collapsed": True,
            "subtitle_preview": {"enabled": True, "source": "localized", "stylePreset": "boxed"},
            "track_states": {"original": {"muted": True, "solo": False, "volume": 0.35}},
            "timeline_zoom": 2,
        },
        "generated_candidates": [{"candidate_id": "candidate_001", "recipe_id": "recipe_001", "status": "success"}],
        "timeline_clips": [{"clip_id": "clip_001", "cue_id": "cue_0001", "track_id": "dub", "start_ms": 1200}],
        "quality_gate": {"pending_issues": 1},
    }

    saved = client.put(f"/api/projects/{project['project_id']}/video-localization", json=draft)
    assert saved.status_code == 200
    assert saved.json()["updated_at"]
    assert saved.json()["cues"][0]["tts_recommended_text"].startswith("一九九二年")

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert fetched.status_code == 200
    assert fetched.json()["reference_clips"][0]["source_stem"] == "vocals_clean"
    assert fetched.json()["ui_state"]["selected_cue_id"] == "cue_0001"
    assert fetched.json()["ui_state"]["track_states"]["original"]["volume"] == 0.35
    assert fetched.json()["generated_candidates"][0]["candidate_id"] == "candidate_001"
    assert fetched.json()["timeline_clips"][0]["track_id"] == "dub"

    stored_project = client.get(f"/api/projects/{project['project_id']}").json()
    assert stored_project["parameters"]["video_localization"]["cues"][0]["cue_id"] == "cue_0001"
    assert stored_project["parameters"]["video_localization"]["ui_state"]["sidebar_collapsed"] is True


def test_video_localization_deduplicates_timeline_clip_ids_using_latest_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "时间线片段唯一性", "description": ""}).json()

    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip_localized_0001",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "audio_path": "/tmp/old.wav",
                },
                {
                    "clip_id": "clip_localized_0001",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_400,
                    "audio_path": "/tmp/latest.wav",
                },
            ],
        },
    )

    assert saved.status_code == 200
    assert saved.json()["timeline_clips"] == [
        {
            "clip_id": "clip_localized_0001",
            "generation_identity": "clip_localized_0001",
            "has_audio_source": False,
            "track_id": "dub",
            "start_ms": 1_000,
            "end_ms": 2_400,
        }
    ]
    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert fetched.status_code == 200
    assert fetched.json()["timeline_clips"] == saved.json()["timeline_clips"]


def test_video_localization_rejects_stale_full_draft_but_ui_patch_preserves_results(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "并发自动保存", "description": ""}).json()
    first = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000, "tts_recommended_text": "测试。"}],
            "generated_candidates": [{"candidate_id": "candidate_001", "recipe_id": "recipe_001", "status": "success"}],
            "ui_state": {"timeline_zoom": 1},
        },
    )
    stale = first.json()

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={"timeline_zoom": 4, "sidebar_collapsed": True},
    )
    assert patched.status_code == 200
    assert patched.json()["ui_state_patch"]["timeline_zoom"] == 4
    current = client.get(
        f"/api/projects/{project['project_id']}/video-localization/"
        "workspace-details/generated_candidates"
    ).json()
    assert current["generated_candidates"][0]["candidate_id"] == "candidate_001"

    stale["ui_state"]["timeline_zoom"] = 2
    conflict = client.put(f"/api/projects/{project['project_id']}/video-localization", json=stale)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VIDEO_LOCALIZATION_DRAFT_CONFLICT"


def test_video_localization_ui_patch_merges_dub_lane_controls_independently(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "分轨状态并发保存", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "ui_state": {
                "dub_lane_states": {
                    "0": {"label": "主配音", "muted": False, "solo": False, "locked": False, "volume": 1},
                    "1": {"label": "补充配音", "muted": False, "solo": False, "locked": False, "volume": 0.8},
                }
            },
        },
    )

    first = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={"dub_lane_states": {"0": {"label": "主旁白"}}},
    )
    second = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={"dub_lane_states": {"1": {"muted": True}}},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    states = second.json()["ui_state_patch"]["dub_lane_states"]
    assert states["0"]["label"] == "主旁白"
    assert states["0"]["volume"] == 1
    assert states["1"]["label"] == "补充配音"
    assert states["1"]["muted"] is True
    assert states["1"]["volume"] == 0.8


def test_video_localization_ui_patch_cannot_overwrite_backend_task_state(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "界面状态归属", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "ui_state": {"sidebar_collapsed": False},
        },
    )
    video_localization_service.update_video_localization_atomic(
        project["project_id"],
        lambda draft: draft.model_copy(
            update={
                "ui_state": {
                    **draft.ui_state,
                    "latest_tts_task_by_segment": {"localized_1": "task-current"},
                    "discarded_tts_task_ids": ["task-deleted"],
                }
            }
        ),
        intent="runtime",
    )

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={
            "latest_tts_task_by_segment": {"localized_1": "task-stale"},
            "discarded_tts_task_ids": [],
            "client_timeline_edit_intent": {"dub_lane_clip_ids": ["clip-stale"]},
            "sidebar_collapsed": True,
            "subtitle_display_mode": "dub",
        },
    )

    assert patched.status_code == 200
    assert patched.json()["ui_state_patch"]["sidebar_collapsed"] is True
    assert patched.json()["ui_state_patch"]["subtitle_display_mode"] == "dub"
    assert "latest_tts_task_by_segment" not in patched.json()["ui_state_patch"]
    assert "discarded_tts_task_ids" not in patched.json()["ui_state_patch"]
    assert "client_timeline_edit_intent" not in patched.json()["ui_state_patch"]
    current_ui = client.get(
        f"/api/projects/{project['project_id']}/video-localization/workspace"
    ).json()["draft"]["ui_state"]
    assert current_ui["latest_tts_task_by_segment"] == {"localized_1": "task-current"}
    assert current_ui["discarded_tts_task_ids"] == ["task-deleted"]


def test_video_localization_timeline_edit_patch_updates_only_requested_fields(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "轻量时间线保存", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-1",
                    "track_id": "dub",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "source_start_ms": 0,
                    "source_end_ms": 1000,
                    "audio_path": "/managed/audio.wav",
                    "task_id": "task-current",
                    "dub_lane": 0,
                }
            ],
            "ui_state": {"dub_lane_states": {"0": {"muted": False, "volume": 1}}},
            "scene_context": "must stay untouched",
        },
    )
    assert created.status_code == 200

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [
                {
                    "clip_id": "clip-1",
                    "expected_generation_identity": "task-current",
                    "expected_editable_fields": {
                        "start_ms": 1_000,
                        "end_ms": 2_000,
                        "source_start_ms": 0,
                        "source_end_ms": 1_000,
                        "media_source_clip_id": None,
                        "dub_lane": 0,
                    },
                    "start_ms": 1400,
                    "end_ms": 2400,
                    "source_start_ms": 200,
                    "source_end_ms": 1200,
                    "dub_lane": 1,
                }
            ],
            "dub_lane_state_patches": [{"lane": 1, "muted": True, "volume": 0.8}],
        },
    )

    assert patched.status_code == 200
    body = patched.json()
    assert body["schema_version"] == "timeline-edit-patch-v2"
    assert len(body["timeline_clips"]) == 1
    assert body["timeline_clips"][0]["start_ms"] == 1400
    assert body["timeline_clips"][0]["end_ms"] == 2400
    assert body["timeline_clips"][0]["task_id"] == "task-current"
    assert body["dub_lane_states"]["1"] == {"muted": True, "volume": 0.8}
    assert isinstance(body["revision"], str)

    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    assert stored.scene_context == "must stay untouched"
    assert stored.timeline_clips[0]["start_ms"] == 1400
    assert stored.timeline_clips[0]["task_id"] == "task-current"
    assert stored.timeline_edit_receipts == {}


def test_video_localization_ui_state_receipt_keeps_its_committed_revision(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "界面回执版本绑定", "description": ""},
    ).json()["project_id"]
    original_update = video_localization_service.update_video_localization_ui_state
    observed_revisions: dict[str, str] = {}

    def update_then_advance_project(active_project_id, patch):
        updated = original_update(active_project_id, patch)
        assert updated is not None
        observed_revisions["ui"] = str(updated._repository_revision)
        later = original_update(
            active_project_id,
            {"subtitle_display_mode": "source"},
        )
        assert later is not None
        observed_revisions["later"] = str(later._repository_revision)
        return updated

    monkeypatch.setattr(
        video_localization_api.video_localization_service,
        "update_video_localization_ui_state",
        update_then_advance_project,
    )
    patched = client.patch(
        f"/api/projects/{project_id}/video-localization/ui-state",
        json={"sidebar_collapsed": True},
    )

    assert patched.status_code == 200, patched.text
    assert observed_revisions["later"] != observed_revisions["ui"]
    assert patched.json()["revision"] == observed_revisions["ui"]
    assert patched.json()["ui_state_patch"] == {"sidebar_collapsed": True}


def test_video_localization_timeline_edit_receipt_keeps_its_committed_revision(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "时间线回执版本绑定", "description": ""},
    ).json()
    project_id = project["project_id"]
    created = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{
                "clip_id": "clip-1",
                "track_id": "dub",
                "start_ms": 1_000,
                "end_ms": 2_000,
            }],
        },
    )
    assert created.status_code == 200
    original_update = (
        video_localization_service.update_video_localization_timeline_edit
    )
    observed_revisions: dict[str, str] = {}

    def update_then_advance_project(*args, **kwargs):
        updated = original_update(*args, **kwargs)
        assert updated is not None
        assert updated._repository_revision is not None
        observed_revisions["timeline"] = str(updated._repository_revision)
        later = video_localization_service.update_video_localization_ui_state(
            project_id,
            {"sidebar_collapsed": True},
        )
        assert later is not None
        assert later._repository_revision is not None
        observed_revisions["later"] = str(later._repository_revision)
        return updated

    monkeypatch.setattr(
        video_localization_api.video_localization_service,
        "update_video_localization_timeline_edit",
        update_then_advance_project,
    )
    patched = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "clip-1",
                "expected_editable_fields": {
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "source_start_ms": None,
                    "source_end_ms": None,
                    "media_source_clip_id": None,
                    "dub_lane": None,
                },
                "start_ms": 1_200,
                "end_ms": 2_200,
            }],
        },
    )

    assert patched.status_code == 200, patched.text
    assert observed_revisions["later"] != observed_revisions["timeline"]
    assert patched.json()["revision"] == observed_revisions["timeline"]
    assert patched.json()["timeline_clips"][0]["start_ms"] == 1_200


def test_video_localization_timeline_edit_request_replays_original_mixed_receipt(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "时间线丢回执重放", "description": ""},
    ).json()["project_id"]
    _save_server_video_localization(
        project_id,
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "source",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 3_000,
                    "source_start_ms": 0,
                    "source_end_ms": 2_000,
                    "audio_path": "/tts/source.wav",
                    "task_id": "task-source",
                    "cqc_report": {"large": "private"},
                },
                {
                    "clip_id": "remove",
                    "track_id": "dub",
                    "start_ms": 4_000,
                    "end_ms": 5_000,
                    "audio_path": "/tts/remove.wav",
                    "task_id": "task-remove",
                },
            ],
        },
    )
    request = {
        "schema_version": "timeline-edit-patch-v2",
        "request_id": "lost-response-request",
        "clip_patches": [{
            "clip_id": "source",
            "expected_generation_identity": "task-source",
            "expected_editable_fields": {
                "start_ms": 1_000,
                "end_ms": 3_000,
                "source_start_ms": 0,
                "source_end_ms": 2_000,
                "media_source_clip_id": None,
                "dub_lane": None,
            },
            "end_ms": 2_000,
            "source_end_ms": 1_000,
        }],
        "added_clips": [{
            "clip_id": "source_part_2",
            "media_source_clip_id": "source",
            "start_ms": 2_000,
            "end_ms": 3_000,
            "source_start_ms": 1_000,
            "source_end_ms": 2_000,
            "dub_lane": 0,
        }],
        "deleted_clips": [{
            "clip_id": "remove",
            "expected_generation_identity": "task-remove",
            "expected_editable_fields": {
                "start_ms": 4_000,
                "end_ms": 5_000,
                "source_start_ms": None,
                "source_end_ms": None,
                "media_source_clip_id": None,
                "dub_lane": None,
            },
        }],
        "ui_state_patch": {"discarded_tts_task_ids": []},
    }

    first = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json=request,
    )
    assert first.status_code == 200, first.text
    later = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "request_id": "later-third-party-edit",
            "clip_patches": [{
                "clip_id": "source",
                "expected_generation_identity": "task-source",
                "expected_editable_fields": {
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "source_start_ms": 0,
                    "source_end_ms": 1_000,
                    "media_source_clip_id": None,
                    "dub_lane": None,
                },
                "start_ms": 1_200,
                "source_start_ms": 200,
            }],
            "deleted_clips": [{
                "clip_id": "source_part_2",
                "expected_generation_identity": "task-source",
                "expected_editable_fields": {
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "source_start_ms": 1_000,
                    "source_end_ms": 2_000,
                    "media_source_clip_id": "source",
                    "dub_lane": 0,
                },
            }],
            "ui_state_patch": {"discarded_tts_task_ids": []},
        },
    )
    assert later.status_code == 200, later.text
    assert later.json()["revision"] != first.json()["revision"]
    replay = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json=request,
    )

    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert all(
        "audio_path" not in clip
        and "cqc_report" not in clip
        and "timeline_edit_gate" not in clip
        for clip in replay.json()["timeline_clips"]
    )
    stored = video_localization_service.get_video_localization(project_id)
    assert stored is not None
    assert [clip["clip_id"] for clip in stored.timeline_clips] == ["source"]
    assert stored.timeline_clips[0]["start_ms"] == 1_200
    assert stored.timeline_clips[0]["source_start_ms"] == 200
    assert str(stored._repository_revision) == later.json()["revision"]
    assert list(stored.timeline_edit_receipts) == [
        "lost-response-request",
        "later-third-party-edit",
    ]

    conflicting = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json={
            **request,
            "clip_patches": [{
                **request["clip_patches"][0],
                "end_ms": 1_900,
            }],
        },
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_TIMELINE_REQUEST_CONFLICT"
    )


def test_pruned_timeline_edit_receipt_falls_back_to_original_conflict_fence(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        video_localization_timeline_edit_receipts,
        "MAX_RECEIPTS",
        1,
    )
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "时间线回执淘汰", "description": ""},
    ).json()["project_id"]
    client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{
                "clip_id": "clip-1",
                "track_id": "dub",
                "start_ms": 1_000,
                "end_ms": 2_000,
            }],
        },
    )

    def request(request_id: str, expected_start: int, next_start: int):
        return {
            "schema_version": "timeline-edit-patch-v2",
            "request_id": request_id,
            "clip_patches": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "clip-1",
                "expected_editable_fields": {
                    "start_ms": expected_start,
                    "end_ms": 2_000,
                    "source_start_ms": None,
                    "source_end_ms": None,
                    "media_source_clip_id": None,
                    "dub_lane": None,
                },
                "start_ms": next_start,
            }],
        }

    endpoint = f"/api/projects/{project_id}/video-localization/timeline-edit"
    first_request = request("receipt-one", 1_000, 1_100)
    assert client.patch(endpoint, json=first_request).status_code == 200
    assert client.patch(
        endpoint,
        json=request("receipt-two", 1_100, 1_200),
    ).status_code == 200
    stored = video_localization_service.get_video_localization(project_id)
    assert stored is not None
    assert list(stored.timeline_edit_receipts) == ["receipt-two"]

    pruned_replay = client.patch(endpoint, json=first_request)
    assert pruned_replay.status_code == 409
    assert pruned_replay.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_TIMELINE_CLIP_CHANGED"
    )
    assert video_localization_service.get_video_localization(
        project_id
    ).timeline_clips[0]["start_ms"] == 1_200


def test_timeline_edit_receipt_byte_budget_retains_the_latest_receipt(
    monkeypatch,
):
    monkeypatch.setattr(
        video_localization_timeline_edit_receipts,
        "MAX_RECEIPT_BYTES",
        500,
    )
    first = VideoLocalizationTimelineEditReceipt(
        request_fingerprint="a" * 64,
        repository_revision=1,
        timeline_clips=[{"clip_id": "first", "label": "x" * 300}],
    )
    latest = VideoLocalizationTimelineEditReceipt(
        request_fingerprint="b" * 64,
        repository_revision=2,
        timeline_clips=[{"clip_id": "latest", "label": "y" * 300}],
    )

    retained = video_localization_timeline_edit_receipts.retain_recent(
        {"first-request": first},
        "latest-request",
        latest,
    )

    assert retained == {"latest-request": latest}


def test_video_localization_timeline_edit_patch_applies_current_edit(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "轻量时间线冲突", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{"clip_id": "clip-1", "track_id": "dub", "start_ms": 1000, "end_ms": 2000}],
        },
    )
    assert created.status_code == 200

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "clip-1",
                "expected_editable_fields": {
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "source_start_ms": None,
                    "source_end_ms": None,
                    "media_source_clip_id": None,
                    "dub_lane": None,
                },
                "start_ms": 1400,
                "end_ms": 2400,
            }],
        },
    )

    assert patched.status_code == 200
    assert patched.json()["timeline_clips"][0]["start_ms"] == 1400


def test_video_localization_timeline_edit_rejects_stale_same_media_timing(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "拒绝旧裁切覆盖", "description": ""},
    ).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{
                "clip_id": "clip-1",
                "track_id": "dub",
                "start_ms": 1_000,
                "end_ms": 3_000,
                "source_start_ms": 0,
                "source_end_ms": 2_000,
                "media_source_clip_id": "source-1",
                "task_id": "task-same",
                "dub_lane": 0,
            }],
        },
    )
    assert created.status_code == 200
    expected = {
        "start_ms": 1_000,
        "end_ms": 3_000,
        "source_start_ms": 0,
        "source_end_ms": 2_000,
        "media_source_clip_id": "source-1",
        "dub_lane": 0,
    }
    endpoint = (
        f"/api/projects/{project['project_id']}"
        "/video-localization/timeline-edit"
    )

    unfenced = client.patch(
        endpoint,
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "task-same",
                "end_ms": 2_000,
            }],
        },
    )
    assert unfenced.status_code == 400

    first = client.patch(
        endpoint,
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "task-same",
                "expected_editable_fields": expected,
                "end_ms": 2_500,
                "source_end_ms": 1_500,
            }],
        },
    )
    assert first.status_code == 200

    stale_patch = client.patch(
        endpoint,
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "task-same",
                "expected_editable_fields": expected,
                "end_ms": 2_000,
                "source_end_ms": 1_000,
            }],
        },
    )
    stale_delete = client.patch(
        endpoint,
        json={
            "schema_version": "timeline-edit-patch-v2",
            "deleted_clips": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "task-same",
                "expected_editable_fields": expected,
            }],
        },
    )

    for response in (stale_patch, stale_delete):
        assert response.status_code == 409
        assert (
            response.json()["error"]["code"]
            == "VIDEO_LOCALIZATION_TIMELINE_CLIP_CHANGED"
        )
        assert response.json()["error"]["detail"]["editable"] is True
    stored = video_localization_service.get_video_localization(
        project["project_id"]
    )
    assert stored is not None
    assert stored.timeline_clips[0]["end_ms"] == 2_500
    assert stored.timeline_clips[0]["source_end_ms"] == 1_500


def test_video_localization_timeline_edit_patch_adds_split_without_quality_gate(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "轻量切分片段", "description": ""}).json()
    _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "source",
                    "track_id": "dub",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "source_start_ms": 0,
                    "source_end_ms": 2000,
                    "audio_path": "/tts/source.wav",
                    "task_id": "task-source",
                    "cqc_status": "needs_review",
                }
            ],
        },
    )

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [
                {
                    "clip_id": "source",
                    "expected_generation_identity": "task-source",
                    "expected_editable_fields": {
                        "start_ms": 1_000,
                        "end_ms": 3_000,
                        "source_start_ms": 0,
                        "source_end_ms": 2_000,
                        "media_source_clip_id": None,
                        "dub_lane": None,
                    },
                    "end_ms": 2000,
                    "source_end_ms": 1000,
                }
            ],
            "added_clips": [
                {
                    "clip_id": "source_part_2",
                    "media_source_clip_id": "source",
                    "start_ms": 2000,
                    "end_ms": 3000,
                    "source_start_ms": 1000,
                    "source_end_ms": 2000,
                    "dub_lane": 0,
                }
            ],
        },
    )

    assert patched.status_code == 200
    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    assert [clip["clip_id"] for clip in stored.timeline_clips] == ["source", "source_part_2"]
    assert stored.timeline_clips[1]["audio_path"] == "/tts/source.wav"
    assert stored.timeline_clips[1]["cqc_status"] == "needs_review"


def test_video_localization_timeline_edit_materializes_referenced_system_media_only(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "系统媒体轨首次剪辑", "description": ""},
    ).json()
    project_id = project["project_id"]
    package_root = media_assets.project_video_localization_dir(project_id)
    source_audio = package_root / "audio" / "source.wav"
    vocals = package_root / "stems" / "vocals.wav"
    background = package_root / "stems" / "background.wav"
    for path in (source_audio, vocals, background):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"managed-audio")
    _save_server_video_localization(
        project_id,
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "audio_path": str(source_audio),
                "duration_ms": 12_000,
                "frame_rate": 24,
            },
            "stems": {
                "original_audio_path": str(source_audio),
                "vocals_clean_path": str(vocals),
                "background_path": str(background),
                "separation_status": "completed",
            },
            "timeline_clips": [],
        },
    )

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    ).json()["draft"]
    projected_vocals = next(
        clip for clip in workspace["timeline_clips"]
        if clip["clip_id"] == "media_vocals"
    )
    patched = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [{
                "clip_id": "media_vocals",
                "expected_generation_identity": projected_vocals["generation_identity"],
                "expected_editable_fields": {
                    "start_ms": projected_vocals["start_ms"],
                    "end_ms": projected_vocals["end_ms"],
                    "source_start_ms": projected_vocals["source_start_ms"],
                    "source_end_ms": projected_vocals["source_end_ms"],
                    "media_source_clip_id": projected_vocals["media_source_clip_id"],
                    "dub_lane": projected_vocals.get("dub_lane"),
                },
                "end_ms": 6_000,
                "source_end_ms": 6_000,
            }],
            "added_clips": [{
                "clip_id": "media_vocals_part_2",
                "media_source_clip_id": "media_vocals",
                "start_ms": 6_000,
                "end_ms": 12_000,
                "source_start_ms": 6_000,
                "source_end_ms": 12_000,
                "dub_lane": 0,
            }],
        },
    )

    assert patched.status_code == 200, patched.text
    stored = video_localization_service.get_video_localization(project_id)
    assert stored is not None
    assert [clip["clip_id"] for clip in stored.timeline_clips] == [
        "media_vocals",
        "media_vocals_part_2",
    ]
    assert all(clip["audio_path"] == str(vocals) for clip in stored.timeline_clips)
    assert all(
        clip["clip_id"] not in {"media_original", "media_background"}
        for clip in stored.timeline_clips
    )


def test_video_localization_timeline_edit_can_delete_projected_system_media(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "删除系统背景轨", "description": ""},
    ).json()
    project_id = project["project_id"]
    package_root = media_assets.project_video_localization_dir(project_id)
    background = package_root / "stems" / "background.wav"
    background.parent.mkdir(parents=True, exist_ok=True)
    background.write_bytes(b"managed-background")
    _save_server_video_localization(
        project_id,
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"duration_ms": 12_000, "frame_rate": 24},
            "stems": {
                "background_path": str(background),
                "separation_status": "completed",
            },
            "timeline_clips": [],
        },
    )

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    ).json()["draft"]
    projected = next(
        clip for clip in workspace["timeline_clips"]
        if clip["clip_id"] == "media_background"
    )
    delete_fence = {
        "clip_id": "media_background",
        "expected_generation_identity": projected["generation_identity"],
        "expected_editable_fields": {
            "start_ms": projected["start_ms"],
            "end_ms": projected["end_ms"],
            "source_start_ms": projected["source_start_ms"],
            "source_end_ms": projected["source_end_ms"],
            "media_source_clip_id": projected["media_source_clip_id"],
            "dub_lane": projected.get("dub_lane"),
        },
    }
    unsafe_delete = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "deleted_clips": [delete_fence],
        },
    )

    assert unsafe_delete.status_code == 409
    assert unsafe_delete.json()["error"]["code"] == (
        "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_FOUND"
    )
    deleted = client.patch(
        f"/api/projects/{project_id}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "deleted_clips": [delete_fence],
            "ui_state_patch": {
                "disabled_media_tracks": ["background"],
                "discarded_tts_task_ids": [],
            },
        },
    )

    assert deleted.status_code == 200, deleted.text
    stored = video_localization_service.get_video_localization(project_id)
    assert stored is not None
    assert stored.timeline_clips == []
    assert stored.ui_state["disabled_media_tracks"] == ["background"]
    refreshed = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    ).json()["draft"]
    assert all(
        clip["clip_id"] != "media_background"
        for clip in refreshed["timeline_clips"]
    )


def test_video_localization_timeline_edit_patch_deletes_only_named_clips(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "轻量删除片段", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {"clip_id": "keep", "track_id": "dub", "start_ms": 0, "end_ms": 1000},
                {"clip_id": "remove", "track_id": "dub", "start_ms": 1000, "end_ms": 2000},
            ],
        },
    )
    assert created.status_code == 200

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "deleted_clips": [{
                "clip_id": "remove",
                "expected_generation_identity": "remove",
                "expected_editable_fields": {
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "source_start_ms": None,
                    "source_end_ms": None,
                    "media_source_clip_id": None,
                    "dub_lane": None,
                },
            }],
        },
    )

    assert patched.status_code == 200
    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    assert [clip["clip_id"] for clip in stored.timeline_clips] == ["keep"]


def test_video_localization_timeline_delete_commits_related_state_atomically(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "删除片段与状态同事务", "description": ""},
    ).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{
                "clip_id": "remove",
                "track_id": "dub",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "task_id": "task-remove",
            }],
        },
    )
    assert created.status_code == 200

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "deleted_clips": [{
                "clip_id": "remove",
                "expected_generation_identity": "task-remove",
                "expected_editable_fields": {
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "source_start_ms": None,
                    "source_end_ms": None,
                    "media_source_clip_id": None,
                    "dub_lane": None,
                },
            }],
            "ui_state_patch": {
                "disabled_media_tracks": ["original"],
                "discarded_tts_task_ids": ["task-remove"],
            },
        },
    )

    assert patched.status_code == 200
    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    assert stored.timeline_clips == []
    assert stored.ui_state["disabled_media_tracks"] == ["original"]
    assert stored.ui_state["discarded_tts_task_ids"] == ["task-remove"]


def test_video_localization_timeline_edit_rejects_stale_delete_after_replacement(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "替换后拒绝旧删除", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{
                "clip_id": "clip-1",
                "track_id": "dub",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "task_id": "task-old",
                "result_id": "result-old",
            }],
        },
    ).json()
    replaced = {
        **created,
        "timeline_clips": [{
            **created["timeline_clips"][0],
            "task_id": "task-new",
            "generation_id": "task-new",
            "result_id": "result-new",
        }],
    }
    _save_server_video_localization(project["project_id"], replaced)

    stale_delete = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "deleted_clips": [{
                "clip_id": "clip-1",
                "expected_generation_identity": "task-old",
                "expected_editable_fields": {
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "source_start_ms": None,
                    "source_end_ms": None,
                    "media_source_clip_id": None,
                    "dub_lane": None,
                },
            }],
        },
    )

    assert stale_delete.status_code == 409
    assert stale_delete.json()["error"]["code"] == "VIDEO_LOCALIZATION_TIMELINE_CLIP_CHANGED"
    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    assert stored.timeline_clips[0]["task_id"] == "task-new"


def test_video_localization_ui_patch_drops_legacy_automatic_mix_markers(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "显式混音状态", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "ui_state": {"track_states": {"original": {"muted": False, "solo": True, "volume": 1}}},
        },
    )

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={
            "automatic_dub_mix_configured": True,
            "initial_track_mix_configured": True,
            "track_states": {"original": {"muted": True}},
        },
    )

    assert patched.status_code == 200
    assert patched.json()["ui_state_patch"]["track_states"]["original"]["muted"] is True
    assert "automatic_dub_mix_configured" not in patched.json()["ui_state_patch"]
    assert "initial_track_mix_configured" not in patched.json()["ui_state_patch"]


def test_video_localization_full_save_drops_legacy_automatic_mix_markers():
    current = VideoLocalizationDraft(
        ui_state={
            "automatic_dub_mix_configured": True,
            "initial_track_mix_configured": True,
            "track_states": {"original": {"muted": True, "solo": False, "volume": 1}},
        },
    )
    incoming = VideoLocalizationDraft(
        ui_state={
            "automatic_dub_mix_configured": True,
            "initial_track_mix_configured": True,
            "track_states": {"original": {"muted": False, "solo": True, "volume": 0.8}},
        },
    )

    protected = video_localization_service._preserve_backend_owned_draft_state(current, incoming)

    assert protected.ui_state["track_states"]["original"]["muted"] is False
    assert "automatic_dub_mix_configured" not in protected.ui_state
    assert "initial_track_mix_configured" not in protected.ui_state


def test_video_localization_full_save_keeps_cleared_backend_task_mapping():
    current = VideoLocalizationDraft(ui_state={"latest_tts_task_by_segment": {}})
    incoming = VideoLocalizationDraft(
        ui_state={"latest_tts_task_by_segment": {"localized_1": "task-stale"}},
    )

    protected = video_localization_service._preserve_backend_owned_draft_state(current, incoming)

    assert protected.ui_state["latest_tts_task_by_segment"] == {}


def test_video_localization_rejects_full_draft_without_current_revision(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺失版本保护", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{"clip_id": "clip-1", "track_id": "dub", "dub_lane": 1}],
        },
    )
    assert created.status_code == 200

    missing_revision = created.json()
    missing_revision["updated_at"] = None
    missing_revision["timeline_clips"][0]["dub_lane"] = 0
    conflict = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=missing_revision,
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VIDEO_LOCALIZATION_DRAFT_CONFLICT"


def test_video_localization_only_accepts_dub_lane_change_with_explicit_intent(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "轨道归属保护", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{"clip_id": "clip-1", "track_id": "dub", "dub_lane": 1}],
        },
    ).json()

    stale_lane = dict(created)
    stale_lane["timeline_clips"] = [{**created["timeline_clips"][0], "dub_lane": 0}]
    protected = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=stale_lane,
    )
    assert protected.status_code == 200
    assert protected.json()["timeline_clips"][0]["dub_lane"] == 1

    intentional = protected.json()
    intentional["timeline_clips"][0]["dub_lane"] = 2
    intentional["ui_state"]["client_timeline_edit_intent"] = {"dub_lane_clip_ids": ["clip-1"]}
    moved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=intentional,
    )
    assert moved.status_code == 200
    assert moved.json()["timeline_clips"][0]["dub_lane"] == 2
    assert "client_timeline_edit_intent" not in moved.json()["ui_state"]


def test_video_localization_workspace_save_preserves_existing_clip_timeline_fields(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "时间线快照保护", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-1",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 3_000,
                    "source_start_ms": 100,
                    "source_end_ms": 2_100,
                    "media_source_clip_id": "source-1",
                }
            ],
        },
    ).json()

    stale_workspace = {
        **created,
        "timeline_clips": [
            {
                **created["timeline_clips"][0],
                "track_id": "original",
                "start_ms": 0,
                "end_ms": 4_000,
                "source_start_ms": 0,
                "source_end_ms": 4_000,
                "media_source_clip_id": "stale-source",
            }
        ],
    }
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization/workspace",
        json=stale_workspace,
    )

    assert saved.status_code == 200
    stored_clip = saved.json()["timeline_clips"][0]
    assert {
        field: stored_clip.get(field)
        for field in (
            "track_id",
            "start_ms",
            "end_ms",
            "source_start_ms",
            "source_end_ms",
            "media_source_clip_id",
        )
    } == {
        "track_id": "dub",
        "start_ms": 1_000,
        "end_ms": 3_000,
        "source_start_ms": 100,
        "source_end_ms": 2_100,
        "media_source_clip_id": "source-1",
    }


def test_video_localization_workspace_snapshot_cannot_expand_parent_after_typed_split(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "拆分后拒绝旧父范围", "description": ""}).json()
    created = _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{
                "clip_id": "clip-parent",
                "track_id": "dub",
                "start_ms": 1_000,
                "end_ms": 3_000,
                "source_start_ms": 0,
                "source_end_ms": 2_000,
                "task_id": "task-source",
                "audio_path": "/tts/source.wav",
            }],
        },
    )
    parent = created.timeline_clips[0]
    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/timeline-edit",
        json={
            "schema_version": "timeline-edit-patch-v2",
            "clip_patches": [{
                "clip_id": "clip-parent",
                "expected_generation_identity": "task-source",
                "expected_editable_fields": {
                    "start_ms": parent.get("start_ms"),
                    "end_ms": parent.get("end_ms"),
                    "source_start_ms": parent.get("source_start_ms"),
                    "source_end_ms": parent.get("source_end_ms"),
                    "media_source_clip_id": parent.get("media_source_clip_id"),
                    "dub_lane": parent.get("dub_lane"),
                },
                "end_ms": 2_000,
                "source_end_ms": 1_000,
            }],
            "added_clips": [{
                "clip_id": "clip-parent_part_2",
                "media_source_clip_id": "clip-parent",
                "start_ms": 2_000,
                "end_ms": 3_000,
                "source_start_ms": 1_000,
                "source_end_ms": 2_000,
                "dub_lane": 0,
            }],
        },
    )
    assert patched.status_code == 200, patched.json()

    workspace = client.get(
        f"/api/projects/{project['project_id']}/video-localization/workspace"
    ).json()["draft"]
    stale_snapshot = {
        **workspace,
        "timeline_clips": [
            {
                **clip,
                **(
                    {"end_ms": 3_000, "source_end_ms": 2_000}
                    if clip["clip_id"] == "clip-parent"
                    else {}
                ),
            }
            for clip in workspace["timeline_clips"]
        ],
    }
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization/workspace",
        json=stale_snapshot,
    )

    assert saved.status_code == 200
    clips = {clip["clip_id"]: clip for clip in saved.json()["timeline_clips"]}
    assert clips["clip-parent"]["end_ms"] == 2_000
    assert clips["clip-parent"]["source_end_ms"] == 1_000
    assert clips["clip-parent_part_2"]["start_ms"] == 2_000


def test_video_localization_only_deletes_timeline_clip_with_explicit_intent(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "片段删除归属保护", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-durable",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "result_id": "result-durable",
                    "status": "ready",
                }
            ],
        },
    ).json()

    omitted = {**created, "timeline_clips": []}
    protected = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=omitted,
    )
    assert protected.status_code == 200
    assert [clip["clip_id"] for clip in protected.json()["timeline_clips"]] == ["clip-durable"]

    intentional = {**protected.json(), "timeline_clips": []}
    intentional["ui_state"] = {
        **intentional["ui_state"],
        "client_timeline_edit_intent": {
            "deleted_timeline_clips": [{
                "clip_id": "clip-durable",
                "expected_generation_identity": "result-durable",
            }],
        },
    }
    deleted = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=intentional,
    )

    assert deleted.status_code == 200
    assert deleted.json()["timeline_clips"] == []
    assert "client_timeline_edit_intent" not in deleted.json()["ui_state"]


def test_video_localization_later_stale_save_cannot_restore_explicitly_deleted_split_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "拆分片段删除后不复活", "description": ""},
    ).json()
    parent = {
        "clip_id": "clip-parent",
        "track_id": "dub",
        "start_ms": 1_000,
        "end_ms": 1_500,
        "source_start_ms": 0,
        "source_end_ms": 500,
        "task_id": "task-shared",
        "generation_id": "task-shared",
        "result_id": "result-shared",
        "status": "ready",
    }
    child = {
        **parent,
        "clip_id": "clip-parent-part-2",
        "start_ms": 1_500,
        "end_ms": 2_000,
        "source_start_ms": 500,
        "source_end_ms": 1_000,
        "media_source_clip_id": "clip-parent",
    }
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [parent, child],
        },
    ).json()

    deleted_payload = {**created, "timeline_clips": [created["timeline_clips"][0]]}
    deleted_payload["ui_state"] = {
        **deleted_payload["ui_state"],
        "client_timeline_edit_intent": {
            "deleted_timeline_clips": [{
                "clip_id": "clip-parent-part-2",
                "expected_generation_identity": "task-shared",
            }],
        },
    }
    deleted = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=deleted_payload,
    ).json()
    assert [item["clip_id"] for item in deleted["timeline_clips"]] == ["clip-parent"]

    stale = {**created, "updated_at": deleted["updated_at"]}
    stale["ui_state"] = {key: value for key, value in stale["ui_state"].items() if key != "client_timeline_edit_intent"}
    protected = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=stale,
    )

    assert protected.status_code == 200
    assert [item["clip_id"] for item in protected.json()["timeline_clips"]] == ["clip-parent"]


def test_video_localization_rejects_forged_explicitly_added_dub_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "拒绝伪造新增配音片段", "description": ""},
    ).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-durable",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "task_id": "task-durable",
                    "generation_id": "task-durable",
                    "result_id": "result-durable",
                    "status": "ready",
                }
            ],
        },
    ).json()
    forged = {
        "clip_id": "clip-forged",
        "track_id": "dub",
        "start_ms": 2_000,
        "end_ms": 3_000,
        "task_id": "task-forged",
        "generation_id": "task-forged",
        "result_id": "result-forged",
        "status": "ready",
    }
    payload = {**created, "timeline_clips": [*created["timeline_clips"], forged]}
    payload["ui_state"] = {
        **payload["ui_state"],
        "client_timeline_edit_intent": {
            "added_timeline_clip_ids": ["clip-forged"],
        },
    }

    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=payload,
    )

    assert saved.status_code == 200
    assert [item["clip_id"] for item in saved.json()["timeline_clips"]] == ["clip-durable"]


def test_video_localization_full_save_cannot_restore_stale_generated_clip_metadata(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "配音并发保护", "description": ""}).json()
    current_audio = _project_root(project["project_id"]) / "tts" / "new.wav"
    current_audio.parent.mkdir(parents=True, exist_ok=True)
    current_audio.write_bytes(b"managed-audio")
    created = _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip_localized_0004",
                    "track_id": "dub",
                    "start_ms": 26240,
                    "end_ms": 28329,
                    "source_start_ms": 0,
                    "source_end_ms": 2089,
                    "audio_path": str(current_audio),
                    "status": "ready",
                    "generation_id": "generation-new",
                    "task_id": "generation-new",
                    "result_id": "result-new",
                    "dub_lane": 1,
                    "media_source_clip_id": "clip_localized_0004",
                }
            ],
        },
    )

    stale = created.model_dump(mode="json")
    stale["timeline_clips"][0].update(
        {
            "start_ms": 29208,
            "end_ms": 31125,
            "source_end_ms": 1917,
            "audio_path": "/tts/old.wav",
            "generation_id": "generation-old",
            "task_id": "generation-old",
            "result_id": "result-old",
            "speech_onset_ms": 289,
        }
    )
    protected = client.put(f"/api/projects/{project['project_id']}/video-localization", json=stale)
    assert protected.status_code == 200
    clip = protected.json()["timeline_clips"][0]
    assert clip["generation_id"] == "generation-new"
    assert clip["audio_path"] == str(current_audio)
    assert clip["result_id"] == "result-new"
    assert clip["start_ms"] == 26240
    assert "speech_onset_ms" not in clip

    moved = protected.json()
    moved["timeline_clips"][0].update({"start_ms": 29208, "end_ms": 31297, "speech_onset_ms": 289})
    moved["timeline_clips"][0].pop("media_source_clip_id")
    saved_move = client.put(f"/api/projects/{project['project_id']}/video-localization", json=moved)
    assert saved_move.status_code == 200
    clip = saved_move.json()["timeline_clips"][0]
    assert (clip["start_ms"], clip["end_ms"]) == (29208, 31297)
    assert clip["generation_id"] == "generation-new"
    assert clip["result_id"] == "result-new"
    assert "speech_onset_ms" not in clip
    assert "media_source_clip_id" not in clip


def test_video_localization_full_save_cannot_restore_a_discarded_tts_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "删除配音并发保护", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip_localized_0004",
                    "track_id": "dub",
                    "start_ms": 26240,
                    "end_ms": 28167,
                    "task_id": "task-deleted",
                    "generation_id": "task-deleted",
                    "audio_path": "/tts/deleted.wav",
                    "status": "ready",
                }
            ],
        },
    ).json()
    deleted = dict(created)
    deleted["timeline_clips"] = []
    deleted["ui_state"] = {
        "discarded_tts_task_ids": ["task-deleted"],
        "client_timeline_edit_intent": {
            "deleted_timeline_clips": [{
                "clip_id": "clip_localized_0004",
                "expected_generation_identity": "task-deleted",
            }],
        },
    }
    deleted = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=deleted,
    ).json()

    stale_rebased = dict(created)
    stale_rebased["updated_at"] = deleted["updated_at"]
    stale_rebased["ui_state"] = {}
    protected = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=stale_rebased,
    )

    assert protected.status_code == 200
    assert protected.json()["timeline_clips"] == []
    assert protected.json()["ui_state"]["discarded_tts_task_ids"] == ["task-deleted"]


def test_video_localization_delete_prepared_tts_workflow_cannot_restore_optimistic_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "删除待提交配音", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-localized-0001-pending",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "queued",
                    "status_label": "等待提交",
                    "optimistic_tts_workflow_id": "workflow-prepared",
                }
            ],
            "tts_tasks": [
                {
                    "workflow_id": "workflow-prepared",
                    "project_id": project["project_id"],
                    "segment_id": "localized_0001",
                    "subtitle_summary": "待提交台词",
                    "text": "待提交台词",
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "prepared",
                }
            ],
        },
    ).json()

    deleted_response = client.delete(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks/workflow-prepared"
    )

    assert deleted_response.status_code == 200
    deleted = deleted_response.json()
    assert deleted["tts_tasks"] == []
    assert deleted["timeline_clips"] == []
    assert "workflow-prepared" in deleted["ui_state"]["discarded_tts_task_ids"]

    stale_client = dict(created)
    stale_client["updated_at"] = deleted["updated_at"]
    stale_client["ui_state"] = {}
    protected = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=stale_client,
    )

    assert protected.status_code == 200
    assert protected.json()["timeline_clips"] == []
    assert "workflow-prepared" in protected.json()["ui_state"]["discarded_tts_task_ids"]


def test_video_localization_stop_then_delete_running_tts_workflow_cancels_generation(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "停止并删除生成中配音", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-localized-0001-running",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "task_id": "task-running",
                    "generation_id": "task-running",
                    "status": "running",
                    "optimistic_tts_workflow_id": "workflow-running",
                }
            ],
            "generated_candidates": [
                {
                    "candidate_id": "candidate-running",
                    "cue_id": "cue_0001",
                    "task_id": "task-running",
                    "status": "running",
                }
            ],
            "tts_tasks": [
                {
                    "workflow_id": "workflow-running",
                    "project_id": project["project_id"],
                    "segment_id": "localized_0001",
                    "subtitle_summary": "生成中台词",
                    "text": "生成中台词",
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "running",
                    "generation_task_id": "task-running",
                    "stages": [
                        {"kind": "generation", "status": "running", "progress": 0.4},
                        {"kind": "placement", "status": "pending", "progress": 0.0},
                    ],
                }
            ],
        },
    )
    cancelled_task_ids: list[str] = []
    monkeypatch.setattr(
        video_localization_service.task_queue,
        "cancel_task",
        lambda task_id: cancelled_task_ids.append(task_id) or {"task_id": task_id, "status": "cancelled"},
    )

    stopped = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/tasks/workflow-running/cancel")

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
    assert [stage["status"] for stage in stopped.json()["stages"]] == ["cancelled", "cancelled"]
    assert cancelled_task_ids == ["task-running"]

    deleted = client.delete(f"/api/projects/{project['project_id']}/video-localization/tts/tasks/workflow-running")

    assert deleted.status_code == 200
    payload = deleted.json()
    assert payload["tts_tasks"] == []
    assert payload["timeline_clips"] == []
    assert payload["generated_candidates"] == []
    assert {"workflow-running", "task-running"}.issubset(payload["ui_state"]["discarded_tts_task_ids"])
    assert cancelled_task_ids == ["task-running"]


def test_video_localization_delete_wins_when_generation_finishes_during_delete(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "删除竞态完成配音", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-localized-0001-raced",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "task_id": "task-raced",
                    "generation_id": "task-raced",
                    "audio_path": "/tts/raced.wav",
                    "status": "ready",
                    "optimistic_tts_workflow_id": "workflow-raced",
                }
            ],
            "tts_tasks": [
                {
                    "workflow_id": "workflow-raced",
                    "project_id": project["project_id"],
                    "segment_id": "localized_0001",
                    "subtitle_summary": "刚完成的台词",
                    "text": "刚完成的台词",
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "success",
                    "generation_task_id": "task-raced",
                    "timeline_clip_id": "clip-localized-0001-raced",
                    "stages": [
                        {"kind": "generation", "status": "success", "progress": 1.0},
                        {"kind": "placement", "status": "success", "progress": 1.0},
                    ],
                }
            ],
        },
    )

    deleted = client.delete(f"/api/projects/{project['project_id']}/video-localization/tts/tasks/workflow-raced")

    assert deleted.status_code == 200
    payload = deleted.json()
    assert payload["tts_tasks"] == []
    assert payload["timeline_clips"] == []
    assert {"workflow-raced", "task-raced"}.issubset(payload["ui_state"]["discarded_tts_task_ids"])


def test_video_localization_delete_replacement_workflow_removes_explicit_target_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "删除替换中的已有配音", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-existing-target",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "audio_path": "/tts/existing.wav",
                    "status": "ready",
                }
            ],
            "tts_tasks": [
                {
                    "workflow_id": "workflow-replacing",
                    "project_id": project["project_id"],
                    "segment_id": "localized_0001",
                    "subtitle_summary": "替换已有配音",
                    "text": "替换已有配音",
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "prepared",
                    "timeline_clip_id": "clip-existing-target",
                }
            ],
        },
    )

    deleted = client.delete(f"/api/projects/{project['project_id']}/video-localization/tts/tasks/workflow-replacing")

    assert deleted.status_code == 200
    payload = deleted.json()
    assert payload["tts_tasks"] == []
    assert payload["timeline_clips"] == []


def test_video_localization_workspace_rejects_full_snapshot_split_that_changes_parent(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "拆分片段保留", "description": ""}).json()
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [
                {
                    "clip_id": "clip-a_part_2",
                    "track_id": "dub",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "source_start_ms": 1000,
                    "source_end_ms": 3000,
                    "media_source_clip_id": "clip-a",
                    "task_id": "task-shared",
                    "generation_id": "task-shared",
                    "audio_path": "/tts/shared.wav",
                    "status": "ready",
                }
            ],
        },
    ).json()

    with_tombstone = dict(created)
    with_tombstone["ui_state"] = {"discarded_tts_task_ids": ["task-shared"]}
    created = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=with_tombstone,
    ).json()

    split = dict(created)
    split["ui_state"] = {
        **created["ui_state"],
        "client_timeline_edit_intent": {
            "added_timeline_clip_ids": ["clip-a_part_2_part_2"],
        },
    }
    split["timeline_clips"] = [
        {**created["timeline_clips"][0], "end_ms": 2000, "source_end_ms": 2000},
        {
            **created["timeline_clips"][0],
            "clip_id": "clip-a_part_2_part_2",
            "start_ms": 2000,
            "source_start_ms": 2000,
        },
    ]
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization/workspace",
        json=split,
    )

    assert saved.status_code == 409
    assert saved.json()["error"]["code"] == "VIDEO_LOCALIZATION_TIMELINE_EDIT_REQUIRES_PATCH"
    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    assert [clip["clip_id"] for clip in stored.timeline_clips] == ["clip-a_part_2"]
    assert stored.timeline_clips[0]["end_ms"] == 3_000
    assert stored.timeline_clips[0]["source_end_ms"] == 3_000


def test_video_localization_save_writes_project_manifest_and_autosave(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "可恢复项目", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 3400},
            "ui_state": {"selected_cue_id": "cue_0001", "timeline_zoom": 1.6},
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1200, "en_subtitle_text": "Hello"}],
        },
    )

    assert response.status_code == 200
    root = _project_root(project["project_id"])
    manifest_path = root / "project.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["kind"] == "video_localization_project"
    assert manifest["project_id"] == project["project_id"]
    assert manifest["project_name"] == "可恢复项目"
    assert manifest["storage"]["primary_state"] == "voice_studio_db.projects.parameters.video_localization"
    assert manifest["storage"]["root"] == "project://."
    assert manifest["storage"]["portability"] == {"status": "portable", "external_paths": []}
    assert manifest["draft"]["ui_state"]["timeline_zoom"] == 1.6
    assert manifest["draft"]["cues"][0]["cue_id"] == "cue_0001"
    assert set(manifest["storage"]["directories"]) >= {
        "source",
        "audio",
        "stems",
        "references",
        "tts",
        "exports",
        "autosave",
    }
    for path in manifest["storage"]["directories"].values():
        assert _portable_path(root, path).exists()
    autosaves = sorted((root / "autosave").glob("*-project.json"))
    assert len(autosaves) == 1
    assert json.loads(autosaves[0].read_text(encoding="utf-8"))["draft"]["source_media"]["filename"] == "source.mp4"
    assert (root / "autosave" / ".project-paths-v1-normalized").exists()


def test_snapshot_write_failure_keeps_committed_draft_successful_and_pending(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "快照写入失败", "description": ""},
    ).json()

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("injected snapshot write failure")

    monkeypatch.setattr(
        project_manifest,
        "write_project_snapshot",
        fail_snapshot,
    )
    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "数据库已经提交"},
    )

    assert response.status_code == 200
    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    assert stored.parameters["video_localization"]["scene_context"] == "数据库已经提交"
    with database.conn() as connection:
        pending = connection.execute(
            """
            SELECT
                target_repository_revision,
                last_error
            FROM video_localization_project_snapshot_projection
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()
    assert pending is not None
    assert pending["target_repository_revision"] == (stored._repository_revision)
    assert "injected snapshot write failure" in pending["last_error"]


def test_runtime_write_commits_without_blocking_on_full_project_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "运行时投影", "description": ""},
    ).json()
    initialized = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "初始内容"},
    )
    assert initialized.status_code == 200

    def unexpected_snapshot(*_args, **_kwargs):
        raise AssertionError("runtime save must not rewrite project.json inline")

    monkeypatch.setattr(
        project_manifest,
        "write_project_snapshot",
        unexpected_snapshot,
    )
    current = draft_store.get(project["project_id"])
    assert current is not None
    saved = draft_store.save(
        project["project_id"],
        current.model_copy(update={"scene_context": "运行时已提交"}),
        intent="runtime",
    )

    assert saved is not None
    assert draft_store.get(project["project_id"]).scene_context == "运行时已提交"
    with database.conn() as connection:
        pending = connection.execute(
            """
            SELECT target_repository_revision
            FROM video_localization_project_snapshot_projection
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()
    assert pending is not None

    monkeypatch.undo()
    assert project_snapshot_projection.replay_pending(limit=10) == 1
    manifest = json.loads(
        (_project_root(project["project_id"]) / "project.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["draft"]["scene_context"] == "运行时已提交"


def test_pending_snapshot_replay_coalesces_to_latest_project_revision(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "快照重放", "description": ""},
    ).json()
    original_writer = project_manifest.write_project_snapshot

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("snapshot storage unavailable")

    monkeypatch.setattr(
        project_manifest,
        "write_project_snapshot",
        fail_snapshot,
    )
    first = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "第一版"},
    )
    assert first.status_code == 200
    second_payload = first.json()
    second_payload["scene_context"] = "第二版"
    second = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=second_payload,
    )
    assert second.status_code == 200
    assert not (_project_root(project["project_id"]) / "project.json").exists()

    monkeypatch.setattr(
        project_manifest,
        "write_project_snapshot",
        original_writer,
    )
    from app.domains.video_localization import project_snapshot_projection

    replayed = project_snapshot_projection.replay_pending(limit=10)

    assert replayed == 1
    manifest = json.loads((_project_root(project["project_id"]) / "project.json").read_text(encoding="utf-8"))
    assert manifest["draft"]["scene_context"] == "第二版"
    autosaves = list((_project_root(project["project_id"]) / "autosave").glob("*-project.json"))
    assert len(autosaves) == 1
    with database.conn() as connection:
        pending = connection.execute(
            """
            SELECT 1
            FROM video_localization_project_snapshot_projection
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()
    assert pending is None


def test_video_localization_explicitly_recovers_from_project_snapshot_when_database_draft_is_missing(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "快照恢复", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "recover.mp4", "duration_ms": 3200},
            "cues": [{"cue_id": "cue_recovered", "start_ms": 0, "end_ms": 1200, "en_subtitle_text": "Recovered."}],
        },
    )
    assert saved.status_code == 200

    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.parameters = {key: value for key, value in stored.parameters.items() if key != "video_localization"}
    project_store.save_project(stored)

    opened = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert opened.status_code == 409
    assert opened.json()["error"]["code"] == ("VIDEO_LOCALIZATION_DRAFT_REPAIR_REQUIRED")

    recovered = client.post(f"/api/projects/{project['project_id']}/video-localization/repair-storage")
    assert recovered.status_code == 200
    assert recovered.json()["source_media"]["filename"] == "recover.mp4"
    assert recovered.json()["cues"][0]["cue_id"] == "cue_recovered"


def test_video_localization_sync_recovers_local_package_and_is_idempotent(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本地恢复项目", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {"filename": "recover.mp4", "duration_ms": 1200},
            "operations": [
                {
                    "operation_id": "operation_pending",
                    "project_id": project["project_id"],
                    "kind": "english_asr",
                    "status": "running",
                }
            ],
        },
    )
    assert saved.status_code == 200
    root = _project_root(project["project_id"])
    project_store.delete_project(project["project_id"])

    first = client.post("/api/projects/video-localization/sync-projects")
    second = client.post("/api/projects/video-localization/sync-projects")

    assert first.status_code == 200
    assert [item["project_id"] for item in first.json()] == [project["project_id"]]
    assert [item["project_id"] for item in second.json()] == [project["project_id"]]
    recovered = project_store.get_project(project["project_id"])
    assert recovered is not None
    assert recovered.parameters[media_assets.PROJECT_DIR_NAME_KEY] == root.name
    operation = recovered.parameters["video_localization"]["operations"][0]
    assert operation["status"] == "failed"
    assert operation["error_code"] == "PROJECT_INDEX_RECOVERED"


def test_delete_project_api_removes_local_package_so_sync_cannot_restore_it(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "待删除本土化项目", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "delete-me.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200
    root = _project_root(project["project_id"])
    assert root.exists()
    history_output = tmp_path / "outputs" / "project-result.wav"
    history_output.parent.mkdir(parents=True, exist_ok=True)
    history_output.write_bytes(b"generated audio")
    history_store.add(
        HistoryItem(
            result_id="project-result",
            task_id="project-task",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            input_text="项目配音",
            output_path=str(history_output),
            parameter_snapshot={"source": "video_localization"},
        )
    )
    managed_reference = custom_reference_store.allocate_path("vl_source_project_delete", ".wav")
    managed_reference.write_bytes(b"managed project reference")
    database.upsert(
        "voice_files",
        "vl_source_project_delete",
        VoiceFile(
            file_id="vl_source_project_delete",
            original_name="project-reference.wav",
            path=str(managed_reference),
            size_bytes=managed_reference.stat().st_size,
        ).model_dump(),
    )
    shared_reference = custom_reference_store.allocate_path("vl_source_shared_delete_test", ".wav")
    shared_reference.write_bytes(b"shared managed reference")
    database.upsert(
        "voice_files",
        "vl_source_shared_delete_test",
        VoiceFile(
            file_id="vl_source_shared_delete_test",
            original_name="shared-reference.wav",
            path=str(shared_reference),
            size_bytes=shared_reference.stat().st_size,
        ).model_dump(),
    )
    other_project = client.post("/api/projects", json={"name": "保留项目", "description": ""}).json()
    other_task = GenerationTask(
        task_id="other-project-task",
        engine_id="indextts-v2",
        project_id=other_project["project_id"],
        input_text="其他项目任务",
        status=TaskStatus.success,
        parameters={"custom_reference_source_audio_path": str(shared_reference)},
    )
    task_queue._save(other_task)
    project_task = GenerationTask(
        task_id="project-task-record",
        engine_id="indextts-v2",
        project_id=project["project_id"],
        input_text="已完成项目配音任务",
        status=TaskStatus.success,
        parameters={
            "custom_reference_source_audio_path": str(managed_reference),
            "reference_audio_path": str(shared_reference),
        },
    )
    task_queue._save(project_task)
    handoff_payload = TtsTaskRegistrationEventV1(
        project_id=project["project_id"],
        source_kind="task",
        source_id=project_task.task_id,
        segment_id="localized-delete",
        generation_task_id=project_task.task_id,
        workflow_id="workflow-delete",
    )
    handoff_event_id = video_localization_tts_handoff_store.registration_event_id(
        source_kind=handoff_payload.source_kind,
        source_id=handoff_payload.source_id,
    )
    with database.conn() as connection:
        video_localization_tts_handoff_store.enqueue(
            connection,
            event_id=handoff_event_id,
            payload=handoff_payload,
            created_at=project_task.created_at,
        )
    project_batch = BatchTask(
        batch_task_id="project-batch-record",
        project_name="项目批量配音",
        status=TaskStatus.success,
        parameters={"source": "video_localization", "project_id": project["project_id"]},
    )
    database.upsert("batches", project_batch.batch_task_id, project_batch.model_dump())
    preview_cache_root = tmp_path / "cache" / "video-preview" / project["project_id"] / "source-signature"
    preview_cache_root.mkdir(parents=True)
    (preview_cache_root / "metadata.json").write_text("{}", encoding="utf-8")
    waveform_cache_root = tmp_path / "cache" / "waveforms"
    waveform_cache_root.mkdir(parents=True, exist_ok=True)
    project_waveform = waveform_cache_root / f"video-localization-{project['project_id']}-clip_1-v2-1-1-320.json"
    project_waveform.write_text("{}", encoding="utf-8")
    unrelated_waveform = waveform_cache_root / "unrelated-v2-1-1-320.json"
    unrelated_waveform.write_text("{}", encoding="utf-8")
    scheduled_job_ids: list[str] = []
    monkeypatch.setattr(
        project_lifecycle_cleanup,
        "schedule",
        scheduled_job_ids.append,
    )

    deleted = client.delete(f"/api/projects/{project['project_id']}")
    assert len(scheduled_job_ids) == 1
    assert project_lifecycle_cleanup.flush(scheduled_job_ids[0])
    synced = client.post("/api/projects/video-localization/sync-projects")

    assert deleted.status_code == 200
    assert not root.exists()
    assert project_store.get_project(project["project_id"]) is None
    assert project["project_id"] not in {item["project_id"] for item in synced.json()}
    assert history_store.get("project-result") is None
    assert task_queue.get_task(project_task.task_id) is None
    assert video_localization_tts_handoff_store.get(handoff_event_id) is None
    assert batch_queue.get_batch(project_batch.batch_task_id) is None
    assert not history_output.exists()
    assert not managed_reference.exists()
    assert database.get_one("voice_files", "file_id", "vl_source_project_delete") is None
    assert shared_reference.exists()
    assert database.get_one("voice_files", "file_id", "vl_source_shared_delete_test") is not None
    assert project_store.get_project(other_project["project_id"]) is not None
    assert task_queue.get_task(other_task.task_id) is not None
    assert not (tmp_path / "cache" / "video-preview" / project["project_id"]).exists()
    assert not project_waveform.exists()
    assert unrelated_waveform.exists()


def test_delete_project_api_returns_after_commit_and_schedules_cleanup(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "后台清理项目", "description": ""},
    ).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {
                "filename": "cleanup-later.mp4",
                "duration_ms": 1200,
            }
        },
    )
    assert saved.status_code == 200
    root = _project_root(project["project_id"])
    assert root.is_dir()
    scheduled_job_ids: list[str] = []

    monkeypatch.setattr(
        project_lifecycle_cleanup,
        "schedule",
        scheduled_job_ids.append,
        raising=False,
    )

    def reject_inline_cleanup(_job_id: str) -> bool:
        raise AssertionError("delete API must not run physical cleanup inline")

    monkeypatch.setattr(
        project_lifecycle_cleanup,
        "flush",
        reject_inline_cleanup,
    )

    deleted = client.delete(f"/api/projects/{project['project_id']}")

    assert deleted.status_code == 200
    assert deleted.json() == {
        "status": "deleted",
        "cleanup_status": "pending",
    }
    assert project_store.get_project(project["project_id"]) is None
    assert root.is_dir()
    assert len(scheduled_job_ids) == 1
    with database.conn() as connection:
        pending = connection.execute(
            """
            SELECT job_id, action
            FROM video_localization_project_cleanup_jobs
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()
    assert pending is not None
    assert pending["job_id"] == scheduled_job_ids[0]
    assert pending["action"] == "delete"


def test_delete_project_reads_only_project_owned_history(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "定向历史查询", "description": ""},
    ).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "只读取当前项目的历史"},
    )
    original_list_history = history_store.list_history
    observed_project_ids: list[str | None] = []

    def project_scoped_history(*args, **kwargs):
        observed_project_ids.append(kwargs.get("project_id"))
        return original_list_history(*args, **kwargs)

    monkeypatch.setattr(
        history_store,
        "list_history",
        project_scoped_history,
    )
    monkeypatch.setattr(
        project_lifecycle_cleanup,
        "schedule",
        lambda _job_id: True,
    )

    deleted = client.delete(f"/api/projects/{project['project_id']}")

    assert deleted.status_code == 200
    assert observed_project_ids == [project["project_id"]]


def test_project_owned_task_and_batch_queries_do_not_load_global_inventory(
    tmp_path: Path,
    monkeypatch,
):
    _client(tmp_path)
    project_id = "project-scoped-inventory"
    project_task = GenerationTask(
        task_id="project-scoped-task",
        engine_id="indextts-v2",
        project_id=project_id,
        input_text="当前项目",
        status=TaskStatus.success,
    )
    other_task = GenerationTask(
        task_id="other-project-task",
        engine_id="indextts-v2",
        project_id="other-project",
        input_text="其他项目",
        status=TaskStatus.success,
    )
    task_queue._save(project_task)
    task_queue._save(other_task)
    project_batch = BatchTask(
        batch_task_id="project-scoped-batch",
        project_name="当前项目批次",
        status=TaskStatus.success,
        parameters={"project_id": project_id},
    )
    other_batch = BatchTask(
        batch_task_id="other-project-batch",
        project_name="其他项目批次",
        status=TaskStatus.success,
        parameters={"project_id": "other-project"},
    )
    database.upsert(
        "batches",
        project_batch.batch_task_id,
        project_batch.model_dump(),
    )
    database.upsert(
        "batches",
        other_batch.batch_task_id,
        other_batch.model_dump(),
    )

    def reject_global_inventory():
        raise AssertionError("must not load the global inventory")

    monkeypatch.setattr(
        task_queue,
        "list_tasks",
        reject_global_inventory,
    )
    monkeypatch.setattr(
        batch_queue,
        "list_batches",
        reject_global_inventory,
    )

    assert [item.task_id for item in task_queue.list_project_tasks(project_id)] == [project_task.task_id]
    assert [item.batch_task_id for item in batch_queue.list_project_batches(project_id)] == [
        project_batch.batch_task_id
    ]


def test_project_delete_queries_use_project_expression_indexes(
    tmp_path: Path,
):
    _client(tmp_path)
    indexed_queries = (
        (
            "idx_tasks_project",
            """
            SELECT 1 FROM tasks
            WHERE json_extract(data, '$.project_id') = ?
            """,
        ),
        (
            "idx_history_project",
            """
            SELECT 1 FROM history
            WHERE json_extract(data, '$.project_id') = ?
            """,
        ),
        (
            "idx_batches_project",
            """
            SELECT 1 FROM batches
            WHERE json_extract(data, '$.parameters.project_id') = ?
            """,
        ),
    )
    with database.conn() as connection:
        for expected_index, query in indexed_queries:
            plan = connection.execute(
                f"EXPLAIN QUERY PLAN {query}",
                ("project-id",),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in plan)
            assert expected_index in detail


def test_project_cleanup_worker_runs_jobs_serially_and_deduplicates(
    monkeypatch,
):
    started_first = threading.Event()
    release_first = threading.Event()
    completed_second = threading.Event()
    calls: list[str] = []

    def controlled_flush(job_id: str) -> bool:
        calls.append(job_id)
        if job_id == "cleanup-a":
            started_first.set()
            assert release_first.wait(timeout=2)
        if job_id == "cleanup-b":
            completed_second.set()
        return True

    monkeypatch.setattr(
        project_lifecycle_cleanup,
        "flush",
        controlled_flush,
    )
    try:
        assert project_lifecycle_cleanup.schedule("cleanup-a")
        assert started_first.wait(timeout=2)
        assert not project_lifecycle_cleanup.schedule("cleanup-a")
        assert project_lifecycle_cleanup.schedule("cleanup-b")
        release_first.set()
        assert completed_second.wait(timeout=2)
        assert calls == ["cleanup-a", "cleanup-b"]
    finally:
        release_first.set()
        asyncio.run(project_lifecycle_cleanup.shutdown())


def test_delete_project_repository_failure_preserves_local_package(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "删除提交失败", "description": ""},
    ).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {
                "filename": "keep-me.mp4",
                "duration_ms": 1200,
            }
        },
    )
    assert saved.status_code == 200
    root = _project_root(project["project_id"])
    source = root / "source" / "keep-me.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"keep")

    def reject_delete(*_args, **_kwargs):
        raise video_localization_operation_store.ProjectRevisionConflict("injected delete conflict")

    monkeypatch.setattr(
        project_store,
        "delete_project",
        reject_delete,
    )
    response = client.delete(f"/api/projects/{project['project_id']}")

    assert response.status_code == 409
    assert project_store.get_project(project["project_id"]) is not None
    assert source.read_bytes() == b"keep"
    assert (root / "project.json").is_file()


def test_delete_project_cleanup_failure_keeps_tombstone_until_replay(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "删除清理重放", "description": ""},
    ).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "不能被目录同步复活"},
    )
    assert saved.status_code == 200
    root = _project_root(project["project_id"])
    original_stage = media_assets.stage_project_video_localization_dir

    def reject_stage(**_kwargs):
        raise OSError("injected package move failure")

    monkeypatch.setattr(
        media_assets,
        "stage_project_video_localization_dir",
        reject_stage,
    )
    scheduled_job_ids: list[str] = []
    monkeypatch.setattr(
        project_lifecycle_cleanup,
        "schedule",
        scheduled_job_ids.append,
    )
    deleted = client.delete(f"/api/projects/{project['project_id']}")
    assert len(scheduled_job_ids) == 1
    assert not project_lifecycle_cleanup.flush(scheduled_job_ids[0])
    synced = client.post("/api/projects/video-localization/sync-projects")

    assert deleted.status_code == 200
    assert project_store.get_project(project["project_id"]) is None
    assert root.is_dir()
    assert project["project_id"] not in {item["project_id"] for item in synced.json()}
    with database.conn() as connection:
        pending = connection.execute(
            """
            SELECT action, last_error
            FROM video_localization_project_cleanup_jobs
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()
    assert pending is not None
    assert pending["action"] == "delete"
    assert "injected package move failure" in pending["last_error"]

    monkeypatch.setattr(
        media_assets,
        "stage_project_video_localization_dir",
        original_stage,
    )
    assert project_lifecycle_cleanup.replay_pending(limit=10) == 1
    assert not root.exists()
    with database.conn() as connection:
        pending = connection.execute(
            """
            SELECT 1
            FROM video_localization_project_cleanup_jobs
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()
    assert pending is None


def test_delete_project_api_blocks_queued_video_localization_operation(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "排队任务项目", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "operations": [
                VideoLocalizationOperation(
                    project_id=project["project_id"],
                    kind="english_asr",
                    status="queued",
                    label="生成 ASR 字幕",
                ).model_dump()
            ]
        },
    )
    assert saved.status_code == 200

    deleted = client.delete(f"/api/projects/{project['project_id']}")

    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "VIDEO_LOCALIZATION_DELETE_BLOCKED"
    assert project_store.get_project(project["project_id"]) is not None


def test_delete_project_api_blocks_active_tts_task(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "配音任务运行中", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "active-tts.mp4", "duration_ms": 1200}},
    )
    task_queue._save(
        GenerationTask(
            task_id="active-project-task",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            input_text="正在生成",
            status=TaskStatus.running,
        )
    )

    deleted = client.delete(f"/api/projects/{project['project_id']}")

    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "VIDEO_LOCALIZATION_DELETE_BLOCKED"
    assert project_store.get_project(project["project_id"]) is not None


def test_delete_project_api_blocks_active_batch_task(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "批量任务运行中", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "active-batch.mp4", "duration_ms": 1200}},
    )
    batch = BatchTask(
        batch_task_id="active-project-batch",
        project_name="正在批量生成",
        status=TaskStatus.running,
        parameters={"source": "video_localization", "project_id": project["project_id"]},
    )
    database.upsert("batches", batch.batch_task_id, batch.model_dump())

    deleted = client.delete(f"/api/projects/{project['project_id']}")

    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "VIDEO_LOCALIZATION_DELETE_BLOCKED"
    assert project_store.get_project(project["project_id"]) is not None


def test_video_localization_sync_hides_missing_directory_without_deleting_database_project(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "目录被删除", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "deleted.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200
    shutil.rmtree(_project_root(project["project_id"]))

    synced = client.post("/api/projects/video-localization/sync-projects")

    assert synced.status_code == 200
    assert synced.json() == []
    assert project_store.get_project(project["project_id"]) is not None
    opened = client.post(f"/api/projects/{project['project_id']}/video-localization/open-directory")
    assert opened.status_code == 410
    assert opened.json()["error"]["code"] == "VIDEO_LOCALIZATION_PROJECT_DIRECTORY_MISSING"
    assert not _project_root(project["project_id"]).exists()


def test_video_localization_sync_does_not_overwrite_existing_database_draft(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "磁盘名称", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "disk.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200
    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.name = "数据库名称"
    stored.parameters["video_localization"]["source_media"]["filename"] = "database.mp4"
    project_store.save_project(stored)

    synced = client.post("/api/projects/video-localization/sync-projects")

    assert synced.status_code == 200
    assert synced.json()[0]["name"] == "数据库名称"
    current = project_store.get_project(project["project_id"])
    assert current is not None
    assert current.parameters["video_localization"]["source_media"]["filename"] == "database.mp4"


def test_video_localization_sync_keeps_known_project_with_external_generated_audio(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "外部生成音频", "description": ""}).json()
    external_audio = tmp_path / "outputs" / "generated.wav"
    external_audio.parent.mkdir(parents=True, exist_ok=True)
    external_audio.write_bytes(b"generated-audio")
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {"filename": "source.mp4", "duration_ms": 1200},
            "timeline_clips": [
                {
                    "clip_id": "pending_external",
                    "track_id": "dub",
                    "start_ms": 0,
                    "end_ms": 1200,
                    "audio_path": str(external_audio),
                    "status": "applying",
                }
            ],
        },
    )
    assert saved.status_code == 200

    synced = client.post("/api/projects/video-localization/sync-projects")

    assert synced.status_code == 200
    assert project["project_id"] in {item["project_id"] for item in synced.json()}


def test_video_localization_sync_rejects_unsafe_or_duplicate_packages(tmp_path: Path):
    client = _client(tmp_path)
    projects_root = tmp_path / "projects"
    unsafe_root = projects_root / "unsafe-package"
    unsafe_root.mkdir(parents=True)
    unsafe_manifest = {
        "kind": "video_localization_project",
        "project_id": "unsafe123456",
        "project_name": "不安全项目",
        "draft": {"source_media": {"filename": "unsafe.mp4", "video_path": "project://../outside.mp4"}},
    }
    (unsafe_root / "project.json").write_text(json.dumps(unsafe_manifest, ensure_ascii=False), encoding="utf-8")
    external_root = projects_root / "unknown-external-package"
    external_root.mkdir()
    external_manifest = {
        "kind": "video_localization_project",
        "project_id": "external123456",
        "project_name": "未知外部依赖项目",
        "draft": {
            "source_media": {"filename": "external.mp4", "duration_ms": 1200},
            "timeline_clips": [
                {
                    "clip_id": "external_clip",
                    "track_id": "dub",
                    "start_ms": 0,
                    "end_ms": 1200,
                    "audio_path": str(tmp_path / "outside.wav"),
                    "status": "ready",
                }
            ],
        },
    }
    (external_root / "project.json").write_text(json.dumps(external_manifest, ensure_ascii=False), encoding="utf-8")
    for directory_name in ("duplicate-a", "duplicate-b"):
        root = projects_root / directory_name
        root.mkdir()
        manifest = {
            "kind": "video_localization_project",
            "project_id": "duplicate123",
            "project_name": "重复项目",
            "draft": {"source_media": {"filename": "duplicate.mp4"}},
        }
        (root / "project.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    synced = client.post("/api/projects/video-localization/sync-projects")

    assert synced.status_code == 200
    assert synced.json() == []
    assert project_store.get_project("unsafe123456") is None
    assert project_store.get_project("duplicate123") is None
    assert project_store.get_project("external123456") is None


def test_video_localization_explicitly_migrates_legacy_nested_project_and_rebases_paths(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "旧目录迁移", "description": ""}).json()
    legacy_root = tmp_path / "projects" / project["project_id"] / "video_localization"
    legacy_video = legacy_root / "source" / "legacy.mp4"
    legacy_video.parent.mkdir(parents=True, exist_ok=True)
    legacy_video.write_bytes(b"legacy-video")
    legacy_manifest = {
        "schema_version": 1,
        "kind": "video_localization_project",
        "project_id": project["project_id"],
        "project_name": "旧目录迁移",
        "draft": {
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "legacy.mp4", "video_path": str(legacy_video), "duration_ms": 1200},
        },
    }
    (legacy_root / "project.json").write_text(json.dumps(legacy_manifest, ensure_ascii=False), encoding="utf-8")
    legacy_autosave = legacy_root / "autosave" / "20260101-000000-000000-project.json"
    legacy_autosave.parent.mkdir(parents=True, exist_ok=True)
    legacy_autosave.write_text(json.dumps(legacy_manifest, ensure_ascii=False), encoding="utf-8")

    opened = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert opened.status_code == 200
    assert opened.json()["source_media"]["video_path"] is None
    assert legacy_video.read_bytes() == b"legacy-video"

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/repair-storage")

    assert response.status_code == 200
    migrated_root = _project_root(project["project_id"])
    migrated_video = migrated_root / "source" / "legacy.mp4"
    assert response.json()["source_media"]["video_path"] is None
    migrated_draft = video_localization_service.get_video_localization(project["project_id"])
    assert migrated_draft is not None
    assert Path(migrated_draft.source_media.video_path or "") == migrated_video
    assert migrated_video.read_bytes() == b"legacy-video"
    assert not (tmp_path / "projects" / project["project_id"]).exists()
    assert migrated_root.name.endswith(f"--{project['project_id']}")

    saved = client.put(f"/api/projects/{project['project_id']}/video-localization", json=response.json())
    assert saved.status_code == 200
    portable_manifest = json.loads((migrated_root / "project.json").read_text(encoding="utf-8"))
    assert portable_manifest["draft"]["source_media"]["video_path"] == "project://source/legacy.mp4"
    migrated_autosave = migrated_root / "autosave" / legacy_autosave.name
    assert (
        json.loads(migrated_autosave.read_text(encoding="utf-8"))["draft"]["source_media"]["video_path"]
        == "project://source/legacy.mp4"
    )


def test_video_localization_empty_draft_has_contract_defaults(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空草稿", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 200
    body = response.json()
    assert body["project_type"] == "video_localization"
    assert body["schema_version"] == "v1"
    assert body["source_media"]["filename"] is None
    assert body["source_media"]["metadata"] == {}
    assert body["stems"]["separation_status"] == "pending"
    assert body["quality_gate"]["status"] == "unknown"
    assert body["quality_gate"]["pending_issues"] == 0
    assert body["localized_subtitles"] == []
    assert body["ui_state"] == {}
    assert body["generated_candidates"] == []
    assert body["timeline_clips"] == []


def test_video_localization_import_localized_srt_updates_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导入字幕", "description": ""}).json()
    draft = {
        "project_type": "video_localization",
        "schema_version": "v1",
        "cues": [
            {
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1000,
                "en_subtitle_text": "Hello",
                "tts_recommended_text": "",
            },
            {
                "cue_id": "cue_0002",
                "start_ms": 1200,
                "end_ms": 2200,
                "en_subtitle_text": "World",
                "tts_recommended_text": "保留已有台词",
            },
        ],
    }
    client.put(f"/api/projects/{project['project_id']}/video-localization", json=draft)

    srt_text = """1
00:00:00,100 --> 00:00:01,100
你好

2
00:00:01,250 --> 00:00:02,150
世界
"""
    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={"srt_text": srt_text, "update_timing": True, "overwrite_tts": False},
    )

    assert response.status_code == 200
    body = response.json()
    cues = body["cues"]
    localized_subtitles = body["localized_subtitles"]
    assert cues[0]["start_ms"] == 0
    assert cues[0]["end_ms"] == 1000
    assert cues[0]["zh_localized_subtitle_text"] == "你好"
    assert cues[0]["tts_recommended_text"] == ""
    assert "zh_srt_import" in cues[0]["quality_flags"]
    assert cues[1]["zh_localized_subtitle_text"] == "世界"
    assert cues[1]["tts_recommended_text"] == "保留已有台词"
    assert [(cue["subtitle_id"], cue["linked_cue_id"]) for cue in localized_subtitles] == [
        ("subtitle_0001", "cue_0001"),
        ("subtitle_0002", "cue_0002"),
    ]
    assert [(cue["start_ms"], cue["end_ms"], cue["text"]) for cue in localized_subtitles] == [
        (100, 1100, "你好"),
        (1250, 2150, "世界"),
    ]
    assert [cue["tts_text"] for cue in localized_subtitles] == [None, "保留已有台词"]
    quality_codes = {issue["code"] for issue in [*body["quality_gate"]["blockers"], *body["quality_gate"]["warnings"]]}
    assert "CUE_SPEAKER_MISSING" not in quality_codes
    assert "TTS_TEXT_MISSING" not in quality_codes

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert fetched["cues"][0]["zh_localized_subtitle_text"] == "你好"
    assert fetched["localized_subtitles"][0]["subtitle_id"] == "subtitle_0001"


def test_video_localization_import_localized_srt_creates_empty_subtitle_track(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空轨导入字幕", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={
            "srt_text": """1
00:00:01,250 --> 00:00:02,800
第一句本土化字幕

2
00:00:03,100 --> 00:00:05,000
第二句本土化字幕
""",
            "update_timing": True,
            "overwrite_tts": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cues"] == []
    subtitles = body["localized_subtitles"]
    assert [subtitle["subtitle_id"] for subtitle in subtitles] == ["subtitle_0001", "subtitle_0002"]
    assert [(subtitle["start_ms"], subtitle["end_ms"]) for subtitle in subtitles] == [(1250, 2800), (3100, 5000)]
    assert [subtitle["text"] for subtitle in subtitles] == ["第一句本土化字幕", "第二句本土化字幕"]
    assert [subtitle["quality_flags"] for subtitle in subtitles] == [["zh_srt_import"], ["zh_srt_import"]]


def test_video_localization_import_tts_srt_creates_empty_subtitle_track(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空轨导入 TTS", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/tts/import",
        json={
            "srt_text": """1
00:00:00,500 --> 00:00:02,000
第一句配音台词

2
00:00:02,250 --> 00:00:03,700
第二句配音台词
""",
        },
    )

    assert response.status_code == 200
    cues = response.json()["cues"]
    assert [cue["cue_id"] for cue in cues] == ["cue_0001", "cue_0002"]
    assert [(cue["start_ms"], cue["end_ms"], cue["source_duration_ms"]) for cue in cues] == [
        (500, 2000, 1500),
        (2250, 3700, 1450),
    ]
    assert [cue["tts_recommended_text"] for cue in cues] == ["第一句配音台词", "第二句配音台词"]
    assert all(cue["zh_localized_subtitle_text"] is None for cue in cues)
    assert cues[0]["quality_flags"] == ["tts_srt_import"]


def test_video_localization_import_srt_rejects_invalid_payload(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "坏字幕", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"project_type": "video_localization", "schema_version": "v1", "cues": [{"cue_id": "cue_0001"}]},
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={"srt_text": "not an srt"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_EMPTY"


def test_video_localization_import_localized_srt_rejects_track_overlap(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕重叠", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={
            "srt_text": """1
00:00:00,000 --> 00:00:01,500
第一句

2
00:00:01,200 --> 00:00:02,000
第二句
""",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_TRACK_OVERLAP"
    assert "时间重叠" in response.json()["error"]["message"]


def test_video_localization_reset_clears_draft_and_project_assets(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重置草稿", "description": ""}).json()
    project_dir = _project_root(project["project_id"])
    source_dir = project_dir / "source"
    stems_dir = project_dir / "stems"
    refs_dir = project_dir / "references"
    source_dir.mkdir(parents=True, exist_ok=True)
    stems_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)
    video_path = source_dir / "demo.mp4"
    audio_path = project_dir / "audio" / "demo-source.wav"
    vocals_path = stems_dir / "demo-vocals.wav"
    reference_path = refs_dir / "ref_001.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    audio_path.write_bytes(b"audio")
    vocals_path.write_bytes(b"vocals")
    reference_path.write_bytes(b"reference")
    preview_cache_root = settings_store.cache_dir() / "video-preview" / project["project_id"]
    preview_cache_root.mkdir(parents=True, exist_ok=True)
    (preview_cache_root / "sprite.jpg").write_bytes(b"sprite")
    waveform_path = (
        settings_store.cache_dir()
        / "waveforms"
        / f"video-localization-{project['project_id']}-media_original-v2-1-1-1200.json"
    )
    waveform_path.parent.mkdir(parents=True, exist_ok=True)
    waveform_path.write_text("{}", encoding="utf-8")

    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "video_path": str(video_path), "audio_path": str(audio_path)},
            "stems": {
                "original_audio_path": str(audio_path),
                "vocals_clean_path": str(vocals_path),
                "separation_status": "completed",
            },
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "audio_path": str(reference_path),
                    "cleanliness": "clean",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "Hello.",
                }
            ],
        },
    )

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert body["source_media"]["filename"] is None
    assert body["source_media"]["video_path"] is None
    assert body["stems"]["separation_status"] == "pending"
    assert body["speakers"] == []
    assert body["reference_clips"] == []
    assert body["cues"] == []
    assert body["operations"] == []
    assert project_dir.exists()
    assert not video_path.exists()
    assert not audio_path.exists()
    assert not vocals_path.exists()
    assert not reference_path.exists()
    assert not preview_cache_root.exists()
    assert not waveform_path.exists()

    stored_project = client.get(f"/api/projects/{project['project_id']}").json()
    assert stored_project["parameters"]["video_localization"]["source_media"]["video_path"] is None


def test_video_localization_reset_rejects_stale_draft_resurrection(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重置后拒绝旧草稿", "description": ""}).json()
    source_path = _project_root(project["project_id"]) / "source" / "demo.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")
    stale_draft = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "video_path": str(source_path)},
        },
    ).json()

    reset = client.delete(f"/api/projects/{project['project_id']}/video-localization")
    resurrect = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=stale_draft,
    )

    assert reset.status_code == 200
    assert resurrect.status_code == 409
    assert resurrect.json()["error"]["code"] == "VIDEO_LOCALIZATION_DRAFT_CONFLICT"
    current = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert current["source_media"]["video_path"] is None


def test_video_localization_reset_abandons_pending_tts_handoffs(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "重置退役配音交接", "description": ""},
    ).json()
    client.get(f"/api/projects/{project['project_id']}/video-localization")
    payload = TtsTaskRegistrationEventV1(
        project_id=project["project_id"],
        source_kind="task",
        source_id="task-before-reset",
        segment_id="localized-before-reset",
        generation_task_id="task-before-reset",
        workflow_id="workflow-before-reset",
    )
    event_id = video_localization_tts_handoff_store.registration_event_id(
        source_kind=payload.source_kind,
        source_id=payload.source_id,
    )
    with database.conn() as connection:
        video_localization_tts_handoff_store.enqueue(
            connection,
            event_id=event_id,
            payload=payload,
            created_at="2026-08-02T00:00:00",
        )

    reset = client.delete(f"/api/projects/{project['project_id']}/video-localization")

    assert reset.status_code == 200
    stored = video_localization_tts_handoff_store.get(event_id)
    assert stored is not None
    assert stored.status == "abandoned"
    assert "reset" in (stored.last_error or "")


def test_video_localization_reset_repository_failure_preserves_assets(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "重置提交失败", "description": ""},
    ).json()
    source_path = _project_root(project["project_id"]) / "source" / "keep.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"keep")
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {
                "filename": "keep.mp4",
                "video_path": str(source_path),
            },
        },
    )
    assert saved.status_code == 200

    def reject_reset(*_args, **_kwargs):
        raise video_localization_operation_store.ProjectRevisionConflict("injected reset conflict")

    monkeypatch.setattr(
        project_store,
        "reset_video_localization_project",
        reject_reset,
    )
    response = client.delete(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 409
    assert source_path.read_bytes() == b"keep"
    current = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert current.status_code == 200
    assert current.json()["source_media"]["filename"] == "keep.mp4"


def test_video_localization_reset_cleanup_failure_blocks_new_writes_until_replay(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "重置清理重放", "description": ""},
    ).json()
    source_path = _project_root(project["project_id"]) / "source" / "old.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"old")
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {
                "filename": "old.mp4",
                "video_path": str(source_path),
            }
        },
    )
    assert saved.status_code == 200
    original_stage = media_assets.stage_project_video_localization_dir

    def reject_stage(**_kwargs):
        raise OSError("injected reset move failure")

    monkeypatch.setattr(
        media_assets,
        "stage_project_video_localization_dir",
        reject_stage,
    )
    reset = client.delete(f"/api/projects/{project['project_id']}/video-localization")
    blocked_write = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=reset.json(),
    )

    assert reset.status_code == 200
    assert reset.json()["source_media"]["filename"] is None
    assert source_path.read_bytes() == b"old"
    assert blocked_write.status_code == 409
    assert blocked_write.json()["error"]["code"] == ("VIDEO_LOCALIZATION_CLEANUP_PENDING")

    monkeypatch.setattr(
        media_assets,
        "stage_project_video_localization_dir",
        original_stage,
    )
    assert project_lifecycle_cleanup.replay_pending(limit=10) == 1
    assert not source_path.exists()
    current = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert current.status_code == 200
    assert current.json()["source_media"]["filename"] is None


def test_video_localization_reset_retires_completed_command_operation(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "重置命令历史", "description": ""},
    ).json()
    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    operation = VideoLocalizationOperation(
        operation_id="completed-command",
        project_id=project["project_id"],
        kind="source_audio",
        status="queued",
        parameters={"scope_id": "reset-test"},
    )
    command = video_localization_operation_ledger_store.command_from_operation(
        "submit",
        operation.model_dump(mode="json"),
        expected_project_revision=(video_localization_operation_store.project_revision(project["project_id"])),
        command_id="reset-command",
    )
    stored.parameters["video_localization"] = VideoLocalizationDraft(operations=[operation]).model_dump(mode="json")
    project_store.save_project(
        stored,
        touch_updated_at=False,
        operation_command=command,
    )
    latest = project_store.get_project(project["project_id"])
    assert latest is not None
    completed = operation.model_copy(
        update={
            "status": "success",
            "completed_at": "2026-08-02T12:00:00",
        }
    )
    latest.parameters["video_localization"] = VideoLocalizationDraft(operations=[completed]).model_dump(mode="json")
    project_store.save_project(
        latest,
        touch_updated_at=False,
        operation_updated_at="2026-08-02T12:00:00",
    )

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 200
    assert response.json()["operations"] == []
    assert (
        video_localization_operation_ledger_store.get_operation(
            project["project_id"],
            operation.operation_id,
        )
        is None
    )


@pytest.mark.parametrize("status", ["queued", "running"])
def test_video_localization_reset_blocks_active_operations(tmp_path: Path, status: str):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重置阻断", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4"},
            "operations": [
                VideoLocalizationOperation(
                    project_id=project["project_id"], kind="stems", status=status, label="分离人声与背景声"
                ).model_dump()
            ],
        },
    )

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_RESET_BLOCKED"
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["source_media"]["filename"] == "demo.mp4"
    assert draft["operations"][0]["status"] == status


def test_video_localization_rejects_inverted_time_ranges(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "坏时间码", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_bad",
                    "start_ms": 5000,
                    "end_ms": 3000,
                    "tts_recommended_text": "这句时间码有问题。",
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_video_localization_save_recalculates_quality_gate_blockers(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "质量门阻断", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "quality_gate": {"status": "pass", "pending_issues": 0},
            "cues": [
                {
                    "cue_id": "cue_blocked",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "zh_localized_subtitle_text": "中文字幕存在。",
                    "tts_recommended_text": "中文字幕存在。",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["quality_gate"]["status"] == "blocked"
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert "EN_SUBTITLE_MISSING" in blocker_codes
    assert "CUE_SPEAKER_MISSING" not in blocker_codes


def test_video_localization_complete_clone_cue_can_pass_quality_gate(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "质量门通过", "description": ""}).json()
    project_root = _project_root(project["project_id"])
    video_path = project_root / "source" / "source.mp4"
    vocals_path = project_root / "stems" / "vocals.wav"
    background_path = project_root / "stems" / "background.wav"
    for path in (video_path, vocals_path, background_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "source.mp4",
                "video_path": str(video_path),
            },
            "stems": {
                "separation_status": "completed",
                "vocals_clean_path": str(vocals_path),
                "background_path": str(background_path),
            },
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A", "route": "clone_from_source"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": "refs/ref_001.wav",
                    "cleanliness": "clean",
                    "asr_text": "This is a clean reference.",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_ready",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                    "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                    "reference_clip_id": "ref_001",
                    "review_status": "ready",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready_for_tts"
    assert body["quality_gate"]["status"] == "pass"
    assert body["quality_gate"]["pending_issues"] == 0


def test_video_localization_patch_cue_updates_single_row_and_quality_gate(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "局部保存 cue", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 2200,
                    "en_subtitle_text": "Original English.",
                },
                {
                    "cue_id": "cue_0002",
                    "speaker_id": "speaker_02",
                    "start_ms": 2300,
                    "end_ms": 3200,
                    "en_subtitle_text": "Keep this line.",
                },
            ],
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001",
        json={
            "zh_localized_subtitle_text": "显示字幕。",
            "tts_recommended_text": "显示字幕。",
            "review_status": "ready",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cues"][0]["zh_localized_subtitle_text"] == "显示字幕。"
    assert body["cues"][0]["review_status"] == "ready"
    assert body["cues"][1]["en_subtitle_text"] == "Keep this line."
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert "ZH_SUBTITLE_MISSING" not in {
        issue["code"] for issue in body["quality_gate"]["blockers"] if issue.get("cue_id") == "cue_0001"
    }
    assert "ZH_SUBTITLE_MISSING" in blocker_codes


def test_video_localization_patch_cue_rejects_invalid_time_range(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "局部坏时间", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 1000, "end_ms": 2000}],
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001",
        json={"end_ms": 500},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_CUE_INVALID"


def test_video_localization_patch_localized_subtitle_updates_timing_without_overlap(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "局部保存字幕轨", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0002",
                    "start_ms": 1200,
                    "end_ms": 2200,
                    "en_subtitle_text": "Second line",
                    "zh_localized_subtitle_text": "第二句",
                    "tts_recommended_text": "第二句。",
                }
            ],
            "localized_subtitles": [
                {"subtitle_id": "subtitle_0001", "start_ms": 0, "end_ms": 1000, "text": "第一句"},
                {
                    "subtitle_id": "subtitle_0002",
                    "start_ms": 1200,
                    "end_ms": 2200,
                    "text": "第二句",
                    "tts_text": "第二句。",
                    "source_cue_ids": ["cue_0002"],
                },
            ],
            "localization_state": {
                "status": "draft",
                "source_fingerprint": "source_unchanged",
                "final_script_fingerprint": "script_old",
                "dual_tracks_fingerprint": "tracks_old",
                "quality_gate_fingerprint": "gate_old",
                "spoken_segment_count": 2,
                "subtitle_count": 2,
            },
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_0002",
        json={"start_ms": 1000, "end_ms": 2100, "text": "修改后的第二句", "tts_text": "修改后的第二句。"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [(item["subtitle_id"], item["start_ms"], item["end_ms"]) for item in body["localized_subtitles"]] == [
        ("subtitle_0001", 0, 1000),
        ("subtitle_0002", 1000, 2100),
    ]
    assert body["cues"][0]["zh_localized_subtitle_text"] == "修改后的第二句"
    assert body["cues"][0]["tts_recommended_text"] == "修改后的第二句。"
    assert body["localization_state"]["status"] == "edited"
    assert body["localization_state"]["source_fingerprint"] == "source_unchanged"
    assert "dual_tracks_fingerprint" not in body["localization_state"]
    assert "quality_gate_fingerprint" not in body["localization_state"]


def test_video_localization_patch_localized_subtitle_rejects_overlap_and_too_short_duration(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕轨坏时间", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {"subtitle_id": "subtitle_0001", "start_ms": 0, "end_ms": 1000, "text": "第一句"},
                {"subtitle_id": "subtitle_0002", "start_ms": 1200, "end_ms": 2200, "text": "第二句"},
            ],
        },
    )

    overlap = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_0002",
        json={"start_ms": 900},
    )
    too_short = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_0002",
        json={"start_ms": 1200, "end_ms": 1220},
    )

    assert overlap.status_code == 400
    assert overlap.json()["error"]["code"] == "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_OVERLAP"
    assert "不能重叠" in overlap.json()["error"]["message"]
    assert too_short.status_code == 400
    assert too_short.json()["error"]["code"] == "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_TOO_SHORT"


def test_video_localization_patch_localized_subtitle_text_ignores_unrelated_legacy_timing_issue(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "旧时间不阻塞文字编辑", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {"subtitle_id": "subtitle_legacy_short", "start_ms": 0, "end_ms": 3, "text": "旧字幕"},
                {
                    "subtitle_id": "subtitle_editable",
                    "start_ms": 1000,
                    "end_ms": 2200,
                    "text": "原上屏字幕",
                    "tts_text": "原配音台词。",
                },
            ],
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_editable",
        json={"text": "修改后的上屏字幕", "tts_text": "修改后的配音台词。"},
    )

    assert response.status_code == 200
    subtitle = next(
        item for item in response.json()["localized_subtitles"] if item["subtitle_id"] == "subtitle_editable"
    )
    assert subtitle["text"] == "修改后的上屏字幕"
    assert subtitle["tts_text"] == "修改后的配音台词。"

    legacy_response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_legacy_short",
        json={"tts_text": "旧时间字幕也能修改台词。"},
    )
    assert legacy_response.status_code == 200
    legacy_subtitle = next(
        item for item in legacy_response.json()["localized_subtitles"] if item["subtitle_id"] == "subtitle_legacy_short"
    )
    assert legacy_subtitle["tts_text"] == "旧时间字幕也能修改台词。"


def test_video_localization_patch_spoken_segment_keeps_display_text_and_changes_dubbing_source(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "单独调整配音台词", "description": ""},
    ).json()
    project_id = project["project_id"]
    client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1000,
                    "end_ms": 2200,
                    "en_subtitle_text": "What does that mean?",
                    "source_word_ids": ["word_0001"],
                }
            ],
            "localized_spoken_segments": [
                {
                    "segment_id": "spoken_segment_0001",
                    "paragraph_id": "paragraph_0001",
                    "text": "这句话到底是什么意思？",
                    "start_ms": 1000,
                    "end_ms": 2200,
                    "source_cue_ids": ["cue_0001"],
                    "source_word_ids": ["word_0001"],
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "subtitle_0001",
                    "start_ms": 1000,
                    "end_ms": 2200,
                    "text": "这句话到底是什么意思？",
                    "tts_text": "这句话到底是什么意思？",
                    "source_cue_ids": ["cue_0001"],
                    "source_word_ids": ["word_0001"],
                    "spoken_segment_id": "spoken_segment_0001",
                }
            ],
            "localization_state": {
                "status": "draft",
                "source_fingerprint": "source_unchanged",
                "final_script_fingerprint": "script_old",
                "dual_tracks_fingerprint": "tracks_old",
                "quality_gate_fingerprint": "gate_old",
                "spoken_segment_count": 1,
                "subtitle_count": 1,
            },
        },
    )
    before = client.get(f"/api/projects/{project_id}/video-localization/dubbing/snapshot").json()["source_revision"]

    response = client.patch(
        f"/api/projects/{project_id}/video-localization/localized-spoken-segments/spoken_segment_0001",
        json={"text": "这是什么意思？"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["localized_spoken_segments"][0]["text"] == "这是什么意思？"
    assert body["localized_subtitles"][0]["text"] == "这句话到底是什么意思？"
    assert body["localization_state"]["status"] == "edited"
    assert "quality_gate_fingerprint" not in body["localization_state"]
    assert "dual_tracks_fingerprint" not in body["localization_state"]
    after = client.get(f"/api/projects/{project_id}/video-localization/dubbing/snapshot").json()["source_revision"]
    assert after != before


def test_video_localization_can_create_speaker_and_assign_cue(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "说话人分配", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "audio_route": "manual_review",
                    "en_subtitle_text": "This changed everything.",
                    "quality_flags": ["generated_by_asr", "needs_speaker_assignment", "needs_zh_localization"],
                }
            ],
        },
    )

    created = client.post(
        f"/api/projects/{project['project_id']}/video-localization/speakers",
        json={"display_name": "A", "route": "clone_from_source"},
    )

    assert created.status_code == 200
    speaker = created.json()["speakers"][0]
    assert speaker["speaker_id"] == "speaker_01"
    assert speaker["display_name"] == "A"
    assert speaker["route"] == "clone_from_source"

    updated = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001",
        json={
            "speaker_id": "speaker_01",
            "audio_route": "clone_from_source",
            "zh_localized_subtitle_text": "这改变了一切。",
            "tts_recommended_text": "这，改变了一切。",
        },
    )

    assert updated.status_code == 200
    body = updated.json()
    cue = body["cues"][0]
    assert cue["speaker_id"] == "speaker_01"
    assert cue["audio_route"] == "clone_from_source"
    assert "needs_speaker_assignment" not in cue["quality_flags"]
    assert "needs_zh_localization" not in cue["quality_flags"]
    assert body["speakers"][0]["time_ranges"] == [{"start_ms": 1000, "end_ms": 3200, "source": "cue"}]


def test_video_localization_patch_speaker_keeps_reconciled_tracks(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "更新说话人", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(tmp_path / "ref_001.wav"),
                    "cleanliness": "clean",
                    "asr_status": "verified",
                    "asr_text": "Reference line.",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1500,
                    "end_ms": 4200,
                    "en_subtitle_text": "Reference line.",
                }
            ],
        },
    )

    updated = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/speakers/speaker_01",
        json={"display_name": "旁白 A", "route": "preset_tts", "review_status": "ready"},
    )

    assert updated.status_code == 200
    speaker = updated.json()["speakers"][0]
    assert speaker["display_name"] == "旁白 A"
    assert speaker["route"] == "preset_tts"
    assert speaker["review_status"] == "ready"
    assert speaker["reference_clip_ids"] == ["ref_001"]
    assert speaker["time_ranges"] == [{"start_ms": 1500, "end_ms": 4200, "source": "cue"}]


def test_video_localization_import_source_media_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导入视频", "description": ""}).json()
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda path: {
            "duration_ms": 3400,
            "width": 1920,
            "height": 1080,
            "frame_rate": 24.0,
        },
    )
    cleared: list[tuple[str, str]] = []
    monkeypatch.setattr(
        video_localization_service.playback_proxy,
        "delete_project_cache",
        lambda project_id: cleared.append(("playback", project_id)),
    )
    monkeypatch.setattr(
        video_localization_service.preview_cache,
        "delete_project_cache",
        lambda project_id: cleared.append(("sprites", project_id)),
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo clip.mp4", b"fake-video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "video-localization-mutation-ack-v1"
    assert body["revision"]
    assert set(body) == {"schema_version", "updated_at", "revision"}
    assert cleared == [
        ("playback", project["project_id"]),
        ("sprites", project["project_id"]),
    ]
    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    assert stored.source_media.filename == "demo clip.mp4"
    assert stored.source_media.size_bytes == len(b"fake-video-bytes")
    assert stored.source_media.duration_ms == 3400
    assert stored.source_media.width == 1920
    assert stored.source_media.height == 1080
    assert stored.source_media.frame_rate == 24.0
    assert stored.source_media.metadata["content_type"] == "video/mp4"
    assert stored.source_media.metadata["probe_status"] == "completed"
    video_path = Path(stored.source_media.video_path or "")
    assert stored.source_media.content_sha256 == media_assets.file_sha256(video_path)
    assert video_path.exists()
    assert video_path.name == "demo_clip.mp4"
    assert project["project_id"] in str(video_path)
    assert "导入视频" in str(video_path)


@pytest.mark.asyncio
async def test_video_localization_upload_streams_to_disk_and_hashes_incrementally(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "流式导入", "description": ""}).json()
    chunks = [b"a" * (1024 * 1024), b"b" * 17, b""]

    class ChunkedUpload:
        filename = "large.mp4"
        content_type = "video/mp4"

        def __init__(self):
            self.read_sizes = []

        async def read(self, size: int):
            self.read_sizes.append(size)
            return chunks.pop(0)

    upload = ChunkedUpload()
    path, size_bytes, content_sha256 = await media_assets.save_uploaded_video(project["project_id"], upload)

    assert upload.read_sizes == [1024 * 1024, 1024 * 1024, 1024 * 1024]
    assert size_bytes == 1024 * 1024 + 17
    assert path.stat().st_size == size_bytes
    assert content_sha256 == media_assets.file_sha256(path)


@pytest.mark.asyncio
async def test_video_localization_cancelled_upload_removes_partial_file(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消导入", "description": ""}).json()

    class CancelledUpload:
        filename = "cancelled.mp4"
        content_type = "video/mp4"

        def __init__(self):
            self.read_count = 0

        async def read(self, _size: int):
            self.read_count += 1
            if self.read_count == 1:
                return b"partial-video"
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await media_assets.save_uploaded_video(project["project_id"], CancelledUpload())

    source_dir = _project_root(project["project_id"]) / "source"
    assert list(source_dir.iterdir()) == []


def test_video_localization_reimport_invalidates_source_derived_state(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "替换源视频", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "language_config": {
                "source_language": "auto",
                "target_language": "zh-Hans",
                "detected_source_language": "en",
            },
            "source_media": {"filename": "old.mp4", "metadata": {"english_asr_status": "completed"}},
            "stems": {"vocals_clean_path": "/tmp/old-vocals.wav", "separation_status": "completed"},
            "speakers": [{"speaker_id": "old-speaker"}],
            "cues": [{"cue_id": "old-cue", "start_ms": 0, "end_ms": 1000, "en_subtitle_text": "Old"}],
            "transcription": {"raw_text": "Old", "corrected_text": "Old"},
            "localized_subtitles": [{"subtitle_id": "old-zh", "start_ms": 0, "end_ms": 1000, "text": "旧字幕"}],
            "localization_state": {"created_at": "2026-07-17T10:00:00Z", "source_fingerprint": "old"},
            "generated_candidates": [{"candidate_id": "old", "status": "ready"}],
            "timeline_clips": [{"clip_id": "old", "track_id": "dub"}],
            "ui_state": {"timeline_zoom": 4},
        },
    )
    monkeypatch.setattr(media_assets, "probe_video", lambda path: {"duration_ms": 2000})

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("new.mp4", b"new-video", "video/mp4")},
    )

    assert response.status_code == 200
    assert response.json()["schema_version"] == "video-localization-mutation-ack-v1"
    saved = video_localization_service.get_video_localization(project["project_id"])
    assert saved is not None
    body = saved.model_dump(mode="json")
    assert body["source_media"]["filename"] == "new.mp4"
    assert body["source_media"]["metadata"] == {
        "content_type": "video/mp4",
        "upload_status": "stored",
        "probe_status": "completed",
    }
    assert body["stems"]["vocals_clean_path"] is None
    assert body["speakers"] == []
    assert body["cues"] == []
    assert body["transcription"] is None
    assert body["localized_subtitles"] == []
    assert body["localization_state"] == {}
    assert body["generated_candidates"] == []
    assert body["timeline_clips"] == []
    assert body["ui_state"] == {"timeline_zoom": 4}
    assert body["language_config"] == {
        "source_language": "auto",
        "target_language": "zh-Hans",
        "detected_source_language": None,
    }


def test_video_localization_project_rename_keeps_storage_locator_and_paths(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "旧项目名", "description": ""}).json()
    monkeypatch.setattr(media_assets, "probe_video", lambda path: {"duration_ms": 3400})

    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    ).json()
    assert imported["schema_version"] == "video-localization-mutation-ack-v1"
    imported_draft = video_localization_service.get_video_localization(project["project_id"])
    assert imported_draft is not None
    old_video_path = Path(imported_draft.source_media.video_path or "")
    old_root = old_video_path.parents[1]
    assert old_root.name.endswith(f"--{project['project_id']}")
    assert "旧项目名" in str(old_root)
    old_directory_name = project_store.get_project(project["project_id"]).parameters["video_localization_dir_name"]

    updated = client.patch(f"/api/projects/{project['project_id']}", json={"name": "新项目名"}).json()
    assert updated["name"] == "新项目名"
    assert updated["parameters"]["video_localization_dir_name"] == old_directory_name

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert fetched["source_media"]["video_path"] is None
    renamed_draft = video_localization_service.get_video_localization(project["project_id"])
    assert renamed_draft is not None
    new_video_path = Path(renamed_draft.source_media.video_path or "")
    assert new_video_path.exists()
    assert new_video_path.read_bytes() == b"fake-video-bytes"
    assert new_video_path == old_video_path
    assert old_root.exists()


def test_failed_project_rename_does_not_move_storage(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "Stable package"},
    ).json()
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda path: {"duration_ms": 3400},
    )
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={
            "file": (
                "demo.mp4",
                b"fake-video-bytes",
                "video/mp4",
            )
        },
    ).json()
    assert imported["schema_version"] == "video-localization-mutation-ack-v1"
    imported_draft = video_localization_service.get_video_localization(project["project_id"])
    assert imported_draft is not None
    source_path = Path(imported_draft.source_media.video_path or "")
    package_root = source_path.parents[1]
    candidate_root = media_assets.project_video_localization_dir_for_name(
        project["project_id"],
        "Rejected name",
    )

    def reject_save(*_args, **_kwargs):
        raise video_localization_operation_store.ProjectRevisionConflict("injected rename conflict")

    monkeypatch.setattr(project_store, "save_project", reject_save)

    response = client.patch(
        f"/api/projects/{project['project_id']}",
        json={"name": "Rejected name"},
    )

    assert response.status_code == 409
    assert source_path.read_bytes() == b"fake-video-bytes"
    assert package_root.exists()
    assert not candidate_root.exists()


def test_first_draft_save_advances_project_repository_once(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "Single repository commit"},
    ).json()
    before = project_store.get_project(project["project_id"])
    assert before is not None

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "first draft"},
    )

    assert response.status_code == 200
    after = project_store.get_project(project["project_id"])
    assert after is not None
    assert after._repository_revision == before._repository_revision + 1
    assert after.parameters["video_localization_dir_name"]


def test_project_repository_conflict_is_exposed_as_http_409(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "Project conflict response"},
    ).json()

    def reject_stale_write(*_args, **_kwargs):
        raise video_localization_operation_store.ProjectRevisionConflict("injected stale project")

    monkeypatch.setattr(
        project_store,
        "add_role",
        reject_stale_write,
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/roles",
        json={"name": "Narrator"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("PROJECT_REVISION_CONFLICT")


def test_draft_repository_conflict_preserves_draft_conflict_contract(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "Draft conflict response"},
    ).json()
    initial = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "initial"},
    )
    assert initial.status_code == 200

    def reject_stale_write(*_args, **_kwargs):
        raise video_localization_operation_store.ProjectRevisionConflict("injected stale draft")

    monkeypatch.setattr(
        project_store,
        "save_project",
        reject_stale_write,
    )

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=initial.json(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("VIDEO_LOCALIZATION_DRAFT_CONFLICT")


def test_backend_draft_patch_retries_repository_conflict_on_latest_state(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "Backend CAS retry"},
    ).json()
    initial = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"scene_context": "initial"},
    )
    assert initial.status_code == 200

    original_save = project_store.save_project
    injected = False

    def conflict_after_concurrent_write(project_model, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            concurrent_project = project_store.get_project(project["project_id"])
            assert concurrent_project is not None
            concurrent_draft = VideoLocalizationDraft(**concurrent_project.parameters["video_localization"]).model_copy(
                update={"scene_context": "concurrent"}
            )
            concurrent_project.parameters = {
                **concurrent_project.parameters,
                "video_localization": concurrent_draft.model_dump(mode="json"),
            }
            original_save(concurrent_project)
            raise (video_localization_operation_store.ProjectRevisionConflict("injected retry"))
        return original_save(project_model, **kwargs)

    monkeypatch.setattr(
        project_store,
        "save_project",
        conflict_after_concurrent_write,
    )

    updated = video_localization_service.update_video_localization_atomic(
        project["project_id"],
        lambda draft: draft.model_copy(update={"scene_context": (f"{draft.scene_context}|worker")}),
        intent="runtime",
    )

    assert injected is True
    assert updated is not None
    assert updated.scene_context == "concurrent|worker"
    persisted = video_localization_service.get_video_localization(project["project_id"])
    assert persisted is not None
    assert persisted.scene_context == "concurrent|worker"


def test_video_localization_auto_name_uses_localized_asr_and_research_context(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "待自动命名", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "scene_context": "创作者演示如何把真实 4K 素材与生成式特效融合。",
            "transcription": {
                "raw_text": "Raw ASR mentions seed ants effects.",
                "corrected_text": "Corrected ASR explains Seedance 2.0 visual effects.",
                "research": {
                    "status": "completed",
                    "provider": "web-search",
                    "sources": [
                        {
                            "source_id": "source_01",
                            "query_id": "query_01",
                            "title": "Seedance 2.0 官方产品资料",
                            "url": "https://example.com/seedance-2",
                            "snippet": "该资料说明模型支持 4K 视频特效工作流。",
                            "provider": "web-search",
                        }
                    ],
                },
            },
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "text": "我把真实拍摄素材和 Seedance 2.0 的 4K 特效融合在一起。",
                }
            ],
        },
    )
    assert saved.status_code == 200
    completion_calls: list[dict] = []

    def fake_complete_json(system_prompt, user_payload, **kwargs):
        completion_calls.append(
            {
                "system_prompt": system_prompt,
                "user_payload": user_payload,
                "kwargs": kwargs,
            }
        )
        return {"name": "Seedance 4K 实拍特效"}

    monkeypatch.setattr(video_localization_service.llm_runtime, "complete_json", fake_complete_json)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/auto-name")

    assert response.status_code == 200
    assert response.json()["name"] == "Seedance 4K 实拍特效"
    assert project_store.get_project(project["project_id"]).name == "Seedance 4K 实拍特效"
    assert len(completion_calls) == 1
    naming_context = json.dumps(completion_calls[0]["user_payload"], ensure_ascii=False)
    assert "我把真实拍摄素材和 Seedance 2.0 的 4K 特效融合在一起。" in naming_context
    assert "Corrected ASR explains Seedance 2.0 visual effects." in naming_context
    assert "Seedance 2.0 官方产品资料" in naming_context
    assert "该资料说明模型支持 4K 视频特效工作流。" in naming_context


def test_video_localization_auto_name_adds_next_numeric_suffix_for_duplicates(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.post("/api/projects", json={"name": "Seedance 4K 实拍特效", "description": ""})
    client.post("/api/projects", json={"name": "Seedance 4K 实拍特效 2", "description": ""})
    project = client.post("/api/projects", json={"name": "待命名项目", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "真实素材与生成式特效融合。",
                }
            ]
        },
    )
    assert saved.status_code == 200
    monkeypatch.setattr(
        video_localization_service.llm_runtime,
        "complete_json",
        lambda *args, **kwargs: {"name": "Seedance 4K 实拍特效"},
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/auto-name")

    assert response.status_code == 200
    assert response.json()["name"] == "Seedance 4K 实拍特效 3"
    assert project_store.get_project(project["project_id"]).name == "Seedance 4K 实拍特效 3"


def test_video_localization_auto_name_rejects_empty_llm_name(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "保留原项目名", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "transcription": {
                "raw_text": "A usable transcript for project naming.",
                "corrected_text": "A usable transcript for project naming.",
            }
        },
    )
    assert saved.status_code == 200
    monkeypatch.setattr(
        video_localization_service.llm_runtime,
        "complete_json",
        lambda *args, **kwargs: {"name": " \n\t "},
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/auto-name")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_NAME_INVALID"
    assert project_store.get_project(project["project_id"]).name == "保留原项目名"


def test_video_localization_source_video_can_be_played_after_import(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "播放视频", "description": ""}).json()
    client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/source-media/video")

    assert response.status_code == 200
    assert response.content == b"fake-video-bytes"


def test_video_localization_preview_video_falls_back_to_source(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "预览回退", "description": ""}).json()
    source_path = _project_root(project["project_id"]) / "source" / "demo.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-video")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"video_path": str(source_path)},
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/source-media/preview-video")

    assert response.status_code == 200
    assert response.content == b"source-video"
    assert "attachment" not in response.headers.get("content-disposition", "")


def test_video_localization_preview_video_supports_browser_range_requests(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "预览分段读取", "description": ""}).json()
    source_path = _project_root(project["project_id"]) / "source" / "demo.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"0123456789abcdef")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"video_path": str(source_path)},
        },
    )
    url = f"/api/projects/{project['project_id']}/video-localization/source-media/preview-video"

    first = client.get(url, headers={"Range": "bytes=0-3"})
    middle = client.get(url, headers={"Range": "bytes=6-9"})
    suffix = client.get(url, headers={"Range": "bytes=-4"})
    overflow = client.get(url, headers={"Range": "bytes=99-120"})

    assert (first.status_code, first.content, first.headers["content-range"]) == (206, b"0123", "bytes 0-3/16")
    assert (middle.status_code, middle.content, middle.headers["content-range"]) == (206, b"6789", "bytes 6-9/16")
    assert (suffix.status_code, suffix.content, suffix.headers["content-range"]) == (206, b"cdef", "bytes 12-15/16")
    assert overflow.status_code == 416
    assert overflow.headers["content-range"] == "bytes */16"


def test_video_localization_preview_video_legacy_variant_serves_source(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "预览文件固定", "description": ""}).json()
    project_id = project["project_id"]
    source_path = _project_root(project_id) / "source" / "demo.webm"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-video-bytes")
    client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"video_path": str(source_path)},
        },
    )
    url = f"/api/projects/{project_id}/video-localization/source-media/preview-video"

    source = client.get(f"{url}?variant=source", headers={"Range": "bytes=0-5"})
    preview = client.get(f"{url}?variant=preview", headers={"Range": "bytes=0-6"})

    assert (source.status_code, source.content) == (206, b"source")
    assert (preview.status_code, preview.content) == (206, b"source-")


def test_preview_video_prepare_returns_segmented_status_without_waiting_for_transcode(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "异步准备视频代理", "description": ""},
    ).json()
    project_id = project["project_id"]
    source_path = _project_root(project_id) / "source" / "demo.webm"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-video")
    client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": source_path.name,
                "video_path": str(source_path),
                "duration_ms": 10_000,
            },
        },
    )

    monkeypatch.setattr(
        video_localization_service.playback_proxy,
        "request_playback",
        lambda *_args, **_kwargs: {
            "contract_version": "video-playback-proxy-status-v2",
            "state": "building",
            "mode": "segmented",
            "variant": "segments",
            "playable": False,
            "profile": "segmented-h264-fmp4-v1",
            "retryable": False,
            "revision": "proxy-build-1",
            "duration_ms": 10_000,
            "segment_ms": 4_000,
            "requested_range": {"start_ms": 0, "end_ms": 10_000},
            "ready_ranges": [],
            "active_segment": 0,
            "ready_segments": 0,
            "total_segments": 3,
            "progress": 0.0,
            "updated_at": "2026-08-15T00:00:00Z",
            "error": None,
        },
    )
    response = client.post(f"/api/projects/{project_id}/video-localization/source-media/preview-video")

    assert response.status_code == 200
    assert response.json() == {
        "contract_version": "video-playback-proxy-status-v2",
        "state": "building",
        "mode": "segmented",
        "variant": "segments",
        "playable": False,
        "profile": "segmented-h264-fmp4-v1",
        "revision": "proxy-build-1",
        "duration_ms": 10_000,
        "segment_ms": 4_000,
        "requested_range": {"start_ms": 0, "end_ms": 10_000},
        "ready_ranges": [],
        "active_segment": 0,
        "ready_segments": 0,
        "total_segments": 3,
        "progress": 0.0,
        "updated_at": "2026-08-15T00:00:00Z",
        "retryable": False,
        "error": None,
    }


def test_preview_video_segment_endpoint_serves_immutable_ready_segment(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "分段预览读取", "description": ""},
    ).json()["project_id"]
    segment = tmp_path / "segment-00002.mp4"
    segment.write_bytes(b"fragmented-video")
    monkeypatch.setattr(
        video_localization_service,
        "source_playback_proxy_segment_file",
        lambda target_project_id, segment_index, revision: (
            segment
            if target_project_id == project_id and segment_index == 2 and revision == "source-revision"
            else None
        ),
    )

    response = client.get(
        f"/api/projects/{project_id}/video-localization/source-media/preview-video/segments/2?revision=source-revision",
        headers={"Range": "bytes=0-9"},
    )

    assert (response.status_code, response.content) == (206, b"fragmented")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_preview_video_prepare_returns_source_after_browser_capability_probe(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "源视频直接兼容", "description": ""},
    ).json()
    project_id = project["project_id"]
    source_path = _project_root(project_id) / "source" / "demo.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-video")
    client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": source_path.name,
                "video_path": str(source_path),
                "duration_ms": 10_000,
            },
        },
    )
    monkeypatch.setattr(
        video_localization_service.playback_proxy,
        "request_playback",
        lambda *_args, **_kwargs: {
            "contract_version": "video-playback-proxy-status-v2",
            "state": "ready",
            "mode": "source",
            "variant": "source",
            "playable": True,
            "profile": "source",
            "revision": "source-r1",
            "duration_ms": 10_000,
            "segment_ms": 4_000,
            "requested_range": {"start_ms": 0, "end_ms": 10_000},
            "ready_ranges": [{"start_ms": 0, "end_ms": 10_000}],
            "active_segment": None,
            "ready_segments": 3,
            "total_segments": 3,
            "progress": 1.0,
            "updated_at": None,
            "retryable": False,
            "error": None,
        },
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/source-media/preview-video",
        json={"source_playable": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "contract_version": "video-playback-proxy-status-v2",
        "state": "ready",
        "mode": "source",
        "variant": "source",
        "playable": True,
        "profile": "source",
        "revision": "source-r1",
        "duration_ms": 10_000,
        "segment_ms": 4_000,
        "requested_range": {"start_ms": 0, "end_ms": 10_000},
        "ready_ranges": [{"start_ms": 0, "end_ms": 10_000}],
        "active_segment": None,
        "ready_segments": 3,
        "total_segments": 3,
        "progress": 1.0,
        "updated_at": None,
        "retryable": False,
        "error": None,
    }


def test_video_localization_audio_variant_stays_on_one_file(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "音频文件固定", "description": ""}).json()
    project_id = project["project_id"]
    source_path = _project_root(project_id) / "audio" / "source.wav"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-audio-bytes")
    client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"audio_path": str(source_path)},
        },
    )
    proxy_path = media_assets.audio_preview_proxy_path(project_id, source_path)
    proxy_path.parent.mkdir(parents=True, exist_ok=True)
    proxy_path.write_bytes(b"preview-audio-bytes")
    url = f"/api/projects/{project_id}/video-localization/source-media/audio"

    source = client.get(f"{url}?variant=source", headers={"Range": "bytes=0-5"})
    preview = client.get(f"{url}?variant=preview", headers={"Range": "bytes=0-6"})

    assert (source.status_code, source.content) == (206, b"source")
    assert (preview.status_code, preview.content) == (206, b"preview")


def test_video_localization_media_paths_are_cached_and_invalidated_on_save(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "媒体路径缓存", "description": ""}).json()
    project_id = project["project_id"]
    source_path = _project_root(project_id) / "source" / "source.mp4"
    audio_path = _project_root(project_id) / "audio" / "source.wav"
    vocals_path = _project_root(project_id) / "stems" / "vocals.wav"
    for path in (source_path, audio_path, vocals_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"media")
    client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"video_path": str(source_path), "audio_path": str(audio_path)},
            "stems": {"vocals_clean_path": str(vocals_path)},
        },
    )

    original_get = video_localization_service.get_video_localization
    calls = 0

    def counted_get(target_project_id: str):
        nonlocal calls
        calls += 1
        return original_get(target_project_id)

    monkeypatch.setattr(video_localization_service, "get_video_localization", counted_get)

    assert video_localization_service.source_video_file(project_id) == source_path
    assert video_localization_service.source_audio_file(project_id) == audio_path
    assert video_localization_service.stem_audio_file(project_id, "vocals") == vocals_path
    assert video_localization_service.source_preview_video_file(project_id) == source_path
    assert calls == 1

    unchanged = original_get(project_id)
    assert unchanged is not None
    video_localization_service.save_video_localization(project_id, unchanged)
    calls = 0
    assert video_localization_service.source_video_file(project_id) == source_path
    assert calls == 0

    replacement = _project_root(project_id) / "source" / "replacement.mp4"
    replacement.write_bytes(b"replacement")
    draft = original_get(project_id)
    assert draft is not None
    updated = draft.model_copy(
        update={"source_media": draft.source_media.model_copy(update={"video_path": str(replacement)})}
    )
    video_localization_service.save_video_localization(project_id, updated)
    calls = 0

    assert video_localization_service.source_video_file(project_id) == replacement
    assert calls == 1


def test_video_localization_recovers_missing_managed_media_locators_by_fingerprint(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "重启后找回媒体", "description": ""},
    ).json()
    project_id = project["project_id"]
    package_root = _project_root(project_id)
    source_video = package_root / "source" / "lesson.mp4"
    source_audio = package_root / "audio" / "lesson.wav"
    vocals = package_root / "stems" / "vocals.wav"
    background = package_root / "stems" / "background.wav"
    for path, payload in (
        (source_video, b"managed-source-video"),
        (source_audio, b"managed-source-audio"),
        (vocals, b"managed-vocals"),
        (background, b"managed-background"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    assert client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": source_video.name,
                "content_sha256": media_assets.file_sha256(source_video),
                "audio_sha256": media_assets.file_sha256(source_audio),
            },
            "stems": {
                "separation_status": "completed",
                "original_audio_sha256": media_assets.file_sha256(source_audio),
                "vocals_clean_sha256": media_assets.file_sha256(vocals),
                "background_sha256": media_assets.file_sha256(background),
            },
        },
    ).status_code == 200

    # Simulate a fresh process: no remembered media locator may be required.
    media_assets.invalidate_project_media_paths(project_id)
    recovered = draft_store.get(project_id)

    assert recovered is not None
    assert recovered.source_media.video_path == str(source_video)
    assert recovered.source_media.audio_path == str(source_audio)
    assert recovered.stems.original_audio_path == str(source_audio)
    assert recovered.stems.vocals_clean_path == str(vocals)
    assert recovered.stems.background_path == str(background)
    assert video_localization_service.source_video_file(project_id) == source_video
    assert video_localization_service.source_audio_file(project_id) == source_audio
    assert video_localization_service.stem_audio_file(project_id, "vocals") == vocals
    assert video_localization_service.stem_audio_file(project_id, "background") == background

    # A read must not silently rewrite the durable project. Explicit repair or
    # the next normal content save is responsible for persisting recovered paths.
    stored = project_store.get_project(project_id)
    assert stored is not None
    stored_draft = stored.parameters["video_localization"]
    assert stored_draft["source_media"]["video_path"] is None
    assert stored_draft["source_media"]["audio_path"] is None
    assert stored_draft["stems"]["vocals_clean_path"] is None


def test_audio_preview_proxy_is_atomic_cached_and_preserves_master(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "音频预览代理", "description": ""}).json()
    source_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"pcm-master")
    calls: list[list[str]] = []
    monkeypatch.setattr(media_assets.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **_kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"aac-preview")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media_assets.subprocess, "run", fake_run)

    first = media_assets.ensure_audio_preview_proxy(project["project_id"], source_path)
    second = media_assets.ensure_audio_preview_proxy(project["project_id"], source_path)
    sibling_source = _project_root(project["project_id"]) / "alternate" / "vocals.wav"
    sibling_source.parent.mkdir(parents=True, exist_ok=True)
    sibling_source.write_bytes(b"alternate-pcm-master")
    sibling = media_assets.ensure_audio_preview_proxy(project["project_id"], sibling_source)

    assert first == second
    assert first.suffix == ".m4a"
    assert first.read_bytes() == b"aac-preview"
    assert source_path.read_bytes() == b"pcm-master"
    assert sibling != first
    assert sibling.exists()
    assert first.exists()
    assert len(calls) == 2
    assert calls[0][calls[0].index("-c:a") + 1] == "aac"
    assert calls[0][calls[0].index("-b:a") + 1] == "128k"
    assert not list(first.parent.glob("*.part.m4a"))


def test_clear_audio_preview_proxies_removes_only_browser_audio_cache(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "清理音频预览缓存", "description": ""}).json()
    project_id = project["project_id"]
    source_path = _project_root(project_id) / "stems" / "vocals.wav"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"pcm-master")
    proxy = media_assets.audio_preview_proxy_path(project_id, source_path)
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b"aac-preview")

    media_assets.clear_audio_preview_proxies(project_id)

    assert not proxy.exists()
    assert source_path.read_bytes() == b"pcm-master"


def test_refresh_preview_frames_keeps_existing_audio_proxies(tmp_path: Path, monkeypatch):
    _client(tmp_path)
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    audio_proxy = tmp_path / "preview.m4a"
    audio_proxy.write_bytes(b"audio-preview")
    monkeypatch.setattr(video_localization_service, "source_video_file", lambda _project_id: source_path)
    monkeypatch.setattr(
        video_localization_service.preview_cache,
        "refresh_cache",
        lambda project_id, path: {"state": "building", "project_id": project_id, "source_path": path},
    )
    monkeypatch.setattr(
        media_assets,
        "clear_audio_preview_proxies",
        lambda _project_id: (_ for _ in ()).throw(AssertionError("audio proxies must not be cleared")),
    )

    result = video_localization_service.refresh_source_preview_cache("project-refresh")

    assert result == {
        "state": "building",
        "project_id": "project-refresh",
        "source_path": source_path,
    }
    assert audio_proxy.read_bytes() == b"audio-preview"


def test_managed_reference_clip_is_recropped_when_cached_duration_drifted(tmp_path: Path):
    _client(tmp_path)
    source_path = tmp_path / "reference-source.wav"
    audio_tools.write_audio(source_path, np.full(10_000, 0.1, dtype=np.float32), 1_000)
    source = voice_store.ensure_managed_audio_file(
        str(source_path),
        file_id="managed-reference-source",
        original_name="reference-source.wav",
    )
    first = voice_store.create_audio_clip(source.file_id, 1_000, 4_000, clip_file_id="managed-reference-clip")
    clip_path = Path(first["path"])
    audio_tools.write_audio(clip_path, np.full(900, 0.2, dtype=np.float32), 1_000)

    rebuilt = voice_store.create_audio_clip(source.file_id, 1_000, 4_000, clip_file_id="managed-reference-clip")

    assert rebuilt["file_id"] == "managed-reference-clip"
    assert abs(int(audio_tools.probe_audio(rebuilt["path"])["duration_ms"]) - 3_000) <= 50


def test_video_localization_source_audio_endpoint_falls_back_to_original_stem(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "源音回退", "description": ""}).json()
    original_audio = _project_root(project["project_id"]) / "audio" / "fallback.wav"
    original_audio.parent.mkdir(parents=True, exist_ok=True)
    original_audio.write_bytes(b"fallback-audio")
    missing_audio = _project_root(project["project_id"]) / "audio" / "missing.wav"

    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(missing_audio)},
            "stems": {"original_audio_path": str(original_audio)},
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/source-media/audio")

    assert response.status_code == 200
    assert response.content == b"fallback-audio"


def test_video_localization_media_endpoints_reject_directories_as_files(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "目录不是媒体文件", "description": ""}).json()
    fake_video = _project_root(project["project_id"]) / "source" / "video.mp4"
    fake_audio = _project_root(project["project_id"]) / "audio" / "source.wav"
    fake_vocals = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    fake_background = _project_root(project["project_id"]) / "stems" / "background.wav"
    for path in (fake_video, fake_audio, fake_vocals, fake_background):
        path.mkdir(parents=True, exist_ok=True)

    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "video.mp4",
                "video_path": str(fake_video),
                "audio_path": str(fake_audio),
            },
            "stems": {
                "vocals_clean_path": str(fake_vocals),
                "background_path": str(fake_background),
            },
        },
    )

    endpoints = (
        "source-media/preview-video",
        "source-media/audio",
        "stems/vocals/audio",
        "stems/background/audio",
    )
    for endpoint in endpoints:
        response = client.get(f"/api/projects/{project['project_id']}/video-localization/{endpoint}")
        assert response.status_code == 404


def test_video_localization_media_original_timeline_waveform_uses_source_audio_at_high_density(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "原音波形", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 100
    duration_seconds = 665
    phase = np.linspace(0, np.pi * 40, sample_rate * duration_seconds, dtype=np.float32)
    stereo = np.column_stack((np.sin(phase), np.cos(phase)))
    sf.write(audio_path, stereo, sample_rate)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "audio_path": str(audio_path), "duration_ms": 665_000},
        },
    )

    audio = client.get(f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_original/audio")
    automatic = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_original/waveform"
    )
    explicit = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_original/waveform",
        params={"bins": 1501},
    )
    windowed = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_original/waveform",
        params={"bins": 64, "start_ms": 100_000, "end_ms": 110_000},
    )

    assert audio.status_code == 200
    assert automatic.status_code == 200
    assert automatic.json()["duration"] == 665.0
    assert automatic.json()["bins"] == 66_500
    assert len(automatic.json()["peaks"]) == 66_500
    assert explicit.status_code == 200
    assert explicit.json()["bins"] == 1501
    assert windowed.status_code == 200
    assert windowed.json()["duration"] == 665.0
    assert windowed.json()["window_start_ms"] == 100_000
    assert windowed.json()["window_end_ms"] == 110_000
    assert len(windowed.json()["peaks"]) == 64
    cache_files = list((tmp_path / "cache" / "waveforms").glob("*.json"))
    assert len(cache_files) == 3


def test_video_localization_media_waveforms_ignore_stale_timeline_audio_paths(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "媒体轨旧路径", "description": ""}).json()
    package_root = _project_root(project["project_id"])
    source_audio = package_root / "audio" / "current-source.wav"
    vocals_audio = package_root / "stems" / "current-vocals.wav"
    background_audio = package_root / "stems" / "current-background.wav"
    for path in (source_audio, vocals_audio, background_audio):
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, np.ones(400, dtype=np.float32) * 0.25, 1000)

    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {"filename": "source.mp4", "duration_ms": 400, "audio_path": str(source_audio)},
            "stems": {
                "vocals_clean_path": str(vocals_audio),
                "background_path": str(background_audio),
                "original_audio_path": str(source_audio),
            },
            "timeline_clips": [
                {"clip_id": "media_original", "track_id": "original", "audio_path": str(tmp_path / "old-source.wav")},
                {"clip_id": "media_vocals", "track_id": "vocals", "audio_path": str(tmp_path / "old-vocals.wav")},
                {
                    "clip_id": "media_background",
                    "track_id": "background",
                    "audio_path": str(tmp_path / "old-background.wav"),
                },
            ],
        },
    )
    assert saved.status_code == 200

    for clip_id in ("media_original", "media_vocals", "media_background"):
        response = client.get(
            f"/api/projects/{project['project_id']}/video-localization/timeline-clips/{clip_id}/waveform"
        )
        assert response.status_code == 200
        assert response.json()["duration"] == pytest.approx(0.4, abs=0.02)


def test_video_localization_timeline_waveform_defaults_to_minimum_bins_for_short_audio(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "短音频波形", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "tts" / "short.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(audio_path, np.ones(100, dtype=np.float32) * 0.25, 1000)
    _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{"clip_id": "clip_short", "track_id": "dub", "audio_path": str(audio_path)}],
        },
    )

    response = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/clip_short/waveform"
    )
    too_many_bins = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/clip_short/waveform",
        params={"bins": 180_001},
    )

    assert response.status_code == 200
    assert response.json()["duration"] == 0.1
    assert response.json()["bins"] == 32
    assert len(response.json()["peaks"]) == 32
    assert too_many_bins.status_code == 400


def test_video_localization_import_rejects_unsupported_media(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导入失败", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("notes.txt", b"not-video", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_UNSUPPORTED_MEDIA"


def test_video_localization_extract_source_audio_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "抽取音轨", "description": ""}).json()
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    ).json()
    assert imported["schema_version"] == "video-localization-mutation-ack-v1"
    imported_draft = video_localization_service.get_video_localization(project["project_id"])
    assert imported_draft is not None
    imported_video_path = Path(imported_draft.source_media.video_path or "")

    def fake_extract(video_path: Path, audio_path: Path) -> dict:
        assert video_path == imported_video_path
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-wav")
        return {"duration_ms": 1234, "sample_rate": 48000, "channels": 2, "size_bytes": 8}

    monkeypatch.setattr(media_assets, "extract_audio_file", fake_extract)

    updated = video_localization_service.extract_source_audio(project["project_id"])

    assert updated is not None
    body = updated.model_dump(mode="json")
    assert body["source_media"]["audio_path"].endswith("-source.wav")
    assert Path(body["source_media"]["audio_path"]).exists()
    assert body["source_media"]["duration_ms"] == 1234
    assert body["source_media"]["metadata"]["audio_sample_rate"] == 48000
    assert body["source_media"]["metadata"]["audio_channels"] == 2
    assert body["source_media"]["audio_sha256"] == media_assets.file_sha256(body["source_media"]["audio_path"])
    assert body["stems"]["original_audio_path"] == body["source_media"]["audio_path"]
    assert body["stems"]["original_audio_sha256"] == body["source_media"]["audio_sha256"]

    audio = client.get(f"/api/projects/{project['project_id']}/video-localization/source-media/audio")
    assert audio.status_code == 200
    assert audio.content == b"fake-wav"


def test_video_localization_extract_source_audio_requires_video(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺视频", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "source_audio", "parameters": {}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_MISSING"
    operations = client.get(f"/api/projects/{project['project_id']}/video-localization/operations").json()
    assert len(operations) == 1
    error_detail = operations[0]["result_summary"]["error_detail"]
    assert error_detail["rule_id"] == "source_media_prerequisite"
    assert "导入源视频" in error_detail["advice"][0]
    assert "ASR 字幕" not in error_detail["advice"][0]


def test_video_localization_extract_source_audio_rejects_stale_video_result(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "抽音频源变更保护", "description": ""}).json()
    source_dir = _project_root(project["project_id"]) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    original_video = source_dir / "original.mp4"
    replacement_video = source_dir / "replacement.mp4"
    original_video.write_bytes(b"original-video")
    replacement_video.write_bytes(b"replacement-video")
    initial = VideoLocalizationDraft(
        source_media={
            "filename": original_video.name,
            "video_path": str(original_video),
            "content_sha256": "original-revision",
            "duration_ms": 1000,
        }
    )
    video_localization_service.save_video_localization(project["project_id"], initial)
    stale_audio = _project_root(project["project_id"]) / "audio" / "stale-source.wav"

    def fake_extract(project_id: str, snapshot: VideoLocalizationDraft) -> VideoLocalizationDraft:
        stale_audio.parent.mkdir(parents=True, exist_ok=True)
        stale_audio.write_bytes(b"stale-audio")
        replacement = snapshot.model_copy(
            update={
                "source_media": snapshot.source_media.model_copy(
                    update={
                        "filename": replacement_video.name,
                        "video_path": str(replacement_video),
                        "content_sha256": "replacement-revision",
                        "audio_path": None,
                        "audio_sha256": None,
                    }
                )
            }
        )
        video_localization_service.save_video_localization(project_id, replacement)
        return snapshot.model_copy(
            update={
                "source_media": snapshot.source_media.model_copy(
                    update={"audio_path": str(stale_audio), "audio_sha256": "stale-audio-revision"}
                ),
                "stems": snapshot.stems.model_copy(
                    update={"original_audio_path": str(stale_audio), "original_audio_sha256": "stale-audio-revision"}
                ),
            }
        )

    monkeypatch.setattr(video_localization_source_pipeline, "with_extracted_source_audio", fake_extract)

    with pytest.raises(AppException) as exc_info:
        video_localization_service.extract_source_audio(project["project_id"])

    assert exc_info.value.code == "VIDEO_LOCALIZATION_SOURCE_CHANGED"
    saved = video_localization_service.get_video_localization(project["project_id"])
    assert saved is not None
    assert saved.source_media.video_path == str(replacement_video)
    assert saved.source_media.audio_path is None
    assert not stale_audio.exists()


def test_video_localization_async_source_audio_operation_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "异步抽音频", "description": ""}).json()
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    ).json()
    assert imported["schema_version"] == "video-localization-mutation-ack-v1"
    imported_draft = video_localization_service.get_video_localization(project["project_id"])
    assert imported_draft is not None
    imported_video_path = Path(imported_draft.source_media.video_path or "")

    def fake_extract(video_path: Path, audio_path: Path) -> dict:
        assert video_path == imported_video_path
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-wav")
        return {"duration_ms": 2345, "sample_rate": 44100, "channels": 1, "size_bytes": 8}

    monkeypatch.setattr(media_assets, "extract_audio_file", fake_extract)

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "source_audio"},
    )

    assert response.status_code == 200
    operation = response.json()
    assert operation["kind"] == "source_audio"
    assert operation["status"] in {"queued", "running", "success"}

    completed = None
    for _ in range(30):
        latest = client.get(
            f"/api/projects/{project['project_id']}/video-localization/operations/{operation['operation_id']}"
        ).json()
        if latest["status"] == "success":
            completed = latest
            break
        time.sleep(0.05)

    assert completed is not None
    assert completed["result_summary"]["duration_ms"] == 2345

    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["source_media"]["audio_path"] is None
    internal_draft = video_localization_service.get_video_localization(project["project_id"])
    assert internal_draft is not None
    assert (internal_draft.source_media.audio_path or "").endswith("-source.wav")
    assert Path(internal_draft.source_media.audio_path or "").exists()
    assert draft["source_media"]["metadata"]["audio_extract_status"] == "completed"
    assert draft["operations"][0]["status"] == "success"
    if video_localization_operation_queue._scheduler is not None:
        video_localization_operation_queue._scheduler.join()


def test_video_localization_async_operation_validates_prerequisites(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "异步缺源音", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "stems"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING"
    operations = client.get(f"/api/projects/{project['project_id']}/video-localization/operations").json()
    assert len(operations) == 1
    assert operations[0]["kind"] == "stems"
    assert operations[0]["status"] == "failed"
    assert operations[0]["error_code"] == "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING"
    assert operations[0]["result_summary"]["error_detail"]["stage"] == "提交任务"


def test_video_localization_dependency_failures_offer_runtime_guidance():
    separation = video_localization_operation_queue._fallback_error_detail(
        "STEM_SEPARATION_MODEL_NOT_INSTALLED",
        "BS-RoFormer model is not installed",
    )
    asr = video_localization_operation_queue._fallback_error_detail(
        "QWEN3_ASR_RUNTIME_MISSING",
        "mlx-audio runtime is unavailable",
    )

    assert "引擎管理" in separation["advice"][0]
    assert "BS-RoFormer" in separation["advice"][0]
    assert "项目虚拟环境" in asr["advice"][0]
    assert "ASR" in asr["advice"][0]


def test_video_localization_source_audio_prerequisite_uses_media_guidance():
    detail = video_localization_operation_queue._fallback_error_detail(
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
        "Extract source audio before separating stems",
        "提交任务",
    )

    assert detail["rule_id"] == "source_media_prerequisite"
    assert "抽取原音轨" in detail["advice"][0]
    assert "ASR 字幕" not in detail["advice"][0]


def test_video_localization_operation_summaries_keep_live_preview_and_lazy_load_terminal_detail(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "轻量任务轮询", "description": ""}).json()
    active = VideoLocalizationOperation(
        operation_id="running_localization",
        project_id=project["project_id"],
        kind="localization_draft",
        status="running",
        progress=0.75,
        result_summary={
            "stage": "正在盲测中文自然度",
            "stage_id": "review_localization_naturalness",
            "task_stage_timings": {
                "review_localization_naturalness": {
                    "duration_ms": 1200,
                    "running": True,
                }
            },
            "preview_phase": "localized_review",
            "preview_cues": [{"subtitle_id": "localized_0001", "text": "实时预览"}],
            "task_step_results": {
                "lock_localization_source": {
                    "status": "success",
                    "summary": "已锁定本土化源输入",
                    "metrics": [],
                    "sections": [],
                    "notes": [],
                },
                "review_localization_naturalness": {
                    "status": "running",
                    "summary": "正在盲测中文自然度",
                    "metrics": [{"label": "当前批次", "value": "1"}],
                    "sections": [{"title": "问题", "items": [{"id": index} for index in range(20)]}],
                    "notes": [],
                    "coverage": {
                        "mode": "complete",
                        "shown_count": 20,
                        "total_count": 20,
                        "unit": "个问题",
                    },
                },
            },
        },
    )
    history = VideoLocalizationOperation(
        operation_id="completed_localization",
        project_id=project["project_id"],
        kind="localization_draft",
        status="success",
        label="生成本土化字幕",
        result_summary={
            "stage": "已完成",
            "localized_subtitle_count": 160,
            "preview_cues": [{"subtitle_id": "old_preview", "text": "旧预览"}],
            "task_step_results": {
                "validate_localization_tracks": {
                    "status": "success",
                    "summary": "本土化双轨质量门通过",
                    "sections": [{"items": [{"id": index} for index in range(20)]}],
                }
            },
        },
    )
    history = history.model_copy(
        update={
            "result_summary": {
                **history.result_summary,
                "task_final_result": video_localization_operation_queue._build_task_final_result(
                    history,
                    history.result_summary,
                ),
            }
        }
    )
    failed = VideoLocalizationOperation(
        operation_id="failed_asr",
        project_id=project["project_id"],
        kind="english_asr",
        status="failed",
        error_code="VIDEO_LOCALIZATION_TRANSCRIPT_FINAL_QUALITY_UNRESOLVED",
        error_message="整篇终审未通过",
        result_summary={
            "stage": "正在整篇 ASR 终审",
            "stage_id": "transcript_quality_gate",
            "error_detail": {
                "code": "VIDEO_LOCALIZATION_TRANSCRIPT_FINAL_QUALITY_UNRESOLVED",
                "message": "整篇终审未通过",
                "issues": [{"segment_ids": ["asr_0001"]}],
            },
            "task_step_results": {
                "transcript_quality_gate": {
                    "status": "failed",
                    "summary": "关键问题未解决",
                    "error_detail": {"code": "VIDEO_LOCALIZATION_TRANSCRIPT_FINAL_QUALITY_UNRESOLVED"},
                    "sections": [{"items": [{"id": index} for index in range(20)]}],
                }
            },
        },
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert (
        video_localization_service.save_video_localization(
            project["project_id"],
            draft.model_copy(update={"operations": [active, history, failed]}),
        )
        is not None
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/operations/feed-v2")

    assert response.status_code == 200
    assert len(response.content) < 18_000
    payload = response.json()
    by_id = {
        item["operation_id"]: item
        for item in [
            *payload["active_operations"],
            *payload["history"],
        ]
    }
    assert by_id["running_localization"]["detail_available"] is True
    assert by_id["running_localization"]["result_summary"]["preview_cues"][0]["text"] == "实时预览"
    live_steps = by_id["running_localization"]["result_summary"]["task_step_results"]
    assert set(live_steps) == {
        "lock_localization_source",
        "review_localization_naturalness",
    }
    live_result = live_steps["review_localization_naturalness"]
    assert live_result["summary"] == "正在盲测中文自然度"
    assert len(live_result["sections"][0]["items"]) == 8
    assert live_result["coverage"] == {
        "mode": "focused",
        "shown_count": 8,
        "total_count": 20,
        "unit": "个问题",
        "reason": "任务轮询摘要为控制体积仅返回部分明细，完整结果仍保存在任务记录中。",
        "truncated": True,
    }
    assert "preview_cues" not in by_id["completed_localization"]["result_summary"]
    assert by_id["completed_localization"]["result_summary"]["localized_subtitle_count"] == 160
    assert by_id["completed_localization"]["detail_available"] is True
    assert "task_step_results" not in by_id["completed_localization"]["result_summary"]
    assert "task_stage_groups" not in by_id["completed_localization"]["result_summary"]
    assert "task_final_result" not in by_id["completed_localization"]["result_summary"]
    failed_summary = by_id["failed_asr"]["result_summary"]
    assert by_id["failed_asr"]["detail_available"] is True
    assert "error_detail" not in failed_summary
    assert "task_step_results" not in failed_summary

    history_detail_response = client.get(
        f"/api/projects/{project['project_id']}/video-localization/operations/completed_localization"
    )
    assert history_detail_response.status_code == 200
    history_detail = history_detail_response.json()["result_summary"]
    history_result = history_detail["task_step_results"]["validate_localization_tracks"]
    assert history_result["summary"] == "本土化双轨质量门通过"
    assert len(history_result["sections"][0]["items"]) == 20
    history_final = history_detail["task_final_result"]
    assert history_final["status"] == "success"
    assert history_final["purpose"] == "生成本土化字幕"
    assert history_final["summary"] == (
        "生成本土化字幕已完成，生成 160 条本土化字幕；共执行 1 个步骤，全部步骤正常完成。"
    )
    assert history_final["sections"][0]["items"][0]["title"] == "检查本土化结果"
    assert history_final["coverage"] == {
        "mode": "complete",
        "shown_count": 1,
        "total_count": 1,
        "unit": "个任务步骤",
    }
    failed_detail_response = client.get(
        f"/api/projects/{project['project_id']}/video-localization/operations/failed_asr"
    )
    assert failed_detail_response.status_code == 200
    failed_detail = failed_detail_response.json()["result_summary"]
    assert failed_detail["error_detail"]["issues"][0]["segment_ids"] == ["asr_0001"]
    assert failed_detail["task_step_results"]["transcript_quality_gate"]["status"] == "failed"


def test_video_localization_compact_step_result_reports_omitted_sections():
    result = video_localization_operation_queue._compact_live_step_result(
        {
            "label": "理解全文并规划复查",
            "order": 30,
            "status": "success",
            "summary": "完整步骤结果",
            "sections": [
                {"title": f"分组 {index}", "items": [{"id": index * 10 + item} for item in range(2)]}
                for index in range(4)
            ],
            "coverage": {
                "mode": "complete",
                "shown_count": 8,
                "total_count": 8,
                "unit": "项",
            },
        }
    )

    assert result["label"] == "理解全文并规划复查"
    assert result["order"] == 30
    assert len(result["sections"]) == 3
    assert result["coverage"] == {
        "mode": "focused",
        "shown_count": 6,
        "total_count": 8,
        "unit": "项",
        "reason": "任务轮询摘要为控制体积仅返回部分明细，完整结果仍保存在任务记录中。",
        "truncated": True,
    }


def test_video_localization_task_final_result_uses_asr_artifacts_and_readable_step_labels():
    operation = VideoLocalizationOperation(
        operation_id="completed_asr_summary",
        project_id="project_001",
        kind="english_asr",
        status="success",
        label="听写字幕",
    )
    result = video_localization_operation_queue._build_task_final_result(
        operation,
        {
            "cue_count": 12,
            "segment_count": 15,
            "task_duration_ms": 219169,
            "task_step_results": {
                "asr": {"status": "success", "summary": "识别完成"},
                "boundary_review": {"status": "warning", "summary": "部分断句建议抽查"},
                "custom_check": {"status": "failed", "summary": "自定义检查失败"},
            },
        },
    )

    assert result["status"] == "warning"
    assert result["summary"] == "听写字幕已完成，生成 12 条 ASR 字幕；共执行 3 个步骤，1 个步骤失败，1 个步骤有建议。"
    assert {item["label"]: item["value"] for item in result["metrics"]} == {
        "已执行步骤": "3",
        "有建议的步骤": "1",
        "失败的步骤": "1",
        "ASR 字幕": "12",
        "识别片段": "15",
        "任务耗时": "3 分 39 秒",
    }
    assert [item["title"] for section in result["sections"] for item in section["items"]] == [
        "生成原始听写",
        "本地确定字幕断句",
        "custom_check",
    ]
    assert [item["meta"] for section in result["sections"] for item in section["items"]] == [
        "已完成",
        "需复核",
        "失败",
    ]


def test_video_localization_task_final_result_uses_dynamic_flow_order_and_labels():
    operation = VideoLocalizationOperation(
        operation_id="completed_localization_v3",
        project_id="project_001",
        kind="localization_draft",
        status="success",
        label="生成本土化字幕",
    )

    result = video_localization_operation_queue._build_task_final_result(
        operation,
        {
            "localized_subtitle_count": 18,
            "task_step_results": {
                "review_localization_naturalness": {
                    "label": "盲测中文自然度",
                    "order": 71,
                    "status": "warning",
                    "summary": "中文自然度有一处建议。",
                },
                "lock_localization_source": {
                    "label": "固定本次英文源数据",
                    "order": 10,
                    "status": "success",
                    "summary": "输入已锁定。",
                },
                "generate_localization_spoken_script": {
                    "label": "生成全文本土化初稿",
                    "order": 60,
                    "status": "success",
                    "summary": "全文本土化初稿已完成。",
                },
            },
        },
    )

    assert [item["title"] for section in result["sections"] for item in section["items"]] == [
        "固定本次英文源数据",
        "生成全文本土化初稿",
        "盲测中文自然度",
    ]
    assert result["status"] == "warning"


def test_video_localization_cancel_queued_operation_marks_cancelled(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消后台任务", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"], kind="english_asr", status="queued", label="英文 ASR 转字幕"
    )
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "operations": [operation.model_dump()],
        },
    )
    save_calls = 0
    original_save = video_localization_service.draft_store.save

    def counted_save(project_id, draft, **kwargs):
        nonlocal save_calls
        save_calls += 1
        return original_save(project_id, draft, **kwargs)

    monkeypatch.setattr(video_localization_service.draft_store, "save", counted_save)

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/{operation.operation_id}/cancel"
    )

    assert response.status_code == 200
    assert save_calls == 1
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancel_requested"] is True
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["operations"][0]["status"] == "cancelled"
    assert draft["source_media"]["metadata"]["english_asr_status"] == "cancelled"


def test_video_localization_cancel_preserves_completed_steps_and_closes_unfinished_steps(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消保留步骤结果", "description": ""}).json()
    step_results = {
        "asr": {
            "status": "success",
            "summary": "原始讲话已经转写完成。",
            "metrics": [],
            "sections": [],
            "notes": [],
        },
        "transcript_quality_gate": {
            "status": "running",
            "summary": "正在通读整篇转写。",
            "metrics": [],
            "sections": [],
            "notes": [],
        },
        "alignment": {
            "status": "todo",
            "summary": "等待前置步骤完成。",
            "metrics": [],
            "sections": [],
            "notes": [],
        },
    }
    operation = VideoLocalizationOperation(
        operation_id="cancel_preserves_step_results",
        project_id=project["project_id"],
        kind="english_asr",
        status="running",
        progress=0.55,
        result_summary={
            "stage": "正在整篇 ASR 终审",
            "stage_id": "transcript_quality_gate",
            "task_step_results": step_results,
        },
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert (
        video_localization_service.save_video_localization(
            project["project_id"],
            draft.model_copy(update={"operations": [operation]}),
        )
        is not None
    )

    cancelled = video_localization_operation_queue.cancel(project["project_id"], operation.operation_id)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.result_summary["task_step_results"]["asr"] == (step_results["asr"])
    assert cancelled.result_summary["task_step_results"]["transcript_quality_gate"] == {
        **step_results["transcript_quality_gate"],
        "status": "cancelled",
        "summary": "任务已取消，此步骤未完成。",
    }
    assert cancelled.result_summary["task_step_results"]["alignment"] == {
        **step_results["alignment"],
        "status": "cancelled",
        "summary": "任务已取消，此步骤未执行。",
    }
    persisted = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert persisted is not None
    assert persisted.result_summary["task_step_results"] == cancelled.result_summary["task_step_results"]


def test_video_localization_retry_failed_operation_creates_new_operation(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重试后台任务", "description": ""}).json()
    monkeypatch.setattr(video_localization_operation_queue, "_enqueue", lambda operation_id: None)
    video_path = _project_root(project["project_id"]) / "source" / "demo.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fake-video")
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="source_audio",
        status="failed",
        label="抽取源音轨",
        error_code="VIDEO_LOCALIZATION_OPERATION_FAILED",
        error_message="previous failure",
    )
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "demo.mp4",
                "video_path": str(video_path),
                "metadata": {
                    "audio_extract_status": "failed",
                    "audio_extract_error_code": "VIDEO_LOCALIZATION_OPERATION_FAILED",
                    "audio_extract_error": "previous failure",
                },
            },
            "operations": [operation.model_dump()],
        },
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/{operation.operation_id}/retry"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] != operation.operation_id
    assert body["kind"] == "source_audio"
    assert body["status"] == "queued"
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert len(draft["operations"]) == 2
    assert draft["source_media"]["metadata"]["audio_extract_status"] == "queued"
    assert "audio_extract_error_code" not in draft["source_media"]["metadata"]
    assert "audio_extract_error" not in draft["source_media"]["metadata"]


def test_video_localization_running_cancel_keeps_cancelled_after_late_exception(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "运行中取消", "description": ""}).json()
    video_path = _project_root(project["project_id"]) / "source" / "demo.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fake-video")
    operation = VideoLocalizationOperation(
        project_id=project["project_id"], kind="source_audio", status="queued", label="抽取源音轨"
    )
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "video_path": str(video_path)},
            "operations": [operation.model_dump()],
        },
    )

    def fake_extract(project_id: str, **_kwargs):
        video_localization_operation_queue.cancel(project_id, operation.operation_id)
        raise RuntimeError("late extractor failure")

    monkeypatch.setattr(video_localization_service, "extract_source_audio", fake_extract)

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["operations"][0]["status"] == "cancelled"
    assert draft["operations"][0]["cancel_requested"] is True
    assert draft["source_media"]["metadata"]["audio_extract_status"] == "cancelled"
    assert "audio_extract_error_code" not in draft["source_media"]["metadata"]


def test_video_localization_separate_source_audio_requires_source_audio(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺源音分离", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "stems", "parameters": {}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING"


def test_video_localization_separate_source_audio_updates_stems(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "分离人声", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-wav")
    stale_vocals = _project_root(project["project_id"]) / "stems" / "source-vocals-clean-old.wav"
    stale_background = _project_root(project["project_id"]) / "stems" / "source-background-old.wav"
    stale_vocals.parent.mkdir(parents=True, exist_ok=True)
    stale_vocals.write_bytes(b"old-vocals")
    stale_background.write_bytes(b"old-background")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(audio_path), "duration_ms": 4200},
        },
    )

    def fake_separate(source_audio: Path, stems_dir: Path) -> dict:
        assert source_audio == audio_path
        # Simulate frontend autosave while the long-running separator is busy.
        concurrent = video_localization_service.update_video_localization_ui_state(
            project["project_id"], {"timeline_zoom": 6, "sidebar_collapsed": True}
        )
        assert concurrent is not None
        vocals = stems_dir / "source-vocals-clean.wav"
        background = stems_dir / "source-background.wav"
        vocals.parent.mkdir(parents=True, exist_ok=True)
        vocals.write_bytes(b"vocals")
        background.write_bytes(b"background")
        return {
            "vocals_clean_path": vocals,
            "background_path": background,
            "engine_id": "bs-roformer-viperx-1297:residual-v1",
            "quality_flags": ["needs_reference_review"],
        }

    monkeypatch.setattr(media_assets, "separate_audio_file", fake_separate)

    updated = video_localization_service.separate_source_audio(project["project_id"])

    assert updated is not None
    body = updated.model_dump(mode="json")
    assert body["stems"]["separation_status"] == "completed"
    assert body["stems"]["separation_engine_id"] == "bs-roformer-viperx-1297:residual-v1"
    assert body["stems"]["vocals_clean_path"].endswith("source-vocals-clean.wav")
    assert body["stems"]["background_path"].endswith("source-background.wav")
    assert body["stems"]["original_audio_path"] == str(audio_path)
    assert body["stems"]["quality_flags"] == ["needs_reference_review"]
    assert body["stems"]["vocals_clean_sha256"] == media_assets.file_sha256(body["stems"]["vocals_clean_path"])
    assert body["stems"]["background_sha256"] == media_assets.file_sha256(body["stems"]["background_path"])
    assert body["ui_state"]["timeline_zoom"] == 6
    assert body["ui_state"]["sidebar_collapsed"] is True
    assert not stale_vocals.exists()
    assert not stale_background.exists()

    vocals = client.get(f"/api/projects/{project['project_id']}/video-localization/stems/vocals/audio")
    background = client.get(f"/api/projects/{project['project_id']}/video-localization/stems/background/audio")
    assert vocals.status_code == 200
    assert vocals.content == b"vocals"
    assert background.status_code == 200
    assert background.content == b"background"

    # Canonical media clips exist client-side before their autosave reaches the server.
    # Their audio routes must still resolve directly from the completed stem fields.
    vocals_clip = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_vocals/audio"
    )
    background_clip = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_background/audio"
    )
    assert vocals_clip.status_code == 200
    assert vocals_clip.content == b"vocals"
    assert background_clip.status_code == 200
    assert background_clip.content == b"background"


def test_video_localization_separate_source_audio_rejects_stale_audio_result(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "分离源变更保护", "description": ""}).json()
    audio_dir = _project_root(project["project_id"]) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    original_audio = audio_dir / "original.wav"
    replacement_audio = audio_dir / "replacement.wav"
    original_audio.write_bytes(b"original-audio")
    replacement_audio.write_bytes(b"replacement-audio")
    initial = VideoLocalizationDraft(
        source_media={
            "filename": "demo.mp4",
            "audio_path": str(original_audio),
            "audio_sha256": "original-revision",
        },
        stems={"original_audio_path": str(original_audio), "original_audio_sha256": "original-revision"},
    )
    video_localization_service.save_video_localization(project["project_id"], initial)
    stale_vocals = _project_root(project["project_id"]) / "stems" / "stale-vocals.wav"
    stale_background = _project_root(project["project_id"]) / "stems" / "stale-background.wav"

    def fake_separate(project_id: str, snapshot: VideoLocalizationDraft) -> VideoLocalizationDraft:
        stale_vocals.parent.mkdir(parents=True, exist_ok=True)
        stale_vocals.write_bytes(b"stale-vocals")
        stale_background.write_bytes(b"stale-background")
        replacement = snapshot.model_copy(
            update={
                "source_media": snapshot.source_media.model_copy(
                    update={"audio_path": str(replacement_audio), "audio_sha256": "replacement-revision"}
                ),
                "stems": snapshot.stems.model_copy(
                    update={
                        "original_audio_path": str(replacement_audio),
                        "original_audio_sha256": "replacement-revision",
                    }
                ),
            }
        )
        video_localization_service.save_video_localization(project_id, replacement)
        return snapshot.model_copy(
            update={
                "stems": snapshot.stems.model_copy(
                    update={
                        "vocals_clean_path": str(stale_vocals),
                        "background_path": str(stale_background),
                        "separation_status": "completed",
                    }
                )
            }
        )

    monkeypatch.setattr(video_localization_source_pipeline, "with_separated_source_audio", fake_separate)

    with pytest.raises(AppException) as exc_info:
        video_localization_service.separate_source_audio(project["project_id"])

    assert exc_info.value.code == "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED"
    saved = video_localization_service.get_video_localization(project["project_id"])
    assert saved is not None
    assert saved.source_media.audio_path == str(replacement_audio)
    assert saved.stems.original_audio_path == str(replacement_audio)
    assert saved.stems.vocals_clean_path is None
    assert saved.stems.background_path is None
    assert not stale_vocals.exists()
    assert not stale_background.exists()


def test_video_localization_separate_audio_file_writes_bs_roformer_outputs(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "audio" / "source.wav"
    stems_dir = tmp_path / "stems"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"placeholder")

    def fake_separate(
        source_audio,
        vocals_path,
        background_path,
        **_kwargs,
    ):
        audio_tools.write_audio(
            vocals_path,
            np.full((8, 2), 0.25, dtype=np.float32),
            48_000,
        )
        audio_tools.write_audio(
            background_path,
            np.full((8, 2), -0.25, dtype=np.float32),
            48_000,
        )
        return {"engine_id": "bs-roformer-viperx-1297:residual-v1"}

    monkeypatch.setattr(
        media_assets.stem_separation_engine,
        "separate",
        fake_separate,
    )
    monkeypatch.setattr(
        audio_tools, "quality_metrics", lambda path, min_duration_ms=1000: {"warnings": ["needs_reference_review"]}
    )

    result = media_assets.separate_audio_file(audio_path, stems_dir)

    assert result["engine_id"] == "bs-roformer-viperx-1297:residual-v1"
    assert result["quality_flags"] == ["needs_reference_review"]
    assert Path(result["vocals_clean_path"]).exists()
    assert Path(result["background_path"]).exists()
    vocals_audio, vocals_sr = audio_tools.read_audio(result["vocals_clean_path"])
    background_audio, background_sr = audio_tools.read_audio(result["background_path"])
    assert vocals_sr == 48_000
    assert background_sr == 48_000
    assert vocals_audio.size > 0
    assert background_audio.size > 0


def test_video_localization_english_asr_requires_clean_vocals_by_default(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺源音", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/english-asr",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ("VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING")


def test_video_localization_english_asr_creates_cue_draft(tmp_path: Path, monkeypatch):
    _configure_asr_review_model(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "英文转录", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-wav")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(audio_path), "duration_ms": 4200},
        },
    )

    def fake_transcribe(*, engine_id: str, audio_path: str, language: str):
        assert engine_id == "qwen3-asr-mlx"
        assert Path(audio_path).name == "source.wav"
        assert language == "auto"
        return {
            "text": "We shipped the first localization pass.",
            "segments": [
                {"start_ms": 0, "end_ms": 1850, "text": "We shipped", "language": "en"},
                {"start_ms": 1850, "end_ms": 4200, "text": "the first localization pass.", "language": "en"},
            ],
        }

    monkeypatch.setattr(video_localization_source_pipeline.asr_service, "transcribe", fake_transcribe)

    updated = video_localization_service.transcribe_english_source_audio(
        project["project_id"],
        source_track_id="original",
    )

    assert updated is not None
    body = updated.model_dump(mode="json")
    assert body["source_media"]["metadata"]["english_asr_status"] == "completed"
    assert body["source_media"]["metadata"]["english_asr_engine_id"] == "qwen3-asr-mlx"
    assert body["source_media"]["metadata"]["english_asr_source_track_id"] == "original"
    assert body["source_media"]["metadata"]["english_asr_segment_count"] == 1
    assert body["status"] == "blocked"
    assert [cue["en_subtitle_text"] for cue in body["cues"]] == ["We shipped the first localization pass"]
    assert body["cues"][0]["start_ms"] == 0
    assert body["cues"][0]["end_ms"] == 4200
    assert body["transcription"]["pipeline_timing"]["stages"]["subtitle_track"]["cue_count"] == 1
    assert "generated_by_asr" in body["cues"][0]["quality_flags"]
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert "CUE_SPEAKER_MISSING" not in blocker_codes
    assert "ZH_SUBTITLE_MISSING" not in blocker_codes
    assert "TTS_TEXT_MISSING" not in blocker_codes


@pytest.mark.parametrize("edit_source_cue", [False, True])
def test_video_localization_asr_preserves_concurrent_edits(tmp_path: Path, monkeypatch, edit_source_cue):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 自动保存竞态", "description": ""}).json()
    initial = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]),
    )
    assert initial is not None

    def fake_asr(draft, engine_id, source_track_id, **_kwargs):
        latest = video_localization_service.get_video_localization(project["project_id"])
        assert latest is not None
        manual_cue = VideoLocalizationCue(
            cue_id="cue_0001",
            start_ms=1500,
            end_ms=2500,
            en_subtitle_text="Manual edit made while ASR was running.",
            review_status="ready",
        )
        autosaved = video_localization_service.save_video_localization(
            project["project_id"],
            latest.model_copy(
                update={
                    "ui_state": {"timeline_zoom": 7, "sidebar_collapsed": True},
                    "cues": [manual_cue] if edit_source_cue else latest.cues,
                }
            ),
        )
        assert autosaved is not None
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    if edit_source_cue:
        with pytest.raises(AppException) as exc_info:
            video_localization_service.transcribe_english_source_audio(project["project_id"])
        assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_TARGET_CHANGED"
        saved = video_localization_service.get_video_localization(project["project_id"])
        assert saved.cues[0].en_subtitle_text == "Manual edit made while ASR was running."
        assert saved.transcription is None
        assert saved.ui_state["timeline_zoom"] == 7
        return

    updated = video_localization_service.transcribe_english_source_audio(project["project_id"])

    assert updated is not None
    assert updated.ui_state == {"timeline_zoom": 7, "sidebar_collapsed": True}
    assert [cue.cue_id for cue in updated.cues] == ["cue_0001"]
    assert updated.cues[0].en_subtitle_text == "Concurrent ASR result"
    assert updated.source_media.metadata["english_asr_engine_id"] == "qwen3-asr-mlx"


def test_video_localization_asr_rejects_result_after_source_changes(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 来源竞态", "description": ""}).json()
    initial = video_localization_service.get_video_localization(project["project_id"])
    assert initial is not None
    initial = video_localization_service.save_video_localization(
        project["project_id"],
        initial.model_copy(
            update={
                "source_media": initial.source_media.model_copy(
                    update={"audio_path": "project://audio/source-v1.wav", "audio_sha256": "source-v1"}
                )
            }
        ),
    )
    assert initial is not None

    def fake_asr(draft, engine_id, source_track_id, **_kwargs):
        latest = video_localization_service.get_video_localization(project["project_id"])
        assert latest is not None
        changed = video_localization_service.save_video_localization(
            project["project_id"],
            latest.model_copy(
                update={
                    "source_media": latest.source_media.model_copy(
                        update={"audio_path": "project://audio/source-v2.wav", "audio_sha256": "source-v2"}
                    )
                }
            ),
        )
        assert changed is not None
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    with pytest.raises(AppException) as exc_info:
        video_localization_service.transcribe_english_source_audio(project["project_id"])

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_SOURCE_CHANGED"
    latest = video_localization_service.get_video_localization(project["project_id"])
    assert latest is not None
    assert latest.source_media.audio_path == "project://audio/source-v2.wav"
    assert latest.transcription is None
    assert latest.cues == []


@pytest.mark.parametrize("failure", ["none", "before_save", "after_save"])
def test_asr_content_and_terminal_operation_share_commit(tmp_path: Path, monkeypatch, failure):
    client = _client(tmp_path)
    project_id = client.post("/api/projects", json={"name": "ASR atomic completion"}).json()["project_id"]
    operation = VideoLocalizationOperation(
        operation_id="asr-atomic", project_id=project_id, kind="english_asr", status="running",
        parameters={"execution_mode": "full"},
    )
    initial = video_localization_service.get_video_localization(project_id)
    video_localization_service.save_video_localization(
        project_id, initial.model_copy(update={"operations": [operation]}),
    )
    monkeypatch.setattr(
        video_localization_source_pipeline, "with_english_asr",
        lambda draft, engine_id, *_args, **_kwargs: _completed_asr_result(draft, engine_id),
    )
    original_save = video_localization_service.draft_store.save

    def injected_save(*args, **kwargs):
        if failure == "before_save":
            raise RuntimeError("before database commit")
        saved = original_save(*args, **kwargs)
        if failure == "after_save":
            raise RuntimeError("after database commit")
        return saved

    monkeypatch.setattr(video_localization_service.draft_store, "save", injected_save)

    def run():
        return video_localization_service.transcribe_english_source_audio(
            project_id, operation_id=operation.operation_id,
            finalize_formal_commit=lambda draft, summary, completed_at: video_localization_operation_queue._finalize_formal_operation(
                draft, operation.operation_id, summary, completed_at,
            ),
        )

    if failure == "none":
        run()
    else:
        with pytest.raises(RuntimeError):
            run()
    refreshed = video_localization_service.get_video_localization(project_id)
    history = video_localization_operation_queue.get_operation(project_id, operation.operation_id)
    if failure == "before_save":
        assert refreshed.transcription is None
        assert history.status == "running"
    else:
        assert refreshed.transcription is not None
        assert refreshed.cues[0].en_subtitle_text == "Concurrent ASR result"
        assert history.status == "success"
        assert history.result_summary["task_final_result"]["status"] == "success"


def test_video_localization_operation_progress_merges_into_latest_draft(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "进度原子合并", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="running",
        label="听写字幕",
    )
    saved = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]).model_copy(
            update={"operations": [operation]}
        ),
    )
    assert saved is not None
    updated_ui = video_localization_service.update_video_localization_ui_state(
        project["project_id"],
        {"timeline_zoom": 9, "sidebar_collapsed": True},
    )
    assert updated_ui is not None

    video_localization_operation_queue._mark_operation(
        project["project_id"],
        operation.operation_id,
        kind="english_asr",
        status="running",
        progress=0.58,
        result_summary={"stage": "正在生成逐词时间码"},
    )

    latest = video_localization_service.get_video_localization(project["project_id"])
    assert latest is not None
    assert latest.ui_state == {"timeline_zoom": 9, "sidebar_collapsed": True}
    latest_operation = next(item for item in latest.operations if item.operation_id == operation.operation_id)
    assert latest_operation.progress == 0.58
    assert latest_operation.result_summary == {"stage": "正在生成逐词时间码"}


def test_asr_stage_timer_records_non_overlapping_step_durations():
    now = [10.0]
    timer = video_localization_operation_queue._AsrStageTimer(clock=lambda: now[0])

    now[0] = 12.5
    timer.update("正在判断是否需要联网核验")
    now[0] = 13.0
    timer.update("正在校对识别文本")
    now[0] = 16.25
    timings = timer.finish()

    assert timings == {
        "asr": {"duration_ms": 2500},
        "web_research": {"duration_ms": 500},
        "text_review": {"duration_ms": 3250},
    }
    assert sum(item["duration_ms"] for item in timings.values()) == 6250


def test_finish_stage_timings_prefers_recorded_atomic_durations_and_keeps_wall_total():
    now = [10.0]
    timer = video_localization_operation_queue._AsrStageTimer(clock=lambda: now[0])
    now[0] = 15.0

    summary = video_localization_operation_queue._finish_stage_timings(
        timer,
        {
            "duration_ms": 5000,
            "stage_timings": {
                "asr": {"duration_ms": 3200},
                "diarization": {"duration_ms": 4100},
                "initial_analysis_join": {"duration_ms": 3},
            },
        },
    )

    assert summary["task_stage_timings"] == {
        "asr": {"duration_ms": 3200},
        "diarization": {"duration_ms": 4100},
        "initial_analysis_join": {"duration_ms": 3},
    }
    assert summary["task_duration_ms"] == 5000


def test_asr_stage_mapper_tracks_local_time_and_pause_segmentation():
    assert video_localization_operation_queue._asr_stage_id("正在按时间码与停顿生成断句") == "boundary_review"


def test_localization_stage_mapper_accepts_dynamic_flow_stage_ids():
    assert (
        video_localization_operation_queue._localization_stage_id(
            "flow:review_localization_naturalness|正在盲测中文自然度"
        )
        == "review_localization_naturalness"
    )


def test_asr_stage_mapper_accepts_dynamic_flow_stage_ids():
    assert (
        video_localization_operation_queue._asr_stage_id("flow:section_review_r2|第 2 轮分段复查")
        == "section_review_r2"
    )


def test_task_step_merge_preserves_dynamic_metadata_when_final_result_is_richer():
    merged = video_localization_operation_queue._merge_task_step_results(
        {
            "task_step_results": {
                "understand_document": {
                    "label": "理解全文并规划复查",
                    "order": 30,
                    "status": "running",
                    "summary": "正在通读全文。",
                }
            }
        },
        {
            "task_step_results": {
                "understand_document": {
                    "status": "success",
                    "summary": "已生成全文理解卡。",
                    "sections": [{"title": "检查结果", "items": []}],
                }
            }
        },
    )

    assert merged["task_step_results"]["understand_document"] == {
        "label": "理解全文并规划复查",
        "order": 30,
        "status": "success",
        "summary": "已生成全文理解卡。",
        "sections": [{"title": "检查结果", "items": []}],
    }


def test_video_localization_operation_persists_asr_preview_phases(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 预览阶段", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert (
        video_localization_service.save_video_localization(
            project["project_id"], draft.model_copy(update={"operations": [operation]})
        )
        is not None
    )
    snapshots = []

    def fake_asr(draft, engine_id, source_track_id, *, preview_callback, **_kwargs):
        assert source_track_id == "auto"
        for phase, text in (
            ("asr_draft", "Raw draft"),
            ("text_review", "Reviewed draft"),
            ("timing_segmentation", "Timed cue"),
        ):
            preview_callback(
                phase,
                [{"cue_id": f"preview_{phase}", "start_ms": 100, "end_ms": 900, "text": text}],
            )
            current = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
            assert current is not None
            snapshots.append(current.result_summary.copy())
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    assert [snapshot["preview_phase"] for snapshot in snapshots] == [
        "asr_draft",
        "text_review",
        "timing_segmentation",
    ]
    assert all(snapshot["stage"] == "准备处理" for snapshot in snapshots)
    assert snapshots[-1]["preview_cues"] == [
        {"cue_id": "preview_timing_segmentation", "start_ms": 100, "end_ms": 900, "text": "Timed cue"}
    ]
    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None and completed.status == "success", (
        json.dumps(
            completed.result_summary,
            ensure_ascii=False,
            indent=2,
        )
        if completed is not None
        else None
    )


def test_video_localization_operation_persists_completed_branch_timings_while_asr_is_running(
    tmp_path: Path, monkeypatch
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 并行耗时", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert (
        video_localization_service.save_video_localization(
            project["project_id"], draft.model_copy(update={"operations": [operation]})
        )
        is not None
    )
    snapshots = []

    def fake_asr(project_id: str, *, on_report, **_kwargs):
        for step_id, duration_ms in (
            ("asr", 69_000),
            ("diarization", 54_000),
            ("initial_analysis_join", 320),
        ):
            on_report(
                step_id,
                {
                    "status": "success",
                    "summary": f"{step_id} 已完成。",
                    "metrics": [],
                    "sections": [],
                    "notes": [],
                    "duration_ms": duration_ms,
                },
            )
            current = video_localization_operation_queue.get_operation(
                project_id,
                operation.operation_id,
            )
            assert current is not None
            snapshots.append(current.result_summary.copy())
        current_draft = video_localization_service.get_video_localization(project_id)
        assert current_draft is not None
        return _completed_asr_result(current_draft)

    monkeypatch.setattr(
        video_localization_service,
        "transcribe_english_source_audio",
        fake_asr,
    )

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    assert snapshots[0]["task_stage_timings"]["asr"] == {
        "duration_ms": 69_000,
        "atomic": True,
    }
    assert snapshots[1]["task_stage_timings"]["diarization"] == {
        "duration_ms": 54_000,
        "atomic": True,
    }
    assert snapshots[2]["task_stage_timings"]["initial_analysis_join"] == {
        "duration_ms": 320,
        "atomic": True,
    }
    assert snapshots[2]["task_step_results"]["diarization"]["duration_ms"] == 54_000


def test_video_localization_operation_keeps_atomic_timings_during_later_progress(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 耗时合并", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="running",
        label="听写字幕",
        result_summary={
            "task_stage_timings": {
                "asr": {
                    "duration_ms": 69_000,
                    "atomic": True,
                },
                "diarization": {
                    "duration_ms": 54_000,
                    "atomic": True,
                },
                "initial_analysis_join": {
                    "duration_ms": 320,
                    "atomic": True,
                },
            }
        },
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert (
        video_localization_service.save_video_localization(
            project["project_id"],
            draft.model_copy(update={"operations": [operation]}),
        )
        is not None
    )

    video_localization_operation_queue._mark_operation(
        project["project_id"],
        operation.operation_id,
        kind="english_asr",
        status="running",
        progress=0.3,
        result_summary={
            "stage": "正在理解全文并规划复查",
            "task_stage_timings": {
                "asr": {"duration_ms": 72_000},
                "understand_document": {
                    "duration_ms": 1_200,
                    "running": True,
                },
            },
        },
    )

    current = video_localization_operation_queue.get_operation(
        project["project_id"],
        operation.operation_id,
    )
    assert current is not None
    assert current.result_summary["task_stage_timings"] == {
        "asr": {"duration_ms": 69_000, "atomic": True},
        "diarization": {"duration_ms": 54_000, "atomic": True},
        "initial_analysis_join": {"duration_ms": 320, "atomic": True},
        "understand_document": {"duration_ms": 1_200, "running": True},
    }


def test_video_localization_operation_runs_localization_pipeline_with_preview_and_step_timings(
    tmp_path: Path, monkeypatch
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化任务", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="localization_draft",
        status="queued",
        label="生成本土化字幕初稿",
        parameters={"target_language": "zh-Hans"},
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    draft = draft.model_copy(
        update={
            "cues": [
                VideoLocalizationCue(
                    cue_id="cue_0001",
                    start_ms=100,
                    end_ms=1000,
                    en_subtitle_text="Hello creators",
                )
            ],
            "operations": [operation],
        }
    )
    assert video_localization_service.save_video_localization(project["project_id"], draft) is not None
    observed = []

    def fake_localization(
        project_id,
        *,
        operation_id,
        on_progress,
        on_preview,
        on_report,
        **_kwargs,
    ):
        assert operation_id == operation.operation_id
        for progress, stage in (
            (0.06, "flow:lock_localization_source|正在锁定本土化源输入"),
            (0.18, "flow:analyze_localization_document|正在理解全文"),
            (0.30, "flow:adjudicate_localization_evidence_v3|正在确认必要证据"),
            (0.62, "flow:generate_localization_spoken_script|正在生成中文台词"),
            (0.75, "flow:review_localization_naturalness|正在盲测中文自然度"),
            (0.93, "flow:validate_localization_tracks|正在执行本地质量门"),
            (0.94, "flow:commit_localization_tracks|正在保存正式双轨"),
        ):
            on_progress(progress, stage)
            observed.append(video_localization_operation_queue.get_operation(project_id, operation.operation_id))
        on_report(
            "review_localization_fidelity",
            {
                "label": "中文初稿事实与原意复核",
                "order": 120,
                "status": "running",
                "summary": "正在逐段修订中文。",
                "metrics": [{"label": "当前段落", "value": "1/3"}],
                "sections": [],
                "notes": [],
            },
        )
        preview = [{"subtitle_id": "localized_0001", "start_ms": 100, "end_ms": 1000, "text": "创作者们好"}]
        on_preview("localized_review", preview)
        return video_localization_service.get_video_localization(project_id), {
            "localized_subtitle_count": 1,
            "stage_timings": {
                step_id: {
                    "duration_ms": index + 1,
                    "started_elapsed_ms": index,
                    "atomic": True,
                }
                for index, step_id in enumerate(
                    (
                        "lock_localization_source",
                        "analyze_localization_document",
                        "adjudicate_localization_evidence_v3",
                        "generate_localization_spoken_script",
                        "review_localization_naturalness",
                        "validate_localization_tracks",
                        "commit_localization_tracks",
                    )
                )
            },
            "task_step_results": {
                task.id: {
                    "label": task.label,
                    "order": task.order,
                    "status": "success",
                    "summary": f"{task.label}已完成。",
                    "metrics": [],
                    "sections": [],
                    "notes": [],
                }
                for stage in (
                    video_localization_operation_queue.workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
                )
                for task in stage.atomic_tasks
            },
            "preview_phase": "localized_review",
            "preview_cues": preview,
        }

    monkeypatch.setattr(
        video_localization_service,
        "run_localization_v3_draft",
        fake_localization,
    )

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    assert [item.result_summary["stage"] for item in observed if item is not None] == [
        "正在锁定本土化源输入",
        "正在理解全文",
        "正在确认必要证据",
        "正在生成中文台词",
        "正在盲测中文自然度",
        "正在执行本地质量门",
        "正在保存正式双轨",
    ]
    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None
    assert completed.status == "success"
    assert completed.result_summary["localized_subtitle_count"] == 1
    final_result = completed.result_summary["task_final_result"]
    assert final_result["status"] == "success"
    assert final_result["purpose"] == "生成本土化字幕初稿"
    workflow_task_count = sum(
        len(stage.atomic_tasks)
        for stage in (video_localization_operation_queue.workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION.stages)
    )
    assert final_result["summary"] == (
        f"生成本土化字幕初稿已完成，生成 1 条本土化字幕；共执行 {workflow_task_count} 个步骤，全部步骤正常完成。"
    )
    shown_titles = [item["title"] for section in final_result["sections"] for item in section["items"]]
    assert len(shown_titles) == workflow_task_count
    assert shown_titles[0] == "固定本次英文源数据"
    assert shown_titles[-1] == "保存正式本土化双轨"
    assert final_result["coverage"] == {
        "mode": "complete",
        "shown_count": workflow_task_count,
        "total_count": workflow_task_count,
        "unit": "个任务步骤",
    }
    assert final_result != completed.result_summary["task_step_results"]["generate_localization_spoken_script"]
    assert set(completed.result_summary["task_stage_timings"]) == {
        "lock_localization_source",
        "analyze_localization_document",
        "adjudicate_localization_evidence_v3",
        "generate_localization_spoken_script",
        "review_localization_naturalness",
        "validate_localization_tracks",
        "commit_localization_tracks",
    }


def test_formal_localization_operation_uses_v3_atomic_workflow(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "新版正式本土化", "description": ""},
    ).json()
    operation = VideoLocalizationOperation(
        operation_id="formal_localization_v2",
        project_id=project["project_id"],
        kind="localization_draft",
        status="queued",
        label="生成本土化字幕初稿",
        parameters={
            "execution_mode": "full",
            "target_language": "zh-Hans",
        },
    )
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=1_200,
                en_subtitle_text="Hello creators",
            )
        ],
        operations=[operation],
    )
    assert (
        video_localization_service.save_video_localization(
            project["project_id"],
            draft,
        )
        is not None
    )

    task_definitions = [
        task
        for stage in (video_localization_operation_queue.workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION.stages)
        for task in stage.atomic_tasks
    ]
    current_settings = video_localization_operation_queue.settings_store.get()
    monkeypatch.setattr(
        video_localization_operation_queue.settings_store,
        "get",
        lambda: current_settings.model_copy(update={"video_localization_development_step_control_enabled": True}),
    )
    checkpoint_root = tmp_path / "formal-localization-checkpoints"
    monkeypatch.setattr(
        video_localization_operation_queue,
        "DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT",
        checkpoint_root,
    )

    def fake_v3_flow(project_id, *, operation_id, on_progress, on_report, **_kwargs):
        assert operation_id == operation.operation_id
        checkpoint_writer = _kwargs["on_atomic_result"]
        assert checkpoint_writer is not None

        class AtomicResult:
            def model_dump(self, *, mode):
                assert mode == "json"
                return {
                    "contract_version": "localization-source-lock-v1",
                    "result_fingerprint": "a" * 64,
                }

        checkpoint_writer("lock_localization_source", AtomicResult())
        steps = {}
        for index, task in enumerate(task_definitions, start=1):
            on_progress(
                index / len(task_definitions),
                f"flow:{task.id}|正在执行{task.label}",
            )
            step_result = {
                "label": task.label,
                "order": task.order,
                "status": "success",
                "summary": f"{task.label}已完成。",
                "metrics": [],
                "sections": [],
                "notes": [],
            }
            steps[task.id] = step_result
            on_report(task.id, step_result)
        return video_localization_service.get_video_localization(project_id), {
            "workflow_id": "localization-v3",
            "localized_subtitle_count": 1,
            "task_step_results": steps,
        }

    monkeypatch.setattr(
        video_localization_service,
        "run_localization_v3_draft",
        fake_v3_flow,
    )

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    completed = video_localization_operation_queue.get_operation(
        project["project_id"],
        operation.operation_id,
    )
    assert completed is not None
    assert completed.status == "success"
    assert completed.result_summary["workflow_id"] == "localization-v3"
    assert set(completed.result_summary["task_step_results"]) == {task.id for task in task_definitions}
    assert sum(len(section["items"]) for section in (completed.result_summary["task_final_result"]["sections"])) == len(
        task_definitions
    )
    assert (checkpoint_root / operation.operation_id / "lock_localization_source" / "checkpoint.json").is_file()


def test_video_localization_failed_operation_keeps_structured_error_detail(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "失败证据", "description": ""}).json()
    operation = VideoLocalizationOperation(
        operation_id="localization_failure_detail",
        project_id=project["project_id"],
        kind="localization_draft",
        status="queued",
        label="生成本土化字幕初稿",
        parameters={"target_language": "zh-Hans"},
    )
    draft = VideoLocalizationDraft(
        cues=[VideoLocalizationCue(cue_id="cue_0001", start_ms=0, end_ms=1200, en_subtitle_text="100% real")],
        operations=[operation],
    )
    assert video_localization_service.save_video_localization(project["project_id"], draft) is not None

    def fail_localization(_project_id, *, on_progress, **_kwargs):
        _kwargs["on_report"](
            "review_localization_fidelity",
            {
                "label": "复核原意与事实",
                "order": 70,
                "status": "running",
                "purpose": "检查错译、漏译、事实和证据使用。",
                "summary": "正在复核中文初稿。",
                "metrics": [],
                "sections": [],
                "_task_timing": {
                    "duration_ms": 0,
                    "started_elapsed_ms": 0,
                    "running": True,
                    "atomic": True,
                },
            },
        )
        on_progress(
            0.3,
            "flow:review_localization_fidelity|复核原意与事实",
        )
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_NUMBER_CHANGED",
            "数字核对失败",
            {"source_cue_ids": ["cue_0001"], "source_numbers": ["100%"], "display_numbers": []},
        )

    monkeypatch.setattr(
        video_localization_service,
        "run_localization_v3_draft",
        fail_localization,
    )

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None
    assert completed.status == "failed"
    assert completed.result_summary["stage"] == "复核原意与事实"
    error_detail = completed.result_summary["error_detail"]
    assert error_detail["code"] == "VIDEO_LOCALIZATION_NUMBER_CHANGED"
    assert error_detail["message"] == "数字核对失败"
    assert error_detail["source_cue_ids"] == ["cue_0001"]
    assert error_detail["source_numbers"] == ["100%"]
    assert error_detail["display_numbers"] == []
    assert error_detail["advice"]
    assert set(completed.result_summary["task_stage_timings"]) == {
        "review_localization_fidelity",
    }
    assert all("running" not in timing for timing in completed.result_summary["task_stage_timings"].values())
    failed_step = completed.result_summary["task_step_results"]["review_localization_fidelity"]
    assert failed_step["status"] == "failed"
    assert failed_step["summary"] == "数字核对失败"
    assert failed_step["error_detail"]["code"] == "VIDEO_LOCALIZATION_NUMBER_CHANGED"
    assert completed.result_summary["task_duration_ms"] >= sum(
        timing["duration_ms"] for timing in completed.result_summary["task_stage_timings"].values()
    )


def test_video_localization_asr_operation_keeps_llm_runtime_error_detail(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 终审超时", "description": ""}).json()
    operation = VideoLocalizationOperation(
        operation_id="asr_llm_timeout",
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="生成 ASR 字幕",
    )
    draft = VideoLocalizationDraft(operations=[operation])
    assert video_localization_service.save_video_localization(project["project_id"], draft) is not None

    step_result = {
        "status": "failed",
        "purpose": "通读整篇源语言转写。",
        "summary": "整篇 ASR 终审未通过。",
        "metrics": [],
        "sections": [],
        "notes": ["llm_timeout：DeepSeek 请求超时"],
        "error_detail": {
            "code": "llm_timeout",
            "status_code": 504,
            "message": "DeepSeek 请求超时",
        },
    }

    def fail_asr(_project_id, *, on_progress, on_report, **_kwargs):
        on_progress(0.5, "正在整篇 ASR 终审 · 第 1/2 轮")
        on_report("transcript_quality_gate", step_result)
        raise AppException(
            504,
            "llm_timeout",
            "DeepSeek 请求超时",
            {"error_detail": step_result["error_detail"], "step_result": step_result},
        )

    monkeypatch.setattr(video_localization_service, "transcribe_english_source_audio", fail_asr)

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None
    assert completed.status == "failed"
    assert completed.error_code == "llm_timeout"
    assert completed.error_message == "DeepSeek 请求超时"
    assert completed.result_summary["stage_id"] == "transcript_quality_gate"
    error_detail = completed.result_summary["error_detail"]
    assert error_detail["code"] == "llm_timeout"
    assert error_detail["status_code"] == 504
    assert error_detail["message"] == "DeepSeek 请求超时"
    assert error_detail["stage"] == "正在整篇 ASR 终审 · 第 1/2 轮"
    assert error_detail["advice"]
    assert completed.result_summary["task_step_results"]["transcript_quality_gate"] == step_result


def test_video_localization_failure_without_structured_detail_gets_actionable_fallback():
    detail = video_localization_operation_queue._fallback_error_detail(
        "VIDEO_LOCALIZATION_REVIEW_INVALID",
        "终审返回格式无效",
        "正在检查中文表达",
    )

    assert detail["code"] == "VIDEO_LOCALIZATION_REVIEW_INVALID"
    assert detail["message"] == "终审返回格式无效"
    assert detail["stage"] == "正在检查中文表达"
    assert detail["rule_id"] == "localization_quality_convergence"
    assert "分组复核和全文复核" in detail["rule"]
    assert detail["advice"]

    unresolved = video_localization_operation_queue._fallback_error_detail(
        "VIDEO_LOCALIZATION_REVIEW_UNRESOLVED",
        "终审仍有一处人称问题",
        "正在终审",
    )
    assert "查看终审列出" in unresolved["advice"][0]
    assert "语言模型服务" not in unresolved["advice"][0]


def test_video_localization_cancel_token_is_visible_only_after_durable_write(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消提交竞态", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="localization_draft",
        status="running",
    )
    video_localization_service.save_video_localization(
        project["project_id"], VideoLocalizationDraft(operations=[operation])
    )

    with video_localization_service._DRAFT_WRITE_LOCK:
        thread = threading.Thread(
            target=video_localization_operation_queue.cancel,
            args=(project["project_id"], operation.operation_id),
        )
        thread.start()
        time.sleep(0.05)
        assert not video_localization_operation_queue._cancel_requested(
            project["project_id"],
            operation.operation_id,
        )
        assert thread.is_alive()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert video_localization_operation_queue._cancel_requested(
        project["project_id"],
        operation.operation_id,
    )
    assert (
        video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id).status
        == "cancelled"
    )


def test_video_localization_cancelled_operation_rejects_late_preview_progress(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消后的预览", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="running",
        label="听写字幕",
        progress=0.42,
        result_summary={
            "stage": "正在识别人声内容",
            "stage_id": "asr",
            "task_step_results": {
                "asr": {
                    "status": "success",
                    "summary": "原始讲话已经转写完成。",
                    "metrics": [],
                    "sections": [],
                    "notes": [],
                }
            },
        },
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert (
        video_localization_service.save_video_localization(
            project["project_id"], draft.model_copy(update={"operations": [operation]})
        )
        is not None
    )

    cancelled = video_localization_operation_queue.cancel(project["project_id"], operation.operation_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"

    video_localization_operation_queue._mark_operation(
        project["project_id"],
        operation.operation_id,
        kind="english_asr",
        status="running",
        progress=0.91,
        result_summary={
            "preview_phase": "text_review",
            "preview_cues": [{"cue_id": "late_preview", "start_ms": 0, "end_ms": 100, "text": "late"}],
        },
    )

    latest = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert latest is not None
    assert latest.status == "cancelled"
    assert latest.cancel_requested is True
    assert latest.progress == 0.42
    assert latest.result_summary["stage"] == "正在取消"
    assert latest.result_summary["preview_cues"] == []
    assert latest.result_summary["task_step_results"]["asr"]["summary"] == "原始讲话已经转写完成。"
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert draft.source_media.metadata["english_asr_status"] == "cancelled"


def test_video_localization_cancelled_asr_does_not_persist_result(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消 ASR 落盘", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
    )
    saved = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]).model_copy(
            update={"operations": [operation]}
        ),
    )
    assert saved is not None

    def fake_asr(draft, engine_id, source_track_id, **_kwargs):
        cancelled = video_localization_operation_queue.cancel(project["project_id"], operation.operation_id)
        assert cancelled is not None and cancelled.cancel_requested is True
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    updated = video_localization_service.get_video_localization(project["project_id"])
    assert updated is not None
    completed = next(item for item in updated.operations if item.operation_id == operation.operation_id)
    assert completed.status == "cancelled"
    assert completed.cancel_requested is True
    assert updated.transcription is None
    assert updated.cues == []
    assert "english_asr_engine_id" not in updated.source_media.metadata


def test_video_localization_async_asr_defaults_to_qwen3_mlx(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "默认千问 ASR", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
    )
    saved = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]).model_copy(
            update={"operations": [operation]}
        ),
    )
    assert saved is not None
    captured = {}

    def fake_asr(draft, engine_id, source_track_id, **_kwargs):
        captured["engine_id"] = engine_id
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    assert captured["engine_id"] == "qwen3-asr-mlx"
    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None and completed.status == "success"


def test_video_localization_async_asr_forwards_selected_llm_profile(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "指定 ASR 复核模型", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
        parameters={"profile_id": "review-profile"},
    )
    saved = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]).model_copy(
            update={"operations": [operation]}
        ),
    )
    assert saved is not None
    captured = {}

    def fake_asr(draft, engine_id, source_track_id, **kwargs):
        captured.update(kwargs)
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    assert captured["llm_profile_id"] == "review-profile"
    assert captured["vision_profile_id"] == "review-profile"


def test_video_localization_english_asr_empty_result_returns_chinese_guidance(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空识别结果", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-wav")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(audio_path), "duration_ms": 1800},
        },
    )
    monkeypatch.setattr(
        video_localization_source_pipeline.asr_service,
        "transcribe",
        lambda **_: {"text": "", "segments": []},
    )

    with pytest.raises(AppException) as exc_info:
        video_localization_service.transcribe_english_source_audio(
            project["project_id"],
            source_track_id="original",
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_EMPTY"
    assert exc_info.value.message == ("语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。")


def test_video_localization_async_asr_uses_requested_vocals_track(tmp_path: Path, monkeypatch):
    _configure_asr_review_model(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "人声轨转录", "description": ""}).json()
    project_root = _project_root(project["project_id"])
    original_path = project_root / "audio" / "source.wav"
    vocals_path = project_root / "stems" / "vocals.wav"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(b"original")
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(original_path), "duration_ms": 1800},
            "stems": {"vocals_clean_path": str(vocals_path), "separation_status": "completed"},
        },
    )

    def fake_transcribe(*, engine_id: str, audio_path: str, language: str):
        assert engine_id == "faster-whisper-turbo"
        assert Path(audio_path) == vocals_path
        assert language == "auto"
        return {"segments": [{"start_ms": 0, "end_ms": 1800, "text": "Clean voice", "language": "en"}]}

    monkeypatch.setattr(video_localization_source_pipeline.asr_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(video_localization_operation_queue, "_enqueue", lambda operation_id: None)
    progress_stages: list[str] = []
    original_mark_operation = video_localization_operation_queue._mark_operation

    def record_progress(*args, **kwargs):
        stage = (kwargs.get("result_summary") or {}).get("stage")
        if stage:
            progress_stages.append(stage)
        return original_mark_operation(*args, **kwargs)

    monkeypatch.setattr(video_localization_operation_queue, "_mark_operation", record_progress)

    operation = video_localization_operation_queue.submit(
        project["project_id"],
        "english_asr",
        {
            "engine_id": "faster-whisper-turbo",
            "source_track_id": "vocals",
            "diarization_engine_id": None,
        },
    )
    assert operation is not None
    assert operation.parameters["source_language"] == "auto"
    assert operation.parameters["scope"] == {
        "area": "subtitle",
        "exclusive": True,
        "cancel_mode": "safe_point",
        "tracks": [
            {"id": "vocals", "role": "input"},
            {"id": "subtitles", "role": "output"},
        ],
    }
    video_localization_operation_queue._process(project["project_id"], operation.operation_id)

    updated = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None and completed.status == "success", (
        json.dumps(
            completed.result_summary,
            ensure_ascii=False,
            indent=2,
        )
        if completed is not None
        else None
    )
    assert updated["source_media"]["metadata"]["english_asr_source_track_id"] == "vocals"
    assert updated["source_media"]["metadata"]["english_asr_alignment_source_track_id"] == "vocals"
    assert updated["transcription"]["source_track_id"] == "vocals"
    assert updated["transcription"]["language"] == "en"
    assert updated["language_config"] == {
        "source_language": "auto",
        "target_language": "zh-Hans",
        "detected_source_language": "en",
    }
    assert updated["transcription"]["alignment_source_track_id"] == "vocals"
    assert updated["cues"][0]["en_subtitle_text"] == "Clean voice"
    assert progress_stages == [
        "准备处理",
        "正在识别人声内容",
        "理解全文并规划复查",
        "正在生成逐词时间码",
        "正在分析停顿与声学边界",
        "正在按时间码与停顿生成断句",
        "正在生成字幕轨",
    ]


def test_video_localization_active_asr_rejects_conflicting_language_parameters(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 参数冲突", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"audio")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(audio_path)},
        },
    )
    monkeypatch.setattr(video_localization_operation_queue, "_enqueue", lambda _operation_id: None)

    first = video_localization_operation_queue.submit(
        project["project_id"],
        "english_asr",
        {"source_track_id": "original", "source_language": "en"},
    )
    duplicate = video_localization_operation_queue.submit(
        project["project_id"],
        "english_asr",
        {
            "engine_id": video_localization_source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID,
            "source_track_id": "original",
            "source_language": "en",
            "segmentation_profile_id": "generic_zh",
        },
    )

    assert first is not None and duplicate is not None
    assert duplicate.operation_id == first.operation_id
    with pytest.raises(AppException) as exc_info:
        video_localization_operation_queue.submit(
            project["project_id"],
            "english_asr",
            {"source_track_id": "original", "source_language": "zh"},
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_OPERATION_PARAMETERS_CONFLICT"


def test_video_localization_asr_parameters_resolve_explicit_auto_engine():
    normalized = video_localization_operation_queue._normalized_operation_parameters(
        "english_asr",
        {"execution_mode": "full", "engine_id": "auto"},
        VideoLocalizationDraft(),
    )

    assert normalized["engine_id"] == (video_localization_source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID)


def test_video_localization_asr_parameters_enable_nonfatal_speaker_analysis_by_default():
    normalized = video_localization_operation_queue._normalized_operation_parameters(
        "english_asr",
        {"execution_mode": "full", "engine_id": "auto"},
        VideoLocalizationDraft(),
    )

    assert normalized["diarization_engine_id"] == "auto"


def test_video_localization_asr_speaker_count_is_an_explicit_speaker_analysis_request():
    normalized = video_localization_operation_queue._normalized_operation_parameters(
        "english_asr",
        {
            "execution_mode": "full",
            "engine_id": "auto",
            "min_speakers": 2,
        },
        VideoLocalizationDraft(),
    )

    assert normalized["diarization_engine_id"] == "auto"
    assert normalized["min_speakers"] == 2


def test_video_localization_formal_asr_summary_shows_the_default_parallel_speaker_branch():
    without_speakers = video_localization_operation_queue._initial_operation_summary(
        "english_asr",
        {"execution_mode": "full"},
    )
    with_speakers = video_localization_operation_queue._initial_operation_summary(
        "english_asr",
        {
            "execution_mode": "full",
            "diarization_engine_id": "moss-transcribe-diarize-mlx",
        },
    )

    without_tasks = without_speakers["task_stage_groups"][0]["atomic_tasks"]
    with_tasks = with_speakers["task_stage_groups"][0]["atomic_tasks"]

    assert [task["id"] for task in without_tasks] == [
        "asr",
        "diarization",
        "initial_analysis_join",
    ]
    assert [task["id"] for task in with_tasks] == [
        "asr",
        "diarization",
        "initial_analysis_join",
    ]
    assert [task["execution"] for task in with_tasks] == [
        "parallel",
        "parallel",
        "join",
    ]
    assert with_tasks[-1]["depends_on"] == ["asr", "diarization"]
    assert without_speakers["task_step_results"]["diarization"]["status"] == "todo"
    assert with_speakers["task_step_results"]["diarization"]["status"] == "todo"


def test_video_localization_diarization_development_target_shows_the_speaker_branch():
    summary = video_localization_operation_queue._initial_operation_summary(
        "english_asr",
        {
            "execution_mode": "development_target",
            "development_source_operation_id": "source-op",
            "development_target_step_id": "diarization",
        },
    )

    task_ids = {task["id"] for group in summary["task_stage_groups"] for task in group["atomic_tasks"]}
    assert task_ids == {"diarization"}


def test_video_localization_localization_parameters_normalize_explicit_defaults():
    draft = VideoLocalizationDraft()

    implicit = video_localization_operation_queue._normalized_operation_parameters(
        "localization_draft",
        {},
        draft,
    )
    explicit = video_localization_operation_queue._normalized_operation_parameters(
        "localization_draft",
        {
            "source_language": "auto",
            "target_language": "zh-Hans",
            "profile_id": None,
        },
        draft,
    )

    assert implicit == explicit


def test_video_localization_localization_parameters_use_current_default_profile(
    monkeypatch,
):
    draft = VideoLocalizationDraft(
        transcription={"review_profile_id": "llm-used-by-asr"},
    )
    monkeypatch.setattr(
        video_localization_operation_queue.settings_store,
        "llm_profiles",
        lambda: type(
            "Profiles",
            (),
            {"default_profile_id": "llm-current-default"},
        )(),
    )

    inherited = video_localization_operation_queue._normalized_operation_parameters(
        "localization_draft",
        {},
        draft,
    )
    explicit = video_localization_operation_queue._normalized_operation_parameters(
        "localization_draft",
        {"profile_id": "llm-chosen-for-localization"},
        draft,
    )

    assert inherited["profile_id"] == "llm-current-default"
    assert explicit["profile_id"] == "llm-chosen-for-localization"


def test_video_localization_submit_validates_current_default_profile(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "复用 ASR 模型", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 0,
                    "end_ms": 1200,
                    "en_subtitle_text": "This is the source line.",
                }
            ],
            "transcription": {"review_profile_id": "llm-used-by-asr"},
        },
    )
    resolved = []
    monkeypatch.setattr(video_localization_operation_queue, "_enqueue", lambda _operation_id: None)
    monkeypatch.setattr(
        video_localization_operation_queue.settings_store,
        "llm_profiles",
        lambda: type(
            "Profiles",
            (),
            {"default_profile_id": "llm-current-default"},
        )(),
    )
    monkeypatch.setattr(
        video_localization_service.llm_runtime,
        "resolve_profile",
        lambda profile_id=None: resolved.append(profile_id) or video_localization_service.llm_runtime.ResolvedProfile(
            profile_id=profile_id, protocol="codex_cli", base_url="", model_id="test-model",
        ),
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/localization",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["parameters"]["profile_id"] == ("llm-current-default")
    assert resolved and set(resolved) == {"llm-current-default"}


def test_video_localization_reference_candidates_require_clean_vocals(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺干净人声", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "reference_clips", "parameters": {}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING"


def test_video_localization_reference_candidates_from_clean_vocals(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "参考音候选", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"separation_status": "completed", "vocals_clean_path": str(vocals_path)},
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "This is a reference line.",
                    "zh_localized_subtitle_text": "这是一句参考台词。",
                    "tts_recommended_text": "这是一句，参考台词。",
                }
            ],
        },
    )

    def fake_cut(source_path: Path, destination: Path, start_ms: int, end_ms: int):
        assert source_path == vocals_path
        assert start_ms == 1000
        assert end_ms == 3200
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"clip")
        return destination

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_cut)
    monkeypatch.setattr(
        video_localization_reference_clips.audio_tools,
        "probe_audio",
        lambda path: {"duration_ms": 2200, "sample_rate": 24000, "channels": 1},
    )

    updated = video_localization_service.create_reference_clips_from_cues(project["project_id"])

    assert updated is not None
    body = updated.model_dump(mode="json")
    assert body["reference_clips"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"
    assert body["reference_clips"][0]["source_stem"] == "vocals_clean"
    assert body["reference_clips"][0]["cleanliness"] == "needs_review"
    assert body["reference_clips"][0]["asr_status"] == "candidate"
    assert body["reference_clips"][0]["asr_text"] == "This is a reference line."
    assert body["cues"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"
    assert body["speakers"][0]["reference_clip_ids"] == ["ref_speaker_01_cue_0001"]
    assert body["speakers"][0]["time_ranges"][0]["source"] == "reference_candidate"


def test_video_localization_async_reference_clip_operation_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "异步参考音候选", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"separation_status": "completed", "vocals_clean_path": str(vocals_path)},
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "This is a reference line.",
                    "zh_localized_subtitle_text": "这是一句参考台词。",
                    "tts_recommended_text": "这是一句，参考台词。",
                }
            ],
        },
    )

    def fake_cut(source_path: Path, destination: Path, start_ms: int, end_ms: int):
        assert source_path == vocals_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"{start_ms}-{end_ms}".encode("utf-8"))
        return destination

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_cut)
    monkeypatch.setattr(
        video_localization_reference_clips.audio_tools,
        "probe_audio",
        lambda path: {"duration_ms": 2200, "sample_rate": 24000, "channels": 1},
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "reference_clips"},
    )

    assert response.status_code == 200
    operation = response.json()
    completed = None
    for _ in range(30):
        latest = client.get(
            f"/api/projects/{project['project_id']}/video-localization/operations/{operation['operation_id']}"
        ).json()
        if latest["status"] == "success":
            completed = latest
            break
        time.sleep(0.05)

    assert completed is not None
    assert completed["result_summary"]["reference_clip_count"] == 1
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["reference_clips"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"
    assert draft["cues"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"


def test_video_localization_workflow_requires_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺 cue", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/localization",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_CUES_MISSING"


def test_video_localization_operation_rejects_non_chinese_localization_target(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "拒绝非简中目标", "description": ""}).json()
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=1600,
                en_subtitle_text="A valid source subtitle",
            )
        ]
    )
    assert video_localization_service.save_video_localization(project["project_id"], draft) is not None

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/localization",
        json={"target_language": "en-US"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_video_localization_workflow_requires_configured_llm(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "中文草稿", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "In 1992, this changed everything.",
                }
            ],
        },
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/localization",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "llm_profile_not_configured"
    cue = client.get(f"/api/projects/{project['project_id']}/video-localization").json()["cues"][0]
    assert cue["zh_localized_subtitle_text"] is None
    assert cue["tts_recommended_text"] is None


def test_video_localization_workflow_does_not_overwrite_without_llm(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "数字读法", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "In 1992, 130 people joined.",
                    "zh_localized_subtitle_text": "1992 年，有 130 人加入。",
                }
            ],
        },
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations/localization",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "llm_profile_not_configured"
    cue = client.get(f"/api/projects/{project['project_id']}/video-localization").json()["cues"][0]
    assert cue["zh_localized_subtitle_text"] == "1992 年，有 130 人加入。"
    assert cue["tts_recommended_text"] is None
    assert "tts_text_normalized" not in cue["quality_flags"]


def test_video_localization_subtitle_export_bilingual_srt(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕导出", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/bilingual")

    assert response.status_code == 200
    filename = _attachment_filename(response)
    assert filename.startswith("字幕导出__字幕__双语__")
    assert "__版本-" in filename
    assert filename.endswith(".srt")
    assert response.text == (
        "1\n00:00:01,000 --> 00:00:03,200\nIn 1992, this changed everything.\n1992 年，这件事改变了一切。\n"
    )


def test_video_localization_subtitle_export_zh_prefers_localized_subtitle_track(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕轨优先导出", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "旧的 cue 中文。",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "subtitle_0001",
                    "start_ms": 1500,
                    "end_ms": 2600,
                    "text": "新的独立字幕轨。",
                    "linked_cue_id": "cue_0001",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 200
    assert response.text == ("1\n00:00:01,500 --> 00:00:02,600\n新的独立字幕轨。\n")


def test_video_localization_blocks_old_localized_track_after_source_text_changes(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化来源失效", "description": ""}).json()
    original = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=1200,
                en_subtitle_text="Original text",
                zh_localized_subtitle_text="原来的翻译",
                source_word_ids=["word_0001", "word_0002"],
            )
        ],
        transcription=VideoLocalizationTranscriptionState(
            revision_id="asr_revision_1",
            language="en",
            source_track_id="original",
            engine_id="test",
            raw_text="Original text",
            corrected_text="Original text",
            segments=[
                VideoLocalizationTranscriptSegment(
                    segment_id="segment_1",
                    start_ms=0,
                    end_ms=1200,
                    raw_text="Original text",
                    corrected_text="Original text",
                )
            ],
            words=[
                VideoLocalizationAlignedWord(
                    word_id="word_0001",
                    segment_id="segment_1",
                    text="Original",
                    start_ms=0,
                    end_ms=600,
                ),
                VideoLocalizationAlignedWord(
                    word_id="word_0002",
                    segment_id="segment_1",
                    text="text",
                    start_ms=600,
                    end_ms=1200,
                ),
            ],
        ),
        localized_subtitles=[{"subtitle_id": "localized_0001", "start_ms": 0, "end_ms": 1200, "text": "原来的翻译"}],
    )
    original = original.model_copy(
        update={
            "localization_state": {
                "status": "draft",
                "source_fingerprint": video_localization_localization_source.current_source_fingerprint(original),
            }
        }
    )
    changed = original.model_copy(
        update={"cues": [original.cues[0].model_copy(update={"en_subtitle_text": "User edited source text"})]}
    )
    video_localization_service.save_video_localization(project["project_id"], changed)

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    assert any(item["code"] == "LOCALIZATION_SOURCE_CHANGED" for item in response.json()["error"]["detail"]["issues"])

    with pytest.raises(AppException) as exc_info:
        video_localization_tts_pipeline.build_batch_request(
            project_id=project["project_id"],
            project_name="本土化来源失效",
            draft=changed,
            output_dir=tmp_path / "tts",
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_SOURCE_CHANGED"


def test_video_localization_restores_legacy_timing_review_flag_without_staling_localization(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "旧时间复核标记兼容", "description": ""},
    ).json()
    transcription = VideoLocalizationTranscriptionState(
        revision_id="asr_revision_1",
        language="en",
        source_track_id="original",
        engine_id="test",
        raw_text="Timing uncertain",
        corrected_text="Timing uncertain",
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="segment_1",
                start_ms=0,
                end_ms=1200,
                raw_text="Timing uncertain",
                corrected_text="Timing uncertain",
            )
        ],
        words=[
            VideoLocalizationAlignedWord(
                word_id="word_0001",
                segment_id="segment_1",
                text="Timing",
                start_ms=0,
                end_ms=600,
                timing_confidence="low",
            ),
            VideoLocalizationAlignedWord(
                word_id="word_0002",
                segment_id="segment_1",
                text="uncertain",
                start_ms=600,
                end_ms=1200,
            ),
        ],
    )
    source_cue = VideoLocalizationCue(
        cue_id="cue_0001",
        speaker_id="speaker_1",
        start_ms=0,
        end_ms=1200,
        en_subtitle_text="Timing uncertain",
        source_word_ids=["word_0001", "word_0002"],
        quality_flags=[
            "generated_by_asr",
            "segment_timing_interpolated",
            "timing_review_required",
        ],
    )
    localized = VideoLocalizationDraft(
        transcription=transcription,
        speakers=[{"speaker_id": "speaker_1"}],
        cues=[source_cue],
        localized_subtitles=[
            {
                "subtitle_id": "localized_0001",
                "start_ms": 0,
                "end_ms": 1200,
                "text": "时间仍需复核",
            }
        ],
    )
    expected = video_localization_localization_source.current_source_fingerprint(localized)
    persisted_without_legacy_flag = localized.model_copy(
        update={
            "cues": [
                source_cue.model_copy(
                    update={
                        "quality_flags": [
                            "generated_by_asr",
                            "segment_timing_interpolated",
                        ]
                    }
                )
            ],
            "localization_state": {
                "status": "draft",
                "source_fingerprint": expected,
            },
        }
    )
    video_localization_service.save_video_localization(
        project["project_id"],
        persisted_without_legacy_flag,
    )
    normalized = video_localization_service.get_video_localization(project["project_id"])
    assert normalized is not None
    assert "timing_review_required" in normalized.cues[0].quality_flags
    assert video_localization_localization_source.current_source_fingerprint(normalized) == expected

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 200, response.text
    loaded = video_localization_service.get_video_localization(project["project_id"])
    assert loaded is not None
    assert "timing_review_required" in loaded.cues[0].quality_flags


def test_video_localization_subtitle_export_rejects_empty_timed_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "无时间字幕", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "en_subtitle_text": "No timing yet."}],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/bilingual")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    assert response.json()["error"]["detail"]["issues"][0]["code"] == "CUE_TIMECODE_MISSING"


def test_video_localization_subtitle_export_blocks_empty_track_before_serialization(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空字幕轨", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    assert response.json()["error"]["detail"]["issues"] == [
        {
            "code": "SUBTITLE_TRACK_EMPTY",
            "message": "字幕轨为空，没有可导出的字幕",
            "severity": "blocker",
            "cue_id": None,
            "speaker_id": None,
            "reference_clip_id": None,
        }
    ]


def test_video_localization_subtitle_export_allows_low_confidence_timing_warning(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "低置信时间", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1500,
                    "en_subtitle_text": "Needs timing review.",
                    "timing_confidence": "low",
                    "quality_flags": ["timing_review_required"],
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 200
    saved = video_localization_service.get_video_localization(project["project_id"])
    assert saved is not None
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in {issue.code for issue in saved.quality_gate.warnings}


def test_video_localization_subtitle_export_does_not_skip_invalid_cue(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "禁止静默跳过", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000, "en_subtitle_text": "Valid."},
                {"cue_id": "cue_0002", "start_ms": 1000, "end_ms": 2000, "en_subtitle_text": ""},
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    issues = response.json()["error"]["detail"]["issues"]
    assert any(issue["code"] == "EN_SUBTITLE_MISSING" and issue["cue_id"] == "cue_0002" for issue in issues)


def test_video_localization_subtitle_export_does_not_skip_invalid_localized_track_entry(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化字幕结构门禁", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {"subtitle_id": "subtitle_bad", "start_ms": 0, "end_ms": 0, "text": "不能被跳过"},
                {"subtitle_id": "subtitle_ok", "start_ms": 1000, "end_ms": 2000, "text": "有效字幕"},
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    codes = {issue["code"] for issue in response.json()["error"]["detail"]["issues"]}
    assert "LOCALIZED_SUBTITLE_DURATION_INVALID" in codes


def test_video_localization_subtitle_export_allows_high_reading_speed_warning(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "中文硬门禁", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "This is too fast.",
                    "zh_localized_subtitle_text": "一二三四五六七八九十甲乙丙",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/bilingual")

    assert response.status_code == 200
    assert "一二三四五六七八九十甲乙丙" in response.text


def test_video_localization_subtitle_export_allows_soft_chinese_quality_warning(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "中文软提示", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "Ten characters.",
                    "zh_localized_subtitle_text": "一二三四五六七八九十",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 200
    assert "一二三四五六七八九十" in response.text


def test_video_localization_subtitle_export_rejects_unsupported_kind(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕类型", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/ass")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_KIND_UNSUPPORTED"


def test_video_localization_timeline_edl_export_includes_clips_and_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "时间线 EDL", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 4200},
            "stems": {"original_audio_path": "/tmp/source.wav"},
            "ui_state": {"track_states": {"dub": {"muted": False, "solo": True, "volume": 0.8}}},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 100,
                    "end_ms": 1200,
                    "en_subtitle_text": "Hello",
                    "zh_localized_subtitle_text": "你好",
                    "tts_recommended_text": "你好",
                    "tts_audio_path": "/tmp/tts.wav",
                    "audio_route": "clone_from_source",
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_001",
                    "cue_id": "cue_0001",
                    "candidate_id": "candidate_001",
                    "track_id": "dub",
                    "start_ms": 100,
                    "end_ms": 1200,
                    "source_start_ms": 0,
                    "source_end_ms": 1100,
                    "audio_path": "/tmp/tts.wav",
                    "status": "ready",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "video_localization_timeline_edl"
    assert body["project_name"] == "时间线 EDL"
    assert body["duration_ms"] == 4200
    assert body["track_states"]["dub"]["solo"] is True
    assert body["timeline_clips"][0]["clip_id"] == "clip_001"
    assert body["cues"][0]["localized_text"] == "你好"


def test_video_localization_mixdown_applies_dub_lane_states_independently(tmp_path: Path):
    lane_zero_path = tmp_path / "lane-zero.wav"
    lane_one_path = tmp_path / "lane-one.wav"
    audio_tools.write_audio(lane_zero_path, np.full(1_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(lane_one_path, np.full(1_000, 0.3, dtype=np.float32), 1_000)
    draft = VideoLocalizationDraft(
        ui_state={
            "dub_lane_states": {
                "0": {"muted": True, "solo": False, "volume": 1.0},
                "1": {"muted": False, "solo": True, "volume": 0.5},
            }
        },
        timeline_clips=[
            {
                "clip_id": "lane-zero",
                "track_id": "dub",
                "dub_lane": 0,
                "start_ms": 0,
                "end_ms": 1_000,
                "audio_path": str(lane_zero_path),
            },
            {
                "clip_id": "lane-one",
                "track_id": "dub",
                "dub_lane": 1,
                "start_ms": 0,
                "end_ms": 1_000,
                "audio_path": str(lane_one_path),
            },
        ],
    )
    mixdown_path = tmp_path / "lane-mix.wav"

    mixed_tracks = video_localization_exporting._write_localized_mixdown(
        mixdown_path,
        draft,
        tmp_path / "legacy-dub.wav",
        1_000,
    )

    mixed_audio, _ = audio_tools.read_audio(mixdown_path)
    assert 0.145 <= float(np.max(np.abs(mixed_audio))) <= 0.155
    assert [(item["clip_id"], item["dub_lane"], item["volume"]) for item in mixed_tracks] == [("lane-one", 1, 0.5)]


def test_video_localization_mixdown_infers_missing_dub_lanes_like_timeline(tmp_path: Path):
    lane_zero_path = tmp_path / "legacy-lane-zero.wav"
    lane_one_path = tmp_path / "legacy-lane-one.wav"
    audio_tools.write_audio(lane_zero_path, np.full(1_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(lane_one_path, np.full(1_000, 0.3, dtype=np.float32), 1_000)
    draft = VideoLocalizationDraft(
        ui_state={
            "dub_lane_states": {
                "0": {"muted": True, "solo": False, "volume": 1.0},
                "1": {"muted": False, "solo": True, "volume": 0.5},
            }
        },
        timeline_clips=[
            {
                "clip_id": "legacy-zero",
                "track_id": "dub",
                "start_ms": 0,
                "end_ms": 1_000,
                "audio_path": str(lane_zero_path),
            },
            {
                "clip_id": "legacy-one",
                "track_id": "dub",
                "start_ms": 0,
                "end_ms": 1_000,
                "audio_path": str(lane_one_path),
            },
        ],
    )
    mixdown_path = tmp_path / "legacy-lane-mix.wav"

    mixed_tracks = video_localization_exporting._write_localized_mixdown(
        mixdown_path,
        draft,
        tmp_path / "legacy-dub.wav",
        1_000,
    )

    mixed_audio, _ = audio_tools.read_audio(mixdown_path)
    assert 0.145 <= float(np.max(np.abs(mixed_audio))) <= 0.155
    assert [(item["clip_id"], item["dub_lane"]) for item in mixed_tracks] == [("legacy-one", 1)]


def test_video_localization_mixdown_resolves_legacy_clip_audio_from_bound_cue(tmp_path: Path):
    cue_audio = tmp_path / "cue-fallback.wav"
    audio_tools.write_audio(cue_audio, np.full(1_000, 0.2, dtype=np.float32), 1_000)
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=1_000,
                tts_audio_path=str(cue_audio),
            )
        ],
        timeline_clips=[
            {
                "clip_id": "legacy-cue-clip",
                "track_id": "dub",
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1_000,
                "dub_lane": 0,
            }
        ],
    )
    mixdown_path = tmp_path / "legacy-cue-mix.wav"

    mixed_tracks = video_localization_exporting._write_localized_mixdown(
        mixdown_path,
        draft,
        tmp_path / "legacy-dub.wav",
        1_000,
    )

    mixed_audio, _ = audio_tools.read_audio(mixdown_path)
    assert 0.195 <= float(np.max(np.abs(mixed_audio))) <= 0.205
    assert mixed_tracks[0]["source_path"] == str(cue_audio)


def test_single_tts_result_replaces_older_subtitle_fragments_and_uses_audio_duration(tmp_path: Path):
    output_path = tmp_path / "generated.wav"
    audio_tools.write_audio(output_path, np.full(1_250, 0.2, dtype=np.float32), 1_000)
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=2_000,
                end_ms=3_000,
                text="字幕",
                tts_text="字幕",
                source_cue_ids=["cue_0001"],
            )
        ],
        timeline_clips=[
            {
                "clip_id": "old",
                "track_id": "dub",
                "subtitle_id": "localized_0001",
                "task_id": "old-task",
                "start_ms": 2_000,
                "end_ms": 2_800,
                "audio_path": "old.wav",
                "dub_lane": 0,
            },
            {
                "clip_id": "new",
                "track_id": "dub",
                "subtitle_id": "localized_0001",
                "task_id": "new-task",
                "candidate_id": "candidate_old-task",
                "cqc_status": "passed",
                "cqc_report_version": "dubbing-candidate-cqc-v1",
                "timeline_edit_gate": {"status": "passed"},
                "start_ms": 2_000,
                "end_ms": 3_000,
                "dub_lane": 1,
            },
        ],
    )

    updated = video_localization_tts_pipeline.with_single_tts_result(
        draft,
        "localized_0001",
        result_id="result-new",
        output_path=str(output_path),
        duration_ms=1_250,
        task_id="new-task",
    )

    assert [item["clip_id"] for item in updated.timeline_clips] == ["new"]
    new_clip = next(item for item in updated.timeline_clips if item["clip_id"] == "new")
    assert new_clip["audio_path"] == str(output_path)
    assert new_clip["start_ms"] == 2_000
    assert new_clip["end_ms"] == 3_250
    assert new_clip["source_start_ms"] == 0
    assert new_clip["source_end_ms"] == 1_250
    assert new_clip["dub_lane"] == 1
    assert new_clip["candidate_id"] == "candidate_new-task"
    assert new_clip["cqc_status"] == "not_reviewed"
    assert new_clip["cqc_report_version"] is None
    assert "timeline_edit_gate" not in new_clip


def test_single_tts_result_replaces_existing_localized_clip_without_placeholder(tmp_path: Path):
    output_path = tmp_path / "regenerated.wav"
    audio_tools.write_audio(output_path, np.full(900, 0.2, dtype=np.float32), 1_000)
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=2_000,
                end_ms=3_000,
                text="字幕",
                tts_text="字幕",
                source_cue_ids=["cue_0001"],
            )
        ],
        timeline_clips=[
            {
                "clip_id": "existing",
                "track_id": "dub",
                "subtitle_id": "localized_0001",
                "task_id": "old-task",
                "start_ms": 2_000,
                "end_ms": 2_800,
                "audio_path": "old.wav",
                "dub_lane": 2,
            }
        ],
    )

    updated = video_localization_tts_pipeline.with_single_tts_result(
        draft,
        "localized_0001",
        result_id="result-new",
        output_path=str(output_path),
        duration_ms=900,
        task_id="new-task",
    )

    assert len(updated.timeline_clips) == 1
    clip = updated.timeline_clips[0]
    assert clip["clip_id"] == "existing"
    assert clip["task_id"] == "new-task"
    assert clip["audio_path"] == str(output_path)
    assert clip["start_ms"] == 2_000
    assert clip["end_ms"] == 2_900
    assert clip["dub_lane"] == 2


def test_single_tts_result_preserves_manual_history_copy_on_another_lane(tmp_path: Path):
    output_path = tmp_path / "regenerated-with-manual-copy.wav"
    audio_tools.write_audio(output_path, np.full(900, 0.2, dtype=np.float32), 1_000)
    draft = VideoLocalizationDraft(
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=2_000,
                end_ms=3_000,
                text="字幕",
                source_cue_ids=["cue_0001"],
            )
        ],
        timeline_clips=[
            {
                "clip_id": "manual-copy",
                "track_id": "dub",
                "subtitle_id": "localized_0001",
                "result_id": "history-old",
                "manual_history_copy": True,
                "start_ms": 4_000,
                "end_ms": 4_800,
                "dub_lane": 1,
            },
            {
                "clip_id": "current",
                "track_id": "dub",
                "subtitle_id": "localized_0001",
                "task_id": "task-new",
                "start_ms": 2_000,
                "end_ms": 3_000,
                "dub_lane": 0,
            },
        ],
    )

    updated = video_localization_tts_pipeline.with_single_tts_result(
        draft,
        "localized_0001",
        result_id="result-new",
        output_path=str(output_path),
        duration_ms=900,
        task_id="task-new",
    )

    assert [item["clip_id"] for item in updated.timeline_clips] == ["manual-copy", "current"]
    assert updated.timeline_clips[0]["manual_history_copy"] is True
    assert updated.timeline_clips[1]["result_id"] == "result-new"


def test_discarded_single_tts_task_cannot_restore_deleted_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "已删除配音不回流", "description": ""}).json()
    output_path = tmp_path / "late-result.wav"
    output_path.write_bytes(b"late-result")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "ui_state": {"discarded_tts_task_ids": ["task-deleted"]},
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "text": "字幕",
                    "tts_text": "字幕",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "timeline_clips": [],
        },
    )

    with pytest.raises(AppException) as error:
        video_localization_service.sync_single_tts_result(
            project["project_id"],
            "localized_0001",
            result_id="result-late",
            output_path=str(output_path),
            duration_ms=1_000,
            task_id="task-deleted",
            generation_id="task-deleted",
        )

    assert error.value.code == "VIDEO_LOCALIZATION_TTS_RESULT_DISCARDED"
    updated = draft_store.get(project["project_id"])
    assert updated is not None
    assert updated.timeline_clips == []
    assert updated.localized_subtitles[0].tts_result_id is None
    adopted = _project_root(project["project_id"]) / "tts" / "localized_0001" / "task-deleted.wav"
    assert not adopted.exists()


def test_older_single_tts_task_finishing_late_cannot_replace_latest_result(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "只采用最新配音", "description": ""}).json()
    old_output = tmp_path / "old.wav"
    new_output = tmp_path / "new.wav"
    audio_tools.write_audio(old_output, np.full(900, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(new_output, np.full(1_100, 0.2, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "text": "字幕",
                    "tts_text": "字幕",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    plan = _create_current_dubbing_plan(
        client,
        project["project_id"],
    )
    lineage = _dubbing_lineage_kwargs(plan, "localized_0001")
    _set_canonical_tts_tasks(
        client,
        project["project_id"],
        [
            _canonical_tts_task(project["project_id"], "workflow-old"),
            _canonical_tts_task(project["project_id"], "workflow-new"),
        ],
    )
    video_localization_service.register_single_tts_task(
        project["project_id"], "localized_0001", "task-old", "workflow-old"
    )
    video_localization_service.register_single_tts_task(
        project["project_id"], "localized_0001", "task-new", "workflow-new"
    )

    video_localization_service.sync_single_tts_result(
        project["project_id"],
        "localized_0001",
        result_id="result-new",
        output_path=str(new_output),
        duration_ms=1_100,
        task_id="task-new",
        generation_id="task-new",
        workflow_id="workflow-new",
        **lineage,
    )
    video_localization_service.sync_single_tts_result(
        project["project_id"],
        "localized_0001",
        result_id="result-old",
        output_path=str(old_output),
        duration_ms=900,
        task_id="task-old",
        generation_id="task-old",
        workflow_id="workflow-old",
        **lineage,
    )

    updated = video_localization_service.get_video_localization(project["project_id"])
    assert updated is not None
    assert len(updated.timeline_clips) == 1
    assert updated.timeline_clips[0]["task_id"] == "task-new"
    assert updated.timeline_clips[0]["result_id"] == "result-new"
    assert updated.timeline_clips[0]["source_end_ms"] == 1_100
    assert updated.localized_subtitles[0].tts_result_id == "result-new"
    assert not (_project_root(project["project_id"]) / "tts" / "localized_0001" / "task-old.wav").exists()


def test_managed_tts_sync_keeps_candidate_out_of_timeline_until_closeout(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "托管候选完成后再落轨", "description": ""},
    ).json()
    output_path = tmp_path / "managed-result.wav"
    audio_tools.write_audio(
        output_path,
        np.full(1_000, 0.2, dtype=np.float32),
        1_000,
    )
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "en_subtitle_text": "Source line.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "text": "字幕",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "current-take",
                    "track_id": "dub",
                    "target_subtitle_ids": ["localized_0001"],
                    "result_id": "result-current",
                    "audio_path": "/tmp/current.wav",
                    "status": "ready",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "source_start_ms": 0,
                    "source_end_ms": 1_000,
                    "dub_lane": 0,
                }
            ],
        },
    )
    plan = _create_current_dubbing_plan(client, project["project_id"])
    lineage = _dubbing_lineage_kwargs(plan, "localized_0001")
    workflow = _canonical_tts_task(
        project["project_id"],
        "workflow-managed",
    )
    workflow["stages"][0]["parameters"] = {
        "video_localization_execution_scope": "all_remaining"
    }
    _set_canonical_tts_tasks(
        client,
        project["project_id"],
        [workflow],
    )
    video_localization_service.register_single_tts_task(
        project["project_id"],
        "localized_0001",
        "task-managed",
        "workflow-managed",
    )

    updated = video_localization_service.sync_single_tts_result(
        project["project_id"],
        "localized_0001",
        result_id="result-managed",
        output_path=str(output_path),
        duration_ms=1_000,
        task_id="task-managed",
        generation_id="task-managed",
        workflow_id="workflow-managed",
        **lineage,
    )

    assert updated is not None
    assert [clip["clip_id"] for clip in updated.timeline_clips] == [
        "current-take"
    ]
    managed_candidate = next(
        item
        for item in updated.generated_candidates
        if item.get("task_id") == "task-managed"
    )
    assert managed_candidate["result_id"] == "result-managed"
    assert "cqc_status" not in managed_candidate
    assert "accepted" not in managed_candidate
    saved_workflow = next(
        item
        for item in updated.tts_tasks
        if item.workflow_id == "workflow-managed"
    )
    assert [stage.status for stage in saved_workflow.stages] == [
        "success",
        "running",
    ]
    assert saved_workflow.timeline_clip_id is None

    failed = video_localization_service.mark_tts_workflow_placement_failed(
        project["project_id"],
        "workflow-managed",
        error_code="TTS_PLACEMENT_REGENERATION_REQUIRED",
        error_message="当前候选不能安全落位。",
    )
    assert failed is not None
    replayed = video_localization_service.sync_single_tts_result(
        project["project_id"],
        "localized_0001",
        result_id="result-managed",
        output_path=str(output_path),
        duration_ms=1_000,
        task_id="task-managed",
        generation_id="task-managed",
        workflow_id="workflow-managed",
        **lineage,
    )
    assert replayed is not None
    replayed_workflow = next(
        item
        for item in replayed.tts_tasks
        if item.workflow_id == "workflow-managed"
    )
    assert replayed_workflow.status == "failed"
    assert [stage.status for stage in replayed_workflow.stages] == [
        "success",
        "failed",
    ]


def test_managed_tts_result_replay_does_not_reopen_completed_placement(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "托管结果重启回放", "description": ""},
    ).json()
    output_path = tmp_path / "managed-replay.wav"
    audio_tools.write_audio(
        output_path,
        np.full(1_000, 0.2, dtype=np.float32),
        1_000,
    )
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "en_subtitle_text": "Source line.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "text": "字幕",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "current-managed-result",
                    "track_id": "dub",
                    "target_subtitle_ids": ["localized_0001"],
                    "result_id": "result-managed",
                    "audio_path": str(output_path),
                    "status": "ready",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "source_start_ms": 0,
                    "source_end_ms": 1_000,
                    "dub_lane": 0,
                }
            ],
        },
    )
    plan = _create_current_dubbing_plan(client, project["project_id"])
    lineage = _dubbing_lineage_kwargs(plan, "localized_0001")
    workflow = _canonical_tts_task(project["project_id"], "workflow-managed")
    workflow["stages"][0]["parameters"] = {
        "video_localization_execution_scope": "all_remaining"
    }
    _set_canonical_tts_tasks(client, project["project_id"], [workflow])
    video_localization_service.register_single_tts_task(
        project["project_id"],
        "localized_0001",
        "task-managed",
        "workflow-managed",
    )

    updated = video_localization_service.sync_single_tts_result(
        project["project_id"],
        "localized_0001",
        result_id="result-managed",
        output_path=str(output_path),
        duration_ms=1_000,
        task_id="task-managed",
        generation_id="task-managed",
        workflow_id="workflow-managed",
        **lineage,
    )

    assert updated is not None
    saved_workflow = next(
        item
        for item in updated.tts_tasks
        if item.workflow_id == "workflow-managed"
    )
    assert saved_workflow.status == "success"
    assert [stage.status for stage in saved_workflow.stages] == [
        "success",
        "success",
    ]
    assert saved_workflow.timeline_clip_id == "current-managed-result"

    preserved = video_localization_service.mark_tts_workflow_placement_failed(
        project["project_id"],
        "workflow-managed",
        error_code="TTS_PLACEMENT_REGENERATION_REQUIRED",
        error_message="过期的失败回放不应覆盖正式结果。",
    )
    assert preserved is not None
    preserved_workflow = next(
        item
        for item in preserved.tts_tasks
        if item.workflow_id == "workflow-managed"
    )
    assert preserved_workflow.status == "success"
    assert [stage.status for stage in preserved_workflow.stages] == [
        "success",
        "success",
    ]


def test_cleanup_unused_tts_history_keeps_timeline_audio_and_other_segments(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "清理未使用配音", "description": ""}).json()
    project_tts = _project_root(project["project_id"]) / "tts"
    used_project_audio = project_tts / "localized_0001" / "used.wav"
    unused_project_audio = project_tts / "localized_0001" / "unused.wav"
    other_project_audio = project_tts / "localized_0002" / "other.wav"
    other_orphan_audio = project_tts / "localized_0002" / "orphan.wav"
    used_output = tmp_path / "used-output.wav"
    unused_output = tmp_path / "unused-output.wav"
    other_output = tmp_path / "other-output.wav"
    for path in (
        used_project_audio,
        unused_project_audio,
        other_project_audio,
        other_orphan_audio,
        used_output,
        unused_output,
        other_output,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.stem.encode())

    _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "text": "第一条",
                    "tts_result_id": "unused-result",
                    "tts_generation_id": "unused-task",
                    "tts_audio_path": str(unused_project_audio),
                    "generated_duration_ms": 900,
                },
                {
                    "subtitle_id": "localized_0002",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "text": "第二条",
                },
            ],
            "generated_candidates": [
                {
                    "candidate_id": "candidate-unused",
                    "subtitle_id": "localized_0001",
                    "result_id": "unused-result",
                    "task_id": "unused-task",
                    "audio_path": str(unused_project_audio),
                    "status": "success",
                },
                {
                    "candidate_id": "candidate-other",
                    "subtitle_id": "localized_0002",
                    "result_id": "other-result",
                    "task_id": "other-task",
                    "audio_path": str(other_project_audio),
                    "status": "success",
                },
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip-used",
                    "track_id": "dub",
                    "subtitle_id": "localized_0001",
                    "task_id": "used-task",
                    "audio_path": str(used_project_audio),
                    "start_ms": 1_000,
                    "end_ms": 1_900,
                }
            ],
        },
    )
    for result_id, task_id, segment_id, output_path in (
        ("used-result", "used-task", "localized_0001", used_output),
        ("unused-result", "unused-task", "localized_0001", unused_output),
        ("other-result", "other-task", "localized_0002", other_output),
    ):
        history_store.add(
            HistoryItem(
                result_id=result_id,
                task_id=task_id,
                engine_id="omnivoice",
                project_id=project["project_id"],
                segment_id=segment_id,
                localized_subtitle_id=segment_id,
                bind_to_video_localization=True,
                input_text=segment_id,
                output_path=str(output_path),
                parameter_snapshot={"source": "video_localization"},
            )
        )
    task_queue._save(
        GenerationTask(
            task_id="unused-task",
            generation_id="unused-task",
            engine_id="omnivoice",
            project_id=project["project_id"],
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            bind_to_video_localization=True,
            input_text="第一条",
            status=TaskStatus.success,
            result_audio_id="unused-task",
            result_id="unused-result",
            result_duration_ms=900,
            parameters={"source": "video_localization"},
        )
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/history/cleanup-unused",
        json={"segment_id": "localized_0001"},
    )

    assert response.status_code == 200
    assert response.json() == {"removed_records": 1, "removed_files": 1, "kept_used_records": 1}
    assert history_store.get("used-result") is not None
    assert history_store.get("unused-result") is None
    assert history_store.get("other-result") is not None
    assert used_output.exists() and used_project_audio.exists()
    assert not unused_output.exists() and not unused_project_audio.exists()
    assert other_output.exists() and other_project_audio.exists() and other_orphan_audio.exists()
    saved = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert [item["candidate_id"] for item in saved["generated_candidates"]] == ["candidate-other"]
    assert saved["localized_subtitles"][0]["tts_result_id"] is None
    assert saved["timeline_clips"][0]["task_id"] == "used-task"
    assert "unused-task" in saved["ui_state"]["discarded_tts_task_ids"]
    cleaned_task = task_queue.get_task("unused-task")
    assert cleaned_task is not None
    assert cleaned_task.result_id is None
    assert cleaned_task.artifacts_removed_at

    global_response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/history/cleanup-unused",
        json={},
    )

    assert global_response.status_code == 200
    assert global_response.json()["removed_records"] == 1
    assert global_response.json()["kept_used_records"] == 1
    assert history_store.get("used-result") is not None
    assert history_store.get("other-result") is None
    assert used_output.exists() and used_project_audio.exists()
    assert not other_output.exists()
    assert not other_project_audio.exists()
    assert not other_orphan_audio.exists()


def test_cleanup_unused_tts_history_rejects_active_generation(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "生成中不可清理", "description": ""}).json()
    task_queue._save(
        GenerationTask(
            task_id="active-cleanup-task",
            engine_id="omnivoice",
            project_id=project["project_id"],
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            bind_to_video_localization=True,
            input_text="生成中的字幕",
            status=TaskStatus.running,
            parameters={"source": "video_localization"},
        )
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/history/cleanup-unused",
        json={"segment_id": "localized_0001"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_CLEANUP_ACTIVE"


def test_bulk_delete_project_tts_history_removes_more_than_page_limit_in_one_command(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "批量删除容量", "description": ""},
    ).json()
    project_id = project["project_id"]
    other_project = client.post(
        "/api/projects",
        json={"name": "其他项目", "description": ""},
    ).json()
    localized_items = [
        HistoryItem(
            result_id=f"bulk-result-{index:04d}",
            task_id=f"bulk-task-{index:04d}",
            engine_id="indextts-v2",
            project_id=project_id,
            segment_id=f"localized-{index % 3}",
            input_text=f"第 {index} 条",
            parameter_snapshot={"source": "video_localization"},
        )
        for index in range(501)
    ]
    retained_items = [
        HistoryItem(
            result_id="same-project-regular",
            task_id="same-project-regular-task",
            engine_id="indextts-v2",
            project_id=project_id,
            input_text="普通生成历史",
            parameter_snapshot={},
        ),
        HistoryItem(
            result_id="other-project-localized",
            task_id="other-project-localized-task",
            engine_id="indextts-v2",
            project_id=other_project["project_id"],
            input_text="其他项目本土化历史",
            parameter_snapshot={"source": "video_localization"},
        ),
    ]
    serialized_items = [item.model_dump(mode="json") for item in [*localized_items, *retained_items]]
    with database.conn() as connection:
        for item in serialized_items:
            history_store.add_from_connection(
                connection,
                HistoryItem(**item),
            )

    original_list_history = history_store.list_history
    list_calls = 0

    def counted_list_history(*args, **kwargs):
        nonlocal list_calls
        list_calls += 1
        return original_list_history(*args, **kwargs)

    monkeypatch.setattr(history_store, "list_history", counted_list_history)

    def reject_per_row_delete(*_args, **_kwargs):
        raise AssertionError("bulk deletion must not call delete_one per record")

    monkeypatch.setattr(database, "delete_one", reject_per_row_delete)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/history/delete",
        json={"scope": "project"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "removed_records": 501,
        "cleanup_failures": 0,
    }
    assert list_calls == 1
    assert (
        original_list_history(
            limit=-1,
            project_id=project_id,
            source="video_localization",
        )
        == []
    )
    assert history_store.get("same-project-regular") is not None
    assert history_store.get("other-project-localized") is not None


def test_bulk_delete_tts_history_validates_and_applies_explicit_scopes(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "批量删除范围", "description": ""},
    ).json()
    other_project = client.post(
        "/api/projects",
        json={"name": "范围隔离项目", "description": ""},
    ).json()
    for result_id, project_id, segment_id in (
        ("segment-a", project["project_id"], "localized-a"),
        ("segment-b", project["project_id"], "localized-b"),
        ("segment-other", other_project["project_id"], "localized-a"),
    ):
        history_store.add(
            HistoryItem(
                result_id=result_id,
                task_id=f"task-{result_id}",
                engine_id="indextts-v2",
                project_id=project_id,
                segment_id=segment_id,
                input_text=result_id,
                parameter_snapshot={"source": "video_localization"},
            )
        )
        task_queue._save(
            GenerationTask(
                task_id=f"task-{result_id}",
                engine_id="indextts-v2",
                project_id=project_id,
                segment_id=segment_id,
                bind_to_video_localization=True,
                input_text=result_id,
                status=TaskStatus.success,
                result_audio_id=result_id,
                result_id=result_id,
                result_duration_ms=1_000,
                parameters={"source": "video_localization"},
            )
        )
    history_store.add(
        HistoryItem(
            result_id="group-containing-a",
            task_id="task-group-containing-a",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            segment_id="group-localized-b-localized-c",
            localized_subtitle_id="localized-b",
            input_text="包含当前字幕的多段配音",
            parameter_snapshot={
                "source": "video_localization",
                "video_localization_target_subtitle_ids": [
                    "localized-a",
                    "localized-b",
                    "localized-c",
                ],
            },
        )
    )

    segment_response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/history/delete",
        json={"scope": "segment", "segment_id": "localized-a"},
    )
    explicit_response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/history/delete",
        json={
            "scope": "result_ids",
            "result_ids": ["segment-b", "segment-other"],
        },
    )

    assert segment_response.status_code == 200
    assert segment_response.json() == {
        "removed_records": 2,
        "cleanup_failures": 0,
    }
    assert explicit_response.status_code == 200
    assert explicit_response.json() == {
        "removed_records": 1,
        "cleanup_failures": 0,
    }
    assert history_store.get("segment-a") is None
    assert history_store.get("group-containing-a") is None
    assert history_store.get("segment-b") is None
    assert history_store.get("segment-other") is not None
    for task_id in ("task-segment-a", "task-segment-b"):
        task = task_queue.get_task(task_id)
        assert task is not None
        assert task.result_id is None
        assert task.result_audio_id is None
        assert task.artifacts_removed_at
    retained_task = task_queue.get_task("task-segment-other")
    assert retained_task is not None
    assert retained_task.result_id == "segment-other"

    for invalid_body in (
        {"scope": "result_ids", "result_ids": []},
        {"scope": "result_ids", "result_ids": [" "]},
        {"scope": "segment"},
        {"scope": "project", "segment_id": "localized-a"},
        {"scope": "segment", "segment_id": "localized-a", "result_ids": ["x"]},
    ):
        response = client.post(
            f"/api/projects/{project['project_id']}/video-localization/tts/history/delete",
            json=invalid_body,
        )
        assert response.status_code == 400


def test_cleanup_unused_tts_history_rejects_active_workflow_placement(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "回填中不可清理", "description": ""}).json()
    project_id = project["project_id"]
    output = tmp_path / "outputs" / "placement-pending.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"generated")
    history_store.add(
        HistoryItem(
            result_id="placement-pending-result",
            task_id="placement-pending-task",
            generation_id="placement-pending-task",
            engine_id="omnivoice",
            project_id=project_id,
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="等待回填",
            output_path=str(output),
            parameter_snapshot={"source": "video_localization"},
        )
    )
    task_queue._save(
        GenerationTask(
            task_id="placement-pending-task",
            engine_id="omnivoice",
            project_id=project_id,
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="等待回填",
            status=TaskStatus.success,
            result_id="placement-pending-result",
            parameters={"source": "video_localization"},
        )
    )
    draft = VideoLocalizationDraft(
        tts_tasks=[
            VideoLocalizationTtsTask(
                workflow_id="placement-pending-workflow",
                project_id=project_id,
                segment_id="localized_0001",
                subtitle_summary="等待回填",
                text="等待回填",
                source_cue_ids=["cue_0001"],
                start_ms=1000,
                end_ms=2000,
                status="running",
                generation_task_id="placement-pending-task",
                stages=[
                    VideoLocalizationTtsTaskStage(
                        kind="generation",
                        status="success",
                        progress=1.0,
                    ),
                    VideoLocalizationTtsTaskStage(
                        kind="placement",
                        status="running",
                        progress=0.25,
                    ),
                ],
            )
        ]
    )
    assert video_localization_service.save_video_localization(project_id, draft) is not None

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/history/cleanup-unused",
        json={"segment_id": "localized_0001"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_CLEANUP_ACTIVE"
    assert history_store.get("placement-pending-result") is not None
    assert output.exists()


def test_history_delete_keeps_output_shared_by_another_record(tmp_path: Path):
    _client(tmp_path)
    output = tmp_path / "shared-output.wav"
    output.write_bytes(b"shared")
    for result_id in ("shared-result-a", "shared-result-b"):
        history_store.add(
            HistoryItem(
                result_id=result_id,
                task_id=f"task-{result_id}",
                engine_id="omnivoice",
                input_text=result_id,
                output_path=str(output),
                parameter_snapshot={},
            )
        )

    history_store.delete("shared-result-a")

    assert output.exists()
    assert history_store.get("shared-result-a") is None
    assert history_store.get("shared-result-b") is not None


def test_single_tts_sync_serializes_with_ui_state_updates(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "配音并发回填", "description": ""}).json()
    output_path = tmp_path / "generated.wav"
    audio_tools.write_audio(output_path, np.full(1_000, 0.2, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 2_000,
                    "end_ms": 3_000,
                    "text": "字幕",
                    "tts_text": "字幕",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    plan = _create_current_dubbing_plan(
        client,
        project["project_id"],
    )
    lineage = _dubbing_lineage_kwargs(plan, "localized_0001")
    _set_canonical_tts_tasks(
        client,
        project["project_id"],
        [_canonical_tts_task(project["project_id"], "workflow-concurrent")],
    )
    entered_sync = threading.Event()
    release_sync = threading.Event()
    ui_saved = threading.Event()
    errors: list[Exception] = []
    video_localization_service.register_single_tts_task(
        project["project_id"], "localized_0001", "task-concurrent", "workflow-concurrent"
    )
    original_sync = video_localization_service.tts_placement.with_explicit_single_tts_result

    def slow_sync(*args, **kwargs):
        entered_sync.set()
        assert release_sync.wait(timeout=2)
        return original_sync(*args, **kwargs)

    monkeypatch.setattr(
        video_localization_service.tts_placement,
        "with_explicit_single_tts_result",
        slow_sync,
    )

    def sync_result():
        try:
            video_localization_service.sync_single_tts_result(
                project["project_id"],
                "localized_0001",
                result_id="result-concurrent",
                output_path=str(output_path),
                duration_ms=1_000,
                task_id="task-concurrent",
                generation_id="task-concurrent",
                workflow_id="workflow-concurrent",
                **lineage,
            )
        except Exception as exc:  # pragma: no cover - assertion reports the captured error
            errors.append(exc)

    def save_ui_state():
        try:
            video_localization_service.update_video_localization_ui_state(
                project["project_id"],
                {"timeline_zoom": 12},
            )
            ui_saved.set()
        except Exception as exc:  # pragma: no cover - assertion reports the captured error
            errors.append(exc)

    sync_thread = threading.Thread(target=sync_result)
    ui_thread = threading.Thread(target=save_ui_state)
    sync_thread.start()
    assert entered_sync.wait(timeout=2)
    ui_thread.start()
    assert not ui_saved.wait(timeout=0.05)
    release_sync.set()
    sync_thread.join(timeout=2)
    ui_thread.join(timeout=2)

    assert errors == []
    updated = video_localization_service.get_video_localization(project["project_id"])
    assert updated is not None
    assert updated.ui_state["timeline_zoom"] == 12
    assert len(updated.timeline_clips) == 1
    assert updated.timeline_clips[0]["task_id"] == "task-concurrent"
    assert updated.timeline_clips[0]["audio_path"].endswith("task-concurrent.wav")


@pytest.mark.parametrize("generation_attempt", [1, 3, 4])
def test_video_localization_handoff_uses_materialized_reference_duration(tmp_path: Path, monkeypatch, generation_attempt):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "参考音实际时长", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    reference_path = tmp_path / "reference-clip.wav"
    audio_tools.write_audio(vocals_path, np.full(10_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(reference_path, np.full(2_200, 0.1, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1_000,
                    "end_ms": 4_000,
                    "en_subtitle_text": "Source line.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 4_000,
                    "text": "本土化台词。",
                    "tts_text": "本土化台词。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )

    managed = type("ManagedAudio", (), {"file_id": "source-file", "path": str(vocals_path), "duration_ms": 10_000})()
    clip_voice = type("ClipVoice", (), {"duration_ms": 2_200})()
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: managed,
    )
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(reference_path), "voice_file": clip_voice},
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff/localized_0001",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": ["cue_0001"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["custom_reference_trim_start_ms"] == 1_000
    assert payload["custom_reference_trim_end_ms"] == 3_200
    # Managed execution attaches its durable ordinal after public preparation;
    # validate the real request at the same serialization boundary as submit.
    from app.schemas.voice_studio import GenerateRequest
    managed = GenerateRequest.model_validate({**payload,
        "video_localization_generation_attempt": generation_attempt})
    assert managed.video_localization_generation_attempt == generation_attempt


def test_video_localization_handoff_rebuilds_authoritative_fields_when_reusing_history(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "沿用参数服务端复核", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    reference_path = tmp_path / "reference.wav"
    adjusted_reference = tmp_path / "adjusted-reference.wav"
    stale_reference = tmp_path / "stale-reference.wav"
    audio_tools.write_audio(vocals_path, np.full(8_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(reference_path, np.full(2_500, 0.2, dtype=np.float32), 1_000)
    audio_tools.write_audio(adjusted_reference, np.full(2_200, 0.25, dtype=np.float32), 1_000)
    audio_tools.write_audio(stale_reference, np.full(3_000, 0.3, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1_000,
                    "end_ms": 2_800,
                    "en_subtitle_text": "Authoritative source text.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_800,
                    "text": "当前字幕",
                    "tts_text": "当前项目台词",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    _create_current_dubbing_plan(client, project["project_id"])
    managed = type("ManagedAudio", (), {"file_id": "source-file", "path": str(vocals_path), "duration_ms": 99_000})()
    clip_voice = type("ClipVoice", (), {"duration_ms": 9_999})()
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: managed,
    )
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(reference_path), "voice_file": clip_voice},
    )
    history_store.add(
        HistoryItem(
            result_id="history-reuse-server-check",
            task_id="old-task",
            engine_id="omnivoice",
            project_id=project["project_id"],
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="旧台词",
            parameter_snapshot={
                "source": "video_localization",
                "text": "不能透传的旧台词",
                "project_id": "wrong-project",
                "segment_id": "wrong-segment",
                "reference_audio_path": str(stale_reference),
                "custom_reference_source_audio_path": str(stale_reference),
                "custom_reference_source_duration_ms": 3_000,
                "custom_reference_trim_start_ms": 0,
                "custom_reference_trim_end_ms": 3_000,
                "engine_id": "omnivoice",
                "speed": 1.35,
                "engine_parameters": {"reference_audio_path": str(stale_reference), "temperature": 0.7},
            },
        )
    )

    preview_response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff-preview/localized_0001",
        json={
            "history_result_id": "history-reuse-server-check",
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
        },
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["video_localization_workflow_id"] is None
    assert preview_payload["video_localization_submission_id"]
    assert client.get(f"/api/projects/{project['project_id']}/video-localization/tts/tasks").json() == []

    finalized_preview = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(preview_payload)
    )
    preview_workflow_id = finalized_preview.video_localization_workflow_id
    assert preview_workflow_id
    assert preview_workflow_id == preview_payload["video_localization_submission_id"]
    prepared_after_submit = client.get(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks/{preview_workflow_id}"
    ).json()
    assert prepared_after_submit["workflow_id"] == preview_workflow_id
    assert prepared_after_submit["stages"][0]["parameters"]["video_localization_workflow_id"] == preview_workflow_id
    assert prepared_after_submit["status"] == "prepared"

    cancelled_preview = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks/{preview_workflow_id}/cancel"
    )
    assert cancelled_preview.status_code == 200
    assert cancelled_preview.json()["status"] == "cancelled"
    deleted_preview = client.delete(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks/{preview_workflow_id}"
    )
    assert deleted_preview.status_code == 200
    assert deleted_preview.json()["tts_tasks"] == []

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff/localized_0001",
        json={
            "submission_id": "abc123def456",
            "history_result_id": "history-reuse-server-check",
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
            "parameters": {
                "content_speed_exception_reason": "目标台词需完整落在已锁定语义窗口内",
                "content_speed_exception_evidence_ids": ["localized_0001"],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["video_localization_workflow_id"] == "abc123def456"
    assert payload["text"] == "当前项目台词"
    assert payload["project_id"] == project["project_id"]
    assert payload["segment_id"] == "localized_0001"
    assert payload["reference_audio_path"] == str(reference_path)
    assert payload["custom_reference_source_audio_path"] == str(vocals_path)
    assert payload["custom_reference_source_duration_ms"] == 8_000
    assert payload["custom_reference_trim_start_ms"] == 1_000
    assert payload["custom_reference_trim_end_ms"] == 3_500
    assert payload["video_localization_start_ms"] == 1_000
    assert payload["video_localization_end_ms"] == 2_800
    assert payload["video_localization_source_cue_ids"] == ["cue_0001"]
    assert payload["engine_id"] == "omnivoice"
    assert payload["speed"] == 1.35
    assert (
        payload["content_speed_exception_reason"]
        == "目标台词需完整落在已锁定语义窗口内"
    )
    assert payload["content_speed_exception_evidence_ids"] == ["localized_0001"]
    assert payload["engine_parameters"] == {"temperature": 0.7}

    tasks = client.get(f"/api/projects/{project['project_id']}/video-localization/tts/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["workflow_id"] == payload["video_localization_workflow_id"]
    assert tasks[0]["subtitle_summary"] == "当前项目台词"
    assert tasks[0]["status"] == "prepared"
    assert [stage["kind"] for stage in tasks[0]["stages"]] == ["generation", "placement"]
    assert tasks[0]["stages"][0]["parameters"]["handoff_mode"] == "reuse"

    finalized = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(
            {
                **payload,
                "text": "客户端旧台词",
                "ref_text": "识别并使用后的准确参考台词。",
                "reference_audio_path": str(adjusted_reference),
                "custom_reference_source_audio_path": str(vocals_path),
                "custom_reference_source_duration_ms": 8_000,
                "custom_reference_trim_start_ms": 3_200,
                "custom_reference_trim_end_ms": 5_400,
            }
        )
    )
    assert finalized.text == "客户端旧台词"
    assert finalized.ref_text == "识别并使用后的准确参考台词。"
    assert finalized.reference_audio_path == str(adjusted_reference)
    assert finalized.custom_reference_source_audio_path == str(vocals_path)
    assert finalized.custom_reference_source_duration_ms == 8_000
    assert finalized.custom_reference_trim_start_ms == 3_200
    assert finalized.custom_reference_trim_end_ms == 5_400
    assert finalized.video_localization_start_ms == 1_000
    assert finalized.video_localization_end_ms == 2_800
    assert finalized.video_localization_source_cue_ids == ["cue_0001"]
    saved_workflow = video_localization_service.get_tts_task(
        project["project_id"],
        finalized.video_localization_workflow_id or "",
    )
    assert saved_workflow is not None
    assert saved_workflow.stages[0].parameters["ref_text"] == "识别并使用后的准确参考台词。"
    assert saved_workflow.stages[0].parameters["reference_audio_path"] == str(adjusted_reference)
    assert saved_workflow.stages[0].parameters["custom_reference_trim_start_ms"] == 3_200
    assert saved_workflow.stages[0].parameters["custom_reference_trim_end_ms"] == 5_400

    with pytest.raises(AppException) as changed_source_error:
        video_localization_service.finalize_single_tts_submission(
            GenerateRequest.model_validate(
                {
                    **preview_payload,
                    "ref_text": "另一份音频的台词。",
                    "reference_audio_path": str(stale_reference),
                    "custom_reference_source_audio_path": str(stale_reference),
                    "custom_reference_source_duration_ms": 3_000,
                    "custom_reference_trim_start_ms": 0,
                    "custom_reference_trim_end_ms": 3_000,
                }
            )
        )
    assert changed_source_error.value.code == "VIDEO_LOCALIZATION_TTS_REFERENCE_SOURCE_CHANGED"

    invalid = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff/localized_0001",
        json={
            "parameters": {"speed": 9},
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
        },
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY"
    tasks_after_invalid_prepare = client.get(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks"
    ).json()
    assert len(tasks_after_invalid_prepare) == 1
    assert tasks_after_invalid_prepare[0]["status"] == "prepared"


def test_video_localization_reuses_parameters_across_subtitles_in_the_same_project():
    history_store.add(
        HistoryItem(
            result_id="history-reuse-across-subtitles",
            task_id="old-task",
            engine_id="omnivoice",
            project_id="project-a",
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            bind_to_video_localization=True,
            input_text="旧字幕",
            parameter_snapshot={"source": "video_localization", "speed": 0.9, "temperature": 0.7},
        )
    )

    parameters = video_localization_service._tts_reuse_parameter_values(
        project_id="project-a",
        segment_id="localized_0002",
        history_result_id="history-reuse-across-subtitles",
        parameters=None,
    )

    assert parameters["speed"] == 0.9
    assert parameters["temperature"] == 0.7
    assert "segment_id" not in parameters

    with pytest.raises(AppException) as exc_info:
        video_localization_service._tts_reuse_parameter_values(
            project_id="project-b",
            segment_id="localized_0002",
            history_result_id="history-reuse-across-subtitles",
            parameters=None,
        )
    assert exc_info.value.code == "VIDEO_LOCALIZATION_TTS_HISTORY_SEGMENT_MISMATCH"


def test_video_localization_tts_placement_skips_locked_lane_and_removes_stale_empty_lanes():
    draft = VideoLocalizationDraft.model_validate(
        {
            "timeline_clips": [
                {
                    "clip_id": "generated-dub",
                    "track_id": "dub",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "dub_lane": 0,
                    "status": "ready",
                }
            ],
            "ui_state": {
                "track_states": {"dub": {"label": "锁定主轨", "locked": True, "volume": 0.7}},
                "dub_lane_states": {
                    "0": {"label": "锁定主轨", "locked": True, "volume": 0.7},
                    "4": {"label": "历史空轨", "locked": False, "volume": 0.5},
                },
            },
        }
    )

    placed = video_localization_service._place_dub_clip_on_free_lane(draft, "generated-dub", 0)

    clip = next(item for item in placed.timeline_clips if item["clip_id"] == "generated-dub")
    assert clip["dub_lane"] == 1
    assert set(placed.ui_state["dub_lane_states"]) == {"0", "1"}
    assert placed.ui_state["dub_lane_states"]["0"]["label"] == "锁定主轨"
    assert placed.ui_state["dub_lane_states"]["1"]["locked"] is False


def test_video_localization_handoff_accepts_short_reference(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "短参考音阻断", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    reference_path = tmp_path / "too-short.wav"
    audio_tools.write_audio(vocals_path, np.full(8_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(reference_path, np.full(1_200, 0.2, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "cues": [{"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_000, "en_subtitle_text": "Source"}],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "text": "短句",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    managed = type("ManagedAudio", (), {"file_id": "source-file", "path": str(vocals_path), "duration_ms": 8_000})()
    clip_voice = type("ClipVoice", (), {"duration_ms": 1_200})()
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: managed,
    )
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(reference_path), "voice_file": clip_voice},
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff/localized_0001",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": ["cue_0001"]},
    )

    assert response.status_code == 200
    assert response.json()["reference_audio_path"] == str(reference_path)
    saved = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert len(saved["tts_tasks"]) == 1
    assert saved["tts_tasks"][0]["status"] == "prepared"


def test_video_localization_handoff_rejects_missing_source_cue_mapping(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺失原文映射", "description": ""}).json()
    vocals_path = tmp_path / "vocals.wav"
    audio_tools.write_audio(vocals_path, np.full(8_000, 0.1, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "localized_subtitles": [
                {"subtitle_id": "localized_0001", "start_ms": 1_000, "end_ms": 2_000, "text": "缺少映射"}
            ],
        },
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff/localized_0001",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": []},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_SOURCE_CUES_MISSING"
    assert client.get(f"/api/projects/{project['project_id']}/video-localization").json()["tts_tasks"] == []


def test_video_localization_handoff_is_durable_while_reference_audio_is_preparing(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "配音准备先登记", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    reference_path = tmp_path / "reference.wav"
    audio_tools.write_audio(vocals_path, np.full(8_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(reference_path, np.full(2_500, 0.2, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "en_subtitle_text": "Source line.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "text": "等待时也不能消失。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    managed = type(
        "ManagedAudio",
        (),
        {"file_id": "source-file", "path": str(vocals_path), "duration_ms": 8_000},
    )()
    clip_voice = type("ClipVoice", (), {"duration_ms": 2_500})()
    preparation_started = threading.Event()
    allow_preparation_to_finish = threading.Event()

    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: managed,
    )

    def delayed_audio_clip(*args, **kwargs):
        preparation_started.set()
        assert allow_preparation_to_finish.wait(timeout=5)
        return {"path": str(reference_path), "voice_file": clip_voice}

    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "create_audio_clip",
        delayed_audio_clip,
    )
    result: dict[str, GenerateRequest] = {}

    def prepare_handoff():
        result["request"] = video_localization_service.build_single_tts_handoff(
            project["project_id"],
            "localized_0001",
            target_subtitle_ids=["localized_0001"],
            source_cue_ids=["cue_0001"],
            workflow_id="feedfacecafe",
        )

    worker = threading.Thread(target=prepare_handoff)
    worker.start()
    assert preparation_started.wait(timeout=5)

    tasks_while_preparing = client.get(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks"
    ).json()
    assert tasks_while_preparing[0]["workflow_id"] == "feedfacecafe"
    assert tasks_while_preparing[0]["status"] == "prepared"

    allow_preparation_to_finish.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert result["request"].video_localization_workflow_id == "feedfacecafe"


def test_video_localization_reservation_rejects_a_second_active_workflow_for_same_target(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "同一字幕只能有一个活动配音", "description": ""},
    ).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "en_subtitle_text": "Source line.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "text": "同一条不能重复排队。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )

    first = video_localization_service.reserve_single_tts_handoff(
        project["project_id"],
        "localized_0001",
        target_subtitle_ids=["localized_0001"],
        source_cue_ids=["cue_0001"],
        workflow_id="workflow-one",
    )
    assert first is not None

    with pytest.raises(AppException) as busy_error:
        video_localization_service.reserve_single_tts_handoff(
            project["project_id"],
            "localized_0001",
            target_subtitle_ids=["localized_0001"],
            source_cue_ids=["cue_0001"],
            workflow_id="workflow-two",
        )

    assert busy_error.value.code == "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY"


def test_video_localization_reservation_ignores_duplicate_completed_by_same_segment(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "同段已有正式结果时不被重复任务锁死", "description": ""},
    ).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "en_subtitle_text": "Source line.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "text": "同一段可以继续重新生成。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    draft = draft_store.get(project["project_id"])
    assert draft is not None
    completed = VideoLocalizationTtsTask(
        workflow_id="workflow-placed",
        project_id=project["project_id"],
        segment_id="localized_0001",
        subtitle_summary="同一段",
        text="同一段",
        source_cue_ids=["cue_0001"],
        start_ms=1_000,
        end_ms=3_500,
        status="success",
        generation_task_id="task-placed",
        stages=[
            VideoLocalizationTtsTaskStage(kind="generation", status="success", progress=1.0),
            VideoLocalizationTtsTaskStage(kind="placement", status="success", progress=1.0),
        ],
    )
    duplicate = VideoLocalizationTtsTask(
        workflow_id="workflow-duplicate",
        project_id=project["project_id"],
        segment_id="localized_0001",
        subtitle_summary="同一段",
        text="同一段",
        source_cue_ids=["cue_0001"],
        start_ms=1_000,
        end_ms=3_500,
        status="running",
        generation_task_id="task-duplicate",
        stages=[
            VideoLocalizationTtsTaskStage(kind="generation", status="success", progress=1.0),
            VideoLocalizationTtsTaskStage(kind="placement", status="running", progress=0.5),
        ],
    )
    assert draft_store.save(
        project["project_id"],
        draft.model_copy(update={"tts_tasks": [completed, duplicate]}),
        intent="runtime",
    )
    monkeypatch.setattr(
        video_localization_service,
        "_reconcile_tts_workflow_task",
        lambda task, *_args: task,
    )
    monkeypatch.setattr(
        video_localization_service.task_queue,
        "get_tasks_by_ids",
        lambda _task_ids: {},
    )

    replacement = video_localization_service.reserve_single_tts_handoff(
        project["project_id"],
        "localized_0001",
        target_subtitle_ids=["localized_0001"],
        source_cue_ids=["cue_0001"],
        workflow_id="workflow-replacement",
    )

    assert replacement is not None
    assert replacement.workflow_id == "workflow-replacement"


def test_video_localization_reservation_ignores_reconciled_failed_workflow(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "失败任务不能永久锁住字幕", "description": ""},
    ).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "en_subtitle_text": "Source line.",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_500,
                    "text": "失败后可以重新排队。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    first = video_localization_service.reserve_single_tts_handoff(
        project["project_id"],
        "localized_0001",
        target_subtitle_ids=["localized_0001"],
        source_cue_ids=["cue_0001"],
        workflow_id="workflow-failed",
    )
    assert first is not None
    monkeypatch.setattr(
        video_localization_service,
        "_reconcile_tts_workflow_task",
        lambda task, *_args: task.model_copy(update={"status": "failed"}),
    )

    replacement = video_localization_service.reserve_single_tts_handoff(
        project["project_id"],
        "localized_0001",
        target_subtitle_ids=["localized_0001"],
        source_cue_ids=["cue_0001"],
        workflow_id="workflow-replacement",
    )

    assert replacement is not None
    assert replacement.workflow_id == "workflow-replacement"


def test_video_localization_tts_task_tracks_generation_and_placement_and_survives_client_save(
    tmp_path: Path, monkeypatch
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "配音任务持久化", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    reference_path = tmp_path / "reference.wav"
    output_path = tmp_path / "generated.wav"
    audio_tools.write_audio(vocals_path, np.full(8_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(reference_path, np.full(2_500, 0.2, dtype=np.float32), 1_000)
    audio_tools.write_audio(output_path, np.full(1_100, 0.3, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "cues": [{"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_800, "en_subtitle_text": "Source"}],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_800,
                    "text": "任务摘要台词",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "occupied-dub",
                    "track_id": "dub",
                    "start_ms": 900,
                    "end_ms": 2_500,
                    "dub_lane": 0,
                    "status": "ready",
                }
            ],
        },
    )
    plan = _create_current_dubbing_plan(
        client,
        project["project_id"],
    )
    lineage = _dubbing_lineage_kwargs(plan, "localized_0001")
    managed = type("ManagedAudio", (), {"file_id": "source-file", "path": str(vocals_path), "duration_ms": 8_000})()
    clip_voice = type("ClipVoice", (), {"duration_ms": 2_500})()
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: managed,
    )
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(reference_path), "voice_file": clip_voice},
    )
    prepared = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff/localized_0001",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": ["cue_0001"]},
    ).json()
    workflow_id = prepared["video_localization_workflow_id"]

    video_localization_service.register_single_tts_task(
        project["project_id"],
        "localized_0001",
        "task-workflow",
        workflow_id,
    )
    with database.conn() as connection:
        revision_before_replay = connection.execute(
            """
            SELECT repository_revision
            FROM projects
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()["repository_revision"]
    replayed_registration = video_localization_service.register_single_tts_task(
        project["project_id"],
        "localized_0001",
        "task-workflow",
        workflow_id,
    )
    with database.conn() as connection:
        revision_after_replay = connection.execute(
            """
            SELECT repository_revision
            FROM projects
            WHERE project_id = ?
            """,
            (project["project_id"],),
        ).fetchone()["repository_revision"]
    assert replayed_registration is not None
    assert revision_after_replay == revision_before_replay
    prepared_tasks = client.get(f"/api/projects/{project['project_id']}/video-localization/tts/tasks").json()
    assert len(prepared_tasks) == 1
    assert prepared_tasks[0]["generation_task_id"] == "task-workflow"
    monkeypatch.setattr(
        video_localization_service.task_queue,
        "get_tasks_by_ids",
        lambda _task_ids: {
            "task-workflow": GenerationTask(
                task_id="task-workflow",
                engine_id="indextts-v2",
                input_text="任务摘要台词",
                status=TaskStatus.running,
                progress=0.45,
            )
        },
    )
    full_draft_reads = 0
    original_draft_get = video_localization_service.draft_store.get

    def count_full_draft_read(project_id: str):
        nonlocal full_draft_reads
        full_draft_reads += 1
        return original_draft_get(project_id)

    monkeypatch.setattr(video_localization_service.draft_store, "get", count_full_draft_read)
    with database.conn() as connection:
        revision_before_poll = connection.execute(
            "SELECT repository_revision FROM projects WHERE project_id = ?",
            (project["project_id"],),
        ).fetchone()["repository_revision"]
    running = client.get(f"/api/projects/{project['project_id']}/video-localization/tts/tasks/{workflow_id}").json()
    with database.conn() as connection:
        revision_after_poll = connection.execute(
            "SELECT repository_revision FROM projects WHERE project_id = ?",
            (project["project_id"],),
        ).fetchone()["repository_revision"]
    assert running["status"] == "running"
    assert running["generation_task_id"] == "task-workflow"
    assert running["stages"][0]["progress"] == 0.45
    assert revision_after_poll == revision_before_poll
    assert full_draft_reads == 0
    monkeypatch.setattr(video_localization_service.draft_store, "get", original_draft_get)

    updated = video_localization_service.sync_single_tts_result(
        project["project_id"],
        "localized_0001",
        result_id="result-workflow",
        output_path=str(output_path),
        duration_ms=1_100,
        task_id="task-workflow",
        generation_id="task-workflow",
        **lineage,
    )
    assert updated is not None
    workflow = next(item for item in updated.tts_tasks if item.workflow_id == workflow_id)
    assert workflow.status == "success"
    assert workflow.result_id == "result-workflow"
    assert workflow.timeline_clip_id
    assert [stage.status for stage in workflow.stages] == ["success", "success"]
    placed_clip = next(item for item in updated.timeline_clips if item.get("task_id") == "task-workflow")
    placement_stage = next(stage for stage in workflow.stages if stage.kind == "placement")
    assert placed_clip["dub_lane"] == 1
    assert placement_stage.parameters["dub_lane"] == 1
    queried_after_callback = client.get(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks/{workflow_id}"
    ).json()
    assert queried_after_callback["status"] == "success"
    assert [stage["status"] for stage in queried_after_callback["stages"]] == ["success", "success"]

    repeated = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(
            {
                **prepared,
                "text": "任务摘要台词，二点零，四K。",
                "diffusion_steps": 31,
            }
        )
    )
    assert repeated.text == "任务摘要台词，二点零，四 K。"
    assert repeated.diffusion_steps == 31
    assert repeated.video_localization_workflow_id != workflow_id
    repeated_tasks = client.get(f"/api/projects/{project['project_id']}/video-localization/tts/tasks").json()
    repeated_workflow = next(
        item for item in repeated_tasks if item["workflow_id"] == repeated.video_localization_workflow_id
    )
    assert repeated_workflow["status"] == "prepared"
    assert repeated_workflow["text"] == repeated.text
    assert repeated_workflow["stages"][0]["parameters"]["handoff_mode"] == "repeat"
    assert repeated_workflow["stages"][0]["parameters"]["previous_workflow_id"] == workflow_id

    stale_client = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    stale_client["tts_tasks"] = []
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=stale_client,
    )
    assert saved.status_code == 200
    assert saved.json()["tts_tasks"][0]["workflow_id"] == workflow_id


def test_video_localization_tts_task_poll_expires_orphaned_prepared_workflow(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "孤儿配音任务收口", "description": ""},
    ).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "tts_tasks": [
                {
                    "workflow_id": "workflow-orphaned",
                    "project_id": project["project_id"],
                    "segment_id": "localized_0001",
                    "subtitle_summary": "未完成提交",
                    "text": "未完成提交",
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "prepared",
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                }
            ],
        },
    )

    task = client.get(f"/api/projects/{project['project_id']}/video-localization/tts/tasks/workflow-orphaned").json()

    assert task["status"] == "failed"
    assert task["generation_task_id"] is None
    assert task["stages"][0]["status"] == "failed"
    assert task["stages"][0]["error_code"] == "VIDEO_LOCALIZATION_TTS_SUBMISSION_INTERRUPTED"
    assert task["stages"][0]["error_message"] == "任务未能进入生成队列，请重新生成"
    assert task["completed_at"] == "2026-01-01T00:02:00"
    assert task["updated_at"] == "2026-01-01T00:02:00"


def test_video_localization_tts_task_poll_recovers_missing_registration_from_workflow_id(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "配音任务注册恢复", "description": ""},
    ).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "tts_tasks": [
                {
                    "workflow_id": "workflow-missed-registration",
                    "project_id": project["project_id"],
                    "segment_id": "localized_0001",
                    "subtitle_summary": "已经生成完成",
                    "text": "已经生成完成",
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "running",
                    "created_at": "2026-08-29T10:00:00",
                    "updated_at": "2026-08-29T10:00:01",
                }
            ],
        },
    )
    monkeypatch.setattr(
        video_localization_service.task_queue,
        "get_task_by_video_localization_workflow_id",
        lambda workflow_id: GenerationTask(
            task_id="task-recovered-registration",
            generation_id="task-recovered-registration",
            engine_id="omnivoice",
            project_id=project["project_id"],
            segment_id="localized_0001",
            status=TaskStatus.success,
            progress=1.0,
            input_text="已经生成完成",
            completed_at="2026-08-29T10:00:10",
            parameters={
                "video_localization_workflow_id": workflow_id,
            },
        ),
    )

    task = client.get(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks/"
        "workflow-missed-registration"
    ).json()

    assert task["status"] == "running"
    assert task["generation_task_id"] == "task-recovered-registration"
    assert task["stages"][0]["status"] == "success"


def test_tts_task_projection_does_not_mark_unplaced_duplicate_as_success(
    monkeypatch,
):
    completed = VideoLocalizationTtsTask(
        workflow_id="workflow-placed",
        project_id="project-1",
        segment_id="localized_0001",
        subtitle_summary="同一段",
        text="同一段",
        source_cue_ids=["cue_0001"],
        start_ms=1_000,
        end_ms=2_000,
        status="success",
        generation_task_id="task-placed",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation", status="success", progress=1.0
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement", status="success", progress=1.0
            ),
        ],
    )
    duplicate = VideoLocalizationTtsTask(
        workflow_id="workflow-duplicate",
        project_id="project-1",
        segment_id="localized_0001",
        subtitle_summary="同一段",
        text="同一段",
        source_cue_ids=["cue_0001"],
        start_ms=1_000,
        end_ms=2_000,
        status="running",
        generation_task_id="task-duplicate",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation", status="success", progress=1.0
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement", status="running", progress=0.5
            ),
        ],
    )
    monkeypatch.setattr(
        video_localization_service,
        "_reconcile_tts_workflow_task",
        lambda task, *_args: task,
    )

    projected = video_localization_service.reconcile_tts_workflow_tasks(
        [completed, duplicate]
    )

    assert [task.status for task in projected] == ["success", "running"]
    placement = next(
        stage for stage in projected[1].stages if stage.kind == "placement"
    )
    assert placement.status == "running"
    assert placement.parameters == {}


def test_adopting_group_candidate_closes_only_successful_unused_siblings():
    def workflow(
        workflow_id: str,
        *,
        group_id: str,
        task_id: str,
        result_id: str,
        generation_status: str = "success",
        placement_status: str = "running",
    ) -> VideoLocalizationTtsTask:
        target_id = "subtitle-1" if group_id == "group-1" else "subtitle-2"
        cue_id = "cue-1" if group_id == "group-1" else "cue-2"
        text = "当前组台词" if group_id == "group-1" else "另一组台词"
        return VideoLocalizationTtsTask(
            workflow_id=workflow_id,
            project_id="project-1",
            segment_id=target_id,
            subtitle_summary=text,
            text=text,
            source_cue_ids=[cue_id],
            start_ms=1_000,
            end_ms=2_000,
            status="running",
            generation_task_id=task_id,
            result_id=result_id,
            stages=[
                VideoLocalizationTtsTaskStage(
                    kind="generation",
                    status=generation_status,
                    progress=1.0 if generation_status == "success" else 0.5,
                    parameters={
                        "video_localization_dubbing_group_id": group_id,
                        "video_localization_dubbing_plan_revision": 3,
                        "video_localization_target_subtitle_ids": [target_id],
                        "video_localization_source_cue_ids": [cue_id],
                    },
                ),
                VideoLocalizationTtsTaskStage(
                    kind="placement",
                    status=placement_status,
                    progress=1.0 if placement_status == "success" else 0.0,
                ),
            ],
        )

    units = [
        DubbingSemanticUnit(
            unit_id="unit-1",
            subtitle_ids=["subtitle-1"],
            source_cue_ids=["cue-1"],
            speaker_id="speaker-1",
            start_ms=1_000,
            end_ms=2_000,
            source_anchor_start_ms=1_000,
            source_anchor_end_ms=2_000,
            display_text="当前组台词",
            spoken_text="当前组台词",
        ),
        DubbingSemanticUnit(
            unit_id="unit-2",
            subtitle_ids=["subtitle-2"],
            source_cue_ids=["cue-2"],
            speaker_id="speaker-1",
            start_ms=3_000,
            end_ms=4_000,
            source_anchor_start_ms=3_000,
            source_anchor_end_ms=4_000,
            display_text="另一组台词",
            spoken_text="另一组台词",
        ),
    ]
    plan = DubbingGenerationPlan(
        source_revision="source-revision",
        plan_revision=3,
        status="passed",
        semantic_units=units,
        speech_islands=[
            DubbingSpeechIsland(
                island_id="island-1",
                unit_ids=["unit-1", "unit-2"],
                speaker_id="speaker-1",
                start_ms=1_000,
                end_ms=4_000,
            )
        ],
        groups=[
            DubbingGenerationGroup(
                group_id="group-1",
                island_id="island-1",
                unit_ids=["unit-1"],
                subtitle_ids=["subtitle-1"],
                speaker_id="speaker-1",
                spoken_text="当前组台词",
                target_start_ms=1_000,
                target_end_ms=2_000,
                source_reference_start_ms=1_000,
                source_reference_end_ms=2_000,
            ),
            DubbingGenerationGroup(
                group_id="group-2",
                island_id="island-1",
                unit_ids=["unit-2"],
                subtitle_ids=["subtitle-2"],
                speaker_id="speaker-1",
                spoken_text="另一组台词",
                target_start_ms=3_000,
                target_end_ms=4_000,
                source_reference_start_ms=3_000,
                source_reference_end_ms=4_000,
            ),
        ],
    )
    selected = workflow(
        "workflow-selected",
        group_id="group-1",
        task_id="task-selected",
        result_id="result-selected",
        placement_status="success",
    )
    sibling = workflow(
        "workflow-sibling",
        group_id="group-1",
        task_id="task-sibling",
        result_id="result-sibling",
    )
    other_group = workflow(
        "workflow-other",
        group_id="group-2",
        task_id="task-other",
        result_id="result-other",
    )
    still_generating = workflow(
        "workflow-generating",
        group_id="group-1",
        task_id="task-generating",
        result_id="result-generating",
        generation_status="running",
    )
    draft = VideoLocalizationDraft(
        dubbing_production=DubbingProductionState(active_plan=plan),
        tts_tasks=[selected, sibling, other_group, still_generating],
    )

    updated = (
        video_localization_service
        .with_superseded_group_tts_placements_closed(
            draft,
            group_id="group-1",
            selected_generation_task_id="task-selected",
            selected_result_id="result-selected",
        )
    )

    by_id = {task.workflow_id: task for task in updated.tts_tasks}
    assert by_id["workflow-selected"] == selected
    assert by_id["workflow-sibling"].status == "cancelled"
    sibling_generation, sibling_placement = by_id["workflow-sibling"].stages
    assert sibling_generation.status == "success"
    assert by_id["workflow-sibling"].result_id == "result-sibling"
    assert sibling_placement.status == "cancelled"
    assert sibling_placement.parameters == {
        "completion_reason": "superseded_by_formal_group_candidate",
        "selected_result_id": "result-selected",
    }
    assert by_id["workflow-other"] == other_group
    assert by_id["workflow-generating"] == still_generating


def test_active_closeout_owner_remains_running_until_it_yields():
    workflow = VideoLocalizationTtsTask(
        workflow_id="workflow-owned",
        project_id="project-1",
        segment_id="subtitle-1",
        subtitle_summary="声音已生成",
        text="声音已生成",
        source_cue_ids=["cue-1"],
        start_ms=1_000,
        end_ms=2_000,
        status="running",
        generation_task_id="task-owned",
        result_id="result-owned",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation", status="success", progress=1.0
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement", status="running", progress=0.0
            ),
        ],
    )
    group = SimpleNamespace(
        group_id="group-1",
        stage="needs_gap_processing",
        recommended_action="process_gaps",
        workflow_ids=[workflow.workflow_id],
    )
    previous_lookup = video_localization_service._TTS_CLOSEOUT_OWNER_LOOKUP
    video_localization_service.configure_tts_closeout_owner_lookup(
        lambda workflow_id: workflow_id == "workflow-owned"
    )
    try:
        projected = video_localization_service._with_dubbing_workflow_attention(
            [workflow],
            groups=[group],
        )[0]
    finally:
        video_localization_service.configure_tts_closeout_owner_lookup(
            previous_lookup
        )

    assert projected.status == "running"
    assert projected.required_action is None


@pytest.mark.parametrize(
    ("stage", "recommended_action", "required_action"),
    [
        ("needs_gap_processing", "process_gaps", "process_gaps"),
        ("failed", "resolve_capacity", "resolve_capacity"),
        (
            "needs_semantic_review",
            "review_semantic_boundaries",
            "review_semantic_boundaries",
        ),
        ("needs_regeneration", "regenerate_candidate", "regenerate_candidate"),
    ],
)
def test_tts_task_projection_exposes_stopped_closeout_as_attention(
    stage,
    recommended_action,
    required_action,
):
    workflow = VideoLocalizationTtsTask(
        workflow_id="workflow-awaiting-agent",
        project_id="project-1",
        segment_id="localized_0001",
        subtitle_summary="声音已生成",
        text="声音已生成",
        source_cue_ids=["cue_0001"],
        start_ms=1_000,
        end_ms=2_000,
        status="running",
        generation_task_id="task-generated",
        result_id="result-generated",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation", status="success", progress=1.0
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement", status="running", progress=0.0
            ),
        ],
    )
    group = SimpleNamespace(
        group_id="group-1",
        stage=stage,
        recommended_action=recommended_action,
        workflow_ids=[workflow.workflow_id],
    )

    projected = video_localization_service._with_dubbing_workflow_attention(
        [workflow],
        groups=[group],
    )[0]

    assert projected.status == "needs_attention"
    assert projected.required_action == required_action
    assert projected.stages == workflow.stages


def test_tts_task_feed_revision_includes_attention_transition(monkeypatch):
    workflow = VideoLocalizationTtsTask(
        workflow_id="workflow-attention-revision",
        project_id="project-1",
        segment_id="localized_0001",
        subtitle_summary="声音已生成",
        text="声音已生成",
        source_cue_ids=["cue_0001"],
        start_ms=1_000,
        end_ms=2_000,
        status="running",
        generation_task_id="task-generated",
        result_id="result-generated",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation", status="success", progress=1.0
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement", status="running", progress=0.0
            ),
        ],
    )
    monkeypatch.setattr(
        video_localization_service.video_localization_tts_workflow_store,
        "read_feed",
        lambda *_args, **_kwargs: SimpleNamespace(
            authoritative=True,
            revision=7,
            tasks=(workflow,),
            workflow_ids=(workflow.workflow_id,),
            generation_runtimes=(),
        ),
    )
    action = {"value": "process_gaps"}

    def project(_project_id, tasks):
        return [
            tasks[0].model_copy(
                update={
                    "status": "needs_attention",
                    "required_action": action["value"],
                }
            )
        ]

    monkeypatch.setattr(
        video_localization_service,
        "_project_current_dubbing_workflow_attention",
        project,
    )

    first = video_localization_service.get_tts_task_feed("project-1")
    assert first is not None and first.changed is True
    unchanged = video_localization_service.get_tts_task_feed(
        "project-1", after_revision=first.revision
    )
    assert unchanged is not None and unchanged.changed is False

    action["value"] = "review_semantic_boundaries"
    changed = video_localization_service.get_tts_task_feed(
        "project-1", after_revision=first.revision
    )
    assert changed is not None and changed.changed is True
    assert changed.tasks[0].required_action == "review_semantic_boundaries"


def test_formal_timeline_closes_stale_running_placement_without_a_new_label():
    running = VideoLocalizationTtsTask(
        workflow_id="workflow-current-result",
        project_id="project-1",
        segment_id="localized_0001",
        subtitle_summary="已经在主轨",
        text="已经在主轨",
        source_cue_ids=["cue_0001"],
        start_ms=1_000,
        end_ms=2_000,
        status="running",
        generation_task_id="task-current-result",
        result_id="result-current",
        stages=[
            VideoLocalizationTtsTaskStage(
                kind="generation", status="success", progress=1.0
            ),
            VideoLocalizationTtsTaskStage(
                kind="placement", status="running", progress=0.0
            ),
        ],
    )
    draft = VideoLocalizationDraft(
        tts_tasks=[running],
        timeline_clips=[
            {
                "clip_id": "clip-current-result",
                "track_id": "dub",
                "status": "ready",
                "dub_lane": 0,
                "result_id": "result-current",
                "start_ms": 1_000,
                "end_ms": 2_000,
            }
        ],
    )

    updated = (
        video_localization_service
        .with_current_timeline_tts_placements_completed(draft)
    )

    workflow = updated.tts_tasks[0]
    placement = next(
        stage for stage in workflow.stages if stage.kind == "placement"
    )
    assert workflow.status == "success"
    assert workflow.timeline_clip_id == "clip-current-result"
    assert placement.status == "success"


def test_video_localization_tts_task_poll_recovers_missed_post_generation_work(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "配音重启恢复", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "text": "重启后继续完成",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "tts_tasks": [
                {
                    "workflow_id": "workflow-restart",
                    "project_id": project["project_id"],
                    "segment_id": "localized_0001",
                    "subtitle_summary": "重启后继续完成",
                    "text": "重启后继续完成",
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "status": "running",
                    "generation_task_id": "task-restart",
                    "stages": [
                        {"kind": "generation", "status": "running", "progress": 0.9},
                        {"kind": "placement", "status": "pending", "progress": 0.0},
                    ],
                }
            ],
        },
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        video_localization_service.task_queue,
        "get_tasks_by_ids",
        lambda _task_ids: {
            "task-restart": GenerationTask(
                task_id="task-restart",
                engine_id="omnivoice",
                input_text="重启后继续完成",
                status=TaskStatus.success,
                progress=1.0,
                result_id="result-restart",
            )
        },
    )
    monkeypatch.setattr(video_localization_service.task_queue, "schedule_auto_verification", scheduled.append)

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/tts/tasks")

    assert response.status_code == 200
    assert response.json()[0]["stages"][0]["status"] == "success"
    assert response.json()[0]["stages"][1]["status"] == "pending"
    assert scheduled == []


def test_video_localization_handoff_rejects_timeline_clip_from_another_subtitle(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "配音目标复核", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    reference_path = tmp_path / "reference.wav"
    audio_tools.write_audio(vocals_path, np.full(8_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(reference_path, np.full(2_500, 0.2, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "cues": [
                {"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_800, "en_subtitle_text": "One"},
                {"cue_id": "cue_0002", "start_ms": 3_000, "end_ms": 4_500, "en_subtitle_text": "Two"},
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_800,
                    "text": "第一条",
                    "source_cue_ids": ["cue_0001"],
                },
                {
                    "subtitle_id": "localized_0002",
                    "start_ms": 3_000,
                    "end_ms": 4_500,
                    "text": "第二条",
                    "source_cue_ids": ["cue_0002"],
                },
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip-localized-2",
                    "track_id": "dub",
                    "subtitle_id": "localized_0002",
                    "start_ms": 3_000,
                    "end_ms": 4_200,
                }
            ],
        },
    )
    managed = type("ManagedAudio", (), {"file_id": "source-file", "path": str(vocals_path), "duration_ms": 8_000})()
    clip_voice = type("ClipVoice", (), {"duration_ms": 2_500})()
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: managed,
    )
    monkeypatch.setattr(
        video_localization_service.tts_orchestration.voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(reference_path), "voice_file": clip_voice},
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/tts/handoff/localized_0001",
        json={
            "timeline_clip_id": "clip-localized-2",
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_TIMELINE_TARGET_INVALID"
    failed_tasks = client.get(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks"
    ).json()
    assert len(failed_tasks) == 1
    assert failed_tasks[0]["status"] == "failed"
    deleted = client.delete(
        f"/api/projects/{project['project_id']}/video-localization/tts/tasks/{failed_tasks[0]['workflow_id']}"
    )
    assert deleted.status_code == 200
    assert deleted.json()["tts_tasks"] == []


def test_video_localization_media_export_respects_editable_background_clip(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "背景片段裁切", "description": ""}).json()
    export_dir = tmp_path / "exports"
    export_dir.mkdir(exist_ok=True)
    package_root = _project_root(project["project_id"])
    background_path = package_root / "stems" / "background.wav"
    audio_tools.write_audio(background_path, np.full(48000 * 2, 0.2, dtype=np.float32), 48000)
    _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "source.mp4",
                "duration_ms": 1500,
            },
            "stems": {"background_path": str(background_path), "separation_status": "completed"},
            "ui_state": {"track_states": {"background": {"muted": False, "solo": True, "volume": 1.0}}},
            "timeline_clips": [
                {
                    "clip_id": "media_background",
                    "track_id": "background",
                    "start_ms": 500,
                    "end_ms": 1000,
                    "source_start_ms": 200,
                    "source_end_ms": 700,
                    "audio_path": str(background_path),
                }
            ],
        },
    )

    destination_response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/export/destination",
        json={"mode": "default"},
    )
    assert destination_response.status_code == 200, destination_response.json()
    destination_id = destination_response.json()["destination_id"]
    assert destination_id
    monkeypatch.setattr(
        video_localization_operation_queue,
        "_enqueue",
        lambda _operation_key: None,
    )
    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/export/render",
        json={
            "schema_version": "v1",
            "destination_id": destination_id,
            "output_filename": "editable-background.wav",
            "render": {
                "schema_version": "v1",
                "kind": "audio",
                "audio_tracks": ["background"],
            },
        },
    )

    assert response.status_code == 200, response.json()
    submitted = response.json()
    video_localization_operation_queue._process(
        project["project_id"],
        submitted["operation_id"],
    )
    completed = video_localization_operation_queue.get_operation(
        project["project_id"],
        submitted["operation_id"],
    )
    assert completed is not None
    assert completed.status == "success", completed.error_message
    rendered, sample_rate = sf.read(
        export_dir / completed.result_summary["filename"],
        dtype="float32",
    )
    assert sample_rate == 48_000
    assert len(rendered) == 72_000
    assert float(np.max(np.abs(rendered[:19_200]))) < 0.001
    assert 0.19 <= float(np.max(np.abs(rendered[26_400:43_200]))) <= 0.21
    assert float(np.max(np.abs(rendered[52_800:]))) < 0.001


def test_video_localization_old_tts_batch_submit_is_not_exposed(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "旧批量入口退役", "description": ""},
    ).json()
    before = client.get(f"/api/projects/{project['project_id']}/video-localization").json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch")

    assert response.status_code == 404
    after = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert after == before


def test_video_localization_old_tts_batch_sync_is_not_exposed(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "旧批量回填退役", "description": ""},
    ).json()
    before = client.get(f"/api/projects/{project['project_id']}/video-localization").json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch/legacy-batch/sync")

    assert response.status_code == 404
    after = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert after == before


def test_video_localization_source_cue_audio_slices_clean_vocals(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "原声切片", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path), "separation_status": "completed"},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "Source line.",
                }
            ],
        },
    )
    captured = {}

    def fake_slice_audio(source, destination, start_ms, end_ms):
        captured["source"] = source
        captured["destination"] = destination
        captured["start_ms"] = start_ms
        captured["end_ms"] = end_ms
        destination.write_bytes(b"source-cue")

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_slice_audio)

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001/source-audio")

    assert response.status_code == 200
    assert response.content == b"source-cue"
    assert captured["source"] == vocals_path
    assert captured["start_ms"] == 1000
    assert captured["end_ms"] == 3200
    assert "cue_0001-1000-3200" in captured["destination"].name
    assert f"-{vocals_path.stat().st_size}-" in captured["destination"].name


def _forbid_content_transcription(monkeypatch) -> None:
    """Editable audio adoption must not invoke content recognition."""
    from app.services import asr_service

    def unexpected_transcription(**kwargs):
        raise AssertionError("Placing editable audio must not invoke ASR")

    monkeypatch.setattr(asr_service, "transcribe", unexpected_transcription)


def test_video_localization_single_tts_generation_syncs_from_task_queue(tmp_path: Path, monkeypatch):
    _forbid_content_transcription(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "单条回填", "description": ""}).json()
    output_path = tmp_path / "outputs" / "single.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"single-audio")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "Source line.",
                    "zh_localized_subtitle_text": "源台词。",
                    "tts_recommended_text": "源台词。",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 3_200,
                    "text": "源台词。",
                    "tts_text": "源台词。",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    plan = _create_current_dubbing_plan(
        client,
        project["project_id"],
    )
    lineage = _dubbing_lineage_kwargs(plan, "localized_0001")
    _set_canonical_tts_tasks(
        client,
        project["project_id"],
        [
            _canonical_tts_task(
                project["project_id"],
                "workflow-task-video-single",
                start_ms=1_000,
                end_ms=3_200,
                text="源台词。",
            )
        ],
    )
    task = GenerationTask(
        task_id="task-video-single",
        generation_id="task-video-single",
        engine_id="indextts-v2",
        project_id=project["project_id"],
        segment_id="localized_0001",
        localized_subtitle_id="localized_0001",
        cue_id="cue_0001",
        bind_to_video_localization=True,
        input_text="源台词。",
        status=TaskStatus.success,
        parameters={
            "source": "video_localization",
            "bind_to_video_localization": True,
            "localized_subtitle_id": "localized_0001",
            "cue_id": "cue_0001",
            "generation_id": "task-video-single",
            "video_localization_target_subtitle_ids": ["localized_0001"],
            "video_localization_workflow_id": "workflow-task-video-single",
            "video_localization_dubbing_plan_revision": lineage["dubbing_plan_revision"],
            "video_localization_dubbing_group_id": lineage["dubbing_group_id"],
        },
    )
    hist = HistoryItem(
        result_id="result-video-single",
        task_id=task.task_id,
        generation_id=task.generation_id,
        engine_id=task.engine_id,
        project_id=task.project_id,
        segment_id=task.segment_id,
        localized_subtitle_id=task.localized_subtitle_id,
        cue_id=task.cue_id,
        bind_to_video_localization=True,
        input_text=task.input_text,
        output_path=str(output_path),
        duration_ms=2300,
    )

    history_store.add(hist)
    assert video_localization_tts_handoff.place_generated_result(task, hist) is True

    response = client.get(f"/api/projects/{project['project_id']}/video-localization")
    localized = response.json()["localized_subtitles"][0]
    adopted_path = _project_root(project["project_id"]) / "tts" / "localized_0001" / "task-video-single.wav"
    assert localized["tts_result_id"] == "result-video-single"
    assert localized["tts_audio_path"] == str(adopted_path)
    assert adopted_path.read_bytes() == b"single-audio"
    assert output_path.exists()
    assert localized["generated_duration_ms"] == 2300
    candidate = response.json()["generated_candidates"][0]
    assert candidate["result_id"] == "result-video-single"
    assert candidate["audio_path"] == str(adopted_path)
    assert candidate["duration_ms"] == 2300
    assert candidate["status"] == "success"
    clip = response.json()["timeline_clips"][0]
    assert clip["audio_path"] == str(adopted_path)
    assert clip["source_end_ms"] == 2300
    assert clip["status"] == "ready"

    video_localization_tts_handoff.place_generated_result(task, hist)
    assert list(adopted_path.parent.glob("task-video-single*.wav")) == [adopted_path]


def test_video_localization_generate_handoff_does_not_bind_without_explicit_confirmation(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "不自动回填", "description": ""}).json()
    output_path = tmp_path / "outputs" / "unbound.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"standalone-audio")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "tts_recommended_text": "原项目台词",
                }
            ],
        },
    )
    task = GenerationTask(
        task_id="task-unbound",
        generation_id="task-unbound",
        engine_id="indextts-v2",
        project_id=project["project_id"],
        segment_id="cue_0001",
        cue_id="cue_0001",
        bind_to_video_localization=False,
        input_text="在语音合成页生成的其他内容",
        status=TaskStatus.success,
        parameters={
            "source": "video_localization",
            "bind_to_video_localization": False,
            "cue_id": "cue_0001",
            "generation_id": "task-unbound",
        },
    )
    hist = HistoryItem(
        result_id="result-unbound",
        task_id=task.task_id,
        generation_id=task.generation_id,
        engine_id=task.engine_id,
        project_id=task.project_id,
        segment_id=task.segment_id,
        cue_id=task.cue_id,
        bind_to_video_localization=False,
        input_text=task.input_text,
        output_path=str(output_path),
        duration_ms=1800,
    )

    video_localization_tts_handoff.place_generated_result(task, hist)

    cue = client.get(f"/api/projects/{project['project_id']}/video-localization").json()["cues"][0]
    assert cue["tts_result_id"] is None
    assert cue["tts_audio_path"] is None


def test_video_localization_candidate_can_be_previewed_but_legacy_apply_requires_plan(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "候选试听与采用", "description": ""}).json()
    first_path = _project_root(project["project_id"]) / "tts" / "first.wav"
    second_path = _project_root(project["project_id"]) / "tts" / "second.wav"
    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_bytes(b"first-audio")
    second_path.write_bytes(b"second-audio")
    _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "tts_recommended_text": "候选台词。",
                    "tts_audio_path": str(first_path),
                }
            ],
            "generated_candidates": [
                {
                    "candidate_id": "candidate_first",
                    "recipe_id": "recipe_001",
                    "cue_id": "cue_0001",
                    "audio_path": str(first_path),
                    "duration_ms": 1800,
                    "status": "success",
                    "cqc_status": "passed",
                },
                {
                    "candidate_id": "candidate_second",
                    "recipe_id": "recipe_001",
                    "cue_id": "cue_0001",
                    "audio_path": str(second_path),
                    "duration_ms": 2100,
                    "status": "success",
                    "cqc_status": "passed",
                },
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_cue_0001",
                    "cue_id": "cue_0001",
                    "candidate_id": "candidate_first",
                    "track_id": "dub",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "audio_path": str(first_path),
                    "status": "ready",
                },
                {
                    "clip_id": "manual_copy",
                    "cue_id": "cue_0001",
                    "candidate_id": "candidate_first",
                    "track_id": "dub",
                    "start_ms": 4000,
                    "end_ms": 6000,
                    "audio_path": str(first_path),
                    "manual_history_copy": True,
                    "dub_lane": 1,
                    "status": "ready",
                },
            ],
        },
    )

    preview = client.get(f"/api/projects/{project['project_id']}/video-localization/candidates/candidate_second/audio")
    assert preview.status_code == 200
    assert preview.content == b"second-audio"

    applied = client.post(f"/api/projects/{project['project_id']}/video-localization/candidates/candidate_second/apply")
    assert applied.status_code == 404
    current = client.get(
        f"/api/projects/{project['project_id']}/video-localization"
    ).json()
    assert current["timeline_clips"][0]["candidate_id"] == (
        "candidate_first"
    )


def test_video_localization_history_result_replaces_only_the_bound_localized_dub_clip(tmp_path: Path, monkeypatch):
    _forbid_content_transcription(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化历史替换", "description": ""}).json()
    old_audio = tmp_path / "outputs" / "old.wav"
    history_audio = tmp_path / "outputs" / "history.wav"
    old_audio.parent.mkdir(parents=True, exist_ok=True)
    audio_tools.write_audio(old_audio, np.full(1_000, 0.2, dtype=np.float32), 1_000)
    audio_tools.write_audio(history_audio, np.full(1_200, 0.35, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_500, "tts_recommended_text": "配音台词"}],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_500,
                    "text": "上屏字幕",
                    "tts_text": "配音台词",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_localized_0001",
                    "track_id": "dub",
                    "subtitle_id": "localized_0001",
                    "target_subtitle_ids": ["localized_0001"],
                    "cue_id": "cue_0001",
                    "source_cue_ids": ["cue_0001"],
                    "target_start_ms": 1_000,
                    "target_end_ms": 2_500,
                    "start_ms": 1_000,
                    "end_ms": 2_500,
                    "audio_path": str(old_audio),
                    "status": "ready",
                    "candidate_id": "candidate_old",
                    "timeline_edit_gate": {"status": "passed"},
                }
            ],
        },
    )
    history_store.add(
        HistoryItem(
            result_id="history-localized-001",
            task_id="task-localized-001",
            generation_id="generation-localized-001",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="配音台词",
            output_path=str(history_audio),
            duration_ms=1_200,
            parameter_snapshot={"source": "video_localization"},
        )
    )

    applied = client.post(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/clip_localized_0001/history/history-localized-001/apply",
        json={"request_id": "replace-history"},
    )

    assert applied.status_code == 200
    body = applied.json()
    assert body["schema_version"] == "video-localization-timeline-mutation-v1"
    assert len(applied.content) < 50_000
    assert body["localized_subtitles"][0]["tts_result_id"] == "history-localized-001"
    assert body["timeline_clips"][0]["result_id"] == "history-localized-001"
    assert body["timeline_clips"][0]["candidate_id"] == "candidate_task-localized-001"
    assert "timeline_edit_gate" not in body["timeline_clips"][0]
    assert body["timeline_clips"][0]["audio_path"] != str(history_audio)
    assert Path(body["timeline_clips"][0]["audio_path"]).exists()
    preview = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/clip_localized_0001/audio"
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("audio/")
    assert "attachment" not in preview.headers.get("content-disposition", "")
    assert len(preview.content) > 100
    assert preview.content != history_audio.read_bytes()


def test_video_localization_history_result_adds_a_missing_localized_dub_clip(tmp_path: Path, monkeypatch):
    _forbid_content_transcription(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化历史添加", "description": ""}).json()
    history_audio = tmp_path / "outputs" / "history-add.wav"
    history_audio.parent.mkdir(parents=True, exist_ok=True)
    audio_tools.write_audio(history_audio, np.full(1_200, 0.35, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_500, "tts_recommended_text": "第一句"},
                {"cue_id": "cue_0002", "start_ms": 1_500, "end_ms": 2_800, "tts_recommended_text": "第二句"},
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_500,
                    "text": "第一句",
                    "tts_text": "第一句",
                    "source_cue_ids": ["cue_0001"],
                },
                {
                    "subtitle_id": "localized_0002",
                    "start_ms": 1_500,
                    "end_ms": 2_800,
                    "text": "第二句",
                    "tts_text": "第二句",
                    "source_cue_ids": ["cue_0002"],
                },
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_localized_0002",
                    "track_id": "dub",
                    "subtitle_id": "localized_0002",
                    "cue_id": "cue_0002",
                    "start_ms": 1_500,
                    "end_ms": 2_800,
                    "audio_path": str(history_audio),
                    "status": "ready",
                }
            ],
        },
    )
    history_store.add(
        HistoryItem(
            result_id="history-localized-add-001",
            task_id="task-localized-add-001",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="第一句",
            output_path=str(history_audio),
            duration_ms=1_200,
            parameter_snapshot={"source": "video_localization"},
        )
    )

    applied = client.post(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/history/history-localized-add-001/apply",
        json={"request_id": "add-history", "segment_id": "localized_0001", "new_clip_id": "clip_localized_0001"},
    )

    assert applied.status_code == 200
    body = applied.json()
    assert body["schema_version"] == "video-localization-timeline-mutation-v1"
    assert len(body["timeline_clips"]) == 1
    added = next(item for item in body["timeline_clips"] if item.get("subtitle_id") == "localized_0001")
    assert added["clip_id"] == "clip_localized_0001"
    assert added["result_id"] == "history-localized-add-001"
    assert added["start_ms"] == 1_000
    assert added["end_ms"] == 2_200
    assert body["localized_subtitles"][0]["tts_result_id"] == "history-localized-add-001"


def test_video_localization_group_history_prefers_exact_group_clip_over_subtitle_alias(tmp_path: Path, monkeypatch):
    _forbid_content_transcription(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化分组历史替换", "description": ""}).json()
    group_id = "group_localized_0001_localized_0002_2"
    group_audio = _project_root(project["project_id"]) / "tts" / "group-history.wav"
    alias_audio = _project_root(project["project_id"]) / "tts" / "alias-old.wav"
    group_audio.parent.mkdir(parents=True, exist_ok=True)
    audio_tools.write_audio(group_audio, np.full(3_200, 0.35, dtype=np.float32), 1_000)
    audio_tools.write_audio(alias_audio, np.full(800, 0.2, dtype=np.float32), 1_000)
    _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_500, "tts_recommended_text": "第一句"},
                {"cue_id": "cue_0002", "start_ms": 2_500, "end_ms": 4_000, "tts_recommended_text": "第二句"},
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_500,
                    "text": "第一句",
                    "tts_text": "第一句",
                    "source_cue_ids": ["cue_0001"],
                },
                {
                    "subtitle_id": "localized_0002",
                    "start_ms": 2_500,
                    "end_ms": 4_000,
                    "text": "第二句",
                    "tts_text": "第二句",
                    "source_cue_ids": ["cue_0002"],
                },
            ],
            "timeline_clips": [
                {
                    "clip_id": f"clip_{group_id}",
                    "track_id": "dub",
                    "subtitle_id": group_id,
                    "cue_id": "cue_0001",
                    "source_cue_ids": ["cue_0001", "cue_0002"],
                    "start_ms": 1_000,
                    "end_ms": 4_000,
                    "status": "queued",
                },
                {
                    "clip_id": "clip_localized_0002",
                    "track_id": "dub",
                    "subtitle_id": "localized_0002",
                    "cue_id": "cue_0002",
                    "source_cue_ids": ["cue_0002"],
                    "start_ms": 5_000,
                    "end_ms": 5_800,
                    "audio_path": str(alias_audio),
                    "status": "ready",
                },
            ],
        },
    )
    history_store.add(
        HistoryItem(
            result_id="history-group-exact-001",
            task_id="task-group-exact-001",
            generation_id="task-group-exact-001",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            segment_id=group_id,
            localized_subtitle_id="localized_0002",
            cue_id="cue_0002",
            bind_to_video_localization=True,
            input_text="第一句，第二句",
            output_path=str(group_audio),
            duration_ms=3_200,
            parameter_snapshot={"source": "video_localization"},
        )
    )

    applied = client.post(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/history/history-group-exact-001/apply",
        json={"request_id": "replace-group", "segment_id": group_id, "clip_id": f"clip_{group_id}"},
    )

    assert applied.status_code == 200
    result = applied.json()
    assert result["schema_version"] == "video-localization-timeline-mutation-v1"
    current = client.get(
        f"/api/projects/{project['project_id']}/video-localization"
    ).json()
    clips = {item["clip_id"]: item for item in current["timeline_clips"]}
    assert clips[f"clip_{group_id}"]["result_id"] == "history-group-exact-001"
    assert clips[f"clip_{group_id}"]["status"] == "ready"
    assert clips[f"clip_{group_id}"]["source_cue_ids"] == ["cue_0001", "cue_0002"]
    assert clips["clip_localized_0002"]["audio_path"] == str(alias_audio)
    assert "result_id" not in clips["clip_localized_0002"]
    assert all(cue["tts_result_id"] is None for cue in current["cues"])


def test_video_localization_history_result_can_be_dropped_at_an_explicit_time_and_lane(
    tmp_path: Path,
    monkeypatch,
):
    _forbid_content_transcription(monkeypatch)
    client = _client(tmp_path)
    monkeypatch.setattr(
        video_localization_api.dubbing_production,
        "resync_history_candidate_automatic_cqc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("history placement must not run CQC")
        ),
    )
    monkeypatch.setattr(
        video_localization_service.tts_pipeline,
        "detect_first_effective_speech_ms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit history placement must not analyze audio alignment")
        ),
    )
    project = client.post("/api/projects", json={"name": "本土化历史拖放", "description": ""}).json()
    history_audio = tmp_path / "outputs" / "history-drop.wav"
    history_audio.parent.mkdir(parents=True, exist_ok=True)
    audio_tools.write_audio(history_audio, np.full(1_200, 0.35, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_500, "tts_recommended_text": "拖放台词"}],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_500,
                    "text": "拖放字幕",
                    "tts_text": "拖放台词",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_localized_0001",
                    "track_id": "dub",
                    "subtitle_id": "localized_0001",
                    "cue_id": "cue_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_200,
                    "audio_path": str(history_audio),
                    "result_id": "history-localized-drop-001",
                    "status": "ready",
                }
            ],
        },
    )
    history_store.add(
        HistoryItem(
            result_id="history-localized-drop-001",
            task_id="task-localized-drop-001",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            segment_id="group_localized_0001_localized_0001_1",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="拖放台词",
            output_path=str(history_audio),
            duration_ms=1_200,
            parameter_snapshot={"source": "video_localization"},
        )
    )

    applied = client.post(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/history/history-localized-drop-001/apply",
        json={
            "segment_id": "group_localized_0001_localized_0001_1",
            "start_ms": 1_500,
            "dub_lane": 0,
            "force_new": True,
            "new_clip_id": "history_clip_drop_001",
            "request_id": "history_clip_drop_001",
        },
    )

    assert applied.status_code == 200
    body = applied.json()
    assert body["schema_version"] == "video-localization-timeline-mutation-v1"
    assert body["affected_clip_ids"] == ["history_clip_drop_001"]
    assert len(body["timeline_clips"]) == 1
    dropped = next(item for item in body["timeline_clips"] if item["clip_id"] == "history_clip_drop_001")
    assert dropped["result_id"] == "history-localized-drop-001"
    assert dropped["start_ms"] == 1_500
    assert dropped["end_ms"] == 2_700
    assert dropped["source_start_ms"] == 0
    assert dropped["source_end_ms"] == 1_200
    assert dropped["dub_lane"] == 1
    assert body["dub_lane_states"]["1"] == {
        "muted": False,
        "solo": False,
        "volume": 1,
        "locked": False,
    }
    stored = video_localization_service.get_video_localization(project["project_id"])
    assert stored is not None
    stored_drop = next(
        item for item in stored.timeline_clips if item["clip_id"] == "history_clip_drop_001"
    )
    assert stored_drop["manual_history_copy"] is True
    assert "cqc_status" not in stored_drop
    assert "timeline_edit_gate" not in stored_drop

    retried = client.post(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/history/history-localized-drop-001/apply",
        json={
            "segment_id": "group_localized_0001_localized_0001_1",
            "start_ms": 1_500,
            "dub_lane": 0,
            "force_new": True,
            "new_clip_id": "history_clip_drop_001",
            "request_id": "history_clip_drop_001",
        },
    )
    assert retried.status_code == 200
    assert retried.json()["affected_clip_ids"] == ["history_clip_drop_001"]
    stored_after_retry = video_localization_service.get_video_localization(project["project_id"])
    assert stored_after_retry is not None
    assert [
        item["clip_id"]
        for item in stored_after_retry.timeline_clips
        if item.get("result_id") == "history-localized-drop-001"
    ] == ["clip_localized_0001", "history_clip_drop_001"]


def test_reapplied_history_clears_stale_tombstone_before_split_and_delete(tmp_path: Path, monkeypatch):
    _forbid_content_transcription(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重新采用历史配音", "description": ""}).json()
    history_audio = tmp_path / "outputs" / "history-reactivated.wav"
    history_audio.parent.mkdir(parents=True, exist_ok=True)
    audio_tools.write_audio(history_audio, np.full(1_600, 0.35, dtype=np.float32), 1_000)
    task_id = "task-reactivated-001"
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_600}],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_600,
                    "text": "重新采用",
                    "tts_text": "重新采用",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
            "ui_state": {"discarded_tts_task_ids": [task_id]},
        },
    )
    history_store.add(
        HistoryItem(
            result_id="history-reactivated-001",
            task_id=task_id,
            generation_id=task_id,
            engine_id="indextts-v2",
            project_id=project["project_id"],
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="重新采用",
            output_path=str(history_audio),
            duration_ms=1_600,
            parameter_snapshot={"source": "video_localization"},
        )
    )

    applied = client.post(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/history/history-reactivated-001/apply",
        json={
            "segment_id": "localized_0001",
            "new_clip_id": "history_clip_reactivated_001",
            "request_id": "history_clip_reactivated_001",
            "start_ms": 1_000,
            "dub_lane": 0,
            "force_new": True,
        },
    )

    assert applied.status_code == 200
    applied_body = applied.json()
    assert task_id not in applied_body["discarded_tts_task_ids"]
    original = next(item for item in applied_body["timeline_clips"] if item["result_id"] == "history-reactivated-001")
    split_ms = 1_800
    second = {
        **original,
        "clip_id": f"{original['clip_id']}_part_2",
        "media_source_clip_id": original["clip_id"],
        "start_ms": split_ms,
        "source_start_ms": split_ms - original["start_ms"],
    }
    current_body = client.get(
        f"/api/projects/{project['project_id']}/video-localization/workspace"
    ).json()["draft"]
    saved_body = {
        **current_body,
        "timeline_clips": [second],
        "ui_state": {
            **current_body["ui_state"],
            # A stale page/controller may still carry the pre-reactivation
            # tombstone while saving the split transaction.
            "discarded_tts_task_ids": [task_id],
            "client_timeline_edit_intent": {
                "deleted_timeline_clips": [{
                    "clip_id": original["clip_id"],
                    "expected_generation_identity": task_id,
                }],
                "added_timeline_clip_ids": [second["clip_id"]],
                "dub_lane_clip_ids": [second["clip_id"]],
            },
        },
    }

    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json=saved_body,
    )

    assert saved.status_code == 200
    assert [item["clip_id"] for item in saved.json()["timeline_clips"]] == [second["clip_id"]]


def test_history_drop_rebases_on_concurrent_project_update(tmp_path: Path, monkeypatch):
    _forbid_content_transcription(monkeypatch)
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "并发拖放", "description": ""}).json()
    history_audio = tmp_path / "outputs" / "history-concurrent.wav"
    history_audio.parent.mkdir(parents=True, exist_ok=True)
    audio_tools.write_audio(history_audio, np.full(1_200, 0.35, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 1_000, "end_ms": 2_500}],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 1_000,
                    "end_ms": 2_500,
                    "text": "并发拖放字幕",
                    "tts_text": "并发拖放台词",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )
    history_store.add(
        HistoryItem(
            result_id="history-concurrent-001",
            task_id="task-concurrent-001",
            engine_id="indextts-v2",
            project_id=project["project_id"],
            segment_id="localized_0001",
            localized_subtitle_id="localized_0001",
            cue_id="cue_0001",
            bind_to_video_localization=True,
            input_text="并发拖放台词",
            output_path=str(history_audio),
            duration_ms=1_200,
            parameter_snapshot={"source": "video_localization"},
        )
    )
    original_adopt = video_localization_service.media_assets.adopt_tts_audio
    concurrent_update_done = False

    def adopt_after_concurrent_update(project_id, source_path, cue_id, identity):
        nonlocal concurrent_update_done
        if not concurrent_update_done:
            concurrent_update_done = True
            video_localization_service.update_video_localization_atomic(
                project_id,
                lambda current: current.model_copy(update={"scene_context": "concurrent marker preserved"}),
                intent="content",
            )
        return original_adopt(project_id, source_path, cue_id, identity)

    monkeypatch.setattr(
        video_localization_service.media_assets,
        "adopt_tts_audio",
        adopt_after_concurrent_update,
    )

    applied = client.post(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/history/history-concurrent-001/apply",
        json={
            "segment_id": "localized_0001",
            "start_ms": 1_500,
            "dub_lane": 0,
            "force_new": True,
            "new_clip_id": "history_clip_concurrent_001",
            "request_id": "history_clip_concurrent_001",
        },
    )

    assert applied.status_code == 200
    current = client.get(
        f"/api/projects/{project['project_id']}/video-localization"
    ).json()
    assert current["scene_context"] == "concurrent marker preserved"
    assert any(clip.get("result_id") == "history-concurrent-001" for clip in applied.json()["timeline_clips"])


def test_video_localization_export_adds_project_metadata(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导出测试", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "tts_recommended_text": "你好。"}],
        },
    )

    exported = client.get(f"/api/projects/{project['project_id']}/video-localization/export")
    assert exported.status_code == 200
    filename = _attachment_filename(exported)
    assert filename.startswith("导出测试__工程数据__完整导出__")
    assert "__版本-" in filename
    assert filename.endswith(".json")
    body = exported.json()
    assert body["project_id"] == project["project_id"]
    assert body["project_name"] == "导出测试"
    assert body["exported_at"]
    assert body["export_summary"]["cue_count"] == 1
    assert body["quality_gate"]["status"] == "blocked"
    assert body["cues"][0]["tts_recommended_text"] == "你好。"


def test_video_localization_readiness_exports_ready_for_mix(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "就绪审计", "description": ""}).json()
    package_root = _project_root(project["project_id"])
    tts_audio = package_root / "tts" / "cue_0001.wav"
    tts_audio.parent.mkdir(parents=True, exist_ok=True)
    tts_audio.write_bytes(b"fake-tts-audio")
    source_video = package_root / "source" / "source.mp4"
    source_audio = package_root / "audio" / "source.wav"
    vocals_audio = package_root / "stems" / "vocals.wav"
    background_audio = package_root / "stems" / "background.wav"
    reference_audio = package_root / "references" / "ref_001.wav"
    for path in (
        source_video,
        source_audio,
        vocals_audio,
        background_audio,
        reference_audio,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    _save_server_video_localization(
        project["project_id"],
        {
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "source.mp4",
                "video_path": str(source_video),
                "audio_path": str(source_audio),
            },
            "stems": {
                "separation_status": "completed",
                "original_audio_path": str(source_audio),
                "vocals_clean_path": str(vocals_audio),
                "background_path": str(background_audio),
            },
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A", "route": "clone_from_source"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(reference_audio),
                    "cleanliness": "clean",
                    "asr_text": "This is a clean reference.",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                    "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                    "reference_clip_id": "ref_001",
                    "tts_audio_path": str(tts_audio),
                    "generated_duration_ms": 2000,
                    "source_duration_ms": 2000,
                    "review_status": "ready",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/readiness")

    assert response.status_code == 200
    filename = _attachment_filename(response)
    assert filename.startswith("就绪审计__工程数据__生产检查__")
    assert "__版本-" in filename
    assert filename.endswith(".json")
    body = response.json()
    assert body["status"] == "ready_for_mix"
    assert body["summary"]["generated_tts_count"] == 1
    assert body["summary"]["quality_gate_status"] == "pass"
    assert body["cue_status"][0]["has_tts_audio"] is True
    assert all(check["status"] == "pass" for check in body["checks"])


def test_video_localization_readiness_blocks_media_without_source_subtitles(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "尚无字幕", "description": ""},
    ).json()
    source_video = tmp_path / "source.mp4"
    source_audio = tmp_path / "source.wav"
    vocals_audio = tmp_path / "vocals.wav"
    background_audio = tmp_path / "background.wav"
    for path in (
        source_video,
        source_audio,
        vocals_audio,
        background_audio,
    ):
        path.write_bytes(b"media")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "source.mp4",
                "video_path": str(source_video),
                "audio_path": str(source_audio),
            },
            "stems": {
                "separation_status": "completed",
                "original_audio_path": str(source_audio),
                "vocals_clean_path": str(vocals_audio),
                "background_path": str(background_audio),
            },
            "cues": [],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    checks = {item["code"]: item for item in body["checks"]}
    assert checks["source_subtitles"]["status"] == "blocked"
    assert checks["source_subtitles"]["details"]["cue_count"] == 0
    assert checks["quality_gate"]["status"] == "warning"
    assert "先生成或导入源字幕" in body["next_actions"]


def test_video_localization_readiness_blocks_missing_or_failed_tts(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "失败审计", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "audio_path": str(tmp_path / "source.wav")},
            "stems": {
                "separation_status": "completed",
                "original_audio_path": str(tmp_path / "source.wav"),
                "vocals_clean_path": str(tmp_path / "vocals.wav"),
                "background_path": str(tmp_path / "background.wav"),
            },
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A", "route": "clone_from_source"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(tmp_path / "ref_001.wav"),
                    "cleanliness": "clean",
                    "asr_text": "This is a clean reference.",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_failed",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                    "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                    "reference_clip_id": "ref_001",
                    "tts_batch_status": "failed",
                    "tts_batch_error": "REFERENCE_AUDIO_NOT_FOUND",
                    "review_status": "ready",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    check_by_code = {check["code"]: check for check in body["checks"]}
    assert check_by_code["tts_audio_coverage"]["status"] == "blocked"
    assert check_by_code["tts_audio_coverage"]["details"]["missing_cue_ids"] == ["cue_failed"]
    assert check_by_code["tts_failures"]["status"] == "blocked"
    assert check_by_code["tts_failures"]["details"]["failed_cue_ids"] == ["cue_failed"]
    assert body["cue_status"][0]["tts_batch_error"] == "REFERENCE_AUDIO_NOT_FOUND"


def test_video_localization_clear_asr_subtitle_track_is_atomic_and_idempotent(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "清空 ASR 字幕", "description": ""}).json()
    base = _completed_asr_result(VideoLocalizationDraft())
    payload = base.model_dump(mode="json")
    payload["language_config"]["detected_source_language"] = "en"
    payload["source_media"]["metadata"]["keep_me"] = "yes"
    payload["cues"] = [
        {
            "cue_id": "cue_0001",
            "start_ms": 0,
            "end_ms": 1200,
            "en_subtitle_text": "Clear this subtitle.",
            "quality_flags": ["generated_by_asr"],
        }
    ]
    payload["localized_subtitles"] = [
        {
            "subtitle_id": "subtitle_0001",
            "start_ms": 0,
            "end_ms": 1200,
            "text": "保留本土化字幕",
            "linked_cue_id": "cue_0001",
        }
    ]
    video_localization_service.save_video_localization(
        project["project_id"], VideoLocalizationDraft.model_validate(payload)
    )

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 200
    body = response.json()
    assert body["cues"] == []
    assert body["transcription"] is None
    assert body["source_media"]["metadata"] == {"keep_me": "yes"}
    assert body["localized_subtitles"][0]["text"] == "保留本土化字幕"
    assert body["localized_subtitles"][0]["linked_cue_id"] is None
    assert body["ui_state"]["selected_cue_id"] == ""
    assert body["language_config"]["detected_source_language"] is None
    assert client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/en").status_code == 200


def test_video_localization_clear_localized_subtitle_track_clears_all_localized_mirrors(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "清空本土化字幕", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "Keep the ASR cue.",
                }
            ],
        },
    )
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={"srt_text": "1\n00:00:00,100 --> 00:00:00,900\n保留镜像文案\n"},
    )
    assert imported.status_code == 200
    assert imported.json()["localized_subtitles"]

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 200
    body = response.json()
    assert body["localized_subtitles"] == []
    assert body["cues"][0]["en_subtitle_text"] == "Keep the ASR cue."
    assert body["cues"][0]["zh_localized_subtitle_text"] is None
    assert body["cues"][0]["tts_recommended_text"] is None
    assert body["localization_state"] == {}
    exported = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")
    assert exported.status_code in {400, 409}
    assert client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh").status_code == 200


def test_video_localization_localized_track_mutations_block_while_generation_runs(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "锁定本土化轨", "description": ""}).json()
    draft = VideoLocalizationDraft(
        cues=[VideoLocalizationCue(cue_id="cue_0001", start_ms=0, end_ms=1000, en_subtitle_text="Hello")],
        localized_subtitles=[
            {"subtitle_id": "subtitle_0001", "start_ms": 0, "end_ms": 1000, "text": "你好", "linked_cue_id": "cue_0001"}
        ],
        operations=[
            VideoLocalizationOperation(
                project_id=project["project_id"],
                kind="localization_draft",
                status="running",
            )
        ],
    )
    video_localization_service.save_video_localization(project["project_id"], draft)

    patch = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_0001",
        json={"text": "新的文本"},
    )
    clear = client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={"srt_text": "1\n00:00:00,000 --> 00:00:01,000\n新的字幕\n"},
    )

    assert {patch.status_code, clear.status_code, imported.status_code} == {409}
    assert patch.json()["error"]["code"] == "VIDEO_LOCALIZATION_TRACK_BUSY"


def test_video_localization_clear_asr_track_blocks_active_transcription(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "运行中字幕", "description": ""}).json()
    draft = VideoLocalizationDraft(
        operations=[
            VideoLocalizationOperation(
                project_id=project["project_id"],
                kind="english_asr",
                status="running",
            )
        ]
    )
    video_localization_service.save_video_localization(project["project_id"], draft)

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_CLEAR_BLOCKED"
