from __future__ import annotations

import threading

import pytest

from app.domains.video_localization import service as video_localization_service
from app.services.keyed_lock_registry import KeyedLockRegistry


def test_keyed_lock_registry_serializes_same_key_and_releases_entry():
    registry = KeyedLockRegistry[str]()
    first_entered = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    order: list[str] = []

    def hold_first() -> None:
        with registry.hold("project-a"):
            order.append("first-enter")
            first_entered.set()
            assert release_first.wait(timeout=1)
            order.append("first-exit")

    def hold_second() -> None:
        assert first_entered.wait(timeout=1)
        second_started.set()
        with registry.hold("project-a"):
            order.append("second-enter")

    first = threading.Thread(target=hold_first)
    second = threading.Thread(target=hold_second)
    first.start()
    second.start()

    assert first_entered.wait(timeout=1)
    assert second_started.wait(timeout=1)
    assert registry.active_key_count == 1
    assert order == ["first-enter"]

    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert order == ["first-enter", "first-exit", "second-enter"]
    assert registry.active_key_count == 0


def test_keyed_lock_registry_does_not_serialize_distinct_keys():
    registry = KeyedLockRegistry[str]()
    second_entered = threading.Event()

    def hold_second_key() -> None:
        with registry.hold("project-b"):
            second_entered.set()

    with registry.hold("project-a"):
        second = threading.Thread(target=hold_second_key)
        second.start()
        assert second_entered.wait(timeout=1)
        second.join(timeout=1)

    assert not second.is_alive()
    assert registry.active_key_count == 0


def test_keyed_lock_registry_releases_entries_after_errors_and_key_churn():
    registry = KeyedLockRegistry[str]()

    with pytest.raises(RuntimeError, match="failed"):
        with registry.hold("failed-project"):
            raise RuntimeError("failed")

    for index in range(1_000):
        with registry.hold(f"project-{index}"):
            assert registry.active_key_count == 1

    assert registry.active_key_count == 0


def test_video_localization_index_lock_registries_release_idle_project_ids():
    timeline_registry = (
        video_localization_service._TIMELINE_AUDIO_INDEX_LOCKS
    )
    media_registry = video_localization_service._PROJECT_MEDIA_INDEX_LOCKS
    for index in range(1_000):
        project_id = f"project-{index}"
        with video_localization_service._timeline_audio_index_lock(project_id):
            pass
        with video_localization_service._project_media_index_lock(project_id):
            pass

    assert timeline_registry.active_key_count == 0
    assert media_registry.active_key_count == 0
