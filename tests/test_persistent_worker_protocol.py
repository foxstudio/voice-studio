from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import cosyvoice_worker, f5_worker


def _fake_cosyvoice_runtime(root: Path) -> None:
    """Install the smallest importable CosyVoice surface for worker tests."""
    package = root / "cosyvoice" / "cli"
    package.mkdir(parents=True)
    (root / "cosyvoice" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (root / "torch.py").write_text("", encoding="utf-8")
    (root / "torchaudio.py").write_text("", encoding="utf-8")
    (package / "cosyvoice.py").write_text(
        """
class AutoModel:
    def __init__(self, **_kwargs):
        pass

    def list_available_spks(self):
        return ["中文女", "中文男"]
""",
        encoding="utf-8",
    )


class _FakeWorker:
    def __init__(self, stdout: str, poll_values: list[int | None] | None = None):
        self.stdout = io.StringIO(stdout)
        self._poll_values = list(poll_values or [None])
        self._poll_index = 0

    def poll(self) -> int | None:
        if not self._poll_values:
            return None
        if self._poll_index < len(self._poll_values):
            value = self._poll_values[self._poll_index]
            self._poll_index += 1
            return value
        return self._poll_values[-1]


class _FakeLogHandle:
    def __init__(self, path: Path):
        self.name = str(path)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeClock:
    def __init__(self, values: list[float]):
        self.values = values
        self.i = 0

    def __call__(self) -> float:
        value = self.values[self.i]
        self.i = min(self.i + 1, len(self.values) - 1)
        return value


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_read_response_returns_json_after_noisy_non_json(worker_module, monkeypatch):
    worker = _FakeWorker('INFO worker bootstrap\n{"ready": true}\n')
    import select as _select_mod
    monkeypatch.setattr(_select_mod, "select", lambda rlist, wlist, xlist, timeout: (rlist, [], []))
    result = worker_module._worker._read_response(
        worker=worker,
        timeout=10,
        started=time.monotonic(),
        cancel_check=None,
        on_tick=None,
    )
    assert result == {"ready": True}


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_read_response_tail_included_on_exit(worker_module, tmp_path, monkeypatch):
    worker = _FakeWorker("WARN unexpected model message\n", poll_values=[None, 1])
    stderr_path = tmp_path / "worker-stderr.log"
    stderr_path.write_text("stderr from worker\n")

    monkeypatch.setattr(worker_module._worker, "_log_handle", _FakeLogHandle(stderr_path))
    import select as _select_mod
    monkeypatch.setattr(_select_mod, "select", lambda rlist, wlist, xlist, timeout: (rlist, [], []))

    with pytest.raises(RuntimeError, match="unexpected model message") as exc:
        worker_module._worker._read_response(
            worker=worker,
            timeout=10,
            started=time.monotonic(),
            cancel_check=None,
            on_tick=None,
        )

    assert "stdout tail" in str(exc.value)
    assert "stderr from worker" in str(exc.value)


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_read_response_timeout_includes_tail(worker_module, tmp_path, monkeypatch):
    worker = _FakeWorker("first noisy line\n")
    stderr_path = tmp_path / "worker-timeout-stderr.log"
    stderr_path.write_text("timeout stderr trace\n")
    clock = _FakeClock([0.0, 0.2, 1.5])

    monkeypatch.setattr(worker_module._worker, "_log_handle", _FakeLogHandle(stderr_path))
    monkeypatch.setattr(time, "monotonic", clock)
    import select as _select_mod
    monkeypatch.setattr(_select_mod, "select", lambda rlist, wlist, xlist, timeout: (rlist, [], []))

    with pytest.raises(RuntimeError, match="timed out after 1s") as exc:
        worker_module._worker._read_response(
            worker=worker,
            timeout=1,
            started=0.0,
            cancel_check=None,
            on_tick=None,
        )
    assert "first noisy line" in str(exc.value)
    assert "timeout stderr trace" in str(exc.value)


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_ready_false_includes_tail(worker_module, tmp_path, monkeypatch):
    class _FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO()
            self.pid = 999

    stderr_path = tmp_path / "worker-ready-stderr.log"
    stderr_path.write_text("ready failed details\n")

    monkeypatch.setattr(worker_module._worker, "_worker", None)
    monkeypatch.setattr(worker_module._worker, "_log_handle", _FakeLogHandle(stderr_path))
    monkeypatch.setattr(worker_module._worker, "_safe_stderr_tail", lambda _worker: "ready failed details")
    import subprocess as _subprocess_mod
    monkeypatch.setattr(_subprocess_mod, "Popen", lambda *args, **kwargs: _FakePopen())
    monkeypatch.setattr(worker_module._worker, "_read_response", lambda *args, **kwargs: {"ready": False, "error": ""})
    monkeypatch.setattr(worker_module._worker, "_reset_worker", lambda: None)

    with pytest.raises(RuntimeError, match="failed to start") as exc:
        worker_module._worker._ensure_worker(
            root=tmp_path,
            python=str(sys.executable),
            timeout=10,
            started=0.0,
            cancel_check=None,
            on_tick=None,
        )
    assert "ready failed details" in str(exc.value)
    assert "stderr tail" in str(exc.value)


def test_f5_persistent_worker_preserves_zero_sway_sampling_coefficient(tmp_path):
    """A zero sway value must reach F5 instead of becoming the -1 default."""
    package = tmp_path / "src" / "f5_tts"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "api.py").write_text(
        """
import json
from pathlib import Path


class F5TTS:
    def __init__(self, **_kwargs):
        pass

    def infer(self, *, file_wave, sway_sampling_coef, **_kwargs):
        Path(file_wave).write_text(
            json.dumps({"sway_sampling_coef": sway_sampling_coef}),
            encoding="utf-8",
        )
""",
        encoding="utf-8",
    )
    output_path = tmp_path / "captured-sway.json"
    payload = {
        "output_path": str(output_path),
        "reference_audio": "reference.wav",
        "ref_text": "参考台词",
        "text": "测试文本",
        "speed": 1.0,
        "nfe_step": 16,
        "cfg_strength": 1.5,
        "target_rms": 0.1,
        "cross_fade_duration": 0.15,
        "sway_sampling_coef": 0.0,
        "fix_duration": None,
        "remove_silence": False,
        "seed": None,
    }

    f5_worker.shutdown()
    try:
        result = f5_worker.run(payload, root=tmp_path, python=sys.executable, timeout=10)
    finally:
        f5_worker.shutdown()

    assert result["output_path"] == str(output_path)
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"sway_sampling_coef": 0.0}


def test_f5_persistent_worker_forwards_every_visible_parameter(tmp_path):
    """Exercise the real long-lived worker script, not only the fallback runner."""
    package = tmp_path / "src" / "f5_tts"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "api.py").write_text(
        """
import json
from pathlib import Path


class F5TTS:
    def __init__(self, **_kwargs):
        pass

    def infer(self, *, file_wave, **kwargs):
        wanted = {
            key: kwargs[key]
            for key in (
                "ref_file", "ref_text", "gen_text", "speed", "nfe_step",
                "cfg_strength", "target_rms", "cross_fade_duration",
                "sway_sampling_coef", "fix_duration", "remove_silence", "seed",
            )
        }
        Path(file_wave).write_text(json.dumps(wanted), encoding="utf-8")
""",
        encoding="utf-8",
    )
    output_path = tmp_path / "captured-f5.json"
    payload = {
        "output_path": str(output_path),
        "reference_audio": "reference.wav",
        "ref_text": "参考台词",
        "text": "测试文本",
        "speed": 1.35,
        "nfe_step": 44,
        "cfg_strength": 3.2,
        "target_rms": 0.22,
        "cross_fade_duration": 0.3,
        "sway_sampling_coef": 0.4,
        "fix_duration": 12.5,
        "remove_silence": True,
        "seed": 12345,
    }

    f5_worker.shutdown()
    try:
        f5_worker.run(payload, root=tmp_path, python=sys.executable, timeout=10)
    finally:
        f5_worker.shutdown()

    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "ref_file": "reference.wav",
        "ref_text": "参考台词",
        "gen_text": "测试文本",
        "speed": 1.35,
        "nfe_step": 44,
        "cfg_strength": 3.2,
        "target_rms": 0.22,
        "cross_fade_duration": 0.3,
        "sway_sampling_coef": 0.4,
        "fix_duration": 12.5,
        "remove_silence": True,
        "seed": 12345,
    }


def test_cosyvoice_persistent_worker_rejects_unknown_sft_speaker(tmp_path):
    _fake_cosyvoice_runtime(tmp_path)
    cosyvoice_worker.shutdown()
    try:
        with pytest.raises(RuntimeError, match="COSYVOICE_SPEAKER_NOT_FOUND") as exc:
            cosyvoice_worker.run(
                "cosyvoice-sft",
                {
                    "output_path": str(tmp_path / "should-not-exist.wav"),
                    "text": "这是一段测试文本。",
                    "speaker_id": "不存在的音色",
                    "speed": 1.0,
                },
                root=tmp_path,
                python=sys.executable,
                timeout=10,
            )
    finally:
        cosyvoice_worker.shutdown()

    message = str(exc.value)
    assert "请从音色列表中重新选择" in message
    assert "中文女、中文男" in message


def test_cosyvoice_persistent_worker_forwards_sft_and_zero_shot_inputs(tmp_path):
    """Verify both real persistent-worker routes retain their visible inputs."""
    package = tmp_path / "cosyvoice" / "cli"
    package.mkdir(parents=True)
    (tmp_path / "cosyvoice" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "torch.py").write_text(
        """
class _Mps:
    def empty_cache(self):
        pass

mps = _Mps()

def cat(chunks, dim=1):
    return chunks[0]
""",
        encoding="utf-8",
    )
    (tmp_path / "torchaudio.py").write_text(
        """
import json
from pathlib import Path

def save(path, speech, sample_rate):
    Path(path).write_text(json.dumps({"sample_rate": sample_rate, **speech.payload}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    (package / "cosyvoice.py").write_text(
        """
class Tensor:
    def __init__(self, payload):
        self.payload = payload
        self.ndim = 1
    def detach(self):
        return self
    def cpu(self):
        return self
    def unsqueeze(self, _axis):
        return self

class AutoModel:
    sample_rate = 22050
    def __init__(self, **_kwargs):
        pass
    def list_available_spks(self):
        return ["中文女", "中文男"]
    def inference_sft(self, text, speaker_id, *, stream, speed):
        yield {"tts_speech": Tensor({"route": "sft", "text": text, "speaker_id": speaker_id, "stream": stream, "speed": speed})}
    def inference_zero_shot(self, text, ref_text, reference_audio, *, stream, speed):
        yield {"tts_speech": Tensor({"route": "zero_shot", "text": text, "ref_text": ref_text, "reference_audio": reference_audio, "stream": stream, "speed": speed})}
""",
        encoding="utf-8",
    )
    sft_output = tmp_path / "sft.json"
    zero_output = tmp_path / "zero.json"
    cosyvoice_worker.shutdown()
    try:
        cosyvoice_worker.run(
            "cosyvoice-sft",
            {"output_path": str(sft_output), "text": "SFT 测试", "speaker_id": "中文男", "speed": 1.25},
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
        cosyvoice_worker.run(
            "cosyvoice-zero-shot",
            {"output_path": str(zero_output), "text": "复刻测试", "reference_audio": "reference.wav", "ref_text": "参考台词", "speed": 0.85},
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
    finally:
        cosyvoice_worker.shutdown()

    assert json.loads(sft_output.read_text(encoding="utf-8")) == {
        "sample_rate": 22050,
        "route": "sft",
        "text": "SFT 测试",
        "speaker_id": "中文男",
        "stream": False,
        "speed": 1.25,
    }
    assert json.loads(zero_output.read_text(encoding="utf-8")) == {
        "sample_rate": 22050,
        "route": "zero_shot",
        "text": "复刻测试",
        "ref_text": "参考台词",
        "reference_audio": "reference.wav",
        "stream": False,
        "speed": 0.85,
    }
