#!/usr/bin/env python3
"""Owned fixed-media server and lifecycle helpers for isolated Web acceptance.

Only TTS inference, engine health and ASR provider outputs are fixed. Product
services, persistence, task delivery and the production SPA remain unchanged.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import hashlib
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import urllib.error
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(root: Path) -> dict[str, str]:
    paths = {
        "DATA": root,
        "MODELS": root / "models",
        "VOICES": root / "voices",
        "OUTPUTS": root / "outputs",
        "EXPORTS": root / "exports",
        "PROJECTS": root / "projects",
        "CACHE": root / "cache",
        "LOGS": root / "logs",
    }
    environment = dict(os.environ)
    environment.update({f"VOICE_STUDIO_{name}_DIR": str(path) for name, path in paths.items()})
    environment.update({
        "VOICE_STUDIO_DB_PATH": str(root / "config" / "voice_studio.db"),
        "VOICE_STUDIO_SERVE_FRONTEND": "0",
        "VOICE_STUDIO_VIDEO_LOCALIZATION_WORKER_MODE": "embedded",
        "PYTHONPATH": str(ROOT / "backend"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(root / "tmp"),
    })
    return environment


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop_process_group(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=7)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def write_fixture_audio(path: Path, seconds=1, *, frequency=440, constant=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    samples = b"".join(struct.pack("<h", round(4000 * (1 if constant else 0.15 + 0.8 * i / (rate * seconds))
                                                 * math.sin(2 * math.pi * frequency * i / rate)))
                       for i in range(rate * seconds))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples)


def serve(
    root: Path,
    port: int,
    *,
    recovery=False,
    editorial=False,
    fixed_dub_subtitles=False,
):
    if not (root / "dubbing-web-owner").is_file():
        raise RuntimeError("An owned temporary root is required")
    os.environ.update(isolated_environment(root))
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app
    from app.domains.video_localization import (
        draft_store,
        dub_subtitles,
        media_assets,
    )
    from app.domains.video_localization.schemas import VideoLocalizationDraft
    from app.schemas import video_localization_dub_subtitle_step as dub_subtitle_steps
    from app.schemas.voice_studio import ProjectCreate
    from app.services import asr_service, engine_health, engine_runner, frontend_distribution, project_store
    from fastapi import Body

    text = "风格变了角色也变了"
    project = project_store.create_project(ProjectCreate(name="隔离配音内容验收", default_engine_id="omnivoice"))
    package = media_assets.project_video_localization_dir(project.project_id)
    vocals = package / "stems" / "vocals.wav"
    old_audio = package / "tts" / "old.wav"
    video = package / "source" / "fixture.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x17202b:s=160x90:r=30",
        "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True, timeout=15)
    write_fixture_audio(vocals, 6, frequency=220)
    write_fixture_audio(old_audio, frequency=330)
    old_audio_sha256 = hashlib.sha256(old_audio.read_bytes()).hexdigest()
    initial_draft = VideoLocalizationDraft(
        source_media={"filename": "fixture.mp4", "video_path": str(video), "duration_ms": 6000,
                      "width": 160, "height": 90, "frame_rate": 30,
                      "content_sha256": hashlib.sha256(video.read_bytes()).hexdigest()},
        stems={"vocals_clean_path": str(vocals)},
        cues=[{"cue_id": "cue", "speaker_id": "speaker", "start_ms": 0, "end_ms": 3000,
               "en_subtitle_text": "Camera movement", "audio_route": "clone_from_source"}],
        localized_subtitles=[{"subtitle_id": "subtitle", "start_ms": 0, "end_ms": 3000,
                               "text": text, "tts_text": text, "source_cue_ids": ["cue"]},
                              {"subtitle_id": "empty_" + "long_target_identifier_" * 8,
                               "start_ms": 3500, "end_ms": 5500,
                               "text": "用于检查长标题和空配音历史的字幕", "tts_text": "用于检查长标题和空配音历史的字幕",
                               "source_cue_ids": ["cue"]}],
        timeline_clips=[
            {"clip_id": "old", "track_id": "dub", "dub_lane": 0,
             "subtitle_id": "subtitle", "target_subtitle_ids": ["subtitle"],
             "start_ms": 0, "end_ms": 1000, "source_start_ms": 0,
             "source_end_ms": 1000, "status": "ready", "audio_path": str(old_audio)},
            # This queued clip owns a separate completed caption. It gives the
            # editor acceptance a second source identity without entering the
            # export mix that this fixture also checks.
            {"clip_id": "unchanged", "track_id": "dub", "dub_lane": 0,
             "subtitle_id": "empty_" + "long_target_identifier_" * 8,
             "target_subtitle_ids": ["empty_" + "long_target_identifier_" * 8],
             "start_ms": 5000, "end_ms": 5500, "source_start_ms": 0,
             "source_end_ms": 500, "status": "queued", "audio_path": str(old_audio)},
        ],
        dub_subtitles=[
            {"subtitle_id": "dub-old", "start_ms": 0, "end_ms": 1000,
             "text": "已剪辑配音", "source_clip_ids": ["old"], "dub_lanes": [0],
             "source_audio_sha256": old_audio_sha256, "needs_review": False},
            {"subtitle_id": "dub-unchanged", "start_ms": 5000, "end_ms": 5500,
             "text": "未编辑配音", "source_clip_ids": ["unchanged"], "dub_lanes": [0],
             "source_audio_sha256": old_audio_sha256, "needs_review": False},
        ],
    )
    if not editorial:
        initial_draft = initial_draft.model_copy(update={
            "timeline_clips": initial_draft.timeline_clips[:1], "dub_subtitles": [],
        })
    draft_store.save(project.project_id, initial_draft, intent="runtime")
    if recovery:
        # Recovery fixture starts with an empty target, like a failed unplaced
        # production group. Other acceptance modes keep their existing clip.
        initial = draft_store.get(project.project_id)
        draft_store.save(project.project_id, initial.model_copy(update={"timeline_clips": []}), intent="runtime")
    state = {
        "mode": "bad",
        "asr_calls": 0,
        "generated": 0,
        "project_id": project.project_id,
        "dub_subtitle_asr_calls": 0,
        "dub_subtitle_asr_clip_ids": [],
    }

    def health(engine_id):
        return {"healthy": engine_id == "omnivoice" or (recovery and engine_id == "qwen3-asr-mlx" and state["mode"] == "good"), "status": "fixed_acceptance_provider",
                "model_path": str(root / "models")}

    def infer(engine_id, kwargs, **unused):
        if engine_id != "omnivoice":
            raise RuntimeError("Real inference is forbidden in this harness")
        output = Path(kwargs["output_path"]).resolve()
        output.relative_to(root.resolve())
        state["generated"] += 1
        write_fixture_audio(output, frequency=440 if state["mode"] == "bad" else 660)
        return {"output_path": str(output), "duration_ms": 1000, "sample_rate": 24000,
                "generation_time_ms": 1}

    def transcribe(**kwargs):
        assert kwargs["language"] == "auto"
        state["asr_calls"] += 1
        # Decode the fixture's acoustic marker, not mutable process state.
        import soundfile as sf
        samples, rate = sf.read(kwargs["audio_path"])
        if samples.ndim > 1:
            samples = samples[:, 0]
        crossings = sum(left <= 0 < right for left, right in zip(samples, samples[1:]))
        frequency = crossings * rate / len(samples)
        return {"text": "Camera movement " + text if frequency < 550 else text}

    engine_health.health_check = health
    engine_runner.run_isolated = infer
    asr_service.transcribe = transcribe

    if fixed_dub_subtitles:
        from threading import Event
        caption_asr_release = Event()

        @app.post("/api/__content_acceptance/release-dub-subtitle", include_in_schema=False)
        def release_dub_subtitle():
            caption_asr_release.set()
            return {"released": True}

        # Keep the public operation, worker, persistence and real segmentation
        # path intact. Only the two model-backed boundaries have deterministic
        # local responses, so the acceptance script can prove selection without
        # invoking ASR or an external aligner.
        def fixed_dub_transcribe(request, *, audio_path, is_cancelled=None):
            del audio_path, is_cancelled
            clip_ids = [clip.clip_id for clip in request.audible_clips]
            state["dub_subtitle_asr_calls"] += 1
            state["dub_subtitle_asr_clip_ids"].append(clip_ids)
            if not caption_asr_release.wait(timeout=20):
                raise RuntimeError("Browser did not observe and release the running caption task")
            assert clip_ids
            clip = request.audible_clips[0]
            return dub_subtitle_steps.DubSubtitleTranscribeTrackOutput(
                audio_sha256=request.audio.audio_sha256,
                engine_id=request.engine_id,
                language="zh",
                chunks=[dub_subtitle_steps.DubSubtitleTranscriptChunk(
                    segment_id="fixed-dub-asr-1",
                    audio_window_start_ms=clip.timeline_start_ms,
                    audio_window_end_ms=clip.timeline_end_ms,
                    text=text,
                )],
                quality_status="passed",
            )

        def fixed_dub_align(request, *, audio_path, is_cancelled=None):
            del audio_path, is_cancelled
            chunk = request.chunks[0]
            duration = chunk.audio_window_end_ms - chunk.audio_window_start_ms
            words = []
            for index, character in enumerate(chunk.text):
                start_ms = chunk.audio_window_start_ms + (duration * index // len(chunk.text))
                end_ms = chunk.audio_window_start_ms + (duration * (index + 1) // len(chunk.text))
                words.append(dub_subtitle_steps.DubSubtitleAlignedWord(
                    word_id=f"fixed-word-{index}",
                    cue_id=chunk.cue_id,
                    text=character,
                    start_ms=start_ms,
                    end_ms=max(start_ms + 1, end_ms),
                ))
            return dub_subtitle_steps.DubSubtitleAlignWordsOutput(
                audio_sha256=request.audio.audio_sha256,
                words=words,
                alignment_call_count=1,
            )

        dub_subtitles.transcribe_track = fixed_dub_transcribe
        dub_subtitles.align_words = fixed_dub_align

    if recovery:
        from app.services import qwen_forced_aligner
        import soundfile as sf
        markers = {}
        def recovery_infer(engine_id, kwargs, **unused):
            assert engine_id == "omnivoice"
            output = Path(kwargs["output_path"]).resolve()
            output.relative_to(root.resolve())
            spoken = kwargs["text"]
            state["generated"] += 1
            frequency = 500 + state["generated"] * 100
            markers[frequency] = spoken
            seconds = 4 if spoken == text else 1
            write_fixture_audio(output, seconds, frequency=frequency, constant=True)
            return {"output_path": str(output), "duration_ms": seconds * 1000, "sample_rate": 24000, "generation_time_ms": 1}
        def recovery_asr(**kwargs):
            samples, rate = sf.read(kwargs["audio_path"])
            crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
            frequency = crossings * rate / len(samples)
            state["asr_calls"] += 1
            return {"text": markers[min(markers, key=lambda value: abs(value-frequency))]}
        def align(**kwargs):
            duration = sf.info(kwargs["audio_path"]).duration
            spoken = kwargs["transcript_text"]
            return [{"text": char, "start_time": i*duration/len(spoken), "end_time": (i+1)*duration/len(spoken)} for i, char in enumerate(spoken)]
        engine_runner.run_isolated = recovery_infer
        asr_service.transcribe = recovery_asr
        qwen_forced_aligner.align_audio = align

    @app.get("/api/__content_acceptance/state", include_in_schema=False)
    def get_state():
        return state

    @app.post("/api/__content_acceptance/mode", include_in_schema=False)
    def mode(body: dict = Body(...)):
        assert body["mode"] in {"good", "bad"}
        state["mode"] = body["mode"]
        return state

    @app.post("/api/__content_acceptance/obsolete-placement", include_in_schema=False)
    def obsolete_placement(body: dict = Body(...)):
        # Seed a distinct generated-but-not-placed command with its frozen old
        # plan identity. A completed command is a valid idempotent no-op, not
        # an unprocessed stale delivery. All domain/outbox behavior stays real.
        from app.schemas.voice_studio import GenerationTask
        from app.services import database, history_store, video_localization_tts_handoff as handoff
        from app.services import video_localization_tts_handoff_store as outbox
        import hashlib
        import uuid

        history = history_store.get(body["result_id"])
        assert history is not None and history.project_id == project.project_id
        task = GenerationTask.model_validate(database.get_one("tasks", "task_id", history.task_id))
        draft = draft_store.get(project.project_id)
        source_workflow = next(item for item in draft.tts_tasks if item.generation_task_id == task.task_id)
        identity = uuid.uuid4().hex
        task_id, workflow_id, result_id = f"late_{identity}", f"workflow_{identity}", f"result_{identity}"
        task = task.model_copy(update={"task_id": task_id, "generation_id": task_id, "logs": []})
        task.parameters = {**task.parameters, "generation_id": task_id,
            "video_localization_workflow_id": workflow_id, "timeline_clip_id": f"clip_{identity}",
            "video_localization_execution_scope": "single_group",
            "video_localization_dubbing_plan_revision": body["plan_revision"],
            "video_localization_dubbing_group_id": body["group_id"],
            "video_localization_target_subtitle_ids": body["subtitle_ids"],
        }
        history = history.model_copy(update={"result_id": result_id, "task_id": task_id, "generation_id": task_id})
        workflow = source_workflow.model_copy(update={
            "workflow_id": workflow_id, "generation_task_id": task_id,
            "result_id": result_id, "timeline_clip_id": f"clip_{identity}",
            "status": "running", "completed_at": None,
            "stages": [source_workflow.stages[0], source_workflow.stages[1].model_copy(update={
                "status": "pending", "progress": 0, "completed_at": None,
                "error_code": None, "error_message": None,
            })],
        })
        database.upsert("tasks", task_id, task.model_dump(mode="json"))
        draft_store.save(project.project_id, draft.model_copy(update={
            "tts_tasks": [*draft.tts_tasks, workflow],
            "ui_state": {**draft.ui_state, "latest_tts_task_by_segment": {
                **draft.ui_state.get("latest_tts_task_by_segment", {}), task.segment_id: task_id,
            }},
        }), intent="runtime")
        before = draft.timeline_clips
        audio = Path(history.output_path)
        audio.resolve().relative_to(root.resolve())
        fingerprint = hashlib.sha256(audio.read_bytes()).hexdigest()
        handoff.persist_generated_history(task, history)
        if body["entrypoint"] == "immediate":
            assert handoff.place_generated_result_with_retry(task, history) is False
        else:
            assert handoff.replay_pending().abandoned == 1
        event = outbox.get(outbox.result_placement_event_id(task.task_id))
        assert event.status == "abandoned", event
        assert event.attempt_count == 1
        assert "VIDEO_LOCALIZATION_DUBBING_TASK_STALE" in event.last_error
        assert handoff.replay_pending().inspected == 0
        assert history_store.get(history.result_id) is not None
        assert hashlib.sha256(audio.read_bytes()).hexdigest() == fingerprint
        assert draft_store.get(project.project_id).timeline_clips == before
        return {"status": event.status, "attempts": event.attempt_count,
                "history_preserved": True, "audio_preserved": True, "timeline_preserved": True}

    frontend_distribution.install_frontend_distribution(app, environ={
        "VOICE_STUDIO_SERVE_FRONTEND": "1",
        "VOICE_STUDIO_FRONTEND_DIST": os.environ.get(
            "VOICE_STUDIO_FRONTEND_DIST", str(ROOT / "frontend" / "build")
        ),
    })
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3)


def api(base, path, body=None, expected=200, *, method=None):
    request = urllib.request.Request(base + "/api" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method)
    try:
        response = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    data = json.loads(response.read())
    if response.status != expected:
        raise AssertionError((path, response.status, data))
    return data



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--editorial", action="store_true")
    parser.add_argument("--fixed-dub-subtitles", action="store_true")
    arguments = parser.parse_args()
    if arguments.port in {5173, 18000}:
        parser.error("Production ports are forbidden")
    serve(
        arguments.serve_root,
        arguments.port,
        editorial=arguments.editorial,
        fixed_dub_subtitles=arguments.fixed_dub_subtitles,
    )
