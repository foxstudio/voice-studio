from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import model_integrity  # noqa: E402


def test_verify_model_file_hashes_once_and_reuses_stat_bound_manifest(monkeypatch, tmp_path):
    model = tmp_path / "model.safetensors"
    model.write_bytes(b"verified model")
    expected = hashlib.sha256(model.read_bytes()).hexdigest()

    verified, detail = model_integrity.verify_model_file(
        tmp_path,
        model.name,
        expected_size=model.stat().st_size,
        expected_sha256=expected,
        revision="revision-1",
    )

    assert verified is True
    assert detail["cached"] is False
    manifest = json.loads((tmp_path / model_integrity.MANIFEST_NAME).read_text())
    assert manifest["files"][model.name]["sha256"] == expected

    monkeypatch.setattr(model_integrity, "_sha256", lambda path: (_ for _ in ()).throw(AssertionError("rehash")))
    verified, detail = model_integrity.verify_model_file(
        tmp_path,
        model.name,
        expected_size=model.stat().st_size,
        expected_sha256=expected,
        revision="revision-1",
    )

    assert verified is True
    assert detail["cached"] is True


def test_verify_model_file_rejects_size_and_sha_mismatch(tmp_path):
    model = tmp_path / "model.safetensors"
    model.write_bytes(b"partial")

    verified, detail = model_integrity.verify_model_file(
        tmp_path,
        model.name,
        expected_size=99,
        expected_sha256="0" * 64,
        revision="revision-1",
    )
    assert verified is False
    assert detail["status"] == "size_mismatch"

    verified, detail = model_integrity.verify_model_file(
        tmp_path,
        model.name,
        expected_size=model.stat().st_size,
        expected_sha256="0" * 64,
        revision="revision-1",
    )
    assert verified is False
    assert detail["status"] == "sha256_mismatch"


@pytest.mark.parametrize(
    "filename",
    ["../outside.bin", "/tmp/outside.bin", "nested/../../outside.bin", "C:\\outside.bin"],
)
def test_verify_model_file_rejects_paths_outside_model_directory(tmp_path, filename):
    with pytest.raises(ValueError, match="Invalid model filename"):
        model_integrity.verify_model_file(
            tmp_path,
            filename,
            expected_size=1,
            expected_sha256="0" * 64,
            revision="revision-1",
        )


def test_integrity_manifest_merges_multiple_verified_files(tmp_path):
    expected: dict[str, str] = {}
    for filename, content in [("model-a.bin", b"first"), ("nested/model-b.bin", b"second")]:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        expected[filename] = digest

        verified, _ = model_integrity.verify_model_file(
            tmp_path,
            filename,
            expected_size=len(content),
            expected_sha256=digest,
            revision="revision-1",
        )
        assert verified is True

    manifest = json.loads((tmp_path / model_integrity.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {
        filename: entry["sha256"]
        for filename, entry in manifest["files"].items()
    } == expected
