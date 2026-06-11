from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import LicenseStatus, VoiceAssetCreate, VoiceAssetUpdate  # noqa: E402
from app.services import database as db, voice_store  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path):
    original = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    try:
        yield
    finally:
        db.set_db_path(original)


def test_update_voice_accepts_partial_quality_fields(isolated_db):
    voice = voice_store.create_voice(
        VoiceAssetCreate(
            name="测试音色",
            description="原始描述",
            tags=["仅测试"],
            reference_text="原参考文本",
            reference_audio_ids=["audio-a"],
            license_status=LicenseStatus.self_voice,
        )
    )

    updated = voice_store.update_voice(
        voice.voice_id,
        VoiceAssetUpdate(
            reference_text="ASR 回填文本",
            quality_status="needs_review",
            quality_notes="ASR 回填 reference_text，需人工复核。",
        ),
    )

    assert updated is not None
    assert updated.name == "测试音色"
    assert updated.description == "原始描述"
    assert updated.tags == ["仅测试"]
    assert updated.reference_audio_ids == ["audio-a"]
    assert updated.license_status == LicenseStatus.self_voice
    assert updated.reference_text == "ASR 回填文本"
    assert updated.quality_status == "needs_review"
    assert updated.quality_notes == "ASR 回填 reference_text，需人工复核。"
