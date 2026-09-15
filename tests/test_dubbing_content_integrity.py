from __future__ import annotations

import pytest
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from types import SimpleNamespace

from app.domains.video_localization import dubbing_production as domain
from app.schemas.tts_content import TtsContentEvidence
from app.schemas.video_localization_dubbing_production import (
    DubbingAutomaticAudioEvidence,
    DubbingCandidateCqcInput,
)
from app.services import tts_content_verification as verification


def candidate(expected="请先打开工具。", actual="请先打开工具。", reference="Open the tool."):
    return DubbingCandidateCqcInput(
        source_revision="source", plan_revision=1, group_id="group", candidate_id="candidate",
        task_status="success", artifact_id="audio", audio_sha256="a" * 64,
        expected_spoken_text=expected, reference_transcript=reference,
        candidate_transcript=expected,  # A supplied expected transcript must not mask real evidence.
        content_evidence=TtsContentEvidence(audio_sha256="a" * 64, engine_id="qwen3-asr-mlx",
                                            status="complete", transcript=actual),
        target_start_ms=0, target_end_ms=3000,
        audio=DubbingAutomaticAudioEvidence(duration_ms=2500, peak_dbfs=-3,
              clipping_ratio=0, leading_silence_ms=50, trailing_silence_ms=50),
    )


@pytest.mark.parametrize("expected,actual,reference", [
    ("请先打开工具。", "Open the tool，请先打开工具。", "Open the tool."),
    ("停！", "Stop，停！", "Stop!"),
    ("停！", "Wait!", "Stop!"),
    ("今晚我们去吃晚饭。", "We will eat dinner tonight.", "We will eat dinner tonight."),
    ("将面粉加入碗中。", "Bowl，将面粉加入碗中。", "Add flour to the bowl."),
    ("打开 Blender 调整模型。", "Camera movement，打开 Blender 调整模型。", "Camera movement."),
])
def test_raw_content_differences_never_block_editing_across_content_types(expected, actual, reference):
    report = domain.build_candidate_gap_processing_report(candidate(expected, actual, reference))
    assert report.overall_status == "warning"
    assert report.recommended_action == "listen_and_review"
    assert not any(item.severity == "blocking" for item in report.findings)


@pytest.mark.parametrize("text", [
    "请先打开工具。", "加入面粉和黄油。", "打开 Blender，再让 Claude 修改。", "这里是 2.5D 画面。",
    "Turn the oven off before serving.",
])
def test_normal_and_intended_foreign_names_are_not_rejected(text):
    report = domain.build_candidate_gap_processing_report(candidate(text, text))
    assert report.overall_status == "passed"


def test_small_asr_spelling_difference_is_advisory_not_regeneration():
    report = domain.build_candidate_gap_processing_report(candidate("把这个模型放到右边。", "把这个模形放到右边。"))
    assert report.overall_status != "failed"
    assert report.recommended_action != "regenerate"


@pytest.mark.parametrize("update", [
    {"content_evidence": None},
    {"audio_sha256": "b" * 64},
])
def test_missing_or_stale_independent_evidence_never_falls_back_to_expected_text(update):
    report = domain.build_candidate_gap_processing_report(candidate().model_copy(update=update))
    assert report.overall_status == "passed"
    assert report.recommended_action != "regenerate"
    assert report.transcript.coverage_ratio == 0
    assert not report.findings


def test_changed_target_rechecks_judgment_without_changing_transcription():
    original = candidate()
    assert domain.build_candidate_gap_processing_report(original).overall_status == "passed"
    changed = original.model_copy(update={"expected_spoken_text": "加入面粉和黄油再揉成面团。"})
    assert domain.build_candidate_gap_processing_report(changed).overall_status == "warning"


def test_acquisition_reuses_only_exact_audio_and_never_supplies_expected_text(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixed audio fixture")
    calls = []
    def transcribe(**kwargs):
        calls.append(kwargs)
        return {"text": "独立识别内容", "incomplete_chunk_ranges": []}
    monkeypatch.setattr(verification.asr_service, "transcribe", transcribe)
    first = verification.acquire_content_evidence(audio)
    assert first.status == "complete"
    assert verification.acquire_content_evidence(audio, first) == first
    assert len(calls) == 1
    assert calls[0] == {"engine_id": "qwen3-asr-mlx", "audio_path": str(audio), "language": "auto"}
    audio.write_bytes(b"changed audio fixture")
    second = verification.acquire_content_evidence(audio, first)
    assert second.audio_sha256 != first.audio_sha256
    assert len(calls) == 2


@pytest.mark.parametrize("result,code", [
    ({"text": ""}, "ASR_EMPTY"),
    ({"text": "部分", "incomplete_chunk_ranges": [{"start_ms": 0}]}, "ASR_INCOMPLETE"),
])
def test_empty_or_incomplete_asr_is_unavailable_and_retryable(tmp_path, monkeypatch, result, code):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixed")
    monkeypatch.setattr(verification.asr_service, "transcribe", lambda **kwargs: result)
    evidence = verification.acquire_content_evidence(audio)
    assert evidence.status == "unavailable"
    assert evidence.error_code == code
    payload = candidate().model_copy(update={"audio_sha256": evidence.audio_sha256,
                                             "content_evidence": evidence})
    report = domain.build_candidate_gap_processing_report(payload)
    assert report.recommended_action != "regenerate"


def test_provider_failure_does_not_poison_retry_cache(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixed")
    def fail(**kwargs):
        raise RuntimeError("private server detail")
    monkeypatch.setattr(verification.asr_service, "transcribe", fail)
    evidence = verification.acquire_content_evidence(audio)
    assert evidence.status == "unavailable"
    assert evidence.error_code == "ASR_UNAVAILABLE"
    monkeypatch.setattr(verification.asr_service, "transcribe", lambda **kwargs: {"text": "完整"})
    assert verification.acquire_content_evidence(audio, evidence).status == "complete"


def test_audio_changed_during_asr_is_not_certified(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"before")
    def change(**kwargs):
        audio.write_bytes(b"after")
        return {"text": "完整"}
    monkeypatch.setattr(verification.asr_service, "transcribe", change)
    evidence = verification.acquire_content_evidence(audio)
    assert evidence.error_code == "AUDIO_CHANGED_DURING_ASR"
    assert evidence.status == "unavailable"


def test_missing_file_has_typed_failure_without_model_call(tmp_path, monkeypatch):
    from app.errors import AppException
    monkeypatch.setattr(verification.asr_service, "transcribe",
                        lambda **kwargs: pytest.fail("missing media must not call ASR"))
    with pytest.raises(AppException) as error:
        verification.acquire_content_evidence(tmp_path / "missing.wav")
    assert error.value.code == "TTS_CONTENT_AUDIO_UNAVAILABLE"


def test_file_removed_during_asr_is_unavailable(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"before")
    def remove(**kwargs):
        audio.unlink()
        return {"text": "完整"}
    monkeypatch.setattr(verification.asr_service, "transcribe", remove)
    evidence = verification.acquire_content_evidence(audio)
    assert evidence.status == "unavailable"
    assert evidence.error_code == "AUDIO_UNAVAILABLE_AFTER_ASR"


def test_different_engine_does_not_reuse_observation(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"original")
    calls = []
    def transcribe(**kwargs):
        calls.append(kwargs["engine_id"])
        return {"text": "完整"}
    monkeypatch.setattr(verification.asr_service, "transcribe", transcribe)
    first = verification.acquire_content_evidence(audio)
    verification.acquire_content_evidence(audio, first, engine_id="other-engine")
    assert calls == ["qwen3-asr-mlx", "other-engine"]


@pytest.mark.parametrize("expected,actual", [
    ("让 Claude 打开 Blender。", "让 Cloud 打开 Blender。"),
    ("这里由 OpenAI 提供服务。", "这里由 Open AI 提供服务。"),
    ("打开 API 设置。", "打开 A P I 设置。"),
])
def test_foreign_names_with_ordinary_asr_spelling_are_not_blocked(expected, actual):
    report = domain.build_candidate_gap_processing_report(candidate(expected, actual, expected))
    assert report.overall_status != "failed"


def test_current_media_hashes_each_shared_file_only_once(tmp_path, monkeypatch):
    from app.domains.video_localization import dubbing_media
    audio = tmp_path / "shared.wav"
    audio.write_bytes(b"original")
    monkeypatch.setattr(dubbing_media.media_assets, "managed_project_file", lambda *args: audio)
    calls = []
    def hash_file(path):
        calls.append(path)
        return "a" * 64
    monkeypatch.setattr(dubbing_media.media_assets, "file_sha256", hash_file)
    draft = SimpleNamespace(timeline_clips=[
        {"track_id": "dub", "clip_id": "first", "audio_path": str(audio)},
        {"track_id": "dub", "clip_id": "second", "audio_path": str(audio)},
    ])
    assert dubbing_media.current_timeline_audio_sha256s("project", draft) == {
        "first": "a" * 64, "second": "a" * 64,
    }
    assert calls == [audio]


@pytest.mark.parametrize("content", [None, "Open the tool，请先打开工具。"])
def test_finalization_requires_editing_evidence_not_raw_content_approval(monkeypatch, content):
    from app.domains.video_localization.dubbing_production_service import DubbingProductionApplicationService

    payload = candidate(actual=content or "")
    if content is None:
        payload = payload.model_copy(update={"content_evidence": None})
    timeline = [{"clip_id": "keep-original"}]
    draft = SimpleNamespace(
        dubbing_production=SimpleNamespace(
            active_plan=SimpleNamespace(groups=[SimpleNamespace(group_id="group")]),
            candidate_inputs=[payload],
        ), timeline_clips=timeline,
    )
    service = DubbingProductionApplicationService()
    monkeypatch.setattr(service, "_require_current_project", lambda _: draft)
    # No aligned words: automatic cutting must wait for safe boundary evidence,
    # but raw English must not send the audio into a regeneration loop.
    assert service.finalize_generated_candidate("project", "candidate", "group") == "retryable_failure"
    assert timeline == [{"clip_id": "keep-original"}]


@pytest.mark.parametrize("update", [
    {"audio": None}, {"audio_sha256": None}, {"artifact_id": None}, {"task_status": "failed"},
])
def test_invalid_media_still_blocks_automatic_editing(update):
    report = domain.build_candidate_gap_processing_report(candidate().model_copy(update=update))
    assert report.overall_status == "failed"
    assert any(item.code == "CANDIDATE_AUDIO_EVIDENCE_MISSING" for item in report.findings)
