#!/usr/bin/env python3
"""Drive managed video-localization dubbing production from the command line.

This is the production driver the workflow was missing: submit the canonical
single-group command, wait on the durable read model, and stop exactly where an
Agent has to judge semantic boundaries.  It never decides wording, naturalness
or capacity, and it writes nothing outside the public API, so the same commands
work against a running service without touching the database or the project
snapshot.

Subcommands
    status      Read-only progress summary, including the concrete failure
                reason for every group that is not accepted yet.
    advance     Run the canonical executor for one group (or every unfinished
                group) until the group is terminal or an Agent decision is
                required.  With --regenerate it first asks for a fresh take at
                the group's current speed.
    boundaries  Print the candidate's per-word boundary evidence so the Agent
                can decide, without re-deriving it by hand.
    review      Submit the Agent's per-boundary disposition file.  The script
                only checks coverage and identity; the judgement itself is the
                Agent's.

Typical continuation
    drive_dubbing_production.py status --project <id>
    drive_dubbing_production.py advance --project <id> --group dubbing_group_0018
    drive_dubbing_production.py boundaries --project <id> --group dubbing_group_0018
    drive_dubbing_production.py review --project <id> --group dubbing_group_0018 \
        --file /tmp/reviews.json
    drive_dubbing_production.py advance --project <id> --group dubbing_group_0018
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:5173"
TERMINAL_STAGES = {"accepted", "deferred_manual_timing"}
AGENT_STAGES = {"needs_semantic_review"}
ATTENTION_STAGES = {
    "needs_attention",
    "needs_gap_processing",
    "needs_timeline_work",
    "ready_to_generate",
    "failed",
    "generating",
}
REVIEW_SCHEMA = "dubbing-candidate-review-command-v2"


class ApiError(RuntimeError):
    """A failed public API call, carrying the server's own message."""


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ApiError(f"HTTP {exc.code}: {detail[:400]}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"无法连接 {url}：{exc.reason}") from exc
    return json.loads(body) if body else {}


def api(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    resolved = method or ("POST" if payload is not None else "GET")
    return http_json(
        resolved,
        base_url.rstrip("/") + path,
        payload,
        timeout=timeout,
    )


def localization_base(project_id: str) -> str:
    return f"/api/projects/{project_id}/video-localization"


def dubbing_base(base_url: str, project_id: str) -> str:
    return f"{localization_base(project_id)}/dubbing"


def read_run(base_url: str, project_id: str) -> dict[str, Any]:
    return api(base_url, f"{dubbing_base(base_url, project_id)}/production-run")


def find_group(run: dict[str, Any], group_id: str) -> dict[str, Any]:
    group = next(
        (item for item in run.get("groups", []) if item.get("group_id") == group_id),
        None,
    )
    if group is None:
        raise ApiError(f"生产计划里没有分组 {group_id}")
    return group


def unfinished_groups(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in run.get("groups", [])
        if item.get("stage") not in TERMINAL_STAGES
    ]


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    """Project the read model into one small, Agent-readable progress object."""

    pending = unfinished_groups(run)
    return {
        "status": run.get("status"),
        "accepted": run.get("accepted_group_count"),
        "total": run.get("group_count"),
        "deferred": run.get("deferred_group_count"),
        "attention": run.get("attention_group_count"),
        "next_action": run.get("next_action"),
        "next_group_id": run.get("next_group_id"),
        "unfinished": [
            {
                "group_id": item.get("group_id"),
                "stage": item.get("stage"),
                "recommended_action": item.get("recommended_action"),
                "last_error": item.get("last_error"),
                "candidate_ids": item.get("candidate_ids"),
            }
            for item in pending
        ],
    }


def candidate_id_aliases(group: dict[str, Any]) -> list[str]:
    """Return every durable candidate alias, newest-first and de-duplicated.

    The read model lists the same take both with and without the
    ``candidate_`` prefix, mixed with older takes, so the driver resolves the
    real candidate by asking the boundary endpoint instead of trusting one
    spelling or position.
    """

    aliases: list[str] = []
    for value in reversed(group.get("candidate_ids") or []):
        if not isinstance(value, str) or not value:
            continue
        candidate_id = value if value.startswith("candidate_") else f"candidate_{value}"
        if candidate_id not in aliases:
            aliases.append(candidate_id)
    return aliases


def pick_agent_candidate(group: dict[str, Any], *, base_url: str, project_id: str) -> str | None:
    """Return the candidate whose boundary evidence is waiting for an Agent."""

    fallback: str | None = None
    for candidate_id in candidate_id_aliases(group):
        audit = read_audit(base_url, project_id, candidate_id)
        if "error" in audit:
            continue
        if audit.get("status") == "pending_agent":
            return candidate_id
        fallback = fallback or candidate_id
    return fallback


def build_review_body(
    audit: dict[str, Any],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind an Agent disposition list to the exact current candidate evidence."""

    by_id = {item.get("boundary_id"): item for item in reviews}
    expected = [item.get("boundary_id") for item in audit.get("boundaries", [])]
    missing = [value for value in expected if value not in by_id]
    unknown = [value for value in by_id if value not in set(expected)]
    if missing or unknown:
        raise ApiError(
            "边界处置没有完整覆盖当前候选。"
            f"缺少 {len(missing)} 项、多出 {len(unknown)} 项："
            f"缺少={missing[:5]} 多出={unknown[:5]}"
        )
    return {
        "schema_version": REVIEW_SCHEMA,
        "source_revision": audit["source_revision"],
        "plan_revision": audit["plan_revision"],
        "candidate_id": audit["candidate_id"],
        "audio_sha256": audit["audio_sha256"],
        "candidate_evidence_fingerprint": audit["candidate_evidence_fingerprint"],
        "candidate_clip_projection_fingerprint": audit[
            "candidate_clip_projection_fingerprint"
        ],
        "semantic_boundary_reviews": [by_id[value] for value in expected],
    }


CONTINUOUS_PUNCTUATION = set("，。！？；：、…—,.!?;:~")
MAX_ACCEPTABLE_JOIN_GAP_MS = 800
NATURAL_INTERNAL_GAP_MS = 90


def evidenced_pause(boundary: dict[str, Any]) -> bool:
    """Whether a long word-boundary pause already carries safety evidence.

    A gap the workflow itself could not shorten keeps the original pause and
    records why.  Both sides being complete plus a recorded decision is the
    evidence an Agent needs to accept a word-boundary pause; it is never used
    to accept clipped speech or an unexplained hole.
    """

    evidence = boundary.get("low_energy_evidence") or []
    if not evidence:
        return False
    return all(
        str(item.get("decision_reason") or "").strip()
        and str(item.get("edit_decision") or "retain") in {"retain", "shorten", "remove"}
        for item in evidence
    )


def rule_reviews(
    audit: dict[str, Any],
    *,
    gap_policy: str = "strict",
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Dispositions the fixed rules prove safe, plus what still needs a judge.

    The returned reviews cover only boundaries the workflow already treats as
    safe; every other boundary comes back in ``undecided`` with the reason, so
    the caller can either stop for an Agent decision or supply one itself.
    """


    if gap_policy not in {"strict", "evidenced"}:
        raise ApiError(f"未知的 gap_policy：{gap_policy}")
    expected = str(audit.get("expected_spoken_text") or "")
    reviews: list[dict[str, Any]] = []
    undecided: list[str] = []
    for boundary in audit.get("boundaries", []):
        boundary_id = str(boundary.get("boundary_id"))
        left = str(boundary.get("left_text") or "")
        right = str(boundary.get("right_text") or "")
        gap = int(boundary.get("final_gap_ms") or 0)
        relation = boundary.get("final_relation")
        left_status = boundary.get("left_render_status")
        right_status = boundary.get("right_render_status")
        anchored = "zero_width_anchor" in (left_status, right_status)
        if not anchored and (
            left_status != "fully_retained" or right_status != "fully_retained"
        ):
            undecided.append(
                {
                    "boundary_id": boundary_id,
                    "reason": f"{left}｜{right} 字音未完整保留（{left_status}/{right_status}）",
                }
            )
            continue
        punctuation = None
        cursor = 0
        while left:
            index = expected.find(left, cursor)
            if index < 0:
                break
            after = index + len(left)
            if after < len(expected) and expected[after] in CONTINUOUS_PUNCTUATION:
                punctuation = expected[after]
                break
            cursor = index + 1
        if anchored or relation == "touching" or gap == 0:
            role = "continuous_phrase"
            reason = f"「{left}{right}」所在表达连续，词间 0 间隔、发音相触。"
        elif punctuation and gap <= MAX_ACCEPTABLE_JOIN_GAP_MS:
            role = "semantic_boundary"
            reason = f"「{left}」后标点（{punctuation}）处的自然语义停顿，保留 {gap}ms，两侧字音完整。"
        elif gap <= NATURAL_INTERNAL_GAP_MS:
            role = "continuous_phrase"
            reason = f"「{left}{right}」为连续表达；{gap}ms 属于连续词组内部的自然发音结构。"
        elif gap_policy == "evidenced" and evidenced_pause(boundary):
            role = "semantic_boundary"
            reason = (
                f"「{left}」与「{right}」之间的 {gap}ms 停顿位于词边界，两侧字音完整，"
                "关联低能量区已记录安全处理依据，保留该停顿。"
            )
        else:
            undecided.append(
                {
                    "boundary_id": boundary_id,
                    "reason": f"{left}｜{right} 存在 {gap}ms 长间隙",
                }
            )
            continue
        reviews.append(
            {
                "boundary_id": boundary_id,
                "semantic_role": role,
                "disposition": "acceptable",
                "reason": reason,
                "evidence_id": None,
            }
        )
    return reviews, undecided


def continuous_reviews(
    audit: dict[str, Any],
    *,
    gap_policy: str = "strict",
) -> list[dict[str, Any]]:
    """Disposition list for boundaries the workflow already treats as safe.

    This encodes the same continuous/semantic-boundary rules the Skill states:
    a touching or zero-width join, a short internal pause, or a pause that
    follows real punctuation.  With ``gap_policy="evidenced"`` it also accepts
    a longer word-boundary pause whose low-energy evidence already records a
    safety decision.  It refuses to guess whenever a boundary is not fully
    retained or shows a gap neither rule covers, so anything that needs a
    judgement call comes back to the Agent instead of being silently accepted
    here.
    """

    reviews, undecided = rule_reviews(audit, gap_policy=gap_policy)
    if undecided:
        raise ApiError(
            "以下边界需要 Agent 判断，不能按明显连续处理："
            + "；".join(f"{item['boundary_id']} {item['reason']}" for item in undecided[:8])
        )
    return reviews


def merge_agent_decisions(
    audit: dict[str, Any],
    decisions: list[dict[str, Any]],
    *,
    gap_policy: str = "strict",
) -> list[dict[str, Any]]:
    """Combine fixed-rule dispositions with the Agent's own boundary calls.

    The rules still decide everything they can prove; ``decisions`` may only
    fill in boundaries the rules left open.  Unknown boundaries, duplicate
    entries and missing calls are refused so a partial hand-written list can
    never silently stand in for a full review.
    """

    reviews, undecided = rule_reviews(audit, gap_policy=gap_policy)
    known = {str(item.get("boundary_id")) for item in audit.get("boundaries", [])}
    decided: dict[str, dict[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, dict):
            raise ApiError("每条 Agent 判断都必须是对象")
        boundary_id = str(item.get("boundary_id") or "")
        if not boundary_id:
            raise ApiError("Agent 判断缺少 boundary_id")
        if boundary_id in decided:
            raise ApiError(f"Agent 判断重复：{boundary_id}")
        if boundary_id not in known:
            raise ApiError(f"Agent 判断引用了不存在的边界：{boundary_id}")
        role = str(item.get("semantic_role") or "")
        disposition = str(item.get("disposition") or "")
        reason = str(item.get("reason") or "").strip()
        if role not in {"semantic_boundary", "continuous_phrase"}:
            raise ApiError(f"{boundary_id} 的 semantic_role 无效")
        if disposition not in {"acceptable", "recover", "uncertain"}:
            raise ApiError(f"{boundary_id} 的 disposition 无效")
        if not reason:
            raise ApiError(f"{boundary_id} 缺少判断理由")
        decided[boundary_id] = {
            "boundary_id": boundary_id,
            "semantic_role": role,
            "disposition": disposition,
            "reason": reason,
            "evidence_id": item.get("evidence_id"),
        }
    missing = [
        item["boundary_id"]
        for item in undecided
        if item["boundary_id"] not in decided
    ]
    if missing:
        raise ApiError(
            "仍有边界需要 Agent 判断：" + "；".join(missing[:8])
        )
    reviews.extend(decided[item["boundary_id"]] for item in undecided)
    return reviews


def load_decision_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "semantic_boundary_reviews" in payload:
        payload = payload["semantic_boundary_reviews"]
    if not isinstance(payload, list) or not payload:
        raise ApiError("Agent 判断文件需要是非空的边界处置数组")
    return payload


def load_review_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "semantic_boundary_reviews" in payload:
        payload = payload["semantic_boundary_reviews"]
    if not isinstance(payload, list) or not payload:
        raise ApiError("处置文件需要是非空的边界处置数组")
    for item in payload:
        if not isinstance(item, dict) or not item.get("boundary_id"):
            raise ApiError("每条处置都需要 boundary_id")
    return payload


def read_audit(base_url: str, project_id: str, candidate_id: str) -> dict[str, Any]:
    return api(
        base_url,
        f"{dubbing_base(base_url, project_id)}/candidates/{candidate_id}/semantic-boundaries",
    )


def submit_reviews(
    base_url: str,
    project_id: str,
    candidate_id: str,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    audit = read_audit(base_url, project_id, candidate_id)
    if "error" in audit:
        raise ApiError(str(audit["error"]))
    body = build_review_body(audit, reviews)
    return api(
        base_url,
        f"{dubbing_base(base_url, project_id)}/candidates/{candidate_id}/semantic-boundaries/review",
        body,
    )


def execute_group(
    base_url: str,
    project_id: str,
    group_id: str,
    *,
    regenerate: bool = False,
    speed_baseline: float | None = None,
    review_mode: str = "full",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "dubbing-production-execute-v1",
        "scope": "single_group",
        "group_id": group_id,
        "review_mode": review_mode,
    }
    if speed_baseline is not None:
        # A run-level ordinary baseline is what keeps adjacent groups at one
        # audible pace; without it every group falls back to its own
        # text-pressure guess and the range drifts between 1.0 and 1.3.
        payload["ordinary_speed_baseline"] = speed_baseline
    if regenerate:
        payload["regenerate_existing"] = True
    return api(
        base_url,
        f"{dubbing_base(base_url, project_id)}/production-run/execute",
        payload,
    )


def advance_group(
    base_url: str,
    project_id: str,
    group_id: str,
    *,
    regenerate: bool = False,
    speed_baseline: float | None = None,
    poll_seconds: float = 12.0,
    max_rounds: int = 60,
) -> dict[str, Any]:
    """Run the canonical executor until the group is terminal or needs a judge."""

    regenerated = False
    for round_index in range(max_rounds):
        run = read_run(base_url, project_id)
        group = find_group(run, group_id)
        stage = str(group.get("stage"))
        if stage in TERMINAL_STAGES:
            return {
                "group_id": group_id,
                "result": "terminal",
                "stage": stage,
                "run": summarize(run),
            }
        if stage in AGENT_STAGES:
            candidate_id = pick_agent_candidate(
                group,
                base_url=base_url,
                project_id=project_id,
            )
            return {
                "group_id": group_id,
                "result": "agent_review_required",
                "stage": stage,
                "candidate_id": candidate_id,
                "instruction": (
                    "读取该候选的边界证据（boundaries 子命令），完成判断后再用 "
                    "review 子命令提交，然后继续 advance。"
                ),
                "run": summarize(run),
            }
        if regenerate and not regenerated:
            regenerated = True
            response = execute_group(
                base_url,
                project_id,
                group_id,
                regenerate=True,
                speed_baseline=speed_baseline,
            )
        else:
            response = execute_group(
                base_url,
                project_id,
                group_id,
                speed_baseline=speed_baseline,
            )
        if str(response.get("required_action") or "") == "resolve_capacity":
            # Proven physical overflow: only the Agent can decide C1–C6, so
            # stop polling instead of burning rounds on the same refusal.
            return {
                "group_id": group_id,
                "result": "capacity_recovery_required",
                "stage": "needs_gap_processing",
                "required_action": "resolve_capacity",
                "message": response.get("message"),
                "run": summarize(read_run(base_url, project_id)),
            }
        time.sleep(poll_seconds)
    raise ApiError(f"{group_id} 在 {max_rounds} 轮内没有稳定，请读取 status 检查")


ALIGNMENT_LEAD_MS = 80
ALIGNMENT_THRESHOLD_MS = 250
_ALIGNMENT_PUNCTUATION = "，。！？；：、…—,.!?;:~「」『』（）()《》〈〉\"' "


def _alignment_text(value: Any) -> str:
    return re.sub(
        f"[{re.escape(_ALIGNMENT_PUNCTUATION)}]",
        "",
        str(value or ""),
    )


def match_subtitle_word_ranges(
    audit: dict[str, Any],
    subtitles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Locate every subtitle of one take inside its aligned word timeline."""

    words = [
        {
            "text": _alignment_text(item.get("text")),
            "start_ms": int(item.get("start_ms") or 0),
            "end_ms": int(item.get("end_ms") or 0),
            "word_id": str(item.get("word_id") or ""),
        }
        for item in audit.get("aligned_words") or []
    ]
    words = [item for item in words if item["text"]]
    cursor = 0
    ranges: list[dict[str, Any]] = []
    for subtitle in subtitles:
        target = _alignment_text(subtitle.get("text"))
        if not target:
            return []
        start_index = cursor
        consumed = ""
        end_index = cursor
        while end_index < len(words) and len(consumed) < len(target):
            consumed += words[end_index]["text"]
            end_index += 1
        if not consumed.startswith(target):
            return []
        ranges.append(
            {
                "subtitle_id": str(subtitle.get("subtitle_id") or ""),
                "target_start_ms": int(subtitle.get("start_ms") or 0),
                "word_start_ms": words[start_index]["start_ms"],
                "word_end_ms": words[end_index - 1]["end_ms"],
                "word_ids": [item["word_id"] for item in words[start_index:end_index]],
            }
        )
        cursor = end_index
    return ranges


def plan_alignment_slices(
    audit: dict[str, Any],
    clip: dict[str, Any],
    subtitles: list[dict[str, Any]],
    *,
    threshold_ms: int = ALIGNMENT_THRESHOLD_MS,
    lead_ms: int = ALIGNMENT_LEAD_MS,
) -> dict[str, Any] | None:
    """Split one take so each subtitle starts with its own source cue.

    Returns ``None`` when the take already matches its subtitles, when the
    subtitle text cannot be located in the aligned words, or when there is only
    one subtitle to place.  A negative gap (the previous slice would run past
    the next subtitle) is clamped to zero, which keeps the slices adjacent
    instead of overlapping; the caller reports that as a warning.
    """

    ranges = match_subtitle_word_ranges(audit, subtitles)
    if len(ranges) < 2:
        return None
    clip_start_ms = int(clip.get("start_ms") or 0)
    source_start_ms = int(clip.get("source_start_ms") or 0)
    source_end_ms = int(clip.get("source_end_ms") or 0)
    deviations = [
        clip_start_ms + range_item["word_start_ms"] - source_start_ms - range_item["target_start_ms"]
        for range_item in ranges
    ]
    if max(abs(value) for value in deviations) <= threshold_ms:
        return None
    slices: list[dict[str, Any]] = []
    previous_end_ms: int | None = None
    previous_source_end: int | None = None
    clamped = False
    for index, range_item in enumerate(ranges):
        if previous_source_end is None:
            # The slices must tile the whole candidate crop: the service
            # refuses a split that leaves any part of the take uncovered.
            source_start = max(0, source_start_ms)
        else:
            # Share one cut point with the previous slice: two independent
            # safety margins would overlap by their sum and replay the same
            # audio on both sides of the join.
            source_start = previous_source_end
        if index == len(ranges) - 1:
            # Cover the candidate crop exactly: neither leaving a tail
            # uncovered nor claiming audio the crop never had.
            source_end = source_end_ms
        else:
            boundary = max(
                range_item["word_end_ms"],
                ranges[index + 1]["word_start_ms"] - lead_ms,
            )
            source_end = min(source_end_ms, boundary)
        if source_end <= source_start:
            return None
        previous_source_end = source_end
        expected_start = range_item["target_start_ms"]
        if previous_end_ms is None:
            # The first slice keeps the adopted take's own start; later slices
            # are positioned from where the previous slice actually ends.
            gap = 0
            current_start = clip_start_ms
        else:
            gap = expected_start - previous_end_ms
            if gap < 0:
                gap = 0
                clamped = True
            current_start = max(expected_start, previous_end_ms)
        slices.append(
            {
                "target_subtitle_ids": [range_item["subtitle_id"]],
                "source_start_ms": source_start,
                "source_end_ms": source_end,
                "speech_start_ms": range_item["word_start_ms"],
                "speech_end_ms": range_item["word_end_ms"],
                "alignment_word_ids": range_item["word_ids"],
                "timeline_gap_before_ms": gap,
            }
        )
        previous_end_ms = current_start + (source_end - source_start)
    return {
        "clip_id": str(clip.get("clip_id") or ""),
        "candidate_id": str(clip.get("candidate_id") or ""),
        "slices": slices,
        "max_deviation_ms": max(abs(value) for value in deviations),
        "gap_clamped": clamped,
    }


def read_subtitle_index(
    base_url: str,
    project_id: str,
) -> dict[str, dict[str, Any]]:
    """Localized subtitles by ID: text plus the target start of each line."""

    draft = api(base_url, localization_base(project_id))
    draft = draft.get("video_localization") or draft
    return {
        str(item.get("subtitle_id")): item
        for item in draft.get("localized_subtitles") or []
    }


def staged_candidate_clips(
    base_url: str,
    project_id: str,
    candidate_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The candidate's staged projection: its own not-yet-adopted clips."""

    details = api(
        base_url,
        f"{localization_base(project_id)}/workspace-details/dubbing_production",
    )
    section = details.get("dubbing_production") or details
    for report in section.get("candidate_reports") or []:
        if str(report.get("candidate_id")) != candidate_id:
            continue
        audit = report.get("semantic_boundary_audit") or {}
        projection = report.get("staged_candidate_projection") or {}
        return audit, list(projection.get("clips") or [])
    audit = read_audit(base_url, project_id, candidate_id)
    if "error" in audit:
        raise ApiError(str(audit["error"]))
    return audit, []


def align_candidate(
    base_url: str,
    project_id: str,
    group_id: str,
    candidate_id: str,
    *,
    threshold_ms: int = ALIGNMENT_THRESHOLD_MS,
) -> dict[str, Any]:
    """Split a multi-subtitle take so every line starts on its own cue.

    The measurement and the split both come from the durable read model: the
    take's aligned words locate each subtitle, and the request only moves the
    safety-proven cut points.  A take that already matches, a single-subtitle
    take, or text that cannot be located is reported unchanged.
    """

    run = read_run(base_url, project_id)
    group = find_group(run, group_id)
    audit, clips = staged_candidate_clips(
        base_url,
        project_id,
        candidate_id,
    )
    if len(clips) != 1:
        return {"result": "unchanged", "reason": f"候选当前有 {len(clips)} 个暂存片段"}
    clip = clips[0]
    subtitles_by_id = read_subtitle_index(base_url, project_id)
    ordered = [
        subtitles_by_id[subtitle_id]
        for subtitle_id in clip.get("target_subtitle_ids") or []
        if subtitle_id in subtitles_by_id
    ]
    if len(ordered) < 2:
        return {"result": "unchanged", "reason": "该片段只有一条字幕"}
    plan = plan_alignment_slices(
        audit,
        clip,
        ordered,
        threshold_ms=threshold_ms,
    )
    if plan is None:
        return {"result": "aligned"}
    revision = api(
        base_url,
        f"{localization_base(project_id)}/workspace-revision",
    )
    command = {
        "clip_id": str(clip.get("clip_id") or ""),
        "candidate_id": candidate_id,
        "audio_sha256": str(audit.get("audio_sha256") or ""),
        "slices": plan["slices"],
    }
    body = {
        "expected_repository_revision": int(revision["revision"]),
        "source_revision": str(audit.get("source_revision") or ""),
        "plan_revision": int(audit.get("plan_revision") or 0),
        "candidate_id": candidate_id,
        "candidate_clip_projection_fingerprint": str(
            audit.get("candidate_clip_projection_fingerprint") or ""
        ),
        "commands": [command],
    }
    response = api(
        base_url,
        f"{dubbing_base(base_url, project_id)}/candidates/{candidate_id}/staged-split",
        body,
    )
    return {
        "result": "split",
        "slices": len(plan["slices"]),
        "max_deviation_ms": plan["max_deviation_ms"],
        "gap_clamped": plan["gap_clamped"],
        "response_schema": str(response.get("schema_version") or ""),
    }


def recent_formal_speeds(
    base_url: str,
    project_id: str,
    *,
    limit: int = 3,
) -> list[float]:
    """Speeds of the most recent successful, non-exception generations."""

    tasks = api(base_url, f"{localization_base(project_id)}/tts/tasks")
    items = tasks if isinstance(tasks, list) else tasks.get("tasks") or []
    ordered = sorted(
        items,
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    speeds: list[float] = []
    for item in ordered:
        if str(item.get("status")) != "success" or not item.get("result_id"):
            continue
        for stage in item.get("stages") or []:
            if str(stage.get("kind")) != "generation":
                continue
            parameters = stage.get("parameters") or {}
            if parameters.get("content_speed_exception_reason"):
                continue
            speed = parameters.get("speed")
            if isinstance(speed, (int, float)) and float(speed) > 0:
                speeds.append(round(float(speed), 2))
            break
        if len(speeds) >= limit:
            break
    return speeds


def resolve_ordinary_speed_baseline(
    base_url: str,
    project_id: str,
    override: float | None = None,
) -> float | None:
    """Frozen ordinary speed for the range: explicit value, then recent median.

    The Skill orders the sources as user value, persisted range baseline, the
    median of the last three ordinary formal clips, and finally the first
    group's text-pressure estimate.  Returning ``None`` leaves that estimate to
    the service, which is correct only for a range that has no history yet.
    """

    if override is not None:
        return round(float(override), 2)
    speeds = recent_formal_speeds(base_url, project_id)
    if not speeds:
        return None
    return round(float(statistics.median(speeds)), 2)


def command_status(args: argparse.Namespace) -> int:
    run = read_run(args.base_url, args.project)
    print(json.dumps(summarize(run), ensure_ascii=False, indent=2))
    return 0


def command_advance(args: argparse.Namespace) -> int:
    speed_baseline = resolve_ordinary_speed_baseline(
        args.base_url,
        args.project,
        args.speed_baseline,
    )
    if not hasattr(args, "align"):
        args.align = True
    targets = [args.group] if args.group else [
        item["group_id"] for item in unfinished_groups(read_run(args.base_url, args.project))
    ]
    if not targets:
        print(json.dumps({"result": "nothing_to_do", "run": summarize(read_run(args.base_url, args.project))}, ensure_ascii=False, indent=2))
        return 0
    outcomes = []
    for group_id in targets:
        outcomes.append(
            advance_group(
                args.base_url,
                args.project,
                group_id,
                regenerate=args.regenerate,
                speed_baseline=speed_baseline,
                poll_seconds=args.poll_seconds,
                max_rounds=args.max_rounds,
            )
        )
        if outcomes[-1]["result"] != "terminal" and not args.keep_going:
            break
    print(json.dumps({"outcomes": outcomes}, ensure_ascii=False, indent=2))
    return 0 if all(item["result"] == "terminal" for item in outcomes) else 2


def plan_run_step(run: dict[str, Any]) -> dict[str, Any]:
    """Decide the next whole-range action from the durable read model.

    Reviewing comes first: a group whose candidate is waiting for its
    per-boundary disposition would otherwise stay unfinished while the executor
    keeps asking for it.
    """

    unfinished = unfinished_groups(run)
    review_group_ids = [
        str(item["group_id"])
        for item in unfinished
        if str(item.get("stage")) in AGENT_STAGES
    ]
    if review_group_ids:
        return {"action": "review", "group_ids": review_group_ids}
    remaining = [str(item["group_id"]) for item in unfinished]
    if remaining:
        return {"action": "advance", "group_ids": remaining}
    return {"action": "complete", "group_ids": []}


def command_run(args: argparse.Namespace) -> int:
    """Drive the whole remaining range, stopping only where an Agent must judge.

    Boundaries the fixed rules already prove safe are disposed of exactly like
    ``review --accept-continuous``; a group whose evidence needs a real
    judgement stops the run and reports which boundaries are waiting, so the
    same command never guesses on the Agent's behalf.
    """

    stalled = 0
    previous: tuple[int, int] | None = None
    speed_baseline = resolve_ordinary_speed_baseline(
        args.base_url,
        args.project,
        args.speed_baseline,
    )
    for cycle in range(1, args.max_cycles + 1):
        run = read_run(args.base_url, args.project)
        step = plan_run_step(run)
        if step["action"] == "complete":
            print(
                json.dumps(
                    {"result": "complete", "cycles": cycle, **summarize(run)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if step["action"] == "advance":
            capacity: list[dict[str, Any]] = []
            for group_id in step["group_ids"]:
                outcome = advance_group(
                    args.base_url,
                    args.project,
                    group_id,
                    speed_baseline=speed_baseline,
                    poll_seconds=args.poll_seconds,
                    max_rounds=args.max_rounds,
                )
                if outcome["result"] == "capacity_recovery_required":
                    capacity.append(
                        {
                            "group_id": group_id,
                            "message": outcome.get("message"),
                        }
                    )
                    continue
                if outcome["result"] != "terminal":
                    break
            if capacity:
                print(
                    json.dumps(
                        {
                            "result": "capacity_recovery_required",
                            "cycle": cycle,
                            "groups": capacity,
                            "summary": summarize(read_run(args.base_url, args.project)),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
        else:
            blocked: list[dict[str, Any]] = []
            aligned: list[dict[str, Any]] = []
            for group_id in step["group_ids"]:
                current = read_run(args.base_url, args.project)
                group = find_group(current, group_id)
                candidate_id = pick_agent_candidate(
                    group,
                    base_url=args.base_url,
                    project_id=args.project,
                )
                if not candidate_id:
                    blocked.append(
                        {
                            "group_id": group_id,
                            "candidate_id": None,
                            "reason": "该组还没有可判断的候选",
                        }
                    )
                    continue
                audit = read_audit(args.base_url, args.project, candidate_id)
                if "error" in audit:
                    blocked.append(
                        {
                            "group_id": group_id,
                            "candidate_id": candidate_id,
                            "reason": str(audit["error"]),
                        }
                    )
                    continue
                if args.align:
                    # Split long takes so every subtitle starts on its own cue
                    # before the take is adopted; a failure here must not block
                    # the review, it only leaves the take as it was.
                    try:
                        alignment = align_candidate(
                            args.base_url,
                            args.project,
                            group_id,
                            candidate_id,
                            threshold_ms=args.align_threshold_ms,
                        )
                        aligned.append({"group_id": group_id, **alignment})
                        if alignment.get("result") == "split":
                            audit = read_audit(
                                args.base_url,
                                args.project,
                                candidate_id,
                            )
                            if "error" in audit:
                                raise ApiError(str(audit["error"]))
                    except ApiError as exc:
                        aligned.append(
                            {
                                "group_id": group_id,
                                "result": "unavailable",
                                "reason": str(exc)[:200],
                            }
                        )
                try:
                    reviews = continuous_reviews(audit, gap_policy=args.gap_policy)
                except ApiError as exc:
                    blocked.append(
                        {
                            "group_id": group_id,
                            "candidate_id": candidate_id,
                            "reason": str(exc),
                        }
                    )
                    continue
                submit_reviews(args.base_url, args.project, candidate_id, reviews)
            if blocked:
                print(
                    json.dumps(
                        {
                            "result": "agent_decision_required",
                            "cycle": cycle,
                            "groups": blocked,
                            "alignment": aligned,
                            "summary": summarize(read_run(args.base_url, args.project)),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
        current = read_run(args.base_url, args.project)
        signature = (
            int(current.get("accepted_group_count") or 0),
            len(unfinished_groups(current)),
        )
        stalled = stalled + 1 if signature == previous else 0
        previous = signature
        if stalled >= args.max_stalled_cycles:
            print(
                json.dumps(
                    {"result": "stalled", "cycle": cycle, **summarize(current)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
    print(
        json.dumps(
            {
                "result": "max_cycles_reached",
                **summarize(read_run(args.base_url, args.project)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2


def command_boundaries(args: argparse.Namespace) -> int:
    run = read_run(args.base_url, args.project)
    group = find_group(run, args.group)
    candidate_id = args.candidate or pick_agent_candidate(
        group,
        base_url=args.base_url,
        project_id=args.project,
    )
    if not candidate_id:
        raise ApiError(f"{args.group} 还没有可判断的候选")
    audit = read_audit(args.base_url, args.project, candidate_id)
    if "error" in audit:
        raise ApiError(str(audit["error"]))
    print(
        json.dumps(
            {
                "group_id": args.group,
                "candidate_id": candidate_id,
                "status": audit.get("status"),
                "expected_spoken_text": audit.get("expected_spoken_text"),
                "boundaries": audit.get("boundaries"),
                "agent_reviews": audit.get("agent_reviews"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_review(args: argparse.Namespace) -> int:
    run = read_run(args.base_url, args.project)
    group = find_group(run, args.group)
    candidate_id = args.candidate or pick_agent_candidate(
        group,
        base_url=args.base_url,
        project_id=args.project,
    )
    if not candidate_id:
        raise ApiError(f"{args.group} 还没有可判断的候选")
    if args.accept_continuous:
        audit = read_audit(args.base_url, args.project, candidate_id)
        if "error" in audit:
            raise ApiError(str(audit["error"]))
        if args.decisions:
            reviews = merge_agent_decisions(
                audit,
                load_decision_file(Path(args.decisions)),
                gap_policy=args.gap_policy,
            )
        else:
            reviews = continuous_reviews(audit, gap_policy=args.gap_policy)
    else:
        if not args.file:
            raise ApiError("review 需要 --file，或显式使用 --accept-continuous")
        reviews = load_review_file(Path(args.file))
    report = submit_reviews(args.base_url, args.project, candidate_id, reviews)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="驱动受管视频本土化配音生产（只调用公开 API，不做语义判断）",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="运行中的服务地址")
    parser.add_argument("--project", required=True, help="项目 ID")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="只读进度摘要")
    status.set_defaults(handler=command_status)

    advance = subparsers.add_parser("advance", help="驱动生成与收口")
    advance.add_argument("--group", default=None, help="只处理这一组，省略则处理全部未完成组")
    advance.add_argument("--regenerate", action="store_true", help="先要求一次新的整组生成")
    advance.add_argument(
        "--speed-baseline",
        type=float,
        default=None,
        help="整段沿用的冻结速度；省略时取最近三个普通正式片段的中位数",
    )
    advance.add_argument("--poll-seconds", type=float, default=12.0)
    advance.add_argument("--max-rounds", type=int, default=60)
    advance.add_argument("--keep-going", action="store_true", help="某组需要 Agent 判断时继续处理后面的组")
    advance.set_defaults(handler=command_advance)

    boundaries = subparsers.add_parser("boundaries", help="输出候选的逐边界证据")
    boundaries.add_argument("--group", required=True)
    boundaries.add_argument("--candidate", default=None)
    boundaries.set_defaults(handler=command_boundaries)

    review = subparsers.add_parser("review", help="提交 Agent 的逐边界处置")
    review.add_argument("--group", required=True)
    review.add_argument("--candidate", default=None)
    review.add_argument("--file", default=None, help="边界处置 JSON 文件")
    review.add_argument(
        "--accept-continuous",
        action="store_true",
        help=(
            "按固定规则处置已证明连续或标点后的边界；字音未完整或长间隙会报错"
            "交由 Agent 判断"
        ),
    )
    review.add_argument(
        "--decisions",
        default=None,
        help="Agent 对规则无法判定的边界给出的处置 JSON；与 --accept-continuous 合用",
    )
    review.add_argument(
        "--gap-policy",
        choices=("strict", "evidenced"),
        default="strict",
        help=(
            "strict 只接受衔接/短间隙/标点后停顿；evidenced 另外接受词边界上"
            "已有安全处理依据的较长停顿"
        ),
    )
    review.set_defaults(handler=command_review)

    run = subparsers.add_parser(
        "run",
        help="连续驱动全部未完成组，只在需要 Agent 判断的边界处停下",
    )
    run.add_argument("--poll-seconds", type=float, default=12.0)
    run.add_argument(
        "--speed-baseline",
        type=float,
        default=None,
        help="整段沿用冻结速度；省略时取最近三个普通正式片段的中位数",
    )
    run.add_argument("--max-rounds", type=int, default=60, help="单组等待轮数上限")
    run.add_argument("--max-cycles", type=int, default=400)
    run.add_argument(
        "--max-stalled-cycles",
        type=int,
        default=4,
        help="连续多少轮既无新完成也无待判断组就退出",
    )
    run.add_argument(
        "--no-align",
        action="store_true",
        help="跳过采用前的逐字幕对齐分片（默认执行）",
    )
    run.add_argument(
        "--align-threshold-ms",
        type=int,
        default=ALIGNMENT_THRESHOLD_MS,
        help="超过该偏差才切分长片段",
    )
    run.add_argument(
        "--gap-policy",
        choices=("strict", "evidenced"),
        default="evidenced",
        help="与 review 相同的规则；evidenced 另外接受词边界上已有安全依据的较长停顿",
    )
    run.set_defaults(handler=command_run, align=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ApiError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
