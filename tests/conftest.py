"""Pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path

import pytest


_TEST_STORAGE = tempfile.TemporaryDirectory(
    prefix="voice-studio-pytest-"
)
_TEST_DATA_ROOT = Path(_TEST_STORAGE.name)
_TEST_PATHS = {
    "VOICE_STUDIO_DATA_DIR": _TEST_DATA_ROOT,
    "VOICE_STUDIO_DB_PATH": (
        _TEST_DATA_ROOT / "config" / "voice_studio.db"
    ),
    "VOICE_STUDIO_MODELS_DIR": _TEST_DATA_ROOT / "models",
    "VOICE_STUDIO_VOICES_DIR": _TEST_DATA_ROOT / "voices",
    "VOICE_STUDIO_OUTPUTS_DIR": _TEST_DATA_ROOT / "outputs",
    "VOICE_STUDIO_EXPORTS_DIR": _TEST_DATA_ROOT / "exports",
    "VOICE_STUDIO_PROJECTS_DIR": _TEST_DATA_ROOT / "projects",
    "VOICE_STUDIO_CACHE_DIR": _TEST_DATA_ROOT / "cache",
    "VOICE_STUDIO_LOGS_DIR": _TEST_DATA_ROOT / "logs",
    # 清理会走系统废纸篓；测试重定向到临时目录，否则跑一遍用例就会往用户真实废纸篓
    # 里塞几十个临时文件（ttl-first.wav、history-orphan.wav 之类）。
    "VOICE_STUDIO_TRASH_DIR": _TEST_DATA_ROOT / "trash",
}

# conftest is loaded before test modules. Force every application path away
# from the user's real VoiceStudio directory before app modules capture their
# import-time defaults.
for _environment_name, _path in _TEST_PATHS.items():
    os.environ[_environment_name] = str(_path)


def pytest_configure(config) -> None:
    """Keep pytest's own tmp_path files in the session-owned storage."""

    if config.option.basetemp is None:
        config.option.basetemp = str(_TEST_DATA_ROOT / "pytest-tmp")


def pytest_unconfigure(config) -> None:
    """Remove the session-owned storage even after failed test runs."""

    _TEST_STORAGE.cleanup()


@pytest.fixture
def sample_audio():
    """Generate sample audio data."""
    mx = pytest.importorskip("mlx.core")
    np = pytest.importorskip("numpy")
    # 1 second of audio at 24kHz
    return mx.array(np.random.randn(24000).astype(np.float32))


@pytest.fixture
def sample_mel():
    """Generate sample mel spectrogram."""
    mx = pytest.importorskip("mlx.core")
    np = pytest.importorskip("numpy")
    # (batch, n_mels, time)
    return mx.array(np.random.randn(1, 100, 200).astype(np.float32))


@pytest.fixture
def sample_text_tokens():
    """Generate sample text tokens."""
    mx = pytest.importorskip("mlx.core")
    return mx.array([[100, 200, 300, 400, 500]], dtype=mx.int32)


@pytest.fixture
def small_config():
    """Create a small config for testing."""
    from mlx_indextts.config import IndexTTSConfig, GPTConfig, ConformerConfig

    config = IndexTTSConfig()
    config.gpt.model_dim = 256
    config.gpt.heads = 4
    config.gpt.layers = 2
    config.gpt.max_mel_tokens = 100
    config.gpt.max_text_tokens = 50
    config.gpt.condition_module = ConformerConfig(
        output_size=128,
        attention_heads=4,
        num_blocks=2,
    )
    config.bigvgan.gpt_dim = 256
    config.bigvgan.upsample_initial_channel = 256

    return config
