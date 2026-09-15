from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (
    dubbing_production,
    service,
    subtitles,
    tts_orchestration,
    tts_pipeline,
)
from app.domains.video_localization.schemas import (
    BatchSegmentResult,
    BatchTask,
    TaskStatus,
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationReferenceClip,
    VideoLocalizationSpokenSegment,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTranscriptionState,
)
from app.errors import AppException
from app.domains.video_localization.tts_selection import TtsSelectionRequest, build_selection_snapshot
from app.services import audio_tools, task_queue, voice_store
from app.schemas.voice_studio import GenerationTask


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


def _selection_handoff(
    project_id: str,
    draft: VideoLocalizationDraft,
    *subtitle_ids: str,
):
    selection = build_selection_snapshot(
        draft,
        TtsSelectionRequest(target_subtitle_ids=list(subtitle_ids)),
    )
    return tts_orchestration.build_selection_handoff(project_id, draft, selection)


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


def test_localized_tts_outputs_use_pronunciation_text_without_changing_display_text(tmp_path: Path, monkeypatch):
    vocals = tmp_path / "vocals.wav"
    vocals.write_bytes(b"vocals")
    managed_source = tmp_path / "managed-source.wav"
    managed_clip = tmp_path / "managed-clip.wav"
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 8_000},
        stems={"separation_status": "completed", "vocals_clean_path": str(vocals)},
        cues=[_cue("cue_0001", "speaker_a", 1_000, 2_800)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=1_000,
                end_ms=2_800,
                text="Seedance 2.0 是 AI 视频模型 支持 4K",
                tts_text="Seedance 2.0 是 AI 视频模型，支持 4K。",
                source_cue_ids=["cue_0001"],
            )
        ],
        reference_clips=[_reference(tmp_path)],
    )

    batch = tts_pipeline.build_batch_request(
        project_id="project_001",
        project_name="本土化轨 TTS",
        draft=draft,
        output_dir=tmp_path / "output",
    )

    monkeypatch.setattr(
        voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: SimpleNamespace(file_id="source-file", path=str(managed_source), duration_ms=8_000),
    )
    monkeypatch.setattr(voice_store, "create_audio_clip", lambda *args, **kwargs: {"path": str(managed_clip)})
    handoff = _selection_handoff("project_001", draft, "localized_0001")

    expected = "Seedance 二点零是 A I 视频模型，支持四 K。"
    assert draft.localized_subtitles[0].text == "Seedance 2.0 是 AI 视频模型 支持 4K"
    assert batch.segments[0].text == expected
    assert handoff.text == expected


def test_selection_handoff_uses_exact_managed_vocals_range_and_localized_segment_id(tmp_path: Path, monkeypatch):
    vocals = tmp_path / "vocals.wav"
    vocals.write_bytes(b"vocals")
    managed_source = tmp_path / "managed-source.wav"
    managed_clip = tmp_path / "managed-clip.wav"
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 8_000},
        stems={"separation_status": "completed", "vocals_clean_path": str(vocals)},
        cues=[_cue("cue_0001", "speaker_a", 1_000, 2_800)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=1_000,
                end_ms=2_800,
                text="上屏字幕",
                tts_text="配音台词",
                source_cue_ids=["cue_0001"],
            )
        ],
    )
    monkeypatch.setattr(
        voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: SimpleNamespace(file_id="source-file", path=str(managed_source), duration_ms=8_000),
    )
    monkeypatch.setattr(
        voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(managed_clip)},
    )
    settings = tts_orchestration.settings_store.get().model_copy(
        update={
            "default_engine_id": "omnivoice",
            "default_language": "zh",
            "default_output_format": "flac",
        }
    )
    monkeypatch.setattr(
        tts_orchestration.settings_store,
        "get",
        lambda: settings,
    )

    request = _selection_handoff("project_001", draft, "localized_0001")

    assert request.segment_id == "localized_0001"
    assert request.engine_id == "omnivoice"
    assert request.output_format == "flac"
    assert request.localized_subtitle_id == "localized_0001"
    assert request.cue_id == "cue_0001"
    assert request.bind_to_video_localization is True
    assert request.text == "配音台词"
    assert request.reference_audio_path == str(managed_clip)
    assert request.custom_reference_source_audio_path == str(managed_source)
    assert request.custom_reference_trim_start_ms == 1_000
    assert request.custom_reference_trim_end_ms == 2_800
    assert request.ref_text == "Source cue_0001"
    assert request.reference_audio_license_status == "本土化"


def test_single_handoff_uses_spoken_timing_instead_of_display_tail_hold(
    tmp_path: Path,
    monkeypatch,
):
    vocals = tmp_path / "vocals.wav"
    vocals.write_bytes(b"vocals")
    managed_source = tmp_path / "managed-source.wav"
    managed_clip = tmp_path / "managed-clip.wav"
    source_cue = _cue("cue_0001", "speaker_a", 1_000, 3_300).model_copy(
        update={"source_duration_ms": 1_800}
    )
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 8_000},
        stems={
            "separation_status": "completed",
            "vocals_clean_path": str(vocals),
        },
        cues=[source_cue],
        localized_spoken_segments=[
            VideoLocalizationSpokenSegment(
                segment_id="spoken_0001",
                paragraph_id="paragraph_0001",
                text="配音台词",
                start_ms=1_000,
                end_ms=2_800,
                source_cue_ids=["cue_0001"],
                source_word_ids=["word_0001"],
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=1_000,
                end_ms=3_300,
                text="上屏字幕",
                tts_text="配音台词",
                source_cue_ids=["cue_0001"],
                spoken_segment_id="spoken_0001",
            )
        ],
    )
    monkeypatch.setattr(
        voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: SimpleNamespace(
            file_id="source-file",
            path=str(managed_source),
            duration_ms=8_000,
        ),
    )
    monkeypatch.setattr(
        voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(managed_clip)},
    )

    snapshot = build_selection_snapshot(
        draft,
        TtsSelectionRequest(target_subtitle_ids=["localized_0001"]),
    )

    assert snapshot.target.start_ms == 1_000
    assert snapshot.target.end_ms == 3_300


def test_single_handoff_uses_project_target_language_for_generation_and_verification(tmp_path: Path, monkeypatch):
    vocals = tmp_path / "vocals.wav"
    vocals.write_bytes(b"vocals")
    managed_source = tmp_path / "managed-source.wav"
    managed_clip = tmp_path / "managed-clip.wav"
    draft = VideoLocalizationDraft(
        language_config={"target_language": "zh-Hans"},
        source_media={"duration_ms": 8_000},
        stems={"separation_status": "completed", "vocals_clean_path": str(vocals)},
        cues=[_cue("cue_0001", "speaker_a", 1_000, 2_800)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=1_000,
                end_ms=2_800,
                text="中文本土化台词",
                source_cue_ids=["cue_0001"],
            )
        ],
    )
    monkeypatch.setattr(
        voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: SimpleNamespace(file_id="source-file", path=str(managed_source), duration_ms=8_000),
    )
    monkeypatch.setattr(
        voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(managed_clip)},
    )
    settings = tts_orchestration.settings_store.get().model_copy(
        update={
            "default_engine_id": "omnivoice",
            "default_language": "en",
            "default_output_format": "wav",
        }
    )
    monkeypatch.setattr(
        tts_orchestration.settings_store,
        "get",
        lambda: settings,
    )

    request = _selection_handoff("project_001", draft, "localized_0001")
    task = GenerationTask(
        engine_id=request.engine_id,
        input_text=request.text,
        parameters=request.model_dump(mode="json"),
    )

    assert request.language == "zh"
    assert task_queue._verification_language(task) == "zh"


def test_group_handoff_materializes_reference_for_the_full_subtitle_range(tmp_path: Path, monkeypatch):
    vocals = tmp_path / "vocals.wav"
    vocals.write_bytes(b"vocals")
    managed_source = tmp_path / "managed-source.wav"
    managed_clip = tmp_path / "managed-group.wav"
    clip_ranges: list[tuple[int, int]] = []
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 8_000},
        stems={"separation_status": "completed", "vocals_clean_path": str(vocals)},
        cues=[_cue("cue_0001", "speaker_a", 1_000, 2_800), _cue("cue_0002", "speaker_a", 2_800, 5_200)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=1_000,
                end_ms=2_800,
                text="第一条",
                tts_text="第一句",
                source_cue_ids=["cue_0001"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0002",
                start_ms=2_800,
                end_ms=5_200,
                text="第二条",
                tts_text="第二句",
                source_cue_ids=["cue_0002"],
            ),
        ],
    )
    monkeypatch.setattr(
        voice_store,
        "ensure_managed_audio_file",
        lambda *args, **kwargs: SimpleNamespace(file_id="source-file", path=str(managed_source), duration_ms=8_000),
    )

    def create_clip(_file_id: str, start_ms: int, end_ms: int, **_kwargs):
        clip_ranges.append((start_ms, end_ms))
        return {"path": str(managed_clip)}

    monkeypatch.setattr(voice_store, "create_audio_clip", create_clip)

    request = _selection_handoff(
        "project_001", draft, "localized_0001", "localized_0002"
    )

    assert clip_ranges == [(1_000, 5_200)]
    assert request.segment_id.startswith("group_localized_0001_localized_0002_2_")
    assert request.text == "第一句\n第二句"
    assert request.reference_audio_path == str(managed_clip)
    assert request.custom_reference_trim_start_ms == 1_000
    assert request.custom_reference_trim_end_ms == 5_200
    assert request.ref_text == "Source cue_0001 Source cue_0002"


def test_handoff_reference_text_uses_aligned_words_when_split_cues_share_raw_sentence():
    raw_sentence = "Everything shown here was created in minutes."
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_0001",
            text=text,
            start_ms=index * 200,
            end_ms=(index + 1) * 200,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, text in enumerate(["Everything", "shown", "here", "here", "was", "created", "in", "minutes."])
    ]
    first = _cue("cue_0001", "speaker_a", 0, 600).model_copy(
        update={
            "en_subtitle_text": "Everything shown here",
            "source_text_raw": raw_sentence,
            "source_word_ids": [word.word_id for word in words[:4]],
        }
    )
    second = _cue("cue_0002", "speaker_a", 600, 1_600).model_copy(
        update={
            "en_subtitle_text": "was created in minutes.",
            "source_text_raw": raw_sentence,
            "source_word_ids": [word.word_id for word in words[4:]],
        }
    )
    draft = VideoLocalizationDraft(
        cues=[first, second],
        transcription=VideoLocalizationTranscriptionState(words=words),
    )

    reference_text = tts_orchestration._source_text_for_range(draft, [first, second], 0, 1_600)

    assert reference_text == raw_sentence
    assert reference_text.count("Everything") == 1


def test_handoff_reference_text_excludes_cue_words_outside_trimmed_audio_range():
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="segment_0001",
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
        for index, (text, start_ms, end_ms) in enumerate(
            [
                ("The", 22_560, 22_680),
                ("camera", 22_680, 22_740),
                ("now,", 22_740, 22_800),
                ("so", 22_800, 22_900),
                ("the", 22_900, 23_000),
                ("new", 23_000, 23_100),
                ("environment", 23_100, 23_360),
                ("can't", 23_360, 23_520),
                ("sit", 23_520, 23_640),
                ("behind", 23_640, 23_860),
                ("me.", 23_860, 24_000),
                ("It", 24_000, 24_080),
                ("has", 24_080, 24_180),
                ("to", 24_180, 24_260),
                ("move", 25_200, 25_380),
                ("with", 25_380, 25_500),
                ("me", 25_500, 25_620),
            ]
        )
    ]
    cue = _cue("cue_0001", "speaker_a", 22_800, 24_000).model_copy(
        update={
            "en_subtitle_text": "so the new environment can't sit behind me.",
            "source_word_ids": [word.word_id for word in words],
        }
    )
    draft = VideoLocalizationDraft(
        cues=[cue],
        transcription=VideoLocalizationTranscriptionState(words=words),
    )

    reference_text = tts_orchestration._source_text_for_range(draft, [cue], 22_750, 24_250)

    assert reference_text == "now, so the new environment can't sit behind me. It has to"
    assert "camera" not in reference_text
    assert "move with me" not in reference_text


def test_handoff_reference_text_uses_cue_partitions_without_word_alignment():
    raw_sentence = "Everything shown here was created in minutes."
    first = _cue("cue_0001", "speaker_a", 0, 600).model_copy(
        update={
            "en_subtitle_text": "Everything shown here",
            "source_text_raw": raw_sentence,
            "source_word_ids": [],
        }
    )
    second = _cue("cue_0002", "speaker_a", 600, 1_400).model_copy(
        update={
            "en_subtitle_text": "was created in minutes.",
            "source_text_raw": raw_sentence,
            "source_word_ids": [],
        }
    )

    reference_text = tts_orchestration._source_text_for_range(
        VideoLocalizationDraft(cues=[first, second]),
        [first, second],
        0,
        1_400,
    )

    assert reference_text == raw_sentence
    assert reference_text.count("Everything") == 1


def test_single_tts_submission_collapses_accidental_exact_subtitle_repetition():
    canonical = "所以我一次性要了几种不同的世界。"

    assert service._normalize_single_tts_submission_text(canonical * 2, canonical) == canonical
    assert service._normalize_single_tts_submission_text(f"{canonical}\n{canonical}", canonical) == canonical
    assert service._normalize_single_tts_submission_text("所以我一次性要了几种不同的世界，真的。", canonical) == "所以我一次性要了几种不同的世界，真的。"


def test_short_handoff_reference_range_expands_within_source_bounds():
    assert tts_orchestration._expanded_reference_range(100, 600, 8_000) == (0, 2_500)
    assert tts_orchestration._expanded_reference_range(7_400, 7_900, 8_000) == (5_500, 8_000)
    assert tts_orchestration._expanded_reference_range(1_000, 3_100, 8_000) == (1_000, 3_100)
    assert tts_orchestration._expanded_reference_range(1_000, 5_200, 8_000) == (1_000, 5_200)


def test_short_handoff_reference_range_does_not_expand_into_another_speaker():
    draft = VideoLocalizationDraft(
        cues=[
            _cue("cue_0001", "speaker_a", 100, 600),
            _cue("cue_0002", "speaker_b", 600, 2_000),
        ]
    )
    bounds = tts_orchestration._speaker_reference_bounds(draft, "speaker_a", 100, 600, 8_000)

    assert bounds == (0, 600)
    assert tts_orchestration._expanded_reference_range(100, 600, 8_000, *bounds) == (0, 600)


def test_legacy_group_tts_result_keeps_partially_overlapping_clips_and_updates_only_its_target(tmp_path: Path):
    audio_path = tmp_path / "group.wav"
    audio_tools.write_audio(audio_path, np.full(4_000, 0.35, dtype=np.float32), 1_000)
    segment_id = "group_localized_0001_localized_0002_2"
    draft = VideoLocalizationDraft(
        cues=[_cue("cue_0001", "speaker_a", 1_000, 2_800), _cue("cue_0002", "speaker_a", 2_800, 5_200)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(subtitle_id="localized_0001", start_ms=1_000, end_ms=2_800, text="第一条", source_cue_ids=["cue_0001"]),
            VideoLocalizationSubtitleCue(subtitle_id="localized_0002", start_ms=2_800, end_ms=5_200, text="第二条", source_cue_ids=["cue_0002"]),
        ],
        timeline_clips=[
            {"clip_id": "clip_localized_0001", "track_id": "dub", "subtitle_id": "localized_0001", "source_cue_ids": ["cue_0001"]},
            {"clip_id": f"clip_{segment_id}", "track_id": "dub", "subtitle_id": segment_id, "source_cue_ids": ["cue_0001", "cue_0002"], "status": "queued"},
        ],
        generated_candidates=[
            {"candidate_id": "candidate_task-group", "task_id": "task-group", "subtitle_id": segment_id, "status": "queued"}
        ],
    )
    draft = draft.model_copy(
        update={
            "dubbing_production": draft.dubbing_production.model_copy(
                update={"enforcement_mode": "legacy"}
            )
        }
    )

    updated = tts_pipeline.with_single_tts_result(
        draft,
        segment_id,
        result_id="result-group",
        output_path=str(audio_path),
        duration_ms=4_000,
        task_id="task-group",
        generation_id="task-group",
    )

    assert all(item.tts_result_id is None for item in updated.localized_subtitles)
    dub_clips = [item for item in updated.timeline_clips if item.get("track_id") == "dub"]
    assert len(dub_clips) == 2
    individual = next(item for item in dub_clips if item["subtitle_id"] == "localized_0001")
    group = next(item for item in dub_clips if item["subtitle_id"] == segment_id)
    assert individual["clip_id"] == "clip_localized_0001"
    assert group["result_id"] == "result-group"
    assert group["audio_path"] == str(audio_path)
    assert group["status"] == "ready"
    assert updated.generated_candidates[0]["result_id"] == "result-group"


def test_planned_group_result_stays_in_candidate_history_until_cqc_acceptance(
    tmp_path: Path,
):
    audio_path = tmp_path / "planned-group.wav"
    audio_tools.write_audio(
        audio_path,
        np.full(4_000, 0.35, dtype=np.float32),
        1_000,
    )
    segment_id = "group_localized_0001_localized_0002_2"
    base = VideoLocalizationDraft(
        cues=[
            _cue("cue_0001", "speaker_a", 1_000, 2_800),
            _cue("cue_0002", "speaker_a", 2_800, 5_200),
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=1_000,
                end_ms=2_800,
                text="第一条",
                tts_text="第一条",
                source_cue_ids=["cue_0001"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0002",
                start_ms=2_800,
                end_ms=5_200,
                text="第二条",
                tts_text="第二条",
                source_cue_ids=["cue_0002"],
            ),
        ],
        generated_candidates=[
            {
                "candidate_id": "candidate_task-planned",
                "task_id": "task-planned",
                "subtitle_id": segment_id,
                "status": "queued",
            }
        ],
    )
    snapshot = dubbing_production.build_project_snapshot(base)
    plan = dubbing_production.build_generation_plan(
        dubbing_production.DubbingGenerationPlanInput(
            source_revision=snapshot.source_revision,
            semantic_units=snapshot.semantic_units,
            boundaries=snapshot.boundaries,
        )
    )
    draft = base.model_copy(
        update={
            "dubbing_production": base.dubbing_production.model_copy(
                update={
                    "enforcement_mode": "planned",
                    "active_plan": plan,
                }
            )
        }
    )

    updated = tts_pipeline.with_single_tts_result(
        draft,
        segment_id,
        result_id="result-planned",
        output_path=str(audio_path),
        duration_ms=4_000,
        task_id="task-planned",
        generation_id="task-planned",
    )

    assert updated.timeline_clips == []
    assert updated.generated_candidates[0]["result_id"] == "result-planned"
    assert updated.generated_candidates[0]["audio_path"] == str(audio_path)


def test_single_tts_result_aligns_first_effective_speech_to_subtitle_in_point(tmp_path: Path):
    audio_path = tmp_path / "leading-silence.wav"
    audio = np.concatenate(
        [
            np.zeros(300, dtype=np.float32),
            np.full(700, 0.4, dtype=np.float32),
        ]
    )
    audio_tools.write_audio(audio_path, audio, 1000)
    draft = VideoLocalizationDraft(
        cues=[_cue("cue_0001", "speaker_a", 1000, 2000)],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=1000,
                end_ms=2000,
                text="上屏字幕",
                tts_text="配音台词",
                source_cue_ids=["cue_0001"],
            )
        ],
    )

    synced = tts_pipeline.with_single_tts_result(
        draft,
        "localized_0001",
        result_id="result-onset",
        output_path=str(audio_path),
        duration_ms=1000,
        task_id="generation-onset",
    )

    clip = synced.timeline_clips[0]
    assert 900 <= clip["start_ms"] < 1000
    assert clip["end_ms"] == clip["start_ms"] + clip["source_end_ms"] - clip["source_start_ms"]
    assert clip["source_end_ms"] == 1000
    assert 280 <= clip["speech_onset_ms"] <= 300
    assert 0 < clip["alignment_lead_ms"] <= 80
    assert clip["start_ms"] + clip["alignment_lead_ms"] == 1000


def test_single_asr_tts_result_adds_then_replaces_timeline_audio_without_placeholder(tmp_path: Path):
    first_audio = tmp_path / "first.wav"
    second_audio = tmp_path / "second.wav"
    audio_tools.write_audio(first_audio, np.full(900, 0.3, dtype=np.float32), 1000)
    audio_tools.write_audio(second_audio, np.full(1200, 0.4, dtype=np.float32), 1000)
    draft = VideoLocalizationDraft(cues=[_cue("cue_0001", "speaker_a", 1000, 2500)])

    first = tts_pipeline.with_single_tts_result(
        draft,
        "cue_0001",
        result_id="result-one",
        output_path=str(first_audio),
        duration_ms=900,
        task_id="task-one",
        generation_id="task-one",
    )
    second = tts_pipeline.with_single_tts_result(
        first,
        "cue_0001",
        result_id="result-two",
        output_path=str(second_audio),
        duration_ms=1200,
        task_id="task-two",
        generation_id="task-two",
    )

    dub_clips = [item for item in second.timeline_clips if item.get("track_id") == "dub"]
    assert len(dub_clips) == 1
    assert dub_clips[0]["audio_path"] == str(second_audio)
    assert dub_clips[0]["task_id"] == "task-two"
    assert dub_clips[0]["result_id"] == "result-two"
    assert dub_clips[0]["start_ms"] == 1000
    assert dub_clips[0]["end_ms"] == 2200


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
