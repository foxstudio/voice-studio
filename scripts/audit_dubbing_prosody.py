#!/usr/bin/env python3
"""Audit dubbing prosody: where the take breaks or joins against its own text.

The Skill's naturalness ladder starts from two defects that make a take hard to
follow: a pause inside a phrase that should run on, and a missing pause where a
new clause starts.  This script only measures them from durable evidence — the
take's aligned words, its per-boundary gaps and the punctuation of the frozen
line — and never edits or regenerates anything.

Usage
    audit_dubbing_prosody.py --project <id> [--json] [--min-gap-ms 300]
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
CLAUSE_PUNCTUATION = "。！？!?；;"
COMMA_PUNCTUATION = "，,、：:…—"
ALL_PUNCTUATION = CLAUSE_PUNCTUATION + COMMA_PUNCTUATION + "「」『』（）()《》〈〉\"' "


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


def punctuation_after(text: str, left: str, cursor: int = 0) -> tuple[str | None, int]:
    """Return the punctuation that follows ``left`` inside ``text``."""

    index = text.find(left, cursor)
    if index < 0:
        return None, cursor
    after = index + len(left)
    position = after
    while position < len(text) and text[position] in " \t":
        position += 1
    if position < len(text) and text[position] in ALL_PUNCTUATION:
        return text[position], after
    return None, after


def audit_boundaries(
    audit: dict[str, Any],
    *,
    min_gap_ms: int,
    max_join_gap_ms: int,
) -> list[dict[str, Any]]:
    """Flag pauses that split a phrase and clause breaks that ran together."""

    expected = str(audit.get("expected_spoken_text") or "")
    findings: list[dict[str, Any]] = []
    cursor = 0
    for boundary in audit.get("boundaries") or []:
        left = str(boundary.get("left_text") or "")
        right = str(boundary.get("right_text") or "")
        gap = int(boundary.get("final_gap_ms") or 0)
        overlap = int(boundary.get("final_overlap_ms") or 0)
        punct, cursor = punctuation_after(expected, left, cursor)
        code = None
        if gap >= min_gap_ms and punct is None:
            code = "pause_inside_phrase"
        elif punct and punct in CLAUSE_PUNCTUATION and gap <= max_join_gap_ms:
            code = "missing_clause_break"
        elif punct and punct in COMMA_PUNCTUATION and gap == 0:
            code = "missing_comma_break"
        if code:
            findings.append(
                {
                    "code": code,
                    "left_text": left,
                    "right_text": right,
                    "punctuation": punct,
                    "final_gap_ms": gap,
                    "final_overlap_ms": overlap,
                    "boundary_id": boundary.get("boundary_id"),
                }
            )
    return findings


def build_report(
    base_url: str,
    project_id: str,
    *,
    min_gap_ms: int,
    max_join_gap_ms: int,
) -> dict[str, Any]:
    localization = f"/api/projects/{project_id}/video-localization"
    projection = api(base_url, f"{localization}/timeline-projection")
    clips = [
        clip
        for clip in projection.get("timeline_clips") or []
        if str(clip.get("track_id")) == "dub"
    ]
    audits: dict[str, Any] = {}
    groups: dict[str, dict[str, Any]] = {}
    for clip in clips:
        group_id = str(clip.get("dubbing_group_id") or "")
        candidate_id = str(clip.get("candidate_id") or "")
        if not candidate_id or candidate_id in audits:
            continue
        try:
            audits[candidate_id] = api(
                base_url,
                f"{localization}/dubbing/candidates/{candidate_id}/semantic-boundaries",
            )
        except ApiError as exc:
            audits[candidate_id] = {"error": str(exc)}
        entry = groups.setdefault(
            group_id,
            {
                "group_id": group_id,
                "clip_ids": [],
                "text": str(clip.get("tts_target_text") or ""),
                "findings": [],
            },
        )
        entry["clip_ids"].append(clip.get("clip_id"))
    for clip in clips:
        candidate_id = str(clip.get("candidate_id") or "")
        group_id = str(clip.get("dubbing_group_id") or "")
        audit = audits.get(candidate_id) or {}
        if audit.get("error"):
            continue
        entry = groups[group_id]
        if not entry["text"]:
            entry["text"] = str(audit.get("expected_spoken_text") or "")
        for finding in audit_boundaries(
            audit,
            min_gap_ms=min_gap_ms,
            max_join_gap_ms=max_join_gap_ms,
        ):
            if finding not in entry["findings"]:
                entry["findings"].append(finding)
    offenders = [entry for entry in groups.values() if entry["findings"]]
    offenders.sort(key=lambda entry: len(entry["findings"]), reverse=True)
    return {
        "project_id": project_id,
        "min_gap_ms": min_gap_ms,
        "max_join_gap_ms": max_join_gap_ms,
        "clip_count": len(clips),
        "group_count": len(groups),
        "offender_count": len(offenders),
        "groups": offenders,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"项目 {report['project_id']}：{report['group_count']} 个已落轨组，"
        f"{report['offender_count']} 组存在断句问题",
    ]
    for entry in report["groups"]:
        lines.append(f"\n[{entry['group_id']}] {len(entry['findings'])} 处 · {entry['text'][:52]}")
        for finding in entry["findings"][:8]:
            label = {
                "pause_inside_phrase": "该连续却断开",
                "missing_clause_break": "该断句却连着",
                "missing_comma_break": "逗号处无停顿",
            }[finding["code"]]
            lines.append(
                f"    - {label}：「{finding['left_text']}｜{finding['right_text']}」"
                f"间隙 {finding['final_gap_ms']}ms"
                f"（标点 {finding['punctuation'] or '无'}）"
            )
        if len(entry["findings"]) > 8:
            lines.append(f"    … 其余 {len(entry['findings']) - 8} 处")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读审计配音的断句与停顿")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project", required=True)
    parser.add_argument("--min-gap-ms", type=int, default=300, help="无标点处超过该停顿视为异常断开")
    parser.add_argument("--max-join-gap-ms", type=int, default=100, help="句末标点处低于该停顿视为黏连")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(
            args.base_url,
            args.project,
            min_gap_ms=args.min_gap_ms,
            max_join_gap_ms=args.max_join_gap_ms,
        )
    except ApiError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 2 if report["offender_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
