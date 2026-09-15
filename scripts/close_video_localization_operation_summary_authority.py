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
            "Atomically close the video-localization operation summary "
            "reader after a bounded full-store reconciliation."
        )
    )
    parser.add_argument("--limit", type=int, default=1_000)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    args = parser.parse_args()

    report = (
        video_localization_operation_summary_migration
        .close_operation_summary_authority(
            operation_summary_legacy.decode_legacy_summary_source,
            limit=args.limit,
        )
    )
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(
            "Operation summary authority close: "
            f"{report.scanned_project_count} projects scanned; "
            f"{report.authoritative_project_count} authoritative; "
            f"{report.unchanged_project_count} unchanged; "
            f"{report.skipped_project_count} skipped; "
            f"{report.rejected_project_count} rejected"
        )
        print(f"Closed: {'yes' if report.closed else 'no'}")
        print(f"Changed: {'yes' if report.changed else 'no'}")
        print(f"Truncated: {'yes' if report.truncated else 'no'}")
        print(f"Closed at: {report.closed_at or 'not closed'}")
        for project in report.projects:
            print(
                "- "
                f"project={project.project_id} "
                f"status={project.status} "
                "issues="
                + (
                    ",".join(project.issue_categories)
                    or "none"
                )
            )
    return int(
        not report.closed
        or report.truncated
        or report.rejected_project_count > 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
