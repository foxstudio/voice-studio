from __future__ import annotations

import pytest

from app.services import runtime_roles


def test_video_localization_worker_defaults_to_embedded() -> None:
    assert (
        runtime_roles.video_localization_worker_mode({})
        == "embedded"
    )
    assert (
        runtime_roles
        .embedded_video_localization_worker_enabled({})
        is True
    )


def test_video_localization_worker_accepts_external_role() -> None:
    environ = {
        runtime_roles.VIDEO_LOCALIZATION_WORKER_MODE_ENV:
            " external "
    }
    assert (
        runtime_roles.video_localization_worker_mode(environ)
        == "external"
    )
    assert (
        runtime_roles
        .embedded_video_localization_worker_enabled(environ)
        is False
    )


def test_video_localization_worker_rejects_unknown_role() -> None:
    with pytest.raises(
        RuntimeError,
        match=(
            runtime_roles
            .VIDEO_LOCALIZATION_WORKER_MODE_ENV
        ),
    ):
        runtime_roles.video_localization_worker_mode(
            {
                runtime_roles
                .VIDEO_LOCALIZATION_WORKER_MODE_ENV:
                    "reload"
            }
        )
