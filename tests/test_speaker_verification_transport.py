from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import speaker_verification_service


def _install_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str) -> Path:
    worker = tmp_path / "speaker_verification_worker.py"
    worker.write_text(source, encoding="utf-8")
    monkeypatch.setattr(speaker_verification_service, "__file__", str(tmp_path / "speaker_verification_service.py"))
    return worker


def _extract(
    clips: list[Path],
    *,
    timeout: float = 2,
    cancel_check=None,
) -> tuple:
    return speaker_verification_service._extract_embeddings(
        clips,
        python=Path(sys.executable),
        model_path=Path("/unused-model"),
        timeout=timeout,
        cancel_check=cancel_check,
    )


def _assert_process_reaped(pid_path: Path) -> None:
    pid = int(pid_path.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _run_bounded_transport_probe(tmp_path: Path) -> dict:
    """Keep the old pipe deadlock contained in a disposable process group."""
    root = tmp_path / "transport-probe"
    root.mkdir()
    pid_path = root / "worker.pid"
    worker_source = f'''
import json
import os
import sys
from pathlib import Path

Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
sys.stderr.write("worker startup failure detail\\n" * 100000)
sys.stderr.flush()
payload = json.load(sys.stdin)
print(json.dumps({{"embeddings": [[0.0] * 192 for _ in payload["clips"]], "metadata": {{"transport": "files"}}}}))
'''
    probe = f'''
import json
import sys
from pathlib import Path

from app.services import speaker_verification_service

root = Path({str(root)!r})
worker = root / "speaker_verification_worker.py"
worker.write_text({worker_source!r}, encoding="utf-8")
speaker_verification_service.__file__ = str(root / "speaker_verification_service.py")
huge_suffix = "x" * (128 * 1024)
clips = [root / f"clip-{{index}}-{{huge_suffix}}.wav" for index in range(8)]
embeddings, metadata = speaker_verification_service._extract_embeddings(
    clips,
    python=Path(sys.executable),
    model_path=root / "unused-model",
    timeout=2,
    cancel_check=None,
)
print(json.dumps({{"shape": list(embeddings.shape), "metadata": metadata}}))
'''
    environment = {**os.environ, "PYTHONPATH": str(BACKEND)}
    process = subprocess.Popen(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=4)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail("CAM++ transport probe exceeded its outer timeout")
    assert process.returncode == 0, stderr
    return json.loads(stdout.splitlines()[-1])


def test_large_stdin_and_early_stderr_complete_within_bounded_probe(tmp_path: Path):
    probe = _run_bounded_transport_probe(tmp_path)

    assert probe == {
        "shape": [8, 192],
        "metadata": {"transport": "files"},
    }


def test_nonzero_worker_exit_reports_stderr(monkeypatch, tmp_path: Path):
    _install_worker(
        monkeypatch,
        tmp_path,
        """
import sys

sys.stderr.write("CAM++ test worker failed\\n")
sys.stderr.flush()
raise SystemExit(7)
""",
    )

    with pytest.raises(RuntimeError, match="CAM\\+\\+ test worker failed"):
        _extract([tmp_path / "clip.wav"])


def test_timeout_terminates_and_reaps_worker(monkeypatch, tmp_path: Path):
    pid_path = tmp_path / "timeout-worker.pid"
    _install_worker(
        monkeypatch,
        tmp_path,
        f"""
import os
import time
from pathlib import Path

Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(30)
""",
    )
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="timed out"):
        _extract([tmp_path / "clip.wav"], timeout=0.15)

    assert time.monotonic() - started < 3
    _assert_process_reaped(pid_path)


def test_cancel_terminates_and_reaps_worker(monkeypatch, tmp_path: Path):
    pid_path = tmp_path / "cancel-worker.pid"
    _install_worker(
        monkeypatch,
        tmp_path,
        f"""
import os
import time
from pathlib import Path

Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(30)
""",
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        _extract([tmp_path / "clip.wav"], timeout=0.5, cancel_check=pid_path.exists)

    _assert_process_reaped(pid_path)
