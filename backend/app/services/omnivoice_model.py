from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.errors import AppException
from app.services import settings_store
from app.services.interprocess_lock import exclusive_file_lock


ENGINE_ID = "omnivoice"
REPO_ID = "k2-fsa/OmniVoice"
REVISION = "18db15024ce4b7e15638be6ef0e283d99d282f39"
MODEL_LICENSE = "CC-BY-NC"
CODE_LICENSE = "Apache-2.0"
LICENSE_ACCEPTANCE_ID = "omnivoice-weights-cc-by-nc"
INSTALL_MANIFEST_FILENAME = "voice-studio-install.json"

# File metadata is pinned to REVISION and comes from the Hugging Face model API.
# Hashes are available for the LFS objects; fixed sizes protect the small files.
FILE_SPECS: dict[str, tuple[int, str | None]] = {
    "config.json": (2_238, None),
    "model.safetensors": (
        2_450_344_112,
        "730839316de585f4c8298ec0e1712efc10fb19c6fa4e36eb741cb8d51ebcf6aa",
    ),
    "tokenizer.json": (
        11_423_986,
        "408f669b7e2b045fdf54201d815bd364e6667dbd845115da81239c40bc6dcfd1",
    ),
    "tokenizer_config.json": (533, None),
    "audio_tokenizer/config.json": (2_531, None),
    "audio_tokenizer/model.safetensors": (
        805_665_628,
        "fe7c5e8785e0a05833e1bfc3e002ec7f55af21e306b2e7154a448c1f54ccfb0d",
    ),
    "audio_tokenizer/preprocessor_config.json": (206, None),
}
TOTAL_BYTES = sum(size for size, _sha256 in FILE_SPECS.values())

_state_lock = threading.RLock()
_install_thread: threading.Thread | None = None
_state: dict[str, Any] = {
    "state": "idle",
    "error": None,
}
_verified_files: dict[tuple[str, int, int, str], bool] = {}


def model_dir() -> Path:
    return settings_store.managed_model_path(ENGINE_ID)


def installation_status(*, verify_integrity: bool = True) -> dict[str, Any]:
    root = model_dir()
    with _state_lock:
        active = dict(_state)
    if active["state"] == "installing":
        state = "installing"
        installed = False
        integrity = "not_verified"
    else:
        installed, integrity = (
            _integrity_status(root)
            if verify_integrity
            else _fast_integrity_status(root)
        )
        if installed:
            state = "installed"
        elif active["state"] == "failed":
            state = "failed"
        elif root.exists():
            state = "incomplete"
        else:
            state = "not_installed"
    downloaded = _downloaded_bytes(root)
    return {
        "engine_id": ENGINE_ID,
        "installed": installed,
        "installation_status": state,
        "integrity": integrity,
        "progress": 1.0 if installed else min(0.99, downloaded / TOTAL_BYTES),
        "downloaded_bytes": downloaded,
        "total_bytes": TOTAL_BYTES,
        "size_bytes": downloaded,
        "error": active.get("error"),
        "preferred_path": str(root),
        "revision": REVISION,
    }


def start_install(accepted_license_id: str | None) -> dict[str, Any]:
    global _install_thread
    _require_license_acceptance(accepted_license_id)
    with _state_lock:
        if _install_thread is not None and _install_thread.is_alive():
            return installation_status()
        if installation_status()["installed"]:
            return installation_status()
        _install_thread = threading.Thread(
            target=_install_in_background,
            kwargs={"accepted_license_id": accepted_license_id},
            name="omnivoice-model-install",
            daemon=True,
        )
        _install_thread.start()
    return installation_status()


def _install_in_background(*, accepted_license_id: str) -> None:
    try:
        install_now(accepted_license_id)
    except Exception:
        # install_now records a bounded, user-safe error for polling clients.
        return


def install_now(
    accepted_license_id: str | None,
    *,
    downloader: Callable[..., str] | None = None,
) -> dict[str, Any]:
    _require_license_acceptance(accepted_license_id)
    root = model_dir()
    lock_path = root.parent / ".locks" / f"{ENGINE_ID}.lock"
    with _state_lock:
        _state.update(state="installing", error=None)
    try:
        with exclusive_file_lock(lock_path):
            if not is_complete_directory(root):
                root.mkdir(parents=True, exist_ok=True)
                (downloader or _snapshot_download)(
                    repo_id=REPO_ID,
                    revision=REVISION,
                    local_dir=str(root),
                    allow_patterns=list(FILE_SPECS),
                    max_workers=_download_workers(),
                )
            require_model_files(root)
            _write_install_manifest(root)
        with _state_lock:
            _state.update(state="installed", error=None)
        return installation_status()
    except Exception as exc:
        with _state_lock:
            _state.update(state="failed", error=str(exc)[:500])
        raise


def is_complete_directory(root: Path, *, verify_integrity: bool = True) -> bool:
    installed, _integrity = (
        _integrity_status(root)
        if verify_integrity
        else _fast_integrity_status(root)
    )
    return installed


def require_model_files(root: Path | None = None) -> Path:
    target = model_dir() if root is None else root
    missing = [relative for relative in FILE_SPECS if not (target / relative).is_file()]
    if missing:
        raise AppException(
            409,
            "OMNIVOICE_MODEL_NOT_INSTALLED",
            f"OmniVoice 模型不完整，缺少文件：{', '.join(missing)}",
        )
    for relative, (expected_size, expected_sha256) in FILE_SPECS.items():
        _verify_file(
            target / relative,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
    return target.resolve()


def _integrity_status(root: Path) -> tuple[bool, str]:
    if not root.exists():
        return False, "not_verified"
    try:
        require_model_files(root)
    except (AppException, OSError, RuntimeError):
        return False, "invalid"
    return True, "verified"


def _fast_integrity_status(root: Path) -> tuple[bool, str]:
    try:
        complete = all(
            (root / relative).is_file()
            and (root / relative).stat().st_size == expected_size
            for relative, (expected_size, _sha256) in FILE_SPECS.items()
        )
    except OSError:
        complete = False
    return (True, "size_verified") if complete else (False, "invalid")


def _require_license_acceptance(accepted_license_id: str | None) -> None:
    if accepted_license_id != LICENSE_ACCEPTANCE_ID:
        raise AppException(
            409,
            "MODEL_LICENSE_ACCEPTANCE_REQUIRED",
            "OmniVoice 预训练权重为 CC-BY-NC，仅限非商业用途；确认接受后才能下载。",
        )


def _snapshot_download(**kwargs: Any) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("缺少 huggingface_hub，无法下载 OmniVoice 模型") from exc
    return str(snapshot_download(**kwargs))


def _download_workers() -> int:
    raw = os.environ.get("VOICE_STUDIO_MODEL_DOWNLOAD_WORKERS", "4")
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 4


def _downloaded_bytes(root: Path) -> int:
    total = 0
    for relative, (expected_size, _sha256) in FILE_SPECS.items():
        path = root / relative
        try:
            total += min(path.stat().st_size, expected_size)
        except OSError:
            continue
    return total


def _verify_file(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str | None,
) -> None:
    stat = path.stat()
    if stat.st_size != expected_size:
        raise RuntimeError(
            f"文件大小校验失败：{path.name}（实际 {stat.st_size}，预期 {expected_size}）"
        )
    if expected_sha256 is None:
        return
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, expected_sha256)
    if _verified_files.get(key):
        return
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise RuntimeError(f"文件哈希校验失败：{path.name}")
    _verified_files[key] = True


def _write_install_manifest(root: Path) -> None:
    payload = {
        "schema_version": 1,
        "engine_id": ENGINE_ID,
        "repo_id": REPO_ID,
        "revision": REVISION,
        "model_license": MODEL_LICENSE,
        "code_license": CODE_LICENSE,
        "license_acceptance_id": LICENSE_ACCEPTANCE_ID,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            relative: {"size": size, "sha256": sha256}
            for relative, (size, sha256) in FILE_SPECS.items()
        },
    }
    destination = root / INSTALL_MANIFEST_FILENAME
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)
