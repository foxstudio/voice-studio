"""Versioned prompt, model-routing, and output-protocol policy for localization."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.voice_studio import AppSettings
from app.services import llm_runtime


POLICY_VERSION = "localization-ai-policy-v1"
LocalizationAiPhase = Literal[
    "document_understanding",
    "evidence_adjudication",
    "spoken_script_creation",
    "fidelity_review",
    "naturalness_review",
    "spoken_script_finalization",
    "alignment_adjudication",
]
LocalizationOutputFormat = Literal["json", "markdown", "text"]
PromptStrategy = Literal["adaptive", "fixed"]
RoutingPreset = Literal["auto", "quality", "cost"]

LOCALIZATION_AI_PHASE_BY_STEP_ID: dict[str, LocalizationAiPhase] = {
    "analyze_localization_document": "document_understanding",
    "adjudicate_localization_evidence_v3": "evidence_adjudication",
    "generate_localization_spoken_script": "spoken_script_creation",
    "review_localization_fidelity": "fidelity_review",
    "review_localization_naturalness": "naturalness_review",
    "finalize_localization_spoken_script": (
        "spoken_script_finalization"
    ),
    "adjudicate_localization_alignment": "alignment_adjudication",
    "adjudicate_localization_display_boundaries": "alignment_adjudication",
}


class LocalizationAiPhaseRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: LocalizationAiPhase
    profile_id: str
    model_id: str
    reasoning_effort: Literal["low", "high", "max"]
    output_format: LocalizationOutputFormat
    prompt_strategy: PromptStrategy
    may_escalate: bool = False
    escalation_reason: str = ""


class LocalizationAiPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-ai-policy-v1"
    ] = POLICY_VERSION
    preset: RoutingPreset
    routes: list[LocalizationAiPhaseRoute]

    def route(self, phase: LocalizationAiPhase) -> LocalizationAiPhaseRoute:
        for route in self.routes:
            if route.phase == phase:
                return route
        raise ValueError(f"本土化 AI 策略缺少阶段：{phase}")


def resolve_localization_ai_policy(
    settings: AppSettings,
    *,
    fallback_profile_id: str | None = None,
    required_phases: Iterable[LocalizationAiPhase] | None = None,
) -> LocalizationAiPolicy:
    """Resolve the simple settings preset into explicit auditable routes."""

    preset = settings.video_localization_ai_strategy
    prompt_strategy = settings.video_localization_prompt_strategy
    phase_profiles = {
        "document_understanding": (
            settings.video_localization_understanding_profile_id
        ),
        "spoken_script_creation": (
            settings.video_localization_creation_profile_id
        ),
        "evidence_adjudication": (
            settings.video_localization_review_profile_id
        ),
        "fidelity_review": (
            settings.video_localization_review_profile_id
        ),
        "naturalness_review": (
            settings.video_localization_review_profile_id
        ),
        "spoken_script_finalization": (
            settings.video_localization_creation_profile_id
        ),
        "alignment_adjudication": (
            settings.video_localization_alignment_profile_id
        ),
    }
    selected_phases = (
        set(phase_profiles)
        if required_phases is None
        else set(required_phases)
    )
    routes = []
    for phase, configured_profile_id in phase_profiles.items():
        if phase not in selected_phases:
            continue
        profile = llm_runtime.resolve_profile(
            configured_profile_id or fallback_profile_id
        )
        output_format: LocalizationOutputFormat = (
            "markdown" if phase == "spoken_script_creation" else "json"
        )
        if preset == "quality":
            reasoning = "high"
            may_escalate = False
        elif preset == "cost":
            reasoning = "low"
            may_escalate = False
        else:
            reasoning = (
                "high"
                if phase == "fidelity_review"
                and profile.reasoning_effort != "max"
                else profile.reasoning_effort
                if profile.reasoning_effort in {"high", "max"}
                else "low"
            )
            may_escalate = phase in {
                "spoken_script_creation",
                "spoken_script_finalization",
                "alignment_adjudication",
            } and reasoning == "low"
        routes.append(
            LocalizationAiPhaseRoute(
                phase=phase,
                profile_id=profile.profile_id,
                model_id=profile.model_id,
                reasoning_effort=reasoning,
                output_format=output_format,
                prompt_strategy=prompt_strategy,
                may_escalate=may_escalate,
                escalation_reason=(
                    "仅当该阶段质量门未通过时提升思考深度或切换高级配置。"
                    if may_escalate
                    else ""
                ),
            )
        )
    return LocalizationAiPolicy(
        preset=preset,
        routes=routes,
    )


__all__ = [
    "LocalizationAiPhase",
    "LocalizationAiPhaseRoute",
    "LocalizationAiPolicy",
    "LocalizationOutputFormat",
    "LOCALIZATION_AI_PHASE_BY_STEP_ID",
    "POLICY_VERSION",
    "resolve_localization_ai_policy",
]
