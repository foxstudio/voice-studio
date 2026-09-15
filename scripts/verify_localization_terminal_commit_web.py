#!/usr/bin/env python3
"""Verify formal localization terminal commit in the isolated production SPA."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from verify_dubbing_web_fixture import (
    ROOT,
    available_port,
    isolated_environment,
    stop_process_group,
)


def serve(root: Path, port: int, dist: Path) -> None:
    if not (root / "localization-terminal-web-owner").is_file():
        raise RuntimeError("An owned acceptance root is required")
    dist.resolve().relative_to(root.resolve())
    os.environ.update(isolated_environment(root))
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

    from app.domains.video_localization import operation_queue, service
    from app.main import app
    from app.schemas.voice_studio import (
        AppSettingsPatch,
        LlmProviderProfileUpsert,
        ProjectCreate,
    )
    from app.services import (
        frontend_distribution,
        llm_runtime,
        project_store,
        settings_store,
    )
    from tests.test_video_localization_localization_source import (
        _draft as source_draft,
    )
    from tests.test_video_localization_localization_terminal_commit import (
        install_fixed_real_localization_execution,
    )

    settings_store.patch(
        AppSettingsPatch(
            video_localization_development_step_control_enabled=True
        )
    )
    settings_store.update_llm_profile(
        "fixture-profile",
        LlmProviderProfileUpsert(
            name="固定本土化终态验收",
            protocol="openai_compatible",
            base_url="http://127.0.0.1:9/v1",
            model_id="fixture-model",
            reasoning_effort="low",
            enabled=True,
        ),
    )
    settings_store.mark_llm_profile_verified("fixture-profile")
    settings_store.set_default_llm_profile("fixture-profile")
    project = project_store.create_project(
        ProjectCreate(name="正式本土化终态网页验收")
    )
    initial = source_draft()
    if service.save_video_localization(project.project_id, initial) is None:
        raise RuntimeError("Failed to save the isolated source fixture")
    spoken_count, subtitle_count = install_fixed_real_localization_execution(
        initial,
        setattr,
        patch_policy=False,
    )
    state = {
        "project_id": project.project_id,
        "profile_id": "fixture-profile",
        "provider_calls": 0,
        "post_commit_checkpoint_failures": 0,
        "spoken_count": spoken_count,
        "subtitle_count": subtitle_count,
    }

    def reject_model_call(*_args, **_kwargs):
        state["provider_calls"] += 1
        raise RuntimeError("The fixed terminal-commit fixture forbids model calls")

    llm_runtime.complete_json = reject_model_call
    llm_runtime.complete_multimodal_json = reject_model_call

    def checkpoint_writer(*_args, **_kwargs):
        def write(step_id, _result):
            if step_id == "commit_localization_tracks":
                state["post_commit_checkpoint_failures"] += 1
                raise RuntimeError(
                    "fixed post-commit checkpoint observer failure"
                )

        return write

    operation_queue.development_checkpoints.LocalizationDevelopmentCheckpointWriter = (
        checkpoint_writer
    )

    @app.get(
        "/api/__localization_terminal_acceptance/state",
        include_in_schema=False,
    )
    def get_state():
        return state

    frontend_distribution.install_frontend_distribution(
        app,
        environ={
            "VOICE_STUDIO_SERVE_FRONTEND": "1",
            "VOICE_STUDIO_FRONTEND_DIST": str(dist),
        },
    )
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=3,
    )


def run() -> None:
    with tempfile.TemporaryDirectory(
        prefix="voice-studio-localization-terminal-web-"
    ) as temporary:
        root = Path(temporary).resolve()
        (root / "localization-terminal-web-owner").touch()
        (root / "tmp").mkdir()
        (root / "node_modules").symlink_to(
            ROOT / "frontend/node_modules",
            target_is_directory=True,
        )
        dist = root / "dist"
        environment = isolated_environment(root)
        reusable_dist = Path(
            os.environ.get("VOICE_STUDIO_FRONTEND_DIST", "")
        )
        if (reusable_dist / "index.html").is_file():
            shutil.copytree(reusable_dist, dist)
        else:
            subprocess.run(
                ["pnpm", "build"],
                cwd=ROOT / "frontend",
                check=True,
                env={
                    **environment,
                    "VOICE_STUDIO_SVELTE_OUT_DIR": str(root / "svelte-kit"),
                    "VOICE_STUDIO_FRONTEND_DIST": str(dist),
                },
                stdout=subprocess.DEVNULL,
            )
        if not (dist / "index.html").is_file():
            raise RuntimeError("The isolated frontend build is missing")

        port = available_port()
        if port in {5173, 18000, 8000}:
            raise RuntimeError("Refusing a known application service port")
        base = f"http://127.0.0.1:{port}"
        server = browser = None
        with (root / "service.log").open("w+") as log:
            try:
                server = subprocess.Popen(
                    [
                        sys.executable,
                        __file__,
                        "--serve-root",
                        str(root),
                        "--port",
                        str(port),
                        "--dist",
                        str(dist),
                    ],
                    cwd=ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 60
                while True:
                    try:
                        with urllib.request.urlopen(
                            base + "/api/health",
                            timeout=1,
                        ):
                            break
                    except OSError:
                        if (
                            server.poll() is not None
                            or time.monotonic() > deadline
                        ):
                            raise RuntimeError(
                                "Isolated localization service failed to start"
                            )
                        time.sleep(0.2)
                browser = subprocess.Popen(
                    [
                        shutil.which("node") or "node",
                        str(
                            ROOT
                            / "scripts/verify_localization_terminal_commit_browser.mjs"
                        ),
                    ],
                    cwd=ROOT,
                    env={
                        **environment,
                        "LOCALIZATION_TERMINAL_E2E_URL": base,
                    },
                    start_new_session=True,
                )
                if browser.wait(timeout=120):
                    raise RuntimeError(
                        "Localization terminal browser regression failed"
                    )
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                stop_process_group(browser)
                stop_process_group(server)
    if root.exists():
        raise RuntimeError("Owned acceptance root was not cleaned")
    print(
        json.dumps(
            {
                "localizationTerminalWeb": "passed",
                "temporary_root_removed": True,
                "model_calls": 0,
                "entry": "production SPA localization button and task refresh",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--dist", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_root is not None:
        if args.port is None or args.dist is None:
            parser.error("--port and --dist are required with --serve-root")
        serve(args.serve_root, args.port, args.dist)
    else:
        run()
