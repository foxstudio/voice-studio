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
    operation_summary_legacy,
)
from app.services import (  # noqa: E402
    video_localization_operation_summary_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only, same-snapshot reconciliation of legacy and shadow "
            "video-localization operation summaries."
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
            "exit non-zero when the bounded audit is incomplete or "
            "inconsistent"
        ),
    )
    args = parser.parse_args()

    report = (
        video_localization_operation_summary_migration
        .reconcile_operation_summaries(
            operation_summary_legacy
            .decode_legacy_summary_source,
            limit=args.limit,
        )
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(
            "Operation summary audit: "
            f"{report.checked_project_count}/"
            f"{report.total_project_count} projects checked; "
            f"{report.matched_operation_count}/"
            f"{report.checked_legacy_count} legacy operations matched"
        )
        print(
            "Rows: "
            f"legacy={report.checked_legacy_count} "
            f"ledger={report.checked_ledger_count}/"
            f"{report.total_ledger_count} "
            f"summary={report.checked_summary_count}/"
            f"{report.total_summary_count}"
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
        print(
            "Issues: "
            f"{len(report.issues)}/{report.total_issue_count}"
        )
        for issue in report.issues:
            print(
                "- "
                f"{issue.category}: "
                f"project={issue.project_id} "
                f"operation={issue.operation_id or 'project'}"
            )
    return 1 if args.check and not report.healthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
