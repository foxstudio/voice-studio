from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domains.video_localization.schemas import VideoLocalizationDraft


MediaAssetStatus = Literal[
    "unknown",
    "unconfigured",
    "available",
    "missing",
    "not_file",
    "unreadable",
]
ProjectPackageStatus = Literal[
    "unknown",
    "available",
    "missing",
    "invalid",
    "repair_required",
]
MediaRecoveryAction = Literal[
    "none",
    "rescan_project",
    "relink_source",
    "import_source",
    "extract_source_audio",
    "separate_stems",
]
SourceAudioCandidate = Literal["source_media", "original_stem"]
StemsStatus = Literal["unconfigured", "complete", "partial", "missing", "stale"]


class MediaCandidateHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: str
    status: MediaAssetStatus
    reason_code: str | None = None


class MediaAssetHealth(BaseModel):
    """Path-free public health for one logical project media asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset: Literal["source_video", "source_audio", "vocals", "background"]
    status: MediaAssetStatus
    resource_id: str | None = None
    revision: str | None = None
    reason_code: str | None = None
    recovery_action: MediaRecoveryAction = "none"


class SourceAudioHealth(MediaAssetHealth):
    asset: Literal["source_audio"] = "source_audio"
    selected_source: SourceAudioCandidate | None = None
    candidates: tuple[MediaCandidateHealth, ...] = ()


class ProjectMediaHealth(BaseModel):
    """Versioned read model shared by UI, readiness and operation checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["project-media-health-v1"] = (
        "project-media-health-v1"
    )
    package_status: ProjectPackageStatus = "unknown"
    source_video: MediaAssetHealth
    source_audio: SourceAudioHealth
    vocals: MediaAssetHealth
    background: MediaAssetHealth
    stems_status: StemsStatus


@dataclass(frozen=True)
class ResolvedProjectMediaPaths:
    source_video: Path | None
    source_audio: Path | None
    vocals: Path | None
    background: Path | None


@dataclass(frozen=True)
class ProjectMediaResolution:
    health: ProjectMediaHealth
    paths: ResolvedProjectMediaPaths


@dataclass(frozen=True)
class _CandidateResolution:
    status: MediaAssetStatus
    configured: bool
    path: Path | None
    reason_code: str | None
    revision: str | None


def inspect_project_media(
    draft: VideoLocalizationDraft,
    *,
    package_root: Path | None = None,
    package_status: ProjectPackageStatus | None = None,
) -> ProjectMediaResolution:
    """Resolve all authoritative project media roles from one draft snapshot.

    A configured path is not an available asset. Availability requires a
    readable regular file. Source audio is the only multi-candidate role and
    always selects the first *available* candidate, not merely the first
    configured string.
    """

    source_video_candidate = _inspect_candidate(
        draft.source_media.video_path,
        configured=bool(
            draft.source_media.video_path or draft.source_media.filename
        ),
        revision_scope="source_video",
        package_root=package_root,
    )
    source_audio_candidates = (
        (
            "source_media",
            _inspect_candidate(
                draft.source_media.audio_path,
                revision_scope="source_audio:source_media",
                package_root=package_root,
            ),
        ),
        (
            "original_stem",
            _inspect_candidate(
                draft.stems.original_audio_path,
                revision_scope="source_audio:original_stem",
                package_root=package_root,
            ),
        ),
    )
    selected_source_audio = next(
        (
            (candidate, resolution)
            for candidate, resolution in source_audio_candidates
            if resolution.status == "available"
        ),
        None,
    )
    source_audio_status = (
        selected_source_audio[1].status
        if selected_source_audio is not None
        else _combined_candidate_status(
            tuple(item for _, item in source_audio_candidates)
        )
    )
    source_audio_reason = (
        None
        if selected_source_audio is not None
        else _reason_for_status(source_audio_status)
    )

    vocals_candidate = _inspect_candidate(
        draft.stems.vocals_clean_path,
        revision_scope="vocals",
        package_root=package_root,
    )
    background_candidate = _inspect_candidate(
        draft.stems.background_path,
        revision_scope="background",
        package_root=package_root,
    )
    source_video_available = source_video_candidate.status == "available"
    source_audio_available = selected_source_audio is not None

    source_video_health = _asset_health(
        asset="source_video",
        candidate=source_video_candidate,
        recovery_action=(
            "none"
            if source_video_available
            else "relink_source"
            if source_video_candidate.configured
            else "import_source"
        ),
    )
    source_audio_health = SourceAudioHealth(
        status=source_audio_status,
        resource_id="source_audio" if source_audio_available else None,
        revision=(
            selected_source_audio[1].revision
            if selected_source_audio is not None
            else None
        ),
        reason_code=source_audio_reason,
        recovery_action=(
            "none"
            if source_audio_available
            else "extract_source_audio"
            if source_video_available
            else "relink_source"
        ),
        selected_source=(
            selected_source_audio[0]
            if selected_source_audio is not None
            else None
        ),
        candidates=tuple(
            MediaCandidateHealth(
                candidate=candidate,
                status=resolution.status,
                reason_code=resolution.reason_code,
            )
            for candidate, resolution in source_audio_candidates
        ),
    )
    vocals_health = _asset_health(
        asset="vocals",
        candidate=vocals_candidate,
        recovery_action=(
            "none"
            if vocals_candidate.status == "available"
            else "separate_stems"
            if source_audio_available
            else "extract_source_audio"
            if source_video_available
            else "relink_source"
        ),
    )
    background_health = _asset_health(
        asset="background",
        candidate=background_candidate,
        recovery_action=(
            "none"
            if background_candidate.status == "available"
            else "separate_stems"
            if source_audio_available
            else "extract_source_audio"
            if source_video_available
            else "relink_source"
        ),
    )
    stems_status = _stems_status(vocals_candidate, background_candidate)
    resolved_package_status = (
        package_status
        if package_status is not None
        else _inspect_package_status(package_root)
    )

    return ProjectMediaResolution(
        health=ProjectMediaHealth(
            package_status=resolved_package_status,
            source_video=source_video_health,
            source_audio=source_audio_health,
            vocals=vocals_health,
            background=background_health,
            stems_status=stems_status,
        ),
        paths=ResolvedProjectMediaPaths(
            source_video=source_video_candidate.path,
            source_audio=(
                selected_source_audio[1].path
                if selected_source_audio is not None
                else None
            ),
            vocals=vocals_candidate.path,
            background=background_candidate.path,
        ),
    )


def _inspect_candidate(
    value: str | None,
    *,
    configured: bool | None = None,
    revision_scope: str,
    package_root: Path | None = None,
) -> _CandidateResolution:
    configured = bool(str(value or "").strip()) if configured is None else configured
    if not value:
        status: MediaAssetStatus = "missing" if configured else "unconfigured"
        return _CandidateResolution(
            status=status,
            configured=configured,
            path=None,
            reason_code=_reason_for_status(status),
            revision=None,
        )
    try:
        path = Path(value).expanduser()
        if package_root is not None and not _is_within_managed_package(
            path,
            package_root,
        ):
            return _unavailable_candidate(
                "unreadable",
                configured=True,
                reason_code="outside_project_package",
            )
        if not path.exists():
            return _unavailable_candidate("missing", configured=True)
        if not path.is_file():
            return _unavailable_candidate("not_file", configured=True)
        if not os.access(path, os.R_OK):
            return _unavailable_candidate("unreadable", configured=True)
        resolved = path.resolve()
        stat = resolved.stat()
    except OSError:
        return _unavailable_candidate("unreadable", configured=True)
    return _CandidateResolution(
        status="available",
        configured=True,
        path=resolved,
        reason_code=None,
        revision=_asset_revision(
            revision_scope,
            resolved,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        ),
    )


def _unavailable_candidate(
    status: Literal["missing", "not_file", "unreadable"],
    *,
    configured: bool,
    reason_code: str | None = None,
) -> _CandidateResolution:
    return _CandidateResolution(
        status=status,
        configured=configured,
        path=None,
        reason_code=reason_code or _reason_for_status(status),
        revision=None,
    )


def _is_within_managed_package(
    path: Path,
    package_root: Path,
) -> bool:
    root_path = Path(package_root).expanduser()
    if root_path.is_symlink():
        return False
    root = root_path.resolve(strict=False)
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            return False
        parent = cursor.parent
        if parent == cursor:
            return False
        cursor = parent
    return True


def _asset_health(
    *,
    asset: Literal["source_video", "vocals", "background"],
    candidate: _CandidateResolution,
    recovery_action: MediaRecoveryAction,
) -> MediaAssetHealth:
    return MediaAssetHealth(
        asset=asset,
        status=candidate.status,
        resource_id=asset if candidate.status == "available" else None,
        revision=candidate.revision,
        reason_code=candidate.reason_code,
        recovery_action=recovery_action,
    )


def _combined_candidate_status(
    candidates: tuple[_CandidateResolution, ...],
) -> MediaAssetStatus:
    configured = tuple(item for item in candidates if item.configured)
    if not configured:
        return "unconfigured"
    if any(item.status == "unreadable" for item in configured):
        return "unreadable"
    if any(item.status == "not_file" for item in configured):
        return "not_file"
    if any(item.status == "missing" for item in configured):
        return "missing"
    return "unknown"


def _stems_status(
    vocals: _CandidateResolution,
    background: _CandidateResolution,
) -> StemsStatus:
    available_count = sum(
        item.status == "available" for item in (vocals, background)
    )
    if available_count == 2:
        return "complete"
    if available_count == 1:
        return "partial"
    if vocals.configured or background.configured:
        return "missing"
    return "unconfigured"


def _reason_for_status(status: MediaAssetStatus) -> str | None:
    return {
        "unknown": "media_status_unknown",
        "unconfigured": "media_not_configured",
        "available": None,
        "missing": "media_file_missing",
        "not_file": "media_path_not_file",
        "unreadable": "media_file_unreadable",
    }[status]


def _inspect_package_status(package_root: Path | None) -> ProjectPackageStatus:
    if package_root is None:
        return "unknown"
    try:
        if not package_root.exists():
            return "missing"
        if not package_root.is_dir():
            return "invalid"
        if not (package_root / "project.json").is_file():
            return "repair_required"
    except OSError:
        return "invalid"
    return "available"


def _asset_revision(
    scope: str,
    path: Path,
    *,
    size: int,
    mtime_ns: int,
) -> str:
    payload = f"{scope}\0{path}\0{size}\0{mtime_ns}".encode(
        "utf-8", errors="surrogatepass"
    )
    return hashlib.sha256(payload).hexdigest()[:24]
