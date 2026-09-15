#!/usr/bin/env python3
"""Isolated timeline selection/resize acceptance; fixed media, no real models."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from verify_dubbing_content_web import backend_scenario
from verify_dubbing_web_fixture import ROOT, api, available_port, isolated_environment, stop_process_group


def run():
    with tempfile.TemporaryDirectory(prefix="voice-studio-viewport-web-") as temporary:
        root = Path(temporary)
        (root / "dubbing-web-owner").touch()
        (root / "tmp").mkdir()
        port = available_port()
        assert port not in {5173, 18000, 65335}
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
                result = backend_scenario(base)
                prefix = f"/projects/{result['project_id']}/video-localization"
                initial_clip_ids = {clip["clip_id"] for clip in api(base, prefix)["timeline_clips"]}
                # More actual fixed-provider history makes the stacked dubbing
                # inspector taller than the collapsed task list on a narrow page.
                for _ in range(10):
                    request = api(base, prefix + "/tts/handoff/subtitle", {
                        "target_subtitle_ids": ["subtitle"], "source_cue_ids": ["cue"],
                        "parameters": {"engine_id": "omnivoice", "language": "zh", "output_format": "wav"},
                    })
                    task = api(base, "/generate", request)
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        state = api(base, "/tasks/" + task["task_id"])
                        if state["status"] == "success":
                            break
                        assert state["status"] != "failed", state
                        time.sleep(.1)
                    else:
                        raise AssertionError("Fixed fixture generation did not finish")
                draft = api(base, prefix)
                draft["timeline_clips"] = [clip for clip in draft["timeline_clips"] if clip["clip_id"] in initial_clip_ids]
                target = next(cue for cue in draft["localized_subtitles"] if cue["subtitle_id"] == "subtitle")
                target.update(start_ms=4000, end_ms=4067)
                draft["ui_state"].update(timeline_zoom=127, timeline_viewport_start_ms=3990)
                request = urllib.request.Request(base + "/api" + prefix, data=json.dumps(draft).encode(),
                    headers={"Content-Type": "application/json"}, method="PUT")
                with urllib.request.urlopen(request, timeout=20) as response:
                    assert response.status == 200
                browser = subprocess.Popen([shutil.which("node") or "node", str(ROOT / "scripts/verify_timeline_viewport_browser.mjs")],
                    cwd=ROOT, env={**isolated_environment(root), "TIMELINE_VIEWPORT_URL": base,
                        "TIMELINE_VIEWPORT_PROJECT": result["project_id"], "TIMELINE_VIEWPORT_ARTIFACT_DIR": str(root)}, start_new_session=True)
                if browser.wait(timeout=90):
                    raise RuntimeError("Timeline viewport browser acceptance failed")
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-5000:], file=sys.stderr)
                raise
            finally:
                stop_process_group(browser)
                stop_process_group(process)
    assert not root.exists()
    print(json.dumps({"cleanup": "complete", "temporary_root_removed": True, "port": port}))


if __name__ == "__main__":
    run()
