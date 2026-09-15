from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from app.errors import AppException
from app.services import settings_store
from app.services.interprocess_lock import exclusive_file_lock
from app.services.paths import PROJECT_ROOT, expand_path


MODEL_FAMILY_ID = "vibevoice-asr"
TOKENIZER_REPO_ID = "Qwen/Qwen2.5-7B"
INSTALL_MANIFEST_FILENAME = "voice-studio-install.json"
MODELSCOPE_ENDPOINT = "https://modelscope.cn"
MODEL_LICENSE = "MIT"
CODE_LICENSE = "MIT"

VariantId = Literal["4bit", "8bit", "official"]


@dataclass(frozen=True, slots=True)
class FileSpec:
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class VariantSpec:
    variant_id: VariantId
    installation_id: str
    display_name: str
    repo_id: str
    revision: str
    directory_name: str
    runtime: Literal["mlx", "pytorch"]
    files: dict[str, FileSpec]
    recommended_for: str
    benchmark_note: str

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files.values())


TOKENIZER_FILES: dict[str, FileSpec] = {
    "config.json": FileSpec(686, "267ce68584c5f24c3b267d934db2de68dd21d1ca677fb78ed809eb60067f7642"),
    "generation_config.json": FileSpec(138, "8c970692323e3ea0e9b8b0a4dca79388d31226e41f83c9fd6014804280ebf6e8"),
    "merges.txt": FileSpec(1_671_839, "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"),
    "tokenizer.json": FileSpec(7_031_645, "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"),
    "tokenizer_config.json": FileSpec(7_228, "c91efca15ceff6e9ee9424db58a6f59cd41294e550a86cbd07e3c1fb500b34f9"),
    "vocab.json": FileSpec(2_776_833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
}


VARIANTS: dict[VariantId, VariantSpec] = {
    "4bit": VariantSpec(
        variant_id="4bit",
        installation_id="vibevoice-asr-4bit",
        display_name="VibeVoice ASR 4bit（推荐）",
        repo_id="mlx-community/VibeVoice-ASR-4bit",
        revision="78284b91f208fb466a9a5f4e74a53a7c7ac00918",
        directory_name="4bit",
        runtime="mlx",
        files={
            "config.json": FileSpec(4_372, "5ac3a43f61743b00d7e26e72aa26c04716033fa913ea2e55c9667a69d4c7ea2d"),
            "model-00001-of-00002.safetensors": FileSpec(
                5_366_951_429, "811dc8e5aa0f322f2b40778c2eee5437d5d8dde6e2133740e69ab2b9db7e319e"
            ),
            "model-00002-of-00002.safetensors": FileSpec(
                346_896_560, "45aab2d00ded3c502cbc1523ff46218cedccc0d798f0f363b9a6d2fe2dec3301"
            ),
            "model.safetensors.index.json": FileSpec(
                130_385, "92f16ddbaca2b010d9785533395dcffa406b2acf8f353b2fd46ce34f0e21920b"
            ),
        },
        recommended_for="Apple Silicon 24 GB 以上、长视频和多说话人转写",
        benchmark_note="本机 90 分钟样本约 628.7 秒，近似 WER 3.075%，峰值 RSS 6.49 GB。",
    ),
    "8bit": VariantSpec(
        variant_id="8bit",
        installation_id="vibevoice-asr-8bit",
        display_name="VibeVoice ASR 8bit",
        repo_id="mlx-community/VibeVoice-ASR-8bit",
        revision="8687406dd5a5500b66418061f3e412ad0802d1da",
        directory_name="8bit",
        runtime="mlx",
        files={
            "config.json": FileSpec(4_372, "f4418d57174253f52174c74d6dc3b53ae452d8234b2e007231bea53f2437f16a"),
            "model-00001-of-00002.safetensors": FileSpec(
                5_331_193_271, "ce6e064d50295cb0100f33af8c69c9d2a3d647a8d375f764851e940180308650"
            ),
            "model-00002-of-00002.safetensors": FileSpec(
                4_190_296_379, "53750f68f0fca138e70d8ed5eb38c29a02e3b44c3e530142a7b0cb3453bf455a"
            ),
            "model.safetensors.index.json": FileSpec(
                130_385, "8f282316181bcbd6bb4d7d57ce3e3c5601de35d9003b6bfe1da3a0a22a814a55"
            ),
        },
        recommended_for="Apple Silicon 32 GB 以上、希望保留更高量化精度",
        benchmark_note="本机 90 分钟样本约 790.5 秒，近似 WER 3.022%，峰值 RSS 10.29 GB。",
    ),
    "official": VariantSpec(
        variant_id="official",
        installation_id="vibevoice-asr-official",
        display_name="VibeVoice ASR 官方完整版",
        repo_id="microsoft/VibeVoice-ASR",
        revision="master",
        directory_name="official",
        runtime="pytorch",
        files={
            "config.json": FileSpec(3_520, "1798906d016a625ffa0100182cad152e055bfee53fb228a45ffe25d8179b9b24"),
            "configuration.json": FileSpec(86, "38a052799e44f23ab0eafcb31ffe9b05d9bca18d56c0a471cd99f788b9ffbbfa"),
            "model-00001-of-00008.safetensors": FileSpec(
                2_488_346_272, "5548c67885d423ba184bc8c33f2e9f81b582a6d119cef79907e19a274b916637"
            ),
            "model-00002-of-00008.safetensors": FileSpec(
                2_389_315_976, "163023c61a3fb047745cbaf53ed41c1e27e515e9786a376e122bfac2ea6e687e"
            ),
            "model-00003-of-00008.safetensors": FileSpec(
                2_466_376_368, "4e021702dfac2c52e8fdd6688de82c118be7bb7ad9b5c7988725ec63c44a64fb"
            ),
            "model-00004-of-00008.safetensors": FileSpec(
                2_466_376_400, "b17657bb151daa117a5a4671374ac1b248acb696691a2a67ac227a1115925e30"
            ),
            "model-00005-of-00008.safetensors": FileSpec(
                2_499_431_136, "0ed4e457268f7b02dda5cffe16b3a32614ccc2ccfe5de2db39bdd79700836406"
            ),
            "model-00006-of-00008.safetensors": FileSpec(
                2_483_469_928, "6de8246bb042fd853b57d40995efd289ea44e4d1b611cec2e122570b8d2122bd"
            ),
            "model-00007-of-00008.safetensors": FileSpec(
                1_464_887_482, "a2ba6960d994dc7598efc6796f85ab097da7708f4dd56095f7fccf4df8dc00e5"
            ),
            "model-00008-of-00008.safetensors": FileSpec(
                1_089_994_848, "1b9d9b328f85a25b4efca712d31513c6eed9e178152cc8cf4a6f0c2cd2bb623f"
            ),
            "model.safetensors.index.json": FileSpec(
                120_151, "1468c7b7c74fe27831d8db57871fbf15efd270c747f3f99caf689119ace658ba"
            ),
        },
        recommended_for="64 GB 以上设备的官方基准对照；不参与自动选择",
        benchmark_note="本机 90 分钟样本约 2258.9 秒，近似 WER 3.016%，峰值 RSS 36.24 GB。",
    ),
}

INSTALLATION_TO_VARIANT = {spec.installation_id: variant_id for variant_id, spec in VARIANTS.items()}
PROVIDER_TO_VARIANT = {
    "vibevoice-asr-mlx-4bit": "4bit",
    "vibevoice-asr-mlx-8bit": "8bit",
}

_state_lock = threading.RLock()
_install_threads: dict[VariantId, threading.Thread] = {}
_state: dict[VariantId, dict[str, Any]] = {
    variant_id: {"state": "idle", "error": None, "downloaded_bytes": 0} for variant_id in VARIANTS
}
_verified_files: dict[tuple[str, int, int, str], bool] = {}


def family_root() -> Path:
    return settings_store.managed_model_root() / MODEL_FAMILY_ID


def tokenizer_dir() -> Path:
    return family_root() / "tokenizer"


def variant_dir(variant_id: VariantId) -> Path:
    return family_root() / VARIANTS[variant_id].directory_name


def variant_for_installation(installation_id: str) -> VariantId:
    try:
        return INSTALLATION_TO_VARIANT[installation_id]
    except KeyError as exc:
        raise ValueError(f"Unknown VibeVoice installation id: {installation_id}") from exc


def variant_for_provider(provider_id: str) -> VariantId:
    try:
        return PROVIDER_TO_VARIANT[provider_id]  # type: ignore[return-value]
    except KeyError as exc:
        raise ValueError(f"Unknown VibeVoice provider id: {provider_id}") from exc


def installation_status(
    variant_id: VariantId,
    *,
    verify_integrity: bool = True,
) -> dict[str, Any]:
    spec = VARIANTS[variant_id]
    root = variant_dir(variant_id)
    with _state_lock:
        active = dict(_state[variant_id])
    if active["state"] == "installing":
        installed = False
        integrity = "not_verified"
        state = "installing"
    else:
        installed, integrity = (
            _integrity_status(variant_id)
            if verify_integrity
            else _fast_integrity_status(variant_id)
        )
        if installed:
            state = "installed"
        elif active["state"] == "failed":
            state = "failed"
        elif root.exists():
            state = "incomplete"
        else:
            state = "not_installed"
    downloaded = int(active.get("downloaded_bytes") or 0) if state == "installing" else _downloaded_bytes(spec, root)
    required_total = spec.total_bytes + (
        sum(item.size for item in TOKENIZER_FILES.values()) if spec.runtime == "mlx" else 0
    )
    return {
        "engine_id": spec.installation_id,
        "family_id": MODEL_FAMILY_ID,
        "variant_id": variant_id,
        "display_name": spec.display_name,
        "installed": installed,
        "installation_status": state,
        "integrity": integrity,
        "progress": 1.0 if installed else min(0.99, downloaded / max(1, required_total)),
        "downloaded_bytes": downloaded,
        "total_bytes": required_total,
        "size_bytes": downloaded,
        "error": active.get("error"),
        "preferred_path": str(root),
        "runtime": spec.runtime,
        "revision": spec.revision,
        "recommended_for": spec.recommended_for,
        "benchmark_note": spec.benchmark_note,
        "auto_selectable": variant_id in {"4bit", "8bit"},
    }


def start_install(installation_id: str) -> dict[str, Any]:
    variant_id = variant_for_installation(installation_id)
    with _state_lock:
        active = _install_threads.get(variant_id)
        if active is not None and active.is_alive():
            return installation_status(variant_id)
        if installation_status(variant_id)["installed"]:
            return installation_status(variant_id)
        thread = threading.Thread(
            target=_install_in_background,
            args=(variant_id,),
            name=f"vibevoice-{variant_id}-model-install",
            daemon=True,
        )
        _install_threads[variant_id] = thread
        thread.start()
    return installation_status(variant_id)


def _install_in_background(variant_id: VariantId) -> None:
    try:
        install_now(variant_id)
    except Exception:
        return


def install_now(
    variant_id: VariantId,
    *,
    downloader: Callable[[str, Path, FileSpec, Callable[[int], None]], None] | None = None,
) -> dict[str, Any]:
    spec = VARIANTS[variant_id]
    root = variant_dir(variant_id)
    lock_path = family_root() / ".locks" / f"{MODEL_FAMILY_ID}.lock"
    required_total = spec.total_bytes + (
        sum(item.size for item in TOKENIZER_FILES.values()) if spec.runtime == "mlx" else 0
    )
    with _state_lock:
        _state[variant_id].update(
            state="installing",
            error=None,
            downloaded_bytes=_downloaded_bytes(spec, root),
            total_bytes=required_total,
        )
    try:
        with exclusive_file_lock(lock_path):
            root.mkdir(parents=True, exist_ok=True)
            if spec.runtime == "mlx":
                _install_file_set(
                    repo_id=TOKENIZER_REPO_ID,
                    revision="master",
                    destination_root=tokenizer_dir(),
                    files=TOKENIZER_FILES,
                    downloader=downloader,
                    progress=lambda _current: _update_install_progress(variant_id),
                )
            _install_file_set(
                repo_id=spec.repo_id,
                revision=spec.revision,
                destination_root=root,
                files=spec.files,
                downloader=downloader,
                progress=lambda _current: _update_install_progress(variant_id),
            )
            if spec.runtime == "mlx":
                _link_shared_tokenizer(root)
            require_variant(variant_id)
            _write_install_manifest(variant_id)
            _cleanup_download_parts(variant_id)
        with _state_lock:
            _state[variant_id].update(state="installed", error=None)
        return installation_status(variant_id)
    except Exception as exc:
        with _state_lock:
            _state[variant_id].update(state="failed", error=str(exc)[:500])
        raise


def adopt_existing_variant(variant_id: VariantId) -> dict[str, Any]:
    """Verify a moved model and record it as managed without downloading."""

    root = variant_dir(variant_id)
    if not root.is_dir():
        raise AppException(
            409,
            "VIBEVOICE_MODEL_NOT_FOUND",
            f"没有找到已迁移的 VibeVoice {variant_id} 模型。",
        )
    if VARIANTS[variant_id].runtime == "mlx":
        _link_shared_tokenizer(root)
    root = require_variant(variant_id)
    _write_install_manifest(variant_id)
    with _state_lock:
        _state[variant_id].update(state="installed", error=None)
    return installation_status(variant_id)


def require_variant(variant_id: VariantId) -> Path:
    spec = VARIANTS[variant_id]
    root = variant_dir(variant_id)
    _verify_file_set(root, spec.files)
    if spec.runtime == "mlx":
        _verify_file_set(tokenizer_dir(), TOKENIZER_FILES)
        for filename in ("tokenizer.json", "tokenizer_config.json", "vocab.json"):
            linked = root / filename
            if not linked.is_file() or linked.resolve() != (tokenizer_dir() / filename).resolve():
                raise AppException(
                    409,
                    "VIBEVOICE_TOKENIZER_LINK_INVALID",
                    f"VibeVoice {variant_id} 的共享 tokenizer 链接不完整：{filename}",
                )
    return root.resolve()


def _integrity_status(variant_id: VariantId) -> tuple[bool, str]:
    root = variant_dir(variant_id)
    if not root.exists():
        return False, "not_verified"
    if _verified_manifest_matches(variant_id):
        return True, "verified"
    try:
        require_variant(variant_id)
    except (AppException, OSError, RuntimeError):
        return False, "invalid"
    return True, "verified"


def _fast_integrity_status(variant_id: VariantId) -> tuple[bool, str]:
    """Check the inventory without hashing multi-gigabyte weights."""

    if _verified_manifest_matches(variant_id):
        return True, "verified"
    spec = VARIANTS[variant_id]
    roots = [(variant_dir(variant_id), spec.files)]
    if spec.runtime == "mlx":
        roots.append((tokenizer_dir(), TOKENIZER_FILES))
    try:
        complete = all(
            (root / relative).is_file()
            and (root / relative).stat().st_size == file_spec.size
            for root, files in roots
            for relative, file_spec in files.items()
        )
    except OSError:
        complete = False
    return (True, "size_verified") if complete else (False, "invalid")


def _install_file_set(
    *,
    repo_id: str,
    revision: str,
    destination_root: Path,
    files: dict[str, FileSpec],
    downloader: Callable[[str, Path, FileSpec, Callable[[int], None]], None] | None,
    progress: Callable[[int], None],
) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    for relative, file_spec in files.items():
        destination = destination_root / relative
        if _file_matches(destination, file_spec):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = _modelscope_file_url(repo_id, revision, relative)
        (downloader or _download_file)(url, destination, file_spec, progress)
        _verify_file(destination, file_spec)


def _modelscope_file_url(repo_id: str, revision: str, relative: str) -> str:
    query = urllib.parse.urlencode({"Revision": revision, "FilePath": relative})
    return f"{MODELSCOPE_ENDPOINT}/api/v1/models/{repo_id}/repo?{query}"


class _ModelScopeOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        host = (urllib.parse.urlsplit(newurl).hostname or "").lower()
        if host != "modelscope.cn" and not host.endswith(".modelscope.cn"):
            raise RuntimeError(f"模型下载被重定向到非 ModelScope 域名：{host or 'unknown'}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_file(
    url: str,
    destination: Path,
    file_spec: FileSpec,
    progress: Callable[[int], None],
) -> None:
    part_root = family_root() / ".downloads"
    part_root.mkdir(parents=True, exist_ok=True)
    part = part_root / f"{destination.parent.name}-{destination.name}.part"
    existing = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": "VoiceStudio/1.0"}
    if 0 < existing < file_spec.size:
        headers["Range"] = f"bytes={existing}-"
    elif existing >= file_spec.size:
        part.unlink(missing_ok=True)
        existing = 0
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _ModelScopeOnlyRedirect(),
    )
    try:
        response = opener.open(urllib.request.Request(url, headers=headers), timeout=60)
        append = existing > 0 and getattr(response, "status", None) == 206
        if not append:
            existing = 0
        mode = "ab" if append else "wb"
        downloaded = existing
        with response, part.open(mode) as output:
            while chunk := response.read(4 * 1024 * 1024):
                output.write(chunk)
                downloaded += len(chunk)
                progress(downloaded)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"ModelScope 国内直连下载失败：{destination.name}：{exc}") from exc
    _verify_file(part, file_spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part.replace(destination)


def _verify_file_set(root: Path, files: dict[str, FileSpec]) -> None:
    missing = [relative for relative in files if not (root / relative).is_file()]
    if missing:
        raise AppException(
            409,
            "VIBEVOICE_MODEL_INCOMPLETE",
            "VibeVoice 模型不完整，缺少文件：" + "、".join(missing),
        )
    for relative, spec in files.items():
        _verify_file(root / relative, spec)


def _file_matches(path: Path, spec: FileSpec) -> bool:
    try:
        _verify_file(path, spec)
        return True
    except (OSError, RuntimeError):
        return False


def _verify_file(path: Path, spec: FileSpec) -> None:
    stat = path.stat()
    if stat.st_size != spec.size:
        raise RuntimeError(f"文件大小校验失败：{path.name}（实际 {stat.st_size}，预期 {spec.size}）")
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, spec.sha256)
    if _verified_files.get(key):
        return
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != spec.sha256:
        raise RuntimeError(f"文件哈希校验失败：{path.name}")
    _verified_files[key] = True


def _link_shared_tokenizer(root: Path) -> None:
    for filename in ("tokenizer.json", "tokenizer_config.json", "vocab.json"):
        source = tokenizer_dir() / filename
        if not source.is_file():
            raise AppException(
                409,
                "VIBEVOICE_TOKENIZER_INCOMPLETE",
                f"共享 tokenizer 不完整：{filename}",
            )
        destination = root / filename
        desired = Path("..") / "tokenizer" / filename
        if destination.is_symlink() and os.readlink(destination) == str(desired):
            continue
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        destination.symlink_to(desired)


def _downloaded_bytes(spec: VariantSpec, root: Path) -> int:
    total = 0
    for relative, item in spec.files.items():
        try:
            total += min((root / relative).stat().st_size, item.size)
        except OSError:
            continue
    if spec.runtime == "mlx":
        for relative, item in TOKENIZER_FILES.items():
            try:
                total += min((tokenizer_dir() / relative).stat().st_size, item.size)
            except OSError:
                continue
    return total


def _update_install_progress(variant_id: VariantId) -> None:
    spec = VARIANTS[variant_id]
    downloaded = _downloaded_bytes(spec, variant_dir(variant_id))
    part_root = family_root() / ".downloads"
    if part_root.is_dir():
        for prefix in _download_part_prefixes(variant_id):
            for part in part_root.glob(f"{prefix}*.part"):
                try:
                    downloaded += part.stat().st_size
                except OSError:
                    continue
    with _state_lock:
        _state[variant_id]["downloaded_bytes"] = downloaded


def _write_install_manifest(variant_id: VariantId) -> None:
    spec = VARIANTS[variant_id]
    payload = {
        "schema_version": 1,
        "family_id": MODEL_FAMILY_ID,
        "variant_id": variant_id,
        "installation_id": spec.installation_id,
        "repo_id": spec.repo_id,
        "revision": spec.revision,
        "source": "modelscope-cn",
        "model_license": MODEL_LICENSE,
        "code_license": CODE_LICENSE,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            relative: {
                "size": item.size,
                "sha256": item.sha256,
                "verified_mtime_ns": (variant_dir(variant_id) / relative).stat().st_mtime_ns,
            }
            for relative, item in spec.files.items()
        },
        "shared_tokenizer": (
            {
                "repo_id": TOKENIZER_REPO_ID,
                "path": "../tokenizer",
                "files": {
                    relative: {
                        "size": item.size,
                        "sha256": item.sha256,
                        "verified_mtime_ns": (tokenizer_dir() / relative).stat().st_mtime_ns,
                    }
                    for relative, item in TOKENIZER_FILES.items()
                },
            }
            if spec.runtime == "mlx"
            else None
        ),
    }
    destination = variant_dir(variant_id) / INSTALL_MANIFEST_FILENAME
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)


def _verified_manifest_matches(variant_id: VariantId) -> bool:
    spec = VARIANTS[variant_id]
    root = variant_dir(variant_id)
    manifest_path = root / INSTALL_MANIFEST_FILENAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            payload.get("schema_version") != 1
            or payload.get("variant_id") != variant_id
            or payload.get("repo_id") != spec.repo_id
        ):
            return False
        if not _manifest_file_set_matches(root, spec.files, payload.get("files")):
            return False
        if spec.runtime == "mlx":
            shared = payload.get("shared_tokenizer")
            if not isinstance(shared, dict) or not _manifest_file_set_matches(
                tokenizer_dir(), TOKENIZER_FILES, shared.get("files")
            ):
                return False
            for filename in ("tokenizer.json", "tokenizer_config.json", "vocab.json"):
                linked = root / filename
                if not linked.is_file() or linked.resolve() != (tokenizer_dir() / filename).resolve():
                    return False
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _manifest_file_set_matches(
    root: Path,
    expected: dict[str, FileSpec],
    recorded: object,
) -> bool:
    if not isinstance(recorded, dict) or set(recorded) != set(expected):
        return False
    for relative, spec in expected.items():
        item = recorded.get(relative)
        if not isinstance(item, dict):
            return False
        path = root / relative
        stat = path.stat()
        if (
            item.get("size") != spec.size
            or item.get("sha256") != spec.sha256
            or item.get("verified_mtime_ns") != stat.st_mtime_ns
            or stat.st_size != spec.size
        ):
            return False
    return True


def _cleanup_download_parts(variant_id: VariantId) -> None:
    part_root = family_root() / ".downloads"
    if not part_root.is_dir():
        return
    for prefix in _download_part_prefixes(variant_id):
        for part in part_root.glob(f"{prefix}*.part"):
            part.unlink(missing_ok=True)
    if not any(part_root.iterdir()):
        part_root.rmdir()


def _download_part_prefixes(variant_id: VariantId) -> set[str]:
    prefixes = {f"{VARIANTS[variant_id].directory_name}-"}
    if VARIANTS[variant_id].runtime == "mlx":
        prefixes.add("tokenizer-")
    return prefixes


def physical_memory_bytes() -> int | None:
    try:
        if platform.system().lower() == "darwin":
            value = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=2)
            return int(value.strip())
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def clear_verification_cache() -> None:
    _verified_files.clear()


def remove_empty_managed_download_directories() -> None:
    for name in (".downloads", ".locks"):
        path = family_root() / name
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            continue
