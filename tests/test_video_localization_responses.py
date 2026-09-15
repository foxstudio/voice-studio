from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.video_localization_responses import audio_file_response  # noqa: E402
from app.services.media_types import browser_audio_media_type  # noqa: E402


def test_browser_audio_media_type_uses_container_mime_for_m4a(tmp_path: Path):
    path = tmp_path / "preview.m4a"
    path.write_bytes(b"m4a")

    response = audio_file_response(path, code="MISSING", message="missing")

    assert browser_audio_media_type(path) == "audio/mp4"
    assert response.media_type == "audio/mp4"
    assert "content-disposition" not in response.headers
