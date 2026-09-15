from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.domains.video_localization import project_manifest
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.schemas.voice_studio import Project
from app.services import database
from app.services import video_localization_project_snapshot_store as store
from app.services.interprocess_lock import exclusive_file_lock


logger = logging.getLogger(__name__)
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def flush(project_id: str) -> bool:
    """Best-effort projection of the latest authoritative Project revision."""

    with project_lock(project_id):
        pending = store.load(project_id)
        if pending is None:
            return False
        attempted_at = datetime.now().isoformat(timespec="microseconds")
        try:
            project = Project(**json.loads(pending.project_data))
            project._repository_revision = (
                pending.project_repository_revision
            )
            raw_draft = project.parameters.get("video_localization")
            if not isinstance(raw_draft, dict):
                store.complete(
                    project_id,
                    target_repository_revision=(
                        pending.project_repository_revision
                    ),
                )
                return False
            draft = VideoLocalizationDraft(**raw_draft)
            project_manifest.write_project_snapshot(
                project,
                draft,
                create_autosave=pending.create_autosave,
            )
        except Exception as exc:
            store.record_failure(
                project_id,
                target_repository_revision=(
                    pending.target_repository_revision
                ),
                attempted_at=attempted_at,
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning(
                "Project snapshot projection remains pending for %s: %s",
                project_id,
                exc,
            )
            return False
        return store.complete(
            project_id,
            target_repository_revision=(
                pending.project_repository_revision
            ),
        )


def replay_pending(*, limit: int = 100) -> int:
    """Replay a bounded startup batch without blocking application startup."""

    completed = 0
    for project_id in store.pending_project_ids(limit=limit):
        if flush(project_id):
            completed += 1
    return completed


@contextmanager
def project_lock(project_id: str) -> Iterator[None]:
    with _thread_locks_guard:
        thread_lock = _thread_locks.setdefault(
            project_id,
            threading.Lock(),
        )
    with thread_lock:
        lock_root = Path(database.DB_PATH).parent / (
            ".project-snapshot-locks"
        )
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(
            project_id.encode("utf-8")
        ).hexdigest()
        with exclusive_file_lock(lock_root / f"{lock_name}.lock"):
            yield


__all__ = ["flush", "project_lock", "replay_pending"]
