from __future__ import annotations

import hashlib
import json
import shutil
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.errors import AppException
from app.services import settings_store


ENGINE_ID = "bs-roformer-viperx-1297"
DISPLAY_NAME = "BS-RoFormer 人声/背景声分离"
MODEL_FILENAME = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
CONFIG_FILENAME = "model_bs_roformer_ep_317_sdr_12.9755.yaml"
REGISTRY_FILENAME = "download_checks.json"
INSTALL_MANIFEST_FILENAME = "voice-studio-install.json"

MODEL_URL = (
    "https://github.com/TRvlvr/model_repo/releases/download/"
    f"all_public_uvr_models/{MODEL_FILENAME}"
)
CONFIG_URL = (
    "https://raw.githubusercontent.com/TRvlvr/application_data/main/"
    f"mdx_model_data/mdx_c_configs/{CONFIG_FILENAME}"
)
REGISTRY_URL = (
    "https://raw.githubusercontent.com/TRvlvr/application_data/main/"
    f"filelists/{REGISTRY_FILENAME}"
)
RUNTIME_URL = "https://github.com/nomadkaraoke/python-audio-separator"
MODEL_SOURCE_URL = "https://github.com/TRvlvr/model_repo"
MODEL_SHA256 = "5b84f37e8d444c8cb30c79d77f613a41c05868ff9c9ac6c7049c00aefae115aa"
MODEL_SIZE_BYTES = 639_331_213
CONFIG_SHA256 = "2bfdd16c656bd9519aba757cc4f8834b7ede675eb1e00ec4772d74ae1c41af7f"

_state_lock = threading.RLock()
_install_thread: threading.Thread | None = None
_state: dict[str, Any] = {
    "state": "idle",
    "progress": 0.0,
    "downloaded_bytes": 0,
    "total_bytes": MODEL_SIZE_BYTES,
    "error": None,
}
_verified_files: dict[tuple[str, int, int, str], bool] = {}


def model_dir() -> Path:
    return settings_store.managed_model_path(ENGINE_ID)


def model_paths() -> tuple[Path, Path, Path]:
    root = model_dir()
    return (
        root / MODEL_FILENAME,
        root / CONFIG_FILENAME,
        root / REGISTRY_FILENAME,
    )


def installation_status(*, verify_integrity: bool = True) -> dict[str, Any]:
    checkpoint, config, registry = model_paths()
    files = (checkpoint, config, registry)
    integrity = "not_verified"
    installed = False
    if all(path.is_file() for path in files) and _registry_contains_model(registry):
        try:
            if verify_integrity:
                _verify_file(
                    checkpoint,
                    expected_sha256=MODEL_SHA256,
                    expected_size=MODEL_SIZE_BYTES,
                )
                _verify_file(
                    config,
                    expected_sha256=CONFIG_SHA256,
                    expected_size=None,
                )
            elif checkpoint.stat().st_size != MODEL_SIZE_BYTES:
                raise RuntimeError("模型文件大小不符合预期")
            installed = True
            integrity = "verified" if verify_integrity else "size_verified"
        except (OSError, RuntimeError):
            integrity = "invalid"
    with _state_lock:
        active = dict(_state)
    if active["state"] == "installing":
        state = "installing"
    elif installed:
        state = "installed"
    elif any(path.exists() for path in files):
        state = "incomplete"
    elif active["state"] == "failed":
        state = "failed"
    else:
        state = "not_installed"
    size_bytes = sum(
        path.stat().st_size for path in files if path.is_file()
    )
    return {
        "engine_id": ENGINE_ID,
        "display_name": DISPLAY_NAME,
        "installed": installed,
        "installation_status": state,
        "integrity": integrity,
        "progress": float(active.get("progress") or 0.0),
        "downloaded_bytes": int(active.get("downloaded_bytes") or 0),
        "total_bytes": int(active.get("total_bytes") or MODEL_SIZE_BYTES),
        "size_bytes": size_bytes,
        "error": active.get("error"),
        "preferred_path": str(model_dir()),
    }


def start_install() -> dict[str, Any]:
    global _install_thread
    with _state_lock:
        if _install_thread is not None and _install_thread.is_alive():
            return installation_status()
        if installation_status()["installed"]:
            return installation_status()
        _install_thread = threading.Thread(
            target=_install_in_background,
            name="bs-roformer-model-install",
            daemon=True,
        )
        _install_thread.start()
    return installation_status()


def _install_in_background() -> None:
    try:
        install_now()
    except Exception:
        # install_now records a safe error for polling clients.
        return


def install_now() -> dict[str, Any]:
    root = model_dir()
    root.mkdir(parents=True, exist_ok=True)
    checkpoint, config, registry = model_paths()
    with _state_lock:
        _state.update(
            state="installing",
            progress=0.0,
            downloaded_bytes=0,
            total_bytes=MODEL_SIZE_BYTES,
            error=None,
        )

    downloaded_before = 0

    def progress(current: int, total: int) -> None:
        combined = downloaded_before + current
        expected = max(MODEL_SIZE_BYTES, downloaded_before + total)
        with _state_lock:
            _state.update(
                downloaded_bytes=combined,
                total_bytes=expected,
                progress=min(0.99, combined / max(1, expected)),
            )

    try:
        _ensure_download(
            MODEL_URL,
            checkpoint,
            expected_sha256=MODEL_SHA256,
            expected_size=MODEL_SIZE_BYTES,
            progress=progress,
        )
        downloaded_before = MODEL_SIZE_BYTES
        _ensure_download(
            CONFIG_URL,
            config,
            expected_sha256=CONFIG_SHA256,
            expected_size=None,
            progress=progress,
        )
        downloaded_before += config.stat().st_size
        _ensure_download(
            REGISTRY_URL,
            registry,
            expected_sha256=None,
            expected_size=None,
            progress=progress,
        )
        if not _registry_contains_model(registry):
            raise RuntimeError("模型官方清单与固定文件不匹配")
        _write_install_manifest(root)
        require_model_files()
        with _state_lock:
            size_bytes = sum(
                path.stat().st_size
                for path in (checkpoint, config, registry)
            )
            _state.update(
                state="installed",
                progress=1.0,
                downloaded_bytes=size_bytes,
                total_bytes=size_bytes,
                error=None,
            )
        return installation_status()
    except Exception as exc:
        for part in root.glob("*.part"):
            part.unlink(missing_ok=True)
        with _state_lock:
            _state.update(
                state="failed",
                error=str(exc)[:500],
            )
        raise


def require_model_files() -> tuple[Path, Path]:
    checkpoint, config, registry = model_paths()
    if not checkpoint.is_file() or not config.is_file() or not registry.is_file():
        raise AppException(
            409,
            "STEM_SEPARATION_MODEL_NOT_INSTALLED",
            "请先在引擎中心安装 BS-RoFormer 分离模型",
        )
    _verify_file(
        checkpoint,
        expected_sha256=MODEL_SHA256,
        expected_size=MODEL_SIZE_BYTES,
    )
    _verify_file(
        config,
        expected_sha256=CONFIG_SHA256,
        expected_size=None,
    )
    if not _registry_contains_model(registry):
        raise AppException(
            500,
            "STEM_SEPARATION_MODEL_INVALID",
            "BS-RoFormer 模型清单不完整，请重新安装模型",
        )
    return checkpoint, config


def uninstall() -> dict[str, Any]:
    global _install_thread
    with _state_lock:
        if _install_thread is not None and _install_thread.is_alive():
            raise AppException(
                409,
                "STEM_SEPARATION_MODEL_INSTALLING",
                "模型正在安装，暂时不能删除",
            )
    root = model_dir()
    if root.is_dir():
        shutil.rmtree(root)
    elif root.exists():
        root.unlink()
    with _state_lock:
        _state.update(
            state="idle",
            progress=0.0,
            downloaded_bytes=0,
            total_bytes=MODEL_SIZE_BYTES,
            error=None,
        )
    _verified_files.clear()
    return installation_status()


def _ensure_download(
    url: str,
    destination: Path,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
    progress: Callable[[int, int], None],
) -> None:
    if destination.is_file():
        try:
            _verify_file(
                destination,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )
            progress(destination.stat().st_size, destination.stat().st_size)
            return
        except (RuntimeError, AppException):
            destination.unlink(missing_ok=True)
    part = destination.with_name(f"{destination.name}.part")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            _download_file(url, part, progress)
            _verify_file(
                part,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )
            part.replace(destination)
            return
        except (RuntimeError, OSError) as exc:
            last_error = exc
            if (
                expected_size is None
                or not part.exists()
                or part.stat().st_size >= expected_size
            ):
                part.unlink(missing_ok=True)
            if attempt == 2:
                break
    if last_error is not None:
        raise RuntimeError(str(last_error)) from last_error
    raise RuntimeError(f"下载失败：{destination.name}")


def _download_file(
    url: str,
    destination: Path,
    progress: Callable[[int, int], None],
) -> None:
    existing = destination.stat().st_size if destination.is_file() else 0
    headers = {"User-Agent": "Voice-Studio/1.2 model-installer"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        partial = response.status == 206 and existing > 0
        response_size = int(response.headers.get("Content-Length") or 0)
        total = existing + response_size if partial else response_size
        downloaded = existing if partial else 0
        with destination.open("ab" if partial else "wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                progress(downloaded, total or downloaded)


def _verify_file(
    path: Path,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
) -> None:
    stat = path.stat()
    if expected_size is not None and stat.st_size != expected_size:
        raise RuntimeError(
            f"文件大小校验失败：{path.name}（实际 {stat.st_size}，预期 {expected_size}）"
        )
    if expected_sha256 is None:
        return
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, expected_sha256)
    if _verified_files.get(key):
        return
    actual = _sha256(path)
    if actual != expected_sha256:
        raise RuntimeError(f"SHA-256 校验失败：{path.name}")
    _verified_files[key] = True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _registry_contains_model(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for section in payload.values():
        if not isinstance(section, dict):
            continue
        for files in section.values():
            if not isinstance(files, dict):
                continue
            if files.get(MODEL_FILENAME) == CONFIG_FILENAME:
                return True
            if MODEL_FILENAME in files and CONFIG_FILENAME in files:
                return True
    return False


def _write_install_manifest(root: Path) -> None:
    payload = {
        "schema_version": "voice-studio-model-install-v1",
        "engine_id": ENGINE_ID,
        "model_filename": MODEL_FILENAME,
        "model_sha256": MODEL_SHA256,
        "model_url": MODEL_URL,
        "config_filename": CONFIG_FILENAME,
        "config_sha256": CONFIG_SHA256,
        "config_url": CONFIG_URL,
        "runtime": "audio-separator==0.44.2",
        "runtime_url": RUNTIME_URL,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / INSTALL_MANIFEST_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
