#!/usr/bin/env python3
"""Audit per-subtitle alignment inside managed dubbing clips.

A generated take may cover several subtitles.  This script measures how far the
take's forced-aligned words start from the *intermediate translation subtitle*
start times held in the durable read model.  That reference is a diagnostic
signal, not the final dubbing-subtitle acceptance criterion: the intermediate
translation track and the final dubbing track are different products and need
not start every subtitle in its own box.

The script only reads evidence — it never edits, places or regenerates
anything — and it distinguishes checked clips from clips whose evidence was
missing, stale or unreachable, so a cleared run cannot silently hide an
unchecked clip.

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
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:5173"
REFERENCE_BASIS = "intermediate_localized_subtitle_start"
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
    """Subtitles of one clip in playback order, with their own target start.

    A referenced subtitle that is missing from the draft is kept as an
    unmatched segment instead of being dropped, so the clip cannot be reported
    as fully checked.
    """

    segments = []
    for subtitle_id in clip.get("target_subtitle_ids") or []:
        key = str(subtitle_id)
        subtitle = subtitles.get(key)
        if subtitle is None:
            segments.append(
                {
                    "subtitle_id": key,
                    "target_start_ms": None,
                    "normalized": "",
                    "missing": True,
                }
            )
            continue
        start = subtitle.get("start_ms")
        segments.append(
            {
                "subtitle_id": key,
                "target_start_ms": int(start) if isinstance(start, (int, float)) else None,
                "normalized": normalize(subtitle.get("text")),
                "missing": False,
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
        matched = bool(target) and consumed.startswith(target)
        results.append(
            {
                **segment,
                "matched": matched,
                "word_start_ms": offsets[start_index]["start_ms"] if matched and start_index is not None else None,
                "word_end_ms": offsets[end_index - 1]["end_ms"] if matched and end_index > cursor else None,
                "consumed": consumed,
            }
        )
        if matched:
            cursor = end_index
    return results


def source_range_issue(clip: dict[str, Any]) -> str | None:
    """Why the clip's source crop cannot scope the word evidence, or ``None``."""

    start = clip.get("source_start_ms")
    end = clip.get("source_end_ms")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return "配音片段缺少 source_start_ms/source_end_ms，无法校验逐词时间范围"
    if start < 0 or end <= start:
        return f"配音片段源范围非法（source_start_ms={start}, source_end_ms={end}）"
    return None


def audit_clip(
    clip: dict[str, Any],
    subtitles: dict[str, dict[str, Any]],
    record: dict[str, Any],
    *,
    threshold_ms: int,
) -> dict[str, Any]:
    candidate_id = str(clip.get("candidate_id") or "")
    clip_start_ms = int(clip.get("start_ms") or 0)
    source_start_ms = int(clip.get("source_start_ms") or 0)
    source_end_ms = int(clip.get("source_end_ms") or 0)
    rows: list[dict[str, Any]] = []
    base = {
        "clip_id": clip.get("clip_id"),
        "dubbing_group_id": clip.get("dubbing_group_id"),
        "subtitle_count": len(clip.get("target_subtitle_ids") or []),
        "clip_start_ms": clip_start_ms,
        "clip_end_ms": int(clip.get("end_ms") or 0),
        "target_end_ms": int(clip.get("target_end_ms") or 0),
        "reference_basis": REFERENCE_BASIS,
    }

    def finished(
        *,
        checked: bool,
        reason: str | None,
        worst: int,
    ) -> dict[str, Any]:
        return {
            **base,
            "checked": checked,
            "unchecked_reason": reason,
            "worst_deviation_ms": worst,
            "over_threshold": bool(checked and worst > threshold_ms),
            "subtitles": rows,
        }

    if not candidate_id:
        return finished(
            checked=False,
            reason="配音片段缺少 candidate_id，无法取得逐词时间",
            worst=0,
        )
    if record.get("error"):
        return finished(
            checked=False,
            reason=f"逐词证据接口调用失败：{record['error']}",
            worst=0,
        )
    audit = record.get("audit") or {}
    words = word_offsets(audit.get("aligned_words") or [])
    if not words:
        return finished(
            checked=False,
            reason="候选缺少可用逐词时间（aligned_words 为空）",
            worst=0,
        )
    source_issue = source_range_issue(clip)
    if source_issue:
        return finished(checked=False, reason=source_issue, worst=0)

    segments = subtitle_segments(clip, subtitles)
    unmatched = 0
    unmatched_notes: list[str] = []
    deviations: list[int] = []
    for row in match_segments(segments, words):
        if row.get("missing"):
            unmatched += 1
            note = "草稿中找不到该字幕，无法取参考起点"
            unmatched_notes.append(f"{row['subtitle_id']}：{note}")
            rows.append(
                {
                    "subtitle_id": row["subtitle_id"],
                    "expected_start_ms": None,
                    "reference_start_ms": None,
                    "deviation_ms": None,
                    "matched": False,
                    "note": note,
                }
            )
            continue
        if row["target_start_ms"] is None:
            unmatched += 1
            note = "参考字幕缺少 start_ms，无法作为参考起点"
            unmatched_notes.append(f"{row['subtitle_id']}：{note}")
            rows.append(
                {
                    "subtitle_id": row["subtitle_id"],
                    "expected_start_ms": None,
                    "reference_start_ms": None,
                    "deviation_ms": None,
                    "matched": False,
                    "note": note,
                }
            )
            continue
        if not row["matched"] or row["word_start_ms"] is None:
            unmatched += 1
            note = "无法在音频逐词时间中定位该字幕"
            unmatched_notes.append(f"{row['subtitle_id']}：{note}")
            rows.append(
                {
                    "subtitle_id": row["subtitle_id"],
                    "expected_start_ms": row["target_start_ms"],
                    "reference_start_ms": row["target_start_ms"],
                    "deviation_ms": None,
                    "matched": False,
                    "note": note,
                }
            )
            continue
        if (
            row["word_start_ms"] < source_start_ms
            or (row["word_end_ms"] or row["word_start_ms"]) > source_end_ms
        ):
            unmatched += 1
            note = (
                "逐词时间落在该片段源范围 "
                f"[{source_start_ms}, {source_end_ms}] 之外，证据不可用"
            )
            unmatched_notes.append(f"{row['subtitle_id']}：{note}")
            rows.append(
                {
                    "subtitle_id": row["subtitle_id"],
                    "expected_start_ms": row["target_start_ms"],
                    "reference_start_ms": row["target_start_ms"],
                    "deviation_ms": None,
                    "matched": False,
                    "note": note,
                }
            )
            continue
        actual_start = clip_start_ms + (row["word_start_ms"] - source_start_ms)
        deviation = actual_start - row["target_start_ms"]
        deviations.append(deviation)
        rows.append(
            {
                "subtitle_id": row["subtitle_id"],
                "expected_start_ms": row["target_start_ms"],
                "reference_start_ms": row["target_start_ms"],
                "actual_start_ms": actual_start,
                "deviation_ms": deviation,
                "matched": True,
            }
        )

    worst = max((abs(value) for value in deviations), default=0)
    if unmatched:
        return finished(
            checked=False,
            reason=(
                f"{unmatched} 条字幕缺少可用匹配证据，不能据此判定通过："
                + "；".join(dict.fromkeys(unmatched_notes))
            ),
            worst=worst,
        )
    return finished(checked=True, reason=None, worst=worst)


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
            audit_by_candidate[candidate_id] = {
                "audit": api(
                    base_url,
                    f"{localization}/dubbing/candidates/{candidate_id}/semantic-boundaries",
                )
            }
        except ApiError as exc:
            audit_by_candidate[candidate_id] = {"error": str(exc)}
    multi_clips = [
        clip for clip in clips if len(clip.get("target_subtitle_ids") or []) > 1
    ]
    audits = [
        audit_clip(
            clip,
            subtitles,
            audit_by_candidate.get(str(clip.get("candidate_id") or "")) or {},
            threshold_ms=threshold_ms,
        )
        for clip in multi_clips
    ]
    offenders = [item for item in audits if item["over_threshold"]]
    unchecked = [
        {
            "clip_id": item["clip_id"],
            "dubbing_group_id": item["dubbing_group_id"],
            "reason": "unmatched_subtitle_evidence",
            "detail": item["unchecked_reason"],
        }
        for item in audits
        if not item["checked"]
    ]
    if offenders:
        status = "findings"
    elif audits and not unchecked:
        status = "checked"
    else:
        status = "incomplete"
    return {
        "project_id": project_id,
        "threshold_ms": threshold_ms,
        "reference_basis": REFERENCE_BASIS,
        "multi_subtitle_clip_count": len(audits),
        "over_threshold_count": len(offenders),
        "checked_clip_count": sum(1 for item in audits if item["checked"]),
        "unchecked_clip_count": len(unchecked),
        "unchecked": unchecked,
        "status": status,
        "clips": sorted(
            audits, key=lambda item: abs(item["worst_deviation_ms"]), reverse=True
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"项目 {report['project_id']}：{report['multi_subtitle_clip_count']} 个多字幕片段，"
        f"{report['over_threshold_count']} 个超过 {report['threshold_ms']}ms 参考偏差",
        "说明：偏差以中间翻译字幕起点为参考（诊断量），不代表最终配音字幕错位，"
        "中间字幕也不需要一盒一切。",
        f"已检查 {report.get('checked_clip_count', '?')} 个，"
        f"未检查 {report.get('unchecked_clip_count', '?')} 个。",
    ]
    for clip in report["clips"]:
        if not clip["checked"]:
            flag = "未检查"
        elif clip["over_threshold"]:
            flag = "参考偏差"
        else:
            flag = "通过"
        lines.append(
            f"[{flag}] {clip['dubbing_group_id']} {clip['clip_id']} "
            f"最大参考偏差 {clip['worst_deviation_ms']}ms "
            f"（片段时间 {clip['clip_start_ms']}–{clip['clip_end_ms']}，"
            f"目标结束 {clip['target_end_ms']}）"
        )
        if not clip["checked"]:
            lines.append(f"    未检查原因：{clip['unchecked_reason']}")
        for row in clip["subtitles"]:
            if row.get("deviation_ms") is None:
                lines.append(f"    -  {row['subtitle_id']}：{row['note']}")
            else:
                lines.append(
                    f"    -  {row['subtitle_id']} 参考起点 {row['reference_start_ms']}ms "
                    f"实际 {row['actual_start_ms']}ms 偏差 {row['deviation_ms']}ms"
                )
    if report.get("status") == "incomplete":
        lines.append("\n结论：本次未能覆盖全部多字幕片段，不能宣称对齐已验收。")
    elif report.get("status") == "findings":
        lines.append("\n结论：以上是相对中间翻译字幕起点的诊断偏差，请 Agent 结合上下文核对后再决定是否调整。")
    else:
        lines.append("\n结论：已检查片段相对参考起点的偏差都在阈值内；这不等于最终配音字幕验收。")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读审计配音片段内部相对中间翻译字幕起点的对齐偏差",
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
    if report["over_threshold_count"]:
        return 2
    if report.get("unchecked_clip_count") or not report.get("multi_subtitle_clip_count"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
