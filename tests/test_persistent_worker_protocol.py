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

from app.services import cosyvoice_worker, f5_worker, inference_runner, qwen3_tts_worker
from app.services import persistent_worker as persistent_worker_module
from app.services.persistent_worker import PersistentWorker


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


class _FakeClock:
    def __init__(self, values: list[float]):
        self.values = values
        self.i = 0

    def __call__(self) -> float:
        value = self.values[self.i]
        self.i = min(self.i + 1, len(self.values) - 1)
        return value


def test_idle_shutdown_only_resets_the_current_worker_generation(monkeypatch):
    worker = PersistentWorker(
        log_name="test.log",
        worker_script="",
        error_prefix="test",
        pythonpath_from_root=lambda root: str(root),
        idle_timeout_seconds=60,
    )
    resets: list[str] = []
    monkeypatch.setattr(worker, "_reset_worker", lambda: resets.append("reset"))

    worker._idle_generation = 3
    worker._shutdown_if_idle(2)
    assert resets == []

    worker._shutdown_if_idle(3)
    assert resets == ["reset"]


def test_scheduling_new_idle_shutdown_cancels_previous_timer(monkeypatch):
    created = []

    class FakeTimer:
        def __init__(self, interval, function, args):
            self.interval = interval
            self.function = function
            self.args = args
            self.cancelled = False
            self.started = False
            created.append(self)

        def cancel(self):
            self.cancelled = True

        def start(self):
            self.started = True

    monkeypatch.setattr("app.services.persistent_worker.threading.Timer", FakeTimer)
    worker = PersistentWorker(
        log_name="test.log",
        worker_script="",
        error_prefix="test",
        pythonpath_from_root=lambda root: str(root),
        idle_timeout_seconds=30,
    )

    worker._schedule_idle_shutdown()
    worker._schedule_idle_shutdown()

    assert created[0].cancelled is True
    assert created[1].started is True
    assert created[1].interval == 30


@pytest.mark.parametrize("worker_module", [f5_worker, cosyvoice_worker], ids=["f5", "cosyvoice"])
def test_worker_read_response_returns_json_after_noisy_non_json(worker_module, monkeypatch):
    worker = _FakeWorker('INFO worker bootstrap\n{"ready": true}\n')
    import select as _select_mod
    monkeypatch.setattr(
        _select_mod,
        "select",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("Windows select does not accept subprocess pipes")
        ),
    )
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

    monkeypatch.setattr(worker_module._worker, "_log_path", stderr_path)
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

    monkeypatch.setattr(worker_module._worker, "_log_path", stderr_path)
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
            self.stderr = io.StringIO()
            self.pid = 999

    stderr_path = tmp_path / "worker-ready-stderr.log"
    stderr_path.write_text("ready failed details\n")

    monkeypatch.setattr(worker_module._worker, "_worker", None)
    monkeypatch.setattr(worker_module._worker, "_log_path", stderr_path)
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


def test_worker_log_rotates_with_bounded_backups_and_private_permissions(tmp_path):
    log_path = tmp_path / "worker.log"

    persistent_worker_module._append_rotating_log(
        log_path,
        "a" * 80,
        max_bytes=100,
        backup_count=2,
    )
    persistent_worker_module._append_rotating_log(
        log_path,
        "b" * 30,
        max_bytes=100,
        backup_count=2,
    )
    persistent_worker_module._append_rotating_log(
        log_path,
        "c" * 90,
        max_bytes=100,
        backup_count=2,
    )

    assert log_path.read_text(encoding="utf-8") == "c" * 90
    assert (tmp_path / "worker.log.1").read_text(encoding="utf-8") == "b" * 30
    assert (tmp_path / "worker.log.2").read_text(encoding="utf-8") == "a" * 80
    if sys.platform != "win32":
        # Windows chmod controls the read-only flag, not POSIX permission bits.
        assert log_path.stat().st_mode & 0o777 == 0o600


def test_worker_error_tail_reads_only_the_bounded_file_suffix(tmp_path):
    log_path = tmp_path / "worker.log"
    log_path.write_text("discarded-prefix\n" + "tail-marker-" * 1000, encoding="utf-8")

    tail = persistent_worker_module._read_file_tail(log_path, 128)

    assert len(tail.encode("utf-8")) <= 128
    assert "discarded-prefix" not in tail
    assert tail.endswith("tail-marker-")


def test_worker_drains_large_stderr_without_deadlock_and_bounds_log_files(tmp_path):
    worker = PersistentWorker(
        log_name="pressure.log",
        error_prefix="pressure",
        pythonpath_from_root=lambda root: str(root),
        log_max_bytes=4096,
        log_backup_count=2,
        worker_script=r"""
import json
import sys

print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    payload = json.loads(line)
    sys.stderr.write("diagnostic-block-" * 20000)
    sys.stderr.flush()
    print(json.dumps({"ok": True, "result": payload}), flush=True)
""",
    )

    try:
        result = worker.run(
            {"value": "completed"},
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
    finally:
        worker.shutdown()

    assert result["value"] == "completed"
    logs = sorted((tmp_path / ".voice_studio").glob("pressure.log*"))
    assert logs
    assert len(logs) <= 3
    assert all(path.stat().st_size <= 4096 for path in logs)


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


def test_f5_persistent_worker_uses_the_resolved_device(tmp_path):
    package = tmp_path / "src" / "f5_tts"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "api.py").write_text(
        """
import json
from pathlib import Path


class F5TTS:
    def __init__(self, *, device, **_kwargs):
        self.device = device

    def infer(self, *, file_wave, **_kwargs):
        Path(file_wave).write_text(
            json.dumps({"device": self.device}),
            encoding="utf-8",
        )
""",
        encoding="utf-8",
    )
    output_path = tmp_path / "captured-device.json"
    payload = {
        "output_path": str(output_path),
        "reference_audio": "reference.wav",
        "ref_text": "参考台词",
        "text": "测试文本",
        "device": "cpu",
        "speed": 1.0,
        "nfe_step": 16,
        "cfg_strength": 1.5,
        "target_rms": 0.1,
        "cross_fade_duration": 0.15,
        "sway_sampling_coef": -1.0,
        "fix_duration": None,
        "remove_silence": False,
        "seed": None,
    }

    f5_worker.shutdown()
    try:
        f5_worker.run(payload, root=tmp_path, python=sys.executable, timeout=10)
        second_output = tmp_path / "captured-second-device.json"
        f5_worker.run(
            {**payload, "output_path": str(second_output), "device": "mps"},
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
    finally:
        f5_worker.shutdown()

    assert json.loads(output_path.read_text(encoding="utf-8")) == {"device": "cpu"}
    assert json.loads(second_output.read_text(encoding="utf-8")) == {
        "device": "mps"
    }


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


def _fake_qwen_runtime(root: Path) -> None:
    package = root / "mlx_audio" / "tts"
    package.mkdir(parents=True)
    (root / "mlx_audio" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "utils.py").write_text(
        """
import os
from pathlib import Path


def load_model(model_dir):
    counter = Path(os.environ["QWEN_FAKE_LOAD_COUNTER"])
    current = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(current + 1), encoding="utf-8")
    return {"model_dir": str(model_dir)}
""",
        encoding="utf-8",
    )
    (package / "generate.py").write_text(
        """
import wave
from pathlib import Path


def generate_audio(*, output_path, file_prefix, **_kwargs):
    destination = Path(output_path) / f"{file_prefix}.wav"
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\\x00\\x00" * 2400)
""",
        encoding="utf-8",
    )


def _fake_qwen_model_root(root: Path) -> Path:
    model_root = root / "managed-models"
    (model_root / qwen3_tts_worker.qwen3_tts_paths.CUSTOM_MODEL_DIR).mkdir(
        parents=True
    )
    (model_root / qwen3_tts_worker.qwen3_tts_paths.BASE_MODEL_DIR).mkdir()
    return model_root


def test_qwen3_persistent_worker_reuses_the_loaded_model(tmp_path, monkeypatch):
    _fake_qwen_runtime(tmp_path)
    model_root = _fake_qwen_model_root(tmp_path)
    counter = tmp_path / "load-count.txt"
    monkeypatch.setenv("VOICE_STUDIO_QWEN3_TTS_MODELS_DIR", str(model_root))
    monkeypatch.setenv("QWEN_FAKE_LOAD_COUNTER", str(counter))

    first_output = tmp_path / "first.wav"
    second_output = tmp_path / "second.wav"
    reference_output = tmp_path / "reference.wav"
    repeated_reference_output = tmp_path / "repeated-reference.wav"
    reference_audio = tmp_path / "reference-input.wav"
    reference_audio.write_bytes(b"reference")
    qwen3_tts_worker.shutdown()
    try:
        first = qwen3_tts_worker.run(
            {"text": "第一句", "output_path": str(first_output)},
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
        second = qwen3_tts_worker.run(
            {"text": "第二句", "output_path": str(second_output)},
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
        reference = qwen3_tts_worker.run(
            {
                "text": "切换到复刻模型",
                "output_path": str(reference_output),
                "reference_audio": str(reference_audio),
                "ref_text": "参考台词",
            },
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
        repeated_reference = qwen3_tts_worker.run(
            {
                "text": "继续使用复刻模型",
                "output_path": str(repeated_reference_output),
                "reference_audio": str(reference_audio),
                "ref_text": "参考台词",
            },
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
    finally:
        qwen3_tts_worker.shutdown()

    assert first["sample_rate"] == 24000
    assert second["sample_rate"] == 24000
    assert reference["sample_rate"] == 24000
    assert repeated_reference["sample_rate"] == 24000
    assert first_output.exists()
    assert second_output.exists()
    assert reference_output.exists()
    assert repeated_reference_output.exists()
    # The active variant is reused, while a real model-kind switch reloads
    # exactly once instead of retaining every Qwen variant in memory.
    assert counter.read_text(encoding="utf-8") == "2"


def test_qwen3_one_shot_fallback_uses_the_shared_runtime(tmp_path, monkeypatch):
    _fake_qwen_runtime(tmp_path)
    model_root = _fake_qwen_model_root(tmp_path)
    counter = tmp_path / "one-shot-load-count.txt"
    output = tmp_path / "one-shot.wav"
    monkeypatch.setenv("VOICE_STUDIO_QWEN3_TTS_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_STUDIO_QWEN3_TTS_MODELS_DIR", str(model_root))
    monkeypatch.setenv("QWEN_FAKE_LOAD_COUNTER", str(counter))
    monkeypatch.setattr(
        inference_runner,
        "_external_python",
        lambda _root: sys.executable,
    )

    result = inference_runner.run_qwen3_tts(
        text="一次性备用进程",
        output_path=str(output),
    )

    assert output.exists()
    assert result["sample_rate"] == 24000
    assert counter.read_text(encoding="utf-8") == "1"


def test_worker_protocol_uses_utf8_independent_of_host_encoding(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    monkeypatch.setattr(persistent_worker_module.subprocess, "_text_encoding", lambda: "ascii")
    worker = PersistentWorker(
        log_name="unicode.log",
        error_prefix="unicode",
        pythonpath_from_root=lambda root: str(root),
        worker_script="""
import json
import sys
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"ok": True, "result": request}, ensure_ascii=False), flush=True)
""",
    )
    try:
        text = "\u4e2d\u6587 \U0001f3b5 caf\u00e9"
        result = worker.run({"text": text}, root=tmp_path, python=sys.executable, timeout=10)
        assert result["text"] == text
    finally:
        worker.shutdown()
