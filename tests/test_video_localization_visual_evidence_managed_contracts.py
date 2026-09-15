from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.domains.video_localization import (
    asr_visual_evidence_managed_contracts as contracts,
    visual_evidence,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _request(
    *,
    questions: bool = True,
) -> visual_evidence.AsrVisualEvidenceInput:
    return visual_evidence.AsrVisualEvidenceInput(
        upstream_operation_id="document-op",
        video_sha256=SHA_A,
        video_duration_ms=30_000,
        profile_id="vision" if questions else None,
        document_summary="Summary",
        segments=[
            visual_evidence.AsrVisualEvidenceSegment(
                ordinal=1,
                segment_id="segment-1",
                start_ms=0,
                end_ms=2_000,
                text="hello",
            )
        ],
        questions=(
            [
                visual_evidence.AsrVisualEvidenceQuestion(
                    question_id="visual_01",
                    start_ordinal=1,
                    end_ordinal=1,
                    start_segment_id="segment-1",
                    end_segment_id="segment-1",
                    start_ms=0,
                    end_ms=2_000,
                    kind="visible_text",
                    reason="Need visible title",
                    question="What title is visible?",
                )
            ]
            if questions
            else []
        ),
    )


def _frame() -> contracts.AsrVisualEvidenceFrameReferenceV1:
    return contracts.AsrVisualEvidenceFrameReferenceV1(
        frame_id="visual_01_r1_f1",
        question_id="visual_01",
        frame_index=1,
        round_index=1,
        timestamp_ms=1_000,
        sha256=SHA_B,
        size_bytes=128,
        artifact_id="artifact-1",
        artifact_fingerprint=SHA_B,
    )


def test_prepared_input_requires_profile_only_when_questions_exist() -> None:
    prepared = contracts.AsrVisualEvidencePreparedInputV2(
        upstream_artifact_fingerprint=SHA_B,
        profile_configuration_fingerprint=SHA_C,
        behavior_fingerprint=SHA_A,
        request=_request(),
    )

    encoded = contracts.prepared_input_bytes(prepared)

    assert contracts.parse_prepared_input(encoded) == prepared
    assert len(contracts.prepared_input_fingerprint(prepared)) == 64
    no_work = contracts.AsrVisualEvidencePreparedInputV2(
        upstream_artifact_fingerprint=SHA_B,
        behavior_fingerprint=SHA_A,
        request=_request(questions=False),
    )
    assert no_work.profile_configuration_fingerprint is None

    with pytest.raises(ValidationError):
        contracts.AsrVisualEvidencePreparedInputV2(
            upstream_artifact_fingerprint=SHA_B,
            behavior_fingerprint=SHA_A,
            request=_request(),
        )


def test_extraction_output_is_path_free_and_validates_identity() -> None:
    output = contracts.AsrVisualEvidenceExtractionOutputV1(
        question_id="visual_01",
        round_index=1,
        attempted_timestamps=(1_000, 2_000),
        frames=(_frame(),),
    )

    encoded = contracts.extraction_output_bytes(output)

    assert contracts.parse_extraction_output(encoded) == output
    assert b"file_name" not in encoded
    with pytest.raises(ValidationError):
        output.model_copy(
            update={
                "frames": (
                    _frame().model_copy(
                        update={"question_id": "visual_02"}
                    ),
                )
            }
        ).model_validate(
            output.model_copy(
                update={
                    "frames": (
                        _frame().model_copy(
                            update={"question_id": "visual_02"}
                        ),
                    )
                }
            ).model_dump()
        )


def test_final_output_requires_exact_frame_and_call_counts() -> None:
    quality = visual_evidence.AsrVisualEvidenceQualitySummary(
        status="passed",
        question_count=1,
        answered_question_count=1,
        unresolved_question_count=0,
        failed_question_count=0,
        frame_count=1,
        model_call_count=1,
        source_text_unchanged=True,
    )
    output = contracts.AsrVisualEvidenceStepOutputV2(
        prepared_input_fingerprint=SHA_A,
        status="completed",
        stop_reason="completed",
        profile_id="vision",
        model_id="vision-model",
        frames=(_frame(),),
        observations=(
            visual_evidence.AsrVisualEvidenceObservation(
                question_id="visual_01",
                status="answered",
                answer="Title",
            ),
        ),
        duration_ms=42,
        quality_summary=quality,
        extractions=(
            contracts.AsrVisualEvidenceExtractionReferenceV1(
                question_id="visual_01",
                round_index=1,
                step_id="extract_visual_01_r1",
                input_fingerprint=SHA_A,
                artifact_fingerprint=SHA_B,
            ),
        ),
        calls=(
            contracts.AsrVisualEvidenceCallReferenceV1(
                call_id="visual_01_round_1",
                question_id="visual_01",
                round_index=1,
                step_id="visual_call_visual_01_r1",
                input_fingerprint=SHA_B,
                artifact_fingerprint=SHA_C,
            ),
        ),
    )

    encoded = contracts.step_output_bytes(output)

    assert contracts.parse_step_output(encoded) == output
    assert json.loads(encoded)["schema_version"] == (
        "asr-visual-evidence-step-output-v2"
    )
    with pytest.raises(ValidationError):
        contracts.AsrVisualEvidenceStepOutputV2(
            **{
                **output.model_dump(),
                "calls": (),
            }
        )
