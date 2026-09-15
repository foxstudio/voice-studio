from app.domains.video_localization import timeline_clip_identity, timeline_edit_receipts
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.schemas.video_localization_timeline_edit import VideoLocalizationTimelineEditPatchRequest


def test_edit_receipt_preserves_audio_availability_without_internal_path():
    for metadata in ({"result_id": "result-1", "task_id": "task-1"}, {}):
        clip = {"clip_id": "clip-1", "track_id": "dub", "audio_path": "/managed/audio.wav",
                "start_ms": 1000, "end_ms": 2000, "source_start_ms": 0,
                "source_end_ms": 1000, **metadata}
        before = timeline_clip_identity.with_generation_identity(clip)
        patch = VideoLocalizationTimelineEditPatchRequest(added_clips=[{
            "clip_id": "clip-1", "media_source_clip_id": "source",
            "start_ms": 1000, "end_ms": 2000, "source_start_ms": 0, "source_end_ms": 1000,
        }])
        receipt = timeline_edit_receipts.project_result(VideoLocalizationDraft(timeline_clips=[clip]), patch)
        after = receipt["timeline_clips"][0]
        assert "audio_path" not in after
        assert before["has_audio_source"] is True
        assert after["has_audio_source"] is True
        assert after["generation_identity"] == before["generation_identity"]
        assert "media_source_clip_id" not in after  # Do not invent an editable source binding.


def test_unplaced_task_is_not_an_audio_source():
    projected = timeline_clip_identity.with_generation_identity({
        "clip_id": "pending", "task_id": "running-task", "audio_path": None,
    })
    assert projected["has_audio_source"] is False
