#!/usr/bin/env python3
"""Audit that the spoken line still matches what the viewer reads.

A take is generated from ``tts_text`` while the viewer sees ``text``; the
product treats an empty ``tts_text`` as "speak the on-screen text" (the domain
uses ``tts_text or text``).  This script applies only a conservative display
normalization — trim and collapse equivalent whitespace runs — and reports the
cues whose text still differs.  It deliberately keeps digits, decimal points,
signs, punctuation, letter case and word boundaries, because any of them can
change what is read aloud.  A difference is a cue for the Agent to check the
semantics with context; it is not proof of a wrong meaning, and a match only
means the display text is the same, never that the audio pronunciation is
right.

This script only reads the draft and reports differences it can show; it never
changes text or regenerates audio.

Usage
    audit_dubbing_script_consistency.py --project <id> [--json]
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
# Only collapse a run of whitespace (including full-width) into one space and
# trim the ends.  Every other character (decimal points, signs, punctuation,
# case, script word boundaries) can change what is read and is left as is.
_WHITESPACE = re.compile(r"\s+")


class ApiError(RuntimeError):
    """A failed public API call, carrying the server's own message."""


def api(base_url: str, path: str, *, timeout: float = 180.0) -> Any:
    request = urllib.request.Request(f"{base_url}{path}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise ApiError(f"HTTP {exc.code}: {detail[:300]}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"无法访问 {base_url}{path}：{exc.reason}") from None


def normalize_text(value: str) -> str:
    """Collapse only display whitespace; keep everything that can be read."""

    return _WHITESPACE.sub(" ", str(value or "")).strip()


def audit_cues(draft: dict[str, Any]) -> dict[str, Any]:
    """Compare the read line with the effective spoken line, per cue."""

    findings: list[dict[str, Any]] = []
    unchecked: list[dict[str, Any]] = []
    checked = 0
    same = 0
    for cue in draft.get("localized_subtitles") or []:
        subtitle_id = str(cue.get("subtitle_id") or "")
        text = str(cue.get("text") or "")
        if not subtitle_id:
            unchecked.append(
                {
                    "subtitle_id": "",
                    "reason": "missing_subtitle_id",
                    "detail": "字幕缺少 subtitle_id，无法定位对比结果",
                }
            )
            continue
        if not text.strip():
            unchecked.append(
                {
                    "subtitle_id": subtitle_id,
                    "reason": "missing_text",
                    "detail": "字幕缺少上屏文本，无法与朗读台词对比",
                }
            )
            continue
        raw_tts = str(cue.get("tts_text") or "")
        # Product semantics: an absent/empty tts_text legally means "read the
        # on-screen text", so it is a checked, matching case by definition.
        fallback = not raw_tts.strip()
        spoken = text if fallback else raw_tts
        checked += 1
        if normalize_text(spoken) == normalize_text(text):
            same += 1
            continue
        findings.append(
            {
                "code": "spoken_text_differs",
                "subtitle_id": subtitle_id,
                "text": text,
                "tts_text": raw_tts,
                "spoken_effective": spoken,
                "tts_fallback_to_text": fallback,
                "detail": "上屏字幕与朗读台词按展示空白规范化后仍有差异，待 Agent 结合上下文核对",
            }
        )
    return {
        "findings": findings,
        "unchecked": unchecked,
        "checked_count": checked,
        "same_count": same,
    }


def build_report(base_url: str, project_id: str) -> dict[str, Any]:
    draft = api(base_url, f"/api/projects/{project_id}/video-localization")
    draft = draft.get("video_localization") or draft
    cues = draft.get("localized_subtitles") or []
    result = audit_cues(draft)
    findings = result["findings"]
    unchecked = result["unchecked"]
    if findings:
        status = "findings"
    elif cues and not unchecked:
        status = "checked"
    else:
        status = "incomplete"
    return {
        "project_id": project_id,
        "cue_count": len(cues),
        "finding_count": len(findings),
        "checked_count": result["checked_count"],
        "same_count": result["same_count"],
        "unchecked_count": len(unchecked),
        "unchecked": unchecked,
        "status": status,
        "findings": findings,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"项目 {report['project_id']}：{report['cue_count']} 条字幕，"
        f"{report['finding_count']} 处展示空白规范化后仍有文本差异（待 Agent 核对），"
        f"其余 {report.get('same_count', '?')} 条规范化后相同。",
        "说明：规范化只折叠空白并去掉首尾空白；数字、小数点、正负号、标点、"
        "大小写与英文词界均保留。“相同”仅表示展示文本一致，"
        "不代表音频发音正确。",
    ]
    for item in report["findings"]:
        lines.append(f"\n[{item['code']}] {item['subtitle_id']}")
        lines.append(f"    字幕：{item['text'][:60]}")
        lines.append(f"    朗读：{item['tts_text'][:60] or '（空，按产品语义等于上屏文本）'}")
        lines.append(f"    {item['detail']}")
    if report.get("unchecked"):
        lines.append("\n未检查字幕：")
        for item in report["unchecked"][:12]:
            lines.append(f"    - {item.get('subtitle_id') or '（无 ID）'}：{item['detail']}")
        if len(report["unchecked"]) > 12:
            lines.append(f"    … 其余 {len(report['unchecked']) - 12} 条")
    if report.get("status") == "incomplete":
        lines.append("\n结论：本次未能覆盖全部字幕，不能宣称朗读一致性已确认。")
    elif report.get("status") == "findings":
        lines.append("\n结论：以上差异需 Agent 结合可用上下文核对，脚本不判定对错，也不保证没有误报。")
    else:
        lines.append("\n结论：所有字幕规范化后展示文本一致；音频发音仍需 Agent 结合音频证据与可得听音核对。")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读检查朗读台词与上屏字幕是否一致")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(args.base_url, args.project)
    except ApiError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    if report["finding_count"]:
        return 2
    if report.get("unchecked_count") or not report.get("cue_count"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
