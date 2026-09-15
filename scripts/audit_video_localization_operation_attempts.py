#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import (  # noqa: E402
    video_localization_operation_attempt_audit,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only reconciliation of latest video-localization "
            "execution attempts against authoritative Project JSON."
        )
    )
    parser.add_argument("--limit", type=int, default=1_000)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the bounded audit is incomplete or inconsistent",
    )
    args = parser.parse_args()

    report = (
        video_localization_operation_attempt_audit
        .reconcile_latest_attempts(limit=args.limit)
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(
            "Operation attempt audit: "
            f"{report.checked_operation_count}/{report.operation_count} "
            "latest attempts checked; "
            f"{report.total_attempt_count} total attempts"
        )
        print(
            "Categories: "
            + (
                ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(
                        report.category_counts.items()
                    )
                )
                or "none"
            )
        )
        print(f"Truncated: {'yes' if report.truncated else 'no'}")
        print(f"Issues: {len(report.issues)}")
        for issue in report.issues:
            print(
                "- "
                f"{issue.category}: "
                f"project={issue.project_id} "
                f"operation={issue.operation_id} "
                f"attempt={issue.attempt_number} "
                f"status={issue.attempt_status} "
                f"authority={issue.authority_status or 'missing'}"
            )
    return 1 if args.check and not report.healthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
