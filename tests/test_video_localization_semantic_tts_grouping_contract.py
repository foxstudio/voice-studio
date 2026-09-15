from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from app.schemas.video_localization_llm_observability import (  # noqa: E402
    VideoLocalizationLlmCallRecord,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (  # noqa: E402
    SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION,
    SemanticTtsGroupingInputV2,
    SemanticTtsGroupingItemV1,
    SemanticTtsGroupingRoundArtifactV1,
    SemanticTtsGroupingRoundInputV1,
    parse_semantic_tts_grouping_round_artifact,
    semantic_tts_grouping_round_artifact_bytes,
    semantic_tts_grouping_round_input_fingerprint,
)


def _workflow_input() -> SemanticTtsGroupingInputV2:
    return SemanticTtsGroupingInputV2(
        profile_id="work",
        model_id="model-a",
        provider_protocol="openai_compatible",
        provider_endpoint_fingerprint="a" * 64,
        target_chars=60,
        max_chars=80,
        subtitles=[
            SemanticTtsGroupingItemV1(
                subtitle_id="localized-1",
                text="先介绍事情的背景。",
                speaker_id="speaker-a",
            ),
            SemanticTtsGroupingItemV1(
                subtitle_id="localized-2",
                text="接着说明具体做法。",
                speaker_id="speaker-a",
            ),
        ],
    )


def _call() -> VideoLocalizationLlmCallRecord:
    return VideoLocalizationLlmCallRecord(
        call_id="semantic-tts-grouping-1",
        purpose="semantic_tts_grouping",
        round_index=1,
        profile_id="work",
        model_id="model-a",
        provider_host="llm.example",
        request_chars=120,
        request_body_bytes=180,
        max_tokens=1200,
        timeout_seconds=180,
        reasoning_effort_requested=None,
        reasoning_control_applied=True,
        duration_ms=25,
        finish_reason="stop",
        prompt_tokens=80,
        completion_tokens=20,
        total_tokens=100,
        content_chars=50,
        reasoning_chars=0,
        response_id="response-1",
    )


def test_round_input_fingerprint_covers_prompt_profile_and_content() -> None:
    current = SemanticTtsGroupingRoundInputV1(
        workflow_input=_workflow_input(),
        round_index=1,
    )
    changed_text = current.model_copy(
        update={
            "workflow_input": current.workflow_input.model_copy(
                update={
                    "subtitles": [
                        current.workflow_input.subtitles[0].model_copy(
                            update={"text": "字幕已经变化。"}
                        ),
                        current.workflow_input.subtitles[1],
                    ]
                }
            )
        }
    )
    changed_model = current.model_copy(
        update={
            "workflow_input": current.workflow_input.model_copy(
                update={"model_id": "model-b"}
            )
        }
    )
    changed_speaker = current.model_copy(
        update={
            "workflow_input": current.workflow_input.model_copy(
                update={
                    "subtitles": [
                        current.workflow_input.subtitles[0].model_copy(
                            update={"speaker_id": "speaker-b"}
                        ),
                        current.workflow_input.subtitles[1],
                    ]
                }
            )
        }
    )
    changed_limits = current.model_copy(
        update={
            "workflow_input": current.workflow_input.model_copy(
                update={"target_chars": 70}
            )
        }
    )
    changed_endpoint = current.model_copy(
        update={
            "workflow_input": current.workflow_input.model_copy(
                update={
                    "provider_endpoint_fingerprint": "b" * 64
                }
            )
        }
    )
    retry = current.model_copy(
        update={
            "round_index": 2,
            "previous_validation_error": "每组只能包含连续字幕",
        }
    )

    fingerprints = {
        semantic_tts_grouping_round_input_fingerprint(value)
        for value in (
            current,
            changed_text,
            changed_model,
            changed_speaker,
            changed_limits,
            changed_endpoint,
            retry,
        )
    }

    assert len(fingerprints) == 7
    assert all(len(value) == 64 for value in fingerprints)


def test_round_input_rejects_duplicate_subtitle_ids_and_invalid_limits() -> None:
    current = _workflow_input()

    with pytest.raises(ValidationError, match="subtitle_id"):
        current.model_copy(
            update={
                "subtitles": [
                    current.subtitles[0],
                    current.subtitles[0],
                ]
            }
        ).model_validate(
            {
                **current.model_dump(mode="json"),
                "subtitles": [
                    current.subtitles[0].model_dump(mode="json"),
                    current.subtitles[0].model_dump(mode="json"),
                ],
            }
        )

    with pytest.raises(ValidationError, match="max_chars"):
        SemanticTtsGroupingInputV2(
            **{
                **current.model_dump(mode="json"),
                "target_chars": 100,
                "max_chars": 80,
            }
        )


def test_round_two_requires_bounded_previous_validation_error() -> None:
    current = _workflow_input()

    with pytest.raises(ValidationError, match="previous_validation_error"):
        SemanticTtsGroupingRoundInputV1(
            workflow_input=current,
            round_index=2,
        )
    with pytest.raises(ValidationError, match="previous_validation_error"):
        SemanticTtsGroupingRoundInputV1(
            workflow_input=current,
            round_index=1,
            previous_validation_error="不应存在",
        )


def test_round_artifact_has_canonical_bytes_and_strict_roundtrip() -> None:
    artifact = SemanticTtsGroupingRoundArtifactV1(
        round_index=1,
        groups=[["localized-1", "localized-2"]],
        llm_call=_call(),
    )

    encoded = semantic_tts_grouping_round_artifact_bytes(artifact)
    parsed = parse_semantic_tts_grouping_round_artifact(encoded)

    assert encoded.startswith(
        b'{"groups":[["localized-1","localized-2"]],'
    )
    assert parsed == artifact
    assert parsed.schema_version == (
        SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION
    )


def test_round_artifact_records_invalid_provider_shape_without_raw_payload() -> None:
    artifact = SemanticTtsGroupingRoundArtifactV1(
        round_index=1,
        response_shape="groups_not_array",
        groups=[],
        llm_call=_call(),
    )

    parsed = parse_semantic_tts_grouping_round_artifact(
        semantic_tts_grouping_round_artifact_bytes(artifact)
    )

    assert parsed.response_shape == "groups_not_array"
    assert parsed.groups == []


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b'{"schema_version":"semantic-tts-grouping-round-v2"}',
        b'{"schema_version":"semantic-tts-grouping-round-v1","round_index":1,"groups":[],"llm_call":{},"extra":true}',
    ],
)
def test_round_artifact_rejects_invalid_or_future_payload(
    payload: bytes,
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        parse_semantic_tts_grouping_round_artifact(payload)
