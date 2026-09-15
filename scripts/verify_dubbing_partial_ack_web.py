#!/usr/bin/env python3
"""Real browser partial-save race acceptance, using isolated fixed media only."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from verify_dubbing_web_fixture import ROOT, api, available_port, isolated_environment, stop_process_group


def run():
    with tempfile.TemporaryDirectory(prefix="voice-studio-partial-ack-web-") as temporary:
        root = Path(temporary)
        (root / "dubbing-web-owner").touch()
        (root / "tmp").mkdir()
        port = available_port()
        if port in {5173, 18000}:
            raise RuntimeError("Production ports are forbidden")
        base = f"http://127.0.0.1:{port}"
        process = browser = None
        with (root / "service.log").open("w+") as log:
            try:
                process = subprocess.Popen([sys.executable, str(ROOT / "scripts/verify_dubbing_web_fixture.py"),
                    "--serve-root", str(root), "--port", str(port)], cwd=ROOT, env=isolated_environment(root),
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 40
                while True:
                    try:
                        api(base, "/health")
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Isolated server startup failed")
                        time.sleep(.15)
                project = api(base, "/__content_acceptance/state")["project_id"]
                browser = subprocess.Popen([shutil.which("node") or "node",
                    str(ROOT / "scripts/verify_dubbing_partial_ack_browser.mjs")], cwd=ROOT,
                    env={**isolated_environment(root), "DUBBING_PARTIAL_ACK_URL": base,
                         "DUBBING_PARTIAL_ACK_PROJECT": project, "DUBBING_PARTIAL_ACK_ARTIFACT_DIR": str(root)},
                    start_new_session=True)
                if browser.wait(timeout=90):
                    raise RuntimeError("Partial acknowledgement browser acceptance failed")
                state = api(base, "/__content_acceptance/state")
                assert state["asr_calls"] == state["generated"] == 0, state
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-3000:], file=sys.stderr)
                raise
            finally:
                stop_process_group(browser)
                stop_process_group(process)
    assert not root.exists()
    print(json.dumps({"cleanup": "complete", "temporary_root_removed": True, "port": port}))


if __name__ == "__main__":
    run()
