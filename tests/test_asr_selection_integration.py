from __future__ import annotations

from app.domains.video_localization import operation_queue
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.services.asr_selection_policy import AsrSelection


def test_video_localization_freezes_joint_vibevoice_selection_at_submission(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        operation_queue.asr_selection_policy,
        "select",
        lambda **_kwargs: AsrSelection(
            engine_id="vibevoice-asr-mlx-4bit",
            diarization_engine_id="vibevoice-asr-mlx-4bit",
            mode="auto",
            reason="fixed test selection",
        ),
    )

    normalized = operation_queue._normalized_operation_parameters(
        "english_asr",
        {
            "engine_id": "auto",
            "diarization_engine_id": "moss-transcribe-diarize-mlx",
            "source_track_id": "vocals",
            "source_language": "en",
            "execution_mode": "full",
        },
        VideoLocalizationDraft(),
    )

    assert normalized["engine_id"] == "vibevoice-asr-mlx-4bit"
    assert normalized["diarization_engine_id"] == "vibevoice-asr-mlx-4bit"
