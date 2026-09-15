from __future__ import annotations

from types import SimpleNamespace

from app.services import asr_selection_policy


def test_auto_prefers_installed_4bit_for_multi_speaker_longform(monkeypatch) -> None:
    monkeypatch.setattr(
        asr_selection_policy.settings_store,
        "get",
        lambda: SimpleNamespace(default_asr_engine_id="auto"),
    )
    monkeypatch.setattr(
        asr_selection_policy.vibevoice_model,
        "physical_memory_bytes",
        lambda: 128 * 1024**3,
    )
    monkeypatch.setattr(
        asr_selection_policy.vibevoice_model,
        "installation_status",
        lambda variant: {"installed": variant in {"4bit", "8bit"}},
    )
    monkeypatch.setattr(
        asr_selection_policy.engine_registry,
        "health_check",
        lambda _engine_id: {"healthy": True},
    )

    result = asr_selection_policy.select(
        requested_engine_id="auto",
        needs_speaker_diarization=True,
    )

    assert result.engine_id == "vibevoice-asr-mlx-4bit"
    assert result.diarization_engine_id == "vibevoice-asr-mlx-4bit"


def test_auto_keeps_qwen_for_short_single_speaker_work(monkeypatch) -> None:
    monkeypatch.setattr(
        asr_selection_policy.settings_store,
        "get",
        lambda: SimpleNamespace(default_asr_engine_id="auto"),
    )

    result = asr_selection_policy.select(
        requested_engine_id="auto",
        needs_speaker_diarization=False,
    )

    assert result.engine_id == "qwen3-asr-mlx"
    assert result.diarization_engine_id is None
