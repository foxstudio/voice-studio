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


def test_verifier_ignores_omnivoice_pause_tags_for_coverage():
    expected = "你问 GPT-3：腿上有几只眼睛？它说：两只。[pause]你再问它：太阳有几只眼睛？它说：一只。"
    transcript = "你问GPT三腿上有几只眼睛它说两只你再问它太阳有几只眼睛它说一只"

    report = text_verifier.verify_transcript(expected_text=expected, transcript_text=transcript)

    assert report.status == "passed"
    assert report.expected_text == expected
    assert "pause" not in report.normalized_expected
    assert report.missing_segments == []


def test_verifier_ignores_known_non_verbal_model_tags():
    expected = "[laughter]这句真的要这么说吗？[question-ha]当然。[dissatisfaction-mm]那就继续。"
    transcript = "这句真的要这么说吗当然那就继续"

    report = text_verifier.verify_transcript(expected_text=expected, transcript_text=transcript)

    assert report.status == "passed"
    assert report.missing_segments == []
    assert "laughter" not in report.normalized_expected
    assert "questionha" not in report.normalized_expected
    assert "dissatisfactionmm" not in report.normalized_expected


def test_verifier_ignores_cosyvoice_control_tags_for_coverage():
    report = text_verifier.verify_transcript(
        expected_text="第一句。<|pause_300|>第二句。<laughter>第三句。",
        transcript_text="第一句第二句第三句",
    )

    assert report.status == "passed"
    assert report.missing_segments == []
    assert "pause300" not in report.normalized_expected
    assert "laughter" not in report.normalized_expected


def test_verifier_keeps_plain_english_pause_word():
    assert text_verifier.normalize_text(text_verifier.strip_verification_control_tags("Please say pause clearly.")) == "pleasesaypauseclearly"


def test_seed_audio_coverage_uses_only_explicit_spoken_lines():
    prompt = """低沉配乐铺底，雨声持续。

旁白（沉稳）说道：“故事开始了。”

音效（近景）有人喊着“抓住她”，随后金属声响起。

男子（压低声音）问：“你听见了吗？”

女子回答：“听见了。”

结尾传来一声“砰”，音乐淡出。"""
    expected = text_verifier.verification_expected_text(
        prompt,
        engine_id=text_verifier.SEED_AUDIO_ENGINE_ID,
    )
    assert expected == "故事开始了。\n你听见了吗？\n听见了。"

    report = text_verifier.verify_transcript(
        expected_text=expected,
        transcript_text="故事开始了。你听见了吗？听见了。",
    )
    assert report.status == "passed"
    assert report.coverage == 1.0


def test_seed_audio_non_speech_prompt_is_not_treated_as_missing_dialogue():
    prompt = "低沉配乐铺底，远处传来雷声，最后逐渐安静。"
    expected = text_verifier.verification_expected_text(
        prompt,
        engine_id=text_verifier.SEED_AUDIO_ENGINE_ID,
    )
    assert expected == ""

    report = text_verifier.skipped_non_speech_report(original_prompt=prompt)
    assert report.status == "skipped"
    assert report.coverage == 0.0
    assert "不适用 ASR 覆盖率" in report.warnings[0]


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
