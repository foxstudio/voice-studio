from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domains.video_localization import (
    asr_visual_evidence_extraction_gateway as extraction_gateway,
    managed_artifact_files,
    media_assets,
    visual_evidence,
)
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.voice_studio import AppSettings
from app.services import database, settings_store
from app.services import (
    video_localization_operation_attempt_store as attempt_store,
)
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


T0 = datetime.now(timezone.utc)
JPEG = b"\xff\xd8\xff\xe0managed-visual-frame\xff\xd9"


def _claim(tmp_path: Path):
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            voice_dir=str(tmp_path / "voices"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    with database.conn() as connection:
        connection.execute(
            """
            INSERT INTO video_localization_operations (
                project_id,
                operation_id,
                kind,
                status,
                parameters_fingerprint,
                workflow_version,
                origin,
                created_at,
                updated_at
            ) VALUES (
                'project-1',
                'operation-1',
                'english_asr',
                'running',
                'parameters-v1',
                ?,
                'command',
                ?,
                ?
            )
            """,
            (
                ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
                T0.isoformat(),
                T0.isoformat(),
            ),
        )
    decision = attempt_store.claim_attempt(
        "project-1",
        "operation-1",
        runner_id="runner-1",
        observed_at=T0,
        lease_duration=timedelta(minutes=10),
    )
    assert decision.attempt.execution_fence is not None
    return decision.attempt.execution_fence


def _request(tmp_path: Path):
    frame_root = tmp_path / "runtime-frames"
    frame_root.mkdir()
    return visual_evidence.VisualEvidenceExtractionRequest(
        video_path=tmp_path / "source.mp4",
        frame_root=frame_root,
        question_id="visual_01",
        timestamps=(1_000, 2_000),
        start_frame_index=1,
        round_index=1,
        frame_rate=30,
        first_success_only=True,
        is_cancelled=None,
    )


def test_extraction_commits_jpeg_and_replays_without_extractor(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    request = _request(tmp_path)
    extractor_calls = 0
    committed: list[
        extraction_gateway.CommittedVisualExtraction
    ] = []

    def extract(
        value: visual_evidence.VisualEvidenceExtractionRequest,
    ) -> visual_evidence.VisualEvidenceExtractionBatch:
        nonlocal extractor_calls
        extractor_calls += 1
        destination = value.frame_root / "ephemeral.jpg"
        destination.write_bytes(JPEG)
        return visual_evidence.VisualEvidenceExtractionBatch(
            frames=(
                visual_evidence.AsrVisualEvidenceFrame(
                    frame_id="frame-a",
                    question_id=value.question_id,
                    frame_index=1,
                    round_index=value.round_index,
                    timestamp_ms=1_000,
                    file_name=destination.name,
                    sha256=hashlib.sha256(JPEG).hexdigest(),
                    size_bytes=len(JPEG),
                ),
            ),
            warnings=(),
            attempted_timestamps=(1_000,),
        )

    gateway = extraction_gateway.ManagedVisualExtractionGateway(
        execution_fence=fence,
        video_sha256="a" * 64,
        behavior_fingerprint="b" * 64,
        extractor=extract,
        on_committed_extraction=committed.append,
        clock=lambda: T0 + timedelta(seconds=1),
    )

    first = gateway(request)
    (request.frame_root / "ephemeral.jpg").unlink()
    replay = gateway(request)

    assert first == replay
    assert extractor_calls == 1
    assert gateway.image_content(first.frames[0]) == JPEG
    assert len(committed) == 2
    assert committed[0] == committed[1]
    steps = step_store.list_step_attempts(
        "project-1",
        "operation-1",
    )
    assert len(steps) == 1
    assert steps[0].status == "success"
    frame_artifact = artifact_store.get_step_artifact(
        "project-1",
        "operation-1",
        steps[0].step_attempt_id,
        artifact_kind="visual-frame",
        artifact_key="frame-a",
    )
    assert frame_artifact is not None
    assert frame_artifact.media_type == "image/jpeg"
    assert (
        media_assets.project_video_localization_dir(
            "project-1"
        )
        / frame_artifact.storage_key
    ).is_file()


def test_extraction_fingerprint_rejects_candidate_change(
    tmp_path: Path,
) -> None:
    fence = _claim(tmp_path)
    request = _request(tmp_path)

    def extract(
        value: visual_evidence.VisualEvidenceExtractionRequest,
    ) -> visual_evidence.VisualEvidenceExtractionBatch:
        return visual_evidence.VisualEvidenceExtractionBatch(
            frames=(),
            warnings=("no frame",),
            attempted_timestamps=value.timestamps,
        )

    gateway = extraction_gateway.ManagedVisualExtractionGateway(
        execution_fence=fence,
        video_sha256="a" * 64,
        behavior_fingerprint="b" * 64,
        extractor=extract,
    )
    gateway(request)

    changed = visual_evidence.VisualEvidenceExtractionRequest(
        **{
            **request.__dict__,
            "timestamps": (3_000,),
        }
    )
    try:
        gateway(changed)
    except Exception as exc:
        assert getattr(exc, "code", "") == (
            "VIDEO_LOCALIZATION_ASR_VISUAL_EVIDENCE_"
            "EXTRACTION_INPUT_CHANGED"
        )
    else:  # pragma: no cover
        raise AssertionError("changed extraction input was accepted")
