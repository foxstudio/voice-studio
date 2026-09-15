from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import codex_cli_provider as provider  # noqa: E402


def _completed(args, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_discover_executable_prefers_configured_executable(tmp_path: Path, monkeypatch):
    executable = tmp_path / "codex"
    executable.touch()
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(provider.CODEX_CLI_ENV, str(executable))
    monkeypatch.setattr(provider.shutil, "which", lambda _name: None)

    assert provider.discover_executable() == executable.resolve()


def test_status_parses_chatgpt_auth_version_and_image_support(monkeypatch):
    executable = Path("/safe/codex")
    provider._METADATA_CACHE.clear()
    monkeypatch.setattr(provider, "discover_executable", lambda: executable)
    calls = {"version": 0, "help": 0, "status": 0}

    def fake_run(command, **_kwargs):
        if command[-1] == "--version":
            calls["version"] += 1
            return _completed(command, "codex-cli 1.2.3\n")
        if command[-2:] == ["exec", "--help"]:
            calls["help"] += 1
            return _completed(command, "Options:\n  -i, --image <FILE>...")
        if command[-2:] == ["login", "status"]:
            calls["status"] += 1
            return _completed(command, "Logged in using ChatGPT\n")
        raise AssertionError(command)

    monkeypatch.setattr(provider, "_run_command", fake_run)

    status = provider.status()

    assert status.installed is True
    assert status.executable_path == "/safe/codex"
    assert status.version == "codex-cli 1.2.3"
    assert status.supports_image_input is True
    assert status.logged_in is True
    assert status.auth_type == "chatgpt"
    assert status.subscription_usable is True
    assert provider.status().subscription_usable is True
    assert calls == {"version": 1, "help": 1, "status": 2}


@pytest.mark.parametrize(
    ("status_line", "expected"),
    [
        ("Logged in using an API key", "api_key"),
        ("Logged in using access token", "access_token"),
        ("Logged in using something else", "unknown"),
    ],
)
def test_auth_status_distinguishes_non_subscription_auth(monkeypatch, status_line, expected):
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **kwargs: _completed(command, status_line),
    )
    assert provider._read_auth_status(Path("/codex")) == (True, expected)


def test_require_chatgpt_auth_rejects_api_key_login(monkeypatch):
    monkeypatch.setattr(
        provider,
        "status",
        lambda: provider.CodexCliStatus(
            installed=True,
            executable_path="/safe/codex",
            logged_in=True,
            auth_type="api_key",
        ),
    )
    with pytest.raises(provider.CodexCliError) as exc_info:
        provider.require_chatgpt_auth()
    assert exc_info.value.code == "codex_cli_subscription_required"


def test_parse_model_list_response_keeps_visible_unique_models():
    response = {
        "id": 2,
        "result": {
            "data": [
                {
                    "id": "gpt-default",
                    "model": "gpt-default",
                    "displayName": "GPT Default",
                    "hidden": False,
                    "isDefault": True,
                },
                {
                    "id": "gpt-hidden",
                    "model": "gpt-hidden",
                    "displayName": "GPT Hidden",
                    "hidden": True,
                    "isDefault": False,
                },
                {
                    "id": "gpt-default",
                    "model": "gpt-default",
                    "displayName": "Duplicate",
                    "hidden": False,
                    "isDefault": False,
                },
                {
                    "id": "gpt-fast",
                    "model": "gpt-fast",
                    "displayName": "GPT Fast",
                    "hidden": False,
                    "isDefault": False,
                },
            ]
        },
    }

    assert provider._parse_model_list_response(response) == [
        {"id": "gpt-default", "owned_by": "GPT Default（Codex 默认）"},
        {"id": "gpt-fast", "owned_by": "GPT Fast"},
    ]


def test_exec_command_is_ephemeral_read_only_and_disables_external_tools(tmp_path: Path):
    command = provider.build_exec_command(
        Path("/safe/codex"),
        temp_dir=tmp_path,
        model_id="gpt-test",
        image_paths=[tmp_path / "frame.png"],
        reasoning_effort="low",
    )
    joined = " ".join(command)
    assert command[-1] == "-"
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--skip-git-repo-check" in command
    assert "--sandbox read-only" in joined
    assert "--json" in command
    assert "--color never" in joined
    assert 'web_search="disabled"' in command
    for feature in ("shell_tool", "apps", "hooks", "goals", "multi_agent", "remote_plugin"):
        assert ["--disable", feature] == command[command.index(feature) - 1 : command.index(feature) + 1]
    assert "--model gpt-test" in joined
    assert "--image" in command


def test_complete_sends_prompt_on_stdin_cleans_auth_env_and_removes_images(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-leak")
    monkeypatch.setattr(
        provider,
        "require_chatgpt_auth",
        lambda: (
            Path("/safe/codex"),
            provider.CodexCliStatus(
                installed=True,
                executable_path="/safe/codex",
                logged_in=True,
                auth_type="chatgpt",
            ),
        ),
    )
    captured = {}

    def fake_run(command, *, input_text, timeout, cwd, env):
        image_path = Path(command[command.index("--image") + 1])
        captured.update(
            command=command,
            input_text=input_text,
            timeout=timeout,
            cwd=cwd,
            env=env,
            image_path=image_path,
            image_mode=stat.S_IMODE(image_path.stat().st_mode),
            image_data=image_path.read_bytes(),
        )
        events = "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": '{"scene":"interview"}'},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "model": "gpt-test",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 2,
                            "output_tokens": 4,
                        },
                    }
                ),
            ]
        )
        return _completed(command, events)

    monkeypatch.setattr(provider, "_run_command", fake_run)

    result = provider.complete(
        "private prompt",
        model_id="gpt-test",
        image_inputs=[(b"image-bytes", "image/png")],
        timeout=12,
    )

    assert result.text == '{"scene":"interview"}'
    assert result.model_id == "gpt-test"
    assert result.prompt_tokens == 10
    assert result.cached_tokens == 2
    assert result.completion_tokens == 4
    assert result.total_tokens == 14
    assert captured["input_text"] == "private prompt"
    assert "private prompt" not in captured["command"]
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "CODEX_ACCESS_TOKEN" not in captured["env"]
    assert captured["image_mode"] == 0o600
    assert captured["image_data"] == b"image-bytes"
    assert not captured["image_path"].exists()
    assert not captured["cwd"].exists()


def test_complete_queues_concurrent_calls_without_spending_the_execution_timeout(monkeypatch):
    monkeypatch.setattr(provider, "_EXECUTION_LIMIT", threading.BoundedSemaphore(value=1))
    monkeypatch.setattr(
        provider,
        "require_chatgpt_auth",
        lambda: (
            Path("/safe/codex"),
            provider.CodexCliStatus(
                installed=True,
                executable_path="/safe/codex",
                logged_in=True,
                auth_type="chatgpt",
            ),
        ),
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls: list[tuple[str, float]] = []

    def fake_run(command, *, input_text, timeout, cwd, env):
        calls.append((input_text, timeout))
        if input_text == "first":
            first_entered.set()
            assert release_first.wait(timeout=1)
        else:
            second_entered.set()
        return _completed(
            command,
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": input_text},
                }
            ),
        )

    monkeypatch.setattr(provider, "_run_command", fake_run)
    results: list[str] = []

    first = threading.Thread(
        target=lambda: results.append(provider.complete("first", timeout=0.5).text)
    )
    second = threading.Thread(
        target=lambda: results.append(provider.complete("second", timeout=0.5).text)
    )
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_entered.is_set()
    assert sorted(results) == ["first", "second"]
    assert calls == [("first", 0.5), ("second", 0.5)]


def test_complete_allows_two_provider_calls_to_run_concurrently(monkeypatch):
    monkeypatch.setattr(
        provider,
        "_EXECUTION_LIMIT",
        threading.BoundedSemaphore(
            value=provider.CODEX_CLI_MAX_CONCURRENCY
        ),
    )
    monkeypatch.setattr(
        provider,
        "require_chatgpt_auth",
        lambda: (
            Path("/safe/codex"),
            provider.CodexCliStatus(
                installed=True,
                executable_path="/safe/codex",
                logged_in=True,
                auth_type="chatgpt",
            ),
        ),
    )
    both_entered = threading.Barrier(2)
    release_calls = threading.Event()

    def fake_run(command, *, input_text, timeout, cwd, env):
        both_entered.wait(timeout=1)
        assert release_calls.wait(timeout=1)
        return _completed(
            command,
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": input_text},
                }
            ),
        )

    monkeypatch.setattr(provider, "_run_command", fake_run)
    results: list[str] = []
    first = threading.Thread(
        target=lambda: results.append(provider.complete("first").text)
    )
    second = threading.Thread(
        target=lambda: results.append(provider.complete("second").text)
    )
    first.start()
    second.start()
    for _ in range(100):
        if both_entered.n_waiting == 0 and first.is_alive() and second.is_alive():
            break
        threading.Event().wait(0.01)
    release_calls.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == ["first", "second"]


def test_jsonl_fatal_event_is_failure_but_item_error_is_nonfatal():
    warning_then_success = "\n".join(
        [
            json.dumps({"type": "item.completed", "item": {"type": "error", "message": "warning"}}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "done"},
                }
            ),
        ]
    )
    assert provider._parse_jsonl(warning_then_success).text == "done"

    with pytest.raises(provider.CodexCliError, match="Codex 服务暂时不可用") as caught:
        provider._parse_jsonl(
            json.dumps({"type": "turn.failed", "error": {"message": "upstream failed"}})
        )
    assert caught.value.code == "codex_cli_execution_failed"
    assert caught.value.diagnostics.failure_reason == "service"


def test_jsonl_recovers_transient_network_error_only_after_fresh_completed_reply_and_turn_completion():
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {"type": "error", "message": "stream disconnected - retrying network request"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"decision":"keep"}'},
        },
        {"type": "turn.completed", "model": "gpt-test"},
    ]

    completion = provider._parse_jsonl("\n".join(json.dumps(event) for event in events))

    assert completion.text == '{"decision":"keep"}'
    assert completion.model_id == "gpt-test"
    assert completion.finish_reason == "stop"


@pytest.mark.parametrize(
    "error_message",
    [
        "authentication failed",
        "usage limit reached",
        "unexpected provider failure",
    ],
)
def test_jsonl_does_not_recover_non_network_error_after_successful_tail(error_message):
    events = [
        {"type": "turn.started"},
        {"type": "error", "message": error_message},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "fresh reply"},
        },
        {"type": "turn.completed"},
    ]

    with pytest.raises(provider.CodexCliError):
        provider._parse_jsonl("\n".join(json.dumps(event) for event in events))


@pytest.mark.parametrize(
    "events",
    [
        [
            {"type": "turn.started"},
            {"type": "error", "message": "network connection reset"},
        ],
        [
            {"type": "turn.started"},
            {"type": "error", "message": "network connection reset"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "fresh reply"},
            },
        ],
        [
            {"type": "turn.started"},
            {"type": "error", "message": "network connection reset"},
            {"type": "agent_message", "text": "reply without completed item"},
            {"type": "turn.completed"},
        ],
        [
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "old reply"},
            },
            {"type": "error", "message": "network connection reset"},
            {"type": "turn.completed"},
        ],
        [
            {"type": "turn.started"},
            {"type": "error", "message": "network connection reset"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "different turn reply"},
            },
            {"type": "turn.completed"},
        ],
        [
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "reply"},
            },
            {"type": "turn.completed"},
            {"type": "error", "message": "network connection reset"},
        ],
    ],
)
def test_jsonl_rejects_unclosed_or_cross_turn_network_error(events):
    with pytest.raises(provider.CodexCliError) as caught:
        provider._parse_jsonl("\n".join(json.dumps(event) for event in events))

    assert caught.value.diagnostics.failure_reason == "network"


@pytest.mark.parametrize("event_type", ["turn.failed", "task.failed", "fatal"])
def test_jsonl_never_recovers_explicit_terminal_failure(event_type):
    events = [
        {"type": "turn.started"},
        {"type": event_type, "error": {"message": "network connection reset"}},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "reply"},
        },
        {"type": "turn.completed"},
    ]

    with pytest.raises(provider.CodexCliError):
        provider._parse_jsonl("\n".join(json.dumps(event) for event in events))


def _authenticated_provider(monkeypatch):
    monkeypatch.setattr(
        provider,
        "require_chatgpt_auth",
        lambda: (
            Path("/safe/codex"),
            provider.CodexCliStatus(
                installed=True,
                executable_path="/safe/codex",
                logged_in=True,
                auth_type="chatgpt",
            ),
        ),
    )


def test_complete_prefers_structured_stdout_failure_and_records_exit_facts(monkeypatch):
    _authenticated_provider(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **_kwargs: _completed(
            command,
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"code": "network_error", "message": "connection reset"},
                }
            ),
            "unexpected failure",
            returncode=23,
        ),
    )

    with pytest.raises(provider.CodexCliError) as caught:
        provider.complete("private prompt")

    error = caught.value
    assert error.code == "codex_cli_execution_failed"
    assert error.status_code == 502
    assert error.diagnostics.process_exit_code == 23
    assert error.diagnostics.last_terminal_event == "turn.failed"
    assert error.diagnostics.failure_reason == "network"
    assert error.diagnostics.failure_source == "stdout"
    assert "无法连接服务" in str(error)
    assert "exit_code=23" in str(error)


def test_complete_uses_stderr_as_fallback_for_nonzero_exit(monkeypatch):
    _authenticated_provider(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **_kwargs: _completed(
            command,
            "",
            "usage limit reached",
            returncode=1,
        ),
    )

    with pytest.raises(provider.CodexCliError) as caught:
        provider.complete("private prompt")

    assert caught.value.code == "codex_cli_rate_limited"
    assert caught.value.diagnostics.failure_reason == "rate_limited"
    assert caught.value.diagnostics.failure_source == "stderr"
    assert caught.value.diagnostics.process_exit_code == 1


def test_complete_unknown_failure_never_leaks_stdout_or_stderr(monkeypatch):
    _authenticated_provider(monkeypatch)
    secret = "Bearer private-token /Users/example/project request-id=secret"
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **_kwargs: _completed(
            command,
            json.dumps({"type": "turn.failed", "error": {"message": secret}}),
            secret,
            returncode=7,
        ),
    )

    with pytest.raises(provider.CodexCliError) as caught:
        provider.complete("private prompt")

    error = caught.value
    rendered = str(error) + repr(error.diagnostics)
    assert error.code == "codex_cli_execution_failed"
    assert error.diagnostics.failure_reason == "unknown"
    assert error.diagnostics.failure_source == "stdout"
    for forbidden in ("Bearer", "private-token", "/Users", "request-id", "private prompt"):
        assert forbidden not in rendered


def test_complete_zero_exit_with_failed_event_is_still_failure(monkeypatch):
    _authenticated_provider(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **_kwargs: _completed(
            command,
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"code": "model_not_found", "message": "model not found"},
                }
            ),
        ),
    )

    with pytest.raises(provider.CodexCliError) as caught:
        provider.complete("private prompt")

    error = caught.value
    assert error.code == "codex_cli_execution_failed"
    assert error.diagnostics.process_exit_code == 0
    assert error.diagnostics.last_terminal_event == "turn.failed"
    assert error.diagnostics.failure_reason == "model"
    assert "模型当前不可用" in str(error)


def test_complete_zero_exit_recovers_network_error_with_same_turn_success_tail(monkeypatch):
    _authenticated_provider(monkeypatch)
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {"type": "error", "message": "network connection reset; retrying"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "done"},
        },
        {"type": "turn.completed", "model": "gpt-test"},
    ]
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **_kwargs: _completed(
            command,
            "\n".join(json.dumps(event) for event in events),
        ),
    )

    result = provider.complete("private prompt")

    assert result.text == "done"
    assert result.model_id == "gpt-test"


def test_complete_nonzero_exit_rejects_recoverable_success_tail(monkeypatch):
    _authenticated_provider(monkeypatch)
    events = [
        {"type": "turn.started"},
        {"type": "error", "message": "network connection reset; retrying"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "done"},
        },
        {"type": "turn.completed"},
    ]
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **_kwargs: _completed(
            command,
            "\n".join(json.dumps(event) for event in events),
            returncode=1,
        ),
    )

    with pytest.raises(provider.CodexCliError) as caught:
        provider.complete("private prompt")

    assert caught.value.diagnostics.process_exit_code == 1
    assert caught.value.diagnostics.failure_reason == "network"


def test_complete_success_keeps_jsonl_completion_path(monkeypatch):
    _authenticated_provider(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda command, **_kwargs: _completed(
            command,
            "\n".join(
                [
                    json.dumps(
                        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}
                    ),
                    json.dumps({"type": "turn.completed", "model": "gpt-test"}),
                ]
            ),
        ),
    )

    result = provider.complete("private prompt")

    assert result.text == "done"
    assert result.model_id == "gpt-test"


@pytest.mark.parametrize(
    "detail",
    ["author", "authoritative provider failure"],
)
def test_execution_failure_reason_does_not_match_author_as_auth(detail):
    assert provider._execution_failure_reason(detail) == "unknown"


@pytest.mark.parametrize(
    "detail",
    ["authentication failed", "authorization denied", "unauthorized", "login required"],
)
def test_execution_failure_reason_keeps_explicit_auth_failures(detail):
    assert provider._execution_failure_reason(detail) == "auth_failed"


def test_run_command_timeout_terminates_then_kills_process_group(monkeypatch):
    actions = []

    class FakeProcess:
        pid = 31415
        returncode = None

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(["codex"], timeout)
            return "", ""

        def wait(self, timeout=None):
            if len(actions) == 1:
                raise subprocess.TimeoutExpired(["codex"], timeout)
            self.returncode = -9
            return self.returncode

        def terminate(self):
            actions.append("terminate-fallback")

        def kill(self):
            actions.append("kill-fallback")

    monkeypatch.setattr(provider.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        provider.os,
        "killpg",
        lambda pid, sig: actions.append((pid, sig)),
    )

    with pytest.raises(provider.CodexCliError) as exc_info:
        provider._run_command(["codex", "exec"], timeout=0.01)

    assert exc_info.value.code == "codex_cli_timeout"
    assert actions == [(31415, provider.signal.SIGTERM), (31415, provider.signal.SIGKILL)]


@pytest.mark.parametrize(
    ("events", "terminal", "reply_seen", "error_codes"),
    [
        ([{"type": "turn.started"}], None, False, ()),
        ([{"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
          {"type": "turn.completed", "usage": {"output_tokens": 99}}], "turn.completed", True, ()),
        ([{"type": "turn.failed", "error": {"message": "usage limit reached"}}],
         "turn.failed", False, ("codex_cli_rate_limited",)),
        ([{"type": "error", "message": "unauthorized"}], "error", False, ("codex_cli_auth_failed",)),
        ([{"type": "task.failed", "error": {"message": "invalid image input"}}],
         "task.failed", False, ("codex_cli_image_input_unsupported",)),
    ],
)
def test_timeout_diagnostics_preserves_observations_but_never_returns_success(
    monkeypatch, events, terminal, reply_seen, error_codes,
):
    stdout = "\n".join(json.dumps(event) for event in events)
    communicate_count = []
    class FakeProcess:
        def communicate(self, input=None, timeout=None):
            communicate_count.append(timeout)
            if timeout is not None:
                raise subprocess.TimeoutExpired(["codex"], timeout)
            return stdout, ""
    monkeypatch.setattr(provider.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(provider, "_terminate_process", lambda process: None)
    with pytest.raises(provider.CodexCliError) as caught:
        provider._run_command(["codex", "exec"], timeout=0.01)
    error = caught.value
    assert error.code == "codex_cli_timeout"
    assert error.status_code == 504
    assert error.diagnostics.last_terminal_event == terminal
    assert error.diagnostics.final_message_seen is reply_seen
    assert error.diagnostics.observed_error_codes == error_codes
    assert communicate_count == [0.01, None]
    assert "answer" not in str(error)
    assert "output_tokens" not in str(error)


def test_timeout_diagnostics_drops_unknown_labels_and_all_sensitive_content():
    secret = "Bearer private-token /Users/example/project hidden-reasoning"
    events = [
        {"type": secret, "message": secret, "authorization": secret},
        {"type": [secret], "message": secret},
        {"type": "item.completed", "item": {"type": "reasoning", "text": secret}},
        {"type": "item.updated", "item": {"type": secret, "text": secret}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": secret}},
        {"type": "turn.completed", "model": secret, "usage": {"reasoning_tokens": 987654321}},
        {"type": "turn.failed", "error": {"message": "auth failed " + secret}},
    ]
    diagnostic = provider._summarize_timeout_output("\n".join(json.dumps(e) for e in events), secret)
    rendered = diagnostic.summary() + repr(diagnostic)
    for forbidden in ("Bearer", "private-token", "/Users", "hidden-reasoning", "987654321", "authorization"):
        assert forbidden not in rendered
    assert dict(diagnostic.event_counts)["unknown"] == 2
    assert dict(diagnostic.item_counts)["unknown"] == 1
    assert dict(diagnostic.item_counts)["reasoning"] == 1
    assert diagnostic.observed_error_codes == ("codex_cli_auth_failed",)


def test_timeout_diagnostics_bounds_tail_and_marks_incomplete_json(monkeypatch):
    monkeypatch.setattr(provider, "MAX_OUTPUT_BYTES", 128)
    prefix = json.dumps({"type": "turn.started", "ignored": "x" * 500})
    final = json.dumps({"type": "turn.completed"})
    diagnostic = provider._summarize_timeout_output(prefix + "\n" + final, b"y" * 300)
    assert diagnostic.truncated is True
    assert diagnostic.last_terminal_event == "turn.completed"
    assert diagnostic.malformed_lines == 1
    assert "x" * 20 not in diagnostic.summary()
    assert "y" * 20 not in diagnostic.summary()


def test_timeout_diagnostics_uses_exception_bytes_when_post_termination_output_is_empty(monkeypatch):
    class FakeProcess:
        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(["codex"], timeout,
                    output=b'{"type":"turn.started"}\n', stderr=b'usage limit')
            return "", ""
    monkeypatch.setattr(provider.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(provider, "_terminate_process", lambda process: None)
    with pytest.raises(provider.CodexCliError) as caught:
        provider._run_command(["codex", "exec"], timeout=0.01)
    assert caught.value.diagnostics.event_counts == (("turn.started", 1),)
    assert caught.value.diagnostics.observed_error_codes == ("codex_cli_rate_limited",)


def test_local_client_accepts_loopback_and_rejects_lan():
    assert provider.is_local_client("testclient")
    assert provider.is_local_client("127.0.0.1")
    assert provider.is_local_client("::1")
    assert not provider.is_local_client("192.168.1.20")
    assert not provider.is_local_client("localhost.example")
