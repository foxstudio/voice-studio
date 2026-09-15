#!/usr/bin/env python3
"""Isolated manual/history content acceptance. No model or user project writes.

Use --backend-only for the public API scenario. The default additionally checks
the production SPA with Playwright; build frontend separately before invoking.
Only TTS inference and ASR provider outputs are fixed, not business services.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from verify_dubbing_web_fixture import ROOT, api, available_port, isolated_environment, serve, stop_process_group, write_fixture_audio


def backend_scenario(base):
    project_id = api(base, "/__content_acceptance/state")["project_id"]
    prefix = f"/projects/{project_id}/video-localization"
    original = api(base, prefix)["timeline_clips"]

    def generate():
        request = api(base, prefix + "/tts/handoff/subtitle", {
            "target_subtitle_ids": ["subtitle"], "source_cue_ids": ["cue"],
            "parameters": {"engine_id": "omnivoice", "language": "zh", "output_format": "wav"},
        })
        task = api(base, "/generate", request)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            draft = api(base, prefix)
            workflow = next((w for w in draft["tts_tasks"] if w.get("generation_task_id") == task["task_id"]), None)
            if workflow and workflow["stages"][1]["status"] in {"success", "failed"}:
                return task, workflow, draft
            time.sleep(0.1)
        raise AssertionError(("workflow did not close", api(base, "/tasks/" + task["task_id"]), draft["tts_tasks"]))

    bad_task, bad, draft = generate()
    assert bad["stages"][0]["status"] == "success", bad
    assert bad["stages"][1]["status"] == "success", bad
    assert len(draft["timeline_clips"]) == len(original) + 1
    assert any(c.get("result_id") == bad["result_id"] for c in draft["timeline_clips"])
    for clip in original:
        assert next(c for c in draft["timeline_clips"] if c["clip_id"] == clip["clip_id"]) == clip
    assert api(base, "/tasks/" + bad_task["task_id"])["status"] == "success"
    after_bad = draft["timeline_clips"]
    api(base, "/__content_acceptance/mode", {"mode": "good"})
    good_task, good, draft = generate()
    assert good["stages"][1]["status"] == "success", good
    assert any(c.get("result_id") == good["result_id"] for c in draft["timeline_clips"])
    assert len(draft["timeline_clips"]) == len(after_bad) + 1
    for clip in after_bad:
        assert next(c for c in draft["timeline_clips"] if c["clip_id"] == clip["clip_id"]) == clip
    before_import = draft["timeline_clips"]
    api(base, prefix + f"/timeline-clips/history/{bad['result_id']}/apply", {
        "request_id": "raw-history-import", "segment_id": "subtitle", "new_clip_id": "raw-history-import", "start_ms": 2000,
        "dub_lane": 0, "force_new": True,
    })
    imported = api(base, prefix)["timeline_clips"]
    assert len(imported) == len(before_import) + 1
    assert next(c for c in imported if c["clip_id"] == "raw-history-import")["result_id"] == bad["result_id"]
    for clip in before_import:
        assert next(c for c in imported if c["clip_id"] == clip["clip_id"]) == clip
    assert api(base, "/__content_acceptance/state")["asr_calls"] == 0, "Raw material placement must not call ASR"
    return {"project_id": project_id, "bad_workflow_id": bad["workflow_id"],
            "good_workflow_id": good["workflow_id"], "bad_result_id": bad["result_id"],
            "good_result_id": good["result_id"], "backend": "passed", "raw_material_asr_calls": 0,
            "provider": "fixed TTS waveform and fixed ASR; no model called"}


def run(backend_only, responsive=False, inspect_seconds=0, trim_only=False):
    with tempfile.TemporaryDirectory(prefix="voice-studio-content-web-") as temporary:
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
                process = subprocess.Popen([sys.executable, __file__, "--serve-root", str(root), "--port", str(port)],
                    env=isolated_environment(root), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 40
                while True:
                    try:
                        api(base, "/health")
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Isolated server startup failed")
                        time.sleep(0.15)
                result = backend_scenario(base)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if not backend_only:
                    environment = {**isolated_environment(root), "DUBBING_CONTENT_E2E_URL": base,
                                   "DUBBING_CONTENT_E2E_PROJECT": result["project_id"],
                                   "DUBBING_CONTENT_E2E_BAD_RESULT": result["bad_result_id"],
                                   "DUBBING_CONTENT_E2E_ARTIFACT_DIR": str(root),
                                   "DUBBING_CONTENT_E2E_RESPONSIVE": "1" if responsive else "0",
                                   "DUBBING_CONTENT_E2E_TRIM_ONLY": "1" if trim_only else "0"}
                    browser = subprocess.Popen([shutil.which("node") or "node", str(ROOT / "scripts/verify_dubbing_content_browser.mjs")],
                        cwd=ROOT, env=environment, start_new_session=True)
                    if browser.wait(timeout=150):
                        raise RuntimeError("Content browser acceptance failed")
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-8000:], file=sys.stderr)
                raise
            finally:
                stop_process_group(browser)
                stop_process_group(process)
                if inspect_seconds:
                    print(json.dumps({"inspect_screenshots": str(root), "cleanup_after_seconds": inspect_seconds}), flush=True)
                    time.sleep(inspect_seconds)
    assert not root.exists()
    print(json.dumps({"cleanup": "complete", "temporary_root_removed": True, "service_port": port}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--responsive", action="store_true", help="Also verify dubbing panels at 1087/768/390/1440 px")
    parser.add_argument("--trim-only", action="store_true", help="Run only the isolated timeline trim browser scenario")
    parser.add_argument("--inspect-seconds", type=int, choices=range(0, 61), default=0,
                        help="Keep screenshots briefly after services stop, then remove the owned root")
    parser.add_argument("--serve-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    serve(arguments.serve_root, arguments.port) if arguments.serve_root else run(arguments.backend_only, arguments.responsive, arguments.inspect_seconds, arguments.trim_only)
