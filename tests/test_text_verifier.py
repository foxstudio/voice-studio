from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import text_verifier  # noqa: E402


def test_verifier_passes_when_transcript_covers_expected_text():
    report = text_verifier.verify_transcript(
        expected_text="大家好，欢迎来到本期内容。今天我们讨论长文本校对。",
        transcript_text="大家好欢迎来到本期内容今天我们讨论长文本校对",
    )

    assert report.status == "passed"
    assert report.coverage >= 0.95
    assert report.missing_segments == []


def test_verifier_fails_when_a_sentence_is_missing():
    report = text_verifier.verify_transcript(
        expected_text="第一句介绍背景。第二句说明关键风险。第三句给出重试建议。",
        transcript_text="第一句介绍背景。第三句给出重试建议。",
    )

    assert report.status == "failed"
    assert any("第二句" in item.expected_text for item in report.missing_segments)


def test_tts_verification_endpoint_accepts_transcript_text_without_asr():
    client = TestClient(app)
    response = client.post(
        "/api/evaluations/tts-verification",
        json={
            "expected_text": "这是一段短文本，用来验证接口是否正常。",
            "transcript_text": "这是一段短文本用来验证接口是否正常",
            "language": "zh",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "passed"
    assert body["transcription_id"] is None


def test_tts_verification_endpoint_requires_text_or_result():
    client = TestClient(app)
    response = client.post("/api/evaluations/tts-verification", json={"transcript_text": "有转录但没有原文"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EXPECTED_TEXT_REQUIRED"
