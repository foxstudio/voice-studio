"""Managed local gateway for bounded visual frame extraction."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_visual_evidence_managed_contracts as managed_contracts,
    managed_artifact_files,
    managed_local_step,
    visual_evidence,
)
from app.errors import AppException
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)


ASR_VISUAL_FRAME_SCHEMA_VERSION = "asr-visual-frame-v1"
Extractor = Callable[
    [visual_evidence.VisualEvidenceExtractionRequest],
    visual_evidence.VisualEvidenceExtractionBatch,
]
CommittedExtractionSink = Callable[
    ["CommittedVisualExtraction"],
    None,
]
_ARTIFACT_KIND = "visual-frame"


@dataclass(frozen=True)
class CommittedVisualExtraction:
    reference: managed_contracts.AsrVisualEvidenceExtractionReferenceV1
    output: managed_contracts.AsrVisualEvidenceExtractionOutputV1


@dataclass
class ManagedVisualExtractionGateway:
    execution_fence: ExecutionFence
    video_sha256: str
    behavior_fingerprint: str
    file_backend: ManagedArtifactFileBackend = managed_artifact_files
    extractor: Extractor = visual_evidence._default_extraction_gateway
    on_committed_extraction: CommittedExtractionSink | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _image_content: dict[str, bytes] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _frame_references: dict[
        str,
        managed_contracts.AsrVisualEvidenceFrameReferenceV1,
    ] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        for label, value in (
            ("video", self.video_sha256),
            ("behavior", self.behavior_fingerprint),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(
                    f"visual extraction {label} fingerprint is invalid"
                )

    def __call__(
        self,
        request: visual_evidence.VisualEvidenceExtractionRequest,
    ) -> visual_evidence.VisualEvidenceExtractionBatch:
        spec = _step_spec(request)
        input_fingerprint = _input_fingerprint(
            request,
            video_sha256=self.video_sha256,
            behavior_fingerprint=self.behavior_fingerprint,
        )
        handle = managed_local_step.prepare_step(
            self.execution_fence,
            spec=spec,
            input_fingerprint=input_fingerprint,
        )
        if handle.prepared_status == "success":
            output = managed_local_step.read_success_output(handle)
        else:
            extracted = self.extractor(request)
            output = self._commit_frames(
                handle,
                request=request,
                extracted=extracted,
            )
            managed_local_step.complete_step(handle, output)
        batch = self._hydrate_output(
            output,
            step_attempt_id=handle.step_attempt_id,
        )
        if self.on_committed_extraction is not None:
            primary = artifact_store.get_step_artifact(
                self.execution_fence.project_id,
                self.execution_fence.operation_id,
                handle.step_attempt_id,
                artifact_kind=managed_local_step.ARTIFACT_KIND,
                artifact_key=managed_local_step.ARTIFACT_KEY,
            )
            if primary is None:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_VISUAL_EXTRACTION_ARTIFACT_MISSING",
                    "画面截图步骤缺少结果清单，请先运行任务详情审计。",
                )
            self.on_committed_extraction(
                CommittedVisualExtraction(
                    reference=(
                        managed_contracts
                        .AsrVisualEvidenceExtractionReferenceV1(
                            question_id=request.question_id,
                            round_index=request.round_index,
                            step_id=spec.step_id,
                            input_fingerprint=input_fingerprint,
                            artifact_fingerprint=(
                                primary.content_fingerprint
                            ),
                        )
                    ),
                    output=output,
                )
            )
        return batch

    def image_content(
        self,
        frame: visual_evidence.AsrVisualEvidenceFrame,
    ) -> bytes:
        try:
            return self._image_content[frame.frame_id]
        except KeyError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_FRAME_ARTIFACT_MISSING",
                "识图调用引用的截图不在当前任务快照中，请先运行任务详情审计。",
            ) from exc

    def frame_reference(
        self,
        frame: visual_evidence.AsrVisualEvidenceFrame,
    ) -> managed_contracts.AsrVisualEvidenceFrameReferenceV1:
        try:
            return self._frame_references[frame.frame_id]
        except KeyError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_FRAME_ARTIFACT_MISSING",
                "识图调用引用的截图不在当前任务快照中，请先运行任务详情审计。",
            ) from exc

    def _commit_frames(
        self,
        handle: managed_local_step.ManagedLocalStepHandle[
            managed_contracts.AsrVisualEvidenceExtractionOutputV1
        ],
        *,
        request: visual_evidence.VisualEvidenceExtractionRequest,
        extracted: visual_evidence.VisualEvidenceExtractionBatch,
    ) -> managed_contracts.AsrVisualEvidenceExtractionOutputV1:
        references: list[
            managed_contracts.AsrVisualEvidenceFrameReferenceV1
        ] = []
        for frame in extracted.frames:
            path = request.frame_root / frame.file_name
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise AppException(
                    500,
                    "VIDEO_LOCALIZATION_VISUAL_FRAME_READ_FAILED",
                    "画面截图已经生成，但无法读取并写入任务快照。",
                ) from exc
            fingerprint = hashlib.sha256(content).hexdigest()
            if (
                fingerprint != frame.sha256
                or len(content) != frame.size_bytes
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_VISUAL_FRAME_CHANGED",
                    "画面截图在写入任务快照前发生变化，本次任务不会标记成功。",
                )
            staged = artifact_store.stage_artifact(
                self.execution_fence,
                file_backend=self.file_backend,
                step_attempt_id=handle.step_attempt_id,
                artifact_kind=_ARTIFACT_KIND,
                artifact_key=frame.frame_id,
                payload_schema_version=(
                    ASR_VISUAL_FRAME_SCHEMA_VERSION
                ),
                media_type="image/jpeg",
                content=content,
                observed_at=self.clock(),
            )
            committed = artifact_store.commit_artifact(
                staged.artifact.artifact_id,
                file_backend=self.file_backend,
                execution_fence=self.execution_fence,
                observed_at=self.clock(),
            )
            references.append(
                managed_contracts
                .AsrVisualEvidenceFrameReferenceV1(
                    frame_id=frame.frame_id,
                    question_id=frame.question_id,
                    frame_index=frame.frame_index,
                    round_index=frame.round_index,
                    timestamp_ms=frame.timestamp_ms,
                    sha256=frame.sha256,
                    size_bytes=frame.size_bytes,
                    artifact_id=committed.artifact_id,
                    artifact_fingerprint=(
                        committed.content_fingerprint
                    ),
                )
            )
        return managed_contracts.AsrVisualEvidenceExtractionOutputV1(
            question_id=request.question_id,
            round_index=request.round_index,
            attempted_timestamps=extracted.attempted_timestamps,
            frames=tuple(references),
            warnings=extracted.warnings,
        )

    def _hydrate_output(
        self,
        output: managed_contracts.AsrVisualEvidenceExtractionOutputV1,
        *,
        step_attempt_id: str,
    ) -> visual_evidence.VisualEvidenceExtractionBatch:
        frames: list[visual_evidence.AsrVisualEvidenceFrame] = []
        for reference in output.frames:
            verified = artifact_store.read_artifact(
                reference.artifact_id,
                file_backend=self.file_backend,
            )
            artifact = verified.artifact
            if (
                artifact.project_id
                != self.execution_fence.project_id
                or artifact.operation_id
                != self.execution_fence.operation_id
                or artifact.step_attempt_id != step_attempt_id
                or artifact.artifact_kind != _ARTIFACT_KIND
                or artifact.artifact_key != reference.frame_id
                or artifact.payload_schema_version
                != ASR_VISUAL_FRAME_SCHEMA_VERSION
                or artifact.media_type != "image/jpeg"
                or artifact.content_fingerprint
                != reference.artifact_fingerprint
                or artifact.size_bytes != reference.size_bytes
            ):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_VISUAL_FRAME_ARTIFACT_INVALID",
                    "已保存的画面截图无法通过任务快照完整性校验，请先运行任务详情审计。",
                )
            self._image_content[reference.frame_id] = verified.content
            self._frame_references[reference.frame_id] = reference
            frames.append(
                visual_evidence.AsrVisualEvidenceFrame(
                    frame_id=reference.frame_id,
                    question_id=reference.question_id,
                    frame_index=reference.frame_index,
                    round_index=reference.round_index,
                    timestamp_ms=reference.timestamp_ms,
                    file_name=f"{reference.frame_id}.jpg",
                    sha256=reference.sha256,
                    size_bytes=reference.size_bytes,
                )
            )
        return visual_evidence.VisualEvidenceExtractionBatch(
            frames=tuple(frames),
            warnings=output.warnings,
            attempted_timestamps=output.attempted_timestamps,
        )


def _step_spec(
    request: visual_evidence.VisualEvidenceExtractionRequest,
) -> managed_local_step.ManagedLocalStepSpec[
    managed_contracts.AsrVisualEvidenceExtractionOutputV1
]:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        request.question_id.casefold(),
    ).strip("_")
    if not normalized:
        raise ValueError("visual extraction question ID is invalid")
    return managed_local_step.ManagedLocalStepSpec(
        kind="english_asr",
        workflow_version=(
            ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        step_id=(
            f"extract_visual_{normalized}_r{request.round_index}"
        ),
        output_schema_version=(
            managed_contracts
            .ASR_VISUAL_EVIDENCE_EXTRACTION_OUTPUT_SCHEMA_VERSION
        ),
        error_namespace="ASR_VISUAL_EVIDENCE_EXTRACTION",
        label="画面截图",
        serialize_output=managed_contracts.extraction_output_bytes,
        parse_output=managed_contracts.parse_extraction_output,
    )


def _input_fingerprint(
    request: visual_evidence.VisualEvidenceExtractionRequest,
    *,
    video_sha256: str,
    behavior_fingerprint: str,
) -> str:
    payload = {
        "schema_version": "asr-visual-extraction-input-v1",
        "video_sha256": video_sha256,
        "behavior_fingerprint": behavior_fingerprint,
        "question_id": request.question_id,
        "timestamps": request.timestamps,
        "start_frame_index": request.start_frame_index,
        "round_index": request.round_index,
        "frame_rate": request.frame_rate,
        "first_success_only": request.first_success_only,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ASR_VISUAL_FRAME_SCHEMA_VERSION",
    "CommittedVisualExtraction",
    "ManagedVisualExtractionGateway",
]
