"""Concurrent media adoption must not share or unlink another writer's temp file."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.domains.video_localization import media_assets


def test_same_audio_concurrent_adoption_uses_independent_temporary_files(tmp_path, monkeypatch):
    package = tmp_path / "project"
    package.mkdir()
    source = tmp_path / "source.wav"
    source.write_bytes(b"fixed media bytes\x00" * 1000)
    monkeypatch.setattr(media_assets, "ensure_project_video_localization_dir", lambda _project_id: package)
    copy = media_assets.shutil.copy2
    both_copied = Barrier(2)
    temporary_paths = []

    def copy_before_either_publish(source_path, temporary_path):
        result = copy(source_path, temporary_path)
        temporary_paths.append(temporary_path)
        # Both real file copies finish before either adopt can publish. The
        # old shared .tmp then lets the first rename steal the second's file.
        both_copied.wait(timeout=5)
        return result

    monkeypatch.setattr(media_assets.shutil, "copy2", copy_before_either_publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(media_assets.adopt_tts_audio, "project", source, "cue", "task") for _ in range(2)]
        results = [future.result(timeout=10) for future in pending]

    assert len(temporary_paths) == 2
    assert temporary_paths[0] != temporary_paths[1]
    assert results[0] == results[1]
    assert results[0].read_bytes() == source.read_bytes()
    assert list(results[0].parent.iterdir()) == [results[0]]
