from __future__ import annotations

import hashlib
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.domains.video_localization import (
    draft_store,
    export_filenames,
    exporting,
    quality_gate,
)
from app.domains.video_localization.export_contracts import (
    VideoLocalizationMediaExportRequest,
    validate_media_export_output_filename,
)
from app.domains.video_localization.export_filenames import (
    ExportFilenameSpec,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationExport,
)
from app.errors import AppException
from app.schemas.voice_studio import Project
from app.services import project_store

class VideoLocalizationExportApplicationService:
    """Public application boundary for read projections and render commands."""

    def __init__(self, *, lock_stripes: int = 32):
        self._render_locks = [
            threading.Lock()
            for _ in range(max(1, lock_stripes))
        ]

    def export_bundle(
        self,
        project_id: str,
    ) -> VideoLocalizationExport | None:
        context = self._project_draft(project_id)
        if context is None:
            return None
        project, draft = context
        return exporting.export_bundle(project.project_id, project.name, draft)

    def timeline_edl(self, project_id: str) -> dict | None:
        context = self._project_draft(project_id)
        if context is None:
            return None
        project, draft = context
        return exporting.timeline_edl(project.project_id, project.name, draft)

    def download_filename(
        self,
        project_id: str,
        spec: ExportFilenameSpec,
        *,
        artifact_path: Path | None = None,
        revision_source: str | bytes | None = None,
    ) -> str:
        project = project_store.get_project(project_id)
        project_name = project.name if project is not None else project_id
        exported_at = datetime.now().astimezone()
        revision_material: bytes
        if artifact_path is not None and artifact_path.is_file():
            stat = artifact_path.stat()
            exported_at = datetime.fromtimestamp(
                stat.st_mtime,
            ).astimezone()
            revision_material = (
                f"{artifact_path.name}:{stat.st_size}:{stat.st_mtime_ns}"
            ).encode("utf-8")
        elif isinstance(revision_source, bytes):
            revision_material = revision_source
        elif isinstance(revision_source, str):
            revision_material = revision_source.encode("utf-8")
        else:
            revision_material = exported_at.isoformat(
                timespec="microseconds",
            ).encode("utf-8")
        revision = hashlib.sha256(revision_material).hexdigest()[:6]
        return export_filenames.build_export_filename(
            project_name,
            spec,
            exported_at=exported_at,
            revision=revision,
        )

    def default_media_export_filename(
        self,
        project_id: str,
        request: VideoLocalizationMediaExportRequest,
    ) -> str | None:
        """Preview the basename used to initialize one export command."""

        context = self._project_draft(project_id)
        if context is None:
            return None
        _project, draft = context
        fingerprint = exporting.media_export_input_fingerprint(
            draft,
            request,
        )
        spec = export_filenames.media_export_filename_spec(
            request,
            source_width=draft.source_media.width,
            source_height=draft.source_media.height,
        )
        return self.download_filename(
            project_id,
            spec,
            revision_source=fingerprint,
        )

    def create_media_export_at(
        self,
        project_id: str,
        request: VideoLocalizationMediaExportRequest,
        destination_directory: Path,
        output_filename: str,
        *,
        on_progress: Callable[[float, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict | None:
        """Render one export directly into an authorized directory."""

        with self._render_lock(project_id):
            context = self._project_draft(project_id)
            if context is None:
                return None
            project, draft = context
            self._raise_delivery_blockers(project_id, draft, request)
            filename = validate_media_export_output_filename(
                output_filename,
                request,
            )
            fingerprint = exporting.media_export_input_fingerprint(
                draft,
                request,
            )
            destination = self._destination_path(
                destination_directory,
                filename,
            )
            partial_destination = destination.with_name(
                (
                    f".{destination.stem}.{uuid.uuid4().hex}"
                    f".partial{destination.suffix}"
                )
            )
            try:
                manifest = exporting.media_export_file(
                    project.project_id,
                    project.name,
                    draft,
                    request,
                    fingerprint=fingerprint,
                    destination_path=partial_destination,
                    on_progress=on_progress,
                    is_cancelled=is_cancelled,
                )
                with draft_store.DRAFT_WRITE_LOCK:
                    with project_store.publication_guard(
                        project_id
                    ) as locked_project:
                        if locked_project is None:
                            return None
                        current = draft_store.from_project(locked_project)
                        self._raise_delivery_blockers(
                            project_id,
                            current,
                            request,
                        )
                        if (
                            exporting.media_export_input_fingerprint(
                                current,
                                request,
                            )
                            != fingerprint
                        ):
                            raise AppException(
                                409,
                                "VIDEO_LOCALIZATION_EXPORT_INPUT_CHANGED",
                                (
                                    "导出期间项目内容发生了变化，"
                                    "请保存最新修改后重新导出。"
                                ),
                            )
                        if destination.exists():
                            raise AppException(
                                409,
                                "VIDEO_LOCALIZATION_EXPORT_FILENAME_CONFLICT",
                                (
                                    "保存目录中已存在同名文件，"
                                    "请修改文件名后重试。"
                                ),
                            )
                        os.replace(partial_destination, destination)
                return {
                    "filename": destination.name,
                    "size_bytes": destination.stat().st_size,
                    "kind": request.kind,
                    "mixed_track_count": len(
                        manifest.get("mixed_tracks") or []
                    ),
                }
            finally:
                partial_destination.unlink(missing_ok=True)

    def _raise_delivery_blockers(
        self,
        project_id: str,
        draft: VideoLocalizationDraft,
        request: VideoLocalizationMediaExportRequest,
    ) -> None:
        delivery_blockers = []
        if "asr" in request.subtitle_tracks:
            delivery_blockers.extend(
                quality_gate.subtitle_export_blockers(draft, "en")
            )
        if "localized" in request.subtitle_tracks:
            delivery_blockers.extend(
                (
                    quality_gate.dub_subtitle_export_blockers(
                        draft,
                    )
                    if request.localized_subtitle_variant == "dub"
                    else quality_gate.subtitle_export_blockers(
                        draft,
                        "zh",
                    )
                )
            )
        if not delivery_blockers:
            return
        unique_blockers = {
            (
                issue.code,
                issue.cue_id,
                issue.reference_clip_id,
                issue.message,
            ): issue
            for issue in delivery_blockers
        }
        blockers = list(unique_blockers.values())
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_MEDIA_EXPORT_BLOCKED",
            (
                "所选配音或字幕未通过交付检查，请先处理 "
                f"{len(blockers)} 个时间或内容问题。"
            ),
            {
                "issues": [
                    issue.model_dump(mode="json")
                    for issue in blockers
                ]
            },
        )

    @staticmethod
    def _destination_path(
        directory: Path,
        filename: str,
    ) -> Path:
        resolved_directory = directory.expanduser().resolve()
        if (
            not resolved_directory.is_dir()
            or not os.access(resolved_directory, os.W_OK)
        ):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_EXPORT_DESTINATION_UNAVAILABLE",
                "选择的保存目录不可用，请重新选择。",
            )
        candidate = resolved_directory / filename
        if candidate.exists():
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_EXPORT_FILENAME_CONFLICT",
                "保存目录中已存在同名文件，请修改文件名后重试。",
            )
        return candidate

    @staticmethod
    def _project_draft(
        project_id: str,
    ) -> tuple[Project, VideoLocalizationDraft] | None:
        project = project_store.get_project(project_id)
        if project is None:
            return None
        draft = draft_store.get(project_id)
        if draft is None:
            return None
        return project, draft

    def _render_lock(self, project_id: str) -> threading.Lock:
        digest = hashlib.sha256(project_id.encode("utf-8")).digest()
        return self._render_locks[
            int.from_bytes(digest[:2], "big")
            % len(self._render_locks)
        ]


video_localization_exports = VideoLocalizationExportApplicationService()
