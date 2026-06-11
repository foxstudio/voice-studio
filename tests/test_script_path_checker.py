from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts" / "migration" / "check_script_paths.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_script_paths", CHECKER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_inventory_parser_finds_planned_script_moves():
    checker = load_checker()

    entries = checker.parse_inventory()
    by_current = {entry.current: entry.proposed for entry in entries}

    assert by_current["scripts/voice_importer.py"] == "scripts/imports/voice_importer.py"
    assert by_current["scripts/webui_smoke_playwright.mjs"] == "scripts/dev/webui_smoke_playwright.mjs"
    assert by_current["scripts/genshin_analysis.json"] == "scripts/reports/genshin_analysis.json"


def test_report_is_dry_run_and_enforces_portable_paths():
    checker = load_checker()

    report = checker.build_report()

    assert report.absolute_path_hits == []
    assert "scripts/voice_studio_batch.py" not in report.reference_hits
    assert any(path.endswith(".py") or path.endswith(".mjs") or path.endswith(".json") for path in checker.current_script_files())
