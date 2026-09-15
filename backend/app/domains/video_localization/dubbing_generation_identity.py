"""Stable identity checks for frozen video-localization TTS requests."""

from __future__ import annotations

from typing import Any

from app.schemas.voice_studio import GenerateRequest
from app.errors import AppException
from app.domains.video_localization.tts_parameter_pack import (
    PARAMETER_PACK_VERSION,
    TtsParameterPack,
)
from app.domains.video_localization.tts_selection import (
    TtsSelectionRequest,
    build_selection_snapshot,
)


PRIMARY_DUBBING_ENGINE_ID = "omnivoice"
RUNTIME_FIELDS = frozenset({
    "generation_id", "video_localization_workflow_id",
    "video_localization_submission_id", "video_localization_generation_attempt",
    "video_localization_execution_scope", "video_localization_dubbing_review_mode",
    "video_localization_execution_start_group_id",
    "video_localization_execution_end_group_id",
    "video_localization_max_in_flight_groups",
    "video_localization_ordinary_speed_baseline", "resource_priority",
    "idempotency_marker", "video_localization_preflight",
})


def _source_cue_ids(draft: Any, group: Any) -> list[str]:
    plan = getattr(getattr(draft, "dubbing_production", None), "active_plan", None)
    if plan is None:
        return []
    units_by_id = {item.unit_id: item for item in plan.semantic_units}
    return list(dict.fromkeys(
        cue_id
        for unit_id in group.unit_ids
        if unit_id in units_by_id
        for cue_id in units_by_id[unit_id].source_cue_ids
    ))


def _valid_parameter_pack(snapshot: Any) -> TtsParameterPack | None:
    if not isinstance(snapshot, dict):
        return None
    try:
        pack = TtsParameterPack.model_validate(snapshot)
    except (TypeError, ValueError):
        return None
    return pack if pack.version == PARAMETER_PACK_VERSION else None


def _parameter_pack_source_is_current(
    draft: Any,
    *,
    request: GenerateRequest,
    pack: TtsParameterPack,
) -> bool:
    """Compare a v2 frozen source snapshot without preparing media or TTS."""

    try:
        current = build_selection_snapshot(
            draft,
            TtsSelectionRequest(
                target_subtitle_ids=list(pack.target.subtitle_ids),
                source_cue_ids=list(pack.source.cue_ids),
            ),
        )
    except (AppException, TypeError, ValueError):
        # A changed/deleted source must not make an old voice reusable.  The
        # selection function is pure, but intentionally raises typed domain
        # errors for unavailable or cross-speaker selections.
        return False
    return (
        request.video_localization_source_cue_ids == list(pack.source.cue_ids)
        and current.source == pack.source
    )


def frozen_group_request(
    draft: Any,
    group: Any,
    *,
    parameters: dict[str, Any] | None = None,
    allow_equivalent_plan_rebind: bool = False,
) -> GenerateRequest | None:
    """Return a frozen request only when it still exactly binds ``group``.

    Historical workflow records stay immutable.  A local text rebase may only
    return a copy with the current plan revision after every synthesis input
    that identifies the group remains unchanged.
    """

    plan = getattr(getattr(draft, "dubbing_production", None), "active_plan", None)
    if plan is None:
        return None
    snapshots = [parameters] if parameters is not None else [
        getattr(stage, "parameters", {})
        for workflow in reversed(getattr(draft, "tts_tasks", []) or [])
        for stage in getattr(workflow, "stages", []) or []
        if getattr(stage, "kind", None) == "generation"
    ]
    required = (
        set(GenerateRequest.model_fields)
        - RUNTIME_FIELDS
        - {"video_localization_recovery"}
    )
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not required.issubset(snapshot):
            continue
        if snapshot.get("video_localization_dubbing_group_id") != group.group_id:
            continue
        snapshot_plan_revision = snapshot.get(
            "video_localization_dubbing_plan_revision"
        )
        if snapshot_plan_revision != plan.plan_revision and not (
            allow_equivalent_plan_rebind
            and isinstance(snapshot_plan_revision, int)
            and 0 < snapshot_plan_revision < plan.plan_revision
        ):
            continue
        try:
            request = GenerateRequest.model_validate(snapshot)
        except (ValueError, TypeError):
            continue
        has_parameter_pack = "video_localization_parameter_pack" in snapshot
        pack = _valid_parameter_pack(
            snapshot.get("video_localization_parameter_pack")
        )
        expected_source_cue_ids = _source_cue_ids(draft, group)
        recovery = request.video_localization_recovery
        if recovery is not None and recovery.stage == "nearby_reference":
            # Nearby-reference recovery has an explicit, validated source
            # choice.  Do not compare it to the group's ordinary reference.
            expected_source_cue_ids = list(recovery.reference_cue_ids)
        if (
            request.engine_id != PRIMARY_DUBBING_ENGINE_ID
            or request.text != group.spoken_text
            or request.video_localization_target_subtitle_ids != list(group.subtitle_ids)
            or request.video_localization_source_cue_ids != expected_source_cue_ids
            or not request.ref_text
            or not request.reference_audio_path
            or not request.custom_reference_source_audio_path
            or request.custom_reference_trim_start_ms is None
            or request.custom_reference_trim_end_ms is None
            or request.custom_reference_trim_end_ms
            <= request.custom_reference_trim_start_ms
        ):
            continue
        if has_parameter_pack and (
            pack is None
            or not _parameter_pack_source_is_current(
                draft, request=request, pack=pack,
            )
        ):
            continue
        if snapshot_plan_revision != plan.plan_revision:
            return request.model_copy(update={
                "video_localization_dubbing_plan_revision": plan.plan_revision,
            })
        return request
    return None


def compatible_generated_identities(draft: Any, group: Any) -> list[str]:
    """Return non-discarded successful old-plan outputs that still bind a group.

    These identities only make an existing audio result eligible for the
    downstream inspection/placement flow.  They never assert current CQC
    completion or mutate the historical workflow.
    """

    discarded = {
        str(value)
        for value in (getattr(draft, "ui_state", {}) or {}).get(
            "discarded_tts_task_ids", []
        )
        if value
    }
    identities: list[str] = []
    for workflow in getattr(draft, "tts_tasks", []) or []:
        task_id = str(getattr(workflow, "generation_task_id", "") or "")
        result_id = str(getattr(workflow, "result_id", "") or "")
        workflow_ids = {
            str(value)
            for value in (
                getattr(workflow, "workflow_id", None), task_id, result_id,
            )
            if value
        }
        if (
            not task_id
            or not result_id
            or workflow_ids & discarded
            or getattr(workflow, "status", None) == "cancelled"
        ):
            continue
        if not any(
            getattr(stage, "kind", None) == "generation"
            and getattr(stage, "status", None) == "success"
            and frozen_group_request(
                draft,
                group,
                parameters=getattr(stage, "parameters", {}) or {},
                allow_equivalent_plan_rebind=True,
            ) is not None
            for stage in getattr(workflow, "stages", []) or []
        ):
            continue
        identities.extend((result_id, task_id, f"candidate_{task_id}"))
    return list(dict.fromkeys(identities))
