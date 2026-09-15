"""Submission identity for queued source-ASR and localization workflows."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domains.video_localization import localization_requirements, localization_timeline, source_pipeline
from app.domains.video_localization import asr_development_workflow_nodes
from app.domains.video_localization.localization_workflow_nodes import (
    LOCALIZATION_WORKFLOW_NODE_SPECS, node_behavior_fingerprint,
)
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.services import (
    llm_runtime, localization_ai_policy, settings_store,
    video_localization_llm_provider_execution,
)


class WorkflowSubmissionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["video-localization-submission-v1"] = "video-localization-submission-v1"
    workflow_id: Literal["english_asr", "localization_draft"]
    source_fingerprint: str
    media_fingerprint: str
    requirements_fingerprint: str
    configuration_fingerprint: str
    behavior_fingerprint: str


def capture_submission_binding(
    draft: VideoLocalizationDraft, *, profile_id: str | None,
    requirements_id: str | None,
) -> WorkflowSubmissionBinding:
    """Freeze source/configuration identity; never call an inference provider."""
    try:
        policy = localization_ai_policy.resolve_localization_ai_policy(
            settings_store.get(), fallback_profile_id=profile_id,
        )
        configuration = {
            "policy": policy.model_dump(mode="json"),
            "profiles": {
                selected_id: video_localization_llm_provider_execution.provider_configuration_fingerprint(
                    llm_runtime.resolve_profile(selected_id)
                )
                for selected_id in sorted({route.profile_id for route in policy.routes})
            },
        }
    except llm_runtime.LlmRuntimeError as exc:
        # Preserve existing admission semantics for unconfigured projects.
        # Configuring the provider later still changes this queued identity.
        configuration = {"profile_id": profile_id, "configuration_error": exc.code}
    return WorkflowSubmissionBinding(
        workflow_id="localization_draft",
        source_fingerprint=localization_timeline.source_fingerprint(draft),
        media_fingerprint=source_pipeline.english_asr_source_revision(draft),
        requirements_fingerprint=localization_requirements.resolve_localization_requirements(requirements_id).fingerprint,
        configuration_fingerprint=_fingerprint(configuration),
        behavior_fingerprint=_fingerprint({
            step_id: node_behavior_fingerprint(step_id)
            for step_id in LOCALIZATION_WORKFLOW_NODE_SPECS
        }),
    )


def capture_asr_submission_binding(
    draft: VideoLocalizationDraft, *, profile_id: str | None,
) -> WorkflowSubmissionBinding:
    try:
        configuration = video_localization_llm_provider_execution.provider_configuration_fingerprint(
            llm_runtime.resolve_profile(profile_id)
        )
    except llm_runtime.LlmRuntimeError as exc:
        configuration = _fingerprint({"profile_id": profile_id, "configuration_error": exc.code})
    return WorkflowSubmissionBinding(
        workflow_id="english_asr",
        source_fingerprint=localization_timeline.source_fingerprint(draft),
        media_fingerprint=source_pipeline.english_asr_source_revision(draft),
        requirements_fingerprint=_fingerprint(None),
        configuration_fingerprint=configuration,
        behavior_fingerprint=_fingerprint({
            node: asr_development_workflow_nodes.asr_development_behavior_fingerprint(node)
            for node in asr_development_workflow_nodes.ASR_DEVELOPMENT_NODE_BEHAVIOR_SPECS
        }),
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
