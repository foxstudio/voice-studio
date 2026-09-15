from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    GenerationTask,
    ProjectCreate,
    ScriptSegment,
    SegmentStatus,
)
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
    task_queue,
)


def _configure(tmp_path: Path) -> None:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )


def test_noop_segment_projection_preserves_project_time_and_revision(
    tmp_path: Path,
):
    _configure(tmp_path)
    project = project_store.create_project(
        ProjectCreate(name="投影写放大回归")
    )
    project.segments = [
        ScriptSegment(
            segment_id="segment-1",
            index=0,
            text="测试文本",
            status=SegmentStatus.completed,
            result_audio_id="audio-1",
            result_id="result-1",
        )
    ]
    project = project_store.save_project(project)
    baseline_updated_at = project.updated_at
    baseline_revision = project._repository_revision

    video_task = GenerationTask(
        task_id="task-video",
        generation_id="task-video",
        engine_id="omnivoice",
        project_id=project.project_id,
        segment_id="localized_0001",
        localized_subtitle_id="localized_0001",
        bind_to_video_localization=True,
        input_text="测试台词",
        parameters={"source": "video_localization"},
    )
    task_queue._update_project_segment(
        video_task,
        "audio-video",
        "result-video",
        SegmentStatus.completed,
    )
    assert (
        project_store.update_segment_result(
            project.project_id,
            "missing-segment",
            "audio-1",
            "result-1",
            SegmentStatus.completed,
        )
        is False
    )
    assert (
        project_store.update_segment_result(
            project.project_id,
            "segment-1",
            "audio-1",
            "result-1",
            SegmentStatus.completed,
        )
        is False
    )

    unchanged = project_store.get_project(project.project_id)
    assert unchanged is not None
    assert unchanged.updated_at == baseline_updated_at
    assert unchanged._repository_revision == baseline_revision

    assert (
        project_store.update_segment_result(
            project.project_id,
            "segment-1",
            "audio-2",
            "result-2",
            SegmentStatus.completed,
        )
        is True
    )
    changed = project_store.get_project(project.project_id)
    assert changed is not None
    assert changed.updated_at != baseline_updated_at
    assert changed._repository_revision == baseline_revision + 1
