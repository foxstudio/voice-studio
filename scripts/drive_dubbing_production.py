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


def dubbing_base(base_url: str, project_id: str) -> str:
    return f"/api/projects/{project_id}/video-localization/dubbing"


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
    if regenerate:
        payload["regenerate_existing"] = True
        if speed_baseline is not None:
            payload["ordinary_speed_baseline"] = speed_baseline
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
            execute_group(
                base_url,
                project_id,
                group_id,
                regenerate=True,
                speed_baseline=speed_baseline,
            )
        else:
            execute_group(base_url, project_id, group_id)
        time.sleep(poll_seconds)
    raise ApiError(f"{group_id} 在 {max_rounds} 轮内没有稳定，请读取 status 检查")


def command_status(args: argparse.Namespace) -> int:
    run = read_run(args.base_url, args.project)
    print(json.dumps(summarize(run), ensure_ascii=False, indent=2))
    return 0


def command_advance(args: argparse.Namespace) -> int:
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
                speed_baseline=args.speed_baseline,
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
            for group_id in step["group_ids"]:
                outcome = advance_group(
                    args.base_url,
                    args.project,
                    group_id,
                    poll_seconds=args.poll_seconds,
                    max_rounds=args.max_rounds,
                )
                if outcome["result"] != "terminal":
                    break
        else:
            blocked: list[dict[str, Any]] = []
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
    advance.add_argument("--speed-baseline", type=float, default=None, help="重新生成时沿用的冻结速度")
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
    run.add_argument("--max-rounds", type=int, default=60, help="单组等待轮数上限")
    run.add_argument("--max-cycles", type=int, default=400)
    run.add_argument(
        "--max-stalled-cycles",
        type=int,
        default=4,
        help="连续多少轮既无新完成也无待判断组就退出",
    )
    run.add_argument(
        "--gap-policy",
        choices=("strict", "evidenced"),
        default="evidenced",
        help="与 review 相同的规则；evidenced 另外接受词边界上已有安全依据的较长停顿",
    )
    run.set_defaults(handler=command_run)

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
