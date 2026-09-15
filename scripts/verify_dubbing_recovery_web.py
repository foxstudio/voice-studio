#!/usr/bin/env python3
"""Exercise durable recovery against owned media and fixed inference providers."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from verify_dubbing_web_fixture import ROOT, api, available_port, isolated_environment, serve, stop_process_group


def scenario(base, *, manual_timing=False):
    project = api(base, "/__content_acceptance/state")["project_id"]
    prefix = f"/projects/{project}/video-localization"
    snapshot = api(base, prefix + "/dubbing/snapshot")
    plan = api(
        base,
        prefix + "/dubbing/plan",
        {
            "source_revision": snapshot["source_revision"],
            "semantic_units": snapshot["semantic_units"],
            "boundaries": snapshot["boundaries"],
        },
    )
    group = plan["groups"][0]
    if manual_timing:
        api(base, "/__content_acceptance/mode", {"mode": "good"})
    response = api(
        base, prefix + "/dubbing/production-run/execute", {"scope": "single_group", "group_id": group["group_id"]}
    )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state = api(base, "/__content_acceptance/state")
        run = api(base, prefix + "/dubbing/production-run")
        if state["generated"] >= 1 and run["active_group_count"] == 0:
            break
        time.sleep(0.2)
    else:
        raise AssertionError((response, state, run))
    if not manual_timing and state["generated"] < 2:
        # The semantic-recovery scenario explicitly chooses its bounded whole
        # retry. Proven capacity overflow must not trigger this TTS by itself.
        response = api(base, prefix + "/dubbing/production-run/execute", {
            "scope": "single_group", "group_id": group["group_id"],
            "regenerate_existing": True,
        })
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            state = api(base, "/__content_acceptance/state")
            run = api(base, prefix + "/dubbing/production-run")
            if state["generated"] >= 2 and run["active_group_count"] == 0:
                break
            time.sleep(0.2)
        else:
            raise AssertionError((response, state, run))
    return {"project_id": project, "group_id": group["group_id"], "generated_before": state["generated"]}


def run(*, manual_timing=False, candidate_resume=False):
    with tempfile.TemporaryDirectory(prefix="voice-recovery-web-") as temporary:
        root = Path(temporary).resolve()
        (root / "dubbing-web-owner").touch()
        (root / "tmp").mkdir()
        port = available_port()
        assert port not in {5173, 18000}
        base = f"http://127.0.0.1:{port}"
        process = browser = None
        with (root / "service.log").open("w+") as log:
            try:
                process = subprocess.Popen(
                    [sys.executable, __file__, "--serve-root", str(root), "--port", str(port)],
                    env=isolated_environment(root),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 40
                while True:
                    try:
                        api(base, "/health")
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Server startup failed")
                        time.sleep(0.2)
                result = ({"project_id": api(base, "/__content_acceptance/state")["project_id"]}
                          if candidate_resume else scenario(base, manual_timing=manual_timing))
                print(json.dumps(result), flush=True)
                browser = subprocess.Popen(
                    ["node", str(ROOT / "scripts" / (
                        "verify_dubbing_candidate_resume_browser.mjs" if candidate_resume else
                        "verify_dubbing_manual_timing_browser.mjs" if manual_timing
                        else "verify_dubbing_recovery_browser.mjs"
                    ))],
                    env={**os.environ, "RECOVERY_URL": base, "RECOVERY_EXPECTED": json.dumps(result)},
                    start_new_session=True,
                )
                if browser.wait(timeout=90):
                    raise RuntimeError("Recovery browser failed")
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-6000:])
                raise
            finally:
                stop_process_group(browser)
                stop_process_group(process)
    assert not root.exists()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve-root", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--manual-timing", action="store_true")
    parser.add_argument("--candidate-resume", action="store_true")
    args = parser.parse_args()
    if args.serve_root:
        serve(args.serve_root, args.port, recovery=True)
    else:
        run(manual_timing=args.manual_timing, candidate_resume=args.candidate_resume)
