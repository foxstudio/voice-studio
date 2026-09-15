"""Neutral storage port for durable video-localization step artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ManagedArtifactStoragePathError(RuntimeError):
    """A managed artifact path is unsafe or leaves its project package."""


class ManagedArtifactStorageIntegrityError(RuntimeError):
    """A managed artifact file is missing or differs from its metadata."""


@dataclass(frozen=True)
class ArtifactStorageKeys:
    storage_key: str
    staging_key: str


class ManagedArtifactFileBackend(Protocol):
    """Filesystem capabilities required by the artifact metadata service."""

    ARTIFACT_STORAGE_BACKEND: str

    def build_storage_keys(
        self,
        *,
        operation_id: str,
        step_attempt_id: str,
        artifact_kind: str,
        artifact_key: str,
        artifact_id: str,
        media_type: str,
    ) -> ArtifactStorageKeys: ...

    def write_staging_file(
        self,
        project_id: str,
        staging_key: str,
        content: bytes,
    ) -> None: ...

    def commit_staging_file(
        self,
        project_id: str,
        *,
        staging_key: str,
        storage_key: str,
        expected_size: int,
        expected_fingerprint: str,
    ) -> None: ...

    def read_verified_file(
        self,
        project_id: str,
        storage_key: str,
        *,
        expected_size: int,
        expected_fingerprint: str,
    ) -> bytes: ...

    def remove_staging_file(
        self,
        project_id: str,
        staging_key: str,
    ) -> None: ...

    def content_fingerprint(self, content: bytes) -> str: ...


__all__ = [
    "ArtifactStorageKeys",
    "ManagedArtifactFileBackend",
    "ManagedArtifactStorageIntegrityError",
    "ManagedArtifactStoragePathError",
]
