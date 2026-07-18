from types import SimpleNamespace

from app.domains.video_localization.audio_access import timeline_clip_audio_path


def test_timeline_clip_audio_path_resolves_stable_media_source_after_split(tmp_path):
    audio_path = tmp_path / "dub.wav"
    audio_path.write_bytes(b"RIFF")
    draft = SimpleNamespace(
        source_media=SimpleNamespace(audio_path=None),
        stems=SimpleNamespace(original_audio_path=None, vocals_clean_path=None, background_path=None),
        timeline_clips=[
            {
                "clip_id": "clip_localized_0001_part_2",
                "media_source_clip_id": "clip_localized_0001",
                "audio_path": str(audio_path),
            }
        ],
    )

    assert timeline_clip_audio_path(draft, "clip_localized_0001") == audio_path
