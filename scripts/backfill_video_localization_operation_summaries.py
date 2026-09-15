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
            "Backfill one bounded, resumable batch of video-localization "
            "operation summary shadows."
        )
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-project-id")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    args = parser.parse_args()

    report = (
        video_localization_operation_summary_migration
        .backfill_operation_summaries(
            operation_summary_legacy
            .decode_legacy_summary_source,
            after_project_id=args.after_project_id,
            limit=args.limit,
        )
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(
            "Operation summary backfill: "
            f"{report.scanned_project_count} projects scanned; "
            f"{report.backfilled_project_count} backfilled; "
            f"{report.unchanged_project_count} unchanged; "
            f"{report.skipped_project_count} skipped; "
            f"{report.repair_required_project_count} repair required"
        )
        print(f"Truncated: {'yes' if report.truncated else 'no'}")
        print(
            "Next cursor: "
            f"{report.next_cursor or 'complete'}"
        )
        for project in report.projects:
            print(
                "- "
                f"project={project.project_id} "
                f"status={project.status} "
                f"category={project.category} "
                f"operations={project.operation_count}"
            )
    return 1 if report.repair_required_project_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
