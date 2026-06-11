# Scripts Inventory

**Status**: dry-run inventory, no files moved  
**Date**: 2026-06-11  
**Related RFC**: `docs/architecture/DIRECTORY_GOVERNANCE_RFC.md`  

## Purpose

This inventory groups current `scripts/` files by likely ownership before any directory migration. It is intentionally documentation-only.

## Proposed Groups

### imports

Voice/corpus import and ingestion scripts:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/anime_voice_import.py` | `scripts/imports/anime_voice_import.py` | HuggingFace/anime voice import. |
| `scripts/batch_import_local.py` | `scripts/imports/batch_import_local.py` | Local batch import. |
| `scripts/batch_voice_import.py` | `scripts/imports/batch_voice_import.py` | Local curated voice import. |
| `scripts/curated_voice_import.py` | `scripts/imports/curated_voice_import.py` | Curated import. |
| `scripts/full_voice_import.py` | `scripts/imports/full_voice_import.py` | Broad local import. |
| `scripts/genshin_batch_import.py` | `scripts/imports/genshin_batch_import.py` | Contains absolute project paths; patch before moving. |
| `scripts/genshin_npc_import.py` | `scripts/imports/genshin_npc_import.py` | Contains absolute project paths; patch before moving. |
| `scripts/voice_importer.py` | `scripts/imports/voice_importer.py` | Early/smaller importer. |

### maintenance

Backfill, cleanup, data audit, and repair scripts:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/backfill_voice_reference_text.py` | `scripts/maintenance/backfill_voice_reference_text.py` | Referenced by docs. |
| `scripts/genshin_cleanup_refs.py` | `scripts/maintenance/genshin_cleanup_refs.py` | Review generated report dependencies. |
| `scripts/genshin_ref_text_check.py` | `scripts/maintenance/genshin_ref_text_check.py` | Review generated report dependencies. |
| `scripts/genshin_reorder_refs.py` | `scripts/maintenance/genshin_reorder_refs.py` | Review generated report dependencies. |
| `scripts/replace_short_refs.py` | `scripts/maintenance/replace_short_refs.py` | Review before move. |
| `scripts/migration/audit_voice_studio_data.py` | `scripts/maintenance/migration/audit_voice_studio_data.py` | Keep current path until docs and handoff instructions are updated. |

### evaluation

Alignment, quality, model verification, and eval scripts:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/alignment_test.py` | `scripts/evaluation/alignment_test.py` | Review CLI examples before move. |
| `scripts/alignment_test_v2.py` | `scripts/evaluation/alignment_test_v2.py` | References `scripts/dump_pytorch_outputs_v2.py`. |
| `scripts/dump_pytorch_outputs.py` | `scripts/evaluation/dump_pytorch_outputs.py` | Review CLI examples before move. |
| `scripts/dump_pytorch_outputs_v2.py` | `scripts/evaluation/dump_pytorch_outputs_v2.py` | Referenced by alignment script. |
| `scripts/run_voice_studio_deep_eval.py` | `scripts/evaluation/run_voice_studio_deep_eval.py` | Evaluation entrypoint. |
| `scripts/run_voice_studio_quality_suite.py` | `scripts/evaluation/run_voice_studio_quality_suite.py` | Evaluation entrypoint. |
| `scripts/verify_mlx_v2.py` | `scripts/evaluation/verify_mlx_v2.py` | Model verification. |

### dev

Local smoke/debug helpers:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/analyze_genshin_pack.py` | `scripts/dev/analyze_genshin_pack.py` | Contains absolute project path output. |
| `scripts/genshin_asr_check.py` | `scripts/dev/genshin_asr_check.py` | Debug utility. |
| `scripts/genshin_asr_fix.py` | `scripts/dev/genshin_asr_fix.py` | Debug/repair utility. |
| `scripts/qwen_forced_align_worker.py` | `scripts/dev/qwen_forced_align_worker.py` | Worker helper; check caller paths before move. |
| `scripts/webui_smoke_playwright.mjs` | `scripts/dev/webui_smoke_playwright.mjs` | Referenced by docs. |

### reports

Generated JSON reports or analysis outputs:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/genshin_analysis.json` | `scripts/reports/genshin_analysis.json` | Generated artifact; do not move until dependent scripts are patched. |
| `scripts/genshin_import_report.json` | `scripts/reports/genshin_import_report.json` | Generated artifact. |
| `scripts/genshin_npc_import_report.json` | `scripts/reports/genshin_npc_import_report.json` | Generated artifact. |
| `scripts/import_report.json` | `scripts/reports/import_report.json` | Generated artifact. |

## Known Reference Risks

- `README.md` describes `scripts/` as a flat directory.
- `docs/VOICE_STUDIO_TODO.md` references:
  - `scripts/voice_studio_batch.py`
  - `scripts/backfill_voice_reference_text.py`
  - multiple import scripts.
- `docs/VOICE_STUDIO_BATCH_AGENT.md` references `scripts/voice_studio_batch.py`.
- `docs/VOICE_STUDIO_ENGINE_PARAMETERS.md` references `scripts/voice_studio_batch.py`.
- `docs/MIMO_V2_5_CLOUD_API_RFC.md` references `scripts/webui_smoke_playwright.mjs`.
- Several scripts contain absolute paths under `/Users/foxmacstudio/Projects/mlx-indextts/scripts/...`.

## Special Case: `voice_studio_batch.py`

`scripts/voice_studio_batch.py` is a user-facing batch entrypoint referenced by docs. Keep it at the top level until a compatibility wrapper is planned.

Recommended later migration:

1. Move implementation to `scripts/dev` or `scripts/maintenance` only after deciding ownership.
2. Leave a top-level `scripts/voice_studio_batch.py` wrapper for compatibility.
3. Update docs after wrapper behavior is verified.

## Safe Next Step

Before moving any script:

1. Add a path-reference test or simple `rg` checklist.
2. Patch scripts with absolute paths to derive repo root from `Path(__file__)`.
3. Move one group at a time.
4. Run:
   - `.venv/bin/python -m compileall -q scripts`
   - docs reference `rg` checks for old paths.

No files were moved as part of this inventory.
