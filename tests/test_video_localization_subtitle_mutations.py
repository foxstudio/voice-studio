from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSpokenSegment,
    VideoLocalizationSubtitleCue,
    VideoLocalizationSubtitleCueUpdate,
)
from app.domains.video_localization.subtitles import with_updated_localized_subtitle  # noqa: E402
from app.domains.video_localization.subtitle_linkage import audit_subtitle_linkages  # noqa: E402
from app.domains.video_localization.subtitle_mutations import (  # noqa: E402
    MAPPING_ORPHANED_FLAG,
    MAPPING_REVIEW_FLAG,
    MAPPING_UPDATED_FLAG,
    delete_localized_subtitle,
    delete_source_cue,
    merge_source_cues,
    split_localized_subtitle,
    split_source_cue,
)
from app.models.schemas import (  # noqa: E402
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
)


def _cue(
    cue_id: str,
    start_ms: int,
    end_ms: int,
    text: str,
    words: list[str],
    *,
    speaker_id: str = "speaker_01",
) -> VideoLocalizationCue:
    return VideoLocalizationCue(
        cue_id=cue_id,
        speaker_id=speaker_id,
        start_ms=start_ms,
        end_ms=end_ms,
        en_subtitle_text=text,
        source_text_raw=text,
        source_word_ids=words,
        source_duration_ms=end_ms - start_ms,
        timing_confidence="high",
    )


def _subtitle(
    subtitle_id: str,
    start_ms: int,
    end_ms: int,
    text: str,
    source_ids: list[str],
    words: list[str],
) -> VideoLocalizationSubtitleCue:
    return VideoLocalizationSubtitleCue(
        subtitle_id=subtitle_id,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        tts_text=f"{text}口播",
        linked_cue_id=source_ids[0] if source_ids else None,
        source_cue_ids=source_ids,
        source_word_ids=words,
    )


def _task(segment_id: str, source_ids: list[str], workflow_id: str) -> VideoLocalizationTtsTask:
    return VideoLocalizationTtsTask(
        workflow_id=workflow_id,
        project_id="project_01",
        segment_id=segment_id,
        subtitle_summary=segment_id,
        text="台词",
        source_cue_ids=source_ids,
        start_ms=0,
        end_ms=1_000,
    )


def _assert_valid_draft(draft: VideoLocalizationDraft) -> None:
    VideoLocalizationDraft.model_validate(draft.model_dump(mode="json"))


def test_delete_source_cue_orphans_single_link_and_preserves_asset_identities():
    cues = [
        _cue("cue_0001", 0, 1_000, "One", ["word_01"]),
        _cue("cue_0002", 1_000, 2_000, "Two", ["word_02"]),
    ]
    subtitles = [
        _subtitle("localized_0001", 0, 1_000, "一", ["cue_0001"], ["word_01"]),
        _subtitle(
            "localized_0002",
            1_000,
            2_000,
            "二",
            ["cue_0001", "cue_0002"],
            ["word_01", "word_02"],
        ),
    ]
    draft = VideoLocalizationDraft(
        cues=cues,
        localized_subtitles=subtitles,
        timeline_clips=[
            {
                "clip_id": "clip_keep_01",
                "track_id": "dub",
                "subtitle_id": "localized_0001",
                "cue_id": "cue_0001",
                "source_cue_ids": ["cue_0001"],
            },
            {"clip_id": "unrelated", "track_id": "background", "audio_path": "background.wav"},
        ],
        tts_tasks=[_task("localized_0001", ["cue_0001"], "workflow_keep_01")],
    )

    result = delete_source_cue(draft, "cue_0001")

    _assert_valid_draft(result.draft)
    assert [cue.cue_id for cue in result.draft.cues] == ["cue_0002"]
    orphaned, retained = result.draft.localized_subtitles
    assert orphaned.source_cue_ids == []
    assert orphaned.source_word_ids == []
    assert {MAPPING_UPDATED_FLAG, MAPPING_ORPHANED_FLAG, MAPPING_REVIEW_FLAG}.issubset(orphaned.quality_flags)
    assert retained.source_cue_ids == ["cue_0002"]
    assert retained.source_word_ids == ["word_02"]
    assert result.orphaned_localized_subtitle_ids == ("localized_0001",)
    assert [clip["clip_id"] for clip in result.draft.timeline_clips] == ["clip_keep_01", "unrelated"]
    assert result.draft.timeline_clips[0]["source_cue_ids"] == []
    assert result.draft.timeline_clips[0]["cue_id"] is None
    assert result.draft.timeline_clips[1] == draft.timeline_clips[1]
    assert result.draft.tts_tasks[0].workflow_id == "workflow_keep_01"
    assert result.draft.tts_tasks[0].source_cue_ids == []
    assert [cue.cue_id for cue in draft.cues] == ["cue_0001", "cue_0002"]
    assert draft.timeline_clips[0]["source_cue_ids"] == ["cue_0001"]


def test_delete_localized_subtitle_clears_mirror_and_detaches_existing_audio():
    cue = _cue("cue_0001", 0, 1_000, "One", ["word_01"])
    subtitle = _subtitle("localized_0001", 0, 1_000, "一", ["cue_0001"], ["word_01"])
    cue = cue.model_copy(update={"zh_localized_subtitle_text": "一", "tts_recommended_text": "一口播"})
    draft = VideoLocalizationDraft(
        cues=[cue],
        localized_subtitles=[subtitle],
        timeline_clips=[
            {
                "clip_id": "clip_keep_audio",
                "track_id": "dub",
                "subtitle_id": subtitle.subtitle_id,
                "target_subtitle_ids": [subtitle.subtitle_id],
                "cue_id": cue.cue_id,
                "source_cue_ids": [cue.cue_id],
                "audio_path": "generated.wav",
            }
        ],
        tts_tasks=[_task(subtitle.subtitle_id, [cue.cue_id], "workflow_history")],
    )

    result = delete_localized_subtitle(draft, subtitle.subtitle_id)

    _assert_valid_draft(result.draft)
    assert result.draft.localized_subtitles == []
    assert result.draft.cues[0].zh_localized_subtitle_text is None
    assert result.draft.cues[0].tts_recommended_text is None
    assert result.draft.timeline_clips[0]["clip_id"] == "clip_keep_audio"
    assert result.draft.timeline_clips[0]["subtitle_id"] is None
    assert result.draft.timeline_clips[0]["target_subtitle_ids"] == []
    assert result.draft.timeline_clips[0]["audio_path"] == "generated.wav"
    assert result.draft.tts_tasks[0].workflow_id == "workflow_history"


def test_delete_localized_subtitle_reconciles_its_spoken_segment():
    cue = _cue("cue_0001", 0, 2_000, "One two", ["word_01", "word_02"])
    first = _subtitle(
        "localized_0001",
        0,
        1_000,
        "第一句",
        ["cue_0001"],
        ["word_01"],
    ).model_copy(update={"spoken_segment_id": "spoken_0001"})
    second = _subtitle(
        "localized_0002",
        1_000,
        2_000,
        "第二句",
        ["cue_0001"],
        ["word_02"],
    ).model_copy(update={"spoken_segment_id": "spoken_0001"})
    draft = VideoLocalizationDraft(
        cues=[cue],
        localized_subtitles=[first, second],
        localized_spoken_segments=[
            VideoLocalizationSpokenSegment(
                segment_id="spoken_0001",
                paragraph_id="paragraph_0001",
                text=f"{first.tts_text}{second.tts_text}",
                start_ms=first.start_ms,
                end_ms=second.end_ms,
                source_cue_ids=["cue_0001"],
                source_word_ids=["word_01", "word_02"],
            )
        ],
    )

    result = delete_localized_subtitle(draft, first.subtitle_id)

    _assert_valid_draft(result.draft)
    assert len(result.draft.localized_spoken_segments) == 1
    segment = result.draft.localized_spoken_segments[0]
    assert segment.text == second.tts_text
    assert (segment.start_ms, segment.end_ms) == (second.start_ms, second.end_ms)
    assert segment.source_cue_ids == ["cue_0001"]
    assert segment.source_word_ids == ["word_02"]


def test_delete_last_localized_subtitle_removes_orphaned_spoken_segment():
    cue = _cue("cue_0001", 0, 1_000, "One", ["word_01"])
    subtitle = _subtitle(
        "localized_0001",
        0,
        1_000,
        "第一句",
        ["cue_0001"],
        ["word_01"],
    ).model_copy(update={"spoken_segment_id": "spoken_0001"})
    draft = VideoLocalizationDraft(
        cues=[cue],
        localized_subtitles=[subtitle],
        localized_spoken_segments=[
            VideoLocalizationSpokenSegment(
                segment_id="spoken_0001",
                paragraph_id="paragraph_0001",
                text=subtitle.tts_text,
                start_ms=subtitle.start_ms,
                end_ms=subtitle.end_ms,
                source_cue_ids=["cue_0001"],
                source_word_ids=["word_01"],
            )
        ],
    )

    result = delete_localized_subtitle(draft, subtitle.subtitle_id)

    _assert_valid_draft(result.draft)
    assert result.draft.localized_spoken_segments == []


def test_editing_spoken_text_stales_binding_without_removing_ready_timeline_audio():
    cue = _cue("cue_0001", 0, 1_000, "One", ["word_01"])
    first = _subtitle(
        "localized_0001",
        0,
        1_000,
        "第一句",
        ["cue_0001"],
        ["word_01"],
    ).model_copy(
        update={
            "tts_result_id": "result_01",
            "tts_generation_id": "generation_01",
            "tts_audio_path": "/tmp/generated_01.wav",
            "generated_duration_ms": 900,
        }
    )
    second = _subtitle(
        "localized_0002",
        1_000,
        2_000,
        "第二句",
        ["cue_0001"],
        ["word_01"],
    )
    target_snapshot = {
        "subtitle_ids": [first.subtitle_id],
        "start_ms": first.start_ms,
        "end_ms": first.end_ms,
        "text": first.tts_text,
    }
    first_task = _task(first.subtitle_id, ["cue_0001"], "workflow_01").model_copy(
        update={
            "generation_task_id": "task_01",
            "timeline_clip_id": "clip_01",
            "stages": [
                VideoLocalizationTtsTaskStage(
                    kind="generation",
                    status="running",
                    parameters={
                        "video_localization_parameter_pack": {
                            "target": target_snapshot,
                        }
                    },
                ),
                VideoLocalizationTtsTaskStage(
                    kind="placement",
                    parameters={"target_snapshot": target_snapshot},
                ),
            ],
        }
    )
    unrelated_task = _task(second.subtitle_id, ["cue_0001"], "workflow_02")
    draft = VideoLocalizationDraft(
        cues=[cue],
        localized_subtitles=[first, second],
        timeline_clips=[
            {
                "clip_id": "clip_01",
                "track_id": "dub",
                "subtitle_id": first.subtitle_id,
                "target_subtitle_ids": [first.subtitle_id],
                "task_id": "task_01",
                "generation_id": "generation_01",
                "result_id": "result_01",
                "audio_path": "/tmp/generated_01.wav",
            },
            {
                "clip_id": "clip_02",
                "track_id": "dub",
                "subtitle_id": second.subtitle_id,
                "target_subtitle_ids": [second.subtitle_id],
                "task_id": "task_02",
                "audio_path": "/tmp/generated_02.wav",
            },
        ],
        tts_tasks=[first_task, unrelated_task],
        ui_state={
            "latest_tts_task_by_segment": {
                first.subtitle_id: "task_01",
                second.subtitle_id: "task_02",
            }
        },
    )

    updated = with_updated_localized_subtitle(
        draft,
        first.subtitle_id,
        VideoLocalizationSubtitleCueUpdate(tts_text="第一句修改后的口播"),
    )

    changed = next(
        item
        for item in updated.localized_subtitles
        if item.subtitle_id == first.subtitle_id
    )
    assert changed.tts_result_id is None
    assert changed.tts_generation_id is None
    assert changed.tts_audio_path is None
    assert changed.generated_duration_ms is None
    assert [item["clip_id"] for item in updated.timeline_clips] == [
        "clip_01",
        "clip_02",
    ]
    assert updated.timeline_clips[0]["audio_path"] == "/tmp/generated_01.wav"
    assert updated.timeline_clips[0]["tts_target_binding_status"] == "stale"
    assert updated.timeline_clips[0]["tts_target_text"] == first.tts_text
    assert updated.timeline_clips[1] == draft.timeline_clips[1]
    assert [item.workflow_id for item in updated.tts_tasks] == [
        "workflow_01",
        "workflow_02",
    ]
    assert updated.tts_tasks[0].status == "cancelled"
    assert updated.tts_tasks[0].generation_task_id == "task_01"
    assert updated.tts_tasks[1] == unrelated_task
    assert {
        "workflow_01",
        "task_01",
    }.issubset(updated.ui_state["discarded_tts_task_ids"])
    assert "generation_01" not in updated.ui_state["discarded_tts_task_ids"]
    assert updated.ui_state["latest_tts_task_by_segment"] == {
        second.subtitle_id: "task_02"
    }


def test_display_only_edit_keeps_audio_when_explicit_tts_text_is_unchanged():
    subtitle = _subtitle(
        "localized_0001",
        0,
        1_000,
        "第一句",
        ["cue_0001"],
        ["word_01"],
    ).model_copy(
        update={
            "tts_result_id": "result_01",
            "tts_generation_id": "generation_01",
            "tts_audio_path": "/tmp/generated_01.wav",
        }
    )
    draft = VideoLocalizationDraft(
        localized_subtitles=[subtitle],
        timeline_clips=[
            {
                "clip_id": "clip_01",
                "track_id": "dub",
                "subtitle_id": subtitle.subtitle_id,
                "target_subtitle_ids": [subtitle.subtitle_id],
                "task_id": "task_01",
                "audio_path": "/tmp/generated_01.wav",
            }
        ],
    )

    updated = with_updated_localized_subtitle(
        draft,
        subtitle.subtitle_id,
        VideoLocalizationSubtitleCueUpdate(text="第一句的新显示写法"),
    )

    assert updated.localized_subtitles[0].tts_audio_path == "/tmp/generated_01.wav"
    assert updated.timeline_clips == draft.timeline_clips
    assert "discarded_tts_task_ids" not in updated.ui_state


def test_timing_edit_stales_binding_without_removing_ready_timeline_audio():
    subtitle = _subtitle(
        "localized_0001",
        0,
        1_000,
        "第一句",
        ["cue_0001"],
        ["word_01"],
    ).model_copy(
        update={
            "tts_result_id": "result_01",
            "tts_audio_path": "/tmp/generated_01.wav",
        }
    )
    draft = VideoLocalizationDraft(
        localized_subtitles=[subtitle],
        timeline_clips=[
            {
                "clip_id": "clip_01",
                "track_id": "dub",
                "subtitle_id": subtitle.subtitle_id,
                "target_subtitle_ids": [subtitle.subtitle_id],
                "task_id": "task_01",
                "audio_path": "/tmp/generated_01.wav",
            }
        ],
    )

    updated = with_updated_localized_subtitle(
        draft,
        subtitle.subtitle_id,
        VideoLocalizationSubtitleCueUpdate(end_ms=1_200),
    )

    assert updated.localized_subtitles[0].tts_audio_path is None
    assert len(updated.timeline_clips) == 1
    assert updated.timeline_clips[0]["clip_id"] == "clip_01"
    assert updated.timeline_clips[0]["audio_path"] == "/tmp/generated_01.wav"
    assert updated.timeline_clips[0]["tts_target_binding_status"] == "stale"
    assert updated.timeline_clips[0]["tts_target_text"] == subtitle.tts_text
    assert "discarded_tts_task_ids" not in updated.ui_state


def test_spoken_text_edit_removes_only_unfinished_tts_placeholder():
    subtitle = _subtitle(
        "localized_0001",
        0,
        1_000,
        "第一句",
        ["cue_0001"],
        ["word_01"],
    )
    active_task = _task(
        subtitle.subtitle_id,
        ["cue_0001"],
        "workflow_pending",
    ).model_copy(
        update={
            "generation_task_id": "task_pending",
            "timeline_clip_id": "pending_clip",
        }
    )
    draft = VideoLocalizationDraft(
        localized_subtitles=[subtitle],
        timeline_clips=[
            {
                "clip_id": "pending_clip",
                "track_id": "dub",
                "subtitle_id": subtitle.subtitle_id,
                "target_subtitle_ids": [subtitle.subtitle_id],
                "task_id": "task_pending",
                "status": "queued",
            }
        ],
        tts_tasks=[active_task],
    )

    updated = with_updated_localized_subtitle(
        draft,
        subtitle.subtitle_id,
        VideoLocalizationSubtitleCueUpdate(tts_text="修改后的口播"),
    )

    assert updated.timeline_clips == []
    assert updated.tts_tasks[0].status == "cancelled"
    assert {"workflow_pending", "task_pending"}.issubset(
        updated.ui_state["discarded_tts_task_ids"]
    )


def test_spoken_text_edit_keeps_completed_tts_history_out_of_discarded_ids():
    subtitle = _subtitle(
        "localized_0001",
        0,
        1_000,
        "第一句",
        ["cue_0001"],
        ["word_01"],
    )
    completed_task = _task(
        subtitle.subtitle_id,
        ["cue_0001"],
        "workflow_done",
    ).model_copy(
        update={
            "status": "success",
            "generation_task_id": "task_done",
            "timeline_clip_id": "ready_clip",
        }
    )
    draft = VideoLocalizationDraft(
        localized_subtitles=[subtitle],
        timeline_clips=[
            {
                "clip_id": "ready_clip",
                "track_id": "dub",
                "subtitle_id": subtitle.subtitle_id,
                "target_subtitle_ids": [subtitle.subtitle_id],
                "task_id": "task_done",
                "audio_path": "/tmp/done.wav",
                "status": "ready",
            }
        ],
        tts_tasks=[completed_task],
        ui_state={
            "latest_tts_task_by_segment": {
                subtitle.subtitle_id: "task_done"
            }
        },
    )

    updated = with_updated_localized_subtitle(
        draft,
        subtitle.subtitle_id,
        VideoLocalizationSubtitleCueUpdate(tts_text="修改后的口播"),
    )

    assert updated.timeline_clips[0]["clip_id"] == "ready_clip"
    assert updated.tts_tasks[0] == completed_task
    assert "discarded_tts_task_ids" not in updated.ui_state
    assert updated.ui_state["latest_tts_task_by_segment"] == {}


def test_merge_source_cues_updates_links_mirrors_and_preserves_clip_task_ids():
    cues = [
        _cue("cue_0001", 0, 1_000, "One", ["word_01"]),
        _cue("cue_0002", 1_000, 2_000, "Two", ["word_02"]),
    ]
    subtitle = _subtitle(
        "localized_0001",
        0,
        2_000,
        "一二",
        ["cue_0001", "cue_0002"],
        ["word_01", "word_02"],
    )
    draft = VideoLocalizationDraft(
        cues=cues,
        localized_subtitles=[subtitle],
        timeline_clips=[
            {
                "clip_id": "clip_keep_01",
                "track_id": "dub",
                "subtitle_id": subtitle.subtitle_id,
                "cue_id": "cue_0001",
                "source_cue_ids": ["cue_0001", "cue_0002"],
            }
        ],
        tts_tasks=[_task(subtitle.subtitle_id, ["cue_0001", "cue_0002"], "workflow_keep_01")],
    )

    result = merge_source_cues(draft, ["cue_0001", "cue_0002"])

    _assert_valid_draft(result.draft)
    assert [cue.cue_id for cue in result.draft.cues] == ["cue_0001"]
    merged = result.draft.cues[0]
    assert (merged.start_ms, merged.end_ms) == (0, 2_000)
    assert merged.en_subtitle_text == "One\nTwo"
    assert merged.source_word_ids == ["word_01", "word_02"]
    assert merged.zh_localized_subtitle_text == "一二"
    assert merged.tts_recommended_text == "一二口播"
    assert result.draft.localized_subtitles[0].source_cue_ids == ["cue_0001"]
    assert result.draft.timeline_clips[0]["clip_id"] == "clip_keep_01"
    assert result.draft.timeline_clips[0]["source_cue_ids"] == ["cue_0001"]
    assert result.draft.tts_tasks[0].workflow_id == "workflow_keep_01"
    assert result.draft.tts_tasks[0].source_cue_ids == ["cue_0001"]
    assert result.removed_source_cue_ids == ("cue_0002",)
    assert [cue.cue_id for cue in draft.cues] == ["cue_0001", "cue_0002"]


def test_split_source_cue_routes_localized_links_by_word_partition():
    source = _cue("cue_parent", 0, 2_000, "One two", ["word_01", "word_02"])
    subtitles = [
        _subtitle("localized_0001", 0, 1_000, "一", ["cue_parent"], ["word_01"]),
        _subtitle("localized_0002", 1_000, 2_000, "二", ["cue_parent"], ["word_02"]),
    ]
    draft = VideoLocalizationDraft(
        cues=[source],
        localized_subtitles=subtitles,
        timeline_clips=[
            {
                "clip_id": "clip_keep_02",
                "track_id": "dub",
                "subtitle_id": "localized_0002",
                "cue_id": "cue_parent",
                "source_cue_ids": ["cue_parent"],
            }
        ],
        tts_tasks=[_task("localized_0002", ["cue_parent"], "workflow_keep_02")],
    )
    children = [
        _cue("cue_parent", 0, 1_000, "One", ["word_01"]),
        _cue("cue_child", 1_000, 2_000, "two", ["word_02"]),
    ]

    result = split_source_cue(draft, "cue_parent", children)

    _assert_valid_draft(result.draft)
    assert [cue.cue_id for cue in result.draft.cues] == ["cue_parent", "cue_child"]
    assert [item.source_cue_ids for item in result.draft.localized_subtitles] == [
        ["cue_parent"],
        ["cue_child"],
    ]
    assert result.review_required_localized_subtitle_ids == ()
    assert result.draft.timeline_clips[0]["clip_id"] == "clip_keep_02"
    assert result.draft.timeline_clips[0]["source_cue_ids"] == ["cue_child"]
    assert result.draft.tts_tasks[0].workflow_id == "workflow_keep_02"
    assert result.draft.tts_tasks[0].source_cue_ids == ["cue_child"]


def test_split_localized_subtitle_partitions_links_without_duplicate_word_evidence():
    cues = [
        _cue("cue_0001", 0, 1_000, "One", ["word_01"]),
        _cue("cue_0002", 1_000, 2_000, "Two", ["word_02"]),
    ]
    original = _subtitle(
        "localized_0001",
        0,
        2_000,
        "一二",
        ["cue_0001", "cue_0002"],
        ["word_01", "word_02"],
    ).model_copy(update={"spoken_segment_id": "spoken_segment_0001"})
    children = [
        _subtitle("localized_0001", 0, 1_000, "一", [], []).model_copy(
            update={"spoken_segment_id": "caller_invented_a"}
        ),
        _subtitle("localized_0002", 1_000, 2_000, "二", [], []).model_copy(
            update={"spoken_segment_id": "caller_invented_b"}
        ),
    ]
    draft = VideoLocalizationDraft(
        cues=cues,
        localized_subtitles=[original],
        timeline_clips=[
            {
                "clip_id": "clip_keep_01",
                "track_id": "dub",
                "subtitle_id": original.subtitle_id,
                "cue_id": "cue_0001",
                "source_cue_ids": ["cue_0001", "cue_0002"],
            }
        ],
        tts_tasks=[_task(original.subtitle_id, ["cue_0001", "cue_0002"], "workflow_keep_01")],
    )

    result = split_localized_subtitle(
        draft,
        original.subtitle_id,
        children,
        source_word_ids_by_subtitle_id={
            "localized_0001": ["word_01"],
            "localized_0002": ["word_02"],
        },
    )

    _assert_valid_draft(result.draft)
    first, second = result.draft.localized_subtitles
    assert first.source_cue_ids == ["cue_0001"]
    assert first.source_word_ids == ["word_01"]
    assert second.source_cue_ids == ["cue_0002"]
    assert second.source_word_ids == ["word_02"]
    assert first.spoken_segment_id == "spoken_segment_0001"
    assert second.spoken_segment_id == "spoken_segment_0001"
    assert all(MAPPING_UPDATED_FLAG in item.quality_flags for item in [first, second])
    assert result.review_required_localized_subtitle_ids == ()
    assert all(audit.valid for audit in audit_subtitle_linkages(result.draft.cues, [first, second]))
    assert result.draft.timeline_clips[0]["clip_id"] == "clip_keep_01"
    assert result.draft.timeline_clips[0]["source_cue_ids"] == ["cue_0001"]
    assert result.draft.tts_tasks[0].workflow_id == "workflow_keep_01"
    assert result.draft.tts_tasks[0].source_cue_ids == ["cue_0001"]


def test_split_localized_subtitle_without_word_partition_is_retained_for_review():
    cue = _cue("cue_0001", 0, 2_000, "One two", [])
    original = _subtitle("localized_0001", 0, 2_000, "一二", ["cue_0001"], [])
    children = [
        _subtitle("localized_0001", 0, 1_000, "一", [], []),
        _subtitle("localized_0002", 1_000, 2_000, "二", [], []),
    ]

    result = split_localized_subtitle(
        VideoLocalizationDraft(cues=[cue], localized_subtitles=[original]),
        original.subtitle_id,
        children,
    )

    _assert_valid_draft(result.draft)
    assert [item.source_cue_ids for item in result.draft.localized_subtitles] == [
        ["cue_0001"],
        ["cue_0001"],
    ]
    assert result.review_required_localized_subtitle_ids == ("localized_0001", "localized_0002")
    assert all(MAPPING_REVIEW_FLAG in item.quality_flags for item in result.draft.localized_subtitles)
