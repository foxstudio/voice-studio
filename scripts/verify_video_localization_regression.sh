#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_frontend_dir="${repo_root}/frontend"
python_bin="${repo_root}/.venv/bin/python"
mode="${1:---full}"
node --test "${repo_root}/scripts/browser_audio_options.test.mjs"
validation_root="$(mktemp -d "${TMPDIR:-/tmp}/voice-studio-regression.XXXXXX")"
# Canonicalize macOS /var -> /private/var before Svelte writes relative imports.
validation_root="$(cd "${validation_root}" && pwd -P)"
frontend_dir="${validation_root}/frontend"
export VOICE_STUDIO_SVELTE_OUT_DIR="${frontend_dir}/.svelte-kit"
export VOICE_STUDIO_FRONTEND_DIST="${validation_root}/build"
cleanup_validation_root() {
  rm -rf "${validation_root}"
}
trap cleanup_validation_root EXIT INT TERM

if [[ ! -x "${python_bin}" ]]; then
  echo "未找到项目 Python 环境：${python_bin}" >&2
  exit 1
fi

"${python_bin}" "${repo_root}/scripts/isolated_frontend_workspace.py" \
  "${source_frontend_dir}" "${frontend_dir}"
ln -s "${frontend_dir}/node_modules" "${validation_root}/node_modules"
# Generate the config that this disposable checkout's tsconfig actually extends.
pnpm --dir "${frontend_dir}" exec svelte-kit sync

run_quick_backend() {
  "${python_bin}" -m pytest -q \
    "${repo_root}/tests/test_isolated_frontend_workspace.py" \
    "${repo_root}/tests/test_video_localization_asr_development_continuation.py" \
    "${repo_root}/tests/test_video_localization_submission_binding.py" \
    "${repo_root}/tests/test_video_localization_localization_terminal_commit.py" \
    "${repo_root}/tests/test_video_localization_binding_repair_api.py" \
    "${repo_root}/tests/test_video_localization_binding_repair.py" \
    "${repo_root}/tests/test_tts_handoff_event_loop.py" \
    "${repo_root}/tests/test_dubbing_content_integrity.py" \
    "${repo_root}/tests/test_video_localization_dubbing_content_adoption.py" \
    "${repo_root}/tests/test_video_localization_placement_replay.py" \
    "${repo_root}/tests/test_video_localization_placement_replay_api.py" \
    "${repo_root}/tests/test_video_localization_media_adoption_concurrency.py" \
    "${repo_root}/tests/test_video_localization_dubbing_completion.py" \
    "${repo_root}/tests/test_video_localization_dubbing_manual_timing.py" \
    "${repo_root}/tests/test_video_localization_capacity_reservation.py" \
    "${repo_root}/tests/test_video_localization_dubbing_plan_continuation.py" \
    "${repo_root}/tests/test_video_localization_dubbing_recovery.py" \
    "${repo_root}/tests/test_video_localization_dubbing_recovery_contract.py" \
    "${repo_root}/tests/test_video_localization_dubbing_preflight.py" \
    "${repo_root}/tests/test_generation_task_scheduler.py" \
    "${repo_root}/tests/test_generation_queue_web_harness.py" \
    "${repo_root}/tests/test_video_localization_localization_edit_spans.py" \
    "${repo_root}/tests/test_video_localization_finalization_round_coordinates.py" \
    "${repo_root}/tests/test_video_localization_localization_document_brief.py" \
    "${repo_root}/tests/test_video_localization_document_brief_replay.py" \
    "${repo_root}/tests/test_video_localization_document_brief_adaptive_rules.py" \
    "${repo_root}/tests/test_video_localization_brief_service_journal.py" \
    "${repo_root}/tests/test_video_localization_candidate_recovery.py" \
    "${repo_root}/tests/test_video_localization_evidence_batch_replay.py" \
    "${repo_root}/tests/test_video_localization_generation_batch_replay.py" \
    "${repo_root}/tests/test_video_localization_evidence_consumer_contract.py" \
    "${repo_root}/tests/test_video_localization_evidence_authority.py" \
    "${repo_root}/tests/test_video_localization_alignment_batch_replay.py" \
    "${repo_root}/tests/test_video_localization_brief_contracts.py" \
    "${repo_root}/tests/test_video_localization_capacity_budgets.py" \
    "${repo_root}/tests/test_video_localization_brief_stages.py" \
    "${repo_root}/tests/test_video_localization_localization_spoken_script.py" \
    "${repo_root}/tests/test_video_localization_localization_development_execution.py" \
    "${repo_root}/tests/test_video_localization_development_llm_batches.py" \
    "${repo_root}/tests/test_task_orchestration_contract.py" \
    "${repo_root}/tests/test_video_localization_operation_media_summaries.py" \
    "${repo_root}/tests/test_video_localization_operation_summary_projection.py" \
    "${repo_root}/tests/test_video_localization_operation_summary_store.py" \
    "${repo_root}/tests/test_video_localization_operation_summary_migration.py" \
    "${repo_root}/tests/test_video_localization_operation_summary_authority.py" \
    "${repo_root}/tests/test_video_localization_operation_summary_reader.py" \
    "${repo_root}/tests/test_video_localization_operation_detail_core_store.py" \
    "${repo_root}/tests/test_video_localization_operation_detail_shadow.py" \
    "${repo_root}/tests/test_video_localization_operation_detail_reconciliation.py" \
    "${repo_root}/tests/test_video_localization_operation_detail_reader.py" \
    "${repo_root}/tests/test_video_localization_operation_detail_migration.py" \
    "${repo_root}/tests/test_video_localization_operation_workflow_version_migration.py" \
    "${repo_root}/tests/test_video_localization_operation_feed_v2.py" \
    "${repo_root}/tests/test_video_localization_operation_attempt_store.py" \
    "${repo_root}/tests/test_video_localization_operation_step_store.py" \
    "${repo_root}/tests/test_video_localization_operation_step_adjudication_store.py" \
    "${repo_root}/tests/test_video_localization_provider_step_lifecycle.py" \
    "${repo_root}/tests/test_video_localization_provider_result_recovery.py" \
    "${repo_root}/tests/test_video_localization_llm_provider_execution.py" \
    "${repo_root}/tests/test_video_localization_semantic_tts_grouping.py" \
    "${repo_root}/tests/test_video_localization_semantic_tts_grouping_contract.py" \
    "${repo_root}/tests/test_video_localization_semantic_tts_grouping_execution.py" \
    "${repo_root}/tests/test_video_localization_semantic_tts_grouping_operation.py" \
    "${repo_root}/tests/test_video_localization_operation_artifact_store.py" \
    "${repo_root}/tests/test_video_localization_source_audio_operation.py" \
    "${repo_root}/tests/test_video_localization_stem_separation_operation.py" \
    "${repo_root}/tests/test_video_localization_reference_candidates_operation.py" \
    "${repo_root}/tests/test_video_localization_speaker_diarization_step.py" \
    "${repo_root}/tests/test_video_localization_asr_development_step.py" \
    "${repo_root}/tests/test_video_localization_document_understanding_gateway.py" \
    "${repo_root}/tests/test_video_localization_document_understanding_provider_gateway.py" \
    "${repo_root}/tests/test_video_localization_document_understanding_execution.py" \
    "${repo_root}/tests/test_video_localization_research_evidence.py" \
    "${repo_root}/tests/test_video_localization_web_research.py" \
    "${repo_root}/tests/test_video_localization_research_evidence_execution.py" \
    "${repo_root}/tests/test_video_localization_entity_normalization.py" \
    "${repo_root}/tests/test_video_localization_entity_normalization_execution.py" \
    "${repo_root}/tests/test_video_localization_section_review.py" \
    "${repo_root}/tests/test_video_localization_section_review_execution.py" \
    "${repo_root}/tests/test_video_localization_review_decisions.py" \
    "${repo_root}/tests/test_video_localization_asr_uncertainty.py" \
    "${repo_root}/tests/test_video_localization_review_decisions_execution.py" \
    "${repo_root}/tests/test_video_localization_whole_recheck.py" \
    "${repo_root}/tests/test_video_localization_whole_recheck_execution.py" \
    "${repo_root}/tests/test_video_localization_transcript_quality_gate.py" \
    "${repo_root}/tests/test_video_localization_transcript_quality_gate_execution.py" \
    "${repo_root}/tests/test_video_localization_visual_evidence_managed_contracts.py" \
    "${repo_root}/tests/test_video_localization_visual_evidence_extraction_gateway.py" \
    "${repo_root}/tests/test_video_localization_visual_evidence_provider_gateway.py" \
    "${repo_root}/tests/test_video_localization_visual_evidence_execution.py" \
    "${repo_root}/tests/test_video_localization_fenced_commit.py" \
    "${repo_root}/tests/test_video_localization_operation_runtime.py" \
    "${repo_root}/tests/test_video_localization_operation_scheduler.py" \
    "${repo_root}/tests/test_video_localization_timeline_timecode.py" \
    "${repo_root}/tests/test_video_localization_dubbing_production.py" \
    "${repo_root}/tests/test_video_localization_dubbing_production_api.py" \
    "${repo_root}/tests/test_video_localization_tts_handoff_outbox.py" \
    "${repo_root}/tests/test_llm_runtime.py" \
    "${repo_root}/tests/test_codex_cli_provider.py"
  "${python_bin}" -m pytest -q \
    "${repo_root}/tests/test_video_localization_operation_feed_reader.py" \
    "${repo_root}/tests/test_video_localization_operation_store.py" \
    "${repo_root}/tests/test_video_localization_operation_read_benchmark.py" \
    "${repo_root}/tests/test_video_localization_project.py" \
    -k "operation_feed_reader or unchanged_operation_feed_revision or operation_read_benchmark or operation_feed_short_circuits"
}

run_architecture_policy() {
  "${python_bin}" \
    "${repo_root}/scripts/audit_video_localization_architecture.py" \
    --check-policy
}

run_bundle_audit_tests() {
  node --test "${repo_root}/scripts/test_audit_video_localization_bundle.mjs"
}

run_bundle_budget() {
  node "${repo_root}/scripts/audit_video_localization_bundle.mjs" --check --frontend-root "${frontend_dir}"
}

run_quick_frontend() {
  pnpm --dir "${frontend_dir}" exec vitest run \
    src/routes/generate/helpers.test.ts \
    src/routes/video-localization/activity-notice.test.ts \
    src/routes/video-localization/timeline-context-menu.test.ts \
    src/routes/video-localization/history-placement-session-controller.test.ts \
    src/routes/video-localization/history-placement-command.test.ts \
    src/routes/video-localization/TaskProgressPanel.test.ts \
    src/routes/video-localization/TaskStepResultDialog.test.ts \
    src/routes/video-localization/operation-feed-controller.test.ts \
    src/routes/video-localization/page-policy.test.ts \
    src/routes/video-localization/semantic-tts-group-selection.test.ts \
    src/routes/video-localization/subtitle-display.test.ts \
    src/routes/video-localization/subtitle-display-wiring.test.ts \
    src/routes/video-localization/asr-operation-preview.test.ts
}

run_full_backend() {
  "${python_bin}" -m pytest -q \
    "${repo_root}/tests/test_isolated_frontend_workspace.py" \
    "${repo_root}/tests/test_tts_handoff_event_loop.py" \
    "${repo_root}/tests/test_dubbing_content_integrity.py" \
    "${repo_root}/tests/test_generation_task_scheduler.py" \
    "${repo_root}/tests/test_generation_queue_web_harness.py" \
    "${repo_root}/tests/test_task_orchestration_contract.py" \
    "${repo_root}/tests/test_llm_runtime.py" \
    "${repo_root}/tests/test_codex_cli_provider.py" \
    "${repo_root}"/tests/test_video_localization_*.py \
    "${repo_root}/tests/test_openapi_docs.py"
}

run_full_frontend() {
  pnpm --dir "${frontend_dir}" test
  pnpm --dir "${frontend_dir}" check
  pnpm --dir "${frontend_dir}" build
  run_bundle_budget
}

run_browser() {
  node "${repo_root}/scripts/verify_video_localization_browser.mjs"
}

case "${mode}" in
  --quick)
    run_architecture_policy
    run_bundle_audit_tests
    run_quick_backend
    run_quick_frontend
    ;;
  --full)
    run_architecture_policy
    run_bundle_audit_tests
    run_full_backend
    run_full_frontend
    "${python_bin}" "${repo_root}/scripts/verify_dubbing_append_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_dubbing_recovery_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_dubbing_recovery_web.py" --manual-timing
    "${python_bin}" "${repo_root}/scripts/verify_dubbing_recovery_web.py" --candidate-resume
    "${python_bin}" "${repo_root}/scripts/verify_dubbing_partial_ack_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_dubbing_content_web.py" --responsive
    "${python_bin}" "${repo_root}/scripts/verify_timeline_viewport_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_timeline_editing_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_editorial_export_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_incremental_dub_subtitles_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_asr_reference_handoff_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_localization_terminal_commit_web.py"
    "${python_bin}" "${repo_root}/scripts/verify_asr_continuation_web.py"
    ;;
  --browser)
    run_browser
    ;;
  *)
    echo "用法：$0 [--quick|--full|--browser]" >&2
    exit 2
    ;;
esac
