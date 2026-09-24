#!/usr/bin/env python3
"""Repair dubbing prosody by re-cutting a take at its own safe word boundaries.

The Skill's first recovery level is a safe gap edit: shorten a pause that splits
a phrase, or open a pause where a new clause starts.  This script does exactly
that through the public staged-split endpoint — it never regenerates audio and
never writes project data directly.

Cut points come from the take's aligned words, so both sides of every cut stay
complete; the timeline gap of each slice follows the punctuation class of the
frozen line (clause mark, comma-class mark, or no mark at all, which means the
take should run on).

Usage
    repair_dubbing_prosody.py --project <id> [--group GROUP] [--apply]
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

# Natural pause ranks for Mandarin delivery: a sentence ends with a longer
# breath than a comma clause, and a phrase that carries no mark at all should
# not be interrupted.  Values are the minimum a listener needs to hear the
# boundary; the surrounding take keeps its own rhythm.
GAP_BY_CLASS = {"clause": 260, "comma": 150, "none": 0}


class ApiError(RuntimeError):
    """A failed public API call, carrying the server's own message."""


def api(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 120.0,
) -> Any:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise ApiError(f"HTTP {exc.code}: {detail[:400]}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"无法访问 {base_url}{path}：{exc.reason}") from None


def punctuation_class(text: str, left: str, cursor: int) -> tuple[str, int]:
    index = text.find(left, cursor)
    if index < 0:
        return "none", cursor
    position = index + len(left)
    while position < len(text) and text[position] in " \t":
        position += 1
    if position < len(text):
        mark = text[position]
        if mark in CLAUSE_PUNCTUATION:
            return "clause", position
        if mark in COMMA_PUNCTUATION:
            return "comma", position
    return "none", position


def plan_repair(
    audit: dict[str, Any],
    clip: dict[str, Any],
    *,
    min_gap_ms: int = 300,
    max_join_gap_ms: int = 100,
) -> list[dict[str, Any]]:
    """Cut the take so every problematic boundary gets the pause it needs."""

    expected = str(audit.get("expected_spoken_text") or "")
    words = [
        {
            "word_id": str(item.get("word_id") or ""),
            "start_ms": int(item.get("start_ms") or 0),
            "end_ms": int(item.get("end_ms") or 0),
        }
        for item in audit.get("aligned_words") or []
    ]
    if len(words) < 2:
        return []
    source_start = int(clip.get("source_start_ms") or 0)
    source_end = int(clip.get("source_end_ms") or 0)
    cuts: list[dict[str, Any]] = []
    cursor = 0
    for boundary in audit.get("boundaries") or []:
        left = str(boundary.get("left_text") or "")
        right = str(boundary.get("right_text") or "")
        gap = int(boundary.get("final_gap_ms") or 0)
        cls, cursor = punctuation_class(expected, left, cursor)
        if cls == "none" and gap < min_gap_ms:
            continue
        if cls != "none" and gap > max_join_gap_ms:
            continue
        left_word = next(
            (item for item in words if item["word_id"] == boundary.get("left_word_id")),
            None,
        )
        right_word = next(
            (item for item in words if item["word_id"] == boundary.get("right_word_id")),
            None,
        )
        if left_word is None or right_word is None:
            continue
        cut = max(source_start, min(source_end, left_word["end_ms"]))
        boundary_start = max(cut, min(source_end, right_word["start_ms"]))
        cuts.append(
            {
                "boundary_id": boundary.get("boundary_id"),
                "left_text": left,
                "right_text": right,
                "punctuation_class": cls,
                "cut_ms": cut,
                "resume_ms": boundary_start,
                "gap_ms": GAP_BY_CLASS[cls],
                "left_word_id": left_word["word_id"],
                "right_word_id": right_word["word_id"],
            }
        )
    if not cuts:
        return []
    cuts.sort(key=lambda item: item["cut_ms"])
    slices: list[dict[str, Any]] = []
    word_ids: list[str] = []
    pieces: list[dict[str, Any]] = []
    previous_cut = source_start
    previous_gap = 0
    for cut in cuts:
        pieces.append(
            {
                "source_start_ms": previous_cut,
                "source_end_ms": cut["cut_ms"],
                "timeline_gap_before_ms": previous_gap,
            }
        )
        previous_cut = cut["resume_ms"]
        previous_gap = cut["gap_ms"]
    pieces.append(
        {
            "source_start_ms": previous_cut,
            "source_end_ms": source_end,
            "timeline_gap_before_ms": previous_gap,
        }
    )
    subject_ids = list(clip.get("target_subtitle_ids") or [])
    for index, piece in enumerate(pieces):
        # Half-open ownership by word start: a word that merely touches the cut
        # belongs to the slice it starts in, never to both.
        last = index == len(pieces) - 1
        covered = [
            item["word_id"]
            for item in words
            if item["start_ms"] >= piece["source_start_ms"]
            and (
                item["start_ms"] < piece["source_end_ms"]
                or (last and item["start_ms"] <= piece["source_end_ms"])
            )
        ]
        if not covered:
            return []
        word_ids.extend(covered)
        slices.append(
            {
                "target_subtitle_ids": subject_ids,
                "source_start_ms": piece["source_start_ms"],
                "source_end_ms": piece["source_end_ms"],
                "speech_start_ms": min(
                    item["start_ms"]
                    for item in words
                    if item["word_id"] in covered
                ),
                "speech_end_ms": max(
                    item["end_ms"]
                    for item in words
                    if item["word_id"] in covered
                ),
                "alignment_word_ids": covered,
                "timeline_gap_before_ms": piece["timeline_gap_before_ms"],
            }
        )
    if len(word_ids) != len(set(word_ids)) or len(slices) < 2:
        return []
    return [{"cuts": cuts, "slices": slices}]


def repair_group(
    base_url: str,
    project_id: str,
    group_id: str,
    *,
    apply: bool,
    min_gap_ms: int,
    max_join_gap_ms: int,
) -> dict[str, Any]:
    localization = f"/api/projects/{project_id}/video-localization"
    projection = api(base_url, f"{localization}/timeline-projection")
    clips = [
        clip
        for clip in projection.get("timeline_clips") or []
        if str(clip.get("track_id")) == "dub"
        and str(clip.get("dubbing_group_id")) == group_id
    ]
    if len(clips) != 1:
        return {"group_id": group_id, "result": "skipped", "reason": f"该组有 {len(clips)} 个片段"}
    clip = clips[0]
    candidate_id = str(clip.get("candidate_id") or "")
    try:
        audit = api(
            base_url,
            f"{localization}/dubbing/candidates/{candidate_id}/semantic-boundaries",
        )
    except ApiError as exc:
        return {"group_id": group_id, "result": "skipped", "reason": str(exc)[:200]}
    if "error" in audit:
        return {"group_id": group_id, "result": "skipped", "reason": str(audit["error"])[:200]}
    plans = plan_repair(
        audit,
        clip,
        min_gap_ms=min_gap_ms,
        max_join_gap_ms=max_join_gap_ms,
    )
    if not plans:
        return {"group_id": group_id, "result": "clean"}
    plan = plans[0]
    report = {
        "group_id": group_id,
        "result": "planned" if not apply else "applied",
        "slices": len(plan["slices"]),
        "cuts": [
            {
                "boundary": f"{item['left_text']}｜{item['right_text']}",
                "class": item["punctuation_class"],
                "gap_ms": item["gap_ms"],
            }
            for item in plan["cuts"]
        ],
    }
    if not apply:
        return report
    revision = api(base_url, f"{localization}/workspace-revision")
    body = {
        "expected_repository_revision": int(revision["revision"]),
        "source_revision": audit["source_revision"],
        "plan_revision": audit["plan_revision"],
        "candidate_id": candidate_id,
        "candidate_clip_projection_fingerprint": audit.get(
            "candidate_clip_projection_fingerprint"
        ),
        "commands": [
            {
                "clip_id": clip["clip_id"],
                "candidate_id": candidate_id,
                "audio_sha256": audit.get("audio_sha256"),
                "slices": plan["slices"],
            }
        ],
    }
    response = api(
        base_url,
        f"{localization}/dubbing/candidates/{candidate_id}/staged-split",
        body,
        timeout=300,
    )
    report["response"] = str(response.get("schema_version") or "") if isinstance(response, dict) else ""
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按断句证据重新切片修复配音停顿")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project", required=True)
    parser.add_argument("--group", default=None, help="只处理这一组，省略则处理所有有问题的组")
    parser.add_argument("--apply", action="store_true", help="真正提交切分；省略时只规划")
    parser.add_argument("--min-gap-ms", type=int, default=300)
    parser.add_argument("--max-join-gap-ms", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    groups = [args.group] if args.group else None
    if groups is None:
        localization = f"/api/projects/{args.project}/video-localization"
        try:
            projection = api(args.base_url, f"{localization}/timeline-projection")
        except ApiError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        groups = sorted(
            {
                str(clip.get("dubbing_group_id"))
                for clip in projection.get("timeline_clips") or []
                if str(clip.get("track_id")) == "dub" and clip.get("dubbing_group_id")
            }
        )
    results = []
    for group_id in groups:
        try:
            results.append(
                repair_group(
                    args.base_url,
                    args.project,
                    group_id,
                    apply=args.apply,
                    min_gap_ms=args.min_gap_ms,
                    max_join_gap_ms=args.max_join_gap_ms,
                )
            )
        except ApiError as exc:
            results.append({"group_id": group_id, "result": "error", "reason": str(exc)[:200]})
    actionable = [item for item in results if item["result"] in {"planned", "applied"}]
    failed = [item for item in results if item["result"] in {"error", "skipped"}]
    for item in actionable:
        cuts = "、".join(
            f"{cut['boundary']}({cut['class']}→{cut['gap_ms']}ms)" for cut in item["cuts"]
        )
        print(f"{item['group_id']} [{item['result']}] {item['slices']} 片：{cuts}")
    for item in failed:
        print(f"{item['group_id']} [{item['result']}] {item.get('reason', '')}")
    print(
        f"\n共 {len(results)} 组：{len(actionable)} 组需要修复，"
        f"{len(failed)} 组无法直接切片，其余断句正常"
    )
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
