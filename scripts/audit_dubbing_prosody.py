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


def _group_key(clip: dict[str, Any]) -> str:
    group_id = str(clip.get("dubbing_group_id") or "")
    return group_id or f"clip:{clip.get('clip_id')}"


# Minimum fields the boundary heuristic reads.  A boundary missing any of them
# must not be silently treated as a 0ms gap.
BOUNDARY_REQUIRED_FIELDS = (
    "boundary_id",
    "left_word_id",
    "right_word_id",
    "left_text",
    "right_text",
    "final_gap_ms",
    "final_overlap_ms",
)


def _valid_aligned_word(word: Any) -> bool:
    """Whether one entry is a structurally valid DubbingCandidateAlignedWord."""

    if not isinstance(word, dict):
        return False
    word_id = word.get("word_id")
    text = word.get("text")
    start = word.get("start_ms")
    end = word.get("end_ms")
    if not isinstance(word_id, str) or not word_id.strip():
        return False
    if not isinstance(text, str) or not text.strip():
        return False
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    return start >= 0 and end >= start


def boundary_evidence_issue(audit: dict[str, Any]) -> str | None:
    """Why the audit's boundary evidence is unusable, or ``None``.

    The empty-boundary case is only valid for exactly one structurally valid
    aligned word; several words without adjacent boundaries, or a malformed
    word list, is unusable evidence rather than "nothing to check".
    """

    if not str(audit.get("expected_spoken_text") or ""):
        return "缺少 expected_spoken_text"
    words = audit.get("aligned_words")
    if not isinstance(words, list) or not words:
        return "缺少 aligned_words 词级证据"
    if not all(_valid_aligned_word(word) for word in words):
        return "aligned_words 含结构不合法的对齐词"
    boundaries = audit.get("boundaries")
    if not isinstance(boundaries, list):
        return "缺少 boundaries 边界列表"
    if not boundaries:
        if len(words) != 1:
            return f"没有相邻边界，但 aligned_words 有 {len(words)} 个词"
        return None
    seen: set[str] = set()
    for index, boundary in enumerate(boundaries):
        if not isinstance(boundary, dict):
            return f"第 {index} 个边界不是对象"
        boundary_id = str(boundary.get("boundary_id") or "")
        if boundary_id in seen:
            return f"第 {index} 个边界 boundary_id 重复：{boundary_id}"
        seen.add(boundary_id)
        for field in BOUNDARY_REQUIRED_FIELDS:
            value = boundary.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                return f"第 {index} 个边界缺少 {field}"
        for field in ("final_gap_ms", "final_overlap_ms"):
            if not isinstance(boundary.get(field), (int, float)):
                return f"第 {index} 个边界 {field} 不是数值"
    return None


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
    audits: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {}
    unchecked: list[dict[str, Any]] = []
    checked_clip_count = 0
    no_boundary_clip_count = 0

    def unchecked_row(clip: dict[str, Any], reason: str, detail: str) -> None:
        unchecked.append(
            {
                "clip_id": clip.get("clip_id"),
                "dubbing_group_id": _group_key(clip),
                "reason": reason,
                "detail": detail,
            }
        )

    for clip in clips:
        group_key = _group_key(clip)
        entry = groups.setdefault(
            group_key,
            {
                "group_id": group_key,
                "clip_ids": [],
                "text": str(clip.get("tts_target_text") or ""),
                "findings": [],
                "has_checked_clip": False,
            },
        )
        entry["clip_ids"].append(clip.get("clip_id"))
        candidate_id = str(clip.get("candidate_id") or "")
        if not candidate_id:
            unchecked_row(clip, "missing_candidate_id", "配音片段缺少 candidate_id，无法取证")
            continue
        if candidate_id not in audits:
            try:
                audits[candidate_id] = {
                    "audit": api(
                        base_url,
                        f"{localization}/dubbing/candidates/{candidate_id}/semantic-boundaries",
                    )
                }
            except ApiError as exc:
                audits[candidate_id] = {"error": str(exc)}
        record = audits[candidate_id]
        if record.get("error"):
            unchecked_row(clip, "api_error", f"断句证据接口调用失败：{record['error']}")
            continue
        audit = record.get("audit") or {}
        issue = boundary_evidence_issue(audit)
        if issue:
            unchecked_row(clip, "missing_evidence", f"断句证据不完整：{issue}")
            continue
        entry["has_checked_clip"] = True
        checked_clip_count += 1
        if not audit.get("boundaries"):
            no_boundary_clip_count += 1
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
    unchecked_clip_count = len(unchecked)
    if offenders:
        status = "findings"
    elif clips and not unchecked_clip_count:
        status = "checked"
    else:
        status = "incomplete"
    return {
        "project_id": project_id,
        "min_gap_ms": min_gap_ms,
        "max_join_gap_ms": max_join_gap_ms,
        "clip_count": len(clips),
        "group_count": len(groups),
        "offender_count": len(offenders),
        "checked_clip_count": checked_clip_count,
        "no_adjacent_boundary_clip_count": no_boundary_clip_count,
        "unchecked_clip_count": unchecked_clip_count,
        "unchecked": unchecked,
        "status": status,
        "assessment": "heuristic_pending_semantic_review",
        "groups": offenders,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"项目 {report['project_id']}：{report['group_count']} 个已落轨组，"
        f"{report['offender_count']} 组存在断句疑点"
        f"（启发式提示，待语义评估）",
        f"已检查 {report.get('checked_clip_count', '?')} 个配音片段"
        f"（其中 {report.get('no_adjacent_boundary_clip_count', 0)} 个无相邻边界），"
        f"未检查 {report.get('unchecked_clip_count', '?')} 个。",
    ]
    for entry in report["groups"]:
        lines.append(f"\n[{entry['group_id']}] {len(entry['findings'])} 处疑点 · {entry['text'][:52]}")
        for finding in entry["findings"][:8]:
            label = {
                "pause_inside_phrase": "无标点处有较长停顿（待语义评估）",
                "missing_clause_break": "句末标点处几乎无停顿（待语义评估）",
                "missing_comma_break": "逗号处几乎无停顿（待语义评估）",
            }[finding["code"]]
            lines.append(
                f"    - {label}：「{finding['left_text']}｜{finding['right_text']}」"
                f"间隙 {finding['final_gap_ms']}ms"
                f"（紧邻标点 {finding['punctuation'] or '无'}）"
            )
        if len(entry["findings"]) > 8:
            lines.append(f"    … 其余 {len(entry['findings']) - 8} 处")
    if report.get("unchecked"):
        lines.append("\n未检查的配音片段：")
        for item in report["unchecked"][:12]:
            lines.append(
                f"    - {item.get('dubbing_group_id')} {item.get('clip_id')}："
                f"{item.get('detail')}"
            )
        if len(report["unchecked"]) > 12:
            lines.append(f"    … 其余 {len(report['unchecked']) - 12} 个")
    if report.get("status") == "incomplete":
        lines.append("\n结论：本次未能覆盖全部配音片段，不能据此宣称全片断句已确认。")
    elif report.get("status") == "findings":
        lines.append("\n结论：以上为可复核的启发式疑点，需 Agent 结合上下文核对，脚本不判定“该连/该停”。")
    else:
        lines.append("\n结论：已检查片段的启发式规则均未命中；语义正确性仍需 Agent 结合音频证据与可得听音核对。")
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
    if report["offender_count"]:
        return 2
    if report.get("unchecked_clip_count") or not report.get("clip_count"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
