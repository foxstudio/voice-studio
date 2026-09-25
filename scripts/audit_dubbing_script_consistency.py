#!/usr/bin/env python3
"""Audit that the spoken line still matches what the viewer reads.

A take is generated from ``tts_text`` while the viewer sees ``text``.  They may
differ in ways that are deliberate (digits spelled out, acronyms spaced so TTS
reads them letter by letter) and in ways that are wrong: a proper noun written
in one form but spoken in another, a line that drifted while it was edited, a
deliberate simplification that never reached the other field.

This script only reads the draft and reports the differences it can prove; it
never changes text or regenerates audio.

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
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\.]*")
HAN = re.compile(r"[\u3400-\u9fff]")
MAX_CHARACTER_DRIFT = 4


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


def fold_spacing(value: str) -> str:
    """Ignore the spacing a TTS normalizer inserts between scripts."""

    return re.sub(r"\s+", "", str(value or "")).lower()


def latin_words(value: str) -> list[str]:
    return [word for word in LATIN_WORD.findall(str(value or "")) if len(word) > 1]


def han_count(value: str) -> int:
    return len(HAN.findall(str(value or "")))


def audit_cues(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """Differences between the read line and the spoken line, per cue."""

    findings: list[dict[str, Any]] = []
    for cue in draft.get("localized_subtitles") or []:
        subtitle_id = str(cue.get("subtitle_id") or "")
        text = str(cue.get("text") or "")
        spoken = str(cue.get("tts_text") or "")
        if not text or not spoken:
            continue
        folded = fold_spacing(spoken)
        missing = [word for word in latin_words(text) if word.lower() not in folded]
        if missing:
            findings.append(
                {
                    "code": "latin_word_not_spoken",
                    "subtitle_id": subtitle_id,
                    "text": text,
                    "tts_text": spoken,
                    "detail": "字幕里的英文词没有出现在朗读台词里：" + "、".join(missing),
                }
            )
        # A transliteration that replaced a Latin name shows up as Chinese
        # characters the subtitle never had, in place of the Latin word.
        drift = han_count(spoken) - han_count(text)
        if drift > MAX_CHARACTER_DRIFT:
            findings.append(
                {
                    "code": "spoken_adds_chinese",
                    "subtitle_id": subtitle_id,
                    "text": text,
                    "tts_text": spoken,
                    "detail": f"朗读比字幕多 {drift} 个汉字（可能把专名换成了中文写法）",
                }
            )
        elif drift < -MAX_CHARACTER_DRIFT:
            findings.append(
                {
                    "code": "spoken_drops_chinese",
                    "subtitle_id": subtitle_id,
                    "text": text,
                    "tts_text": spoken,
                    "detail": f"朗读比字幕少 {abs(drift)} 个汉字（可能漏字）",
                }
            )
    return findings


def build_report(base_url: str, project_id: str) -> dict[str, Any]:
    draft = api(base_url, f"/api/projects/{project_id}/video-localization")
    draft = draft.get("video_localization") or draft
    findings = audit_cues(draft)
    return {
        "project_id": project_id,
        "cue_count": len(draft.get("localized_subtitles") or []),
        "finding_count": len(findings),
        "findings": findings,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"项目 {report['project_id']}：{report['cue_count']} 条字幕，"
        f"{report['finding_count']} 处朗读与字幕不一致"
    ]
    for item in report["findings"]:
        lines.append(f"\n[{item['code']}] {item['subtitle_id']}")
        lines.append(f"    字幕：{item['text'][:60]}")
        lines.append(f"    朗读：{item['tts_text'][:60]}")
        lines.append(f"    {item['detail']}")
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
    return 2 if report["finding_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
