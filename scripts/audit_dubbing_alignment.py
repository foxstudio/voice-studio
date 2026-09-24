#!/usr/bin/env python3
"""Audit per-subtitle alignment inside managed dubbing clips.

A generated take may cover several subtitles.  The Skill expects every one of
those subtitles to start where its own source cue starts, either by trimming
the gaps or by splitting the take at proven word boundaries.  This script only
measures that alignment from the durable read model — it never edits, places or
regenerates anything — so a run can report the real偏差 before anyone changes a
timeline.

Usage
    audit_dubbing_alignment.py --project <id> [--threshold-ms 250] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:5173"
_PUNCTUATION = "，。！？；：、…—,.!?;:~「」『』（）()《》〈〉\"' "


class ApiError(RuntimeError):
    """A failed public API call, carrying the server's own message."""


def api(base_url: str, path: str, *, timeout: float = 120.0) -> Any:
    request = urllib.request.Request(f"{base_url}{path}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise ApiError(f"HTTP {exc.code}: {detail[:300]}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"无法访问 {base_url}{path}：{exc.reason}") from None


def normalize(text: str) -> str:
    """Compare only the characters that carry audible content."""

    return re.sub(rf"[{re.escape(_PUNCTUATION)}]", "", str(text or ""))


def subtitle_segments(
    clip: dict[str, Any],
    subtitles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Subtitles of one clip in playback order, with their own target start."""

    segments = []
    for subtitle_id in clip.get("target_subtitle_ids") or []:
        subtitle = subtitles.get(str(subtitle_id))
        if subtitle is None:
            continue
        segments.append(
            {
                "subtitle_id": str(subtitle_id),
                "target_start_ms": int(subtitle.get("start_ms") or 0),
                "normalized": normalize(subtitle.get("text")),
            }
        )
    return segments


def word_offsets(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offsets = []
    for word in words:
        text = normalize(word.get("text"))
        if not text:
            continue
        offsets.append(
            {
                "text": text,
                "start_ms": int(word.get("start_ms") or 0),
                "end_ms": int(word.get("end_ms") or 0),
            }
        )
    return offsets


def match_segments(
    segments: list[dict[str, Any]],
    offsets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Locate each subtitle inside the take by walking the words in order."""

    cursor = 0
    results = []
    for segment in segments:
        target = segment["normalized"]
        consumed = ""
        start_index = None
        end_index = cursor
        while end_index < len(offsets) and len(consumed) < len(target):
            if start_index is None:
                start_index = end_index
            consumed += offsets[end_index]["text"]
            end_index += 1
        matched = target and consumed.startswith(target)
        results.append(
            {
                **segment,
                "matched": bool(matched),
                "word_start_ms": offsets[start_index]["start_ms"] if matched and start_index is not None else None,
                "consumed": consumed,
            }
        )
        if matched:
            cursor = end_index
    return results


def audit_clip(
    clip: dict[str, Any],
    subtitles: dict[str, dict[str, Any]],
    audit_by_candidate: dict[str, dict[str, Any]],
    *,
    threshold_ms: int,
) -> dict[str, Any]:
    candidate_id = str(clip.get("candidate_id") or "")
    audit = audit_by_candidate.get(candidate_id) or {}
    words = word_offsets(audit.get("aligned_words") or [])
    segments = subtitle_segments(clip, subtitles)
    clip_start_ms = int(clip.get("start_ms") or 0)
    source_start_ms = int(clip.get("source_start_ms") or 0)
    rows = []
    for row in match_segments(segments, words):
        if not row["matched"] or row["word_start_ms"] is None:
            rows.append(
                {
                    "subtitle_id": row["subtitle_id"],
                    "deviation_ms": None,
                    "note": "无法在音频逐词时间中定位该字幕",
                }
            )
            continue
        actual_start = clip_start_ms + (row["word_start_ms"] - source_start_ms)
        deviation = actual_start - row["target_start_ms"]
        rows.append(
            {
                "subtitle_id": row["subtitle_id"],
                "expected_start_ms": row["target_start_ms"],
                "actual_start_ms": actual_start,
                "deviation_ms": deviation,
            }
        )
    worst = max(
        (abs(row["deviation_ms"]) for row in rows if row.get("deviation_ms") is not None),
        default=0,
    )
    return {
        "clip_id": clip.get("clip_id"),
        "dubbing_group_id": clip.get("dubbing_group_id"),
        "subtitle_count": len(segments),
        "clip_start_ms": clip_start_ms,
        "clip_end_ms": int(clip.get("end_ms") or 0),
        "target_end_ms": int(clip.get("target_end_ms") or 0),
        "worst_deviation_ms": worst,
        "over_threshold": worst > threshold_ms,
        "subtitles": rows,
    }


def build_report(
    base_url: str,
    project_id: str,
    *,
    threshold_ms: int,
) -> dict[str, Any]:
    localization = f"/api/projects/{project_id}/video-localization"
    projection = api(base_url, f"{localization}/timeline-projection")
    draft = api(base_url, localization)
    draft = draft.get("video_localization") or draft
    subtitles = {
        str(item.get("subtitle_id")): item
        for item in draft.get("localized_subtitles") or []
    }
    clips = [
        clip
        for clip in projection.get("timeline_clips") or []
        if str(clip.get("track_id")) == "dub"
    ]
    audit_by_candidate: dict[str, dict[str, Any]] = {}
    for clip in clips:
        candidate_id = str(clip.get("candidate_id") or "")
        if not candidate_id or candidate_id in audit_by_candidate:
            continue
        try:
            audit_by_candidate[candidate_id] = api(
                base_url,
                f"{localization}/dubbing/candidates/{candidate_id}/semantic-boundaries",
            )
        except ApiError as exc:
            audit_by_candidate[candidate_id] = {"error": str(exc)}
    audits = [
        audit_clip(clip, subtitles, audit_by_candidate, threshold_ms=threshold_ms)
        for clip in clips
        if len(clip.get("target_subtitle_ids") or []) > 1
    ]
    offenders = [item for item in audits if item["over_threshold"]]
    return {
        "project_id": project_id,
        "threshold_ms": threshold_ms,
        "multi_subtitle_clip_count": len(audits),
        "over_threshold_count": len(offenders),
        "clips": sorted(audits, key=lambda item: item["worst_deviation_ms"], reverse=True),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"项目 {report['project_id']}：{report['multi_subtitle_clip_count']} 个多字幕片段，"
        f"{report['over_threshold_count']} 个超过 {report['threshold_ms']}ms 对齐偏差",
    ]
    for clip in report["clips"]:
        flag = "偏差" if clip["over_threshold"] else "通过"
        lines.append(
            f"[{flag}] {clip['dubbing_group_id']} {clip['clip_id']} "
            f"最大偏差 {clip['worst_deviation_ms']}ms "
            f"（片段时间 {clip['clip_start_ms']}–{clip['clip_end_ms']}，"
            f"目标结束 {clip['target_end_ms']}）"
        )
        for row in clip["subtitles"]:
            if row.get("deviation_ms") is None:
                lines.append(f"    -  {row['subtitle_id']}：{row['note']}")
            else:
                lines.append(
                    f"    -  {row['subtitle_id']} 期望 {row['expected_start_ms']}ms "
                    f"实际 {row['actual_start_ms']}ms 偏差 {row['deviation_ms']}ms"
                )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读审计配音片段内部的逐字幕对齐偏差",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project", required=True)
    parser.add_argument("--threshold-ms", type=int, default=250)
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(
            args.base_url,
            args.project,
            threshold_ms=args.threshold_ms,
        )
    except ApiError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 2 if report["over_threshold_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
