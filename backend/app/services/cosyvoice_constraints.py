"""Official runtime constraints shared by CosyVoice request paths."""

from __future__ import annotations

from pathlib import Path

from app.services import audio_tools


# CosyVoice-300M v1 Base extracts prompt tokens at 16 kHz and rejects a prompt
# longer than 30 seconds in ``cosyvoice.cli.frontend.Frontend``.
COSYVOICE_ZERO_SHOT_MAX_REFERENCE_DURATION_MS = 30_000


def validate_zero_shot_reference_audio(path: str | Path) -> int:
    """Return the reference duration or raise a stable, user-facing error.

    The validation intentionally happens before a worker is started.  The
    upstream runtime otherwise raises an internal assertion after queueing the
    task, which makes a simple input correction look like a generation error.
    """

    try:
        duration_ms = int(audio_tools.probe_audio(path)["duration_ms"])
    except Exception as exc:
        raise ValueError(
            "COSYVOICE_REFERENCE_AUDIO_INVALID: 无法读取 CosyVoice Zero-Shot 的参考音频，请使用可播放的音频文件。"
        ) from exc
    if duration_ms > COSYVOICE_ZERO_SHOT_MAX_REFERENCE_DURATION_MS:
        raise ValueError(
            "COSYVOICE_REFERENCE_AUDIO_TOO_LONG: "
            "CosyVoice Zero-Shot 官方要求参考音频不超过 30 秒；请在参考音色编辑器中裁到 30 秒以内后再生成。"
        )
    return duration_ms
