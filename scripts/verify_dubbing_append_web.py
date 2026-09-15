#!/usr/bin/env python3
"""Isolated append/replay/explicit-replace acceptance using fixed providers only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from verify_dubbing_web_fixture import ROOT, api, available_port, isolated_environment, stop_process_group


def scenario(base: str) -> dict:
    project = api(base, "/__content_acceptance/state")["project_id"]
    prefix = f"/projects/{project}/video-localization"
    draft = api(base, prefix)
    original = next(clip for clip in draft["timeline_clips"] if clip["clip_id"] == "old")
    # Two multi-subtitle selections overlap only at part_b, but each says the
    # exact independent fixture sentence. No real speech provider is needed.
    for subtitle, start, end, text in [
        ("part_a", 0, 500, "风格变了"),
        ("part_c", 500, 1000, "风格变了"),
        ("part_b", 1000, 2000, "角色也变了"),
    ]:
        draft["localized_subtitles"].append({"subtitle_id": subtitle, "start_ms": start,
            "end_ms": end, "text": text, "tts_text": text, "source_cue_ids": ["cue"]})
    request = urllib.request.Request(base + "/api" + prefix, data=json.dumps(draft).encode(),
                                    headers={"Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
    api(base, "/__content_acceptance/mode", {"mode": "good"})

    def generate(targets: list[str], history_id: str | None = None):
        body = {"target_subtitle_ids": targets, "source_cue_ids": ["cue"],
                "parameters": {"engine_id": "omnivoice", "language": "zh", "output_format": "wav"}}
        if history_id:
            body["history_result_id"] = history_id
        request = api(base, prefix + "/tts/handoff/" + targets[0], body)
        task = api(base, "/generate", request)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = api(base, prefix)
            workflow = next((item for item in current["tts_tasks"] if item.get("generation_task_id") == task["task_id"]), None)
            if workflow and workflow["stages"][1]["status"] in {"success", "failed"}:
                assert workflow["stages"][1]["status"] == "success", workflow
                clip = next(item for item in current["timeline_clips"] if item.get("result_id") == workflow["result_id"])
                return workflow["result_id"], clip, current
            time.sleep(.1)
        raise AssertionError("Generation did not finish")

    first_result, first_clip, after_first = generate(["subtitle"])
    assert original in after_first["timeline_clips"], "First ordinary generation erased the old clip"
    second_result, second_clip, after_second = generate(["subtitle"], first_result)
    assert original in after_second["timeline_clips"] and first_clip in after_second["timeline_clips"], "Reuse altered old clips"
    assert len({original.get("dub_lane", 0), first_clip.get("dub_lane", 0), second_clip.get("dub_lane", 0)}) == 3
    _, group_clip, after_group = generate(["part_a", "part_b"])
    _, overlapping_clip, after_overlap = generate(["part_c", "part_b"])
    assert all(clip in after_overlap["timeline_clips"] for clip in after_group["timeline_clips"]), "Partial selection erased old grouped clip"
    assert group_clip["clip_id"] != overlapping_clip["clip_id"]

    # Explicit adoption replaces only the selected ID; even other clips bound
    # to the same subtitle must remain byte-for-byte unchanged.
    before = after_overlap["timeline_clips"]
    calls = api(base, "/__content_acceptance/state")["asr_calls"]
    api(base, prefix + f"/timeline-clips/{first_clip['clip_id']}/history/{second_result}/apply", {"request_id": "replace-first"})
    # Mutation responses contain only affected clips; read the complete project
    # before checking preservation of the untouched siblings.
    replaced = api(base, prefix)
    assert len(replaced["timeline_clips"]) == len(before)
    for clip in before:
        if clip["clip_id"] != first_clip["clip_id"]:
            assert clip in replaced["timeline_clips"], "Explicit history replacement changed an unselected clip"
    selected = next(clip for clip in replaced["timeline_clips"] if clip["clip_id"] == first_clip["clip_id"])
    assert selected["result_id"] == second_result
    assert api(base, "/__content_acceptance/state")["asr_calls"] == calls, "History reuse repeated ASR"
    return {"project_id": project, "clips": replaced["timeline_clips"], "old_clip": original,
            "obsolete_workflow_id": next(item["workflow_id"] for item in replaced["tts_tasks"]
                                         if item.get("result_id") == first_result),
            "alternate_result_id": first_result,
            "first_clip_id": first_clip["clip_id"], "second_clip_id": second_clip["clip_id"],
            "group_clip_id": group_clip["clip_id"], "overlap_clip_id": overlapping_clip["clip_id"],
            "workflow_count": len(replaced["tts_tasks"]), "backend": "passed"}


def run(backend_only: bool):
    with tempfile.TemporaryDirectory(prefix="voice-studio-append-web-") as temporary:
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
                result = scenario(base)
                print(json.dumps({"append_backend": "passed", "clip_count": len(result["clips"]),
                                  "workflows": result["workflow_count"]}), flush=True)
                if not backend_only:
                    browser = subprocess.Popen([shutil.which("node") or "node",
                        str(ROOT / "scripts/verify_dubbing_append_browser.mjs")], cwd=ROOT,
                        env={**isolated_environment(root), "DUBBING_APPEND_URL": base,
                             "DUBBING_APPEND_EXPECTED": json.dumps(result),
                             "DUBBING_APPEND_ARTIFACT_DIR": str(root),
                             # This harness verifies decoded playback clock and UI state,
                             # not audible hardware output. A fake sink keeps Chromium's
                             # media clock deterministic on busy/headless hosts.
                             "VOICE_STUDIO_BROWSER_AUDIO": "fake"},
                        start_new_session=True)
                    if browser.wait(timeout=150):
                        raise RuntimeError("Append browser acceptance failed")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-only", action="store_true")
    run(parser.parse_args().backend_only)
