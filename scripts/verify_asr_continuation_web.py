#!/usr/bin/env python3
"""Verify fixed-input ASR development continuation in the isolated SPA."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from isolated_frontend_workspace import prepare_frontend_workspace
from verify_dubbing_web_fixture import (
    ROOT,
    available_port,
    isolated_environment,
    stop_process_group,
)


def serve(root: Path, port: int, dist: Path) -> None:
    if not (root / "asr-continuation-owner").is_file():
        raise RuntimeError("An owned acceptance root is required")
    dist.resolve().relative_to(root.resolve())
    os.environ.update(isolated_environment(root))
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

    from app.domains.video_localization import (
        asr_pipeline,
        development_checkpoints,
        operation_queue,
        review_decisions,
        service,
        transcription,
    )
    from app.domains.video_localization.schemas import (
        VideoLocalizationCue,
        VideoLocalizationDraft,
        VideoLocalizationOperation,
        VideoLocalizationTranscriptSegment,
    )
    from app.main import app
    from app.schemas.voice_studio import AppSettingsPatch, ProjectCreate
    from app.services import (
        frontend_distribution,
        llm_runtime,
        project_store,
        settings_store,
    )
    from tests.test_video_localization_research_evidence import (
        _understanding_result,
    )

    project = project_store.create_project(
        ProjectCreate(name="ASR 续跑隔离验收")
    )
    settings_store.patch(
        AppSettingsPatch(
            video_localization_development_step_control_enabled=True
        )
    )
    understanding = _understanding_result()
    item = understanding.input.segments[0]
    segment = VideoLocalizationTranscriptSegment(
        segment_id=item.segment_id,
        start_ms=item.start_ms,
        end_ms=item.end_ms,
        raw_text=item.text,
        corrected_text=item.text,
    )
    raw_input = transcription.TranscribeRawInput(
        audio_path=str(root / "source.wav"),
        audio_sha256="audio-sha256",
        engine_id="qwen3-asr-mlx",
        source_track_id="vocals",
        requested_language="en",
        duration_ms=2200,
    )
    raw = transcription.build_transcribe_raw_output(
        input=raw_input,
        raw_text=item.text,
        language="en",
        segments=[segment],
    )
    workflow = asr_pipeline.AsrPipelineInput(
        operation_id="formal-op",
        audio_path=raw_input.audio_path,
        alignment_audio_path=raw_input.audio_path,
        engine_id=raw_input.engine_id,
        source_track_id="vocals",
        alignment_source_track_id="vocals",
        source_audio_sha256="audio-sha256",
        alignment_audio_sha256="audio-sha256",
        language="en",
        duration_ms=2200,
        llm_profile_id="review-profile",
    )
    request = review_decisions.AsrReviewDecisionsInput(
        upstream_operation_id="section-op",
        source_track_id="vocals",
        source_audio_sha256="audio-sha256",
        language="en",
        profile_id="review-profile",
        document_summary=understanding.brief.summary,
        segments=[segment],
        upstream_status="completed",
        issues=[],
    )
    calls = {"model_calls": 0, "whole_recheck_calls": 0}
    whole_recheck_started = threading.Event()
    release_whole_recheck = threading.Event()

    def reject_model(*_args, **_kwargs):
        calls["model_calls"] += 1
        raise RuntimeError("No provider calls allowed in continuation acceptance")

    llm_runtime.complete_json = reject_model
    llm_runtime.complete_multimodal_json = reject_model
    reviewed = review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE.run(request)
    draft = VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="user-cue",
                start_ms=0,
                end_ms=1000,
                en_subtitle_text="user edit must survive",
            )
        ],
        operations=[
            VideoLocalizationOperation(
                project_id=project.project_id,
                operation_id="historical-stop",
                kind="english_asr",
                status="success",
                label="历史 ASR 开发结果",
                parameters={
                    "execution_mode": "stop_after",
                    "stop_after_step": "asr",
                },
                result_summary={"cue_count": 0},
            ),
            VideoLocalizationOperation(
                project_id=project.project_id,
                operation_id="historical-round-two",
                kind="english_asr",
                status="success",
                label="历史 ASR 第二轮结果",
                parameters={
                    "execution_mode": "development_target",
                    "development_target_step_id": "whole_recheck_r2",
                },
                result_summary={
                    "task_step_results": {
                        "whole_recheck_r2": {
                            "label": "历史 ASR 第二轮结果",
                            "status": "success",
                            "summary": "已保存的历史结果，仅供查看。",
                        }
                    }
                },
            ),
            VideoLocalizationOperation(
                project_id=project.project_id,
                operation_id="formal-op",
                kind="english_asr",
                status="failed",
                parameters={"execution_mode": "full"},
            ),
            VideoLocalizationOperation(
                project_id=project.project_id,
                operation_id="review-op",
                kind="english_asr",
                status="success",
                parameters={
                    "execution_mode": "development_target",
                    "development_source_operation_id": "formal-op",
                    "development_target_step_id": "review_decisions_r1",
                },
            ),
        ],
    )
    service.save_video_localization(project.project_id, draft)
    for operation_id, snapshots in {
        "formal-op": {
            "workflow_input": workflow,
            "asr_result": raw,
            "initial_analysis_join_result": asr_pipeline.AsrJoinedTranscript(
                raw_asr=raw,
                segments=[segment],
            ),
            "understand_document_result": understanding,
        },
        "review-op": {"review_decisions_r1_result": reviewed},
    }.items():
        writer = development_checkpoints.LocalizationDevelopmentCheckpointWriter(
            operation_queue.DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT,
            project_id=project.project_id,
            workflow_operation_id=operation_id,
        )
        for step, value in snapshots.items():
            writer(step, value)

    run_whole_recheck = asr_pipeline.DEFAULT_ASR_PIPELINE.run_whole_recheck

    def observed_whole_recheck(request, *, context=None):
        calls["whole_recheck_calls"] += 1
        whole_recheck_started.set()
        if not release_whole_recheck.wait(timeout=30):
            raise RuntimeError("Browser did not release the fixed whole recheck")
        return run_whole_recheck(request, context=context)

    asr_pipeline.DEFAULT_ASR_PIPELINE.run_whole_recheck = observed_whole_recheck

    @app.get(
        "/api/__asr_continuation_acceptance/state",
        include_in_schema=False,
    )
    def state():
        return {
            "project_id": project.project_id,
            "whole_recheck_started": whole_recheck_started.is_set(),
            **calls,
        }

    @app.post(
        "/api/__asr_continuation_acceptance/release",
        include_in_schema=False,
    )
    def release():
        release_whole_recheck.set()
        return {"released": True}

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


def run(*, manual: bool = False) -> None:
    with tempfile.TemporaryDirectory(
        prefix="voice-studio-asr-continuation-web-"
    ) as temporary:
        root = Path(temporary).resolve()
        (root / "asr-continuation-owner").touch()
        (root / "tmp").mkdir()
        dist = root / "dist"
        environment = isolated_environment(root)
        reusable_dist = Path(
            os.environ.get("VOICE_STUDIO_FRONTEND_DIST", "")
        )
        if (reusable_dist / "index.html").is_file():
            shutil.copytree(reusable_dist, dist)
        else:
            frontend = prepare_frontend_workspace(
                ROOT / "frontend",
                root / "frontend",
            )
            build_environment = {
                **environment,
                "VOICE_STUDIO_SVELTE_OUT_DIR": str(frontend / ".svelte-kit"),
                "VOICE_STUDIO_FRONTEND_DIST": str(dist),
            }
            subprocess.run(
                ["pnpm", "exec", "svelte-kit", "sync"],
                cwd=frontend,
                check=True,
                env=build_environment,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                ["pnpm", "build"],
                cwd=frontend,
                check=True,
                env=build_environment,
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
                                "Isolated ASR continuation service failed to start"
                            )
                        time.sleep(0.2)
                if manual:
                    print(
                        json.dumps(
                            {"url": base, "project_id": "read from state endpoint"},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    input("Manual acceptance ready; press Enter to clean up.\n")
                else:
                    browser = subprocess.Popen(
                        [
                            shutil.which("node") or "node",
                            str(
                                ROOT
                                / "scripts/verify_asr_continuation_browser.mjs"
                            ),
                        ],
                        cwd=ROOT,
                        env={
                            **environment,
                            "ASR_CONTINUATION_E2E_URL": base,
                        },
                        start_new_session=True,
                    )
                    if browser.wait(timeout=120):
                        raise RuntimeError(
                            "ASR continuation browser regression failed"
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
                "asrContinuationWeb": "manual-finished" if manual else "passed",
                "temporary_root_removed": True,
                "model_calls": 0,
                "entry": "public ASR development API and production SPA task history",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--dist", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Keep the isolated service open for manual browser inspection.",
    )
    args = parser.parse_args()
    if args.serve_root is not None:
        if args.port is None or args.dist is None:
            parser.error("--port and --dist are required with --serve-root")
        serve(args.serve_root, args.port, args.dist)
    else:
        run(manual=args.manual)
