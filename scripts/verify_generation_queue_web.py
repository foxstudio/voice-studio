#!/usr/bin/env python3
"""No-model Web acceptance for the real generation queue and production SPA.

Run with the repository venv after ``cd frontend && pnpm build``. Only provider
health/inference are fixed; application services, database, workers, public
routes, cancellation, audio postprocessing and history remain real. Every
writable path and child process belongs to this invocation and is removed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_dubbing_web_fixture import ROOT, available_port, isolated_environment, stop_process_group


def serve_fixture(root: Path, port: int) -> None:
    if not (root / "queue-web-owner").is_file():
        raise RuntimeError("Refusing to start outside a harness-owned temporary root")
    for name, value in isolated_environment(root).items():
        os.environ[name] = value
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app
    from app.services import engine_health, engine_runner, frontend_distribution

    events: list[dict] = []
    releases: dict[str, threading.Event] = {}
    mutex = threading.Lock()

    def health(engine_id: str) -> dict:
        # Automatic ASR verification sees unavailable models and cannot infer.
        if engine_id != "omnivoice":
            return {"healthy": False, "status": "acceptance_provider_disabled"}
        return {"healthy": True, "status": "fixed_acceptance_provider", "model_path": str(root / "models")}

    def infer(engine_id, kwargs, timeout=900, cancel_check=None, on_tick=None):
        if engine_id != "omnivoice":
            raise RuntimeError("Acceptance harness forbids other inference providers")
        output = Path(kwargs["output_path"]).resolve()
        output.relative_to(root.resolve())
        text = kwargs["text"]
        with mutex:
            release = releases.setdefault(text, threading.Event())
            events.append({"event": "started", "text": text})
        started = time.monotonic()
        while not release.wait(0.05):
            if cancel_check and cancel_check():
                raise RuntimeError("Generation cancelled")
            if time.monotonic() - started > 90:
                raise RuntimeError("Acceptance provider release timed out")
        output.parent.mkdir(parents=True, exist_ok=True)
        rate = 24000
        frames = b"".join(struct.pack("<h", round(4000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate))
        with wave.open(str(output), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(frames)
        with mutex:
            events.append({"event": "completed", "text": text})
        return {"output_path": str(output), "duration_ms": 1000, "sample_rate": rate, "generation_time_ms": round((time.monotonic() - started) * 1000)}

    engine_health.health_check = health
    engine_runner.run_isolated = infer
    from fastapi import Body

    @app.get("/api/__queue_acceptance/state", include_in_schema=False)
    def state():
        with mutex:
            return {"events": list(events)}

    @app.post("/api/__queue_acceptance/release", include_in_schema=False)
    def release(payload: dict = Body(...)):
        with mutex:
            releases.setdefault(str(payload["text"]), threading.Event()).set()
        return {"released": True}

    frontend_distribution.install_frontend_distribution(app, environ={
        "VOICE_STUDIO_SERVE_FRONTEND": "1",
        "VOICE_STUDIO_FRONTEND_DIST": str(ROOT / "frontend" / "build"),
    })
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3)


def run() -> None:
    if not (ROOT / "frontend" / "build" / "index.html").is_file():
        raise RuntimeError("Build the production frontend before Web acceptance")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for the existing Playwright installation")
    with tempfile.TemporaryDirectory(prefix="voice-studio-queue-web-") as temporary:
        root = Path(temporary)
        (root / "queue-web-owner").touch()
        (root / "tmp").mkdir()
        port = available_port()
        if port in {5173, 18000}:
            raise RuntimeError("Refusing a production service port")
        base = f"http://127.0.0.1:{port}"
        environment = isolated_environment(root)
        process = None
        browser_process = None
        with (root / "service.log").open("w+") as service_log:
            try:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve-root", str(root), "--port", str(port)], cwd=ROOT, env=environment, stdout=service_log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 35
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Isolated acceptance service exited during startup")
                    try:
                        with urllib.request.urlopen(f"{base}/api/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Isolated acceptance service did not become ready")
                        time.sleep(0.15)
                browser_process = subprocess.Popen([node, str(ROOT / "scripts" / "verify_generation_queue_browser.mjs")], cwd=ROOT, env={**environment, "GENERATION_QUEUE_E2E_URL": base, "GENERATION_QUEUE_E2E_ARTIFACT_DIR": str(root)}, text=True, start_new_session=True)
                browser_exit = browser_process.wait(timeout=150)
                if browser_exit:
                    raise RuntimeError(f"Web acceptance failed with exit code {browser_exit}")
            except BaseException:
                service_log.flush()
                service_log.seek(0)
                print(service_log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                stop_process_group(browser_process)
                stop_process_group(process)
    assert not root.exists()
    print(json.dumps({"cleanup": "complete", "temporary_root_removed": True, "service_port": port}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.serve_root:
        serve_fixture(arguments.serve_root, arguments.port)
    else:
        run()
