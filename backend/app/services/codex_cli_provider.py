"""Local Codex CLI transport backed by an existing ChatGPT login.

This module never reads Codex credential files. Authentication is discovered
only through ``codex login status``, and model requests are accepted only when
that command reports a ChatGPT login.
"""

from __future__ import annotations

import ipaddress
import json
import os
import select
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence, get_args


CODEX_CLI_ENV = "VOICE_STUDIO_CODEX_CLI"
CODEX_CLI_MAX_CONCURRENCY = 2
MAX_OUTPUT_BYTES = 1024 * 1024
_AUTH_ENV_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_AD_TOKEN",
    }
)
_DISABLED_FEATURES = (
    "shell_tool",
    "apps",
    "hooks",
    "goals",
    "multi_agent",
    "remote_plugin",
    "image_generation",
)
_EXECUTION_LIMIT = threading.BoundedSemaphore(
    value=CODEX_CLI_MAX_CONCURRENCY
)
_METADATA_CACHE: dict[str, tuple[str | None, bool]] = {}
_METADATA_LOCK = threading.Lock()

DiagnosticEvent = Literal[
    "thread.started", "turn.started", "task.started", "item.started", "item.updated",
    "item.completed", "agent_message", "turn.completed", "task.completed",
    "turn.failed", "task.failed", "error", "fatal", "unknown",
]
DiagnosticItem = Literal["agent_message", "reasoning", "error", "command_execution", "tool_call", "unknown"]
DiagnosticErrorCode = Literal[
    "codex_cli_rate_limited", "codex_cli_image_input_unsupported", "codex_cli_auth_failed",
    "codex_cli_execution_failed",
]
DiagnosticFailureReason = Literal[
    "rate_limited", "image_input_unsupported", "auth_failed", "network",
    "service", "model", "unknown",
]
DiagnosticFailureSource = Literal["stdout", "stderr", "none"]
_DIAGNOSTIC_EVENTS = frozenset(get_args(DiagnosticEvent))
_DIAGNOSTIC_ITEMS = frozenset(get_args(DiagnosticItem))
_DIAGNOSTIC_ERROR_CODES = frozenset(get_args(DiagnosticErrorCode))
_DIAGNOSTIC_TERMINALS = frozenset({"turn.completed", "task.completed", "turn.failed", "task.failed", "error", "fatal"})


@dataclass(frozen=True)
class CodexTimeoutDiagnostics:
    """Bounded failure observations; no text, IDs, paths, tokens or credentials."""

    event_counts: tuple[tuple[DiagnosticEvent, int], ...]
    item_counts: tuple[tuple[DiagnosticItem, int], ...]
    last_terminal_event: Literal["turn.completed", "task.completed", "turn.failed", "task.failed", "error", "fatal"] | None
    final_message_seen: bool
    observed_error_codes: tuple[DiagnosticErrorCode, ...]
    malformed_lines: int
    stderr_present: bool
    truncated: bool
    process_exit_code: int | None = None
    failure_reason: DiagnosticFailureReason = "unknown"
    failure_source: DiagnosticFailureSource = "none"

    def summary(self) -> str:
        events = ",".join(f"{kind}:{count}" for kind, count in self.event_counts) or "none"
        items = ",".join(f"{kind}:{count}" for kind, count in self.item_counts) or "none"
        codes = ",".join(self.observed_error_codes) or "none"
        return (
            f"events={events};items={items};terminal={self.last_terminal_event or 'none'};"
            f"reply_seen={int(self.final_message_seen)};error_codes={codes};"
            f"malformed_lines={self.malformed_lines};stderr_present={int(self.stderr_present)};"
            f"truncated={int(self.truncated)};exit_code="
            f"{self.process_exit_code if self.process_exit_code is not None else 'none'};"
            f"failure_reason={self.failure_reason};failure_source={self.failure_source}"
        )


class CodexCliError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int,
                 diagnostics: CodexTimeoutDiagnostics | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class CodexCliStatus:
    installed: bool
    executable_path: str | None = None
    version: str | None = None
    supports_image_input: bool = False
    logged_in: bool = False
    auth_type: Literal["chatgpt", "api_key", "access_token", "unknown"] | None = None
    subscription_usable: bool = False
    operation: Literal["idle", "running", "succeeded", "failed"] = "idle"
    operation_message: str | None = None
    operation_error: str | None = None


@dataclass(frozen=True)
class CodexCompletion:
    text: str
    model_id: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class _AuthOperation:
    operation_id: int = 0
    state: Literal["idle", "running", "succeeded", "failed"] = "idle"
    message: str | None = None
    error: str | None = None
    process: subprocess.Popen[str] | None = field(default=None, repr=False)


_AUTH_OPERATION = _AuthOperation()
_AUTH_LOCK = threading.Lock()


def is_local_client(host: str | None) -> bool:
    """Return whether a request originated from loopback or TestClient."""

    normalized = str(host or "").strip().casefold()
    if normalized == "testclient":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def discover_executable() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get(CODEX_CLI_ENV)
    if configured and configured.strip():
        candidates.append(Path(configured.strip()).expanduser())
    path_match = shutil.which("codex")
    if path_match:
        candidates.append(Path(path_match))
    candidates.extend(
        [
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
            Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
        ]
    )
    seen: set[str] = set()
    for candidate in candidates:
        try:
            normalized = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        key = str(normalized)
        if key in seen:
            continue
        seen.add(key)
        if normalized.is_file() and os.access(normalized, os.X_OK):
            return normalized
    return None


def status() -> CodexCliStatus:
    executable = discover_executable()
    operation = _auth_operation_snapshot()
    if executable is None:
        return CodexCliStatus(
            installed=False,
            operation=operation.state,
            operation_message=operation.message,
            operation_error=operation.error,
        )

    version, supports_image_input = _read_cached_metadata(executable)
    logged_in, auth_type = _read_auth_status(executable)
    return CodexCliStatus(
        installed=True,
        executable_path=str(executable),
        version=version,
        supports_image_input=supports_image_input,
        logged_in=logged_in,
        auth_type=auth_type,
        subscription_usable=logged_in and auth_type == "chatgpt",
        operation=operation.state,
        operation_message=operation.message,
        operation_error=operation.error,
    )


def require_chatgpt_auth() -> tuple[Path, CodexCliStatus]:
    current = status()
    if not current.installed or not current.executable_path:
        raise CodexCliError(
            "未找到本机 Codex CLI，请先安装 ChatGPT 或 Codex CLI",
            code="codex_cli_not_installed",
            status_code=503,
        )
    if not current.logged_in:
        raise CodexCliError(
            "Codex CLI 尚未登录，请先使用 ChatGPT 账号登录",
            code="codex_cli_not_logged_in",
            status_code=401,
        )
    if current.auth_type != "chatgpt":
        raise CodexCliError(
            "当前 Codex CLI 不是 ChatGPT 订阅登录；为避免消耗 API Key，已拒绝本次调用",
            code="codex_cli_subscription_required",
            status_code=409,
        )
    return Path(current.executable_path), current


def list_models(*, timeout: float = 10) -> list[dict[str, str | None]]:
    """Read the current ChatGPT account's visible Codex model catalog."""

    executable, _ = require_chatgpt_auth()
    process = subprocess.Popen(
        [str(executable), "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_clean_environment(),
        start_new_session=True,
    )
    try:
        _write_app_server_message(
            process,
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "voice-studio",
                        "version": "1",
                    }
                },
            },
        )
        _read_app_server_response(process, request_id=1, timeout=timeout)
        _write_app_server_message(process, {"method": "initialized", "params": {}})
        _write_app_server_message(
            process,
            {
                "method": "model/list",
                "id": 2,
                "params": {
                    "limit": 100,
                    "includeHidden": False,
                },
            },
        )
        response = _read_app_server_response(process, request_id=2, timeout=timeout)
        return _parse_model_list_response(response)
    finally:
        if process.poll() is None:
            _terminate_process(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _write_app_server_message(
    process: subprocess.Popen[str],
    message: dict[str, Any],
) -> None:
    if process.stdin is None:
        raise CodexCliError(
            "无法连接本机 Codex 模型目录",
            code="codex_cli_models_unavailable",
            status_code=502,
        )
    try:
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()
    except (BrokenPipeError, OSError):
        raise CodexCliError(
            "本机 Codex 模型目录连接已中断",
            code="codex_cli_models_unavailable",
            status_code=502,
        ) from None


def _read_app_server_response(
    process: subprocess.Popen[str],
    *,
    request_id: int,
    timeout: float,
) -> dict[str, Any]:
    if process.stdout is None:
        raise CodexCliError(
            "无法读取本机 Codex 模型目录",
            code="codex_cli_models_unavailable",
            status_code=502,
        )
    deadline = time.monotonic() + timeout
    output_bytes = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexCliError(
                "读取本机 Codex 模型列表超时",
                code="codex_cli_timeout",
                status_code=504,
            )
        readable, _, _ = select.select([process.stdout], [], [], remaining)
        if not readable:
            raise CodexCliError(
                "读取本机 Codex 模型列表超时",
                code="codex_cli_timeout",
                status_code=504,
            )
        line = process.stdout.readline()
        if not line:
            raise CodexCliError(
                "本机 Codex 没有返回模型列表",
                code="codex_cli_models_unavailable",
                status_code=502,
            )
        output_bytes += len(line.encode("utf-8", "ignore"))
        if output_bytes > MAX_OUTPUT_BYTES:
            raise CodexCliError(
                "本机 Codex 模型列表过大",
                code="codex_cli_response_too_large",
                status_code=502,
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(response, dict) or response.get("id") != request_id:
            continue
        if response.get("error") is not None:
            raise CodexCliError(
                "本机 Codex 无法读取当前账号的模型列表",
                code="codex_cli_models_unavailable",
                status_code=502,
            )
        return response


def _parse_model_list_response(response: dict[str, Any]) -> list[dict[str, str | None]]:
    result = response.get("result")
    raw_models = result.get("data") if isinstance(result, dict) else None
    if not isinstance(raw_models, list):
        raise CodexCliError(
            "本机 Codex 返回了无法识别的模型列表",
            code="codex_cli_response_invalid",
            status_code=502,
        )
    models: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        model_id = item.get("model") or item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        normalized_id = model_id.strip()
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        display_name = item.get("displayName")
        owner = display_name.strip() if isinstance(display_name, str) and display_name.strip() else "Codex CLI"
        if item.get("isDefault") is True:
            owner = f"{owner}（Codex 默认）"
        models.append({"id": normalized_id, "owned_by": owner})
    return models


def start_login(*, relogin: bool = False) -> None:
    executable = discover_executable()
    if executable is None:
        raise CodexCliError(
            "未找到本机 Codex CLI，请先安装 ChatGPT 或 Codex CLI",
            code="codex_cli_not_installed",
            status_code=503,
        )
    with _AUTH_LOCK:
        if _AUTH_OPERATION.state == "running":
            raise CodexCliError(
                "已有 Codex 登录操作正在进行",
                code="codex_cli_login_in_progress",
                status_code=409,
            )
        _AUTH_OPERATION.state = "running"
        _AUTH_OPERATION.operation_id += 1
        operation_id = _AUTH_OPERATION.operation_id
        _AUTH_OPERATION.message = "正在退出旧账号并启动登录…" if relogin else "正在打开 ChatGPT 登录页面…"
        _AUTH_OPERATION.error = None
        _AUTH_OPERATION.process = None
    thread = threading.Thread(
        target=_login_worker,
        args=(executable, relogin, operation_id),
        name="voice-studio-codex-login",
        daemon=True,
    )
    thread.start()


def logout() -> None:
    executable = discover_executable()
    if executable is None:
        raise CodexCliError(
            "未找到本机 Codex CLI",
            code="codex_cli_not_installed",
            status_code=503,
        )
    with _AUTH_LOCK:
        _AUTH_OPERATION.operation_id += 1
        _AUTH_OPERATION.state = "running"
        _AUTH_OPERATION.message = "正在退出 Codex CLI 登录…"
        _AUTH_OPERATION.error = None
        process = _AUTH_OPERATION.process
    if process is not None and process.poll() is None:
        _terminate_process(process)
    try:
        result = _run_command(
            [str(executable), "logout"],
            timeout=15,
            env=_clean_environment(),
        )
    except CodexCliError:
        with _AUTH_LOCK:
            _AUTH_OPERATION.state = "failed"
            _AUTH_OPERATION.message = None
            _AUTH_OPERATION.error = "退出 Codex CLI 登录失败"
            _AUTH_OPERATION.process = None
        raise
    if result.returncode != 0:
        with _AUTH_LOCK:
            _AUTH_OPERATION.state = "failed"
            _AUTH_OPERATION.message = None
            _AUTH_OPERATION.error = "退出 Codex CLI 登录失败"
            _AUTH_OPERATION.process = None
        raise CodexCliError(
            "退出 Codex CLI 登录失败",
            code="codex_cli_logout_failed",
            status_code=502,
        )
    with _AUTH_LOCK:
        _AUTH_OPERATION.state = "succeeded"
        _AUTH_OPERATION.message = "已退出 Codex CLI 登录"
        _AUTH_OPERATION.error = None
        _AUTH_OPERATION.process = None


def complete(
    prompt: str,
    *,
    model_id: str = "",
    image_inputs: Sequence[tuple[bytes, str]] = (),
    timeout: float = 90,
    reasoning_effort: str | None = None,
) -> CodexCompletion:
    executable, _ = require_chatgpt_auth()
    # Keep a small provider-wide concurrency bound and queue overflow callers,
    # but reserve the caller's timeout for its own model execution. Charging
    # queue time against the execution timeout made valid parallel workflow
    # branches fail merely because their sibling was still using the provider.
    _EXECUTION_LIMIT.acquire()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-studio-codex-") as temp_name:
            temp_dir = Path(temp_name)
            os.chmod(temp_dir, 0o700)
            image_paths = _write_image_inputs(temp_dir, image_inputs)
            command = build_exec_command(
                executable,
                temp_dir=temp_dir,
                model_id=model_id,
                image_paths=image_paths,
                reasoning_effort=reasoning_effort,
            )
            result = _run_command(
                command,
                input_text=prompt,
                timeout=timeout,
                cwd=temp_dir,
                env=_clean_environment(),
            )
        if result.returncode != 0:
            diagnostics = _summarize_timeout_output(
                result.stdout,
                result.stderr,
                process_exit_code=result.returncode,
            )
            raise _execution_error_from_diagnostics(diagnostics)
        completion = _parse_jsonl(result.stdout)
        return CodexCompletion(
            text=completion.text,
            model_id=completion.model_id or model_id or "",
            finish_reason=completion.finish_reason,
            prompt_tokens=completion.prompt_tokens,
            cached_tokens=completion.cached_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
        )
    finally:
        _EXECUTION_LIMIT.release()


def build_exec_command(
    executable: Path,
    *,
    temp_dir: Path,
    model_id: str = "",
    image_paths: Sequence[Path] = (),
    reasoning_effort: str | None = None,
) -> list[str]:
    command = [
        str(executable),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--json",
        "--color",
        "never",
        "--cd",
        str(temp_dir),
        "--config",
        'web_search="disabled"',
        "--config",
        'approval_policy="never"',
    ]
    for feature in _DISABLED_FEATURES:
        command.extend(["--disable", feature])
    if model_id.strip():
        command.extend(["--model", model_id.strip()])
    if reasoning_effort in {"low", "high", "max"}:
        command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
    for path in image_paths:
        command.extend(["--image", str(path)])
    command.append("-")
    return command


def _write_image_inputs(temp_dir: Path, image_inputs: Sequence[tuple[bytes, str]]) -> list[Path]:
    suffixes = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    paths: list[Path] = []
    for index, (data, media_type) in enumerate(image_inputs):
        suffix = suffixes.get(media_type)
        if suffix is None:
            raise CodexCliError(
                "Codex 图片格式不受支持",
                code="codex_cli_image_type_unsupported",
                status_code=400,
            )
        target = temp_dir / f"input-{index + 1}{suffix}"
        target.touch(mode=0o600, exist_ok=False)
        target.write_bytes(data)
        os.chmod(target, 0o600)
        paths.append(target)
    return paths


def _read_version(executable: Path) -> str | None:
    try:
        result = _run_command(
            [str(executable), "--version"],
            timeout=5,
            env=_clean_environment(),
        )
    except CodexCliError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0][:200] if result.stdout.strip() else None


def _read_cached_metadata(executable: Path) -> tuple[str | None, bool]:
    key = str(executable)
    with _METADATA_LOCK:
        cached = _METADATA_CACHE.get(key)
    if cached is not None:
        return cached
    metadata = (_read_version(executable), _supports_image_input(executable))
    with _METADATA_LOCK:
        return _METADATA_CACHE.setdefault(key, metadata)


def _read_auth_status(executable: Path) -> tuple[bool, str | None]:
    try:
        result = _run_command(
            [str(executable), "login", "status"],
            timeout=5,
            env=_clean_environment(),
        )
    except CodexCliError:
        return False, None
    output = f"{result.stdout}\n{result.stderr}".strip().casefold()
    if "not logged in" in output or result.returncode != 0:
        return False, None
    if "chatgpt" in output:
        return True, "chatgpt"
    if "api key" in output or "api-key" in output:
        return True, "api_key"
    if "access token" in output or "access-token" in output:
        return True, "access_token"
    if "logged in" in output:
        return True, "unknown"
    return False, None


def _supports_image_input(executable: Path) -> bool:
    try:
        result = _run_command(
            [str(executable), "exec", "--help"],
            timeout=5,
            env=_clean_environment(),
        )
    except CodexCliError:
        return False
    return result.returncode == 0 and "--image" in result.stdout


def _login_worker(executable: Path, relogin: bool, operation_id: int) -> None:
    try:
        if relogin:
            logout_result = _run_command(
                [str(executable), "logout"],
                timeout=15,
                env=_clean_environment(),
            )
            if logout_result.returncode != 0:
                raise CodexCliError(
                    "退出旧 Codex 账号失败",
                    code="codex_cli_logout_failed",
                    status_code=502,
                )
        temp_name = tempfile.mkdtemp(prefix="voice-studio-codex-login-")
        try:
            temp_dir = Path(temp_name)
            os.chmod(temp_dir, 0o700)
            process = subprocess.Popen(
                [str(executable), "login"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=temp_dir,
                env=_clean_environment(),
                start_new_session=True,
            )
            with _AUTH_LOCK:
                if _AUTH_OPERATION.operation_id == operation_id:
                    _AUTH_OPERATION.process = process
            try:
                process.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                _terminate_process(process)
                process.communicate()
                raise CodexCliError(
                    "ChatGPT 登录等待超时，请重试",
                    code="codex_cli_login_timeout",
                    status_code=504,
                ) from None
            returncode = process.returncode
        finally:
            shutil.rmtree(temp_name, ignore_errors=True)
        with _AUTH_LOCK:
            if _AUTH_OPERATION.operation_id == operation_id:
                _AUTH_OPERATION.process = None
                if returncode == 0:
                    _AUTH_OPERATION.state = "succeeded"
                    _AUTH_OPERATION.message = "ChatGPT 登录已完成"
                    _AUTH_OPERATION.error = None
                else:
                    _AUTH_OPERATION.state = "failed"
                    _AUTH_OPERATION.error = "ChatGPT 登录未完成，请重试"
    except Exception as exc:
        with _AUTH_LOCK:
            if _AUTH_OPERATION.operation_id == operation_id:
                _AUTH_OPERATION.process = None
                _AUTH_OPERATION.state = "failed"
                _AUTH_OPERATION.error = (
                    "ChatGPT 登录等待超时，请重试"
                    if isinstance(exc, CodexCliError) and exc.code == "codex_cli_login_timeout"
                    else "ChatGPT 登录失败，请重试"
                )


def _auth_operation_snapshot() -> _AuthOperation:
    with _AUTH_LOCK:
        return _AuthOperation(
            operation_id=_AUTH_OPERATION.operation_id,
            state=_AUTH_OPERATION.state,
            message=_AUTH_OPERATION.message,
            error=_AUTH_OPERATION.error,
        )


def _clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() not in _AUTH_ENV_NAMES}


def _run_command(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        stdout, stderr = process.communicate()
        diagnostics = _summarize_timeout_output(
            stdout or exc.output or "",
            stderr or exc.stderr or "",
            process_exit_code=getattr(process, "returncode", None),
        )
        raise CodexCliError(
            f"本机 Codex CLI 响应超时（安全诊断：{diagnostics.summary()}）",
            code="codex_cli_timeout",
            status_code=504,
            diagnostics=diagnostics,
        ) from None
    if len(stdout.encode("utf-8", "ignore")) > MAX_OUTPUT_BYTES:
        raise CodexCliError(
            "本机 Codex CLI 返回的数据过大",
            code="codex_cli_response_too_large",
            status_code=502,
        )
    return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, OSError):
        process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            process.kill()
        process.wait(timeout=1)


def _execution_failure_reason(detail: str) -> DiagnosticFailureReason:
    message = detail.casefold().replace("_", " ").replace("-", " ")
    if any(
        phrase in message
        for phrase in ("rate limit", "usage limit", "limit reached", "quota", "credit exceeded")
    ):
        return "rate_limited"
    if "image" in message and ("support" in message or "invalid" in message):
        return "image_input_unsupported"
    if any(
        phrase in message
        for phrase in (
            "login", "sign in", "logged out", "not logged in",
            "auth failed", "auth error", "auth required", "authentication",
            "authorization", "unauthorized", "forbidden",
        )
    ):
        return "auth_failed"
    if "model" in message and any(
        phrase in message
        for phrase in ("not found", "not available", "unavailable", "unsupported", "invalid")
    ):
        return "model"
    if any(
        phrase in message
        for phrase in ("network", "connection", "connect ", "econn", "dns", "timed out", "timeout")
    ):
        return "network"
    if any(
        phrase in message
        for phrase in (
            "service unavailable", "temporarily unavailable", "upstream", "server error",
            "internal error", "gateway", "overloaded",
        )
    ):
        return "service"
    return "unknown"


def _diagnostic_error_code(reason: DiagnosticFailureReason) -> DiagnosticErrorCode | None:
    return {
        "rate_limited": "codex_cli_rate_limited",
        "image_input_unsupported": "codex_cli_image_input_unsupported",
        "auth_failed": "codex_cli_auth_failed",
    }.get(reason)


def _execution_error(
    detail: str,
    *,
    diagnostics: CodexTimeoutDiagnostics | None = None,
) -> CodexCliError:
    return _execution_error_for_reason(
        _execution_failure_reason(detail),
        diagnostics=diagnostics,
    )


def _execution_error_from_diagnostics(
    diagnostics: CodexTimeoutDiagnostics,
) -> CodexCliError:
    return _execution_error_for_reason(
        diagnostics.failure_reason,
        diagnostics=diagnostics,
    )


def _execution_error_for_reason(
    reason: DiagnosticFailureReason,
    *,
    diagnostics: CodexTimeoutDiagnostics | None,
) -> CodexCliError:
    messages: dict[DiagnosticFailureReason, tuple[str, str, int]] = {
        "rate_limited": ("ChatGPT/Codex 订阅额度暂时不可用，请稍后重试", "codex_cli_rate_limited", 429),
        "image_input_unsupported": ("当前 Codex 模型无法处理图片输入", "codex_cli_image_input_unsupported", 400),
        "auth_failed": ("Codex CLI 登录已失效，请重新登录 ChatGPT", "codex_cli_auth_failed", 401),
        "network": ("本机 Codex CLI 无法连接服务，请检查网络或服务状态", "codex_cli_execution_failed", 502),
        "service": ("Codex 服务暂时不可用，请稍后重试", "codex_cli_execution_failed", 502),
        "model": ("所选 Codex 模型当前不可用，请检查模型配置", "codex_cli_execution_failed", 502),
        "unknown": ("本机 Codex CLI 调用失败", "codex_cli_execution_failed", 502),
    }
    message, code, status_code = messages[reason]
    if diagnostics is not None:
        message += f"（安全诊断：{diagnostics.summary()}）"
    return CodexCliError(
        message,
        code=code,
        status_code=status_code,
        diagnostics=diagnostics,
    )


def _summarize_timeout_output(
    stdout: str | bytes,
    stderr: str | bytes,
    *,
    process_exit_code: int | None = None,
) -> CodexTimeoutDiagnostics:
    """Inspect bounded tails, emitting only known enum labels and numeric facts.

    This does not invoke the completion parser or establish success. A reply or
    completed event seen before process termination is only an observation.
    """
    def bounded_tail(value: str | bytes) -> tuple[str, bool]:
        encoded = value.encode("utf-8", "replace") if isinstance(value, str) else value
        return encoded[-MAX_OUTPUT_BYTES:].decode("utf-8", "replace"), len(encoded) > MAX_OUTPUT_BYTES

    output, output_truncated = bounded_tail(stdout)
    errors, errors_truncated = bounded_tail(stderr)
    events: dict[str, int] = {}
    items: dict[str, int] = {}
    codes: set[str] = set()
    terminal = None
    reply_seen = False
    malformed = 0
    stdout_failure_seen = False
    stdout_failure_reason: DiagnosticFailureReason = "unknown"
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        event_type = event.get("type")
        kind = event_type if isinstance(event_type, str) and event_type in _DIAGNOSTIC_EVENTS else "unknown"
        events[kind] = events.get(kind, 0) + 1
        if kind in _DIAGNOSTIC_TERMINALS:
            terminal = kind
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = item.get("type")
        if kind in {"item.started", "item.updated", "item.completed"}:
            item_kind = item_type if isinstance(item_type, str) and item_type in _DIAGNOSTIC_ITEMS else "unknown"
            items[item_kind] = items.get(item_kind, 0) + 1
        if kind == "agent_message":
            text = event.get("text") or event.get("message")
            reply_seen |= isinstance(text, str) and bool(text.strip())
        elif kind == "item.completed" and item_type == "agent_message":
            text = item.get("text")
            reply_seen |= isinstance(text, str) and bool(text.strip())
        if kind in {"turn.failed", "task.failed", "error", "fatal"}:
            stdout_failure_seen = True
            detail = _event_failure_detail(event)
            reason = _execution_failure_reason(detail)
            if stdout_failure_reason == "unknown" and reason != "unknown":
                stdout_failure_reason = reason
            code = _diagnostic_error_code(reason)
            if code is not None:
                codes.add(code)
    stderr_reason = _execution_failure_reason(errors) if errors.strip() else "unknown"
    if errors.strip():
        code = _diagnostic_error_code(stderr_reason)
        # Unstructured stderr alone does not prove a failure or reconnect.
        if code is not None:
            codes.add(code)
    if stdout_failure_reason != "unknown":
        failure_reason = stdout_failure_reason
        failure_source: DiagnosticFailureSource = "stdout"
    elif stderr_reason != "unknown":
        failure_reason = stderr_reason
        failure_source = "stderr"
    elif stdout_failure_seen:
        failure_reason = "unknown"
        failure_source = "stdout"
    elif errors.strip():
        failure_reason = "unknown"
        failure_source = "stderr"
    else:
        failure_reason = "unknown"
        failure_source = "none"
    return CodexTimeoutDiagnostics(
        event_counts=tuple(sorted(events.items())), item_counts=tuple(sorted(items.items())),
        last_terminal_event=terminal, final_message_seen=reply_seen,
        observed_error_codes=tuple(sorted(codes)), malformed_lines=malformed,
        stderr_present=bool(errors), truncated=output_truncated or errors_truncated,
        process_exit_code=(
            process_exit_code
            if isinstance(process_exit_code, int) and not isinstance(process_exit_code, bool)
            else None
        ),
        failure_reason=failure_reason,
        failure_source=failure_source,
    )


def _event_failure_detail(event: dict[str, Any]) -> str:
    """Read only enough transient text to classify an event; never retain it."""

    values: list[str] = []
    error = event.get("error")
    if isinstance(error, dict):
        for key in ("code", "type", "message"):
            value = error.get(key)
            if isinstance(value, str):
                values.append(value)
    for key in ("code", "message"):
        value = event.get(key)
        if isinstance(value, str):
            values.append(value)
    return "\n".join(values)


def _parse_jsonl(raw: str) -> CodexCompletion:
    final_text = ""
    model_id = ""
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    turn_number = 0
    active_turn: int | None = None
    pending_network_error_turn: int | None = None
    fresh_completed_reply_seen = False

    def raise_stream_failure() -> None:
        diagnostics = _summarize_timeout_output(
            raw,
            "",
            process_exit_code=0,
        )
        raise _execution_error_from_diagnostics(diagnostics)

    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "turn.started":
            if pending_network_error_turn is not None:
                raise_stream_failure()
            turn_number += 1
            active_turn = turn_number
        if event_type in {"turn.failed", "task.failed", "fatal"}:
            raise_stream_failure()
        if event_type == "error":
            reason = _execution_failure_reason(_event_failure_detail(event))
            if reason != "network" or active_turn is None:
                raise_stream_failure()
            pending_network_error_turn = active_turn
            fresh_completed_reply_seen = False
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if event_type == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                final_text = text.strip()
                if pending_network_error_turn == active_turn:
                    fresh_completed_reply_seen = True
        elif event_type == "agent_message":
            text = event.get("text") or event.get("message")
            if isinstance(text, str) and text.strip():
                final_text = text.strip()
        if event_type in {"turn.completed", "task.completed"}:
            if event_type == "turn.completed" and pending_network_error_turn is not None:
                if (
                    pending_network_error_turn != active_turn
                    or not fresh_completed_reply_seen
                ):
                    raise_stream_failure()
                pending_network_error_turn = None
                fresh_completed_reply_seen = False
            event_usage = event.get("usage")
            if isinstance(event_usage, dict):
                usage = event_usage
            candidate_model = event.get("model")
            if isinstance(candidate_model, str):
                model_id = candidate_model
            finish_reason = "stop"
            if event_type == "turn.completed":
                active_turn = None
    if pending_network_error_turn is not None:
        raise_stream_failure()
    if not final_text:
        raise CodexCliError(
            "本机 Codex CLI 没有返回最终回复",
            code="codex_cli_response_invalid",
            status_code=502,
        )
    input_tokens = _optional_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    cached_tokens = _optional_int(usage.get("cached_input_tokens") or usage.get("cached_tokens"))
    output_tokens = _optional_int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _optional_int(usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return CodexCompletion(
        text=final_text,
        model_id=model_id,
        finish_reason=finish_reason,
        prompt_tokens=input_tokens,
        cached_tokens=cached_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
