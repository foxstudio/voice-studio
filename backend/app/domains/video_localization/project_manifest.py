from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domains.video_localization import media_assets
from app.domains.video_localization.schemas import VideoLocalizationDraft, now_iso

AUTOSAVE_KEEP_LIMIT = 24
PROJECT_PATH_PREFIX = "project://"


def write_project_snapshot(project: Any, draft: VideoLocalizationDraft) -> dict[str, Any]:
    """Mirror the database-backed draft into the project media directory."""
    root = media_assets.project_video_localization_dir(project.project_id)
    directories = ensure_project_layout(project.project_id)
    saved_at = now_iso()
    draft_payload = draft.model_dump(mode="json")
    external_dependencies = _external_paths(draft_payload, root)
    manifest = {
        "schema_version": 1,
        "kind": "video_localization_project",
        "project_id": project.project_id,
        "project_name": project.name,
        "saved_at": saved_at,
        "storage": {
            "root": str(root),
            "directories": {key: str(path) for key, path in directories.items()},
            "primary_state": "voice_studio_db.projects.parameters.video_localization",
            "snapshot_file": str(root / "project.json"),
            "autosave_dir": str(directories["autosave"]),
            "portability": {
                "status": "portable" if not external_dependencies else "external_dependencies",
                "external_paths": external_dependencies,
            },
        },
        "draft": draft_payload,
    }
    portable_manifest = _encode_project_paths(manifest, root)
    _normalize_existing_autosaves(project.project_id, root, directories["autosave"])
    _write_json(root / "project.json", portable_manifest)
    _write_json(_autosave_path(directories["autosave"]), portable_manifest)
    _prune_autosaves(directories["autosave"], keep=AUTOSAVE_KEEP_LIMIT)
    return manifest


def read_project_snapshot(project_id: str) -> VideoLocalizationDraft | None:
    root = media_assets.project_video_localization_dir(project_id)
    primary = root / "project.json"
    candidates = [primary] if primary.exists() else []
    autosave_dir = root / "autosave"
    if autosave_dir.exists():
        candidates.extend(sorted(autosave_dir.glob("*-project.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    for path in candidates:
        try:
            payload = _decode_project_paths(json.loads(path.read_text(encoding="utf-8")), root)
            draft = payload.get("draft") if isinstance(payload, dict) else None
            if isinstance(draft, dict):
                return VideoLocalizationDraft(**media_assets.rebase_project_paths(project_id, draft))
        except (OSError, ValueError, TypeError):
            continue
    return None


def ensure_project_layout(project_id: str) -> dict[str, Path]:
    root = media_assets.project_video_localization_dir(project_id)
    directories = {
        "source": root / "source",
        "audio": root / "audio",
        "stems": root / "stems",
        "references": root / "references",
        "tts": root / "tts",
        "cue_source_audio": root / "cue-source-audio",
        "exports": root / "exports",
        "autosave": root / "autosave",
    }
    for path in directories.values():
        path.mkdir(parents=True, exist_ok=True)
    return directories


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _encode_project_paths(value: Any, root: Path) -> Any:
    if isinstance(value, str):
        path = Path(value)
        if not path.is_absolute():
            return value
        try:
            relative = path.relative_to(root)
        except ValueError:
            return value
        suffix = relative.as_posix()
        return f"{PROJECT_PATH_PREFIX}{suffix}" if suffix != "." else f"{PROJECT_PATH_PREFIX}."
    if isinstance(value, list):
        return [_encode_project_paths(item, root) for item in value]
    if isinstance(value, dict):
        return {key: _encode_project_paths(item, root) for key, item in value.items()}
    return value


def _decode_project_paths(value: Any, root: Path) -> Any:
    if isinstance(value, str) and value.startswith(PROJECT_PATH_PREFIX):
        relative = value[len(PROJECT_PATH_PREFIX):]
        return str(root if relative in {"", "."} else root / relative)
    if isinstance(value, list):
        return [_decode_project_paths(item, root) for item in value]
    if isinstance(value, dict):
        return {key: _decode_project_paths(item, root) for key, item in value.items()}
    return value


def _external_paths(value: Any, root: Path) -> list[str]:
    paths: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            path = Path(item)
            if not path.is_absolute():
                return
            try:
                path.relative_to(root)
            except ValueError:
                paths.add(str(path))
            return
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if isinstance(item, dict):
            for child in item.values():
                visit(child)

    visit(value)
    return sorted(paths)


def _normalize_existing_autosaves(project_id: str, root: Path, autosave_dir: Path) -> None:
    for path in autosave_dir.glob("*-project.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rebased = media_assets.rebase_project_paths(project_id, payload)
            portable = _encode_project_paths(rebased, root)
            if portable != payload:
                _write_json(path, portable)
        except (OSError, ValueError, TypeError):
            continue


def _autosave_path(autosave_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return autosave_dir / f"{stamp}-project.json"


def _prune_autosaves(autosave_dir: Path, *, keep: int) -> None:
    snapshots = sorted(autosave_dir.glob("*-project.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for snapshot in snapshots[keep:]:
        snapshot.unlink(missing_ok=True)
