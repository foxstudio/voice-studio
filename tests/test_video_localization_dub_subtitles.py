from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.domains.video_localization import (
    dub_subtitles,
    operation_queue,
    operation_state,
    quality_gate,
    service,
    subtitle_entry_timing,
    transcription,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationOperationRequest,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTranscriptSegment,
)
from app.errors import AppException
from app.services import audio_tools
from app.schemas import video_localization_dub_subtitle_step as step_contracts


def _audio(path: Path, duration_ms: int = 1_000) -> Path:
    sample_rate = 16_000
    samples = np.zeros(
        int(sample_rate * duration_ms / 1_000),
        dtype=np.float32,
    )
    audio_tools.write_audio(path, samples, sample_rate, fmt="wav")
    return path


def _tone_audio(
    path: Path,
    *,
    duration_ms: int = 1_000,
    amplitude: float = 0.3,
) -> Path:
    sample_rate = 16_000
    frame_count = int(sample_rate * duration_ms / 1_000)
    time = np.arange(frame_count, dtype=np.float32) / sample_rate
    samples = amplitude * np.sin(2 * np.pi * 440 * time)
    audio_tools.write_audio(path, samples, sample_rate, fmt="wav")
    return path


def _draft(
    tmp_path: Path,
    *,
    lane_zero_muted: bool = False,
    lane_one_muted: bool = False,
) -> VideoLocalizationDraft:
    first = _audio(tmp_path / "first.wav")
    second = _audio(tmp_path / "second.wav")
    return VideoLocalizationDraft(
        source_media={"duration_ms": 2_500},
        cues=[
            VideoLocalizationCue(
                cue_id="cue-1",
                speaker_id="speaker-1",
                start_ms=0,
                end_ms=1_000,
                en_subtitle_text="hello",
            ),
            VideoLocalizationCue(
                cue_id="cue-2",
                start_ms=1_000,
                end_ms=2_000,
                en_subtitle_text="world",
            ),
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized-1",
                start_ms=0,
                end_ms=1_000,
                text="你好。",
                tts_text="你好。",
                source_cue_ids=["cue-1"],
            ),
            VideoLocalizationSubtitleCue(
                subtitle_id="localized-2",
                start_ms=1_000,
                end_ms=2_000,
                text="后来改过。",
                source_cue_ids=["cue-2"],
            ),
        ],
        timeline_clips=[
            {
                "clip_id": "clip-1",
                "track_id": "dub",
                "dub_lane": 0,
                "start_ms": 100,
                "end_ms": 1_000,
                "source_start_ms": 50,
                "source_end_ms": 950,
                "audio_path": str(first),
                "target_subtitle_ids": ["localized-1"],
                "source_cue_ids": ["cue-1"],
            },
            {
                "clip_id": "clip-2",
                "track_id": "dub",
                "dub_lane": 1,
                "start_ms": 1_200,
                "end_ms": 2_100,
                "source_start_ms": 0,
                "source_end_ms": 900,
                "audio_path": str(second),
                "target_subtitle_ids": ["localized-2"],
                "source_cue_ids": ["cue-2"],
            },
        ],
        ui_state={
            "dub_lane_states": {
                "0": {
                    "muted": lane_zero_muted,
                    "solo": False,
                },
                "1": {
                    "muted": lane_one_muted,
                    "solo": True,
                },
            }
        },
    )


def _run_atomic_subtitle_chain(
    draft: VideoLocalizationDraft,
    tmp_path: Path,
) -> step_contracts.DubSubtitleSegmentSubtitlesOutput:
    prepare_input = dub_subtitles.freeze_workflow_input(
        draft,
    )
    artifact_path = tmp_path / "atomic-full-dub.wav"
    prepared = dub_subtitles.prepare_track(
        prepare_input.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            prepare_input,
        ),
        artifact_id="test:full-dub",
        artifact_path=artifact_path,
    )
    transcribed = dub_subtitles.transcribe_track(
        step_contracts.DubSubtitleTranscribeTrackInput(
            audio=prepared.audio,
            engine_id="qwen3-asr-mlx",
        ),
        audio_path=artifact_path,
    )
    proofread = dub_subtitles.proofread_text(
        step_contracts.DubSubtitleProofreadTextInput(
            chunks=transcribed.chunks,
            references=prepare_input.references,
        )
    )
    aligned = dub_subtitles.align_words(
        step_contracts.DubSubtitleAlignWordsInput(
            audio=prepared.audio,
            language=transcribed.language,
            chunks=proofread.chunks,
        ),
        audio_path=artifact_path,
    )
    return dub_subtitles.segment_subtitles(
        step_contracts.DubSubtitleSegmentSubtitlesInput(
            audio_sha256=aligned.audio_sha256,
            asr_requires_review=(
                transcribed.quality_status == "warning"
            ),
            chunks=[
                step_contracts.DubSubtitleSegmentTextChunk(
                    cue_id=item.cue_id,
                    text=item.text,
                )
                for item in proofread.chunks
            ],
            words=aligned.words,
            subtitle_entry_by_word_id=(
                aligned.subtitle_entry_by_word_id
            ),
            clips=[
                step_contracts.DubSubtitleTimelineClip(
                    clip_id=item.clip_id,
                    dub_lane=item.dub_lane,
                    timeline_start_ms=item.timeline_start_ms,
                    timeline_end_ms=item.timeline_end_ms,
                    speaker_id=item.speaker_id,
                )
                for item in prepare_input.prepare_track.clips
            ],
        )
    )


def _strict_alignment_stub(
    sentence_ranges: list[tuple[int, int]],
):
    def align(
        _audio_path,
        segments,
        *,
        language,
        max_duration_ms,
    ):
        del language, max_duration_ms
        units = [
            unit
            for segment in segments
            for unit in dub_subtitles._semantic_sentence_units(
                segment.corrected_text or segment.raw_text
            )
        ]
        assert len(units) == len(sentence_ranges)
        words: list[VideoLocalizationAlignedWord] = []
        unit_index = 0
        for segment in segments:
            for unit in dub_subtitles._semantic_sentence_units(
                segment.corrected_text or segment.raw_text
            ):
                start_ms, end_ms = sentence_ranges[unit_index]
                tokens = transcription.display_tokens(unit)
                for token_index, token in enumerate(tokens):
                    token_start_ms = start_ms + round(
                        (end_ms - start_ms)
                        * token_index
                        / len(tokens)
                    )
                    token_end_ms = start_ms + round(
                        (end_ms - start_ms)
                        * (token_index + 1)
                        / len(tokens)
                    )
                    words.append(
                        VideoLocalizationAlignedWord(
                            word_id=(
                                f"word_{len(words) + 1:06d}"
                            ),
                            segment_id=segment.segment_id,
                            text=token,
                            start_ms=token_start_ms,
                            end_ms=token_end_ms,
                            timing_confidence="high",
                            timing_source="forced_aligner",
                        )
                    )
                unit_index += 1
        return words, {
            "quality_flags": ["timing:forced-aligner"],
            "alignment_call_count": len(segments),
        }

    return align


def test_freeze_prepare_input_uses_every_unmuted_dub_lane_and_is_path_free(
    tmp_path: Path,
):
    draft = _draft(
        tmp_path,
        lane_zero_muted=True,
        lane_one_muted=False,
    )

    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )

    assert [item.clip_id for item in frozen.prepare_track.clips] == ["clip-2"]
    assert [item.dub_lane for item in frozen.prepare_track.clips] == [1]
    assert [
        (item.subtitle_id, item.text)
        for item in frozen.references
    ] == [
        ("localized-2", "后来改过"),
    ]
    assert "start_ms" not in frozen.references[0].model_dump()
    assert "end_ms" not in frozen.references[0].model_dump()
    assert frozen.prepare_track.clips[0].speaker_id is None
    assert "audio_path" not in frozen.model_dump_json()


def test_freeze_prepare_input_uses_real_audio_length_for_both_ranges(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    short_audio = _audio(tmp_path / "first.wav", duration_ms=2_480)
    clip = {
        **draft.timeline_clips[0],
        "start_ms": 0,
        "end_ms": 2_500,
        "source_start_ms": 0,
        "source_end_ms": 2_500,
        "audio_path": str(short_audio),
    }
    draft = draft.model_copy(update={"timeline_clips": [clip]})

    frozen = dub_subtitles.freeze_workflow_input(draft)
    prepared_clip = frozen.prepare_track.clips[0]

    assert prepared_clip.timeline_start_ms == 0
    assert prepared_clip.timeline_end_ms == 2_480
    assert prepared_clip.source_start_ms == 0
    assert prepared_clip.source_end_ms == 2_480

    prepared = dub_subtitles.prepare_track(
        frozen.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            frozen,
        ),
        artifact_id="test:short-real-audio",
        artifact_path=tmp_path / "short-real-audio-track.wav",
    )

    assert prepared.clip_count == 1


def test_freeze_prepare_input_rounds_up_to_keep_final_audio_sample(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    audio_path = tmp_path / "first.wav"
    audio_tools.write_audio(
        audio_path,
        np.zeros(48_001, dtype=np.float32),
        48_000,
    )
    clip = {
        **draft.timeline_clips[0],
        "start_ms": 0,
        "end_ms": 1_001,
        "source_start_ms": 0,
        "source_end_ms": 1_001,
        "audio_path": str(audio_path),
    }
    draft = draft.model_copy(update={"timeline_clips": [clip]})

    frozen = dub_subtitles.freeze_workflow_input(draft)
    prepared_clip = frozen.prepare_track.clips[0]

    assert prepared_clip.timeline_end_ms == 1_001
    assert prepared_clip.source_end_ms == 1_001


def test_prepare_track_preserves_leading_middle_and_trailing_silence(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    _tone_audio(tmp_path / "first.wav", amplitude=0.25)
    _tone_audio(tmp_path / "second.wav", amplitude=0.45)
    workflow_input = dub_subtitles.freeze_workflow_input(draft)
    artifact_path = tmp_path / "full-track-with-gaps.wav"

    prepared = dub_subtitles.prepare_track(
        workflow_input.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            workflow_input,
        ),
        artifact_id="test:full-track-with-gaps",
        artifact_path=artifact_path,
    )

    audio, sample_rate = audio_tools.read_audio(artifact_path)

    def peak(start_ms: int, end_ms: int) -> float:
        start = int(sample_rate * start_ms / 1_000)
        end = int(sample_rate * end_ms / 1_000)
        return float(np.max(np.abs(audio[start:end])))

    assert prepared.audio.duration_ms == 2_500
    assert peak(0, 90) < 1e-4
    assert peak(200, 800) > 0.2
    assert peak(1_110, 1_190) < 1e-4
    assert peak(1_300, 1_900) > 0.4
    assert peak(2_110, 2_490) < 1e-4


def test_freeze_prepare_input_uses_display_text_and_existing_subtitle_punctuation(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    localized = list(draft.localized_subtitles)
    localized[0] = localized[0].model_copy(
        update={
            "text": "Seedance 2.0 生成的 4K 效果，真的可以吗？！",
            "tts_text": "Seedance 二点零生成的四 K 效果，真的可以吗？！",
        }
    )
    draft = draft.model_copy(
        update={"localized_subtitles": localized}
    )

    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )

    assert frozen.prepare_track.timeline_duration_ms == 2_500
    assert frozen.references[0].text == (
        "Seedance 2.0 生成的 4K 效果 真的可以吗"
    )
    assert "四 K" not in frozen.references[0].text


def test_freeze_prepare_input_preserves_localized_subtitle_boundaries(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    localized = [
        *draft.localized_subtitles,
        draft.localized_subtitles[-1].model_copy(
            update={
                "subtitle_id": "localized-3",
                "text": "没有直接关联到片段的本土化文字",
                "tts_text": "没有直接关联到片段的本土化文字",
            }
        ),
    ]
    first_clip = dict(draft.timeline_clips[0])
    first_clip["target_subtitle_ids"] = [
        "localized-1",
        "localized-2",
    ]
    draft = draft.model_copy(
        update={
            "timeline_clips": [first_clip],
            "localized_subtitles": localized,
        }
    )

    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )

    assert frozen.prepare_track.clips[0].reference_ids == [
        "localized-1",
        "localized-2",
    ]
    assert [item.text for item in frozen.references] == [
        "你好",
        "后来改过",
    ]


def test_freeze_prepare_input_prefers_the_exact_generated_task_text(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    clips = [dict(item) for item in draft.timeline_clips]
    clips[0]["task_id"] = "task-technical"
    clips[1]["task_id"] = "task-dialogue"
    draft = draft.model_copy(update={"timeline_clips": clips})

    frozen = dub_subtitles.freeze_workflow_input(
        draft,
        generation_text_by_task_id={
            "task-technical": (
                "在 Higgsfield 里命名为 @car 2.0"
            ),
            "task-dialogue": "行 听你的",
        },
    )

    assert [item.text for item in frozen.references] == [
        "在 Higgsfield 里命名为 @car 2.0",
        "行 听你的",
    ]
    assert [item.display_text for item in frozen.references] == [
        "你好",
        "后来改过",
    ]


def test_freeze_prefers_adopted_clip_text_over_transient_task_queue_text(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    clips = [dict(item) for item in draft.timeline_clips]
    clips[0].update(
        {
            "task_id": "task-technical",
            "tts_target_text": "正式采用的完整台词",
        }
    )
    draft = draft.model_copy(update={"timeline_clips": clips})

    with_transient_task = dub_subtitles.freeze_workflow_input(
        draft,
        generation_text_by_task_id={
            "task-technical": "队列里已经过期的台词",
        },
    )
    without_transient_task = dub_subtitles.freeze_workflow_input(
        draft,
    )

    assert with_transient_task.references[0].text == "正式采用的完整台词"
    assert (
        with_transient_task.source_revision
        == without_transient_task.source_revision
    )


def test_freeze_prepare_input_never_uses_subtitle_time_as_reference_fallback(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    clip = dict(draft.timeline_clips[0])
    clip["target_subtitle_ids"] = []
    clip.pop("subtitle_id", None)
    draft = draft.model_copy(update={"timeline_clips": [clip]})

    with pytest.raises(AppException) as raised:
        dub_subtitles.freeze_workflow_input(
            draft,
    )

    assert (
        raised.value.code
        == "VIDEO_LOCALIZATION_DUB_SUBTITLE_REFERENCE_MISSING"
    )


def test_freeze_prepare_input_rejects_clip_beyond_video_duration(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    clip = dict(draft.timeline_clips[0])
    clip["end_ms"] = 3_000
    draft = draft.model_copy(update={"timeline_clips": [clip]})

    with pytest.raises(AppException) as raised:
        dub_subtitles.freeze_workflow_input(
            draft,
    )

    assert (
        raised.value.code
        == "VIDEO_LOCALIZATION_DUB_CLIP_OUTSIDE_VIDEO"
    )


def test_freeze_prepare_input_ignores_solo_and_keeps_all_unmuted_lanes(
    tmp_path: Path,
):
    frozen = dub_subtitles.freeze_workflow_input(
        _draft(tmp_path),
    )

    assert [item.clip_id for item in frozen.prepare_track.clips] == [
        "clip-1",
        "clip-2",
    ]


def test_freeze_prepare_input_resolves_split_audio_from_media_source(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    source = dict(draft.timeline_clips[0])
    source.update(
        {
            "end_ms": 500,
            "source_end_ms": 500,
            "media_source_clip_id": "clip-1",
        }
    )
    split = {
        **source,
        "clip_id": "clip-1-part-2",
        "start_ms": 500,
        "end_ms": 1_000,
        "source_start_ms": 500,
        "source_end_ms": 1_000,
    }
    split.pop("audio_path")
    draft = draft.model_copy(
        update={"timeline_clips": [source, split]}
    )

    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )
    paths = dub_subtitles.resolve_prepare_audio_paths(
        draft,
        frozen,
    )

    assert [item.clip_id for item in frozen.prepare_track.clips] == [
        "clip-1",
        "clip-1-part-2",
    ]
    assert paths["clip-1-part-2"] == paths["clip-1"]
    assert frozen.prepare_track.clips[1].source_start_ms == 500
    assert frozen.prepare_track.clips[1].source_end_ms == 1_000


def test_freeze_prepare_input_resolves_orphaned_split_from_tts_result(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    source = dict(draft.timeline_clips[0])
    split = {
        **source,
        "clip_id": "clip-1-part-2",
        "media_source_clip_id": "clip-1",
        "start_ms": 500,
        "end_ms": 1_000,
        "source_start_ms": 500,
        "source_end_ms": 1_000,
    }
    split.pop("audio_path")
    localized = list(draft.localized_subtitles)
    localized[0] = localized[0].model_copy(
        update={"tts_audio_path": source["audio_path"]}
    )
    draft = draft.model_copy(
        update={
            "localized_subtitles": localized,
            "timeline_clips": [split],
        }
    )

    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )
    paths = dub_subtitles.resolve_prepare_audio_paths(
        draft,
        frozen,
    )

    assert [item.clip_id for item in frozen.prepare_track.clips] == [
        "clip-1-part-2"
    ]
    assert paths["clip-1-part-2"] == Path(source["audio_path"])


def test_placeholder_dub_clip_does_not_block_audible_audio(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    draft = draft.model_copy(
        update={
            "timeline_clips": [
                draft.timeline_clips[0],
                {
                    "clip_id": "optimistic-placeholder",
                    "track_id": "dub",
                    "dub_lane": 1,
                    "start_ms": 1_200,
                    "end_ms": 2_100,
                    "audio_path": "",
                    "status": "queued",
                },
            ],
            "ui_state": {
                "dub_lane_states": {
                    "0": {"muted": False},
                    "1": {"muted": False},
                }
            },
        }
    )

    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )

    assert [item.clip_id for item in frozen.prepare_track.clips] == ["clip-1"]


def test_ready_dub_clip_without_audio_stops_instead_of_being_dropped(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    draft = draft.model_copy(
        update={
            "timeline_clips": [
                draft.timeline_clips[0],
                {
                    "clip_id": "ready-without-audio",
                    "track_id": "dub",
                    "dub_lane": 0,
                    "start_ms": 1_200,
                    "end_ms": 2_100,
                    "audio_path": "",
                    "status": "ready",
                },
            ],
        }
    )

    with pytest.raises(AppException) as raised:
        dub_subtitles.freeze_workflow_input(draft)

    assert (
        raised.value.code
        == "VIDEO_LOCALIZATION_DUB_CLIP_AUDIO_MISSING"
    )
    assert raised.value.detail_dict["clip_ids"] == [
        "ready-without-audio"
    ]


def test_all_occupied_dub_lanes_muted_is_visible_prerequisite_failure(
    tmp_path: Path,
):
    draft = _draft(
        tmp_path,
        lane_zero_muted=True,
        lane_one_muted=True,
    )

    with pytest.raises(AppException) as raised:
        operation_state.validate_prerequisites(
            "dub_subtitle_generation",
            draft,
            {},
        )

    assert (
        raised.value.code
        == "VIDEO_LOCALIZATION_DUB_LANES_ALL_MUTED"
    )
    assert "至少打开一条" in raised.value.message


def test_dub_subtitle_prerequisites_do_not_require_production_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)

    def unexpected_audit(*_args, **_kwargs):
        raise AssertionError(
            "配音生产审核只能提示，不能阻止生成配音字幕"
        )

    monkeypatch.setattr(
        quality_gate,
        "dubbing_production_timeline_blockers",
        unexpected_audit,
    )

    operation_state.validate_prerequisites(
        "dub_subtitle_generation",
        draft,
        {},
    )


def test_operation_parameters_accept_formal_and_development_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    monkeypatch.setattr(
        operation_queue.settings_store,
        "get",
        lambda: type(
            "Settings",
            (),
            {
                "video_localization_development_step_control_enabled": True,
            },
        )(),
    )

    normalized = operation_queue._normalized_operation_parameters(
        "dub_subtitle_generation",
        {"engine_id": "qwen3-asr-mlx"},
        draft,
    )

    assert normalized["engine_id"] == "qwen3-asr-mlx"
    assert normalized["execution_mode"] == "full"
    assert set(normalized) == {
        "engine_id",
        "regeneration_mode",
        "execution_mode",
        "scope",
    }
    development = operation_queue._normalized_operation_parameters(
        "dub_subtitle_generation",
        {
            "engine_id": "qwen3-asr-mlx",
            "execution_mode": "development_target",
            "development_target_step_id": "transcribe_track",
            "development_session_id": "dub-debug-1",
        },
        draft,
    )
    assert development == {
        "engine_id": "qwen3-asr-mlx",
        "regeneration_mode": "auto",
        "execution_mode": "development_target",
        "development_target_step_id": "transcribe_track",
        "development_session_id": "dub-debug-1",
        "scope": {
            "area": "development",
            "exclusive": True,
            "cancel_mode": "safe_point",
            "tracks": [
                {"id": "dub", "role": "input"},
                {"id": "development_snapshot", "role": "output"},
            ],
        },
    }
    with pytest.raises(AppException) as raised:
        operation_queue._normalized_operation_parameters(
            "dub_subtitle_generation",
            {
                "engine_id": "qwen3-asr-mlx",
                "scene_context": "not allowed",
            },
            draft,
        )
    assert (
        raised.value.code
        == "VIDEO_LOCALIZATION_DUB_SUBTITLE_PARAMETERS_INVALID"
    )
    with pytest.raises(AppException) as force_raised:
        operation_queue._normalized_operation_parameters(
            "dub_subtitle_generation",
            {
                "engine_id": "qwen3-asr-mlx",
                "execution_mode": "development_target",
                "development_target_step_id": "transcribe_track",
                "development_session_id": "dub-debug-1",
                "force_development_target": False,
            },
            draft,
        )
    assert (
        force_raised.value.code
        == "VIDEO_LOCALIZATION_DUB_SUBTITLE_PARAMETERS_INVALID"
    )
    assert force_raised.value.detail_dict == {
        "unsupported_parameters": ["force_development_target"]
    }


def test_operation_summary_and_retry_use_the_canonical_six_step_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    parameters = {
        "engine_id": "qwen3-asr-mlx",
        "execution_mode": "development_target",
        "development_target_step_id": "transcribe_track",
        "development_session_id": "dub-debug-1",
        "force_development_target": False,
        "scope": {"area": "development"},
    }
    operation = VideoLocalizationOperation(
        operation_id="operation-1",
        project_id="project-1",
        kind="dub_subtitle_generation",
        label="根据合成配音生成字幕",
        status="failed",
        parameters=parameters,
    )
    definition = (
        operation_queue
        .workflow_contracts
        .DUB_SUBTITLE_WORKFLOW_DEFINITION
    )
    summary = operation_queue._initial_operation_summary(
        "dub_subtitle_generation",
        parameters,
    )
    expected_tasks = [
        task
        for stage in definition.stages
        for task in stage.atomic_tasks
    ]

    assert summary["workflow_schema_version"] == definition.schema_version
    assert summary["workflow_id"] == definition.workflow_id
    assert list(summary["task_step_results"]) == [
        task.id for task in expected_tasks
    ]
    assert [
        (
            item["label"],
            item["order"],
            item["purpose"],
        )
        for item in summary["task_step_results"].values()
    ] == [
        (task.label, task.order, task.description)
        for task in expected_tasks
    ]

    submitted: list[dict] = []
    monkeypatch.setattr(
        operation_queue.service,
        "get_video_localization",
        lambda _project_id: draft,
    )
    monkeypatch.setattr(
        operation_queue,
        "get_operation",
        lambda _project_id, _operation_id: operation,
    )
    monkeypatch.setattr(
        operation_queue
        .video_localization_operation_step_store,
        "list_step_attempts",
        lambda _project_id, _operation_id: [],
    )
    monkeypatch.setattr(
        operation_queue,
        "submit",
        lambda _project_id, _kind, retry_parameters, **_kwargs: (
            submitted.append(retry_parameters)
            or operation
        ),
    )

    retried = operation_queue.retry("project-1", "operation-1")

    assert retried is operation
    assert submitted == [
        {
            "engine_id": "qwen3-asr-mlx",
            "execution_mode": "development_target",
            "development_target_step_id": "transcribe_track",
            "development_session_id": "dub-debug-1",
        }
    ]


def test_generic_operation_contract_rejects_dub_subtitle_generation():
    with pytest.raises(ValueError):
        VideoLocalizationOperationRequest(
            kind="dub_subtitle_generation",
            parameters={"engine_id": "qwen3-asr-mlx"},
        )


def test_six_atomic_steps_share_versioned_outputs_and_acoustic_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    localized = list(draft.localized_subtitles)
    localized[1] = localized[1].model_copy(
        update={"text": "分三级", "tts_text": "分三级"}
    )
    draft = draft.model_copy(
        update={"localized_subtitles": localized}
    )
    prepare_input = dub_subtitles.freeze_workflow_input(
        draft,
    )
    audio_paths = dub_subtitles.resolve_prepare_audio_paths(
        draft,
        prepare_input,
    )
    artifact_path = tmp_path / "full-dub.wav"
    prepared = dub_subtitles.prepare_track(
        prepare_input.prepare_track,
        audio_paths=audio_paths,
        artifact_id="development-session:prepare_track",
        artifact_path=artifact_path,
    )

    assert prepared.schema_version == (
        step_contracts
        .DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION
    )
    assert prepared.audio.duration_ms == 2_500
    assert audio_tools.probe_audio(artifact_path)["duration_ms"] == 2_500

    def fake_raw(
        request: transcription.TranscribeRawInput,
        *,
        context,
    ) -> transcription.TranscribeRawOutput:
        del context
        assert request.audio_path == str(artifact_path)
        return transcription.TranscribeRawOutput(
            input=request,
            raw_text="你好。分三集",
            language="zh",
            segments=[
                VideoLocalizationTranscriptSegment(
                    segment_id="raw-1",
                    start_ms=100,
                    end_ms=2_000,
                    raw_text="你好。分三集",
                    corrected_text="你好。分三集",
                )
            ],
            quality_summary=(
                transcription.TranscribeRawQualitySummary(
                    status="passed",
                    has_text=True,
                    has_segments=True,
                    timestamps_monotonic=True,
                    segment_count=1,
                    raw_text_char_count=6,
                    first_start_ms=100,
                    last_end_ms=2_000,
                    audio_duration_ms=2_500,
                    trailing_gap_ms=500,
                    incomplete_range_count=0,
                )
            ),
        )

    monkeypatch.setattr(
        dub_subtitles.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        fake_raw,
    )
    transcribed = dub_subtitles.transcribe_track(
        step_contracts.DubSubtitleTranscribeTrackInput(
            audio=prepared.audio,
            engine_id="qwen3-asr-mlx",
        ),
        audio_path=artifact_path,
    )
    assert transcribed.chunks[0].audio_window_start_ms == 100
    assert transcribed.chunks[0].audio_window_end_ms == 2_000

    proofread = dub_subtitles.proofread_text(
        step_contracts.DubSubtitleProofreadTextInput(
            chunks=transcribed.chunks,
            references=prepare_input.references,
        )
    )
    assert [
        (item.audio_window_start_ms, item.audio_window_end_ms)
        for item in proofread.chunks
    ] == [
        (100, 2_000)
    ]
    assert "".join(item.text for item in proofread.chunks).replace(
        " ",
        "",
    ) == "你好。分三级"

    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        _strict_alignment_stub(
            [(300, 900), (1_300, 1_900)]
        ),
    )
    aligned = dub_subtitles.align_words(
        step_contracts.DubSubtitleAlignWordsInput(
            audio=prepared.audio,
            language=transcribed.language,
            chunks=proofread.chunks,
        ),
        audio_path=artifact_path,
    )
    segmented = dub_subtitles.segment_subtitles(
        step_contracts.DubSubtitleSegmentSubtitlesInput(
            audio_sha256=aligned.audio_sha256,
            asr_requires_review=(
                transcribed.quality_status == "warning"
            ),
            chunks=[
                step_contracts.DubSubtitleSegmentTextChunk(
                    cue_id=item.cue_id,
                    text=item.text,
                )
                for item in proofread.chunks
            ],
            words=aligned.words,
            clips=[
                step_contracts.DubSubtitleTimelineClip(
                    clip_id=item.clip_id,
                    dub_lane=item.dub_lane,
                    timeline_start_ms=item.timeline_start_ms,
                    timeline_end_ms=item.timeline_end_ms,
                    speaker_id=item.speaker_id,
                )
                for item in prepare_input.prepare_track.clips
            ],
        )
    )
    assert [
        (item.start_ms, item.end_ms)
        for item in segmented.subtitles
    ] == [(300, 1_400), (1_300, 2_400)]
    assert "".join(item.text for item in segmented.subtitles).replace(
        " ",
        "",
    ) == "你好分三级"


def test_atomic_chain_transcribes_one_track_and_uses_localized_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    localized = list(draft.localized_subtitles)
    localized[1] = localized[1].model_copy(
        update={"text": "分三级。", "tts_text": "分三级。"}
    )
    draft = draft.model_copy(update={"localized_subtitles": localized})
    asr_calls = 0

    def fake_raw(
        request: transcription.TranscribeRawInput,
        *,
        context,
    ) -> transcription.TranscribeRawOutput:
        nonlocal asr_calls
        asr_calls += 1
        segment = VideoLocalizationTranscriptSegment(
            segment_id="raw-1",
            start_ms=100,
            end_ms=2_000,
            raw_text="你好。分三集",
            corrected_text="你好。分三集",
        )
        return transcription.TranscribeRawOutput(
            input=request,
            raw_text="你好。分三集",
            language="zh",
            segments=[segment],
            quality_summary=(
                transcription.TranscribeRawQualitySummary(
                    status="passed",
                    has_text=True,
                    has_segments=True,
                    timestamps_monotonic=True,
                    segment_count=1,
                    raw_text_char_count=6,
                    first_start_ms=100,
                    last_end_ms=2_000,
                    audio_duration_ms=request.duration_ms,
                    trailing_gap_ms=100,
                    incomplete_range_count=0,
                )
            ),
        )

    monkeypatch.setattr(
        dub_subtitles.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        fake_raw,
    )

    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        _strict_alignment_stub(
            [(100, 1_000), (1_400, 2_000)]
        ),
    )
    result = _run_atomic_subtitle_chain(draft, tmp_path)

    assert asr_calls == 1
    assert [
        (
            item.start_ms,
            item.end_ms,
            item.text,
            item.dub_lanes,
        )
        for item in result.subtitles
    ] == [
        (100, 1_500, "你好", [0]),
        (1_400, 2_500, "分三级", [1]),
    ]
    assert result.subtitles[0].speaker_id == "speaker-1"
    assert result.subtitles[0].needs_review is False
    assert result.subtitles[1].speaker_id is None
    assert result.subtitles[1].needs_review is True
    assert (
        "speaker_metadata_missing"
        in result.subtitles[1].quality_flags
    )


def test_transcribe_track_discards_asr_text_from_fully_silent_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    workflow_input = dub_subtitles.freeze_workflow_input(draft)
    artifact_path = tmp_path / "silent-window-full-dub.wav"
    prepared = dub_subtitles.prepare_track(
        workflow_input.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            workflow_input,
        ),
        artifact_id="test:silent-window",
        artifact_path=artifact_path,
    )

    def fake_raw(
        request: transcription.TranscribeRawInput,
        *,
        context,
    ) -> transcription.TranscribeRawOutput:
        del context
        return transcription.TranscribeRawOutput(
            input=request,
            raw_text="嗯。你好。",
            language="zh",
            segments=[
                VideoLocalizationTranscriptSegment(
                    segment_id="silent-hallucination",
                    start_ms=0,
                    end_ms=100,
                    raw_text="嗯。",
                    corrected_text="嗯。",
                ),
                VideoLocalizationTranscriptSegment(
                    segment_id="audible-speech",
                    start_ms=100,
                    end_ms=1_100,
                    raw_text="你好。",
                    corrected_text="你好。",
                ),
            ],
            quality_summary=(
                transcription.TranscribeRawQualitySummary(
                    status="passed",
                    has_text=True,
                    has_segments=True,
                    timestamps_monotonic=True,
                    segment_count=2,
                    raw_text_char_count=6,
                    first_start_ms=0,
                    last_end_ms=1_100,
                    audio_duration_ms=request.duration_ms,
                    trailing_gap_ms=request.duration_ms - 1_100,
                    incomplete_range_count=0,
                )
            ),
        )

    monkeypatch.setattr(
        dub_subtitles.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        fake_raw,
    )

    transcribed = dub_subtitles.transcribe_track(
        step_contracts.DubSubtitleTranscribeTrackInput(
            audio=prepared.audio,
            engine_id="qwen3-asr-mlx",
            audible_clips=workflow_input.prepare_track.clips,
        ),
        audio_path=artifact_path,
    )

    assert [item.segment_id for item in transcribed.chunks] == [
        "audible-speech"
    ]


def test_atomic_chain_uses_real_speech_timing_while_proofreading_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    def fake_raw(
        request: transcription.TranscribeRawInput,
        *,
        context,
    ) -> transcription.TranscribeRawOutput:
        segment = VideoLocalizationTranscriptSegment(
            segment_id="raw-1",
            start_ms=100,
            end_ms=2_000,
            raw_text="你好。后来改过。",
            corrected_text="你好。后来改过。",
        )
        return transcription.TranscribeRawOutput(
            input=request,
            raw_text=segment.raw_text,
            language="zh",
            segments=[segment],
            quality_summary=(
                transcription.TranscribeRawQualitySummary(
                    status="passed",
                    has_text=True,
                    has_segments=True,
                    timestamps_monotonic=True,
                    segment_count=1,
                    raw_text_char_count=8,
                    first_start_ms=100,
                    last_end_ms=2_000,
                    audio_duration_ms=request.duration_ms,
                    trailing_gap_ms=100,
                    incomplete_range_count=0,
                )
            ),
        )

    monkeypatch.setattr(
        dub_subtitles.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        fake_raw,
    )
    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        _strict_alignment_stub(
            [(100, 733), (1_200, 2_000)]
        ),
    )

    result = _run_atomic_subtitle_chain(draft, tmp_path)

    assert [item.text for item in result.subtitles] == [
        "你好",
        "后来改过",
    ]
    assert [
        (item.start_ms, item.end_ms)
        for item in result.subtitles
    ] == [
        (100, 1_233),
        (1_200, 2_500),
    ]
    assert all(
        "asr_timing_outside_dub_clip"
        not in item.quality_flags
        for item in result.subtitles
    )


def test_atomic_chain_never_inserts_words_missing_from_dub_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    localized = list(draft.localized_subtitles)
    localized[0] = localized[0].model_copy(
        update={
            "text": "让我的身体发生变化，不过先说一句：一定要把视频放到大屏幕上看。",
            "tts_text": "让我的身体发生变化，不过先说一句：一定要把视频放到大屏幕上看。",
        }
    )
    draft = draft.model_copy(
        update={
            "localized_subtitles": localized,
            "timeline_clips": [draft.timeline_clips[0]],
        }
    )
    def fake_raw(
        request: transcription.TranscribeRawInput,
        *,
        context,
    ) -> transcription.TranscribeRawOutput:
        segment = VideoLocalizationTranscriptSegment(
            segment_id="raw-1",
            start_ms=100,
            end_ms=900,
            raw_text="让我的身体发生变化一定要把视频放到大屏幕上看",
        )
        return transcription.build_transcribe_raw_output(
            input=request,
            raw_text=segment.raw_text,
            language="zh",
            segments=[segment],
        )

    monkeypatch.setattr(
        dub_subtitles.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        fake_raw,
    )
    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        _strict_alignment_stub([(120, 880)]),
    )

    result = _run_atomic_subtitle_chain(draft, tmp_path)
    final_text = "".join(item.text for item in result.subtitles)

    assert "让我的身体发生变化" in final_text
    assert "一定要把视频放到大屏幕上看" in final_text
    assert "不过先说一句" not in final_text


def test_atomic_chain_keeps_global_offsets_and_long_silence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    first_timeline_clip = {
        **draft.timeline_clips[0],
        "start_ms": 12_000,
        "end_ms": 13_000,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
    }
    second_timeline_clip = {
        **draft.timeline_clips[1],
        "start_ms": 42_000,
        "end_ms": 43_000,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
    }
    draft = draft.model_copy(
        update={
            "source_media": draft.source_media.model_copy(
                update={"duration_ms": 70_000}
            ),
            "timeline_clips": [
                first_timeline_clip,
                second_timeline_clip,
            ],
        }
    )
    def fake_raw(
        request: transcription.TranscribeRawInput,
        *,
        context,
    ) -> transcription.TranscribeRawOutput:
        segments = [
            VideoLocalizationTranscriptSegment(
                segment_id="raw-1",
                start_ms=0,
                end_ms=30_000,
                raw_text="你好",
            ),
            VideoLocalizationTranscriptSegment(
                segment_id="raw-2",
                start_ms=30_000,
                end_ms=60_000,
                raw_text="后来改过",
            ),
        ]
        return transcription.build_transcribe_raw_output(
            input=request,
            raw_text="你好 后来改过",
            language="zh",
            segments=segments,
        )

    monkeypatch.setattr(
        dub_subtitles.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        fake_raw,
    )
    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        _strict_alignment_stub(
            [
                (12_100, 12_900),
                (42_100, 42_900),
            ]
        ),
    )

    result = _run_atomic_subtitle_chain(draft, tmp_path)

    assert [
        (item.start_ms, item.end_ms, item.text)
        for item in result.subtitles
    ] == [
        (12_100, 13_400, "你好"),
        (42_100, 43_400, "后来改过"),
    ]


def test_transcribe_track_stops_when_full_track_asr_has_no_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    prepare_input = dub_subtitles.freeze_workflow_input(
        draft,
    )
    artifact_path = tmp_path / "empty-asr-full-dub.wav"
    prepared = dub_subtitles.prepare_track(
        prepare_input.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            prepare_input,
        ),
        artifact_id="test:empty-asr",
        artifact_path=artifact_path,
    )
    call_count = 0

    def fake_raw(
        request: transcription.TranscribeRawInput,
        *,
        context,
    ) -> transcription.TranscribeRawOutput:
        nonlocal call_count
        call_count += 1
        return transcription.TranscribeRawOutput(
            input=request,
            raw_text="",
            language="zh",
            segments=[],
            quality_summary=(
                transcription.TranscribeRawQualitySummary(
                    status="failed",
                    has_text=False,
                    has_segments=False,
                    timestamps_monotonic=True,
                    segment_count=0,
                    raw_text_char_count=0,
                    first_start_ms=None,
                    last_end_ms=None,
                    audio_duration_ms=request.duration_ms,
                    trailing_gap_ms=request.duration_ms,
                    incomplete_range_count=0,
                )
            ),
        )

    monkeypatch.setattr(
        dub_subtitles.asr_pipeline.DEFAULT_ASR_PIPELINE,
        "run_raw_asr",
        fake_raw,
    )

    with pytest.raises(AppException) as raised:
        dub_subtitles.transcribe_track(
            step_contracts.DubSubtitleTranscribeTrackInput(
                audio=prepared.audio,
                engine_id="qwen3-asr-mlx",
            ),
            audio_path=artifact_path,
        )

    assert call_count == 1
    assert raised.value.code == "VIDEO_LOCALIZATION_DUB_SUBTITLES_EMPTY"


def test_align_words_rejects_non_acoustic_timing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    workflow_input = dub_subtitles.freeze_workflow_input(draft)
    artifact_path = tmp_path / "reject-interpolated-time.wav"
    prepared = dub_subtitles.prepare_track(
        workflow_input.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            workflow_input,
        ),
        artifact_id="test:reject-interpolated-time",
        artifact_path=artifact_path,
    )
    chunk = step_contracts.DubSubtitleProofreadChunk(
        cue_id="chunk-1",
        audio_window_start_ms=0,
        audio_window_end_ms=1_000,
        raw_text="你好",
        text="你好",
    )
    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        lambda *_args, **_kwargs: (
            [
                VideoLocalizationAlignedWord(
                    word_id="word-1",
                    segment_id="chunk-1",
                    text="你",
                    start_ms=100,
                    end_ms=200,
                    timing_confidence="low",
                    timing_source="asr_segment_interpolation",
                )
            ],
            {"alignment_call_count": 1},
        ),
    )

    with pytest.raises(AppException) as exc:
        dub_subtitles.align_words(
            step_contracts.DubSubtitleAlignWordsInput(
                audio=prepared.audio,
                language="zh",
                chunks=[chunk],
            ),
            audio_path=artifact_path,
        )

    assert exc.value.code == (
        "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED"
    )


def test_align_words_reports_effective_waveform_onsets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    workflow_input = dub_subtitles.freeze_workflow_input(draft)
    artifact_path = tmp_path / "effective-onset.wav"
    prepared = dub_subtitles.prepare_track(
        workflow_input.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            workflow_input,
        ),
        artifact_id="test:effective-onset",
        artifact_path=artifact_path,
    )
    chunk = step_contracts.DubSubtitleProofreadChunk(
        cue_id="chunk-1",
        audio_window_start_ms=0,
        audio_window_end_ms=1_000,
        raw_text="你好",
        text="你好",
    )
    words = [
        VideoLocalizationAlignedWord(
            word_id="word-1",
            segment_id="chunk-1",
            text="你",
            start_ms=100,
            end_ms=300,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word-2",
            segment_id="chunk-1",
            text="好",
            start_ms=300,
            end_ms=500,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
    ]
    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        lambda *_args, **_kwargs: (
            words,
            {"alignment_call_count": 1},
        ),
    )
    monkeypatch.setattr(
        subtitle_entry_timing,
        "detect_subtitle_entries",
        lambda _path, _words, *, frame_rate: (
            {"word-1": 170}
            if frame_rate == 24.0
            else {}
        ),
    )

    result = dub_subtitles.align_words(
        step_contracts.DubSubtitleAlignWordsInput(
            audio=prepared.audio,
            language="zh",
            chunks=[chunk],
            video_frame_rate=24.0,
        ),
        audio_path=artifact_path,
    )

    assert result.subtitle_entry_by_word_id == {"word-1": 170}


def test_segment_subtitles_starts_at_first_word_acoustic_entry():
    result = dub_subtitles.segment_subtitles(
        step_contracts.DubSubtitleSegmentSubtitlesInput(
            audio_sha256="a" * 64,
            chunks=[
                step_contracts.DubSubtitleSegmentTextChunk(
                    cue_id="chunk-1",
                    text="你好",
                )
            ],
            words=[
                step_contracts.DubSubtitleAlignedWord(
                    word_id="word-1",
                    cue_id="chunk-1",
                    text="你",
                    start_ms=100,
                    end_ms=300,
                ),
                step_contracts.DubSubtitleAlignedWord(
                    word_id="word-2",
                    cue_id="chunk-1",
                    text="好",
                    start_ms=300,
                    end_ms=500,
                ),
            ],
            subtitle_entry_by_word_id={"word-1": 170},
            clips=[
                step_contracts.DubSubtitleTimelineClip(
                    clip_id="clip-1",
                    dub_lane=0,
                    timeline_start_ms=0,
                    timeline_end_ms=1_000,
                    speaker_id="speaker-1",
                )
            ],
        )
    )

    assert result.subtitles[0].start_ms == 170
    assert "timing:acoustic-entry-refined" in result.subtitles[0].quality_flags


def test_segment_subtitles_split_short_replies_at_speaker_change():
    result = dub_subtitles.segment_subtitles(
        step_contracts.DubSubtitleSegmentSubtitlesInput(
            audio_sha256="a" * 64,
            chunks=[
                step_contracts.DubSubtitleSegmentTextChunk(
                    cue_id="chunk-1",
                    text="你好。没错。",
                )
            ],
            words=[
                step_contracts.DubSubtitleAlignedWord(
                    word_id="word-1",
                    cue_id="chunk-1",
                    text="你",
                    start_ms=100,
                    end_ms=200,
                ),
                step_contracts.DubSubtitleAlignedWord(
                    word_id="word-2",
                    cue_id="chunk-1",
                    text="好。",
                    start_ms=200,
                    end_ms=200,
                ),
                step_contracts.DubSubtitleAlignedWord(
                    word_id="word-3",
                    cue_id="chunk-1",
                    text="没",
                    start_ms=1_100,
                    end_ms=1_200,
                ),
                step_contracts.DubSubtitleAlignedWord(
                    word_id="word-4",
                    cue_id="chunk-1",
                    text="错。",
                    start_ms=1_200,
                    end_ms=1_400,
                ),
            ],
            clips=[
                step_contracts.DubSubtitleTimelineClip(
                    clip_id="clip-1",
                    dub_lane=0,
                    timeline_start_ms=0,
                    timeline_end_ms=1_000,
                    speaker_id="speaker-1",
                ),
                step_contracts.DubSubtitleTimelineClip(
                    clip_id="clip-2",
                    dub_lane=0,
                    timeline_start_ms=1_000,
                    timeline_end_ms=2_000,
                    speaker_id="speaker-2",
                ),
            ],
        )
    )

    assert [item.text for item in result.subtitles] == [
        "你好",
        "没错",
    ]
    assert [item.speaker_id for item in result.subtitles] == [
        "speaker-1",
        "speaker-2",
    ]
    assert all(not item.needs_review for item in result.subtitles)
    assert all(
        "speaker_metadata_missing" not in item.quality_flags
        for item in result.subtitles
    )


def test_zero_width_aligned_word_in_short_gap_inherits_nearest_speaker():
    speaker_id = dub_subtitles._speaker_for_aligned_word(
        VideoLocalizationAlignedWord(
            word_id="word-1",
            segment_id="chunk-1",
            text="美。",
            start_ms=1_035,
            end_ms=1_035,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        [
            step_contracts.DubSubtitleTimelineClip(
                clip_id="clip-1",
                dub_lane=0,
                timeline_start_ms=0,
                timeline_end_ms=1_000,
                speaker_id="speaker-1",
            )
        ],
    )

    assert speaker_id == "speaker-1"


def test_align_words_rejects_incomplete_acoustic_word_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    workflow_input = dub_subtitles.freeze_workflow_input(draft)
    artifact_path = tmp_path / "reject-incomplete-coverage.wav"
    prepared = dub_subtitles.prepare_track(
        workflow_input.prepare_track,
        audio_paths=dub_subtitles.resolve_prepare_audio_paths(
            draft,
            workflow_input,
        ),
        artifact_id="test:reject-incomplete-coverage",
        artifact_path=artifact_path,
    )
    chunk = step_contracts.DubSubtitleProofreadChunk(
        cue_id="chunk-1",
        audio_window_start_ms=0,
        audio_window_end_ms=1_000,
        raw_text="你好",
        text="你好",
    )
    monkeypatch.setattr(
        dub_subtitles.transcription,
        "align_segments_strict",
        lambda *_args, **_kwargs: (
            [
                VideoLocalizationAlignedWord(
                    word_id="word-1",
                    segment_id="chunk-1",
                    text="你",
                    start_ms=100,
                    end_ms=200,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                )
            ],
            {"alignment_call_count": 1},
        ),
    )

    with pytest.raises(AppException) as exc:
        dub_subtitles.align_words(
            step_contracts.DubSubtitleAlignWordsInput(
                audio=prepared.audio,
                language="zh",
                chunks=[chunk],
            ),
            audio_path=artifact_path,
        )

    assert exc.value.code == (
        "VIDEO_LOCALIZATION_DUB_SUBTITLE_ALIGNMENT_FAILED"
    )


def test_proofreading_keeps_corrected_text_in_original_asr_audio_chunks():
    cues = [
        VideoLocalizationCue(
            cue_id="asr-1",
            start_ms=10_000,
            end_ms=11_000,
            en_subtitle_text="分三集",
        ),
        VideoLocalizationCue(
            cue_id="asr-2",
            start_ms=11_200,
            end_ms=12_500,
            en_subtitle_text="下一段",
        ),
    ]

    corrected = dub_subtitles._proofread_asr_cues(
        cues,
        "分三级。下一段。",
    )

    assert [
        (item.start_ms, item.end_ms, item.en_subtitle_text)
        for item in corrected
    ] == [
        (10_000, 11_000, "分三级。"),
        (11_200, 12_500, "下一段。"),
    ]


def test_proofreading_assigns_boundary_insertion_to_following_audio_chunk():
    cues = [
        VideoLocalizationCue(
            cue_id="asr-1",
            start_ms=0,
            end_ms=1_000,
            en_subtitle_text="不会达到你想要的效果",
        ),
        VideoLocalizationCue(
            cue_id="asr-2",
            start_ms=1_000,
            end_ms=2_000,
            en_subtitle_text="Celines二点五现在可以用",
        ),
    ]

    corrected = dub_subtitles._proofread_asr_cues(
        cues,
        "不会达到你想要的效果Seedance二点五现在可以用",
    )

    assert [item.en_subtitle_text for item in corrected] == [
        "不会达到你想要的效果",
        "Seedance二点五现在可以用",
    ]


def test_proofreading_merges_small_audio_windows_for_cross_boundary_correction():
    cues = [
        VideoLocalizationCue(
            cue_id="asr-1",
            start_ms=0,
            end_ms=1_000,
            en_subtitle_text="甲错",
        ),
        VideoLocalizationCue(
            cue_id="asr-2",
            start_ms=1_000,
            end_ms=2_000,
            en_subtitle_text="误乙",
        ),
    ]

    corrected = dub_subtitles._proofread_asr_cues(
        cues,
        "甲正确乙",
    )

    assert len(corrected) == 1
    assert corrected[0].start_ms == 0
    assert corrected[0].end_ms == 2_000
    assert corrected[0].en_subtitle_text == "甲正确乙"


def test_document_proofreading_fixes_small_errors_without_inserting_unspoken_text():
    corrected, stats = dub_subtitles._proofread_document_text(
        "今天的效果分成三集，然后继续。",
        (
            "今天的效果分成三级，"
            "这是一整段没有在配音里说过的本土化文字，"
            "然后继续。"
        ),
    )

    assert "分成三级" in corrected
    assert "没有在配音里说过" not in corrected
    assert "然后继续" in corrected
    assert stats["unmatched_localized_char_count"] > 0


def test_document_proofreading_never_inserts_short_reference_only_words():
    corrected, stats = dub_subtitles._proofread_document_text(
        "今天继续",
        "今天不过继续",
    )

    assert corrected == "今天继续"
    assert "不过" not in corrected
    assert stats["unmatched_localized_char_count"] == 2


def test_document_proofreading_uses_localized_spelling_for_homophones_and_4k():
    corrected, stats = dub_subtitles._proofread_document_text(
        "今天的效果分成三集这是最干净的四 K画面",
        "今天的效果分成三级 这是最干净的 4K 画面",
    )

    assert "分成三级" in corrected
    assert "4K" in corrected
    assert "三集" not in corrected
    assert "四K" not in corrected
    assert stats["unmatched_localized_char_count"] == 0


def test_semantic_sentence_units_use_heard_clause_punctuation():
    assert dub_subtitles._semantic_sentence_units(
        "先说明结果，然后解释原因：补充背景——最后给出结论。"
    ) == [
        "先说明结果，",
        "然后解释原因：",
        "补充背景——",
        "最后给出结论。",
    ]


def test_short_semantic_part_merges_using_real_aligned_gap():
    parts = [
        (
            "很离谱，",
            [
                VideoLocalizationAlignedWord(
                    word_id="word-1",
                    segment_id="chunk-1",
                    text="很离谱，",
                    start_ms=26_080,
                    end_ms=26_640,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                )
            ],
        ),
        (
            "对吧？",
            [
                VideoLocalizationAlignedWord(
                    word_id="word-2",
                    segment_id="chunk-1",
                    text="对吧？",
                    start_ms=26_640,
                    end_ms=26_960,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                )
            ],
        ),
    ]

    merged = dub_subtitles._merge_short_semantic_parts(parts)

    assert len(merged) == 1
    assert merged[0][0] == "很离谱，对吧？"
    assert (
        merged[0][1][0].start_ms,
        merged[0][1][-1].end_ms,
    ) == (26_080, 26_960)


def test_segmentation_merges_parts_with_shared_acoustic_range():
    parts = [
        (
            "Claude，",
            [
                VideoLocalizationAlignedWord(
                    word_id="word-1",
                    segment_id="chunk-1",
                    text="Claude，",
                    start_ms=1_000,
                    end_ms=1_480,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                )
            ],
        ),
        (
            "Claude 给出。",
            [
                VideoLocalizationAlignedWord(
                    word_id="word-2",
                    segment_id="chunk-1",
                    text="Claude",
                    start_ms=1_000,
                    end_ms=1_480,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                ),
                VideoLocalizationAlignedWord(
                    word_id="word-3",
                    segment_id="chunk-1",
                    text="给出。",
                    start_ms=1_480,
                    end_ms=1_900,
                    timing_confidence="high",
                    timing_source="forced_aligner",
                ),
            ],
        ),
    ]

    merged = dub_subtitles._merge_acoustically_inseparable_parts(parts)

    assert len(merged) == 1
    assert merged[0][0] == "Claude，Claude 给出。"
    assert [word.word_id for word in merged[0][1]] == [
        "word-1",
        "word-2",
        "word-3",
    ]
    assert (
        merged[0][1][0].start_ms,
        merged[0][1][-1].end_ms,
    ) == (1_000, 1_900)


def test_dub_subtitles_use_shared_display_exit_timing_per_lane():
    cues = [
        VideoLocalizationCue(
            cue_id="cue-1",
            start_ms=0,
            end_ms=700,
            en_subtitle_text="第一条",
        ),
        VideoLocalizationCue(
            cue_id="cue-2",
            start_ms=1_000,
            end_ms=1_300,
            en_subtitle_text="第二条",
        ),
    ]
    clips = [
        step_contracts.DubSubtitleTimelineClip(
            clip_id="clip-1",
            dub_lane=0,
            timeline_start_ms=0,
            timeline_end_ms=2_000,
            speaker_id="speaker-1",
        )
    ]

    subtitles = dub_subtitles._candidate_subtitles(
        cues,
        clips=clips,
        source_audio_sha256="a" * 64,
        asr_requires_review=False,
        frame_rate=25.0,
        media_duration_ms=2_000,
    )

    assert [item.start_ms for item in subtitles] == [0, 1_000]
    assert [item.end_ms for item in subtitles] == [920, 1_800]
    assert all(
        "timing:display-exit-extended" in item.quality_flags
        for item in subtitles
    )


def test_dub_subtitle_cqc_records_automatic_screen_number_correction():
    subtitles = dub_subtitles._candidate_subtitles(
        [
            VideoLocalizationCue(
                cue_id="cue-1",
                start_ms=0,
                end_ms=1_000,
                en_subtitle_text=(
                    "这个二十世纪的模型是 Seedance 二点五"
                ),
            )
        ],
        clips=[
            step_contracts.DubSubtitleTimelineClip(
                clip_id="clip-1",
                dub_lane=0,
                timeline_start_ms=0,
                timeline_end_ms=1_500,
                speaker_id="speaker-1",
            )
        ],
        source_audio_sha256="a" * 64,
        asr_requires_review=False,
        frame_rate=25.0,
        media_duration_ms=1_500,
    )

    assert subtitles[0].text == "这个 20 世纪的模型是 Seedance 2.5"
    assert "display:number-form-normalized" in subtitles[0].quality_flags
    assert subtitles[0].needs_review is False


def test_candidate_subtitle_ignores_one_aligner_tick_of_neighbor_overlap():
    subtitles = dub_subtitles._candidate_subtitles(
        [
            VideoLocalizationCue(
                cue_id="cue-1",
                start_ms=100,
                end_ms=905,
                en_subtitle_text="同一个人说完这句话",
            )
        ],
        clips=[
            step_contracts.DubSubtitleTimelineClip(
                clip_id="clip-1",
                dub_lane=0,
                timeline_start_ms=0,
                timeline_end_ms=900,
                speaker_id="speaker-1",
            ),
            step_contracts.DubSubtitleTimelineClip(
                clip_id="clip-2",
                dub_lane=0,
                timeline_start_ms=900,
                timeline_end_ms=1_500,
                speaker_id="speaker-2",
            ),
        ],
        source_audio_sha256="a" * 64,
        asr_requires_review=False,
    )

    assert subtitles[0].source_clip_ids == ["clip-1"]
    assert subtitles[0].speaker_id == "speaker-1"
    assert subtitles[0].needs_review is False


def test_proofreading_keeps_asr_words_missing_from_localized_reference():
    cues = [
        VideoLocalizationCue(
            cue_id="asr-1",
            start_ms=0,
            end_ms=1_000,
            en_subtitle_text="你好",
        ),
        VideoLocalizationCue(
            cue_id="asr-2",
            start_ms=1_000,
            end_ms=2_000,
            en_subtitle_text="多余",
        ),
        VideoLocalizationCue(
            cue_id="asr-3",
            start_ms=2_000,
            end_ms=3_000,
            en_subtitle_text="再见",
        ),
    ]

    proofread_text, _stats = dub_subtitles._proofread_document_text(
        "你好多余再见",
        "你好。再见。",
    )
    corrected = dub_subtitles._proofread_asr_cues(
        cues,
        proofread_text,
    )

    assert [
        (item.start_ms, item.end_ms, item.en_subtitle_text)
        for item in corrected
    ] == [
        (0, 1_000, "你好"),
        (1_000, 2_000, "多余"),
        (2_000, 3_000, "再见"),
    ]


def test_document_proofreading_never_imports_reference_punctuation_into_name():
    corrected, _stats = dub_subtitles._proofread_document_text(
        "大家好，我是 Adele，我不是电影制作人",
        "大家好 我是 Adil。我不是电影制作人",
    )

    assert corrected == "大家好，我是 Adil，我不是电影制作人"
    assert "Adil。" not in corrected
    assert "Adil" in corrected


def test_document_proofreading_uses_ordered_reference_names_and_versions():
    corrected, _stats = dub_subtitles._proofread_document_text(
        "你可以体验 Silence 二点零",
        "你可以体验 Seedance 2.0",
    )

    assert corrected == "你可以体验 Seedance 2.0"


def test_document_proofreading_uses_reference_semantic_symbol():
    corrected, _stats = dub_subtitles._proofread_document_text(
        "给它一个以 ad 开头的名字",
        "给它一个以 @ 开头的名字",
    )

    assert corrected == "给它一个以 @ 开头的名字"


def test_proofreading_uses_generated_wording_and_canonical_display_forms():
    result = dub_subtitles.proofread_text(
        step_contracts.DubSubtitleProofreadTextInput(
            chunks=[
                step_contracts.DubSubtitleTranscriptChunk(
                    segment_id="chunk-1",
                    audio_window_start_ms=0,
                    audio_window_end_ms=2_000,
                    text=(
                        "这没法修不 使用 C Dream 五点零 Pro "
                        "角色约三十岁 分成五A"
                    ),
                )
            ],
            references=[
                step_contracts.DubSubtitleTextReference(
                    subtitle_id="generation-task:task-1",
                    text=(
                        "这没法修理 使用 Seedream 五点零 Pro "
                        "角色约三十岁 分成五 A"
                    ),
                    display_text=(
                        "这没法修不 使用 Seedream 5.0 Pro "
                        "角色约 30 岁 分成 5A"
                    ),
                )
            ],
        )
    )

    assert result.chunks[0].text == (
        "这没法修理 使用 Seedream 5.0 Pro 角色约30岁 分成5A"
    )


def test_proofreading_restores_single_digit_product_version_for_display():
    result = dub_subtitles.proofread_text(
        step_contracts.DubSubtitleProofreadTextInput(
            chunks=[
                step_contracts.DubSubtitleTranscriptChunk(
                    segment_id="chunk-1",
                    audio_window_start_ms=0,
                    audio_window_end_ms=2_000,
                    text="接着切换到 Nano Banana 二",
                )
            ],
            references=[
                step_contracts.DubSubtitleTextReference(
                    subtitle_id="generation-task:task-1",
                    text="接着切换到 Nano Banana 二",
                    display_text="接着切换到 Nano Banana 2",
                )
            ],
        )
    )

    assert result.chunks[0].text == "接着切换到 Nano Banana 2"


def test_proofreading_formats_same_pronunciation_numbers_and_units_for_display():
    spoken = (
        "最高时速一百二十公里每小时 容量五百毫升 "
        "增长百分之三点五"
    )
    result = dub_subtitles.proofread_text(
        step_contracts.DubSubtitleProofreadTextInput(
            chunks=[
                step_contracts.DubSubtitleTranscriptChunk(
                    segment_id="chunk-1",
                    audio_window_start_ms=0,
                    audio_window_end_ms=2_000,
                    text=spoken,
                )
            ],
            references=[
                step_contracts.DubSubtitleTextReference(
                    subtitle_id="generation-task:task-1",
                    text=spoken,
                    display_text=(
                        "最高时速 120 km/h 容量 500 mL 增长 3.5%"
                    ),
                )
            ],
        )
    )

    assert result.chunks[0].text == (
        "最高时速120 km/h 容量500 mL 增长3.5%"
    )


def test_proofreading_uses_chinese_unit_display_only_when_pronunciation_matches():
    matching = dub_subtitles._canonicalize_reference_display_forms(
        "时速一百二十公里每小时",
        [
            step_contracts.DubSubtitleTextReference(
                subtitle_id="generation-task:matching",
                text="时速一百二十公里每小时",
                display_text="时速 120 公里/小时",
            )
        ],
    )
    stale = dub_subtitles._canonicalize_reference_display_forms(
        "时速一百二十公里每小时",
        [
            step_contracts.DubSubtitleTextReference(
                subtitle_id="generation-task:stale",
                text="时速一百二十公里每小时",
                display_text="时速 100 km/h",
            )
        ],
    )

    assert matching == "时速120公里/小时"
    assert stale == "时速一百二十公里每小时"


def test_display_form_canonicalization_never_globally_rewrites_one_chinese_digit():
    result = dub_subtitles._canonicalize_reference_display_forms(
        "比如一九二九年的泡沫，过去每周六七十个小时。",
        [
            step_contracts.DubSubtitleTextReference(
                subtitle_id="year",
                text="一九二九年",
                display_text="1929 年",
            ),
            step_contracts.DubSubtitleTextReference(
                subtitle_id="rank",
                text="第二大",
                display_text="第 2 大",
            ),
            step_contracts.DubSubtitleTextReference(
                subtitle_id="hours",
                text="六七十个小时",
                display_text="六七十个小时",
            ),
        ],
    )

    assert "一九2九" not in result
    assert "六七10" not in result
    assert result.endswith("六七十个小时。")


@pytest.mark.parametrize(
    "asr_name",
    ["C Dance", "C Dams", "C-land"],
)
def test_document_proofreading_corrects_bounded_reference_name_variant(
    asr_name: str,
):
    corrected, _stats = dub_subtitles._proofread_document_text(
        f"然后把它放进 {asr_name} 看看结果",
        "然后把它放进 Seedance 看看结果",
    )

    assert corrected == "然后把它放进 Seedance 看看结果"


def test_proofreading_returns_unequal_length_name_to_its_original_audio_chunk():
    cues = [
        VideoLocalizationCue(
            cue_id="asr-1",
            start_ms=0,
            end_ms=1_000,
            en_subtitle_text="大家好我是 Adele",
        ),
        VideoLocalizationCue(
            cue_id="asr-2",
            start_ms=1_000,
            end_ms=2_000,
            en_subtitle_text="体验 Silence 二点零",
        ),
    ]
    raw_text = "".join(
        str(item.en_subtitle_text or "")
        for item in cues
    )
    corrected_text, _stats = (
        dub_subtitles._proofread_document_text(
            raw_text,
            "大家好我是 Adil 体验 Seedance 2.0",
        )
    )

    corrected = dub_subtitles._proofread_asr_cues(
        cues,
        corrected_text,
    )

    assert [
        item.en_subtitle_text for item in corrected
    ] == [
        "大家好我是 Adil",
        "体验 Seedance 2.0",
    ]


def test_source_revision_changes_when_lane_mute_or_reference_changes(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )
    muted = draft.model_copy(
        update={
            "ui_state": {
                "dub_lane_states": {
                    "0": {"muted": True},
                    "1": {"muted": False},
                }
            }
        }
    )
    changed_subtitles = list(draft.localized_subtitles)
    changed_subtitles[0] = changed_subtitles[0].model_copy(
        update={"text": "参考台词已修改。"}
    )

    with pytest.raises(AppException):
        dub_subtitles.ensure_prepare_source_unchanged(
            muted,
            frozen,
        )
    with pytest.raises(AppException):
        dub_subtitles.ensure_prepare_source_unchanged(
            draft.model_copy(
                update={
                    "localized_subtitles": changed_subtitles
                }
            ),
            frozen,
        )


def test_merge_replaces_only_independent_dub_subtitle_track(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )
    subtitle = (
        step_contracts.DubSubtitleCandidateCue(
            subtitle_id="dub-1",
            start_ms=100,
            end_ms=800,
            text="最终配音字幕",
            speaker_id="speaker-1",
            source_clip_ids=["clip-1"],
            dub_lanes=[0],
            source_audio_sha256=(
                frozen.prepare_track.clips[0].audio_sha256
            ),
        )
    )
    merged, committed = dub_subtitles.merge_generated_subtitles(
        draft,
        step_contracts.DubSubtitleCommitInput(
            source_revision=frozen.source_revision,
            subtitles=[subtitle],
            regeneration_scope=frozen.regeneration_scope,
        ),
    )

    assert [
        item.model_dump(mode="json")
        for item in merged.dub_subtitles
    ] == [subtitle.model_dump(mode="json")]
    assert (
        merged.dub_subtitle_source_revision
        == frozen.source_revision
    )
    assert merged.localized_subtitles == draft.localized_subtitles
    assert merged.cues == draft.cues
    assert merged.timeline_clips == draft.timeline_clips
    assert committed.saved_subtitle_count == 1


def test_merge_recovers_exact_cqc_clip_omission_and_marks_review(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    clips = [dict(item) for item in draft.timeline_clips]
    clips[1].update(
        {
            "dub_lane": 0,
            "status": "ready",
            "candidate_id": "candidate-2",
            "tts_target_text": "嗯？",
        }
    )
    candidates = [
        {
            "candidate_id": "candidate-2",
            "cqc_report": {
                "transcript": {
                    "expected_tokens": 1,
                    "matched_tokens": 1,
                    "missing_tokens": [],
                    "extra_tokens": [],
                    "reference_only_extra_tokens": [],
                    "coverage_ratio": 1.0,
                    "extra_ratio": 0.0,
                },
                "audio_evidence": {
                    "speech_start_ms": 100,
                    "speech_end_ms": 800,
                },
            },
        }
    ]
    cues = list(draft.cues)
    cues[1] = cues[1].model_copy(
        update={"speaker_id": "speaker-1"}
    )
    current = draft.model_copy(
        update={
            "timeline_clips": clips,
            "generated_candidates": candidates,
            "cues": cues,
        }
    )
    frozen = dub_subtitles.freeze_workflow_input(current)
    first = step_contracts.DubSubtitleCandidateCue(
        subtitle_id="dub-1",
        start_ms=100,
        end_ms=1_400,
        text="前一句",
        speaker_id="speaker-1",
        source_clip_ids=["clip-1"],
        dub_lanes=[0],
        source_audio_sha256=(
            frozen.prepare_track.clips[0].audio_sha256
        ),
        quality_flags=["timing:display-exit-extended"],
    )

    merged, committed = dub_subtitles.merge_generated_subtitles(
        current,
        step_contracts.DubSubtitleCommitInput(
            source_revision=frozen.source_revision,
            subtitles=[first],
            regeneration_scope=frozen.regeneration_scope,
        ),
    )

    assert committed.saved_subtitle_count == 2
    assert len(merged.dub_subtitles) == 2
    assert merged.dub_subtitles[0].end_ms == 1_300
    recovered = merged.dub_subtitles[1]
    assert (recovered.start_ms, recovered.end_ms) == (1_300, 2_000)
    assert recovered.text == "嗯"
    assert recovered.source_clip_ids == ["clip-2"]
    assert recovered.needs_review is True
    assert "recovered_from_exact_candidate_cqc" in (
        recovered.quality_flags
    )


def test_client_draft_cannot_clear_or_forge_dub_subtitles(
    tmp_path: Path,
):
    draft = _draft(tmp_path).model_copy(
        update={"updated_at": "2026-08-03T12:00:00"}
    )
    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )
    authoritative = (
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="dub-authoritative",
            start_ms=100,
            end_ms=800,
            text="服务端生成",
            source_clip_ids=["clip-1"],
            dub_lanes=[0],
            source_audio_sha256=(
                frozen.prepare_track.clips[0].audio_sha256
            ),
        )
    )
    forged = authoritative.model_copy(
        update={
            "subtitle_id": "dub-forged",
            "text": "客户端伪造",
        }
    )
    current = draft.model_copy(
        update={"dub_subtitles": [authoritative]}
    )

    cleared = service._preserve_backend_owned_draft_state(
        current,
        draft.model_copy(update={"dub_subtitles": []}),
    )
    forged_result = (
        service._preserve_backend_owned_draft_state(
            current,
            draft.model_copy(
                update={"dub_subtitles": [forged]}
            ),
        )
    )

    assert cleared.dub_subtitles == [authoritative]
    assert forged_result.dub_subtitles == [authoritative]


def test_derived_dub_subtitles_stay_visible_and_are_marked_stale_when_source_changes(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(
        draft,
    )
    authoritative = (
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="dub-authoritative",
            start_ms=100,
            end_ms=800,
            text="服务端生成",
            source_clip_ids=["clip-1"],
            dub_lanes=[0],
            source_audio_sha256=frozen.prepare_track.clips[0].audio_sha256,
        )
    )
    current = draft.model_copy(
        update={
            "dub_subtitles": [authoritative],
            "dub_subtitle_source_revision": frozen.source_revision,
        }
    )
    unrelated_ui_change = current.model_copy(
        update={"ui_state": {**current.ui_state, "timeline_zoom": 2}}
    )
    muted = current.model_copy(
        update={
            "ui_state": {
                **current.ui_state,
                "dub_lane_states": {
                    "0": {"muted": True},
                    "1": {"muted": False},
                },
            }
        }
    )

    assert (
        dub_subtitles.reconcile_source_lifecycle(
            current,
            unrelated_ui_change,
        ).dub_subtitles
        == [authoritative]
    )
    preserved = dub_subtitles.reconcile_source_lifecycle(
        current,
        muted,
    )
    assert len(preserved.dub_subtitles) == 1
    assert preserved.dub_subtitles[0].subtitle_id == "dub-authoritative"
    assert preserved.dub_subtitles[0].needs_review is True
    assert dub_subtitles.SOURCE_CHANGED_QUALITY_FLAG in (
        preserved.dub_subtitles[0].quality_flags
    )
    assert (
        preserved.dub_subtitle_source_revision
        == frozen.source_revision
    )

    preserved_again = dub_subtitles.reconcile_source_lifecycle(
        preserved,
        preserved.model_copy(
            update={"ui_state": {**muted.ui_state, "timeline_zoom": 3}}
        ),
    )
    assert preserved_again.dub_subtitles[0].quality_flags.count(
        dub_subtitles.SOURCE_CHANGED_QUALITY_FLAG
    ) == 1


def test_formal_dub_subtitle_submission_keeps_previous_track_until_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(draft)
    previous = dub_subtitles.VideoLocalizationDubSubtitleCue(
        subtitle_id="dub-previous",
        start_ms=100,
        end_ms=800,
        text="上一版字幕",
        source_clip_ids=["clip-1"],
        dub_lanes=[0],
        source_audio_sha256=frozen.prepare_track.clips[0].audio_sha256,
    )
    current = draft.model_copy(
        update={
            "dub_subtitles": [previous],
            "dub_subtitle_source_revision": frozen.source_revision,
        }
    )
    saved: list[VideoLocalizationDraft] = []
    monkeypatch.setattr(
        operation_queue.video_localization_operation_store,
        "project_revision",
        lambda _project_id: 0,
    )
    monkeypatch.setattr(
        operation_queue.project_store,
        "get_project",
        lambda _project_id: object(),
    )
    monkeypatch.setattr(
        operation_queue.service,
        "get_video_localization",
        lambda _project_id: current,
    )
    monkeypatch.setattr(
        operation_queue.service,
        "save_video_localization",
        lambda _project_id, next_draft, **_kwargs: saved.append(
            next_draft
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "_active_operation_for_kind",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)

    submitted = operation_queue.submit(
        "project-1",
        "dub_subtitle_generation",
        {"engine_id": "qwen3-asr-mlx"},
    )

    assert submitted is not None
    assert saved
    assert saved[-1].dub_subtitles == [previous]
    assert (
        saved[-1].dub_subtitle_source_revision
        == frozen.source_revision
    )


def test_reviewed_dub_subtitles_can_merge_contiguous_generated_cues(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(draft)
    audio_sha256 = frozen.prepare_track.clips[0].audio_sha256
    generated = [
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="dub-1",
            start_ms=100,
            end_ms=300,
            text="嚯",
            speaker_id="speaker-1",
            source_clip_ids=["clip-1"],
            dub_lanes=[0],
            source_audio_sha256=audio_sha256,
            quality_flags=["generated_by_dub_asr"],
        ),
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="dub-2",
            start_ms=500,
            end_ms=1_400,
            text="做得真不错！",
            speaker_id="speaker-1",
            source_clip_ids=["clip-1"],
            dub_lanes=[0],
            source_audio_sha256=audio_sha256,
            quality_flags=["generated_by_dub_asr"],
        ),
    ]
    current = draft.model_copy(
        update={
            "dub_subtitles": generated,
            "dub_subtitle_source_revision": frozen.source_revision,
        }
    )

    reviewed = dub_subtitles.apply_reviewed_subtitles(
        current,
        source_revision=frozen.source_revision,
        cues=[
            {
                "subtitle_id": "dub-1",
                "source_subtitle_ids": ["dub-1", "dub-2"],
                "start_ms": 100,
                "end_ms": 1_400,
                "text": "嚯，做得真不错！",
            }
        ],
    )

    assert len(reviewed.dub_subtitles) == 1
    assert reviewed.dub_subtitles[0].model_dump(mode="json") == {
        "subtitle_id": "dub-1",
        "start_ms": 100,
        "end_ms": 1_400,
        "text": "嚯，做得真不错！",
        "speaker_id": "speaker-1",
        "source_clip_ids": ["clip-1"],
        "dub_lanes": [0],
        "source_audio_sha256": audio_sha256,
        "needs_review": False,
        "quality_flags": [
            "generated_by_dub_asr",
            "readability:manual-review",
        ],
    }
    assert (
        reviewed.dub_subtitle_source_revision
        == frozen.source_revision
    )


def test_display_number_cqc_repairs_text_without_claiming_manual_review(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(draft)
    current = draft.model_copy(
        update={
            "dub_subtitles": [
                dub_subtitles.VideoLocalizationDubSubtitleCue(
                    subtitle_id="dub-1",
                    start_ms=100,
                    end_ms=1_000,
                    text="这个二十世纪的版本时长三十秒",
                    speaker_id="speaker-1",
                    source_clip_ids=["clip-1"],
                    dub_lanes=[0],
                    source_audio_sha256=(
                        frozen.prepare_track.clips[0].audio_sha256
                    ),
                    needs_review=True,
                    quality_flags=["asr_quality_warning"],
                )
            ],
            "dub_subtitle_source_revision": frozen.source_revision,
        }
    )

    normalized, changed_count = (
        dub_subtitles.normalize_display_number_forms(
            current,
            source_revision=frozen.source_revision,
        )
    )

    assert changed_count == 1
    assert normalized.dub_subtitles[0].text == (
        "这个 20 世纪的版本时长 30 秒"
    )
    assert normalized.dub_subtitles[0].needs_review is True
    assert normalized.dub_subtitles[0].quality_flags == [
        "asr_quality_warning",
        "display:number-form-normalized",
    ]
    assert "readability:manual-review" not in (
        normalized.dub_subtitles[0].quality_flags
    )


def test_display_number_cqc_does_not_rewrite_punctuation_only():
    draft = VideoLocalizationDraft(
        dub_subtitle_source_revision="a" * 64,
        dub_subtitles=[
            dub_subtitles.VideoLocalizationDubSubtitleCue(
                subtitle_id="dub-1",
                start_ms=0,
                end_ms=1_000,
                text="普通字幕，保持原样。",
                source_audio_sha256="b" * 64,
            )
        ],
    )

    normalized, changed_count = (
        dub_subtitles.normalize_display_number_forms(
            draft,
            source_revision="a" * 64,
        )
    )

    assert changed_count == 0
    assert normalized is draft


def test_reviewed_dub_subtitles_reject_stale_or_incomplete_partitions(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(draft)
    generated = [
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id=f"dub-{index}",
            start_ms=index * 500,
            end_ms=index * 500 + 400,
            text=f"字幕 {index}",
            source_clip_ids=["clip-1"],
            dub_lanes=[0],
            source_audio_sha256=frozen.prepare_track.clips[0].audio_sha256,
        )
        for index in (1, 2)
    ]
    current = draft.model_copy(
        update={
            "dub_subtitles": generated,
            "dub_subtitle_source_revision": frozen.source_revision,
        }
    )
    incomplete = [
        {
            "subtitle_id": "dub-1",
            "source_subtitle_ids": ["dub-1"],
            "start_ms": 500,
            "end_ms": 900,
            "text": "字幕 1",
        }
    ]

    with pytest.raises(AppException) as stale:
        dub_subtitles.apply_reviewed_subtitles(
            current,
            source_revision="0" * 64,
            cues=incomplete,
        )
    assert stale.value.code == (
        "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_STALE"
    )

    with pytest.raises(AppException) as missing:
        dub_subtitles.apply_reviewed_subtitles(
            current,
            source_revision=frozen.source_revision,
            cues=incomplete,
        )
    assert missing.value.code == (
        "VIDEO_LOCALIZATION_DUB_SUBTITLE_REVIEW_INCOMPLETE"
    )


@pytest.mark.parametrize("change", ["trim", "delete", "mute", "text", "solo", "insert"])
def test_source_edits_only_invalidate_related_dub_captions(tmp_path: Path, change: str):
    draft = _draft(tmp_path)
    captions = [
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id=f"dub-{index}", start_ms=start, end_ms=end,
            text=f"人工校对保留-{index}", source_clip_ids=[f"clip-{index}"],
            dub_lanes=[index - 1], source_audio_sha256="a" * 64,
        )
        for index, start, end in [(1, 100, 800), (2, 1200, 2000)]
    ]
    draft = draft.model_copy(update={"dub_subtitles": captions})
    if change == "trim":
        current = draft.model_copy(update={"timeline_clips": [
            {**draft.timeline_clips[0], "source_start_ms": 100, "end_ms": 950},
            draft.timeline_clips[1],
        ]})
    elif change == "delete":
        current = draft.model_copy(update={"timeline_clips": draft.timeline_clips[1:]})
    elif change in {"mute", "solo"}:
        current = draft.model_copy(update={"ui_state": {"dub_lane_states": {
            **draft.ui_state["dub_lane_states"],
            "0": {"muted": change == "mute", "solo": change == "solo"},
        }}})
    elif change == "text":
        current = draft.model_copy(update={"localized_subtitles": [
            draft.localized_subtitles[0].model_copy(update={"tts_text": "修改台词"}),
            draft.localized_subtitles[1],
        ]})
    else:
        current = draft.model_copy(update={"timeline_clips": [
            *draft.timeline_clips,
            {**draft.timeline_clips[0], "clip_id": "new-audio", "dub_lane": 1},
        ]})
    result = dub_subtitles.reconcile_source_lifecycle(draft, current)
    assert result.dub_subtitles[0].needs_review is (change != "solo")
    assert result.dub_subtitles[1] == captions[1]
    assert [item.text for item in result.dub_subtitles] == [item.text for item in captions]
    assert draft.dub_subtitles == captions


def test_incremental_freeze_renders_only_dirty_audio_and_keeps_full_fence(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(draft)
    captions = [
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id=f"dub-{index}",
            start_ms=start_ms,
            end_ms=end_ms,
            text=f"人工字幕-{index}",
            source_clip_ids=[f"clip-{index}"],
            dub_lanes=[index - 1],
            source_audio_sha256=frozen.prepare_track.clips[index - 1].audio_sha256,
        )
        for index, start_ms, end_ms in [(1, 100, 900), (2, 1_200, 2_000)]
    ]
    saved = draft.model_copy(
        update={
            "dub_subtitles": captions,
            "dub_subtitle_source_revision": frozen.source_revision,
        }
    )
    edited = saved.model_copy(
        update={
            "timeline_clips": [
                {**saved.timeline_clips[0], "source_start_ms": 100},
                saved.timeline_clips[1],
            ]
        }
    )
    dirty = dub_subtitles.reconcile_source_lifecycle(saved, edited)

    selected = dub_subtitles.freeze_workflow_input(dirty)

    assert selected.regeneration_scope.mode == "incremental"
    assert selected.regeneration_scope.reason == "dirty_scope"
    assert [item.clip_id for item in selected.prepare_track.clips] == [
        "clip-1"
    ]
    assert [item.subtitle_id for item in selected.references] == [
        "localized-1"
    ]
    assert selected.source_revision != frozen.source_revision
    assert selected.regeneration_scope.replaceable_subtitle_fingerprints == {
        "dub-1": dub_subtitles._caption_fingerprint(dirty.dub_subtitles[0])
    }
    full = dub_subtitles.freeze_workflow_input(
        dirty,
        regeneration_mode="full",
    )
    assert full.regeneration_scope.mode == "full"
    assert full.regeneration_scope.reason == "explicit_full"
    assert [item.clip_id for item in full.prepare_track.clips] == [
        "clip-1",
        "clip-2",
    ]


def test_incremental_commit_preserves_outside_and_during_job_manual_captions(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    initial = dub_subtitles.freeze_workflow_input(draft)
    captions = [
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id=f"dub-{index}",
            start_ms=start_ms,
            end_ms=end_ms,
            text=f"人工字幕-{index}",
            source_clip_ids=[f"clip-{index}"],
            dub_lanes=[index - 1],
            source_audio_sha256=initial.prepare_track.clips[index - 1].audio_sha256,
        )
        for index, start_ms, end_ms in [(1, 100, 900), (2, 1_200, 2_000)]
    ]
    saved = draft.model_copy(
        update={
            "dub_subtitles": captions,
            "dub_subtitle_source_revision": initial.source_revision,
        }
    )
    changed = dub_subtitles.reconcile_source_lifecycle(
        saved,
        saved.model_copy(
            update={
                "timeline_clips": [
                    {**saved.timeline_clips[0], "source_start_ms": 100},
                    saved.timeline_clips[1],
                ]
            }
        ),
    )
    request_input = dub_subtitles.freeze_workflow_input(changed)
    generated = step_contracts.DubSubtitleCandidateCue(
        subtitle_id="new-dub-1",
        start_ms=120,
        end_ms=850,
        text="重新识别的字幕",
        source_clip_ids=["clip-1"],
        dub_lanes=[0],
        source_audio_sha256=request_input.prepare_track.clips[0].audio_sha256,
    )

    merged, output = dub_subtitles.merge_generated_subtitles(
        changed,
        step_contracts.DubSubtitleCommitInput(
            source_revision=request_input.source_revision,
            subtitles=[generated],
            regeneration_scope=request_input.regeneration_scope,
        ),
    )
    assert [item.subtitle_id for item in merged.dub_subtitles] == [
        "new-dub-1",
        "dub-2",
    ]
    assert output.generated_subtitle_count == 1
    assert output.preserved_subtitle_count == 1
    assert merged.dub_subtitle_dirty_scope is None

    manually_edited = changed.model_copy(
        update={
            "dub_subtitles": [
                changed.dub_subtitles[0].model_copy(
                    update={"text": "作业期间人工改过"}
                ),
                changed.dub_subtitles[1],
            ]
        }
    )
    preserved, output = dub_subtitles.merge_generated_subtitles(
        manually_edited,
        step_contracts.DubSubtitleCommitInput(
            source_revision=request_input.source_revision,
            subtitles=[generated],
            regeneration_scope=request_input.regeneration_scope,
        ),
    )
    assert [item.subtitle_id for item in preserved.dub_subtitles] == [
        "dub-1",
        "dub-2",
    ]
    assert preserved.dub_subtitles[0].text == "作业期间人工改过"
    assert output.generated_subtitle_count == 0
    assert output.preserved_manual_subtitle_count == 1


def test_incremental_scope_expands_a_joined_caption_to_all_audible_sources(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    initial = dub_subtitles.freeze_workflow_input(draft)
    joined = dub_subtitles.VideoLocalizationDubSubtitleCue(
        subtitle_id="joined",
        start_ms=100,
        end_ms=2_000,
        text="跨越静音间隔的合并字幕",
        source_clip_ids=["clip-1", "clip-2"],
        dub_lanes=[0, 1],
        source_audio_sha256=initial.prepare_track.clips[0].audio_sha256,
    )
    saved = draft.model_copy(
        update={
            "dub_subtitles": [joined],
            "dub_subtitle_source_revision": initial.source_revision,
        }
    )
    dirty = dub_subtitles.reconcile_source_lifecycle(
        saved,
        saved.model_copy(
            update={
                "timeline_clips": [
                    {**saved.timeline_clips[0], "source_start_ms": 100},
                    saved.timeline_clips[1],
                ]
            }
        ),
    )

    selected = dub_subtitles.freeze_workflow_input(dirty)

    assert selected.regeneration_scope.mode == "incremental"
    assert selected.regeneration_scope.affected_clip_ids == [
        "clip-1",
        "clip-2",
    ]
    assert [item.clip_id for item in selected.prepare_track.clips] == [
        "clip-1",
        "clip-2",
    ]


def test_incremental_scope_closes_transitive_joined_captions(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    initial = dub_subtitles.freeze_workflow_input(draft)
    captions = [
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="joined-a",
            start_ms=100,
            end_ms=1_400,
            text="跨片段一",
            source_clip_ids=["clip-1", "clip-2"],
            source_audio_sha256=initial.prepare_track.clips[0].audio_sha256,
        ),
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="joined-b",
            start_ms=1_200,
            end_ms=2_000,
            text="跨片段二",
            source_clip_ids=["clip-2", "clip-3"],
            source_audio_sha256=initial.prepare_track.clips[1].audio_sha256,
        ),
    ]
    third = {
        **draft.timeline_clips[1],
        "clip_id": "clip-3",
        "start_ms": 2_000,
        "end_ms": 2_400,
    }
    saved = draft.model_copy(update={
        "timeline_clips": [*draft.timeline_clips, third],
        "dub_subtitles": captions,
    })
    changed = dub_subtitles.reconcile_source_lifecycle(
        saved,
        saved.model_copy(update={"timeline_clips": [
            {**saved.timeline_clips[0], "source_start_ms": 100},
            saved.timeline_clips[1],
            saved.timeline_clips[2],
        ]}),
    )

    scope = changed.dub_subtitle_dirty_scope
    assert scope is not None
    assert scope.affected_clip_ids == ["clip-1", "clip-2", "clip-3"]
    assert set(scope.replaceable_subtitle_fingerprints) == {
        "joined-a", "joined-b",
    }


def test_later_source_edit_accumulates_dirty_scope_instead_of_clearing_it(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    initial = dub_subtitles.freeze_workflow_input(draft)
    captions = [
        dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id=f"dub-{index}",
            start_ms=start_ms,
            end_ms=end_ms,
            text=f"字幕-{index}",
            source_clip_ids=[f"clip-{index}"],
            source_audio_sha256=initial.prepare_track.clips[index - 1].audio_sha256,
        )
        for index, start_ms, end_ms in [(1, 100, 900), (2, 1_200, 2_000)]
    ]
    saved = draft.model_copy(update={"dub_subtitles": captions})
    first = dub_subtitles.reconcile_source_lifecycle(
        saved,
        saved.model_copy(
            update={
                "timeline_clips": [
                    {**saved.timeline_clips[0], "source_start_ms": 100},
                    saved.timeline_clips[1],
                ]
            }
        ),
    )
    second = dub_subtitles.reconcile_source_lifecycle(
        first,
        first.model_copy(
            update={
                "timeline_clips": [
                    first.timeline_clips[0],
                    {**first.timeline_clips[1], "source_start_ms": 100},
                ]
            }
        ),
    )

    assert second.dub_subtitle_dirty_scope.affected_clip_ids == [
        "clip-1",
        "clip-2",
    ]
    assert set(
        second.dub_subtitle_dirty_scope.replaceable_subtitle_fingerprints
    ) == {"dub-1", "dub-2"}
    assert [item.clip_id for item in dub_subtitles.freeze_workflow_input(
        second
    ).prepare_track.clips] == ["clip-1", "clip-2"]


def test_delete_only_incremental_scope_can_commit_without_preparing_audio(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
    initial = dub_subtitles.freeze_workflow_input(draft)
    caption = dub_subtitles.VideoLocalizationDubSubtitleCue(
        subtitle_id="dub-1",
        start_ms=100,
        end_ms=900,
        text="将被删除的配音字幕",
        source_clip_ids=["clip-1"],
        dub_lanes=[0],
        source_audio_sha256=initial.prepare_track.clips[0].audio_sha256,
    )
    saved = draft.model_copy(
        update={
            "dub_subtitles": [caption],
            "dub_subtitle_source_revision": initial.source_revision,
        }
    )
    deleted = dub_subtitles.reconcile_source_lifecycle(
        saved,
        saved.model_copy(update={"timeline_clips": []}),
    )

    workflow_input = dub_subtitles.freeze_workflow_input(deleted)
    assert workflow_input.prepare_track.clips == []
    assert workflow_input.references == []
    assert workflow_input.regeneration_scope.mode == "incremental"
    merged, output = dub_subtitles.merge_generated_subtitles(
        deleted,
        step_contracts.DubSubtitleCommitInput(
            source_revision=workflow_input.source_revision,
            subtitles=[],
            regeneration_scope=workflow_input.regeneration_scope,
        ),
    )
    assert merged.dub_subtitles == []
    assert output.saved_subtitle_count == 0


def test_auto_regeneration_of_current_captions_is_a_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft = _draft(tmp_path)
    frozen = dub_subtitles.freeze_workflow_input(draft)
    current = draft.model_copy(update={
        "dub_subtitles": [dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="dub-1",
            start_ms=100,
            end_ms=900,
            text="人工字幕",
            source_clip_ids=["clip-1"],
            source_audio_sha256=frozen.prepare_track.clips[0].audio_sha256,
        )],
        "dub_subtitle_source_revision": frozen.source_revision,
    })
    monkeypatch.setattr(service.project_store, "get_project", lambda _id: object())
    monkeypatch.setattr(service, "get_video_localization", lambda _id: current)
    monkeypatch.setattr(
        service.dub_subtitle_workflow,
        "DubSubtitleWorkflowExecution",
        lambda **_kwargs: pytest.fail("current captions must not start ASR"),
    )

    returned, summary = service.generate_dub_subtitles(
        "project-current",
        operation_id="operation-current",
    )

    assert returned is current
    assert summary["regeneration_mode"] == "none"
    assert summary["regeneration_reason"] == "already_current"


def test_scope_less_legacy_captions_require_explicit_full_regeneration(
    tmp_path: Path,
):
    draft = _draft(tmp_path).model_copy(update={
        "dub_subtitles": [dub_subtitles.VideoLocalizationDubSubtitleCue(
            subtitle_id="legacy-caption",
            start_ms=100,
            end_ms=900,
            text="人工字幕",
            source_clip_ids=["clip-1"],
            source_audio_sha256="a" * 64,
        )],
    })
    with pytest.raises(AppException) as raised:
        dub_subtitles.freeze_workflow_input(draft)
    assert raised.value.code == "VIDEO_LOCALIZATION_DUB_SUBTITLE_SCOPE_UNKNOWN"
    assert dub_subtitles.freeze_workflow_input(
        draft,
        regeneration_mode="full",
    ).regeneration_scope.reason == "explicit_full"
