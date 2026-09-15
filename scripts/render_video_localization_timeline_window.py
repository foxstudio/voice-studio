from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.video_localization import (  # noqa: E402
    draft_store,
    timeline_audio_renderer,
    timeline_audio_sources,
)


def render_dub_window(
    project_id: str,
    *,
    start_ms: int,
    end_ms: int,
    output_path: Path,
) -> dict[str, object]:
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("试听时间窗必须满足 0 <= start_ms < end_ms。")
    draft = draft_store.get(project_id)
    if draft is None:
        raise ValueError(f"找不到视频本土化项目：{project_id}")

    clips = [
        dict(item)
        for item in draft.timeline_clips
        if dict(item).get("track_id") == "dub"
        and int(dict(item).get("end_ms") or 0) > start_ms
        and int(dict(item).get("start_ms") or 0) < end_ms
    ]
    resolved_paths = timeline_audio_sources.resolve_dub_clip_audio_paths(
        draft,
        clips,
    )
    missing = [
        str(clip.get("clip_id") or "")
        for clip in clips
        if str(clip.get("clip_id") or "") not in resolved_paths
    ]
    if missing:
        raise ValueError(
            "试听区间存在找不到音频的配音片段：" + ", ".join(missing)
        )

    items: list[timeline_audio_renderer.TimelineAudioRenderItem] = []
    source_paths: dict[str, Path] = {}
    for clip in clips:
        clip_id = str(clip.get("clip_id") or "")
        clip_start_ms = int(clip.get("start_ms") or 0)
        clip_end_ms = int(clip.get("end_ms") or 0)
        intersection_start_ms = max(start_ms, clip_start_ms)
        intersection_end_ms = min(end_ms, clip_end_ms)
        if intersection_end_ms <= intersection_start_ms:
            continue
        source_start_ms = int(clip.get("source_start_ms") or 0) + (
            intersection_start_ms - clip_start_ms
        )
        duration_ms = intersection_end_ms - intersection_start_ms
        source_id = f"dub:{clip_id}"
        source_paths[source_id] = resolved_paths[clip_id]
        items.append(
            timeline_audio_renderer.TimelineAudioRenderItem(
                item_id=source_id,
                source_id=source_id,
                track_id="dub",
                timeline_start_ms=intersection_start_ms - start_ms,
                timeline_end_ms=intersection_end_ms - start_ms,
                source_start_ms=source_start_ms,
                source_end_ms=source_start_ms + duration_ms,
            )
        )
    if not items:
        raise ValueError("试听区间内没有配音片段。")

    output = timeline_audio_renderer.render_timeline_audio(
        timeline_audio_renderer.TimelineAudioRenderInput(
            timeline_duration_ms=end_ms - start_ms,
            channel_mode="mono",
            items=items,
        ),
        source_paths=source_paths,
        output_path=output_path,
    )
    return {
        "project_id": project_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "clip_ids": [
            item.item_id.removeprefix("dub:") for item in items
        ],
        "output_path": str(output_path.resolve()),
        **output.model_dump(mode="json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读渲染视频本土化时间线中的一段中文配音。"
    )
    parser.add_argument("project_id")
    parser.add_argument("--start-ms", required=True, type=int)
    parser.add_argument("--end-ms", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = render_dub_window(
        args.project_id,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
