from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import workflow_contracts  # noqa: E402
from app.schemas.video_localization_dub_subtitle_step import (  # noqa: E402
    DUB_SUBTITLE_ALIGN_WORDS_INPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_COMMIT_INPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_PREPARE_TRACK_INPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_PROOFREAD_TEXT_INPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_SEGMENT_SUBTITLES_INPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_TRANSCRIBE_TRACK_INPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION,
    DUB_SUBTITLE_WORKFLOW_SCHEMA_VERSION,
    DubSubtitleAlignedWord,
    DubSubtitleAlignWordsInput,
    DubSubtitleAlignWordsOutput,
    DubSubtitleCandidateCue,
    DubSubtitleCommitInput,
    DubSubtitleCommitOutput,
    DubSubtitlePreparedAudio,
    DubSubtitlePrepareTrackInput,
    DubSubtitlePrepareTrackOutput,
    DubSubtitleProofreadChunk,
    DubSubtitleProofreadTextInput,
    DubSubtitleProofreadTextOutput,
    DubSubtitleSegmentSubtitlesInput,
    DubSubtitleSegmentSubtitlesOutput,
    DubSubtitleSegmentTextChunk,
    DubSubtitleSourceClip,
    DubSubtitleTextCorrection,
    DubSubtitleTextReference,
    DubSubtitleTranscriptChunk,
    DubSubtitleTranscribeTrackInput,
    DubSubtitleTranscribeTrackOutput,
    DubSubtitleTimelineClip,
    DubSubtitleWorkflowInput,
)


def _contract_chain():
    clip = DubSubtitleSourceClip(
        clip_id="clip-1",
        dub_lane=0,
        audio_sha256="a" * 64,
        timeline_start_ms=12_000,
        timeline_end_ms=13_800,
        source_start_ms=0,
        source_end_ms=1_800,
        reference_ids=["subtitle-1"],
        speaker_id="speaker-1",
    )
    reference = DubSubtitleTextReference(
        subtitle_id="subtitle-1",
        text="今天的效果分成三级",
    )
    prepare_input = DubSubtitlePrepareTrackInput(
        timeline_duration_ms=30_000,
        clips=[clip],
    )
    workflow_input = DubSubtitleWorkflowInput(
        source_revision="b" * 64,
        video_frame_rate=24.0,
        prepare_track=prepare_input,
        references=[reference],
        regeneration_scope={
            "mode": "full",
            "reason": "no_prior_subtitles",
            "affected_clip_ids": [clip.clip_id],
        },
    )
    prepare_output = DubSubtitlePrepareTrackOutput(
        audio=DubSubtitlePreparedAudio(
            artifact_id="artifact-full-dub",
            audio_sha256="c" * 64,
            duration_ms=30_000,
        ),
        clip_count=1,
    )
    transcribe_input = DubSubtitleTranscribeTrackInput(
        audio=prepare_output.audio,
        engine_id="qwen3-asr-mlx",
        audible_clips=[clip],
    )
    raw_segment = DubSubtitleTranscriptChunk(
        segment_id="segment-1",
        audio_window_start_ms=12_000,
        audio_window_end_ms=14_000,
        text="今天的效果分成三集",
    )
    transcribe_output = DubSubtitleTranscribeTrackOutput(
        audio_sha256=prepare_output.audio.audio_sha256,
        engine_id=transcribe_input.engine_id,
        language="zh",
        chunks=[raw_segment],
        quality_status="passed",
    )
    proofread_input = DubSubtitleProofreadTextInput(
        chunks=transcribe_output.chunks,
        references=[reference],
    )
    proofread_cue = DubSubtitleProofreadChunk(
        cue_id="dub-asr-1",
        audio_window_start_ms=raw_segment.audio_window_start_ms,
        audio_window_end_ms=raw_segment.audio_window_end_ms,
        raw_text=raw_segment.text,
        text="今天的效果分成三级",
    )
    proofread_output = DubSubtitleProofreadTextOutput(
        chunks=[proofread_cue],
        corrections=[
            DubSubtitleTextCorrection(
                before="三集",
                after="三级",
                kind="changed",
            )
        ],
        unmatched_asr_char_count=0,
        unmatched_reference_char_count=0,
    )
    align_input = DubSubtitleAlignWordsInput(
        audio=prepare_output.audio,
        language=transcribe_output.language,
        chunks=proofread_output.chunks,
    )
    aligned_word = DubSubtitleAlignedWord(
        word_id="word-1",
        cue_id=proofread_cue.cue_id,
        text=proofread_cue.text,
        start_ms=12_420,
        end_ms=13_610,
    )
    align_output = DubSubtitleAlignWordsOutput(
        audio_sha256=prepare_output.audio.audio_sha256,
        words=[aligned_word],
        alignment_call_count=1,
    )
    zero_width_word = DubSubtitleAlignedWord(
        word_id="word-2",
        cue_id=proofread_cue.cue_id,
        text="字",
        start_ms=13_610,
        end_ms=13_610,
    )
    assert zero_width_word.start_ms == zero_width_word.end_ms
    candidate = DubSubtitleCandidateCue(
        subtitle_id="dub-subtitle-1",
        start_ms=aligned_word.start_ms,
        end_ms=aligned_word.end_ms,
        text=proofread_cue.text,
        speaker_id="speaker-1",
        source_clip_ids=["clip-1"],
        dub_lanes=[0],
        source_audio_sha256=prepare_output.audio.audio_sha256,
    )
    segment_input = DubSubtitleSegmentSubtitlesInput(
        audio_sha256=align_output.audio_sha256,
        asr_requires_review=False,
        video_duration_ms=prepare_input.timeline_duration_ms,
        video_frame_rate=workflow_input.video_frame_rate,
        chunks=[
            DubSubtitleSegmentTextChunk(
                cue_id=proofread_cue.cue_id,
                text=proofread_cue.text,
            )
        ],
        words=align_output.words,
        clips=[
            DubSubtitleTimelineClip(
                clip_id=clip.clip_id,
                dub_lane=clip.dub_lane,
                timeline_start_ms=clip.timeline_start_ms,
                timeline_end_ms=clip.timeline_end_ms,
                speaker_id=clip.speaker_id,
            )
        ],
    )
    segment_output = DubSubtitleSegmentSubtitlesOutput(
        subtitles=[candidate],
    )
    commit_input = DubSubtitleCommitInput(
        source_revision=workflow_input.source_revision,
        subtitles=segment_output.subtitles,
        regeneration_scope=workflow_input.regeneration_scope,
    )
    commit_output = DubSubtitleCommitOutput(
        saved_source_revision=workflow_input.source_revision,
        saved_subtitle_count=1,
    )
    return (
        prepare_input,
        prepare_output,
        transcribe_input,
        transcribe_output,
        proofread_input,
        proofread_output,
        align_input,
        align_output,
        segment_input,
        segment_output,
        commit_input,
        commit_output,
    )


def test_dub_subtitle_workflow_has_one_strictly_serial_six_step_definition():
    definition = workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION
    tasks = [
        task
        for stage in definition.stages
        for task in stage.atomic_tasks
    ]

    assert definition.schema_version == DUB_SUBTITLE_WORKFLOW_SCHEMA_VERSION
    assert definition.workflow_id == "dub-subtitle-generation"
    assert len(definition.stages) == 1
    assert [task.id for task in tasks] == [
        "prepare_track",
        "transcribe_track",
        "proofread_text",
        "align_words",
        "segment_subtitles",
        "commit",
    ]
    assert [task.order for task in tasks] == [10, 20, 30, 40, 50, 60]
    assert [task.execution for task in tasks] == ["serial"] * 6
    assert [task.optional for task in tasks] == [False] * 6
    assert [task.depends_on for task in tasks] == [
        [],
        ["prepare_track"],
        ["transcribe_track"],
        ["proofread_text"],
        ["align_words"],
        ["segment_subtitles"],
    ]
    assert [task.output_contract_version for task in tasks] == [
        DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION,
    ]
    assert (
        workflow_contracts.dub_subtitle_workflow_summary()
        == definition.model_dump(mode="json")
    )


def test_every_dub_subtitle_atomic_input_and_output_is_versioned_json():
    contracts = _contract_chain()

    expected_versions = [
        DUB_SUBTITLE_PREPARE_TRACK_INPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_PREPARE_TRACK_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_TRANSCRIBE_TRACK_INPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_TRANSCRIBE_TRACK_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_PROOFREAD_TEXT_INPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_PROOFREAD_TEXT_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_ALIGN_WORDS_INPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_ALIGN_WORDS_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_SEGMENT_SUBTITLES_INPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_SEGMENT_SUBTITLES_OUTPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_COMMIT_INPUT_SCHEMA_VERSION,
        DUB_SUBTITLE_COMMIT_OUTPUT_SCHEMA_VERSION,
    ]

    assert [item.schema_version for item in contracts] == expected_versions
    for item in contracts:
        restored = type(item).model_validate_json(item.model_dump_json())
        assert restored == item


def test_generation_reference_contract_has_text_but_no_timing_fields():
    schema_properties = DubSubtitleTextReference.model_json_schema()[
        "properties"
    ]

    assert set(schema_properties) == {
        "subtitle_id",
        "text",
        "display_text",
    }
    with pytest.raises(ValidationError):
        DubSubtitleTextReference.model_validate(
            {
                "subtitle_id": "subtitle-1",
                "text": "只校对文字",
                "start_ms": 0,
                "end_ms": 1_000,
            }
        )


def test_atomic_outputs_are_compact_and_asr_ranges_are_not_subtitle_times():
    (
        _prepare_input,
        prepare_output,
        _transcribe_input,
        transcribe_output,
        _proofread_input,
        proofread_output,
        _align_input,
        align_output,
        _segment_input,
        segment_output,
        _commit_input,
        commit_output,
    ) = _contract_chain()

    for output in (
        prepare_output,
        transcribe_output,
        proofread_output,
        align_output,
        segment_output,
        commit_output,
    ):
        assert "input" not in output.model_dump()
    assert (
        transcribe_output.chunks[0].timing_kind
        == "audio_chunk_window"
    )


def test_every_atomic_contract_rejects_unknown_top_level_fields():
    for contract in _contract_chain():
        payload = contract.model_dump(mode="json")
        payload["unexpected"] = "not-allowed"
        with pytest.raises(ValidationError):
            type(contract).model_validate(payload)


def test_legacy_speech_onset_field_migrates_to_subtitle_entry_field():
    payload = _contract_chain()[7].model_dump(mode="json")
    payload["speech_onset_by_word_id"] = {"word-1": 170}
    payload.pop("subtitle_entry_by_word_id", None)

    migrated = DubSubtitleAlignWordsOutput.model_validate(payload)

    assert migrated.subtitle_entry_by_word_id == {"word-1": 170}
    assert "speech_onset_by_word_id" not in migrated.model_dump(
        mode="json"
    )


def test_every_atomic_contract_has_only_its_declared_fields():
    expected_fields = [
        {"schema_version", "timeline_duration_ms", "clips"},
        {"schema_version", "audio", "clip_count"},
        {
            "schema_version",
            "audio",
            "engine_id",
            "requested_language",
            "audible_clips",
        },
        {
            "schema_version",
            "audio_sha256",
            "engine_id",
            "language",
            "chunks",
            "quality_status",
            "quality_flags",
        },
        {
            "schema_version",
            "chunks",
            "references",
        },
        {
            "schema_version",
            "chunks",
            "corrections",
            "unmatched_asr_char_count",
            "unmatched_reference_char_count",
        },
        {
            "schema_version",
            "audio",
            "language",
            "chunks",
            "video_frame_rate",
        },
        {
            "schema_version",
            "audio_sha256",
            "words",
            "subtitle_entry_by_word_id",
            "alignment_call_count",
        },
        {
            "schema_version",
            "audio_sha256",
            "asr_requires_review",
            "video_duration_ms",
            "video_frame_rate",
            "chunks",
            "words",
            "subtitle_entry_by_word_id",
            "clips",
        },
        {"schema_version", "subtitles"},
        {
            "schema_version",
            "source_revision",
            "subtitles",
            "regeneration_scope",
        },
        {
            "schema_version",
            "saved_source_revision",
            "saved_subtitle_count",
            "generated_subtitle_count",
            "preserved_subtitle_count",
            "preserved_manual_subtitle_count",
        },
    ]

    assert [
        set(type(contract).model_json_schema()["properties"])
        for contract in _contract_chain()
    ] == expected_fields


def test_segmentation_contract_cannot_receive_asr_compute_windows():
    schema = DubSubtitleSegmentSubtitlesInput.model_json_schema()
    chunk_schema = schema["$defs"]["DubSubtitleSegmentTextChunk"][
        "properties"
    ]

    assert set(chunk_schema) == {"cue_id", "text"}
    assert "audio_window_start_ms" not in str(schema)
    assert "audio_window_end_ms" not in str(schema)
