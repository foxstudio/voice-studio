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
    video_localization_operation_ledger_audit,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only reconciliation of the video-localization operation "
            "ledger against its Project compatibility mirror."
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
            "exit non-zero when the bounded audit is incomplete, "
            "inconsistent, or has unapplied outbox events"
        ),
    )
    args = parser.parse_args()

    report = (
        video_localization_operation_ledger_audit
        .reconcile_operation_ledger(limit=args.limit)
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(
            "Operation ledger audit: "
            f"{report.checked_ledger_count}/"
            f"{report.total_ledger_count} ledger rows checked; "
            f"{report.total_mirror_count} Project mirror rows"
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
        print(
            "Pending outbox events: "
            f"{report.pending_outbox_count}"
        )
        print(f"Truncated: {'yes' if report.truncated else 'no'}")
        print(f"Issues: {len(report.issues)}")
        for issue in report.issues:
            print(
                "- "
                f"{issue.category}: "
                f"project={issue.project_id} "
                f"operation={issue.operation_id} "
                f"ledger={issue.ledger_status or 'missing'} "
                f"mirror={issue.mirror_status or 'missing'}"
            )
    return 1 if args.check and not report.healthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
