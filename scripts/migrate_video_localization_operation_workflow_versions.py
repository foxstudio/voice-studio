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
    video_localization_operation_workflow_version_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or explicitly migrate legacy video-localization "
            "workflow-version null sentinels."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=1_000)
    parser.add_argument("--after-project-id")
    parser.add_argument("--after-operation-id")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the bounded batch is incomplete or rejected",
    )
    args = parser.parse_args()
    report = (
        video_localization_operation_workflow_version_migration
        .migrate_legacy_null_workflow_versions(
            apply=args.apply,
            limit=args.limit,
            after_project_id=args.after_project_id,
            after_operation_id=args.after_operation_id,
        )
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        mode = "apply" if report.apply else "query-only plan"
        print(
            "Operation workflow-version migration "
            f"({mode}): scanned={report.scanned_operation_count}; "
            f"planned={report.planned_operation_count}; "
            f"migrated={report.migrated_operation_count}; "
            f"rejected={report.rejected_operation_count}"
        )
        print(f"Truncated: {'yes' if report.truncated else 'no'}")
        if report.next_project_id and report.next_operation_id:
            print(
                "Next cursor: "
                f"project={report.next_project_id} "
                f"operation={report.next_operation_id}"
            )
        for result in report.results:
            if result.status == "rejected":
                print(
                    "- rejected: "
                    f"project={result.project_id} "
                    f"operation={result.operation_id} "
                    f"issues={','.join(result.issue_codes)}"
                )
    return 1 if args.check and not report.healthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
