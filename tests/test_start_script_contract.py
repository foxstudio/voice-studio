import os
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_script_passes_configured_ports_to_vite_and_proxy():
    script = (ROOT / "start.sh").read_text(encoding="utf-8")

    assert 'VITE_API_TARGET="http://127.0.0.1:${BACKEND_PORT}"' in script
    assert 'pnpm dev --port "$FRONTEND_PORT"' in script


def test_optional_media_capabilities_do_not_block_application_startup():
    script = (ROOT / "start.sh").read_text(encoding="utf-8")

    startup_check = script.rsplit("if ! backend_runtime_ready; then", 1)[1].split("fi", 1)[0]
    assert "warn" in startup_check
    assert "exit 1" not in startup_check
    assert "stop_external_video_localization_worker" not in startup_check


def test_existing_healthy_backend_is_reused_when_optional_capabilities_are_missing():
    script = (ROOT / "start.sh").read_text(encoding="utf-8")
    healthy_check = script.split(
        'if check_healthy "$BACKEND_PORT" "$BACKEND_HEALTH_URL"; then',
        1,
    )[1].split("fi", 1)[0]

    assert "BACKEND_HEALTHY=true" in healthy_check
    assert "if ! backend_runtime_ready; then" in healthy_check


def test_startup_has_no_personal_absolute_engine_path_and_runs_doctor():
    script = (ROOT / "start.sh").read_text(encoding="utf-8")

    assert "/Users/foxmacstudio" not in script
    assert 'if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then' in script
    assert 'BOOTSTRAP_PYTHON="$PROJECT_ROOT/.venv/bin/python"' in script
    assert "voice_studio_doctor.py" in script
    assert "--launcher posix --strict" in script


def test_windows_launcher_routes_mlx_runtime_through_wsl():
    script = (ROOT / "start.ps1").read_text(encoding="utf-8")

    assert "wsl.exe" in script
    assert "wslpath" in script
    assert "start.sh" in script
    assert "WSL 2" in script


def test_windows_launcher_checks_dependencies_inside_wsl_without_host_python():
    script = (ROOT / "start.ps1").read_text(encoding="utf-8")

    assert "Get-Command py" not in script
    assert "Get-Command python" not in script
    assert "scripts\\voice_studio_doctor.py" not in script
    assert '& wsl.exe bash $WslDoctorScript --doctor' in script
    assert '$WslDoctorScript = & wsl.exe wslpath -a $DoctorStartScript' in script
    assert '$WslDoctorPathExitCode = $LASTEXITCODE' in script
    assert '$WslDoctorScript = ($WslDoctorScript | Out-String).Trim()' in script
    assert "(& wsl.exe wslpath" not in script
    assert "exit $LASTEXITCODE" in script


def test_production_launcher_builds_and_serves_one_local_application():
    script = (ROOT / "start-production.sh").read_text(encoding="utf-8")

    assert 'if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then' in script
    assert 'BOOTSTRAP_PYTHON="$PROJECT_ROOT/.venv/bin/python"' in script
    assert 'pnpm --dir "$PROJECT_ROOT/frontend" build' in script
    assert "VOICE_STUDIO_SERVE_FRONTEND=1" in script
    assert 'VOICE_STUDIO_FRONTEND_DIST="$PROJECT_ROOT/frontend/build"' in script
    assert "pnpm dev" not in script
    assert "exec uv run --extra server uvicorn" in script


def test_development_launcher_requests_the_server_extra_for_backend_processes():
    script = (ROOT / "start.sh").read_text(encoding="utf-8")

    assert script.count("uv run --extra server uvicorn") == 2
    assert "uv run --extra server python run_video_localization_worker.py" in script


def _run_wait_for_url_with_fake_clock(tmp_path: Path, *, successes_after: int, timeout: int) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    attempts = tmp_path / "attempts"
    (bin_dir / "curl").write_text(
        "#!/usr/bin/env bash\n"
        f"attempts_file={shlex.quote(str(attempts))}\n"
        "attempts=0\n"
        "[ -f \"$attempts_file\" ] && attempts=$(cat \"$attempts_file\")\n"
        "attempts=$((attempts + 1))\n"
        "printf '%s' \"$attempts\" > \"$attempts_file\"\n"
        f"[ \"$attempts\" -ge {successes_after} ]\n",
        encoding="utf-8",
    )
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for command in (bin_dir / "curl", bin_dir / "sleep"):
        command.chmod(0o755)
    script = (ROOT / "start.sh").read_text(encoding="utf-8")
    function_start = script.index("wait_for_url() {")
    function_end = script.index("\n}\n", function_start) + 3
    wait_function = tmp_path / "wait-for-url.sh"
    wait_function.write_text(script[function_start:function_end], encoding="utf-8")
    command = (
        "log() { printf '%s\\n' \"$*\"; }; warn() { printf '%s\\n' \"$*\" >&2; }; "
        f"source {shlex.quote(str(wait_function))}; "
        f'wait_for_url "http://example.invalid/health" "测试健康检查" {timeout}'
    )
    return subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )


def test_backend_cold_start_wait_accepts_health_after_more_than_thirty_polls(tmp_path: Path):
    result = _run_wait_for_url_with_fake_clock(tmp_path, successes_after=31, timeout=120)

    assert result.returncode == 0
    assert "up (31s)" in result.stdout


def test_backend_cold_start_wait_still_fails_after_its_deadline(tmp_path: Path):
    result = _run_wait_for_url_with_fake_clock(tmp_path, successes_after=99, timeout=3)

    assert result.returncode == 1
    assert "未在 3s 内响应" in result.stderr


def test_backend_cold_start_timeout_defaults_to_two_minutes_and_is_configurable():
    script = (ROOT / "start.sh").read_text(encoding="utf-8")

    assert 'BACKEND_STARTUP_TIMEOUT="${VOICE_STUDIO_BACKEND_STARTUP_TIMEOUT:-120}"' in script
    assert 'wait_for_url "$BACKEND_HEALTH_URL" "后端健康检查" "$BACKEND_STARTUP_TIMEOUT"' in script


def test_windows_launcher_can_forward_to_production_entrypoint():
    script = (ROOT / "start.ps1").read_text(encoding="utf-8")

    assert "[switch]$Production" in script
    assert '"start-production.sh"' in script


def test_windows_ci_only_parses_launcher_without_claiming_wsl_readiness():
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )

    assert "Parser]::ParseFile" in workflow
    assert ".\\start.ps1 -Doctor" not in workflow
