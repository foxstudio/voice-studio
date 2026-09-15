"""Run the video-localization operation worker without an HTTP server."""

from __future__ import annotations

from app.services.video_localization_operations import (
    run_worker_main,
)


if __name__ == "__main__":
    run_worker_main()
