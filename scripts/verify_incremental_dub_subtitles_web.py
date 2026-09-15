#!/usr/bin/env python3
"""Exercise incremental dub-caption regeneration through the public API.

The temporary fixture hosts the actual FastAPI app and embedded operation
worker. Its ASR and forced-alignment boundaries are fixed locally; no user
project, paid provider, shared server, or frontend build is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from verify_dubbing_web_fixture import (
    ROOT,
    api,
    available_port,
    isolated_environment,
    stop_process_group,
)


def _wait_for_operation(base: str, project_id: str, operation_id: str) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        operation = api(
            base,
            f"/projects/{project_id}/video-localization/operations/{operation_id}",
        )
        if operation["status"] in {"success", "failed", "cancelled"}:
            return operation
        time.sleep(0.15)
    raise AssertionError("incremental dub-subtitle operation did not finish")


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="voice-studio-incremental-dub-subtitles-") as temporary:
        root = Path(temporary)
        (root / "dubbing-web-owner").touch()
        (root / "tmp").mkdir()
        port = available_port()
        assert port not in {5173, 18000, 65335}
        base = f"http://127.0.0.1:{port}"
        process = None
        with (root / "service.log").open("w+") as log:
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "scripts/verify_dubbing_web_fixture.py"),
                        "--serve-root", str(root), "--port", str(port),
                        "--editorial", "--fixed-dub-subtitles",
                    ],
                    cwd=ROOT,
                    env=isolated_environment(root),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 40
                while True:
                    try:
                        state = api(base, "/__content_acceptance/state")
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("isolated incremental-caption server did not start")
                        time.sleep(0.15)

                project_id = state["project_id"]
                before_edit = api(base, f"/projects/{project_id}/video-localization")
                old_clip = next(
                    item for item in before_edit["timeline_clips"]
                    if item["clip_id"] == "old"
                )
                # This is the same bounded timeline-edit request the editor sends.
                # It dirties only `old`; `unchanged` has an existing caption and is
                # deliberately never presented to the fixed ASR boundary.
                api(
                    base,
                    f"/projects/{project_id}/video-localization/timeline-edit",
                    {
                        "clip_patches": [{
                            "clip_id": "old",
                            "expected_generation_identity": old_clip["generation_identity"],
                            "expected_editable_fields": {
                                "start_ms": 0, "end_ms": 1000,
                                "source_start_ms": 0, "source_end_ms": 1000,
                                "media_source_clip_id": None, "dub_lane": 0,
                            },
                            "end_ms": 700,
                            "source_end_ms": 700,
                        }],
                    },
                    method="PATCH",
                )
                after_edit = api(base, f"/projects/{project_id}/video-localization")
                scope = after_edit.get("dub_subtitle_dirty_scope") or {}
                assert scope.get("affected_clip_ids") == ["old"], scope
                dist = Path(os.environ.get(
                    "VOICE_STUDIO_FRONTEND_DIST",
                    ROOT / "frontend" / "build",
                ))
                if not dist.is_dir():
                    raise RuntimeError("isolated frontend build is required for browser acceptance")
                browser = subprocess.Popen(
                    ["node", str(ROOT / "scripts/verify_incremental_dub_subtitles_browser.mjs")],
                    cwd=ROOT,
                    env={
                        **isolated_environment(root),
                        "INCREMENTAL_DUB_URL": base,
                        "INCREMENTAL_DUB_PROJECT": project_id,
                    },
                    start_new_session=True,
                )
                if browser.wait(timeout=70):
                    raise RuntimeError("incremental dub-caption browser acceptance failed")
                operations = api(base, f"/projects/{project_id}/video-localization/operations")
                operation = next(item for item in operations if item["kind"] == "dub_subtitle_generation")
                assert operation["status"] == "success", operation
                summary = operation.get("result_summary") or {}
                assert summary.get("regeneration_mode") == "incremental", summary
                assert summary.get("regeneration_reason") == "dirty_scope", summary
                final = api(base, f"/projects/{project_id}/video-localization")
                captions = {item["subtitle_id"]: item for item in final["dub_subtitles"]}
                assert captions["dub-unchanged"]["text"] == "未编辑配音", captions
                assert final.get("dub_subtitle_dirty_scope") is None
                repeated = api(base, f"/projects/{project_id}/video-localization/operations/dub-subtitles",
                               {"regeneration_mode": "auto", "engine_id": "qwen3-asr-mlx", "execution_mode": "full"}, method="POST")
                replay = _wait_for_operation(base, project_id, repeated["operation_id"])
                assert replay["status"] == "success", replay
                assert replay["result_summary"]["regeneration_reason"] == "already_current", replay
                assert api(base, f"/projects/{project_id}/video-localization")["dub_subtitles"] == final["dub_subtitles"]
                final_state = api(base, "/__content_acceptance/state")
                assert final_state["dub_subtitle_asr_calls"] == 1, final_state
                assert final_state["dub_subtitle_asr_clip_ids"] == [["old"]], final_state
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-7000:], file=sys.stderr)
                raise
            finally:
                if 'browser' in locals():
                    stop_process_group(browser)
                stop_process_group(process)
    assert not root.exists()
    print(json.dumps({
        "incremental_dub_subtitles": "passed",
        "asr_calls": 1,
        "asr_clip_ids": ["old"],
        "preserved_caption": "dub-unchanged",
    }))


if __name__ == "__main__":
    run()
