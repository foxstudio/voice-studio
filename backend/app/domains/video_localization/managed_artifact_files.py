"""Managed project-package files for durable operation artifacts."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath

from app.domains.video_localization import media_assets
from app.services import durable_files
from app.schemas.video_localization_operation_artifact_storage import (
    ArtifactStorageKeys,
    ManagedArtifactStorageIntegrityError,
    ManagedArtifactStoragePathError,
)


ARTIFACT_STORAGE_BACKEND = "project_package"
ARTIFACT_ROOT = PurePosixPath("artifacts", "operations")
ARTIFACT_STAGING_ROOT = PurePosixPath(".artifact-staging")
_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MEDIA_EXTENSIONS = {
    "application/json": ".json",
    "text/plain": ".txt",
    "application/octet-stream": ".bin",
    "image/jpeg": ".jpg",
}


ManagedArtifactPathError = ManagedArtifactStoragePathError
ManagedArtifactIntegrityError = ManagedArtifactStorageIntegrityError


def build_storage_keys(
    *,
    operation_id: str,
    step_attempt_id: str,
    artifact_kind: str,
    artifact_key: str,
    artifact_id: str,
    media_type: str,
) -> ArtifactStorageKeys:
    operation = _safe_part(operation_id, "operation ID")
    step_attempt = _safe_part(
        step_attempt_id,
        "step attempt ID",
    )
    kind = _safe_part(artifact_kind, "artifact kind")
    key = _safe_part(artifact_key, "artifact key")
    identity = _safe_part(artifact_id, "artifact ID")
    try:
        extension = _MEDIA_EXTENSIONS[media_type]
    except KeyError as exc:
        raise ValueError("managed artifact media type is unsupported") from exc
    final = (
        ARTIFACT_ROOT
        / operation
        / step_attempt
        / kind
        / f"{key}-{identity}{extension}"
    )
    staging = ARTIFACT_STAGING_ROOT / f"{identity}.part"
    return ArtifactStorageKeys(
        storage_key=final.as_posix(),
        staging_key=staging.as_posix(),
    )


def write_staging_file(
    project_id: str,
    staging_key: str,
    content: bytes,
) -> None:
    root = _managed_project_root(project_id, create=True)
    destination = _resolve_relative_key(root, staging_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(root, destination.parent)
    created = False
    try:
        with destination.open("xb") as handle:
            created = True
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        durable_files.fsync_directory(destination.parent)
    except FileExistsError as exc:
        raise ManagedArtifactIntegrityError(
            "managed artifact staging file already exists"
        ) from exc
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def commit_staging_file(
    project_id: str,
    *,
    staging_key: str,
    storage_key: str,
    expected_size: int,
    expected_fingerprint: str,
) -> None:
    root = _managed_project_root(project_id, create=True)
    staging = _resolve_relative_key(root, staging_key)
    destination = _resolve_relative_key(root, storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(root, destination.parent)
    if destination.exists():
        _verify_file(
            destination,
            expected_size=expected_size,
            expected_fingerprint=expected_fingerprint,
        )
        if staging.exists():
            _verify_file(
                staging,
                expected_size=expected_size,
                expected_fingerprint=expected_fingerprint,
            )
            staging.unlink()
            durable_files.fsync_directory(staging.parent)
        return
    _verify_file(
        staging,
        expected_size=expected_size,
        expected_fingerprint=expected_fingerprint,
    )
    durable_files.durable_replace(staging, destination)
    _verify_file(
        destination,
        expected_size=expected_size,
        expected_fingerprint=expected_fingerprint,
    )


def read_verified_file(
    project_id: str,
    storage_key: str,
    *,
    expected_size: int,
    expected_fingerprint: str,
) -> bytes:
    root = _managed_project_root(project_id, create=False)
    path = _resolve_relative_key(root, storage_key)
    return _read_verified_content(
        path,
        expected_size=expected_size,
        expected_fingerprint=expected_fingerprint,
    )


def verified_file_path(
    project_id: str,
    storage_key: str,
    *,
    expected_size: int,
    expected_fingerprint: str,
) -> Path:
    """Verify and return one managed local path for media responses."""

    root = _managed_project_root(project_id, create=False)
    path = _resolve_relative_key(root, storage_key)
    _verify_file(
        path,
        expected_size=expected_size,
        expected_fingerprint=expected_fingerprint,
    )
    return path


def remove_staging_file(
    project_id: str,
    staging_key: str,
) -> None:
    try:
        root = _managed_project_root(project_id, create=False)
        path = _resolve_relative_key(root, staging_key)
        if path.is_symlink():
            raise ManagedArtifactPathError(
                "managed artifact staging path is a symlink"
            )
        path.unlink(missing_ok=True)
        if path.parent.is_dir():
            durable_files.fsync_directory(path.parent)
    except FileNotFoundError:
        return


def content_fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _managed_project_root(
    project_id: str,
    *,
    create: bool,
) -> Path:
    projects_root = media_assets.projects_root_dir()
    if projects_root.is_symlink():
        raise ManagedArtifactPathError(
            "managed projects root must not be a symlink"
        )
    if create:
        projects_root.mkdir(parents=True, exist_ok=True)
    elif not projects_root.is_dir():
        raise ManagedArtifactIntegrityError(
            "managed projects storage is missing"
        )
    project_root = (
        media_assets.ensure_project_video_localization_dir(project_id)
        if create
        else media_assets.project_video_localization_dir(project_id)
    )
    if project_root.is_symlink():
        raise ManagedArtifactPathError(
            "managed project root must not be a symlink"
        )
    if create:
        project_root.mkdir(parents=True, exist_ok=True)
    projects_resolved = projects_root.resolve()
    project_resolved = project_root.resolve(strict=False)
    try:
        project_resolved.relative_to(projects_resolved)
    except ValueError as exc:
        raise ManagedArtifactPathError(
            "managed project root leaves projects storage"
        ) from exc
    if project_resolved == projects_resolved:
        raise ManagedArtifactPathError(
            "managed project root cannot equal projects storage"
        )
    if not project_resolved.is_dir():
        raise ManagedArtifactIntegrityError(
            "managed project package is missing"
        )
    return project_resolved


def _resolve_relative_key(root: Path, key: str) -> Path:
    value = str(key or "")
    relative = PurePosixPath(value)
    raw_parts = value.split("/")
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ManagedArtifactPathError(
            "managed artifact key must be a safe relative path"
        )
    candidate = root.joinpath(*relative.parts)
    _reject_symlink_chain(root, candidate)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ManagedArtifactPathError(
            "managed artifact key leaves project root"
        ) from exc
    return resolved


def _reject_symlink_chain(root: Path, target: Path) -> None:
    current = root
    if current.is_symlink():
        raise ManagedArtifactPathError(
            "managed project root must not be a symlink"
        )
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ManagedArtifactPathError(
            "managed artifact path leaves project root"
        ) from exc
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ManagedArtifactPathError(
                "managed artifact path contains a symlink"
            )


def _verify_file(
    path: Path,
    *,
    expected_size: int,
    expected_fingerprint: str,
) -> None:
    if path.is_symlink():
        raise ManagedArtifactPathError(
            "managed artifact file must not be a symlink"
        )
    try:
        if not path.is_file():
            raise ManagedArtifactIntegrityError(
                "managed artifact file is missing"
            )
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise ManagedArtifactIntegrityError(
                "managed artifact size differs from metadata"
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_fingerprint:
            raise ManagedArtifactIntegrityError(
                "managed artifact fingerprint differs from metadata"
            )
    except ManagedArtifactIntegrityError:
        raise
    except OSError as exc:
        raise ManagedArtifactIntegrityError(
            "managed artifact file cannot be verified"
        ) from exc


def _read_verified_content(
    path: Path,
    *,
    expected_size: int,
    expected_fingerprint: str,
) -> bytes:
    if path.is_symlink():
        raise ManagedArtifactPathError(
            "managed artifact file must not be a symlink"
        )
    try:
        if not path.is_file():
            raise ManagedArtifactIntegrityError(
                "managed artifact file is missing"
            )
        content = path.read_bytes()
    except ManagedArtifactIntegrityError:
        raise
    except OSError as exc:
        raise ManagedArtifactIntegrityError(
            "managed artifact file cannot be read"
        ) from exc
    if len(content) != expected_size:
        raise ManagedArtifactIntegrityError(
            "managed artifact size differs from metadata"
        )
    if content_fingerprint(content) != expected_fingerprint:
        raise ManagedArtifactIntegrityError(
            "managed artifact fingerprint differs from metadata"
        )
    return content


def _safe_part(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_PART.fullmatch(normalized):
        raise ValueError(f"{label} contains unsafe characters")
    return normalized


__all__ = [
    "ARTIFACT_STORAGE_BACKEND",
    "ArtifactStorageKeys",
    "ManagedArtifactIntegrityError",
    "ManagedArtifactPathError",
    "build_storage_keys",
    "commit_staging_file",
    "content_fingerprint",
    "read_verified_file",
    "remove_staging_file",
    "write_staging_file",
]
