#!/usr/bin/env python3
"""Verify reference editor route reentry on an isolated real application (no ASR/TTS)."""
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave

from verify_dubbing_web_fixture import ROOT, api, available_port, isolated_environment, stop_process_group


def run():
    with tempfile.TemporaryDirectory(prefix="voice-reference-reentry-") as temporary:
        root = Path(temporary).resolve()
        (root / "tmp").mkdir()
        environment = isolated_environment(root)
        dist = os.environ.get("VOICE_STUDIO_FRONTEND_DIST")
        if not dist:
            (root / "node_modules").symlink_to(ROOT / "frontend/node_modules", target_is_directory=True)
            dist = str(root / "dist")
            subprocess.run(
                ["pnpm", "build"], cwd=ROOT / "frontend", check=True,
                env={**environment, "VOICE_STUDIO_SVELTE_OUT_DIR": str(root / "kit"),
                     "VOICE_STUDIO_FRONTEND_DIST": dist}, stdout=subprocess.DEVNULL,
            )
        with wave.open(str(root / "sample.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", int(2000 * math.sin(i * 440 * math.tau / 16000))) for i in range(160000)))
        port = available_port()
        base = f"http://127.0.0.1:{port}"
        service = browser = None
        with (root / "service.log").open("w+") as log:
            try:
                service = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                    cwd=ROOT, env={**environment, "VOICE_STUDIO_SERVE_FRONTEND": "1", "VOICE_STUDIO_FRONTEND_DIST": dist},
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
                deadline = time.monotonic() + 60
                while True:
                    try:
                        api(base, "/health")
                        break
                    except OSError:
                        if service.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Isolated application did not start")
                        time.sleep(.2)
                browser = subprocess.Popen(
                    [shutil.which("node") or "node", str(ROOT / "scripts/verify_reference_editor_reentry_browser.mjs")],
                    cwd=ROOT, env={**environment, "REFERENCE_EDITOR_URL": base, "REFERENCE_EDITOR_ROOT": str(root)},
                    start_new_session=True,
                )
                if browser.wait(timeout=120):
                    raise RuntimeError("Reference editor browser regression failed")
            finally:
                stop_process_group(browser)
                stop_process_group(service)
    assert not root.exists()
    print("Reference editor regression passed; isolated data cleaned.")


if __name__ == "__main__":
    run()
