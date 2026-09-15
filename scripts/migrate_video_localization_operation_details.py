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
    video_localization_operation_detail_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or apply one bounded explicit migration of legacy "
            "semantic-v2 operation detail cores."
        )
    )
    parser.add_argument("--limit", type=int, default=1_000)
    parser.add_argument("--after-project-id")
    parser.add_argument("--after-operation-id")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write validated missing cores. The default is query-only.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    args = parser.parse_args()

    report = (
        video_localization_operation_detail_migration
        .migrate_operation_details(
            apply=args.apply,
            limit=args.limit,
            after_project_id=args.after_project_id,
            after_operation_id=args.after_operation_id,
        )
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        mode = "apply" if report.apply else "plan"
        print(
            f"Operation detail migration ({mode}): "
            f"{report.scanned_operation_count} scanned; "
            f"{report.planned_operation_count} planned; "
            f"{report.migrated_operation_count} migrated; "
            f"{report.unchanged_operation_count} unchanged; "
            f"{report.rejected_operation_count} rejected"
        )
        print(f"Truncated: {'yes' if report.truncated else 'no'}")
        if report.next_project_id:
            print(
                "Next cursor: "
                f"{report.next_project_id}/"
                f"{report.next_operation_id}"
            )
        else:
            print("Next cursor: complete")
        for result in report.results:
            print(
                "- "
                f"project={result.project_id} "
                f"operation={result.operation_id} "
                f"status={result.status} "
                "issues="
                + (
                    ",".join(result.issue_codes)
                    or "none"
                )
            )
    return 1 if (
        report.rejected_operation_count or report.truncated
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
