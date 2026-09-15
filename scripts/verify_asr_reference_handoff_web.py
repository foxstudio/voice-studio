#!/usr/bin/env python3
"""ASR reference handoff round trips against an owned fixed-media service."""
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
    with tempfile.TemporaryDirectory(prefix="voice-studio-reference-web-") as temporary:
        root = Path(temporary)
        (root / "dubbing-web-owner").touch()
        (root / "tmp").mkdir()
        port = available_port()
        assert port not in {5173, 18000, 65335}
        base = f"http://127.0.0.1:{port}"
        process = browser = None
        with (root / "service.log").open("w+") as log:
            try:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts/verify_dubbing_web_fixture.py"),
                     "--serve-root", str(root), "--port", str(port)],
                    cwd=ROOT, env=isolated_environment(root), stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
                deadline = time.monotonic() + 40
                while True:
                    try:
                        state = api(base, "/__content_acceptance/state")
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Isolated reference server did not start")
                        time.sleep(.15)
                browser = subprocess.Popen(
                    [shutil.which("node") or "node", str(ROOT / "scripts/verify_asr_reference_handoff_browser.mjs")],
                    cwd=ROOT, env={**isolated_environment(root), "TIMELINE_EDIT_URL": base,
                                  "TIMELINE_EDIT_PROJECT": state["project_id"],
                                  "TIMELINE_EDIT_ARTIFACT_DIR": str(root)}, start_new_session=True,
                )
                if browser.wait(timeout=180):
                    raise RuntimeError("ASR reference browser acceptance failed")
                after = api(base, "/__content_acceptance/state")
                assert after["asr_calls"] == after["generated"] == 0
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-5000:], file=sys.stderr)
                raise
            finally:
                stop_process_group(browser)
                stop_process_group(process)
    assert not root.exists()
    print(json.dumps({"asr_reference_cleanup": "complete", "provider_calls": 0}))


if __name__ == "__main__":
    run()
