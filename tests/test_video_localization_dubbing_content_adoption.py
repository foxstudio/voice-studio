"""Editable history import is independent from transcript quality checks."""

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import service
from app.errors import AppException
from app.schemas.tts_content import CONTENT_ASR_ENGINE, CONTENT_ASR_PROTOCOL, TtsContentEvidence
from app.schemas.voice_studio import HistoryItem, VideoLocalizationDraft, VideoLocalizationSubtitleCue
from app.services import database, history_store, tts_content_verification


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    original_db = database.DB_PATH
    database.set_db_path(tmp_path / "history.db")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixed test media")
    item = HistoryItem(
        result_id="result", task_id="task", engine_id="omnivoice", project_id="project",
        segment_id="subtitle", localized_subtitle_id="subtitle", input_text="风格变了。",
        parameter_snapshot={"source": "video_localization", "ref_text": "Camera movement"},
        output_path=str(audio), duration_ms=1000,
    )
    history_store.add(item)
    old_clip = {"clip_id": "old", "track_id": "dub", "subtitle_id": "subtitle", "dub_lane": 0,
                "start_ms": 0, "end_ms": 1000, "audio_path": str(audio), "status": "ready"}
    current = [VideoLocalizationDraft(
        localized_subtitles=[VideoLocalizationSubtitleCue(
            subtitle_id="subtitle", start_ms=0, end_ms=1000, text=item.input_text,
        )], timeline_clips=[old_clip],
    )]
    monkeypatch.setattr(service.project_store, "get_project", lambda _: SimpleNamespace())
    monkeypatch.setattr(service.media_assets, "adopt_tts_audio", lambda *_: audio)

    def update(_project_id, apply, **_kwargs):
        current[0] = apply(current[0])
        return current[0]

    def no_asr(*_args, **_kwargs):
        raise AssertionError("importing editable media must not run content ASR")

    monkeypatch.setattr(service, "update_video_localization_atomic", update)
    monkeypatch.setattr(tts_content_verification, "acquire_content_evidence", no_asr)
    try:
        yield item, audio, current, old_clip
    finally:
        database.set_db_path(original_db)


@pytest.mark.parametrize("transcript", [None, "", "Camera movement 风格变了。", "完全不同的台词"])
@pytest.mark.parametrize("force_new", [False, True])
def test_history_can_be_imported_for_editing_without_content_approval(workspace, transcript, force_new):
    item, audio, current, old_clip = workspace
    if transcript is not None:
        evidence = TtsContentEvidence(
            audio_sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
            engine_id=CONTENT_ASR_ENGINE, protocol=CONTENT_ASR_PROTOCOL,
            status="complete" if transcript else "unavailable", transcript=transcript,
        )
        history_store.add(item.model_copy(update={"content_evidence": evidence}))
    before_evidence = history_store.get(item.result_id).content_evidence
    kwargs = {"request_id": "import", **({"force_new": True, "new_clip_id": "new"} if force_new else {"clip_id": "old"})}
    updated = service.apply_tts_history_to_timeline(
        item.project_id, item.result_id, segment_id="subtitle", **kwargs,
    )
    target_id = "new" if force_new else "old"
    adopted = next(clip for clip in updated.timeline_clips if clip["clip_id"] == target_id)
    assert adopted["result_id"] == item.result_id
    assert adopted["audio_path"] == str(audio)
    assert history_store.get(item.result_id).content_evidence == before_evidence
    if force_new:
        assert next(clip for clip in updated.timeline_clips if clip["clip_id"] == "old") == old_clip
        repeated = service.apply_tts_history_to_timeline(
            item.project_id, item.result_id, segment_id="subtitle", **kwargs,
        )
        assert repeated.timeline_clips == updated.timeline_clips
    assert current[0] == updated


def test_history_import_still_rejects_another_project(workspace):
    item, _, current, _ = workspace
    before = current[0]
    with pytest.raises(AppException) as error:
        service.apply_tts_history_to_timeline("other-project", item.result_id, request_id="import", segment_id="subtitle")
    assert error.value.code == "VIDEO_LOCALIZATION_TTS_HISTORY_PROJECT_MISMATCH"
    assert current[0] is before


def test_history_import_still_rejects_missing_audio(workspace, monkeypatch):
    item, _, current, _ = workspace
    before = current[0]
    monkeypatch.setattr(history_store, "audio_path", lambda _: None)
    with pytest.raises(AppException) as error:
        service.apply_tts_history_to_timeline(item.project_id, item.result_id, request_id="import", segment_id="subtitle")
    assert error.value.code == "VIDEO_LOCALIZATION_TTS_HISTORY_NOT_FOUND"
    assert current[0] is before
