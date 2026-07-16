from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import exporting, subtitles, tts_pipeline
from app.domains.video_localization.schemas import (
    BatchSegmentResult,
    BatchTask,
    TaskStatus,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationReferenceClip,
    VideoLocalizationSubtitleCue,
)
from app.errors import AppException


def _reference(tmp_path: Path) -> VideoLocalizationReferenceClip:
    audio_path = tmp_path / "reference.wav"
    audio_path.write_bytes(b"reference")
    return VideoLocalizationReferenceClip(
        reference_clip_id="ref_001",
        speaker_id="speaker_a",
        source_stem="vocals_clean",
        cleanliness="clean",
        asr_status="verified",
        asr_text="Reference words",
        audio_path=str(audio_path),
    )


def _cue(cue_id: str, speaker_id: str, start_ms: int, end_ms: int) -> VideoLocalizationCue:
    return VideoLocalizationCue(
        cue_id=cue_id,
        speaker_id=speaker_id,
        start_ms=start_ms,
        end_ms=end_ms,
        audio_route="clone_from_source",
        en_subtitle_text=f"Source {cue_id}",
        tts_recommended_text=f"旧台词 {cue_id}",
        reference_clip_id="ref_001",
        review_status="ready",
    )


def test_merged_localized_subtitle_enters_tts_as_one_authoritative_segment(tmp_path: Path):
    draft = VideoLocalizationDraft(
        cues=[_cue("cue_0001", "speaker_a", 100, 1000), _cue("cue_0002", "speaker_a", 1100, 2200)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=150,
                end_ms=2150,
                text="合并后的中文字幕",
                tts_text="合并后的口播台词",
                source_cue_ids=["cue_0001", "cue_0002"],
            )
        ],
        reference_clips=[_reference(tmp_path)],
    )

    request = tts_pipeline.build_batch_request(
        project_id="project_001",
        project_name="本土化轨 TTS",
        draft=draft,
        output_dir=tmp_path / "output",
    )

    assert len(request.segments) == 1
    segment = request.segments[0]
    assert segment.segment_id == "localized_0001"
    assert segment.text == "合并后的口播台词"
    assert segment.parameters["source_cue_ids"] == ["cue_0001", "cue_0002"]
    assert segment.parameters["source_start_ms"] == 150
    assert segment.parameters["source_end_ms"] == 2150
    assert segment.parameters["source_duration_ms"] == 2000
    assert segment.reference_audio_path == draft.reference_clips[0].audio_path


def test_localized_subtitle_crossing_speakers_is_rejected(tmp_path: Path):
    draft = VideoLocalizationDraft(
        cues=[_cue("cue_0001", "speaker_a", 0, 1000), _cue("cue_0002", "speaker_b", 1000, 2000)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=0,
                end_ms=2000,
                text="跨人物字幕",
                tts_text="跨人物口播",
                source_cue_ids=["cue_0001", "cue_0002"],
            )
        ],
        reference_clips=[_reference(tmp_path)],
    )

    with pytest.raises(AppException) as exc_info:
        tts_pipeline.build_batch_request(
            project_id="project_001",
            project_name="跨人物",
            draft=draft,
            output_dir=tmp_path / "output",
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_TTS_CROSS_SPEAKER_SUBTITLE"


def test_split_localized_subtitles_keep_distinct_tts_results_and_timeline_clips(tmp_path: Path):
    first_audio = tmp_path / "localized_0001.mp3"
    second_audio = tmp_path / "localized_0002.mp3"
    first_audio.write_bytes(b"first")
    second_audio.write_bytes(b"second")
    draft = VideoLocalizationDraft(
        cues=[_cue("cue_0001", "speaker_a", 0, 2000)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=0,
                end_ms=900,
                text="第一条字幕",
                tts_text="第一条口播",
                source_cue_ids=["cue_0001"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0002",
                start_ms=1000,
                end_ms=2000,
                text="第二条字幕",
                tts_text="第二条口播",
                source_cue_ids=["cue_0001"],
            ),
        ],
        reference_clips=[_reference(tmp_path)],
    )
    request = tts_pipeline.build_batch_request(
        project_id="project_001",
        project_name="一拆多",
        draft=draft,
        output_dir=tmp_path / "output",
    )
    assert [segment.segment_id for segment in request.segments] == ["localized_0001", "localized_0002"]

    submitted = tts_pipeline.with_batch_submitted(
        draft,
        "batch_split",
        [segment.segment_id for segment in request.segments],
        attempted_at="2026-07-16T12:00:00Z",
    )
    assert [subtitle.tts_batch_status for subtitle in submitted.localized_subtitles] == ["queued", "queued"]
    assert submitted.cues[0].tts_batch_task_id is None

    synced = tts_pipeline.with_synced_batch_results(
        submitted,
        BatchTask(
            batch_task_id="batch_split",
            project_name="一拆多",
            engine_id="indextts-v2",
            status=TaskStatus.success,
            segments=[
                BatchSegmentResult(
                    segment_id="localized_0001",
                    text="第一条口播",
                    output_path=str(first_audio),
                    duration_ms=800,
                    status=TaskStatus.success,
                ),
                BatchSegmentResult(
                    segment_id="localized_0002",
                    text="第二条口播",
                    output_path=str(second_audio),
                    duration_ms=950,
                    status=TaskStatus.success,
                ),
            ],
        ),
    )

    assert [subtitle.tts_audio_path for subtitle in synced.localized_subtitles] == [str(first_audio), str(second_audio)]
    assert [subtitle.tts_result_id for subtitle in synced.localized_subtitles] == [
        "batch_split:localized_0001",
        "batch_split:localized_0002",
    ]
    assert synced.cues[0].tts_audio_path is None
    localized_clips = [dict(clip) for clip in synced.timeline_clips if dict(clip).get("subtitle_id")]
    assert [clip["subtitle_id"] for clip in localized_clips] == ["localized_0001", "localized_0002"]
    assert [clip["audio_path"] for clip in localized_clips] == [str(first_audio), str(second_audio)]
    assert all(clip["source_cue_ids"] == ["cue_0001"] for clip in localized_clips)
    assert tts_pipeline.tts_audio_path(synced, "localized_0002") == second_audio

    resynced = tts_pipeline.with_synced_batch_results(
        synced,
        BatchTask(
            batch_task_id="batch_split",
            project_name="一拆多",
            engine_id="indextts-v2",
            status=TaskStatus.success,
            segments=[
                BatchSegmentResult(
                    segment_id="localized_0001",
                    text="第一条口播",
                    output_path=str(first_audio),
                    duration_ms=800,
                    status=TaskStatus.success,
                ),
                BatchSegmentResult(
                    segment_id="localized_0002",
                    text="第二条口播",
                    output_path=str(second_audio),
                    duration_ms=950,
                    status=TaskStatus.success,
                ),
            ],
        ),
    )
    assert len([clip for clip in resynced.timeline_clips if clip.get("subtitle_id")]) == 2


def test_merged_localized_subtitle_result_does_not_overwrite_source_cues(tmp_path: Path):
    audio_path = tmp_path / "localized_merged.mp3"
    audio_path.write_bytes(b"merged")
    draft = VideoLocalizationDraft(
        cues=[_cue("cue_0001", "speaker_a", 0, 1000), _cue("cue_0002", "speaker_a", 1000, 2200)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_merged",
                start_ms=0,
                end_ms=2200,
                text="合并字幕",
                tts_text="合并口播",
                source_cue_ids=["cue_0001", "cue_0002"],
            )
        ],
        reference_clips=[_reference(tmp_path)],
    )

    synced = tts_pipeline.with_synced_batch_results(
        draft,
        BatchTask(
            batch_task_id="batch_merge",
            project_name="多并一",
            engine_id="indextts-v2",
            status=TaskStatus.success,
            segments=[
                BatchSegmentResult(
                    segment_id="localized_merged",
                    text="合并口播",
                    output_path=str(audio_path),
                    duration_ms=2100,
                    status=TaskStatus.success,
                )
            ],
        ),
    )

    assert synced.localized_subtitles[0].tts_audio_path == str(audio_path)
    assert [cue.tts_audio_path for cue in synced.cues] == [None, None]
    assert len(synced.timeline_clips) == 1
    assert synced.timeline_clips[0]["source_cue_ids"] == ["cue_0001", "cue_0002"]


def test_timeline_audio_export_includes_every_split_localized_tts_segment(tmp_path: Path, monkeypatch):
    first_audio = tmp_path / "first.mp3"
    second_audio = tmp_path / "second.mp3"
    first_audio.write_bytes(b"first")
    second_audio.write_bytes(b"second")
    draft = VideoLocalizationDraft(
        cues=[_cue("cue_0001", "speaker_a", 0, 2000)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=0,
                end_ms=900,
                text="第一条",
                tts_text="第一条",
                tts_audio_path=str(first_audio),
                generated_duration_ms=800,
                source_cue_ids=["cue_0001"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0002",
                start_ms=1000,
                end_ms=2000,
                text="第二条",
                tts_text="第二条",
                tts_audio_path=str(second_audio),
                generated_duration_ms=950,
                source_cue_ids=["cue_0001"],
            ),
        ],
    )

    monkeypatch.setattr(exporting.media_assets, "project_video_localization_dir", lambda _project_id: tmp_path / "project")

    def fake_crop(_source, destination, _start_ms, _end_ms, *, fmt):
        assert fmt == "wav"
        destination.write_bytes(b"segment")

    monkeypatch.setattr(exporting.audio_tools, "crop_file", fake_crop)
    monkeypatch.setattr(exporting.audio_tools, "probe_audio", lambda _path: {"duration_ms": 800})
    monkeypatch.setattr(
        exporting.audio_tools,
        "read_audio",
        lambda _path: (np.ones(80, dtype=np.float32), 1000),
    )
    monkeypatch.setattr(
        exporting.audio_tools,
        "write_audio",
        lambda path, _audio, _sample_rate, *, fmt: path.write_bytes(b"dub-track"),
    )

    manifest = exporting.timeline_audio_package("project_001", "导出一拆多", draft)

    assert [segment["subtitle_id"] for segment in manifest["segments"]] == ["localized_0001", "localized_0002"]
    assert [segment["source_cue_ids"] for segment in manifest["segments"]] == [["cue_0001"], ["cue_0001"]]
    assert manifest["missing_segments"] == []
    assert Path(manifest["package_path"]).exists()
    assert [item["tts_audio_path"] for item in manifest["edl"]["localized_subtitles"]] == [
        str(first_audio),
        str(second_audio),
    ]


def test_legacy_per_cue_batch_result_and_export_fallback_remain_supported(tmp_path: Path):
    audio_path = tmp_path / "legacy.mp3"
    audio_path.write_bytes(b"legacy")
    draft = VideoLocalizationDraft(cues=[_cue("cue_0001", "speaker_a", 0, 1000)])

    synced = tts_pipeline.with_synced_batch_results(
        draft,
        BatchTask(
            batch_task_id="batch_legacy",
            project_name="旧逐 cue",
            engine_id="indextts-v2",
            status=TaskStatus.success,
            segments=[
                BatchSegmentResult(
                    segment_id="cue_0001",
                    text="旧逐 cue",
                    output_path=str(audio_path),
                    duration_ms=900,
                    status=TaskStatus.success,
                )
            ],
        ),
    )

    assert synced.cues[0].tts_audio_path == str(audio_path)
    assert synced.localized_subtitles == []
    clips = exporting._renderable_dub_clips(synced)
    assert len(clips) == 1
    assert clips[0]["cue_id"] == "cue_0001"
    assert clips[0]["audio_path"] == str(audio_path)


def test_zh_srt_import_with_overwrite_tts_uses_imported_text_and_syncs_mirror(tmp_path: Path):
    cue = _cue("cue_0001", "speaker_a", 0, 1000).model_copy(
        update={
            "tts_result_id": "old-result",
            "tts_audio_path": str(tmp_path / "old.mp3"),
            "tts_batch_task_id": "old-batch",
            "tts_batch_status": "success",
            "generated_duration_ms": 900,
            "quality_flags": ["tts_generated"],
        }
    )
    draft = VideoLocalizationDraft(cues=[cue], reference_clips=[_reference(tmp_path)])

    imported = subtitles.import_srt(
        draft,
        "zh",
        "1\n00:00:00,000 --> 00:00:01,000\n导入的新台词\n",
        overwrite_tts=True,
    )

    subtitle = imported.localized_subtitles[0]
    mirrored_cue = imported.cues[0]
    assert subtitle.tts_text == "导入的新台词"
    assert subtitle.source_cue_ids == ["cue_0001"]
    assert mirrored_cue.zh_localized_subtitle_text == "导入的新台词"
    assert mirrored_cue.tts_recommended_text == "导入的新台词"
    assert mirrored_cue.tts_result_id is None
    assert mirrored_cue.tts_audio_path is None
    assert "tts_generated" not in mirrored_cue.quality_flags
    request = tts_pipeline.build_batch_request(
        project_id="project_001",
        project_name="覆盖 TTS",
        draft=imported,
        output_dir=tmp_path / "output",
    )
    assert request.segments[0].text == "导入的新台词"


def test_zh_srt_import_without_overwrite_tts_preserves_existing_text_and_generation(tmp_path: Path):
    cue = _cue("cue_0001", "speaker_a", 0, 1000).model_copy(
        update={"tts_recommended_text": "保留已有台词"}
    )
    draft = VideoLocalizationDraft(
        cues=[cue],
        reference_clips=[_reference(tmp_path)],
    )

    imported = subtitles.import_srt(
        draft,
        "zh",
        "1\n00:00:00,000 --> 00:00:01,000\n只替换中文字幕\n",
        overwrite_tts=False,
    )

    assert imported.localized_subtitles[0].tts_text == "保留已有台词"
    assert imported.cues[0].tts_recommended_text == "保留已有台词"
    request = tts_pipeline.build_batch_request(
        project_id="project_001",
        project_name="不覆盖 TTS",
        draft=imported,
        output_dir=tmp_path / "output",
    )
    assert request.segments[0].text == "保留已有台词"
