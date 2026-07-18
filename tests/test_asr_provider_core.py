from __future__ import annotations

import subprocess
import sys
import threading
import builtins
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.asr_providers import (  # noqa: E402
    AsrSegment,
    DiarizationSegment,
    ProviderAlreadyRegisteredError,
    ProviderRegistry,
    map_speakers_by_time_overlap,
    normalize_asr_segments,
)
from app.services.asr_providers.providers import moss_mlx  # noqa: E402


def test_contracts_are_frozen():
    segment = AsrSegment(start_ms=0, end_ms=100, text="hello")

    with pytest.raises(FrozenInstanceError):
        segment.text = "changed"  # type: ignore[misc]


def test_registry_supports_isolated_injection_and_replacement():
    first = type("Provider", (), {"provider_id": "fake"})()
    second = type("Provider", (), {"provider_id": "fake"})()
    registry = ProviderRegistry([first])

    assert registry.require("fake") is first
    with pytest.raises(ProviderAlreadyRegisteredError):
        registry.register(second)

    registry.register(second, replace=True)
    assert registry.get("fake") is second
    assert registry.list_provider_ids() == ("fake",)


def test_normalization_converts_milliseconds_sorts_and_preserves_overlap():
    segments = normalize_asr_segments(
        [
            {"start": 2.0, "end": 4.0, "text": " later "},
            {"start": 1.0, "end": 3.0, "text": "first"},
            {"start": 4.0, "end": 5.0, "text": "   "},
            {"start_ms": 1500, "end_ms": 2500, "text": "middle"},
        ]
    )

    assert [(item.start_ms, item.end_ms, item.text) for item in segments] == [
        (1000, 3000, "first"),
        (1500, 2500, "middle"),
        (2000, 4000, "later"),
    ]


def test_normalization_keeps_non_monotonic_end_points_for_qc():
    segments = normalize_asr_segments([{"start": 3.0, "end": 2.5, "text": "raw model output"}])

    assert segments[0].start_ms == 3000
    assert segments[0].end_ms == 2500


def test_speaker_mapping_uses_largest_overlap_for_words_and_segments():
    asr_segments = (
        AsrSegment(0, 800, "word"),
        AsrSegment(800, 2200, "sentence"),
        AsrSegment(3000, 3200, "unmatched", speaker_cluster="manual"),
    )
    diarization = (
        DiarizationSegment(0, 1000, "S01"),
        DiarizationSegment(1000, 2500, "S02"),
    )

    mapped = map_speakers_by_time_overlap(asr_segments, diarization)

    assert [item.speaker_cluster for item in mapped] == ["S01", "S02", "manual"]
    assert [(item.start_ms, item.end_ms) for item in mapped] == [(0, 800), (800, 2200), (3000, 3200)]


def _configured_provider(tmp_path: Path, **kwargs) -> moss_mlx.MossMlxProvider:
    runtime = tmp_path / "moss-python"
    runtime.write_text("#!/bin/sh\n")
    model = tmp_path / "moss-model"
    model.mkdir()
    return moss_mlx.MossMlxProvider(runtime_python=runtime, model_path=model, **kwargs)


def test_moss_health_check_only_inspects_configuration(monkeypatch, tmp_path: Path):
    provider = _configured_provider(tmp_path)
    monkeypatch.setattr(moss_mlx.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("health check started runtime"))
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("moss_transcribe_diarize"):
            pytest.fail("health check imported the external runtime")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    health = provider.health_check()

    assert health["healthy"] is True
    assert health["isolation"] == "external_process"


def test_moss_health_check_reports_missing_env(monkeypatch):
    monkeypatch.delenv(moss_mlx.RUNTIME_ENV, raising=False)
    monkeypatch.delenv(moss_mlx.MODEL_ENV, raising=False)

    assert moss_mlx.MossMlxProvider().health_check()["status"] == "runtime_missing"


def test_moss_configuration_uses_env_and_explicit_values_take_precedence(monkeypatch, tmp_path: Path):
    env_runtime = tmp_path / "env-python"
    env_runtime.write_text("#!/bin/sh\n")
    env_model = tmp_path / "env-model"
    env_model.mkdir()
    explicit_runtime = tmp_path / "explicit-python"
    explicit_runtime.write_text("#!/bin/sh\n")
    explicit_model = tmp_path / "explicit-model"
    explicit_model.mkdir()
    monkeypatch.setenv(moss_mlx.RUNTIME_ENV, str(env_runtime))
    monkeypatch.setenv(moss_mlx.MODEL_ENV, str(env_model))

    from_env = moss_mlx.MossMlxProvider()
    explicit = moss_mlx.MossMlxProvider(runtime_python=explicit_runtime, model_path=explicit_model)

    assert from_env.runtime_python() == str(env_runtime)
    assert from_env.model_path() == env_model
    assert explicit.runtime_python() == str(explicit_runtime)
    assert explicit.model_path() == explicit_model


def test_moss_adapter_runs_mocked_cli_and_parses_segments(tmp_path: Path):
    calls = []

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout):
            return '{"segments": 2}', ""

        def poll(self):
            return 0

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        output_dir = Path(command[command.index("--out-dir") + 1])
        (output_dir / "segments.json").write_text(
            '[{"start": 1.2, "end": 2.4, "speaker": "S02", "text": " second "},'
            '{"start": 0.0, "end": 1.0, "speaker": "S01", "text": "first"}]'
        )
        return FakeProcess()

    provider = _configured_provider(tmp_path, popen_factory=fake_popen)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF")

    asr, diarization = provider.transcribe_and_diarize(str(audio), hotwords=["OpenMOSS"])

    assert asr.text == "first second"
    assert [(item.start_ms, item.end_ms, item.speaker_cluster) for item in asr.segments] == [
        (0, 1000, "S01"),
        (1200, 2400, "S02"),
    ]
    assert diarization.speaker_clusters == ("S01", "S02")
    command = calls[0][0]
    assert command[1:3] == ["-m", moss_mlx.DEFAULT_CLI_MODULE]
    assert "--model" in command and "--out-dir" in command and "--prompt" in command
    assert calls[0][1]["text"] is True


def test_moss_adapter_terminates_on_cancel(tmp_path: Path):
    class WaitingProcess:
        returncode = None
        terminated = False

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("moss", timeout)

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    process = WaitingProcess()
    provider = _configured_provider(tmp_path, popen_factory=lambda *args, **kwargs: process)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF")
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(moss_mlx.MossMlxCancelledError):
        provider.transcribe(str(audio), cancel_event=cancelled)

    assert process.terminated is True


def test_moss_adapter_terminates_on_timeout(monkeypatch, tmp_path: Path):
    class WaitingProcess:
        returncode = None
        terminated = False

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("moss", timeout)

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    process = WaitingProcess()
    clock = iter([10.0, 12.0])
    monkeypatch.setattr(moss_mlx.time, "monotonic", lambda: next(clock))
    provider = _configured_provider(tmp_path, popen_factory=lambda *args, **kwargs: process)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF")

    with pytest.raises(moss_mlx.MossMlxTimeoutError):
        provider.transcribe(str(audio), timeout_seconds=1)

    assert process.terminated is True
