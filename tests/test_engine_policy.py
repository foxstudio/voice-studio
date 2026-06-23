from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import engine_policy  # noqa: E402


def test_resolve_engine_id_keeps_mimo_legacy_alias():
    assert engine_policy.resolve_engine_id("mimo-v2.5-tts") == "mimo-v2.5-tts-preset"
    assert engine_policy.resolve_engine_id("indextts-v2") == "indextts-v2"


def test_mimo_tts_requires_idempotency_marker():
    assert engine_policy.requires_idempotency_marker("mimo-v2.5-tts-preset") is True
    assert engine_policy.requires_idempotency_marker("mimo-v2.5-tts-voicedesign") is True
    assert engine_policy.requires_idempotency_marker("mimo-v2.5-tts-voiceclone") is True
    assert engine_policy.requires_idempotency_marker("mimo-v2.5-asr") is False
    assert engine_policy.requires_idempotency_marker("indextts-v2") is False


def test_runner_kind_keeps_current_execution_families():
    assert engine_policy.runner_kind_for("mimo-v2.5-tts") == "cloud"
    assert engine_policy.runner_kind_for("f5-tts") == "persistent_worker"
    assert engine_policy.runner_kind_for("cosyvoice-zero-shot") == "persistent_worker"
    assert engine_policy.runner_kind_for("emotivoice") == "external_subprocess"
    assert engine_policy.runner_kind_for("confucius4-mlx-int8") == "external_subprocess"
    assert engine_policy.runner_kind_for("qwen3-asr-mlx") == "asr_local"
    assert engine_policy.runner_kind_for("faster-whisper-turbo") == "asr_local"
    assert engine_policy.runner_kind_for("indextts-v2") == "local"


def test_timeout_policy_matches_current_task_queue_defaults():
    assert engine_policy.timeout_seconds_for("omnivoice") == 600
    assert engine_policy.timeout_seconds_for("indextts-v2") == 420
    assert engine_policy.timeout_seconds_for("confucius4-mlx-int8") == 600
    assert engine_policy.timeout_seconds_for("f5-tts") == 600
    assert engine_policy.timeout_seconds_for("cosyvoice-zero-shot") == 900
    assert engine_policy.timeout_seconds_for("unknown") == 300
