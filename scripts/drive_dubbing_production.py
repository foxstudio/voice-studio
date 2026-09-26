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
ATTENTION_STAGES = {
    "needs_attention",
    "needs_gap_processing",
    "needs_timeline_work",
    "ready_to_generate",
    "failed",
    "generating",
}
REVIEW_SCHEMA = "dubbing-candidate-review-command-v2"
# The server request binds a decision to these exact facts.  They are carried
# through the review file unchanged and re-checked before every POST.
IDENTITY_FIELDS = (
    "source_revision",
    "plan_revision",
    "candidate_id",
    "audio_sha256",
    "candidate_evidence_fingerprint",
    "candidate_clip_projection_fingerprint",
)


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


def valid_aligned_word(word: Any) -> bool:
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


def review_identity(source: dict[str, Any]) -> dict[str, Any]:
    """The review-bound identity fields, or a clear error if any is missing."""

    identity = {field: source.get(field) for field in IDENTITY_FIELDS}
    missing = [
        field
        for field, value in identity.items()
        if value is None or (isinstance(value, str) and not value.strip())
    ]
    if missing:
        raise ApiError(
            "缺少候选身份字段：" + "、".join(missing) + "。"
            "请重新运行 boundaries 读取当前候选身份，再重新判断；不能自动绑定最新候选。"
        )
    return identity


def build_review_body(
    audit: dict[str, Any],
    reviews: list[dict[str, Any]],
    *,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind an Agent disposition list to one exact candidate identity.

    ``identity`` is the identity the caller already read (the review file or
    the audit used for reuse).  When omitted it falls back to the audit passed
    in, which is only safe for callers that just read that same audit.
    """

    resolved = dict(identity) if identity is not None else {
        field: audit.get(field) for field in IDENTITY_FIELDS
    }
    resolved = review_identity(resolved)
    expected = [
        str(item.get("boundary_id") or "") for item in audit.get("boundaries") or []
    ]
    if any(not value for value in expected):
        raise ApiError("当前候选的边界缺少 boundary_id，无法校验覆盖。")
    by_id: dict[str, dict[str, Any]] = {}
    for item in reviews:
        if not isinstance(item, dict):
            raise ApiError("每条边界处置都必须是对象。")
        boundary_id = str(item.get("boundary_id") or "")
        if not boundary_id:
            raise ApiError("每条边界处置都需要 boundary_id。")
        if boundary_id in by_id:
            raise ApiError(f"边界处置重复：{boundary_id}")
        by_id[boundary_id] = item
    if not expected:
        # A real single aligned word has no adjacent boundary and may submit an
        # empty full-coverage list.  A candidate with no word evidence, a
        # malformed word, or several words but no adjacent boundary is not
        # "nothing to check".
        words = audit.get("aligned_words")
        if (
            not isinstance(words, list)
            or len(words) != 1
            or not valid_aligned_word(words[0])
        ):
            raise ApiError(
                "候选没有相邻边界，但逐词证据不是恰好一个结构合法的对齐词，"
                "不能当作无需检查。"
            )
        if by_id:
            raise ApiError("当前候选没有相邻边界，处置列表应为空。")
    else:
        missing = [value for value in expected if value not in by_id]
        unknown = [value for value in by_id if value not in set(expected)]
        if missing or unknown:
            raise ApiError(
                "边界处置没有完整覆盖当前候选。"
                f"缺少 {len(missing)} 项、多出 {len(unknown)} 项："
                f"缺少={missing[:5]} 多出={unknown[:5]}"
            )
    body: dict[str, Any] = {"schema_version": REVIEW_SCHEMA}
    body.update(resolved)
    body["semantic_boundary_reviews"] = [by_id[value] for value in expected]
    return body


def load_review_file(path: Path) -> dict[str, Any]:
    """Read an Agent review file, keeping its original candidate identity.

    The file must carry the server's typed request identity; a bare review
    array or a wrapper without identity is refused, because the driver must
    never relabel an old judgment with a newer candidate's fingerprints.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ApiError(
            "处置文件必须是包含候选身份与 semantic_boundary_reviews 的对象；"
            "不再接受裸数组。请重新运行 boundaries 读取当前身份后重新判断。"
        )
    schema_version = payload.get("schema_version") or REVIEW_SCHEMA
    if schema_version != REVIEW_SCHEMA:
        raise ApiError(f"处置文件的 schema_version 不是 {REVIEW_SCHEMA}。")
    reviews = payload.get("semantic_boundary_reviews")
    if not isinstance(reviews, list):
        raise ApiError("处置文件缺少 semantic_boundary_reviews 数组。")
    identity = review_identity(payload)
    return {
        "schema_version": REVIEW_SCHEMA,
        **identity,
        "semantic_boundary_reviews": reviews,
    }


def read_audit(base_url: str, project_id: str, candidate_id: str) -> dict[str, Any]:
    """Boundary evidence of one candidate, or an ``{"error": ...}`` marker.

    Callers already branch on the marker, and a candidate id left over from an
    earlier plan revision must not abort the whole run.
    """

    try:
        return api(
            base_url,
            f"{dubbing_base(base_url, project_id)}/candidates/{candidate_id}/semantic-boundaries",
        )
    except ApiError as exc:
        return {"error": str(exc)}


def submit_reviews(
    base_url: str,
    project_id: str,
    candidate_id: str,
    reviews: list[dict[str, Any]],
    *,
    identity: dict[str, Any],
) -> dict[str, Any]:
    """Submit decisions under their original identity, refusing a stale one.

    The audit is re-read first so a concurrent replan cannot be disguised by
    old fingerprints; the server still re-validates versions, and the driver
    never overwrites the original identity with the current one.  ``identity``
    is required so a caller cannot silently relabel a decision with the latest
    candidate.
    """

    audit = read_audit(base_url, project_id, candidate_id)
    if "error" in audit:
        raise ApiError(str(audit["error"]))
    resolved = review_identity(identity)
    current = {field: audit.get(field) for field in IDENTITY_FIELDS}
    mismatched = [
        field
        for field in IDENTITY_FIELDS
        if str(current.get(field)) != str(resolved.get(field))
    ]
    if str(resolved.get("candidate_id")) != str(candidate_id):
        mismatched.append("candidate_id")
    if mismatched:
        raise ApiError(
            "处置文件身份与当前候选不一致（STALE）："
            + "、".join(dict.fromkeys(mismatched))
            + "。请重新运行 boundaries 读取当前身份并重新判断，不能自动绑定最新候选。"
        )
    body = build_review_body(audit, reviews, identity=resolved)
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
    stale_rounds = 0
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
        try:
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
        except ApiError as exc:
            message = str(exc)
            if "DUBBING_COMMIT_TIMELINE_CONFLICT" in message:
                # The new take would overlap a neighbouring clip.  Only an
                # Agent can reconcile the timeline, so hand the group back
                # instead of aborting the whole range.
                return {
                    "group_id": group_id,
                    "result": "agent_decision_required",
                    "stage": "needs_timeline_edit",
                    "message": "新声音与相邻配音冲突，需要核对现有片段范围",
                    "run": summarize(read_run(base_url, project_id)),
                }
            # A candidate left over from an earlier plan revision no longer
            # exists.  Keep asking: the next round starts a current generation
            # for the same group.  Give up only if it stays missing.
            if "CANDIDATE_NOT_FOUND" not in message:
                raise
            stale_rounds += 1
            if stale_rounds > 3:
                return {
                    "group_id": group_id,
                    "result": "stale_candidate",
                    "stage": "unknown",
                }
            time.sleep(poll_seconds)
            continue
        if str(response.get("status") or "") == "needs_attention" and not response.get("workflow_id"):
            # The executor already decided this group needs an Agent: either a
            # capacity call, a manual timeline check, or a semantic split.
            # Polling the same refusal only burns rounds.
            return {
                "group_id": group_id,
                "result": (
                    "capacity_recovery_required"
                    if str(response.get("required_action") or "") == "resolve_capacity"
                    else "agent_decision_required"
                ),
                "stage": "needs_gap_processing",
                "required_action": response.get("required_action"),
                "message": response.get("message"),
                "run": summarize(read_run(base_url, project_id)),
            }
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


def resolve_ordinary_speed_baseline(
    base_url: str,
    project_id: str,
    override: float | None = None,
) -> float:
    """Frozen ordinary speed for a new range: the explicit value, else 1.0.

    The Skill's order is user value > the recovery target's already-submitted
    frozen value (owned by the service) > the new-production default 1.0.  A
    missing history is never filled from faster previous clips or a
    text-pressure estimate, so a brand-new range starts at 1.0 even when the
    project's last takes ran at 1.2.
    """

    if override is not None:
        value = float(override)
        if not 1.0 <= value <= 2.0:
            raise ApiError(
                "--speed-baseline 必须不小于 1.0 且不超过工具上限 2.0；"
                "新范围默认 1.0，已有冻结任务由服务端保持原值。"
            )
        return round(value, 2)
    return 1.0


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
    """Decide the next whole-range action from the durable read model."""

    remaining = [str(item["group_id"]) for item in unfinished_groups(run)]
    if remaining:
        return {"action": "advance", "group_ids": remaining}
    return {"action": "complete", "group_ids": []}


def command_run(args: argparse.Namespace) -> int:
    """Drive the remaining range until every group is terminal or recovering.

    The driver only schedules and gathers evidence; it never fabricates a
    semantic decision or splits a take by the intermediate translation
    subtitle boxes. Explicit per-boundary review stays available through the
    `boundaries`/`review` subcommands for intentional editing.
    """

    stalled = 0
    previous: tuple[int, int] | None = None
    speed_baseline = resolve_ordinary_speed_baseline(
        args.base_url,
        args.project,
        args.speed_baseline,
    )

    def advance_targets(
        group_ids: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        capacity: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        for group_id in group_ids:
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
                    {"group_id": group_id, "message": outcome.get("message")}
                )
                continue
            if outcome["result"] != "terminal":
                # A group that needs an Agent decision is left for the caller,
                # but must not stop the independent groups behind it.
                deferred.append(
                    {
                        "group_id": group_id,
                        "candidate_id": outcome.get("candidate_id"),
                        "reason": str(
                            outcome.get("message") or outcome.get("result")
                        )[:200],
                    }
                )
        return capacity, deferred

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
        unfinished_ids = [
            str(item["group_id"]) for item in unfinished_groups(run)
        ]
        capacity, deferred = advance_targets(unfinished_ids)
        if capacity or deferred:
            print(
                json.dumps(
                    {
                        "result": (
                            "capacity_recovery_required"
                            if capacity
                            else "agent_decision_required"
                        ),
                        "cycle": cycle,
                        "capacity": capacity,
                        "deferred": deferred,
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
    identity = review_identity(audit)
    print(
        json.dumps(
            {
                "group_id": args.group,
                "candidate_id": candidate_id,
                "schema_version": REVIEW_SCHEMA,
                "status": audit.get("status"),
                "source_revision": identity["source_revision"],
                "plan_revision": identity["plan_revision"],
                "audio_sha256": identity["audio_sha256"],
                "candidate_evidence_fingerprint": identity[
                    "candidate_evidence_fingerprint"
                ],
                "candidate_clip_projection_fingerprint": identity[
                    "candidate_clip_projection_fingerprint"
                ],
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
    if not args.file:
        raise ApiError(
            "review 只会提交 Agent 写出的处置文件。请先用 boundaries 读取候选与"
            "全部边界证据，写出包含候选身份与 semantic_boundary_reviews 的 JSON，"
            "再用 review --file 提交。"
        )
    payload = load_review_file(Path(args.file))
    identity = review_identity(payload)
    reviews = payload["semantic_boundary_reviews"]
    report = submit_reviews(
        args.base_url,
        args.project,
        candidate_id,
        reviews,
        identity=identity,
    )
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
        help="整段沿用的冻结速度（1.0–2.0）；省略时新范围默认 1.0",
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
    review.add_argument("--file", default=None, help="Agent 写出的边界处置 JSON 文件")
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
        help="整段沿用冻结速度（1.0–2.0）；省略时新范围默认 1.0",
    )
    run.add_argument("--max-rounds", type=int, default=60, help="单组等待轮数上限")
    run.add_argument("--max-cycles", type=int, default=400)
    run.add_argument(
        "--max-stalled-cycles",
        type=int,
        default=4,
        help="连续多少轮既无新完成也无待判断组就退出",
    )
    run.set_defaults(handler=command_run)

    return parser


# Rules that used to auto-derive semantic boundaries or auto-split by the
# intermediate subtitle boxes.  They are gone; point an old caller at the
# Agent-decision path instead of silently doing the wrong thing.
LEGACY_FLAGS = {
    "--accept-continuous": (
        "自动规则判定已移除：标点、0ms、短停顿、安全 retain 都不能替代 Agent 的"
        "语义判断。请用 boundaries 读取证据，由 Agent 写出 semantic_boundary_reviews "
        "JSON，再用 review --file 提交。"
    ),
    "--gap-policy": (
        "gap_policy 自动放宽已移除：长停顿不能按低能量证据自动判为可接受。"
        "请走 Agent 逐边界决定与 review --file。"
    ),
    "--decisions": (
        "--decisions 不再与自动规则合并。请直接把 Agent 的完整处置写入 JSON，"
        "用 review --file 提交。"
    ),
    "--no-align": (
        "run 不再按中间翻译字幕盒自动分片。真正的语义分片请通过已有公共编辑/"
        "恢复入口表达 Agent 的决定。"
    ),
    "--align-threshold-ms": (
        "run 不再按中间翻译字幕盒自动分片，阈值参数已移除。请通过已有公共编辑/"
        "恢复入口表达 Agent 的语义分片决定。"
    ),
}


def legacy_flag_message(argv: list[str]) -> str | None:
    for flag, message in LEGACY_FLAGS.items():
        if flag in argv or any(value.startswith(f"{flag}=") for value in argv):
            return f"{flag} 已废弃：{message}"
    return None


def main(argv: list[str] | None = None) -> int:
    resolved = list(sys.argv[1:] if argv is None else argv)
    message = legacy_flag_message(resolved)
    if message is not None:
        print(f"错误：{message}", file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(resolved)
    try:
        return int(args.handler(args))
    except ApiError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
