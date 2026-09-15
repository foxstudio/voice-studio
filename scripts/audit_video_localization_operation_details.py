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

from app.domains.video_localization import (  # noqa: E402
    operation_detail_reconciliation,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only, same-snapshot audit of managed operation "
            "detail authorities, durable artifacts and the Project mirror."
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
        help=(
            "exit non-zero when the bounded audit is truncated or "
            "contains a non-matching operation"
        ),
    )
    args = parser.parse_args()

    report = (
        operation_detail_reconciliation
        .reconcile_operation_details(limit=args.limit)
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(
            "Operation detail audit: "
            f"{report.checked_candidate_count}/"
            f"{report.total_candidate_count} candidates checked"
        )
        print(
            "Statuses: "
            + (
                ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(
                        report.status_counts.items()
                    )
                )
                or "none"
            )
        )
        print(f"Truncated: {'yes' if report.truncated else 'no'}")
        for result in report.results:
            if result.matched:
                continue
            print(
                "- "
                f"{result.status}: "
                f"project={result.project_id} "
                f"operation={result.operation_id} "
                f"issues={','.join(result.issues) or 'none'}"
            )
    return 1 if args.check and not report.healthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
