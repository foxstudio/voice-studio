from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_generation_queue_web.py"
spec = importlib.util.spec_from_file_location("queue_web_harness", SCRIPT)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_all_application_storage_is_owned_by_one_temporary_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_DB_PATH", "/not-the-test-database")
    monkeypatch.setenv("VOICE_STUDIO_OUTPUTS_DIR", "/not-the-test-output")
    environment = harness.isolated_environment(tmp_path)
    for key in ("DATA", "MODELS", "VOICES", "OUTPUTS", "EXPORTS", "PROJECTS", "CACHE", "LOGS"):
        Path(environment[f"VOICE_STUDIO_{key}_DIR"]).relative_to(tmp_path)
    assert environment["VOICE_STUDIO_DB_PATH"] == str(tmp_path / "config" / "voice_studio.db")
    assert environment["VOICE_STUDIO_SERVE_FRONTEND"] == "0"
    assert environment["TMPDIR"] == str(tmp_path / "tmp")


def test_fixture_server_refuses_unowned_directory_before_importing_app(tmp_path):
    with pytest.raises(RuntimeError, match="harness-owned"):
        harness.serve_fixture(tmp_path, 12345)


def test_random_port_never_uses_product_fixed_ports():
    assert harness.available_port() not in {5173, 18000}
