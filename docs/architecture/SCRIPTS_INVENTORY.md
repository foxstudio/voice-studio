# Scripts Inventory

**Status**: dry-run inventory plus checker, no files moved\
**Date**: 2026-06-11\
**Related RFC**: `docs/architecture/DIRECTORY_GOVERNANCE_RFC.md`\

## Purpose

This inventory groups current `scripts/` files by likely ownership before any directory migration. It is intentionally documentation-only.

Use the dry-run checker before any script move:

```bash
.venv/bin/python scripts/migration/check_script_paths.py
.venv/bin/python scripts/migration/check_script_paths.py --fail-on-risk
```

The first command reports migration risks and exits 0. The second command exits non-zero while risks remain, making it suitable as a pre-migration gate.

## Proposed Groups

### imports

Voice/corpus import and ingestion scripts:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/anime_voice_import.py` | `scripts/anime_voice_import.py` | Stable user-facing import entrypoint; group later only with wrapper. |
| `scripts/batch_import_local.py` | `scripts/imports/batch_import_local.py` | Local batch import. |
| `scripts/batch_voice_import.py` | `scripts/batch_voice_import.py` | Stable user-facing import entrypoint; group later only with wrapper. |
| `scripts/curated_voice_import.py` | `scripts/curated_voice_import.py` | Stable user-facing import entrypoint; group later only with wrapper. |
| `scripts/full_voice_import.py` | `scripts/full_voice_import.py` | Stable user-facing import entrypoint; group later only with wrapper. |
| `scripts/genshin_batch_import.py` | `scripts/imports/genshin_batch_import.py` | Uses env/default source and report paths. |
| `scripts/genshin_npc_import.py` | `scripts/imports/genshin_npc_import.py` | Uses env/default source and report paths. |
| `scripts/voice_importer.py` | `scripts/voice_importer.py` | Stable user-facing import entrypoint; group later only with wrapper. |

### maintenance

Backfill, cleanup, data audit, and repair scripts:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/backfill_voice_reference_text.py` | `scripts/backfill_voice_reference_text.py` | Stable user-facing maintenance entrypoint; group later only with wrapper. |
| `scripts/genshin_cleanup_refs.py` | `scripts/maintenance/genshin_cleanup_refs.py` | Review generated report dependencies. |
| `scripts/genshin_ref_text_check.py` | `scripts/maintenance/genshin_ref_text_check.py` | Review generated report dependencies. |
| `scripts/genshin_reorder_refs.py` | `scripts/maintenance/genshin_reorder_refs.py` | Review generated report dependencies. |
| `scripts/replace_short_refs.py` | `scripts/maintenance/replace_short_refs.py` | Review before move. |
| `scripts/migration/audit_voice_studio_data.py` | `scripts/migration/audit_voice_studio_data.py` | Stable migration entrypoint. |
| `scripts/migration/restore_voice_library.py` | `scripts/migration/restore_voice_library.py` | Verifies and restores the versioned voice-library backup into a fresh managed data root. |

### evaluation

Alignment, quality, model verification, and eval scripts:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/alignment_test.py` | `scripts/evaluation/alignment_test.py` | Review CLI examples before move. |
| `scripts/alignment_test_v2.py` | `scripts/evaluation/alignment_test_v2.py` | Self-contained usage examples now ignored by checker. |
| `scripts/dump_pytorch_outputs.py` | `scripts/evaluation/dump_pytorch_outputs.py` | Review CLI examples before move. |
| `scripts/dump_pytorch_outputs_v2.py` | `scripts/dump_pytorch_outputs_v2.py` | Stable paired entrypoint for `alignment_test_v2.py`; group later only with wrapper. |
| `scripts/run_voice_studio_deep_eval.py` | `scripts/evaluation/run_voice_studio_deep_eval.py` | Evaluation entrypoint. |
| `scripts/run_voice_studio_quality_suite.py` | `scripts/evaluation/run_voice_studio_quality_suite.py` | Evaluation entrypoint. |
| `scripts/verify_mlx_v2.py` | `scripts/evaluation/verify_mlx_v2.py` | Model verification. |

### video localization governance

Architecture, ledger, and browser verification entrypoints. These paths are
already part of the active regression and migration toolchain, so they remain
stable rather than being proposed for relocation.

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/architecture/video_localization_architecture_policy.json` | `scripts/architecture/video_localization_architecture_policy.json` | Architecture debt allowlist consumed by the regression gate. |
| `scripts/audit_video_localization_architecture.py` | `scripts/audit_video_localization_architecture.py` | Generates and checks the current architecture baseline. |
| `scripts/audit_video_localization_bundle.mjs` | `scripts/audit_video_localization_bundle.mjs` | Checks the production bundle budget. |
| `scripts/audit_video_localization_operation_attempts.py` | `scripts/audit_video_localization_operation_attempts.py` | Audits durable Provider attempt records. |
| `scripts/audit_video_localization_operation_details.py` | `scripts/audit_video_localization_operation_details.py` | Audits operation detail authority and projections. |
| `scripts/audit_video_localization_operation_ledger.py` | `scripts/audit_video_localization_operation_ledger.py` | Audits operation ledger integrity. |
| `scripts/audit_video_localization_operation_summaries.py` | `scripts/audit_video_localization_operation_summaries.py` | Audits operation summary coverage and authority. |
| `scripts/backfill_video_localization_operation_summaries.py` | `scripts/backfill_video_localization_operation_summaries.py` | Managed summary backfill entrypoint. |
| `scripts/benchmark_video_localization_operation_reads.py` | `scripts/benchmark_video_localization_operation_reads.py` | Fixed-data operation read benchmark. |
| `scripts/close_video_localization_operation_summary_authority.py` | `scripts/close_video_localization_operation_summary_authority.py` | Closes the summary authority migration after audits pass. |
| `scripts/evaluate_subtitle_alignment.py` | `scripts/evaluate_subtitle_alignment.py` | Fixed-data subtitle alignment evaluator. |
| `scripts/migrate_video_localization_operation_details.py` | `scripts/migrate_video_localization_operation_details.py` | Managed operation detail migration entrypoint. |
| `scripts/migrate_video_localization_operation_workflow_versions.py` | `scripts/migrate_video_localization_operation_workflow_versions.py` | Managed workflow-version migration entrypoint. |
| `scripts/promote_video_localization_operation_summaries.py` | `scripts/promote_video_localization_operation_summaries.py` | Promotes verified operation summaries. |
| `scripts/test_audit_video_localization_bundle.mjs` | `scripts/test_audit_video_localization_bundle.mjs` | Fixed fixtures for the bundle analyzer. |
| `scripts/verify_video_localization_browser.mjs` | `scripts/verify_video_localization_browser.mjs` | Real-browser regression entrypoint. |

### dev

Local smoke/debug helpers:

| Current path | Proposed path | Notes |
| --- | --- | --- |
| `scripts/analyze_genshin_pack.py` | `scripts/dev/analyze_genshin_pack.py` | Uses env/default source and report paths. |
| `scripts/genshin_asr_check.py` | `scripts/dev/genshin_asr_check.py` | Debug utility. |
| `scripts/genshin_asr_fix.py` | `scripts/dev/genshin_asr_fix.py` | Debug/repair utility. |
| `scripts/qwen_forced_align_worker.py` | `scripts/dev/qwen_forced_align_worker.py` | Worker helper; check caller paths before move. |
| `scripts/webui_smoke_playwright.mjs` | `scripts/webui_smoke_playwright.mjs` | Stable smoke-test entrypoint; group later only with wrapper. |

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
- Stable top-level entrypoints remain in place until wrapper migrations are explicitly planned.

Expected checker invariants:

- Missing inventory sources: 0
- Unmanaged scripts/artifacts: 0
- Absolute path hits: 0
- Proposed moved scripts with references: 0

The current `--fail-on-risk` result is expected to pass after the stable-entrypoint waivers above.

## Special Case: `voice_studio_batch.py`

`scripts/voice_studio_batch.py` is a user-facing batch entrypoint referenced by docs. Keep it at the top level until a compatibility wrapper is planned.

Recommended later migration:

1. Move implementation to `scripts/dev` or `scripts/maintenance` only after deciding ownership.
2. Leave a top-level `scripts/voice_studio_batch.py` wrapper for compatibility.
3. Update docs after wrapper behavior is verified.

## Safe Next Step

Before moving any script:

1. Run `scripts/migration/check_script_paths.py --fail-on-risk`.
2. Move one group at a time only after compatibility wrappers are planned.
3. Keep stable top-level entrypoints until their wrappers are tested.
4. Run:
   - `.venv/bin/python -m compileall -q scripts`
   - docs reference `rg` checks for old paths.

No files were moved as part of this inventory.
